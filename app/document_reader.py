"""Human document projection. Canonical artifacts and audit evidence stay intact."""
import copy
import re

from .core import brief_hash
from .requirements import delivery_items

PRESENTATION_VERSION = 'document-reading-8'
_EMPTY_BEHAVIOR = {'未提及异常分支'}

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
_SOURCE_PURPOSE_LABELS = {
    'current': '现状资料',
    'reference': '参考资料',
    'goal': '本期诉求资料',
    'template': '模板资料',
}


def _source_limit_text(source):
    """Describe only the source's declared purpose and recorded read state."""
    title = source.get('title') or '未命名文件'
    purpose = _SOURCE_PURPOSE_LABELS.get(source.get('purpose'), '用途未标注资料')
    status = source.get('parse_status')
    limits = {
        'partial': '部分内容未能完整读取',
        'failed': '当前无法读取',
        'office_required': '需要在本机 Office 中读取',
        'permission_denied': '因权限限制未能读取',
        'awaiting_vision': '图片内容尚未完成读取',
    }
    state = limits.get(status, '读取状态尚待核对')
    return f'《{title}》（{purpose}）：{state}；未读取内容不作为本版产品结论的依据。'


def reading_structure(content, items, source_snapshot=(), *, show_requirement_behavior=True):
    """Project explicit section/ref/behavior structure; never infer headings from prose.

    This is a versioned read view, not a migration of the signed content object.
    Model-planned parallel topics remain parallel. Only stored parent links and
    an item's placement in a block establish containment.
    """
    outline, numbers, child_counts = [], {}, {}
    structured = content['document_type']=='prd' and any(
        b['kind']=='detail' for s in content['sections'] for b in s['blocks'])
    ref_labels = {}
    if structured:
        counters = {}
        for section in content['sections']:
            for block in section['blocks']:
                for ref in block.get('ref_ids', []):
                    item = items.get(ref, {})
                    prefix = {'requirement':'R', 'rule':'BR', 'acceptance':'A'}.get(item.get('kind'))
                    if prefix and ref not in ref_labels:
                        counters[prefix] = counters.get(prefix, 0)+1
                        ref_labels[ref] = prefix+f'{counters[prefix]:02d}'
        content['reference_labels'] = [dict(label=label, item_id=ref,
            content_version=items[ref].get('content_version',1)) for ref,label in ref_labels.items()]

    def clause_table(refs):
        return dict(kind='table', title='验收清单', text='验收清单',
                    columns=['编号','验收要求'],
                    rows=[[ref_labels.get(ref,ref),items[ref]['title']+'\n'+items[ref]['statement']] for ref in refs],
                    item_ids=refs, table_role='acceptance', column_weights=[.10,.90])
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
        acceptance_refs=[]
        def flush_acceptance():
            if acceptance_refs:
                nodes.append(clause_table(acceptance_refs.copy()))
                acceptance_refs.clear()
        for index, block in enumerate(section['blocks']):
            if structured and block['kind']=='acceptance' and all(r in items for r in block['ref_ids']):
                acceptance_refs.extend(block['ref_ids'])
                continue
            flush_acceptance()
            if structured and block['kind']=='open_question' and block.get('question_id'):
                nodes.append(dict(kind='table', title='待确认事项', text='待确认事项',
                    columns=['事项','影响及状态'], rows=[[block['question_text'],block['question_context']]],
                    question_ids=[block['question_id']],table_role='questions',column_weights=[.40,.60]))
                continue
            if block['kind']=='detail':
                from .detail_rendering import detail_nodes, detail_hash
                ref=block['ref_ids'][0]; item=items.get(ref)
                node_id=f'{sid}-detail-{index}'
                # A one-feature chapter already identifies its topic. Repeating
                # the same feature title above every table adds no information.
                if len(section['blocks'])>1:
                    nodes.append(heading(node_id,item.get('title',ref) if item else ref,level+1,sid,item_id=ref))
                if not item or not item.get('detail_contract') or block.get('detail_hash')!=detail_hash(item):
                    nodes.append(dict(kind='detail_error',text='详细定义快照缺失或版本不一致，请重新生成文档。',item_id=ref))
                    continue
                try:
                    nodes.extend(detail_nodes(item,block['detail_group'],ref_labels=ref_labels))
                except (ValueError,KeyError,TypeError) as error:
                    nodes.append(dict(kind='detail_error',text='详细定义暂时无法渲染：'+str(error),item_id=ref))
                continue
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
                nodes.append(dict(kind='metadata', text=f'{ref_labels.get(ref,ref)} · v{item.get("content_version",1)}', item_id=ref))
                nodes.append(dict(kind='paragraph', text=item['statement'], block_kind=item['kind']))
                if item['kind'] == 'requirement' and show_requirement_behavior and not item.get('detail_contract'):
                    from .product_flow import BEHAVIOR
                    for key, label in BEHAVIOR.items():
                        value = item.get('behavior', {}).get(key)
                        if value and value.strip().rstrip('。') not in _EMPTY_BEHAVIOR:
                            nodes.append(heading(node_id+'-'+key, label, level+2, node_id))
                            nodes.append(dict(kind='paragraph', text=value, block_kind='behavior'))
        flush_acceptance()
        compact=[]
        for node in nodes:
            if (node.get('table_role')=='questions' and compact
                    and compact[-1].get('table_role')=='questions'):
                compact[-1]['rows'].extend(node['rows'])
                compact[-1]['question_ids'].extend(node['question_ids'])
            else:
                compact.append(node)
        section['reading_nodes'] = compact
    content['outline'] = outline
    content['presentation_version'] = PRESENTATION_VERSION
    return content


def document_name(artifact):
    return artifact.get('requirement_name') or artifact['content']['title']


def filename(artifact):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', document_name(artifact)).strip(' .')[:100]
    return f'{name or "未命名需求"}_{artifact["content"]["document_type"].upper()}_v{artifact.get("document_version",artifact["draft_revision"])}_{artifact["id"]}'


def _answer_pending(project, question, delivered):
    from .understanding import answer_targets, applied_targets
    targets = {item['id'] for item in answer_targets(project, question)} & delivered
    return bool(targets and (question.get('understanding_status') != 'applied'
                             or targets - applied_targets(project, question)))


def _prewrite_pending_section(artifact, items, section_ids, *, include_locations=False):
    """Show unresolved checks from this artifact, never the project's latest run."""
    snapshot = artifact.get('prewrite_snapshot') or {}
    blocks = []
    for index, finding in enumerate(snapshot.get('findings', [])):
        if finding.get('status') == 'resolved' or finding.get('severity') not in ('blocker', 'warning'):
            continue
        label = '阻塞项' if finding['severity'] == 'blocker' else '提醒项'
        text = f'待核对问题（{label}）：' + (finding.get('message') or finding.get('title') or '')
        if finding.get('suggested_resolution'):
            text += '\n建议（待确认）：' + finding['suggested_resolution']
        refs = finding.get('related_refs', [])
        if refs:
            text += '\n关联对象：' + '、'.join(
                ref + ('（' + items[ref]['title'] + '）' if items.get(ref, {}).get('title') else '')
                for ref in refs)
        block = dict(kind='narrative', ref_ids=[], text=text)
        if include_locations:
            block['source_location'] = f"{artifact['id']}.prewrite_snapshot.findings[{index}]"
        blocks.append(block)
    if not blocks:
        return None
    section_id = 'PRODUCT-PREWRITE-PENDING'
    while section_id in section_ids:
        section_id += '-CHECK'
    return dict(section_id=section_id, title='成文检查待核对事项', level=1,
                parent_section_id=None, blocks=blocks)


def reader_document(artifact, *, include_locations=False):
    """Return product content only; retain canonical audit evidence in the project."""
    content = copy.deepcopy(artifact['content'])
    if include_locations:
        for section in content.get('sections',[]):
            for index,block in enumerate(section.get('blocks',[])):
                block['source_location']=f"{artifact['id']}.{section['section_id']}.blocks[{index}]"
    v4_mrd = (artifact.get('assembly_version') == 'prd-plan-4'
              and content['document_type'] == 'mrd')
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
    pending_promotion = {}
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
                promoted=dict(kind='narrative', ref_ids=[], text=line)
                if include_locations:promoted['source_location']=block['source_location']
                business.setdefault(kind, []).append(promoted)
            for kind, product_blocks in business.items():
                product_title = _PRODUCT_SECTION_TITLES[kind]
                product_section = next((candidate for candidate in original_sections
                                        if candidate['title'] == product_title
                                        and candidate['level'] == 1
                                        and candidate['parent_section_id'] is None
                                        and candidate['title'] not in _INTERNAL_SECTIONS), None)
                if product_section is not None:
                    # Only the exact root product section is a valid destination.
                    # Keep the canonical appendix and its identity/source audit intact.
                    retained_section = next((candidate for candidate in sections
                                             if candidate['section_id'] == product_section['section_id']), None)
                    if retained_section is None:
                        pending_promotion.setdefault(product_section['section_id'], []).extend(product_blocks)
                    else:
                        retained_section['blocks'].extend(product_blocks)
                else:
                    product_section = dict(
                        section_id=f'PRODUCT-{content["document_type"].upper()}-BUSINESS-{kind.upper()}',
                        title=product_title, level=1,
                        parent_section_id=None, blocks=product_blocks)
                    original_sections.append(product_section)
                    sections.append(product_section)
            continue
        if section['title'] in ('已有回答与未决问题','当前决定与未决事项','当前决定',
                                 '已有回答与待核对修订'):
            blocks=[]
            seen_questions=set()
            for block in section['blocks']:
                q=questions.get(block['ref_ids'][0]) if len(block.get('ref_ids',[]))==1 else None
                if not q or q['id'] in seen_questions or q.get('superseded_by') or q.get('prewrite_resolution'):
                    continue
                answered = q.get('status') == 'answered'
                if answered and (q.get('out_of_scope_reason') or not _answer_pending(
                        dict(items=list(items.values()), questions=list(questions.values())), q, delivered or set(items))):
                    continue
                seen_questions.add(q['id'])
                state=('已回答，正文及关联验收仍待落实' if answered else
                       '本期范围外，仍未决定' if q.get('out_of_scope_reason') else '本期未决')
                text='待决事项：'+q['question']+'\n影响：'+(q.get('why') or '尚未明确')+'\n状态：'+state
                if q.get('out_of_scope_reason'):
                    text += '\n范围理由：' + q['out_of_scope_reason']
                visible=dict(kind='open_question',ref_ids=[],text=text,question_id=q['id'],
                             question_text=q['question'], question_context=text.split('\n',1)[1])
                if include_locations:visible['source_location']=block['source_location']
                blocks.append(visible)
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
                if item and item['kind']=='requirement' and not v4_mrd and not item.get('detail_contract'):
                    from .product_flow import BEHAVIOR
                    text += ''.join('\n'+label+'：'+item['behavior'][key] for key,label in BEHAVIOR.items()
                                    if item.get('behavior',{}).get(key)
                                    and item['behavior'][key].strip().rstrip('。') not in _EMPTY_BEHAVIOR)
            elif block['kind']=='detail':
                # Keep the signed snapshot reference for typed read projection.
                # Semantic reviewers use block.text and source_location, so the
                # plain view must include every definition, not just its title.
                from .detail_rendering import detail_plaintext, detail_hash
                if item and item.get('detail_contract') and block.get('detail_hash')==detail_hash(item):
                    try:
                        text=detail_plaintext(item,block['detail_group'])
                    except (ValueError,KeyError,TypeError):
                        text='详细定义暂时无法渲染，请核对结构及流程后重新生成。'
                else:
                    text='详细定义快照缺失或版本不一致，请重新生成文档。'
            elif block['kind']=='explanation':
                # The compiler validated these exact snapshot references. The
                # reader shows product prose; claim IDs remain in the artifact.
                refs=[]
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
                    text=_source_limit_text(source)
                    refs=[]
                else:
                    # Reference mapping and model planning notes are retained in the project.
                    continue
            if not any(b['text']==text and b['ref_ids']==refs for b in blocks):
                visible=dict(block, text=text, ref_ids=refs)
                if block['kind']=='explanation':
                    visible.pop('evidence_refs',None)
                blocks.append(visible)
        pending_blocks = pending_promotion.pop(section['section_id'], [])
        if blocks or pending_blocks or (not section['blocks'] and section['section_id'] in picture_sections):
            title='文档边界与资料限制' if section['title']=='限制及参考维度待核对' else section['title']
            blocks.extend(pending_blocks)
            sections.append(dict(section, title=title, blocks=blocks))
    # A matching product heading may have been empty and omitted by the normal
    # pass; append its promotion once, using the original section identity.
    for section in original_sections:
        pending_blocks = pending_promotion.pop(section['section_id'], [])
        if pending_blocks:
            sections.append(dict(section, blocks=pending_blocks))
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
    # The compiler appends typed definitions after its default acceptance
    # chapter. In the read view, finish those definitions before acceptance.
    # Only root compiler chapters without children qualify; custom hierarchy
    # and canonical source locations are never rewritten.
    detail_sections=[s for s in sections if any(b['kind']=='detail' for b in s['blocks'])]
    if content['document_type']=='prd' and artifact.get('assembly_version')=='prd-plan-4' and detail_sections:
        parents={s.get('parent_section_id') for s in sections}
        acceptance=next((s for s in sections if s['title']=='验收条件' and s['level']==1
            and s['parent_section_id'] is None and s['section_id'] not in parents
            and all(b['kind'] in ('acceptance','explanation') for b in s['blocks'])),None)
        if acceptance and all(s['level']==1 and s['parent_section_id'] is None for s in detail_sections):
            sections.remove(acceptance)
            sections.insert(max(sections.index(s) for s in detail_sections)+1,acceptance)
    if content['document_type'] == 'prd':
        pending = _prewrite_pending_section(artifact, items,
            {s['section_id'] for s in sections}, include_locations=include_locations)
        if pending:
            sections.append(pending)
    # Mapping explanations and raw coverage are audit/provenance structures, not
    # part of a product document. Rebuild coverage from clauses actually shown.
    content.pop('reference_mapping', None)
    coverage = {}
    for section in sections:
        for block in section['blocks']:
            if block['kind'] not in ('requirement','rule','acceptance'):
                continue
            for ref in block.get('ref_ids', []):
                if ref in items and items[ref].get('kind') in ('requirement', 'rule', 'acceptance'):
                    coverage.setdefault(ref, []).append(section['section_id'])
    content['coverage'] = [
        dict(item_id=ref, section_ids=list(dict.fromkeys(ids)))
        for ref, ids in coverage.items()
    ]
    return reading_structure(content, items, show_requirement_behavior=not v4_mrd)


def export_readiness(p, kind, mode='formal'):
    artifact = p['documents'].get(kind)
    issues = []
    if not artifact:
        issues.append(dict(code='NOT_FOUND', message='请先生成文档。'))
    elif artifact['brief_hash'] != brief_hash(p) or kind in p.get('stale_document_kinds',[]):
        issues.append(dict(code='STALE_REVISION', message='需求或页面已更新，请重新生成文档后下载。'))
    if artifact:
        from .prd import cloud_prd_gaps
        for message in cloud_prd_gaps(artifact['content'],p):
            issues.append(dict(code='DOCUMENT_CONTENT_REQUIRED',message=message))
    if kind=='prd':
        from .prewrite import formal_issues
        issues.extend(formal_issues(p))
        from .requirement_details import detail_issues
        from .detail_rendering import detail_hash, GROUP_TITLES
        for item in delivery_items(p):
            if item['kind']!='requirement':
                continue
            for issue in detail_issues(item,p,require_confirmed=True):
                issues.append(dict(issue,code='DETAIL_INVALID' if issue['code'] in ('DETAIL_INVALID','DETAIL_STRUCTURE') else 'DETAIL_REQUIRED'))
            if not item.get('detail_contract'):
                continue
            if artifact:
                groups={b['detail_group'] for s in artifact['content']['sections'] for b in s['blocks']
                        if b['kind']=='detail' and b['ref_ids']==[item['id']] and b.get('detail_hash')==detail_hash(item)}
                if groups!=set(GROUP_TITLES):
                    issues.append(dict(code='DETAIL_VERSION_INVALID',message=item['id']+' 的详细定义未完整绑定当前版本，请重新生成文档。'))
        if artifact:
            view=reader_document(artifact)
            for node in (n for section in view['sections'] for n in section['reading_nodes']):
                if node['kind']=='detail_error':
                    issues.append(dict(code='DETAIL_RENDER_FAILED',message=node['text']))
    from .semantic_quality import current_counting_checks, current_document_checks
    semantic=current_counting_checks(p)+[check for check in current_document_checks(p)
                                       if check['document_type']==kind]
    for check in semantic:
        issues.append(dict(code='SEMANTIC_BLOCKED',check_id=check['check_id'],location=check['location'],
            message='当前内容存在可复现语义风险，请先核对并修订：'+check['check_id']))
    problems = []
    for q in p['questions']:
        if q.get('superseded_by') or q.get('prewrite_resolution'):
            continue
        state = None
        if q['status'] != 'answered' and not q.get('out_of_scope_reason'):
            state = 'unanswered'
        elif q['status']=='answered' and not q.get('out_of_scope_reason') and _answer_pending(
                p, q, {i['id'] for i in delivery_items(p)}):
            state = 'answer_pending'
        if state:
            message = q['question'] if state == 'unanswered' else q['question'] + '：回答已保存，尚未完整纳入关联条款，请核对采纳。'
            issues.append(dict(code='CLARIFICATION_REQUIRED', question_id=q['id'], message=message))
            problems.append(dict(question_id=q['id'], title=q['question'], state=state,
                                 action='answer' if state == 'unanswered' else 'apply_answer',
                                 message=message, related_refs=q.get('related_refs', [])))
    warnings=[]
    if mode=='review':
        reviewable={'DOCUMENT_CONTENT_REQUIRED','DETAIL_REQUIRED','SEMANTIC_BLOCKED','CLARIFICATION_REQUIRED','PREWRITE_REQUIRED'}
        warnings=[issue for issue in issues if issue['code'] in reviewable]
        issues=[issue for issue in issues if issue['code'] not in reviewable]
    return dict(ready=not issues, issues=issues, problems=problems, warnings=warnings, mode=mode)
