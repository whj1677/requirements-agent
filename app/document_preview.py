"""Read-only PRD change preview compiled from the current project snapshot.

This module never calls a model and never changes project state. The baseline is
an already saved PRD artifact; the candidate is the current delivery scope.
"""
import copy
import difflib
import json

from .core import digest
from .product_flow import question_open
from .requirements import delivery_items, item_content

FIELD_LABELS = {
    'kind': '条目类型', 'title': '名称', 'statement': '需求原文',
    'applies_to': '适用范围', 'epistemic_status': '内容属性',
    'source_refs': '来源引用', 'related_refs': '关联条目',
    'change_type': '改动性质', 'behavior': '业务行为',
    'scope_evidence': '范围依据', 'classification_reason': '分类说明',
    'detail_contract': '详细定义', 'detail_required': '详细定义要求',
}
CHANGE_LABELS = {'added': '新增到本版', 'modified': '文字或定义有修改', 'removed': '移出本版'}
EXCLUDED_LABELS = {
    'excluded': '已明确排除', 'rejected': '已拒绝', 'deferred': '已暂缓',
    'candidate': '候选尚未采纳', 'selected': '未纳入本期交付',
}


def _text(value):
    if value is None:
        return ''
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def text_diff(before, after):
    """Character-level before/after segments; every changed character is kept."""
    before, after = _text(before), _text(after)
    segments = []
    for operation, a1, a2, b1, b2 in difflib.SequenceMatcher(
            None, before, after, autojunk=False).get_opcodes():
        if operation == 'equal':
            segments.append(dict(type='same', text=before[a1:a2]))
        else:
            if a1 != a2:
                segments.append(dict(type='removed', text=before[a1:a2]))
            if b1 != b2:
                segments.append(dict(type='added', text=after[b1:b2]))
    return [segment for segment in segments if segment['text']]


def _change_fields(before, after):
    before, after = before or {}, after or {}
    fields = []
    for key in dict.fromkeys([*item_content(before), *item_content(after)]):
        old, new = before.get(key), after.get(key)
        if old == new:
            continue
        fields.append(dict(field=key, label=FIELD_LABELS.get(key, key),
            before=_text(old), after=_text(new), segments=text_diff(old, new)))
    return fields


def _reason(item):
    status = item.get('selection_status', 'unknown')
    if item.get('applies_to') == 'as_is':
        return '现状或保持项，不作为本期新增目标'
    if status == 'selected':
        return '未纳入本期交付范围'
    if item.get('target_item_id'):
        return EXCLUDED_LABELS.get(status, '历史修订未采纳') + '；属于历史修订'
    return EXCLUDED_LABELS.get(status, '当前状态不进入本期交付')


def _pending_confirmations(p, current):
    rows = []
    seen = set()

    def add(key, kind, text, related_refs=()):
        if key in seen:
            return
        seen.add(key)
        rows.append(dict(id=key, kind=kind, text=text, related_refs=list(related_refs)))

    for question in p.get('questions', []):
        if question.get('superseded_by') or question.get('out_of_scope_reason') or question.get('prewrite_resolution'):
            continue
        if question_open(question):
            add(question['id'], 'question', question['question'], question.get('related_refs', []))
        elif question.get('understanding_status') in ('pending', 'candidate_ready') or question.get('decision_pending_group_ids'):
            add(question['id'], 'answer', question['question'] + '（回答关联修改仍待核对）', question.get('related_refs', []))

    # The connected workbench uses explicit row confirmations. Older projects
    # without that contract retain their existing checkpoint semantics.
    uses_row_confirmation = (p.get('requirement_confirmation_mode') == 'table'
        or any(item.get('requirement_confirmation') for item in p.get('items', [])))
    if uses_row_confirmation:
        from .requirement_confirmation import confirmation_current
        for item in current:
            if item['kind'] == 'requirement' and not confirmation_current(p, item):
                add('requirement:' + item['id'], 'requirement', item.get('title') or item['id'], [item['id']])

    from .prewrite import status as prewrite_status
    report = prewrite_status(p)
    if report.get('current'):
        for finding in report.get('findings', []):
            if finding.get('status') == 'resolved' or not finding.get('blocks_document', finding.get('severity') == 'blocker'):
                continue
            question_id = finding.get('question_id')
            if question_id and question_id in seen:
                continue
            add('prewrite:' + finding['id'], 'prewrite', finding.get('message') or finding.get('title') or '成文检查待确认', finding.get('related_refs', []))
    return rows


def _suggestions(p, report):
    rows = []
    for item in p.get('items', []):
        if (item.get('selection_status') == 'candidate'
                and item.get('epistemic_status') in ('proposed', 'inferred')
                and not item.get('superseded_by')):
            rows.append(dict(id=item['id'], kind='item', title=item.get('title') or item['id'],
                text=item.get('statement') or '', status='候选建议，尚未采纳'))
    if report.get('current'):
        for finding in report.get('findings', []):
            if finding.get('status') == 'resolved':
                continue
            text = finding.get('recommended_answer') or finding.get('suggested_resolution')
            if text:
                rows.append(dict(id=finding['id'], kind='prewrite',
                    title=finding.get('title') or '成文检查建议', text=text,
                    status='检查建议，尚未确认'))
    return rows


def build_prd_preview(p):
    """Return the exact prospective PRD scope delta for an action plan."""
    previous = p.get('documents', {}).get('prd')
    before_items = {item['id']: item for item in (previous or {}).get('item_snapshot', [])}
    current_items = {item['id']: item for item in p.get('items', [])}
    current = delivery_items(p)
    current_ids = {item['id'] for item in current}
    if previous:
        before_ids = set(previous.get('delivery_item_ids') or [
            item['id'] for item in before_items.values()
            if item.get('selection_status') == 'selected' and item.get('applies_to') == 'to_be'])
    else:
        before_ids = set()

    changes = []
    ordered_ids = [item['id'] for item in p.get('items', []) if item['id'] in current_ids | before_ids]
    ordered_ids += sorted((current_ids | before_ids) - set(ordered_ids))
    for item_id in ordered_ids:
        before, after = before_items.get(item_id), current_items.get(item_id)
        if item_id not in before_ids and item_id in current_ids:
            change_type = 'added'
        elif item_id in before_ids and item_id not in current_ids:
            change_type = 'removed'
        elif item_id in before_ids and item_id in current_ids and before and after and item_content(before) != item_content(after):
            change_type = 'modified'
        else:
            continue
        subject = after or before or {}
        changes.append(dict(item_id=item_id, title=subject.get('title') or item_id,
            item_kind=subject.get('kind'), change_type=change_type,
            change_label=CHANGE_LABELS[change_type], fields=_change_fields(before, after)))

    excluded = []
    for item in p.get('items', []):
        if item['id'] in current_ids or item.get('kind') not in ('requirement', 'rule', 'acceptance'):
            continue
        excluded.append(dict(id=item['id'], kind='item', title=item.get('title') or item['id'],
            text=item.get('statement') or '', reason=_reason(item)))
    for source in p.get('sources', []):
        if source.get('excluded'):
            excluded.append(dict(id=source['id'], kind='source', title=source.get('title') or source['id'],
                text='', reason='资料已排除，不作为本次模型输入或文档依据'))

    pending = _pending_confirmations(p, current)
    report = __import__('app.prewrite', fromlist=['status']).status(p)
    suggestions = _suggestions(p, report)
    counts = dict(added=sum(row['change_type'] == 'added' for row in changes),
        modified=sum(row['change_type'] == 'modified' for row in changes),
        removed=sum(row['change_type'] == 'removed' for row in changes),
        pending=len(pending), excluded=len(excluded), suggestions=len(suggestions))
    preview = dict(version='prd-change-preview-1', document_type='prd',
        input_revision=p['revision'], first_generation=not bool(previous),
        baseline=(dict(document_id=previous['id'],
            document_version=previous.get('document_version'),
            draft_revision=previous.get('draft_revision')) if previous else None),
        counts=counts, changes=changes, pending_confirmations=pending,
        excluded=excluded, ai_suggestions=suggestions,
        notice='预览按当前底稿和上一版已保存 PRD 快照计算；未调用模型。实际生成仍须通过当前门禁，模型成文结果生成后仍需人工评审。')
    preview['preview_hash'] = digest(preview)
    return copy.deepcopy(preview)
