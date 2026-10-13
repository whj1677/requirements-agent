"""Human-reviewed, exact text witnesses for applying saved answers.

The program verifies identity, location and freshness. A witness is a recorded
human review of these words, not an automatic proof of arbitrary semantics.
"""
import copy
import re

from .core import digest, now, require

CONTRACT_VERSION = 2
BEHAVIOR_FIELDS = ('actor', 'entry', 'flow', 'data', 'permissions', 'result', 'exceptions')
FIELDS = ('statement', *(f'behavior.{field}' for field in BEHAVIOR_FIELDS))
DETAIL_PATH = re.compile(r'^detail_contract\.(?:fields|interactions|state_transitions|flow\.(?:nodes|edges))\.[A-Za-z0-9_-]{1,80}\.[a-z_]+$')


def valid_field(field):
    return isinstance(field, str) and (field in FIELDS or bool(DETAIL_PATH.fullmatch(field)))


def field_text(item, field):
    require(valid_field(field) and (item.get('kind') != 'acceptance' or field == 'statement'),
            'ANSWER_IMPLEMENTATION_INVALID',
            '回答落实位置只能指向正文、已有业务说明或稳定编号的详细需求属性；验收只能选择正文：' + item['id'], 409)
    if field.startswith('detail_contract.'):
        from .requirement_details import detail_property_text
        value = detail_property_text(item, field)
        require(value is not None, 'ANSWER_IMPLEMENTATION_INVALID', '回答落实的详细需求属性不存在：' + field, 409)
        return value
    value = item.get('statement', '') if field == 'statement' else (item.get('behavior') or {}).get(field[9:], '')
    return value if isinstance(value, str) else ''


def validate_implementation_map(project, value):
    value = {} if value is None else value
    known = {q['id'] for q in project.get('questions', []) if not q.get('superseded_by') and not q.get('out_of_scope_reason')}
    require(isinstance(value, dict) and len(value) <= 300 and set(value) <= known,
            'ANSWER_IMPLEMENTATION_INVALID', '回答落实位置包含无效或不再适用的问题编号', 409)
    for qid, rows in value.items():
        require(isinstance(rows, list) and len(rows) <= 100,
                'ANSWER_IMPLEMENTATION_INVALID', qid + ' 的回答落实位置格式不正确', 409)
        for row in rows:
            require(isinstance(row, dict) and set(row) == {'item_id', 'field', 'quote'}
                    and all(isinstance(row[key], str) for key in row)
                    and bool(row['item_id']) and valid_field(row['field'])
                    and bool(row['quote'].strip()) and len(row['quote']) <= 6000,
                    'ANSWER_IMPLEMENTATION_INVALID', qid + ' 的回答落实位置需要条款、字段和非空原文', 409)
    return copy.deepcopy(value)


def validate_implementations(project, question, witnesses, *, targets=None):
    from .understanding import answer_targets
    targets = answer_targets(project, question) if targets is None else targets
    by_id = {item['id']: item for item in targets}
    require(by_id and isinstance(witnesses, list) and witnesses,
            'ANSWER_IMPLEMENTATION_REQUIRED', question['id'] + ' 请选择回答已落实到哪条正文或业务说明，并核对对应原文', 409)
    validated = validate_implementation_map(project, {question['id']: witnesses})[question['id']]
    grouped = {}
    for witness in validated:
        iid, field, quote = witness['item_id'], witness['field'], witness['quote']
        require(iid in by_id, 'ANSWER_IMPLEMENTATION_INVALID',
                question['id'] + ' 的回答只能落实到直接关联原条款：' + iid, 409)
        text = field_text(by_id[iid], field)
        require(quote in text, 'ANSWER_IMPLEMENTATION_STALE',
                question['id'] + ' 所选原文不在本次确认后的条款中，请重新选择：' + iid + ' ' + field, 409)
        row = dict(field=field, quote=quote)
        if row not in grouped.setdefault(iid, []):
            grouped[iid].append(row)
    require(set(grouped) == set(by_id), 'ANSWER_IMPLEMENTATION_REQUIRED',
            question['id'] + ' 还缺直接关联条款的回答落实位置：' + '、'.join(sorted(set(by_id) - set(grouped))), 409)
    # Only an explicit question-to-location anchor can identify the same unknown.
    # Other questions' unknown wording in this or another field is not scanned.
    for anchor in question.get('unresolved_locations') or []:
        require(isinstance(anchor, dict) and set(anchor) == {'item_id', 'field', 'quote'}
                and all(isinstance(anchor[key], str) for key in anchor)
                and bool(anchor['quote'].strip()), 'ANSWER_IMPLEMENTATION_INVALID',
                question['id'] + ' 的未决位置记录无效，请重新核对问题', 409)
        if anchor['item_id'] not in by_id:
            continue
        require(anchor['quote'] not in field_text(by_id[anchor['item_id']], anchor['field']),
                'ANSWER_IMPLEMENTATION_UNRESOLVED', question['id'] + ' 的同一未决原文仍保留，请先落实此决定：'
                + anchor['item_id'] + ' ' + anchor['field'], 409)
    return grouped


def derive_changed_witnesses(before, after):
    """Offer actual changed words for a displayed human-confirmed modification."""
    if not before or before.get('id') != after.get('id'):
        return []
    result = []
    from .requirement_details import detail_property_paths, detail_property_text
    fields = [*FIELDS, *detail_property_paths(after)] if after.get('kind') != 'acceptance' else ('statement',)
    for field in fields:
        text = field_text(after, field)
        previous = detail_property_text(before, field) if field.startswith('detail_contract.') else field_text(before, field)
        if text.strip() and text != previous:
            result.append(dict(item_id=after['id'], field=field, quote=text))
    return result


def implementation_records(project, question, grouped, *, method):
    from .understanding import application_hash, question_fingerprint
    items = {item['id']: item for item in project['items']}
    return {iid: dict(contract_version=CONTRACT_VERSION, answer_hash=digest(question.get('answer')),
                     question_hash=question_fingerprint(question), clause_hash=application_hash(items[iid]),
                     witnesses=copy.deepcopy(rows), method=method, actor='authenticated_local_user', reviewed_at=now(),
                     answer_source_refs=copy.deepcopy(question.get('answer_source_refs') or []))
            for iid, rows in grouped.items()}


def record_current(project, question, item, record):
    from .core import Problem
    from .understanding import application_hash, question_fingerprint
    if (not isinstance(record, dict) or record.get('contract_version') != CONTRACT_VERSION
            or record.get('answer_hash') != digest(question.get('answer'))
            or record.get('question_hash') != question_fingerprint(question)
            or record.get('clause_hash') != application_hash(item)):
        return False
    try:
        witnesses = [dict(item_id=item['id'], **row) for row in record.get('witnesses', [])]
        validate_implementations(project, question, witnesses, targets=[item])
    except (Problem, TypeError, KeyError):
        return False
    return True


def saved_implementations(project, question):
    from .understanding import answer_targets
    records = question.get('answer_applications') or {}
    return [dict(item_id=item['id'], **row) for item in answer_targets(project, question)
            if record_current(project, question, item, records.get(item['id'], {}))
            for row in records[item['id']]['witnesses']]
