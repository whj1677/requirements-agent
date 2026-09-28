"""Human document projection. Canonical artifacts and audit evidence stay intact."""
import copy
import re

from .core import brief_hash
from .requirements import delivery_items

PRESENTATION_VERSION = 'document-reading-3'

_INTERNAL_SECTIONS = {
    '历史分析提示（非当前事实）',
    '附录：条目身份与历史讨论',
    '附录：未采纳与历史讨论（不作为本期要求）',
}
_PROCESS_PREFIXES = (
    '以下已核对事实与已选条款未由章节建议指定位置',
    '本轮未完整送入的来源片段：',
    '章节建议未指定的已核对上下文字段：',
    '章节建议未指定的已选规范条目：',
)
_PRODUCT_BUSINESS_KINDS = {'goal', 'actor', 'constraint', 'non_goal'}
_PRODUCT_SECTION_TITLES = {
    'goal': '目标与价值', 'actor': '使用者',
    'constraint': '产品约束', 'non_goal': '本期范围边界',
}


def reading_structure(content, items, source_snapshot=()):
    """Project explicit section/ref/behavior structure; never infer headings from prose.

    This is a versioned read view, not a migration of the signed content object.
    Model-planned parallel topics remain parallel. Only stored parent links and
    an item's placement in a block establish containment.
    """
    outline, numbers, child_counts = [], {}, {}
    def heading(node_id, title, level, parent, **extra):
        child_counts[parent] = child_counts.get(parent, 0) + 1
        number = (numbers[parent] + '.' if parent in numbers else '') + str(child_counts[parent])
        numbers[node_id] = number
        node = dict(kind='heading', id=node_id, text=title, level=level,
                    parent_id=parent, number=number, **extra)
        outline.append(node)
        return node

    for section in content['sections']:
        sid, level = section['section_id'], section['level']
        nodes = [heading(sid, section['title'], level, section['parent_section_id'])]
        for index, block in enumerate(section['blocks']):
            if block['kind'] not in ('requirement', 'rule', 'acceptance'):
                nodes.append(dict(kind='paragraph', text=block.get('text') or '', block_kind=block['kind']))
                continue
            for position, ref in enumerate(block['ref_ids']):
                item = items.get(ref)
                if not item:
                    nodes.append(dict(kind='paragraph', text=ref+'：历史条款原文未保存，无法还原。'))
                    continue
                node_id = f'{sid}-item-{index}-{position}'
                nodes.append(heading(node_id, item['title'], level+1, sid, item_id=ref, item_kind=item['kind']))
                nodes.append(dict(kind='metadata', text=f'{ref} · v{item.get("content_version",1)}', item_id=ref))
                nodes.append(dict(kind='paragraph', text=item['statement'], block_kind=item['kind']))
                if item['kind'] == 'requirement':
                    from .product_flow import BEHAVIOR
                    for key, label in BEHAVIOR.items():
                        value = item.get('behavior', {}).get(key)
                        if value:
                            nodes.append(heading(node_id+'-'+key, label, level+2, node_id))
                            nodes.append(dict(kind='paragraph', text=value, block_kind='behavior'))
        section['reading_nodes'] = nodes
    content['outline'] = outline
    content['presentation_version'] = PRESENTATION_VERSION
    return content


def document_name(artifact):
    return artifact.get('requirement_name') or artifact['content']['title']


def filename(artifact):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', document_name(artifact)).strip(' .')[:100]
    return f'{name or "未命名需求"}_{artifact["content"]["document_type"].upper()}_v{artifact.get("document_version",artifact["draft_revision"])}_{artifact["id"]}'


def reader_document(artifact):
    """Return product content only; retain canonical audit evidence in the project."""
    content = copy.deepcopy(artifact['content'])
    original_sections = list(content.get('sections', []))
    name = document_name(artifact)
    content['title'] = name + ('｜产品需求文档（PRD）' if content['document_type']=='prd' else '｜市场需求文档（MRD）')
    items = {i['id']:i for i in artifact.get('item_snapshot', [])}
    questions = {q['id']:q for q in artifact.get('question_snapshot', [])}
    delivered = set(artifact.get('delivery_item_ids', []))
    sections = []
    source_snapshots=artifact.get('source_snapshot',[])
    source_by_title={s.get('title'):s for s in source_snapshots}
    source_by_id={s.get('id'):s for s in source_snapshots}
    # A layout-only section may carry its product content as a mapped picture.
    # Keep it only for an explicitly selected in-scope requirement, never for a
    # discussion-only/candidate page that filtering has removed.
    picture_dimension = 'PRD-4.F.1' if content['document_type']=='prd' else 'MRD-5.1.F.3'
    picture_sections = set()
    for mapping in content.get('reference_mapping', []):
        item = items.get(mapping.get('scope_ref'), {})
        if (mapping.get('profile_section_id') == picture_dimension
                and mapping.get('disposition') in ('included', 'merged')
                and item.get('kind') == 'requirement'
                and item.get('selection_status') == 'selected'
                and item.get('applies_to') == 'to_be'
                and ('delivery_item_ids' not in artifact or item.get('id') in delivered)):
            picture_sections.update(mapping.get('output_section_ids', []))
    for section in content['sections']:
        if section['title'] in _INTERNAL_SECTIONS:
            # compile_plan places any selected but unplanned items in its
            # audit appendix. Promote only explicit, reported, in-scope
            # business statements to neutral product sections.
            business = {}
            for block in section.get('blocks', []):
                refs = block.get('ref_ids', [])
                item = items.get(refs[0]) if len(refs) == 1 else None
                if not item:
                    continue
                kind = item.get('kind')
                if (kind not in _PRODUCT_BUSINESS_KINDS
                        or item.get('id') not in delivered
                        or item.get('selection_status') != 'selected'
                        or item.get('epistemic_status') != 'reported'
                        or item.get('applies_to') != 'to_be'):
                    continue
                line = item.get('title', '') + '\n' + item.get('statement', '')
                business.setdefault(kind, []).append(dict(kind='narrative', ref_ids=[], text=line))
            for kind, product_blocks in business.items():
                product_section = dict(
                    section_id=f'PRODUCT-{content["document_type"].upper()}-BUSINESS-{kind.upper()}',
                    title=_PRODUCT_SECTION_TITLES[kind], level=1,
                    parent_section_id=None, blocks=product_blocks)
                sections.append(product_section)
                original_sections.append(product_section)
            continue
        if section['title'] in ('已有回答与未决问题','当前决定与未决事项','当前决定',
                                 '已有回答与待核对修订'):
            blocks=[]
            seen_questions=set()
            for block in section['blocks']:
                q=questions.get(block['ref_ids'][0]) if len(block.get('ref_ids',[]))==1 else None
                if not q or q['id'] in seen_questions or q.get('status')=='answered' or q.get('superseded_by'):
                    continue
                seen_questions.add(q['id'])
                state='本期范围外，仍未决定' if q.get('out_of_scope_reason') else '本期未决'
                text='待决事项：'+q['question']+'\n影响：'+(q.get('why') or '尚未明确')+'\n状态：'+state
                blocks.append(dict(kind='open_question',ref_ids=[],text=text))
            if blocks:
                sections.append(dict(section, title='未决产品事项', blocks=blocks))
            continue
        blocks = []
        for block in section['blocks']:
            text = block.get('text') or ''
            refs = block.get('ref_ids',[])
            item = items.get(refs[0]) if len(refs)==1 else None
            question = questions.get(refs[0]) if len(refs)==1 else None
            if block['kind'] in ('requirement','rule','acceptance'):
                text = '\n'.join(f'{r}｜{items[r]["title"]} · v{items[r].get("content_version",1)}\n{items[r]["statement"]}' if r in items else r+'：历史条款原文未保存，无法还原。' for r in refs)
                if item and item['kind']=='requirement':
                    from .product_flow import BEHAVIOR
                    text += ''.join('\n'+label+'：'+item['behavior'][key] for key,label in BEHAVIOR.items() if item.get('behavior',{}).get(key))
            elif item:
                # Selected, reported business statements can be product prose.
                # Keep their wording while excluding proposal/hypothesis status
                # and internal identity/provenance from the reader projection.
                if (item.get('kind') not in _PRODUCT_BUSINESS_KINDS
                        or item.get('id') not in delivered
                        or item.get('selection_status') != 'selected'
                        or item.get('epistemic_status') != 'reported'
                        or item.get('applies_to') != 'to_be'):
                    continue
                text = item.get('title', '') + '\n' + item.get('statement', '')
                refs = []
            elif question:
                # Answers and workflow states remain in the project audit trail.
                continue
            if any(text.startswith(prefix) for prefix in _PROCESS_PREFIXES):
                continue
            if text.startswith('【模型讨论说明'):
                continue
            if section['title'] in ('限制及参考维度待核对','文档边界与资料限制'):
                if (text.startswith('材料「') and '尚有读取限制：' in text) or text.startswith('材料限制：'):
                    if text.startswith('材料「'):
                        title=text.split('材料「',1)[1].split('」',1)[0]
                        source=source_by_title.get(title,{})
                    else:
                        source_id=text.split('材料限制：',1)[1].split()[0]
                        source=source_by_id.get(source_id,{})
                    status=source.get('parse_status')
                    limits={
                        'partial':'部分材料内容未能完整读取，相关内容仍待核对。',
                        'failed':'有材料当前无法读取，相关内容尚未纳入本版。',
                        'office_required':'有材料需要在本机 Office 中读取，相关内容尚未纳入本版。',
                        'permission_denied':'有材料因权限限制未能读取，相关内容尚未纳入本版。',
                        'awaiting_vision':'有图片材料尚未完成读取，相关内容仍待核对。',
                    }
                    text=limits.get(status,'部分材料内容尚待核对，相关结论未作为已确认事实。')
                    refs=[]
                else:
                    # Reference mapping and model planning notes are retained in the project.
                    continue
            if not any(b['text']==text and b['ref_ids']==refs for b in blocks):
                blocks.append(dict(block, text=text, ref_ids=refs))
        if blocks or (not section['blocks'] and section['section_id'] in picture_sections):
            title='文档边界与资料限制' if section['title']=='限制及参考维度待核对' else section['title']
            sections.append(dict(section, title=title, blocks=blocks))
    # Preserve empty ancestors for retained sections so parent links remain valid.
    section_by_id = {s['section_id']: s for s in original_sections}
    retained = {s['section_id']: s for s in sections}
    needed = set(retained)
    for section in list(retained.values()):
        parent = section.get('parent_section_id')
        while parent and parent in section_by_id and parent not in needed:
            needed.add(parent)
            parent = section_by_id[parent].get('parent_section_id')
    sections = []
    for section in original_sections:
        if section['section_id'] not in needed:
            continue
        if section['section_id'] in retained:
            sections.append(retained[section['section_id']])
            continue
        title = section['title']
        if title in _INTERNAL_SECTIONS:
            title = '产品内容'
        elif title in ('已有回答与未决问题','当前决定与未决事项','当前决定',
                       '已有回答与待核对修订'):
            title = '未决产品事项'
        sections.append(dict(section, title=title, blocks=[]))
    content['sections'] = sections
    # Mapping explanations and raw coverage are audit/provenance structures, not
    # part of a product document. Rebuild coverage from clauses actually shown.
    content.pop('reference_mapping', None)
    coverage = {}
    for section in sections:
        for block in section['blocks']:
            for ref in block.get('ref_ids', []):
                if ref in items and items[ref].get('kind') in ('requirement', 'rule', 'acceptance'):
                    coverage.setdefault(ref, []).append(section['section_id'])
    content['coverage'] = [
        dict(item_id=ref, section_ids=list(dict.fromkeys(ids)))
        for ref, ids in coverage.items()
    ]
    return reading_structure(content, items)


def export_readiness(p, kind):
    artifact = p['documents'].get(kind)
    issues = []
    if not artifact:
        issues.append(dict(code='NOT_FOUND', message='请先生成文档。'))
    elif artifact['brief_hash'] != brief_hash(p) or kind in p.get('stale_document_kinds',[]):
        issues.append(dict(code='STALE_REVISION', message='需求或页面已更新，请重新生成文档后下载。'))
    for q in p['questions']:
        if q['status'] != 'answered' and not q.get('out_of_scope_reason'):
            issues.append(dict(code='CLARIFICATION_REQUIRED', question_id=q['id'], message=q['question']))
        elif q['status']=='answered' and q.get('understanding_status')!='applied':
            from .understanding import answer_targets
            targets={i['id'] for i in answer_targets(p,q)}
            selected={i['id'] for i in delivery_items(p)}
            if targets & selected:
                issues.append(dict(code='CLARIFICATION_REQUIRED',question_id=q['id'],
                                   message='已保存回答尚未完整纳入关联产品条款，请先核对采纳。'))
        elif q['status']=='answered':
            from .understanding import answer_targets
            targets={i['id'] for i in answer_targets(p,q)}
            selected={i['id'] for i in delivery_items(p)}
            applied=set(q.get('applied_item_ids',q.get('applied_requirement_ids',[])))
            if (targets & selected)-applied:
                issues.append(dict(code='CLARIFICATION_REQUIRED',question_id=q['id'],
                                   message='已保存回答尚未完整纳入关联产品条款，请先核对采纳。'))
    return dict(ready=not issues, issues=issues)
