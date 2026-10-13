"""An explicit human review of one displayed requirement and its acceptance.

The signed row replaces the fixed seven-field checklist, not source validation
or outstanding business decisions. Preview is immutable evidence only.
"""
import copy
import re

from .core import Problem, digest, ident, now, require
from .requirements import behavior_fingerprint, item_content

FIELDS = ('actor', 'entry', 'flow', 'data', 'permissions', 'result', 'exceptions')
CHANGE_TYPES = ('new', 'modified', 'preserved', 'unspecified')


def _manual_text(before, after):
    from .manual_decision_review import manual_edit_text
    normalized = copy.deepcopy(before)
    normalized['behavior'] = {field: before.get('behavior', {}).get(field, '') for field in FIELDS}
    text = manual_edit_text(normalized, after)
    if before.get('detail_contract') != after.get('detail_contract') and 'detail_contract' in after:
        from .core import dumps
        text += '\n人工核对的结构化详细需求：\n' + dumps(after['detail_contract'])
    return text


def linked_acceptance(project, requirement):
    return [item for item in project['items'] if item['kind'] == 'acceptance'
            and item.get('applies_to') == 'to_be' and not item.get('target_item_id')
            and (requirement['id'] in item.get('related_refs', [])
                 or item['id'] in requirement.get('related_refs', []))]


def row_fingerprint(project, requirement):
    """Local identity; edits to another row do not revoke this confirmation."""
    acceptance = [item for item in linked_acceptance(project, requirement)
                  if item.get('selection_status') == 'selected']
    sources = {source['id']: source for source in project.get('sources', [])}
    from .requirement_details import dependency_snapshot, detail_source_refs
    dependencies = dependency_snapshot(project, requirement)
    related = [requirement, *acceptance]
    if dependencies:
        dependency_ids = {row['id'] for row in dependencies}
        dependency_ids.update(clause['id'] for row in dependencies for clause in row.get('linked_clauses', []))
        related += [item for item in project['items'] if item['id'] in dependency_ids]
    refs = {ref['source_id'] for item in related
            for ref in [*item.get('source_refs', []), *detail_source_refs(item)]}
    content = dict(requirement=item_content(requirement),
                       selection_status=requirement.get('selection_status'),
                       acceptance=[dict(id=item['id'], content=item_content(item))
                                   for item in sorted(acceptance, key=lambda item: item['id'])],
                       sources={sid: {key: sources.get(sid, {}).get(key) for key in
                                     ('sha256', 'version', 'excluded', 'superseded_by', 'excerpts')}
                                for sid in sorted(refs)})
    if dependencies is not None:
        content['detail_dependencies'] = dependencies
    return digest(content)


def confirmation_current(project, requirement):
    review = requirement.get('requirement_confirmation') or {}
    return bool(review.get('actor') == 'authenticated_local_user'
                and review.get('schema') == 'requirement-confirmation-1'
                and review.get('content_hash') == row_fingerprint(project, requirement))


def confirmation_state(project):
    from .product_flow import question_open
    from .requirement_details import detail_issues
    return dict(revision=project['revision'], rows=[dict(
        requirement_id=item['id'], confirmed=confirmation_current(project, item),
        current=confirmation_current(project, item),
        detail_issues=detail_issues(item, project),
        acceptance_ids=[ac['id'] for ac in linked_acceptance(project, item)
                        if ac.get('selection_status') == 'selected'],
        missing_decisions=[dict(question_id=q['id'], question=q['question'])
                           for q in project['questions'] if q.get('blocking')
                           and not q.get('superseded_by') and not q.get('out_of_scope_reason')
                           and item['id'] in q.get('related_refs', [])
                           and (question_open(q) or q.get('understanding_status') in ('pending', 'candidate_ready')
                                or q.get('decision_pending_group_ids'))])
        for item in project['items'] if item['kind'] == 'requirement'
        and item.get('applies_to') == 'to_be' and not item.get('target_item_id')])


def _text(value, label):
    require(isinstance(value, str) and value.strip() and len(value) <= 6000,
            'INPUT_REQUIRED', label + '不能为空或超过6000字符')
    return value


def _sources_valid(project, item):
    available = {(source['id'], excerpt['id']) for source in project['sources']
                 if not source.get('excluded') and not source.get('superseded_by')
                 for excerpt in source.get('excerpts', [])}
    refs = item.get('source_refs', [])
    require(refs and all((ref.get('source_id'), ref.get('excerpt_id')) in available for ref in refs),
            'REFERENCE_INVALID', '请先补充或核对这条内容的有效来源：' + item['id'], 409)


def _revision_candidate(project, before, candidate_id):
    """Keep the candidate identity and its saved-answer/source provenance."""
    candidate = next((item for item in project['items'] if item['id'] == candidate_id), None)
    require(candidate is not None and candidate.get('kind') == before['kind']
            and candidate.get('target_item_id') == before['id']
            and candidate.get('selection_status') == 'candidate'
            and not candidate.get('superseded_by')
            and candidate.get('applies_to') == 'to_be'
            and (before['kind'] != 'acceptance' or candidate.get('action') == 'revise'),
            'REFERENCE_INVALID', '修订建议必须属于当前原始条款，且仍为本期同类候选', 409)
    _sources_valid(project, candidate)
    from .understanding import answer_target, candidate_answer_binding_current
    questions = {q['id']: q for q in project['questions']}
    require(all(qid in questions and candidate_answer_binding_current(project, questions[qid], candidate)
                for qid in candidate.get('answer_refs', [])), 'STALE_ANSWER',
            'AI建议绑定的回答已变化，请按最新回答重新整理', 409)
    require(all(answer_target(project, questions[qid], candidate) is not None
                for qid in candidate.get('answer_refs', [])), 'REFERENCE_INVALID',
            'AI建议只能绑定直接关联原条款的回答', 409)
    return candidate


def _merge_sources(before, candidate):
    refs = copy.deepcopy(before.get('source_refs', []))
    for ref in candidate.get('source_refs', []):
        if ref not in refs:
            refs.append(copy.deepcopy(ref))
    return refs


def _validate_content(project, before, after):
    from .result_conditions import validate_item
    from .source_fidelity import validate_item as validate_source
    validate_item(after)
    validate_source(after, project, decision_text=_manual_text(before, after))


def _question_answer(project, question):
    """Return actual saved words, never infer a leaf answer from a compound group."""
    from .understanding import current_answer, current_decision_answer
    group = question.get('decision_group_id')
    members = [q for q in project['questions'] if group and q.get('decision_group_id') == group
               and not q.get('superseded_by') and not q.get('out_of_scope_reason')]
    saved = current_decision_answer(project, question) if group else None
    text = question.get('answer') if current_answer(question) else None
    if not isinstance(text, str) or not text.strip():
        text = saved['text'] if saved else None
    return text, saved, len(members)


def _question_results(project, requirement_id, displayed_ids, requested, *,
                      answer_implementations=None, before_items=None, check_implementations=True):
    from .understanding import answer_targets, applied_targets, coverage_complete
    from .answer_implementation import (derive_changed_witnesses, saved_implementations,
        validate_implementation_map, validate_implementations)
    supplied=validate_implementation_map(project,answer_implementations)
    results = []
    known = {q['id']: q for q in project['questions']}
    require(isinstance(requested, list) and all(isinstance(qid, str) for qid in requested)
            and len(requested) == len(set(requested)) and set(requested) <= set(known),
            'REFERENCE_INVALID', '待确认回答编号无效或重复')
    for q in project['questions']:
        if requirement_id not in q.get('related_refs', []) or q.get('superseded_by') or q.get('out_of_scope_reason'):
            continue
        targets = {item['id'] for item in answer_targets(project, q)}
        answer, saved_answer, member_count = _question_answer(project, q)
        reason = ''
        if member_count > 1 or len(q.get('decision_points') or []) > 1:
            reason = '这条原话包含多个独立决定，请先核对每项决定的回答范围。'
        elif not isinstance(answer, str) or not answer.strip():
            reason = '请先回答这个业务问题，再核对需求内容。'
        elif re.fullmatch(r'\s*(?:暂时?|还|尚)?(?:不确定|不知道|不清楚|未确定|待定|待确认|待核对|后续再说)[。！!\s]*', answer):
            reason = '这个业务决定仍待确定，请补充明确回答后再确认。'
        elif q.get('decision_pending_group_ids'):
            reason = '这条回答仍有其他关联修改待核对。'
        elif not targets or not targets <= displayed_ids:
            reason = '这条回答还关联其他条款，须一起核对，不能只确认当前行。'
        else:
            proposal = q.get('decision_review_proposal') or {}
            from .understanding import question_fingerprint
            if (len(proposal.get('decisions') or []) > 1
                    and proposal.get('question_hash') == digest([question_fingerprint(q)])
                    and proposal.get('answer_hash') == digest(answer)):
                reason = '这条回答包含多个独立决定，请先逐项核对。'
        requested_here = q['id'] in requested
        witnesses=supplied.get(q['id'])
        if witnesses is None:
            witnesses=saved_implementations(project,q)
            represented={row['item_id'] for row in witnesses}
            for item in answer_targets(project,q):
                if item['id'] not in represented:
                    witnesses+=derive_changed_witnesses((before_items or {}).get(item['id']),item)
        implementation_code=None
        if requested_here and not reason and check_implementations:
            try:
                validate_implementations(project,q,witnesses)
            except Problem as error:
                reason=error.message;implementation_code=error.code
        results.append(dict(question_id=q['id'], question=q['question'], answer=answer,
                            saved_answer_id=saved_answer['id'] if saved_answer else None,
                            blocking=bool(q.get('blocking')), can_confirm=not reason,
                            confirm=requested_here and not reason,
                            reason=reason, implementation_code=implementation_code,
                            implementation=copy.deepcopy(witnesses),
                            already_confirmed=q.get('understanding_status') == 'applied'
                            and coverage_complete(q) and bool(targets) and targets<=applied_targets(project,q)))
    require(set(requested) <= {row['question_id'] for row in results}, 'REFERENCE_INVALID',
            '只能确认当前需求直接关联的回答', 409)
    return results


def build_preview(project, payload, *, detail_project=None):
    require(type(payload.get('expected_revision')) is int
            and isinstance(payload.get('requirement_id'), str),
            'INPUT_REQUIRED', '缺少项目版本或需求编号')
    require(project['revision'] == payload['expected_revision'], 'STALE_REVISION',
            '需求已更新，请查看最新内容后重新确认', 409)
    before = next((item for item in project['items'] if item['id'] == payload['requirement_id']), None)
    require(before is not None and before['kind'] == 'requirement'
            and before.get('applies_to') == 'to_be' and not before.get('target_item_id')
            and before.get('selection_status') in ('selected', 'candidate'),
            'REFERENCE_INVALID', '只能确认本期原始需求；修订建议需核对后落实到原需求', 409)
    scope = project.get('product_context', {}).get('scope_ids')
    require(scope is None or before['id'] in scope, 'SCOPE_INVALID', '请先将这条需求纳入本期范围', 409)
    _sources_valid(project, before)
    after = copy.deepcopy(before)
    candidate = None
    if payload.get('candidate_id'):
        candidate = _revision_candidate(project, before, payload['candidate_id'])
        after['source_refs'] = _merge_sources(before, candidate)
        if candidate.get('detail_required'):
            after['detail_required'] = True
    after['statement'] = _text(payload.get('statement'), '需求说明')
    behavior = payload.get('behavior')
    require(isinstance(behavior, dict) and set(behavior) == set(FIELDS)
            and all(isinstance(value, str) and len(value) <= 6000 for value in behavior.values()),
            'INPUT_REQUIRED', '请完整展示本条业务说明字段；不适用的字段可以留空')
    after['behavior'] = copy.deepcopy(behavior)
    if 'detail_contract' in payload:
        from .requirement_details import validate_detail
        validate_detail(payload['detail_contract'], project)
        after['detail_contract'] = copy.deepcopy(payload['detail_contract'])
        after['detail_required'] = True
    elif candidate and 'detail_contract' in candidate and candidate['detail_contract'] != before.get('detail_contract'):
        # A new structured proposal must be displayed explicitly, not silently
        # adopted through its legacy statement/behavior fields.
        require(False, 'DETAIL_REVIEW_REQUIRED', '请先展示并核对修订建议中的结构化详细需求', 409)
    if 'change_type' in payload:
        require(isinstance(payload['change_type'], str) and payload['change_type'] in CHANGE_TYPES,
                'CONTEXT_INVALID', '本期需求的改动性质须为新增、修改、保持或待核对')
        # This is an explicit human classification, like the behavior editor.
        # Keep its original source/classification evidence; a choice is not a
        # newly authored source statement, nor permission to adopt a candidate's
        # classification implicitly. The displayed value joins both review hashes.
        after['change_type'] = payload['change_type']
    after['selection_status'] = 'selected'
    _validate_content(project, candidate or before, after)
    rows = payload.get('acceptance_items')
    require(isinstance(rows, list) and rows and len(rows) <= 40
            and all(isinstance(row, dict) for row in rows),
            'INPUT_REQUIRED', '请至少确认一条可验证的验收建议')
    available = {item['id']: item for item in linked_acceptance(project, before)}
    selected = {iid for iid, item in available.items() if item.get('selection_status') == 'selected'}
    acceptance, seen = [], set()
    for row in rows:
        require(set(row) <= {'id', 'statement', 'candidate_id'}, 'INPUT_REQUIRED', '验收只支持已展示的编号、正文和修订建议')
        statement = _text(row.get('statement'), '验收内容')
        iid = row.get('id')
        require(iid is None or isinstance(iid, str) and iid in available and iid not in seen,
                'REFERENCE_INVALID', '验收须属于当前需求，且不能重复', 409)
        ac_candidate = None
        if iid:
            seen.add(iid)
            old = copy.deepcopy(available[iid])
            require(old.get('selection_status') in ('candidate', 'selected'),
                    'REFERENCE_INVALID', '暂缓或拒绝的验收需先恢复候选状态', 409)
            _sources_valid(project, old)
            item = copy.deepcopy(old)
            if row.get('candidate_id'):
                ac_candidate = _revision_candidate(project, old, row['candidate_id'])
                require(not ac_candidate.get('recommendation_requirement_id')
                        or ac_candidate['recommendation_requirement_id'] == before['id'],
                        'SCOPE_INVALID', '这条验收修订建议不属于当前需求的AI补充范围', 409)
                item['source_refs'] = _merge_sources(old, ac_candidate)
                item['epistemic_status'] = ac_candidate.get('epistemic_status', old.get('epistemic_status'))
            item['statement'] = statement
        else:
            require(not row.get('candidate_id'), 'REFERENCE_INVALID', '验收修订建议必须指向已展示的原验收', 409)
            old = None
            item = dict(id=ident('AC'), kind='acceptance', title=before.get('title', '') + '验收',
                        statement=statement, applies_to='to_be', epistemic_status='reported',
                        source_refs=[], related_refs=[before['id']], selection_status='candidate',
                        revision=project['revision'] + 1)
        item['selection_status'] = 'selected'
        _validate_content(project, ac_candidate or old or {}, item)
        acceptance.append(dict(id=item['id'], before=old, after=item, new=old is None,
                               candidate=copy.deepcopy(ac_candidate)))
    require(selected <= seen, 'REVIEW_INCOMPLETE',
            '本条已有验收必须一起展示核对，不能通过遗漏悄悄移除', 409)
    projected=copy.deepcopy(detail_project if detail_project is not None else project)
    by_id={item['id']:item for item in projected['items']}
    for value in [after,*(row['after'] for row in acceptance)]:
        if value['id'] in by_id:
            by_id[value['id']].update(copy.deepcopy(value))
        else:
            projected['items'].append(copy.deepcopy(value))
    from .requirement_details import require_complete
    require_complete(after, projected)
    results = _question_results(projected, before['id'], {before['id'], *seen},
                                payload.get('confirmed_question_ids', []),
                                answer_implementations=payload.get('answer_implementations'),
                                before_items={item['id']:item for item in project['items']})
    preview = dict(schema='requirement-confirmation-1', expected_revision=project['revision'],
                   requirement_id=before['id'], before=copy.deepcopy(before), after=after,
                   candidate=copy.deepcopy(candidate),
                   acceptance_items=acceptance, question_results=results,
                   confirmed_question_ids=[row['question_id'] for row in results if row['confirm']],
                   created=now())
    from .decision_review import _source_snapshot
    refs = []
    for item in [after, *(row['after'] for row in acceptance)]:
        for ref in item.get('source_refs', []):
            if ref not in refs:
                refs.append(ref)
    preview['source_snapshot'] = _source_snapshot(project, refs)
    preview['preview_hash'] = digest(preview)
    return preview


def apply_question_confirmations(project, question_results):
    """Confirm explicit saved answers after all displayed clause edits are applied."""
    from .understanding import application_hash, answer_targets, confirm_manual_answer
    for result in question_results:
        if not result.get('confirm'):
            continue
        question = next(q for q in project['questions'] if q['id'] == result['question_id'])
        answer, saved_answer, member_count = _question_answer(project, question)
        require(answer == result['answer']
                and (saved_answer['id'] if saved_answer else None) == result.get('saved_answer_id'),
                'STALE_ANSWER', '已保存原话发生变化，请重新核对', 409)
        if question.get('answer') != answer or question.get('status') != 'answered':
            require(member_count == 1 and saved_answer is not None, 'ANSWER_REVIEW_REQUIRED',
                    '只能在当前单一决定中明确确认其完整原话', 409)
            question.update(answer=answer, status='answered', answered_at=saved_answer['created'],
                            answer_source_refs=copy.deepcopy(saved_answer.get('source_refs') or []))
        confirm_manual_answer(project, question, digest(question.get('answer')),
                              {item['id']: application_hash(item) for item in answer_targets(project, question)}, True,
                              answer_implementations={question['id']:result.get('implementation',[])})


def apply_preview(project, preview, store, *, confirm_questions=True, detail_project=None):
    """Validate first, then apply the displayed package in the caller transaction."""
    from .sources import save_source
    items = {item['id']: item for item in project['items']}
    requirement = items[preview['requirement_id']]
    require(requirement == preview['before'], 'STALE_REVISION', '需求已变化，请重新核对', 409)
    candidate = preview.get('candidate')
    from .decision_review import _source_snapshot
    if 'source_snapshot' in preview:
        refs = [{key: ref[key] for key in ('source_id', 'excerpt_id')}
                for ref in preview['source_snapshot']]
        require(_source_snapshot(project, refs) == preview['source_snapshot'],
                'STALE_SOURCE', '已展示的来源内容发生变化，请重新核对', 409)
    changes = [dict(before=preview['before'], after=preview['after'],
                    candidate=candidate), *preview['acceptance_items']]
    projected = copy.deepcopy(detail_project if detail_project is not None else project)
    for change in changes:
        original = next((item for item in projected['items'] if item['id'] == change['after']['id']), None)
        if original is None:
            projected['items'].append(copy.deepcopy(change['after']))
        else:
            original.update(copy.deepcopy(change['after']))
    from .requirement_details import require_complete
    require_complete(preview['after'], projected)
    for row in changes:
        if row['before'] is not None:
            require(items.get(row['after']['id']) == row['before'], 'STALE_REVISION', '关联验收已变化', 409)
            _sources_valid(project, row['before'])
        candidate_row = row.get('candidate')
        if candidate_row:
            require(items.get(candidate_row['id']) == candidate_row, 'STALE_REVISION',
                    'AI建议已变化，请重新核对', 409)
            _revision_candidate(project, row['before'], candidate_row['id'])
        _validate_content(project, candidate_row or row['before'] or {}, row['after'])
    authored = [(row, _manual_text(row.get('candidate') or row['before'] or {}, row['after']))
                for row in changes]
    for row, text in authored:
        item = copy.deepcopy(row['after'])
        if text:
            source = save_source(store, '人工需求整行确认.txt', text.encode('utf-8'), 'goal')
            project['sources'].append(source)
            item['source_refs'] = item.get('source_refs', []) + [
                dict(source_id=source['id'], excerpt_id=excerpt['id']) for excerpt in source['excerpts']]
        if item['id'] in items:
            items[item['id']].update(item)
        else:
            project['items'].append(item)
            items[item['id']] = item
        if item.get('behavior_review_required') or item['kind'] == 'requirement':
            item = items[item['id']]
            item['behavior_review'] = dict(content_hash=behavior_fingerprint(item),
                actor='authenticated_local_user', reviewed_at=now(), method='requirement_confirmation')
    requirement = items[preview['requirement_id']]
    for row in changes:
        candidate_row = row.get('candidate')
        if candidate_row:
            items[candidate_row['id']]['selection_status'] = 'deferred'
            items[row['after']['id']].setdefault('confirmed_revisions', []).append(dict(
                candidate_id=candidate_row['id'], candidate_hash=digest(candidate_row),
                source_refs=copy.deepcopy(candidate_row.get('source_refs', [])),
                answer_refs=copy.deepcopy(candidate_row.get('answer_refs', [])),
                actor='authenticated_local_user', reviewed_at=now()))
    if confirm_questions:
        apply_question_confirmations(project, [row for row in preview['question_results']
                                               if row['question_id'] in preview['confirmed_question_ids']])
    requirement['requirement_confirmation'] = dict(schema='requirement-confirmation-1',
        content_hash=row_fingerprint(project, requirement), actor='authenticated_local_user',
        reviewed_at=now(), preview_hash=preview['preview_hash'])
    project['requirement_confirmation_mode'] = 'table'
    return dict(requirement_id=requirement['id'], acceptance_ids=[row['id'] for row in preview['acceptance_items']],
                confirmed_question_ids=preview['confirmed_question_ids'])


def register(app, store):
    from .requirement_batch_confirmation import register as register_batch_confirmation
    register_batch_confirmation(app, store)

    @app.get('/api/projects/{pid}/requirement-confirmation/state')
    async def state(pid: str):
        return confirmation_state(store.get(pid))

    @app.post('/api/projects/{pid}/requirement-confirmation/preview')
    async def preview(pid: str, body: dict):
        result = build_preview(store.get(pid), body)
        record_id = store.record(pid, 'requirement_confirmation_preview', result)
        return dict(preview_id=record_id, **result)

    @app.post('/api/projects/{pid}/requirement-confirmation/commit')
    async def commit(pid: str, body: dict):
        keys = {'expected_revision', 'preview_id', 'preview_hash', 'idempotency_key', 'review_confirmed'}
        require(set(body) == keys and type(body.get('expected_revision')) is int
                and all(isinstance(body.get(key), str) for key in ('preview_id', 'preview_hash', 'idempotency_key'))
                and 8 <= len(body['idempotency_key']) <= 100,
                'INPUT_REQUIRED', '提交缺少有效版本、预览或提交编号')
        require(body['review_confirmed'] is True, 'REVIEW_REQUIRED', '请明确确认已展示的需求和验收内容', 409)
        request_hash = digest(body)
        def prior_receipt(db=None):
            old = next((r for r in store.records(pid, 'requirement_confirmation_receipt', db)
                        if r['idempotency_key'] == body['idempotency_key']), None)
            if old:
                require(old['request_hash'] == request_hash, 'IDEMPOTENCY_CONFLICT',
                        '同一提交编号已用于不同内容', 409)
            return old
        previous = prior_receipt()
        if previous:
            return dict(project=store.get(pid), receipt=previous, repeated=True)
        try:
            with store.edit(pid, body['expected_revision'], '人工一次确认需求与验收') as (project, db):
                require(prior_receipt(db) is None, 'IDEMPOTENCY_CONFLICT', '相同提交正在处理', 409)
                saved = store.get_record(pid, body['preview_id'], 'requirement_confirmation_preview')
                require(saved['preview_hash'] == body['preview_hash']
                        and saved['preview_hash'] == digest({key: value for key, value in saved.items()
                                                            if key not in ('id', 'preview_hash')}),
                        'SNAPSHOT_INVALID', '预览内容校验失败，请重新核对', 409)
                require(saved['expected_revision'] == body['expected_revision'], 'STALE_REVISION', '预览版本已变化', 409)
                summary = apply_preview(project, saved, store)
                receipt = dict(idempotency_key=body['idempotency_key'], request_hash=request_hash,
                               preview_id=body['preview_id'], result_revision=project['revision'] + 1, **summary)
                receipt['id'] = store.record(pid, 'requirement_confirmation_receipt', receipt, db=db)
            return dict(project=project, receipt=receipt, repeated=False)
        except Problem as exc:
            if exc.code == 'STALE_REVISION':
                previous = prior_receipt()
                if previous:
                    return dict(project=store.get(pid), receipt=previous, repeated=True)
            raise
