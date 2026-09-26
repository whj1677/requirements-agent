"""Human document projection. Canonical artifacts and audit evidence stay intact."""
import copy
import re

from .core import brief_hash

PRESENTATION_VERSION = 'document-reading-2'


def reading_structure(content, items, source_snapshot=()):
    """Project explicit section/ref/behavior structure; never infer headings from prose.

    This is a versioned read view, not a migration of the signed content object.
    Model-planned parallel topics remain parallel. Only stored parent links and
    an item's placement in a block establish containment.
    """
    outline, numbers, child_counts = [], {}, {}
    source_names = {s['id']:s.get('title') for s in source_snapshot}

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
                refs = item.get('source_refs', [])
                sources = '、'.join(dict.fromkeys(source_names.get(r['source_id']) or '历史材料名称未保存（见条目来源记录）' for r in refs)) or '未提供'
                identity = {'reported':'材料陈述','inferred':'推断，待核对','proposed':'建议，非已定事实'}.get(item.get('epistemic_status'), item.get('epistemic_status','未记录'))
                selection = '草稿已采纳，不代表业务负责人批准' if item.get('selection_status')=='selected' else '尚未采纳'
                nodes.append(dict(kind='metadata', text=f'{identity}；{selection}；来源：{sources}', source_refs=copy.deepcopy(refs)))
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
    """Only remove recognized compiler scaffolding, never arbitrary business prose."""
    content = copy.deepcopy(artifact['content'])
    name = document_name(artifact)
    content['title'] = name + ('｜产品需求文档（PRD）' if content['document_type']=='prd' else '｜市场需求文档（MRD）')
    items = {i['id']:i for i in artifact.get('item_snapshot', [])}
    questions = {q['id']:q for q in artifact.get('question_snapshot', [])}
    covered={c['item_id'] for c in content.get('coverage',[])}
    delivered=set(artifact.get('delivery_item_ids',covered))
    stable=[i for i in items.values() if i['id'] in covered and i['id'] in delivered
            and i.get('selection_status')=='selected' and i.get('applies_to')=='to_be'
            and i.get('kind') in ('requirement','rule','acceptance')]
    business_fields=('kind','statement','behavior','applies_to','epistemic_status')
    def same_stable(item):
        matches=[candidate for candidate in stable if candidate['id']!=item['id']
                 and all(candidate.get(key)==item.get(key) for key in business_fields)]
        exact=next((candidate for candidate in matches
                    if candidate.get('change_type')==item.get('change_type')),None)
        if exact:return exact,False
        return (matches[0],True) if item['kind']=='acceptance' and matches else (None,False)
    sections = []
    appendix_blocks=[]
    appendix_section=None
    appendix_title='附录：未采纳与历史讨论（不作为本期要求）'
    for section in content['sections']:
        if section['title'] == '历史分析提示（非当前事实）':
            continue
        blocks = []
        for block in section['blocks']:
            text = block.get('text') or ''
            generated_discussion=False
            refs = block['ref_ids']
            item = items.get(refs[0]) if len(refs)==1 else None
            question = questions.get(refs[0]) if len(refs)==1 else None
            if block['kind'] in ('requirement','rule','acceptance'):
                text = '\n'.join(f'{r}｜{items[r]["title"]} · v{items[r].get("content_version",1)}\n{items[r]["statement"]}' if r in items else r+'：历史条款原文未保存，无法还原。' for r in refs)
                if item and item['kind']=='requirement':
                    from .product_flow import BEHAVIOR
                    text += ''.join('\n'+label+'：'+item['behavior'][key] for key,label in BEHAVIOR.items() if item.get('behavior',{}).get(key))
            elif item:
                from .prd import discussion_status
                provenance = '；来源：'+('、'.join(r['source_id']+'/'+r['excerpt_id'] for r in item.get('source_refs',[])) or '未提供')
                status = discussion_status(item)
                outside=item['kind'] in ('requirement','rule','acceptance') and item['selection_status']=='selected' and 'delivery_item_ids' in artifact and item['id'] not in artifact['delivery_item_ids']
                if outside:status='已草稿采纳但不在本期规范范围'
                generated = f'【{status}；{item["epistemic_status"]}；{item["applies_to"]}】{item["id"]} — {item["statement"]}'+provenance
                audit = f'{item["id"]}：{item["epistemic_status"]}；草稿采纳不代表业务负责人批准'+provenance
                if text == audit:
                    continue
                legacy_generated = text.startswith('【未采纳；') and text.endswith(item['statement']+provenance)
                if text == generated or legacy_generated:
                    generated_discussion=True
                    if item['selection_status']=='rejected':label='已拒绝：'
                    elif item['selection_status']=='deferred':label='已暂缓：'
                    elif item['selection_status']=='candidate':label='独立条目尚未采纳（不改变已核对的产品上下文）：' if item['epistemic_status']=='reported' else '候选，尚未采纳：'
                    else:label=''
                    if outside:label='不在本期规范范围：'
                    if item['epistemic_status']=='inferred':
                        label += '待核实的推断：'
                    elif item['epistemic_status']=='proposed':
                        label += '方案建议：'
                    match,different_classification=same_stable(item) if item['selection_status'] in ('rejected','deferred') else (None,False)
                    identity=''
                    if match:
                        decision='已拒绝' if item['selection_status']=='rejected' else '已暂缓'
                        if different_classification:
                            old=item.get('change_type') or 'unspecified'
                            current=match.get('change_type') or 'unspecified'
                            identity=(f'验收预期原文与已采纳 {match["id"]} 相同；本独立草稿{decision}，'
                                      f'不撤销 {match["id"]} 的验收要求；两条分类记录不同（{old}/{current}）。')
                        else:
                            identity=f'本草稿{decision}；规范原文与 {match["id"]} 相同，以该已采纳条目为准。'
                    text = item['id']+'｜'+label+item['statement']+('（'+identity+'）' if identity else '')
                elif text.startswith('参见「') and text.endswith(('原文与依据不变。','身份及原文不变。')):
                    generated_discussion=text.endswith('身份及原文不变。')
                    text = item['id']+'｜'+status+'；'+text.split('」中的 ')[0]+'」。'
            elif question and section['title']=='已有回答与未决问题':
                text = question['question']
                if question['status']=='answered':
                    applied=[r for r in question.get('applied_requirement_ids',[])
                             if r in items and items[r].get('selection_status')=='selected']
                    if question.get('understanding_status')=='applied' and applied:
                        label='已应用于当前草稿条款的回答：'
                    elif question.get('understanding_status') in ('pending','candidate_ready'):
                        label='已有回答，关联条款修订待核对采纳：'
                    else:
                        label='已有回答，关联条款更新状态未核实：'
                    text = label+question['answer']
                    related = [items[r]['statement'] for r in question.get('related_refs',[]) if r in items]
                    if related:
                        text += '\n关联条目当前快照：'+'\n'.join(related)
                else:
                    if question.get('superseded_by'):
                        text += '（已拆分，具体子问题见工作台）'
                    elif question.get('out_of_scope_reason'):
                        text += '（本期以外，仍未知：'+question['out_of_scope_reason']+'）'
                    else:
                        text += '（待澄清）'
            prefix = '【模型讨论说明，未核实；不构成规范或批准】'
            if text.startswith(prefix):
                text = text[len(prefix):]
            if not refs:
                # Replace only known internal identifiers in model prose, not canonical statements.
                for index, (qid, q) in enumerate(questions.items(), 1):
                    text = re.sub(r'(?<![\w-])'+re.escape(qid)+r'(?![\w-])', f'澄清记录 {index}', text)
                for source in artifact.get('source_snapshot',[]):
                    # Only the artifact's own source snapshot supplies display names.
                    # Canonical business clauses and the stored model output remain unchanged.
                    pattern=r'(?<![\w-])'+re.escape(source['id'])+r'(?![\w-])'
                    label='材料「'+(source.get('title') or '未命名材料')+'」'
                    text=re.sub(pattern+r'\s+(partial|failed|read|pending)\b',lambda m:label+'（'+{'partial':'部分读取','failed':'读取失败','read':'已读取','pending':'待读取'}[m[1]]+'）',text)
                    text=re.sub(pattern,lambda _:label,text)
            if section['title']=='限制及参考维度待核对':
                if text == '讨论稿，不构成正式确认；章节语义覆盖待核对。':
                    continue
                if text.startswith('未成文规范条目：'):
                    iid = text.split('：',1)[1]
                    text = '尚未编入正文：'+items.get(iid,{}).get('statement','存在尚未编入正文的需求。')
            destination=appendix_blocks if generated_discussion else blocks
            if not any(b['text']==text and b['ref_ids']==refs for b in destination):
                destination.append(dict(block, text=text))
        if section['title']==appendix_title and not blocks:
            appendix_section=section
            continue
        if blocks or any(s.get('parent_section_id')==section['section_id'] for s in content['sections']):
            active_questions=[q for q in questions.values() if not q.get('superseded_by')]
            if any(q['status']!='answered' for q in active_questions):question_title='当前决定与未决事项'
            elif active_questions and all(q.get('understanding_status')=='applied' for q in active_questions):question_title='当前决定'
            else:question_title='已有回答与待核对修订'
            titles = {'材料陈述、未成文条目与讨论建议':'补充背景与讨论建议',
                      '已有回答与未决问题':question_title, '限制及参考维度待核对':'文档边界与补充说明'}
            sections.append(dict(section, title=titles.get(section['title'],section['title']), blocks=blocks))
    if appendix_blocks:
        base=appendix_section or dict(section_id=content['document_type'].upper()+'-READER-DISCUSSION-APPENDIX',
                                      level=1,parent_section_id=None)
        sections.append(dict(base,title=appendix_title,blocks=appendix_blocks))
    content['sections'] = sections
    return reading_structure(content, items, artifact.get('source_snapshot', []))


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
    return dict(ready=not issues, issues=issues)
