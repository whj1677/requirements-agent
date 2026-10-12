"""Explicit review of displayed requirement rows in one atomic project edit."""
import copy
from pathlib import Path
import tempfile
from types import SimpleNamespace

from .core import Problem, digest, now, require
from .requirement_confirmation import (build_preview, apply_preview, _question_results,
    apply_question_confirmations, confirmation_state)
from .requirements import item_content

SCHEMA = 'requirement-batch-confirmation-1'
PREVIEW_KIND = 'requirement_batch_confirmation_preview'
RECEIPT_KIND = 'requirement_batch_confirmation_receipt'
ROW_KEYS = {'requirement_id', 'candidate_id', 'statement', 'behavior', 'change_type',
            'acceptance_items', 'confirmed_question_ids'}


def build_batch_preview(project, payload):
    require(set(payload) == {'expected_revision', 'rows'} and
            type(payload.get('expected_revision')) is int,
            'INPUT_REQUIRED', '批量预览需要项目版本及明确选择的需求行')
    require(project['revision'] == payload['expected_revision'], 'STALE_REVISION',
            '需求已更新，请查看最新内容后重新确认', 409)
    inputs = payload.get('rows')
    require(isinstance(inputs, list) and 1 <= len(inputs) <= 100 and
            all(isinstance(row, dict) and set(row) <= ROW_KEYS for row in inputs),
            'INPUT_REQUIRED', '请选择1至100条已完整展示的需求；只能提交正文、业务说明、验收和回答')
    ids = [row.get('requirement_id') for row in inputs]
    require(all(isinstance(iid, str) for iid in ids) and len(ids) == len(set(ids)),
            'REFERENCE_INVALID', '批量需求编号无效或重复')
    # Run the existing complete single-row validation against the same revision.
    # Requested question IDs are checked there; collective coverage is computed
    # only after every displayed requirement and acceptance is known.
    rows = []
    for row in inputs:
        try:
            rows.append(build_preview(project, dict(row, expected_revision=project['revision'])))
        except Problem as error:
            requirement = next((item for item in project['items'] if item['id'] == row['requirement_id']), {})
            label = requirement.get('title') or row['requirement_id']
            raise Problem(error.code, '「' + label + '」' + '（' + row['requirement_id'] + '）：' + error.message,
                          error.status, model_repair_allowed=error.model_repair_allowed) from error
    displayed_ids = set(ids)
    shared = {}
    for row in rows:
        for acceptance in row['acceptance_items']:
            iid = acceptance['id']
            if iid in shared:
                require(acceptance == shared[iid], 'BATCH_CONTENT_CONFLICT',
                        '同一关联验收在多行中内容不同，请统一后再确认：' + iid, 409)
            shared[iid] = acceptance
            displayed_ids.add(iid)
    requested = {qid for row in inputs for qid in row.get('confirmed_question_ids', [])}
    changed_ids = {change['after']['id'] for row in rows
                   for change in [dict(before=row['before'], after=row['after']), *row['acceptance_items']]
                   if change['before'] is None or item_content(change['before']) != item_content(change['after'])}
    question_results = {}
    for row in rows:
        related = [q['id'] for q in project['questions']
                   if row['requirement_id'] in q.get('related_refs', []) and q['id'] in requested]
        results = _question_results(project, row['requirement_id'], displayed_ids, related)
        for result in results:
            question = next(q for q in project['questions'] if q['id'] == result['question_id'])
            if changed_ids.intersection(question.get('related_refs', [])):
                result['already_confirmed'] = False
        row['question_results'] = results
        row['confirmed_question_ids'] = [result['question_id'] for result in results if result['confirm']]
        row['preview_hash'] = digest({key: value for key, value in row.items() if key != 'preview_hash'})
        for result in results:
            saved = question_results.setdefault(result['question_id'], dict(copy.deepcopy(result), requirement_ids=[]))
            saved['requirement_ids'].append(row['requirement_id'])
    questions = list(question_results.values())
    pending = [result for result in questions if result['blocking'] and
               not result['confirm'] and not result['already_confirmed']]
    preview = dict(schema=SCHEMA, expected_revision=project['revision'], rows=rows,
        question_results=questions,
        confirmed_question_ids=[result['question_id'] for result in questions if result['confirm']],
        summary=dict(requirement_count=len(rows), acceptance_count=len(shared),
                     confirmed_question_count=sum(result['confirm'] for result in questions),
                     pending_question_count=len(pending)), created=now())
    preview['preview_hash'] = digest(preview)
    return preview


def apply_batch_preview(project, preview, store):
    """Apply only listed rows; caller supplies a staging store and transaction."""
    require(preview.get('schema') == SCHEMA, 'SNAPSHOT_INVALID', '批量预览格式无效', 409)
    displayed_ids = {row['requirement_id'] for row in preview['rows']}
    displayed_ids.update(ac['id'] for row in preview['rows'] for ac in row['acceptance_items'])
    for row in preview['rows']:
        latest = _question_results(project, row['requirement_id'], displayed_ids, row['confirmed_question_ids'])
        latest = {result['question_id']: result for result in latest}
        for result in row['question_results']:
            if result['confirm']:
                current = latest.get(result['question_id'], {})
                require(current.get('confirm') and all(current.get(key) == result.get(key)
                    for key in ('question', 'answer', 'saved_answer_id')), 'STALE_ANSWER',
                    '已展示回答或关联范围发生变化，请重新核对', 409)
    results, acceptance_done = [], set()
    # Shared acceptance is visibly reviewed in both rows but applied once. Its
    # equality was validated at preview time; no hidden clause is added here.
    for row in preview['rows']:
        current = copy.deepcopy(row)
        current['acceptance_items'] = [acceptance for acceptance in row['acceptance_items']
                                       if acceptance['id'] not in acceptance_done]
        apply_preview(project, current, store, confirm_questions=False)
        acceptance_done.update(acceptance['id'] for acceptance in row['acceptance_items'])
        results.append(dict(requirement_id=row['requirement_id'],
            acceptance_ids=[acceptance['id'] for acceptance in row['acceptance_items']],
            confirmed_question_ids=row['confirmed_question_ids']))
    # Bind answers to the final contents of all displayed targets. Confirming
    # midway would invalidate cross-row answer hashes when later rows are edited.
    apply_question_confirmations(project, preview['question_results'])
    return dict(results=results, confirmed_question_ids=preview['confirmed_question_ids'])


def register(app, store):
    @app.post('/api/projects/{pid}/requirement-confirmation/batch/preview')
    async def preview(pid: str, body: dict):
        result = build_batch_preview(store.get(pid), body)
        record_id = store.record(pid, PREVIEW_KIND, result)
        return dict(preview_id=record_id, **result)

    @app.post('/api/projects/{pid}/requirement-confirmation/batch/commit')
    async def commit(pid: str, body: dict):
        keys = {'expected_revision', 'preview_id', 'preview_hash', 'idempotency_key', 'review_confirmed'}
        require(set(body) == keys and type(body.get('expected_revision')) is int
                and all(isinstance(body.get(key), str) for key in ('preview_id', 'preview_hash', 'idempotency_key'))
                and 8 <= len(body['idempotency_key']) <= 100,
                'INPUT_REQUIRED', '提交缺少有效版本、预览或提交编号')
        require(body['review_confirmed'] is True, 'REVIEW_REQUIRED',
                '请明确确认预览中展示的全部所选需求、验收和回答', 409)
        request_hash = digest(body)
        def prior_receipt(db=None):
            old = next((record for record in store.records(pid, RECEIPT_KIND, db)
                        if record['idempotency_key'] == body['idempotency_key']), None)
            if old:
                require(old['request_hash'] == request_hash, 'IDEMPOTENCY_CONFLICT',
                        '同一提交编号已用于不同内容', 409)
            return old
        previous = prior_receipt()
        if previous:
            return dict(project=store.get(pid), receipt=previous, repeated=True)
        created_files, committed = [], False
        try:
            # save_source writes only into this transaction-owned temporary
            # folder. No partial human-authored sources leak on a later failure.
            with tempfile.TemporaryDirectory(prefix='requirement-batch-') as temporary:
                staging = SimpleNamespace(folder=Path(temporary))
                with store.edit(pid, body['expected_revision'], '人工一次批量确认需求与验收') as (project, db):
                    require(prior_receipt(db) is None, 'IDEMPOTENCY_CONFLICT', '相同提交正在处理', 409)
                    saved = store.get_record(pid, body['preview_id'], PREVIEW_KIND)
                    require(saved['preview_hash'] == body['preview_hash'] and saved['preview_hash'] == digest(
                        {key: value for key, value in saved.items() if key not in ('id', 'preview_hash')}),
                        'SNAPSHOT_INVALID', '预览内容校验失败，请重新核对', 409)
                    require(saved['expected_revision'] == body['expected_revision'], 'STALE_REVISION',
                            '预览版本已变化', 409)
                    summary = apply_batch_preview(project, saved, staging)
                    receipt = dict(idempotency_key=body['idempotency_key'], request_hash=request_hash,
                        preview_id=body['preview_id'], result_revision=project['revision'] + 1, **summary)
                    receipt['id'] = store.record(pid, RECEIPT_KIND, receipt, db=db)
                    staged_sources = staging.folder / 'sources'
                    if staged_sources.exists():
                        destination = store.folder / 'sources'
                        destination.mkdir(exist_ok=True)
                        for source in staged_sources.iterdir():
                            target = destination / source.name
                            with target.open('xb') as handle:
                                created_files.append(target)
                                handle.write(source.read_bytes())
                committed = True
            return dict(project=project, receipt=receipt, repeated=False,
                        confirmation_state=confirmation_state(project))
        except BaseException as exc:
            if not committed:
                for path in created_files:
                    path.unlink(missing_ok=True)
            if isinstance(exc, Problem) and exc.code == 'STALE_REVISION':
                previous = prior_receipt()
                if previous:
                    return dict(project=store.get(pid), receipt=previous, repeated=True)
            raise
