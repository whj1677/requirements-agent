"""Evidence-bound mapping from reference dimensions to generated document sections.

The mapping records where existing content is expressed. It does not approve a
business rule, infer missing behavior, or turn a discussion item into a norm.
"""

from .contracts import profile
from .product_flow import BEHAVIOR, INTAKE, SCOPE
from .requirements import delivery_items


CONTEXT_LABELS = {**INTAKE, **SCOPE}
PICTURE = {'prd': 'PRD-4.F.1', 'mrd': 'MRD-5.1.F.3'}


def build_reference_mapping(p, kind, sections, coverage, *, verified_context,
                            pending_section_id, discussion_locations=None):
    """Map only material actually projected into ``sections``.

    ``verified_context`` is the same human-checked context used by the document
    compiler. ``pending_section_id`` names its real limitations section, which
    is required by the reference contract even for pending dispositions.
    ``coverage`` maps normative item IDs to their generated section IDs.
    """
    by_id = {section['section_id']: section for section in sections}
    if pending_section_id not in by_id:
        raise ValueError('pending_section_id must name a generated section')
    items = {item['id']: item for item in p['items']}
    functions = [item['id'] for item in delivery_items(p)
                 if item['kind'] == 'requirement']
    discussion_locations = discussion_locations or {}

    # A context value counts only when its exact, verified rendering is present.
    context_at = {}
    for key, value in verified_context.items():
        if key not in CONTEXT_LABELS or not isinstance(value, str) or not value.strip():
            continue
        rendered = CONTEXT_LABELS[key] + '：' + value
        context_at[key] = [sid for sid, section in by_id.items()
                           if any(block['kind'] == 'narrative' and block.get('text') == rendered
                                  for block in section['blocks'])]

    def normative_at(item_id):
        item = items.get(item_id)
        if not item or item.get('selection_status') != 'selected' or item.get('applies_to') != 'to_be':
            return []
        return [sid for sid in coverage.get(item_id, ()) if sid in by_id
                and any(block['kind'] == item['kind'] and item_id in block['ref_ids']
                        for block in by_id[sid]['blocks'])]

    def context(*keys):
        if not all(context_at.get(key) for key in keys):
            return []
        return list(dict.fromkeys(sid for key in keys for sid in context_at[key]))

    def behavior(item_id, *keys):
        item = items.get(item_id, {})
        values = item.get('behavior') or {}
        if not all(key in BEHAVIOR and isinstance(values.get(key), str)
                   and values[key].strip() for key in keys):
            return []
        return normative_at(item_id)

    def normed_functions(*keys):
        return list(dict.fromkeys(sid for item_id in functions
                              for sid in (behavior(item_id, *keys) if keys else normative_at(item_id))))

    mappings = []
    for dimension in profile(kind, p.get('reference_mode') == 'builtin')['sections']:
        if not dimension['mapping_required']:
            continue
        dimension_id = dimension['id']
        for scope in (functions or [None]) if dimension['repeat_per_function'] else [None]:
            if dimension_id == PICTURE[kind] and p.get('_sketch_retired'):
                mappings.append(dict(profile_section_id=dimension_id, scope_ref=scope,
                    disposition='not_applicable', output_section_ids=[],
                    reason='工作台已取消业务草图交付；现状截图仅作参考，不冒充生成的业务页面。'))
                continue

            destinations = []
            evidence = ''
            if kind == 'prd':
                if dimension_id == 'PRD-2.1':
                    destinations = context('product', 'current_state')
                    evidence = '已核对的产品与现状'
                elif dimension_id == 'PRD-2.2':
                    destinations = context('users', 'current_state')
                    evidence = '已核对的使用者与当前使用场景'
                elif dimension_id == 'PRD-2.3':
                    destinations = normed_functions('actor', 'permissions')
                    evidence = '本期功能的操作者与权限约束'
                elif dimension_id == 'PRD-3.3':
                    destinations = normed_functions('actor', 'flow')
                    evidence = '本期功能的操作者与流程步骤；不冒充图形流程图'
                elif dimension_id == 'PRD-4.F.2' and scope:
                    destinations = behavior(scope, 'result')
                    evidence = '该功能的规范条目与操作结果'
                elif dimension_id == 'PRD-4.F.3' and scope:
                    destinations = behavior(scope, 'flow', 'exceptions')
                    evidence = '该功能的主要流程与异常路径；以步骤表达'
                elif dimension_id == 'PRD-4.F.5' and scope:
                    destinations = behavior(scope, 'flow', 'result')
                    evidence = '该功能的规范条目、处理流程与结果'
                elif dimension_id == 'PRD-4.F.7' and scope:
                    destinations = behavior(scope, 'data')
                    evidence = '该功能的业务数据描述'
            else:
                if dimension_id == 'MRD-1':
                    destinations = context('product', 'current_state')
                    evidence = '已核对的产品与现状背景'
                elif dimension_id == 'MRD-4.1':
                    destinations = context('change_scope') + normed_functions()
                    destinations = list(dict.fromkeys(destinations)) if context('change_scope') and normed_functions() else []
                    evidence = '已核对的本期改动范围与成文功能条目'
                elif dimension_id == 'MRD-5.1.F.4' and scope:
                    destinations = behavior(scope, 'flow', 'result')
                    evidence = '该功能的规范条目、流程与操作结果'

            if dimension_id == PICTURE[kind] and scope:
                pages = ((p.get('ui') or {}).get('spec') or {}).get('pages', [])
                if any(scope in page.get('requirement_refs', ()) for page in pages):
                    destinations = normative_at(scope) or ([discussion_locations[scope]]
                        if scope in discussion_locations and discussion_locations[scope] in by_id else [])
                    if destinations:
                        evidence = ('实际低保真原型关联该功能的正文位置；仅说明原型关联，'
                                    '不表示已采纳、规范覆盖或正式确认。')

            if destinations:
                mappings.append(dict(profile_section_id=dimension_id, scope_ref=scope,
                    disposition='merged', output_section_ids=list(dict.fromkeys(destinations)),
                    reason=evidence + '合并表达于所指章节；定位不等于业务已确认。'))
            else:
                mappings.append(dict(profile_section_id=dimension_id, scope_ref=scope,
                    disposition='pending', output_section_ids=[pending_section_id],
                    reason='尚未在本期已核对上下文或成文规范条目中找到足以表达「'
                           + dimension['reference_heading'] + '」的内容；保留待核对，不以章节位置代替语义覆盖。'))

    # A linked picture for a discussion-only item must retain its unadopted ID.
    picture_id = PICTURE[kind]
    if not p.get('_sketch_retired') and p.get('ui'):
        pages = p['ui']['spec']['pages']
        for item_id, sid in discussion_locations.items():
            item = items.get(item_id)
            if (item and item['kind'] == 'requirement' and item_id not in coverage
                    and sid in by_id and any(item_id in page.get('requirement_refs', ()) for page in pages)):
                mappings.append(dict(profile_section_id=picture_id, scope_ref=item_id,
                    disposition='merged', output_section_ids=[sid],
                    reason='原型插图对应实际讨论条目原文；不表示已采纳、规范覆盖或正式确认。'))
    return mappings
