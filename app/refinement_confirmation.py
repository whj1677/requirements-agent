"""One explicit review of the displayed refinement table, committed atomically."""
import copy
import re
import tempfile
from pathlib import Path
from types import SimpleNamespace

from .core import Problem, digest, now, require
from .decision_review import (apply_review, build_preview as decision_preview,
                              decision_ids, save_raw_answer)
from .product_flow import INTAKE, SCOPE, blockers, checkpoint, fingerprint
from .requirement_batch_confirmation import (ROW_KEYS, apply_batch_preview,
                                             build_batch_preview)
from .requirement_confirmation import FIELDS, _manual_text, _sources_valid, _validate_content
from .requirements import behavior_fingerprint, reconcile_items
from .scope_context import save_context_edit, scope_field_rules, validate_context_edit
from .understanding import (answer_targets, current_decision_answer,
                            invalidate_answer_applications, question_fingerprint)

RECEIPT_KIND = 'refinement_confirmation_receipt'
UNKNOWN_ANSWER = re.compile(r'\s*(?:暂时?|暂且|目前|还|尚|仍)?(?:不确定|不知道|不清楚|未确定|未明确|未知|未定|待定|待确认|待核对|待补充|待澄清|待讨论|待决策|后续再说)[。！!\s]*')
SCOPE_EXCLUSION_SCHEMA = 'refinement-scope-exclusion-1'


class TableIssues(Problem):
    def __init__(self, issues, code='REFINEMENT_INCOMPLETE'):
        self.issues = issues
        super().__init__(code, '；'.join(row['message'] for row in issues), 409)


def issue(message, kind='scope', **location):
    return dict(kind=kind, message=message, **location)


def check(condition, message, kind='scope', **location):
    if not condition:
        raise TableIssues([issue(message, kind, **location)])


def active(question):
    return not question.get('superseded_by') and not question.get('out_of_scope_reason')


def _sync_scope_questions(project, scope):
    """Derive reversible scope deferral, never an answer or a guessed relation."""
    items = {item['id']: item for item in project['items']}
    roots = {iid: item for iid, item in items.items() if item['kind'] == 'requirement'
             and item.get('applies_to') == 'to_be' and not item.get('target_item_id')
             and item.get('selection_status') in ('candidate', 'selected')}

    def associated_roots(question):
        refs = question.get('related_refs') or []
        if not refs:
            return None  # A global decision cannot be excluded by row scope.
        result = set()
        for ref in refs:
            if ref in roots:
                result.add(ref)
                continue
            item = items.get(ref)
            if (not item or item.get('kind') != 'acceptance' or item.get('target_item_id')
                    or item.get('applies_to') != 'to_be'
                    or item.get('selection_status') not in ('candidate', 'selected')):
                return None
            linked = set(item.get('related_refs') or [])
            if not linked <= set(roots):
                return None  # Do not guess the meaning of another clause link.
            linked.update(iid for iid, root in roots.items() if ref in root.get('related_refs', []))
            if not linked:
                return None
            result.update(linked)
        return result

    for question in project['questions']:
        if question.get('superseded_by'):
            continue
        mark = question.get('scope_exclusion') or {}
        ours = isinstance(mark, dict) and mark.get('schema') == SCOPE_EXCLUSION_SCHEMA
        if ours and (question.get('out_of_scope_reason') != mark.get('reason')
                     or question.get('scope_decided_at') != mark.get('prior_scope_decided_at')):
            # A subsequent explicit scope decision owns the reason. Never undo it.
            question.pop('scope_exclusion', None)
            ours = False
        related = associated_roots(question)
        excluded = bool(related and not related.intersection(scope))
        if ours and not excluded:
            question.pop('out_of_scope_reason', None)
            question.pop('scope_exclusion', None)
            # Keep actual words and answered/open status, but make restored
            # business decisions visible for a fresh explicit table review.
            question.update(decision_coverage='pending', understanding_status='pending')
        elif excluded and not question.get('out_of_scope_reason'):
            reason = '随关联需求暂不纳入本期；重新纳入后须核对：' + '、'.join(sorted(related))
            question['out_of_scope_reason'] = reason
            question['scope_exclusion'] = dict(schema=SCOPE_EXCLUSION_SCHEMA, reason=reason,
                requirement_ids=sorted(related), created_at=now(),
                prior_scope_decided_at=question.get('scope_decided_at'))


def _validate(project, body):
    context = body.get('context')
    check(isinstance(context, dict) and set(context) <= {'values', 'field_states', 'scope_profile', 'scope_ids'}
          and {'values', 'field_states', 'scope_ids'} <= set(context), '请提交表格中的摘要和本期范围')
    check(isinstance(context['values'], dict) and isinstance(context['field_states'], dict)
          and all(isinstance(state, dict) for state in context['field_states'].values())
          and (context.get('scope_profile') is None or isinstance(context['scope_profile'], dict)),
          '摘要内容、适用状态或场景格式无效', 'context')
    validate_context_edit(context['values'], context.get('scope_profile'), context['field_states'])
    scope = context['scope_ids']
    roots = {item['id'] for item in project['items'] if item['kind'] == 'requirement'
             and item.get('applies_to') == 'to_be' and not item.get('target_item_id')
             and item.get('selection_status') in ('candidate', 'selected')}
    check(isinstance(scope, list) and all(isinstance(iid, str) for iid in scope)
          and len(scope) == len(set(scope)) and set(scope) <= roots,
          '本期范围包含重复、历史或不可用的需求，请重新核对')
    check(bool(scope), '请保留至少一条本期需求')
    rows = body.get('rows')
    check(isinstance(rows, list) and 1 <= len(rows) <= 100
          and all(isinstance(row, dict) and set(row) <= ROW_KEYS
                  and {'requirement_id', 'statement', 'behavior', 'change_type', 'acceptance_items'} <= set(row)
                  and isinstance(row.get('requirement_id'), str) for row in rows),
          '请提交表格中完整展示的1至100条本期需求')
    ids = [row['requirement_id'] for row in rows]
    check(len(ids) == len(set(ids)) and set(ids) == set(scope),
          '必须逐行展示并确认本期范围内的全部需求，不可遗漏或重复')
    _sync_scope_questions(project, set(scope))
    answers = body.get('answers')
    check(isinstance(answers, list) and len(answers) <= 300 and all(isinstance(row, dict)
          and set(row) == {'question_id', 'answer'} and isinstance(row['question_id'], str)
          and isinstance(row['answer'], str) and len(row['answer']) <= 6000 for row in answers),
          '请提交每个独立业务问题及其原话回答', 'question')
    supplied = {row['question_id']: row['answer'] for row in answers}
    known = {q['id']: q for q in project['questions'] if active(q)}
    check(len(supplied) == len(answers) and set(supplied) <= set(known),
          '回答包含重复、已拆分或不再适用的问题', 'question')
    for row in rows:
        qids = row.get('confirmed_question_ids', [])
        check(isinstance(qids, list) and all(isinstance(qid, str) for qid in qids)
              and len(qids) == len(set(qids)) and set(qids) <= set(supplied),
              '待核对的回答必须在本次表格中明确展示', 'requirement', item_id=row['requirement_id'])
        check(all(row['requirement_id'] in known[qid].get('related_refs', []) for qid in qids),
              '回答与当前需求没有直接关联', 'requirement', item_id=row['requirement_id'])
    return context, rows, supplied



def _linked_clause_preview(project, body, supplied):
    """Only directly bound, visibly submitted original clauses join this review."""
    rows = body.get('linked_clauses', [])
    check(isinstance(rows, list) and len(rows) <= 300 and all(
        isinstance(row, dict) and set(row) == {'item_id', 'statement', 'behavior'}
        and isinstance(row['item_id'], str) for row in rows),
        '关联条款须提交编号、正文及完整业务说明', 'clause')
    ids = [row['item_id'] for row in rows]
    check(len(ids) == len(set(ids)), '同一关联条款只需核对一次，请勿重复提交', 'clause')
    allowed = {item['id'] for question in project['questions'] if active(question)
               and supplied.get(question['id'], '').strip()
               and not UNKNOWN_ANSWER.fullmatch(supplied[question['id']])
               for item in answer_targets(project, question)}
    items = {item['id']: item for item in project['items']}
    result = []
    from .decision_review import _source_snapshot
    for row in rows:
        iid = row['item_id']; before = items.get(iid)
        title = (before or {}).get('title') or '关联条款'
        check(iid in allowed and before is not None and before.get('kind') in ('rule', 'constraint', 'goal')
              and before.get('applies_to') == 'to_be' and not before.get('target_item_id')
              and not before.get('superseded_by') and before.get('selection_status') in ('candidate', 'selected'),
              '「' + title + '」不是本次已填写回答直接关联的可确认条款', 'clause', item_id=iid)
        check(isinstance(row['statement'], str) and row['statement'].strip() and len(row['statement']) <= 6000,
              '请填写「' + title + '」的条款正文（最多6000字符）', 'clause', item_id=iid)
        behavior = row['behavior']
        check(isinstance(behavior, dict) and set(behavior) == set(FIELDS)
              and all(isinstance(value, str) and len(value) <= 6000 for value in behavior.values()),
              '请完整展示「' + title + '」的业务说明；不适用字段可以留空', 'clause', item_id=iid)
        after = copy.deepcopy(before)
        after['statement'] = row['statement']
        # Empty displayed fields do not manufacture a content revision in legacy clauses.
        if behavior != {field: before.get('behavior', {}).get(field, '') for field in FIELDS}:
            after['behavior'] = copy.deepcopy(behavior)
        after['selection_status'] = 'selected'
        try:
            _sources_valid(project, before)
            _validate_content(project, before, after)
            snapshot = _source_snapshot(project, before.get('source_refs', []))
        except Problem as error:
            message = '请核对「' + title + '」的有效来源后再确认' if error.code == 'REFERENCE_INVALID' else error.message.replace(iid, '「' + title + '」')
            raise TableIssues([issue(message, 'clause', item_id=iid)], error.code) from error
        result.append(dict(before=copy.deepcopy(before), after=after, source_snapshot=snapshot))
    return result


def _apply_linked_clauses(project, preview, staging):
    from .decision_review import _source_snapshot
    from .sources import save_source
    items = {item['id']: item for item in project['items']}
    for row in preview:
        before, after = row['before'], row['after']
        iid = before['id']; title = before.get('title') or '关联条款'
        check(items.get(iid) == before, '「' + title + '」已变化，请重新核对', 'clause', item_id=iid)
        check(_source_snapshot(project, before.get('source_refs', [])) == row['source_snapshot'],
              '「' + title + '」的来源已变化，请重新核对', 'clause', item_id=iid)
        text = _manual_text(before, after)
        changed = copy.deepcopy(after)
        if text:
            source = save_source(staging, '人工关联条款确认.txt', text.encode('utf-8'), 'goal')
            project['sources'].append(source)
            changed['source_refs'] = list(changed.get('source_refs', [])) + [
                dict(source_id=source['id'], excerpt_id=excerpt['id']) for excerpt in source['excerpts']]
        if changed.get('behavior_review_required'):
            changed['behavior_review'] = dict(content_hash=behavior_fingerprint(changed),
                actor='authenticated_local_user', reviewed_at=now(), method='refinement_confirmation')
        items[iid].update(changed)
    return [row['after']['id'] for row in preview]


def _stage_answers(project, supplied, staging):
    """Preserve exact leaf words; a group creates one source, never N copies."""
    known = {q['id']: q for q in project['questions'] if active(q)}
    # Historical/out-of-scope leaves remain untouched in the real project.
    # The legacy decision helper otherwise includes an excluded group member.
    if supplied:
        project.setdefault('decision_answers', [])
    decision_project = dict(project, questions=list(known.values()))
    bundles = {}
    for qid in supplied:
        question = known[qid]
        bundles.setdefault(question.get('decision_group_id') or qid, []).append(question)
    result = []
    for group_id, supplied_questions in bundles.items():
        ids = decision_ids(decision_project, group_id)
        check(set(ids) <= set(supplied) and set(ids) <= set(known),
              '请在表格中核对这个决定组的每一个当前子问题', 'question', question_id=supplied_questions[0]['id'])
        questions = [known[qid] for qid in ids]
        for question in questions:
            qid = question['id']; answer = supplied[qid]
            if not answer.strip() or UNKNOWN_ANSWER.fullmatch(answer):
                continue
            proposal = question.get('decision_review_proposal') or {}
            compound = len(question.get('decision_points') or []) > 1 or (
                not question.get('decision_group_id') and len(proposal.get('decisions') or []) > 1
                and proposal.get('question_hash') == digest([question_fingerprint(question)])
                and proposal.get('answer_hash') == digest(question.get('answer')))
            check(not compound, '此题仍包含多个独立决定，请先分别列出后逐项填写，不能整题确认',
                  'question', question_id=qid)
            check(not question.get('decision_pending_group_ids'),
                  '此回答仍有未核对的关联修改，不能在未展示修改时确认', 'question', question_id=qid)
        grouped = bool(questions[0].get('decision_group_id'))
        existing = current_decision_answer(project, questions[0]) if grouped else None
        # Existing group source may be reused only if every submitted leaf quote
        # is literally in it. New/changed words get their own source and invalidate
        # model candidates still bound to the old saved answer.
        if existing and all(not supplied[qid].strip() or supplied[qid] in existing['text'] for qid in ids):
            saved_id = existing['id']
        elif not grouped and questions[0].get('answer') == supplied[ids[0]] and supplied[ids[0]].strip():
            question = questions[0]
            saved = next((row for row in project.get('decision_answers', [])
                          if row.get('id') == question.get('decision_answer_id')
                          and row.get('question_id') == group_id
                          and row.get('decision_ids') == ids
                          and row.get('text') == supplied[ids[0]]
                          and row.get('answer_hash') == digest(supplied[ids[0]])
                          and row.get('source_refs', []) == question.get('answer_source_refs', [])), None)
            saved_id = saved['id'] if saved else None
        else:
            text = '\n\n'.join('[' + q['id'] + ']\n' + supplied[q['id']] for q in questions
                               if supplied[q['id']].strip()) if grouped else supplied[ids[0]]
            if not text.strip():
                # No manufactured source for an empty answer; final gate retains
                # any unresolved critical question. Explicit clearing is handled below.
                saved_id = None
            else:
                check(len(text) <= 6000, '同组回答超过6000字符，请缩短各项原话', 'question', question_id=ids[0])
                saved_id = save_raw_answer(decision_project, group_id, text, staging)['id']
        result.append((group_id, ids, saved_id))
    return result


def _review_answers(project, bundles, supplied, displayed_ids, implementations):
    known = {q['id']: q for q in project['questions']}
    decision_project = dict(project, questions=[q for q in project['questions'] if active(q)])
    confirmed, pending, implementation_issues = [], [], []
    for group_id, ids, saved_id in bundles:
        coverage, answered, targets = [], [], set()
        for qid in ids:
            answer = supplied[qid]
            clear = bool(answer.strip() and not UNKNOWN_ANSWER.fullmatch(answer))
            coverage.append(dict(decision_id=qid, status='clear' if clear else 'pending',
                                 answer_quote=answer if answer.strip() else ''))
            if clear:
                direct = {item['id'] for item in answer_targets(project, known[qid])}
                missing = direct - displayed_ids
                if missing:
                    items = {item['id']: item for item in project['items']}
                    first = sorted(missing)[0]
                    labels = '、'.join('「' + (items[iid].get('title') or '关联条款') + '」' for iid in sorted(missing))
                    kind = 'clause' if items[first]['kind'] in ('rule', 'constraint', 'goal') else 'requirement'
                    raise TableIssues([issue('请在表格中核对关联条款' + labels + '后再确认这条回答',
                                             kind, item_id=first, question_id=qid)])
                targets.update(direct); answered.append(qid)
        if not any(supplied[qid].strip() for qid in ids):
            from .understanding import record_answer
            for qid in ids:
                question = known[qid]
                if question.get('answer') is not None:
                    record_answer(question)
                question.update(answer=None, status='open', understanding_status='pending')
            continue
        groups = [dict(id='table-answer', decision_ids=answered, target_item_ids=sorted(targets),
                       candidate_item_ids=[])] if answered and targets else []
        try:
            view = decision_preview(decision_project, dict(expected_revision=project['revision'], question_id=group_id,
                answer_id=saved_id, coverage=coverage, groups=groups,
                answer_implementations={qid: implementations[qid] for qid in ids if qid in implementations}))
            apply_review(decision_project, view, [group['id'] for group in groups])
        except Problem as error:
            raise TableIssues([issue(error.message, 'question', question_id=ids[0])], error.code) from error
        # Shared targets are reviewed once. Retain each leaf's direct binding,
        # rather than giving every leaf the union of the group's other clauses.
        for qid in answered:
            question = known[qid]
            direct = {item['id'] for item in answer_targets(project, question)}
            question['answer_applications'] = {iid: value for iid, value in
                question.get('answer_applications', {}).items() if iid in direct}
            question['applied_item_ids'] = (sorted(question['answer_applications'])
                                            if question.get('understanding_status') == 'applied' else [])
            question['applied_requirement_ids'] = sorted(item['id'] for item in answer_targets(project, question)
                if item['kind'] == 'requirement' and item['id'] in question['applied_item_ids'])
            if question.get('understanding_status') == 'applied':
                confirmed.append(qid)
            else:
                pending.append(qid)
        implementation_issues.extend(dict(row, kind='question')
            for row in view.get('implementation_issues', []))
    return confirmed, pending, implementation_issues


def _flow_issues(project):
    issues = []
    context = project.get('product_context') or {}
    states = project.get('context_field_states') or {}
    for field, rule in scope_field_rules(project).items():
        value = context.get(field, '')
        if (rule['required'] and (states.get(field) or {}).get('status') != 'none'
                and isinstance(value, str) and UNKNOWN_ANSWER.fullmatch(value)):
            issues.append(issue('请明确' + {**INTAKE, **SCOPE}[field] + '，不能将待确认内容标为已明确',
                                'context', field=field))
    for step in (1, 2, 3):
        for blocker in blockers(project, step):
            for action in blocker.get('actions') or [{}]:
                kind = action.get('kind', 'scope')
                kind = {'answer': 'question', 'behavior': 'requirement', 'candidate': 'scope'}.get(kind, kind)
                if kind not in ('context', 'requirement', 'question', 'source', 'scope'):
                    kind = 'scope'
                row = issue(blocker['message'], kind, code=blocker.get('code'), **{key: action[key]
                    for key in ('field', 'item_id', 'question_id', 'path', 'object_id') if key in action})
                if action.get('path'):
                    row['field'] = action['path']
                if row not in issues:
                    issues.append(row)
    return issues


def _reuse_unchanged_reviews(project, before):
    """A second explicit review is not a new business input to an existing PRD.

    Run all validation and application first. Reuse only evidence whose actual
    binding is identical; never exclude business fields from the project hash.
    The new table receipt still records this confirmation attempt.
    """
    def reuse(current, previous, field, volatile=('reviewed_at',)):
        value, old = current.get(field), previous.get(field)
        if (isinstance(value, dict) and isinstance(old, dict) and old
                and {key: item for key, item in value.items() if key not in volatile}
                == {key: item for key, item in old.items() if key not in volatile}):
            current[field] = copy.deepcopy(old)

    items = {item['id']: item for item in before['items']}
    for item in project['items']:
        old = items.get(item['id'], {})
        reuse(item, old, 'behavior_review')
        reuse(item, old, 'requirement_confirmation', ('reviewed_at', 'preview_hash'))
    questions = {question['id']: question for question in before['questions']}
    for question in project['questions']:
        old = questions.get(question['id'], {})
        reuse(question, old, 'decision_coverage_review')
        for iid in question.get('answer_applications', {}):
            reuse(question['answer_applications'], old.get('answer_applications', {}), iid)
        if ('answered_at' in old and question.get('answer') == old.get('answer')
                and question.get('status') == old.get('status')
                and question.get('answer_source_refs') == old.get('answer_source_refs')):
            question['answered_at'] = old['answered_at']


def confirm_table(store, pid, body):
    keys = {'expected_revision', 'idempotency_key', 'review_confirmed', 'context', 'rows', 'answers'}
    require(keys <= set(body) <= keys | {'linked_clauses','answer_implementations'} and type(body.get('expected_revision')) is int
            and isinstance(body.get('idempotency_key'), str) and 8 <= len(body['idempotency_key']) <= 100,
            'INPUT_REQUIRED', '提交缺少有效版本、提交编号或完整表格')
    require(body['review_confirmed'] is True, 'REVIEW_REQUIRED', '请明确确认表格展示的摘要、需求、验收和回答', 409)
    request_hash = digest(body)

    def prior_receipt(db=None):
        old = next((record for record in store.records(pid, RECEIPT_KIND, db)
                    if record['idempotency_key'] == body['idempotency_key']), None)
        if old:
            require(old['request_hash'] == request_hash, 'IDEMPOTENCY_CONFLICT', '同一提交编号已用于不同内容', 409)
        return old

    def repeated_response(receipt):
        current = store.get(pid)
        require(current['revision'] == receipt['result_revision'], 'STALE_REVISION',
                '该次确认已完成，但项目之后又有修改，请核对当前表格后重新确认', 409)
        return dict(confirmed=True, project=current, receipt=receipt, repeated=True,
            destination=receipt.get('destination','delivery'),
            implementation_pending_ids=receipt.get('implementation_pending_ids',[]),
            issues=receipt.get('implementation_issues',[]))

    previous = prior_receipt()
    if previous:
        return repeated_response(previous)
    created_files, committed = [], False
    try:
        with tempfile.TemporaryDirectory(prefix='refinement-confirmation-') as temporary:
            staging = SimpleNamespace(folder=Path(temporary))
            with store.edit(pid, body['expected_revision'], '人工确认完善需求表格并进入交付') as (project, db):
                require(prior_receipt(db) is None, 'IDEMPOTENCY_CONFLICT', '相同提交正在处理', 409)
                before = copy.deepcopy(project)
                context, rows, supplied = _validate(project, body)
                from .answer_implementation import validate_implementation_map
                implementations = validate_implementation_map(project, body.get('answer_implementations', {}))
                check(set(implementations) <= set(supplied), '落文核对只能对应本次展示的回答', 'question')
                clauses = _linked_clause_preview(project, body, supplied)
                save_context_edit(project, context['values'], context['scope_ids'],
                                  context.get('scope_profile'), context['field_states'])
                bundles = _stage_answers(project, supplied, staging)
                # Answers are explicitly reviewed once, collectively, after all
                # displayed clauses are final. The batch still guards sources,
                # candidate bindings, scope, shared AC and immutable clause content.
                inputs = [dict(row, confirmed_question_ids=[]) for row in rows]
                preview = build_batch_preview(project, dict(expected_revision=project['revision'], rows=inputs))
                summary = apply_batch_preview(project, preview, staging)
                confirmed_clauses = _apply_linked_clauses(project, clauses, staging)
                displayed = {row['requirement_id'] for row in preview['rows']}
                displayed.update(ac['id'] for row in preview['rows'] for ac in row['acceptance_items'])
                displayed.update(confirmed_clauses)
                confirmed, pending, implementation_issues = _review_answers(
                    project, bundles, supplied, displayed, implementations)
                # Store.edit performs these same deterministic transitions at
                # commit. Checkpoint against that final content/version now, not
                # against pre-reconciliation hashes that would immediately expire.
                reconcile_items(before, project)
                invalidate_answer_applications(project, before)
                _reuse_unchanged_reviews(project, before)
                issues = _flow_issues(project)
                # Nonempty answers may be saved without claiming they are in the
                # text. Keep the table open and leave step 3 unconfirmed. Other
                # incomplete inputs retain the existing atomic failure behavior.
                issues = [row for row in issues if not (
                    row.get('question_id') in pending and row.get('code') == 'answer_review_pending')]
                if issues:
                    raise TableIssues(issues)
                checked_steps = [1, 2] if pending else [1, 2, 3]
                for step in checked_steps:
                    checkpoint(project, step, fingerprint(project, step))
                if pending:
                    project.get('stage_checks', {}).pop('3', None)
                receipt = dict(idempotency_key=body['idempotency_key'], request_hash=request_hash,
                    result_revision=project['revision'] + 1, results=summary['results'],
                    confirmed_question_ids=confirmed, confirmed_clause_ids=confirmed_clauses,
                    stage_checks=checked_steps, preview_hash=preview['preview_hash'],
                    destination='refinement' if pending else 'delivery',
                    implementation_pending_ids=pending, implementation_issues=implementation_issues)
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
        return dict(confirmed=True, project=project, receipt=receipt, repeated=False,
            destination=receipt['destination'], implementation_pending_ids=pending, issues=implementation_issues)
    except BaseException as error:
        if not committed:
            for path in created_files:
                path.unlink(missing_ok=True)
        if isinstance(error, Problem) and error.code == 'STALE_REVISION':
            previous = prior_receipt()
            if previous:
                return repeated_response(previous)
        if isinstance(error, TableIssues):
            return dict(confirmed=False, code=error.code, message=error.message, issues=error.issues)
        if isinstance(error, Problem) and hasattr(error, 'issues'):
            return dict(confirmed=False, code=error.code, message=error.message,
                        issues=[dict(row, kind=row.get('kind', 'requirement'), field=row.get('path', '')) for row in error.issues])
        if isinstance(error, Problem) and error.code not in ('STALE_REVISION', 'IDEMPOTENCY_CONFLICT', 'NOT_FOUND'):
            return dict(confirmed=False, code=error.code, message=error.message,
                        issues=[issue(error.message)])
        raise


def register(app, store):
    @app.post('/api/projects/{pid}/refinement-confirmation')
    async def refinement_confirmation(pid: str, body: dict):
        return confirm_table(store, pid, body)
