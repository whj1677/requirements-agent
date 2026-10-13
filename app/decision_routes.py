"""Local decision review endpoints; register from the existing application."""
import copy

from .core import Problem, digest, require
from .decision_review import (apply_review, build_preview, save_raw_answer,
                              validate_snapshot)


def register(app, store):
    from .requirement_confirmation import register as register_requirement_confirmation
    register_requirement_confirmation(app, store)

    @app.post('/api/projects/{pid}/decision-review/answer')
    async def save_decision_answer(pid: str, body: dict):
        require(isinstance(body.get('expected_revision'), int)
                and isinstance(body.get('question_id'), str),
                'INPUT_REQUIRED', '缺少项目版本或决定组编号')
        with store.edit(pid, body['expected_revision'], '保存决定组原话') as (project, _):
            answer = save_raw_answer(project, body['question_id'], body.get('answer'), store)
        return dict(project=project, answer_id=answer['id'],
                    answer_hash=answer['answer_hash'], question_id=body['question_id'])

    @app.post('/api/projects/{pid}/decision-review')
    async def preview_decision_review(pid: str, body: dict):
        require(isinstance(body.get('expected_revision'), int)
                and isinstance(body.get('question_id'), str),
                'INPUT_REQUIRED', '缺少项目版本或问题编号')
        preview = build_preview(store.get(pid), body)
        # A preview record is immutable audit evidence, not a project mutation.
        preview_id = store.record(pid, 'decision_preview', preview)
        return dict(preview_id=preview_id, **preview)

    @app.post('/api/projects/{pid}/decision-review/manual-preview')
    async def preview_manual_decision_review(pid: str, body: dict):
        from .manual_decision_review import build_manual_preview
        require(isinstance(body.get('expected_revision'), int)
                and isinstance(body.get('question_id'), str),
                'INPUT_REQUIRED', '缺少项目版本或问题编号')
        preview = build_manual_preview(store.get(pid), body)
        preview_id = store.record(pid, 'decision_preview', preview)
        return dict(preview_id=preview_id, **preview)

    @app.post('/api/projects/{pid}/decision-review/commit')
    async def commit_decision_review(pid: str, body: dict):
        required = ('expected_revision', 'preview_id', 'preview_hash',
                    'selected_group_ids', 'idempotency_key', 'review_confirmed')
        require(all(key in body for key in required), 'INPUT_REQUIRED',
                '提交缺少预览、选择或确认信息')
        require(isinstance(body['expected_revision'], int)
                and isinstance(body['preview_id'], str)
                and isinstance(body['preview_hash'], str)
                and isinstance(body['selected_group_ids'], list)
                and all(isinstance(x, str) for x in body['selected_group_ids'])
                and isinstance(body['idempotency_key'], str)
                and 8 <= len(body['idempotency_key']) <= 100,
                'INPUT_REQUIRED', '提交字段格式无效')
        require(body['review_confirmed'] is True, 'REVIEW_REQUIRED',
                '请先核对覆盖和全部关联修改', 409)
        discussion = body.get('document_discussion')
        request = {key:body[key] for key in required}
        if 'document_discussion' in body:
            from .document_discussion import validate_scope
            discussion = validate_scope(discussion)
            request['document_discussion'] = discussion
        fingerprint = digest(request)

        def previous_receipt(db=None):
            prior = next((r for r in store.records(pid, 'decision_receipt', db)
                          if r['idempotency_key'] == body['idempotency_key']), None)
            if prior:
                require(prior['request_hash'] == fingerprint, 'IDEMPOTENCY_CONFLICT',
                        '同一提交键已用于不同内容', 409)
            return prior

        prior = previous_receipt()
        if prior:
            return dict(project=store.get(pid), receipt=prior, repeated=True)
        try:
            with store.edit(pid, body['expected_revision'], '一次核对并落实独立决定') as (project, db):
                prior = previous_receipt(db)
                require(prior is None, 'IDEMPOTENCY_CONFLICT',
                        '相同提交正在处理，请查询原回执', 409)
                preview = store.get_record(pid, body['preview_id'], 'decision_preview')
                require(preview['preview_hash'] == body['preview_hash']
                        and digest({key:value for key,value in preview.items() if key not in ('id','preview_hash')})
                        == body['preview_hash'],
                        'SNAPSHOT_INVALID', '预览回执不匹配，请重新核对', 409)
                require(preview['expected_revision'] == body['expected_revision'],
                        'STALE_REVISION', '预览项目版本已变化', 409)
                validate_snapshot(project, preview)
                before = copy.deepcopy(project) if discussion else None
                if discussion:
                    from .document_discussion import validate_review_scope
                    validate_review_scope(project, preview, store, db, discussion)
                summary = apply_review(project, preview, body['selected_group_ids'], store=store)
                if discussion:
                    from .document_discussion import close_review
                    summary['document_discussion'] = close_review(project, before, discussion)
                receipt = dict(idempotency_key=body['idempotency_key'],
                               request_hash=fingerprint, preview_id=body['preview_id'],
                               preview_hash=body['preview_hash'],
                               result_revision=project['revision'] + 1, **summary)
                receipt_id = store.record(pid, 'decision_receipt', receipt, db=db)
                receipt['id'] = receipt_id
            return dict(project=project, receipt=receipt, repeated=False)
        except Problem as exc:
            if exc.code == 'STALE_REVISION':
                prior = previous_receipt()
                if prior:
                    return dict(project=store.get(pid), receipt=prior, repeated=True)
            raise
