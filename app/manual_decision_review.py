"""Human-authored clause edits using the existing immutable review/commit flow.

The preview binds only directly related original clauses. It proposes coverage;
only the explicit commit confirms that the displayed clauses carry the answer.
"""
import copy

from .core import digest, require
from .decision_review import _answer, build_preview, decision_ids, prepare_implementations, review_question_hash
from .understanding import answer_targets

BEHAVIOR_FIELDS = {'actor', 'entry', 'flow', 'data', 'permissions', 'result', 'exceptions'}


def manual_edit_text(before, after):
    """Only newly authored fields count as a new human source statement."""
    parts = []
    if before.get('statement') != after.get('statement'):
        parts.append(after['statement'])
    for key, value in (after.get('behavior') or {}).items():
        if (before.get('behavior') or {}).get(key) != value:
            parts.append(key + '：' + value)
    return '\n'.join(parts)


def validate_manual_target(project, target):
    from .result_conditions import validate_item
    from .source_fidelity import validate_item as validate_source_fidelity
    validate_item(target['after'])
    validate_source_fidelity(target['after'], project,
                             decision_text=manual_edit_text(target['before'], target['after']))


def build_manual_preview(project, payload):
    require(project['revision'] == payload['expected_revision'], 'STALE_REVISION',
            '页面版本已过期，请刷新核对', 409)
    ids = decision_ids(project, payload['question_id'])
    questions = {q['id']: q for q in project['questions']}
    answer_id = payload.get('answer_id')
    if not answer_id and len(ids) > 1:
        current_ids = {questions[qid].get('decision_answer_id') for qid in ids}
        require(len(current_ids) == 1 and None not in current_ids, 'ANSWER_REQUIRED',
                '请先保存这一组回答，不能拼接不同版本的原话', 409)
        answer_id = next(iter(current_ids))
    answer = _answer(project, payload['question_id'], answer_id)
    if 'expected_answer_text' in payload:
        require(isinstance(payload['expected_answer_text'], str)
                and payload['expected_answer_text'] == answer['text'],
                'STALE_ANSWER', '已保存的回答发生变化，请对照最新原话后重新核对', 409)
    # A known compound legacy answer still needs its explicit split review.
    if len(ids) == 1:
        proposal = questions[ids[0]].get('decision_review_proposal') or {}
        require(not (len(proposal.get('decisions') or []) > 1
                     and proposal.get('question_hash') == review_question_hash(project, ids[0])
                     and proposal.get('answer_hash') == answer['answer_hash']),
                'DECISION_MAPPING_REQUIRED', '这条回答包含多个独立决定，请先逐项核对，不能整条确认', 409)
    rows = payload.get('decisions')
    if rows is None:
        require(len(ids) == 1, 'REVIEW_REQUIRED', '多个独立决定须逐项明确是否已回答', 409)
        rows = [dict(decision_id=ids[0], confirmed=True, answer_quote=answer['text'])]
    require(isinstance(rows, list) and len(rows) == len(ids)
            and all(isinstance(row, dict) for row in rows)
            and all(isinstance(row.get('decision_id'), str) for row in rows)
            and {row.get('decision_id') for row in rows} == set(ids),
            'REFERENCE_INVALID', '须逐项核对全部独立决定，不可遗漏或重复', 409)
    coverage = []
    confirmed = []
    for row in rows:
        require(isinstance(row.get('confirmed'), bool), 'INPUT_REQUIRED', '请明确是否确认这项回答')
        quote = row.get('answer_quote', answer['text'] if len(ids) == 1 else '')
        require(isinstance(quote, str) and (not quote or quote in answer['text'])
                and (not row['confirmed'] or bool(quote)),
                'REFERENCE_INVALID', '每个已明确的决定需要对应的原话，不能推测或补造', 409)
        coverage.append(dict(decision_id=row['decision_id'],
                             status='clear' if row['confirmed'] else 'pending',
                             answer_quote=quote if row['confirmed'] else ''))
        if row['confirmed']:
            confirmed.append(row['decision_id'])
    targets = {}
    for qid in confirmed:
        related = answer_targets(project, questions[qid])
        require(related, 'REFERENCE_INVALID', '这条回答还没有关联需求内容，请先核对问题关联', 409)
        for item in related:
            require(item.get('selection_status') == 'selected', 'REVIEW_REQUIRED',
                    '请先明确采纳关联条款：' + item['id'], 409)
            targets[item['id']] = item
    edits = payload.get('edits', [])
    require(isinstance(edits, list) and all(isinstance(edit, dict) for edit in edits),
            'INPUT_REQUIRED', '条款修改格式无效')
    edited = {}
    for edit in edits:
        iid = edit.get('item_id')
        require(isinstance(iid, str) and iid in targets and iid not in edited,
                'REFERENCE_INVALID', '只能修改当前已明确回答直接关联的原条款，不可重复或跨项', 409)
        require(set(edit) <= {'item_id', 'statement', 'behavior'}
                and ('statement' in edit or 'behavior' in edit),
                'INPUT_REQUIRED', '此处只修改需求正文和已展示的业务说明')
        after = copy.deepcopy(targets[iid])
        if 'statement' in edit:
            require(isinstance(edit['statement'], str) and edit['statement'].strip()
                    and len(edit['statement']) <= 6000, 'INPUT_REQUIRED', '需求正文不能为空或超过6000字符')
            after['statement'] = edit['statement']
        if 'behavior' in edit:
            behavior = edit['behavior']
            require(isinstance(behavior, dict) and set(behavior) <= BEHAVIOR_FIELDS
                    and all(isinstance(v, str) and len(v) <= 6000 for v in behavior.values()),
                    'INPUT_REQUIRED', '业务说明字段或内容无效')
            after['behavior'] = {**after.get('behavior', {}), **behavior}
        edited[iid] = after
    review_ids = payload.get('review_behavior_item_ids', [])
    require(isinstance(review_ids, list) and all(isinstance(iid, str) for iid in review_ids)
            and len(set(review_ids)) == len(review_ids) and set(review_ids) <= set(targets),
            'REFERENCE_INVALID', '业务说明核对只适用于本次完整展示的关联条款', 409)
    groups = ([dict(id='manual-answer', title='核对回答与关联需求内容', decision_ids=confirmed,
                    target_item_ids=list(targets), candidate_item_ids=[])] if confirmed else [])
    preview = build_preview(project, dict(expected_revision=payload['expected_revision'],
                            question_id=payload['question_id'], answer_id=answer_id,
                             coverage=coverage, groups=groups,
                             answer_implementations=payload.get('answer_implementations')))
    for group in preview['groups']:
        for target in group['targets']:
            target['after'] = edited.get(target['id'], target['after'])
            target['manual_edit'] = target['before'] != target['after']
            target['review_behavior'] = target['id'] in review_ids
            validate_manual_target(project, target)
    preview['review_mode'] = 'manual_clause_revision'
    prepare_implementations(project,preview,payload.get('answer_implementations'))
    preview['preview_hash'] = digest({key: value for key, value in preview.items() if key != 'preview_hash'})
    return preview
