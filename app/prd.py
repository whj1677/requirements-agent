"""Bounded document planning contract; canonical text and bookkeeping stay local."""
import copy
import jsonschema
from .core import digest, dumps, require
from .contracts import profile
from .document_mapping import build_reference_mapping
from .requirements import delivery_items, item_content
from .product_flow import INTAKE, SCOPE, status as flow_status

NORMATIVE = ('requirement', 'rule', 'acceptance')
REFS = dict(type='array', items=dict(type='string', minLength=1, maxLength=120), maxItems=100, uniqueItems=True)
SECTION_TITLES = {
    'background':'背景与现状', 'goal':'目标与价值', 'scope':'本期范围与边界',
    'users':'使用者与权限', 'function':'功能需求', 'rules':'业务规则',
    'acceptance':'验收条件', 'discussion':'待核对内容', 'limits':'资料限制与未决事项',
}
CONTEXT_LABELS = {**INTAKE, **SCOPE}
PLAN_SCHEMA = {
    '$schema':'https://json-schema.org/draft/2020-12/schema', 'type':'object',
    'required':['plan_version','sections'], 'additionalProperties':False,
    'properties':{
        'plan_version':{'const':'2'},
        'sections':dict(type='array',minItems=1,maxItems=12,items={
            'type':'object','additionalProperties':False,
            'required':['section_key','context_refs','normative_refs','discussion_refs'],
            'properties':{'section_key':{'enum':list(SECTION_TITLES)},
                'context_refs':{'type':'array','items':{'enum':list(CONTEXT_LABELS)},
                                'maxItems':len(CONTEXT_LABELS),'uniqueItems':True},
                'normative_refs':REFS,'discussion_refs':REFS}})}}


def verified_context(p):
    """Only a current human-checked product context can become document prose."""
    steps=flow_status(p)
    if len(steps)<2 or not steps[1]['complete']:
        return {}
    current=p.get('product_context',{})
    return {key:copy.deepcopy(current[key]) for key in CONTEXT_LABELS
            if isinstance(current.get(key),str) and current[key].strip()}


def allowed(item,p=None):
    return item['kind'] in NORMATIVE and item['selection_status']=='selected' and item['applies_to']=='to_be' and (p is None or any(i['id']==item['id'] for i in delivery_items(p)))


def represented_revision(item, items):
    target=items.get(item.get('target_item_id'))
    # Only a deferred revision already copied into its stable selected target is redundant.
    # A candidate/rejected revision may still represent a distinct human decision.
    return bool(item.get('target_item_id') and item.get('selection_status')=='deferred'
        and target and target['selection_status']=='selected' and item_content(item)==item_content(target))


def historical_notes(p):
    """Retain old limitations for audit, separate from current planning evidence."""
    return [dict(round=n+1,stage=m.get('stage','unknown'),created=m.get('created'),
        limitations=list(dict.fromkeys((m.get('response') or {}).get('limitations',[]))),
        notice='仅是当轮观察，可能过期。材料数量、参考模板、UI、采纳状态以当前任务头和快照为准；业务未知仍须核对，不能当作已解决。')
        for n,m in enumerate(p['messages']) if (m.get('response') or {}).get('limitations')]


def discussion_items(p):
    items={i['id']:i for i in p['items']}
    return [i for i in p['items'] if not represented_revision(i,items)]


def discussion_status(item):
    status=item['selection_status']
    if status=='rejected':return '已拒绝，保留审计'
    if status=='deferred':return '已暂缓，保留审计'
    if status=='candidate':return '候选，尚未采纳'
    return '已在草稿采纳，不代表业务负责人批准'


def plan_contract(p):
    example=dict(plan_version='2',sections=[dict(section_key='background',context_refs=[],
        normative_refs=[],discussion_refs=[])])
    jsonschema.Draft202012Validator(PLAN_SCHEMA).validate(example)
    return dict(version='prd-plan-contract-3',example=example,
        section_keys=list(SECTION_TITLES),context_ref_ids=list(verified_context(p)),
        normative_item_ids=[i['id'] for i in p['items'] if allowed(i,p)],
        discussion_item_ids=[i['id'] for i in discussion_items(p) if not allowed(i,p)],
        rules='根节点只有且必须包含 plan_version=2、sections。'
        '所有 sections 元素只有且必须包含 section_key、context_refs、normative_refs、discussion_refs。'
        'section_key 只从 section_keys 选择；章节标题由程序生成。不得输出 title、narration、limitations 或任何自由业务文案。'
        'context_refs 只能使用当前已核对 context_ref_ids；未核对的 product_context 不可引用。'
        'normative_refs 只能使用 normative_item_ids；白名单为空时必须全部为 []。'
        'discussion_refs 只能使用 discussion_item_ids，程序统一移入未采纳与历史讨论附录，不与业务正文混排。问题、方案、来源 ID 不得放入这两个字段。'
        '已有回答、未决问题、来源读取限制和未覆盖项由程序保留，不输出自由说明。'
        '示例仅说明结构，不是本项目答案或失败回退产物。')


def repair_instruction(error,p):
    common='重新输出完整 v2 章节引用对象，只修结构或引用；不重选方向、不补答、不改规则或采纳状态。业务原文由程序回填。'
    instructions={
        'SCHEMA_INVALID':'按实际错误位置修复 JSON 和字段。根节点仅 plan_version、sections；每节仅 section_key、context_refs、normative_refs、discussion_refs；不添加 title、narration、limitations。',
        'REFERENCE_INVALID':'按错误位置、具体 ID 和实际对象类型修复引用。context_refs 仅取已核对字段；问题由程序保留，不能放入条目引用；不能修改底稿。',
        'OUTPUT_EMPTY':'返回项目实际章节与有效引用，不用结构示例代替项目内容。',
        'OUTPUT_TRUNCATED':'实际输出截断：在既定预算内合并章节，仍保留有效引用；程序将补列遗漏的已选条款与已核对上下文。'}
    return common+'\n'+instructions.get(error.code,'按实际错误修复，不改变业务含义。')+'\n实际错误：'+error.message+'\n唯一章节建议契约：'+dumps(plan_contract(p))


def require_item(ref,at,p,ids):
    if ref in ids:return
    for collection,label in (('questions','问题'),('options','方案'),('sources','来源')):
        if any(x['id']==ref for x in p[collection]):
            require(False,'REFERENCE_INVALID',at+' '+ref+' 该ID存在，但属于'+label+'，不是条目；允许范围为任务头的条目清单')
    require(False,'REFERENCE_INVALID',at+' '+ref+' 该ID在本次允许范围内不存在；允许范围为任务头的条目清单')


def without_sketch(p):
    """New document input excludes retired sketches without mutating old snapshots."""
    return dict(p, ui=None, sketch_review={}, _sketch_retired=True)


def context(p, *, include_sketch=True):
    if not include_sketch:p=without_sketch(p)
    discussion=discussion_items(p)
    return dict(
        incremental_context=verified_context(p),
        sketch_check=copy.deepcopy(p.get('sketch_review',{})),
        A_normative={kind:[copy.deepcopy(i) for i in p['items'] if allowed(i,p) and i['kind']==kind] for kind in NORMATIVE},
        B_statements_answers=dict(items=[copy.deepcopy(i) for i in p['items'] if not allowed(i,p) and i['selection_status']=='selected' and i['epistemic_status'] not in ('proposed','inferred')],
            answers=[copy.deepcopy(q) for q in p['questions'] if q['status']=='answered'],
            notice='selected仅为当前草稿采纳，不代表业务负责人批准；原条目和后续回答可能存在差异，必须并列保留。'),
        C_discussion=dict(items=[copy.deepcopy(i) for i in discussion if not allowed(i,p) and (i['selection_status']!='selected' or i['epistemic_status'] in ('proposed','inferred'))],
            options=copy.deepcopy(p['options']), notice='讨论方向不等于采纳条目；建议和假设不成为规范。'),
        D_unknowns_limits=dict(questions=[copy.deepcopy(q) for q in p['questions'] if q['status']!='answered'],
            source_status=[{k:s.get(k) for k in ('id','title','purpose','parse_status','failure_reason','excluded')} for s in p['sources']],
            ui='本工作台已取消业务草图；仅交付需求文档，原页面截图仍是现状参考。' if p.get('_sketch_retired') else '尚无原型' if not p.get('ui') else '已有低保真模拟原型，不能证明业务实现',
            active_ui=None if not p.get('ui') else dict(version=p['ui']['spec']['draft_revision'],
                pages=[dict(title=page['title'],requirement_refs=page['requirement_refs'],
                    components=[dict(type=c['type'],label=c['label'],ref_ids=c['ref_ids']) for region in page['regions'] for c in region['components']]) for page in p['ui']['spec']['pages']],
                notice='这是当前活动原型的实际布局；可能已在原讨论方向上修改布局。不能用旧方案文字覆盖它，也不代表条目采纳或正式确认。'),
            current_material_count=len([s for s in p['sources'] if not s['excluded']]),
            reference_notice='当前章节参考由可信任务头 content_profile 指定；项目材料与章节参考不是同一对象。'))


def compile_plan(plan, p, kind, omitted=(), *, include_sketch=True):
    if not include_sketch:p=without_sketch(p)
    errors=list(jsonschema.Draft202012Validator(PLAN_SCHEMA).iter_errors(plan))
    details=[''.join('['+str(x)+']' if isinstance(x,int) else ('.' if j else '')+x for j,x in enumerate(e.absolute_path)) or '根节点' for e in errors]
    def error_message(e):
        if e.validator in ('maxItems','minItems','maxLength','minLength'):
            return f'{e.validator}={e.validator_value}，实际长度={len(e.instance)}'
        return e.message[:400]
    require(not errors,'SCHEMA_INVALID','成文章节建议错误：'+'；'.join(at+' '+error_message(e) for at,e in zip(details,errors)))
    items={i['id']:i for i in p['items']}
    contract=plan_contract(p)
    item_ref_ids=set(contract['normative_item_ids'])|set(contract['discussion_item_ids'])
    current_context=verified_context(p)
    sections=[]; coverage={}; discussed=set(); discussion_order=[]; placed={}; context_placed={}; discussion_locations={}
    def section(title,blocks):
        sid=kind.upper()+'-'+digest(dict(title=title,index=len(sections)))[:12]
        # The model chooses only a neutral section key and item order.
        # The shared reader projects referenced items/behavior into subheadings.
        sections.append(dict(section_id=sid,title=title,level=1,parent_section_id=None,blocks=blocks))
        return sid
    def text(value,refs=None,block_kind='narrative'):
        return dict(kind=block_kind,ref_ids=refs or [],text=value)
    def provenance(i):
        return '；来源：'+ ('、'.join(r['source_id']+'/'+r['excerpt_id'] for r in i.get('source_refs',[])) or '未提供')
    def discussion(i):
        status=discussion_status(i)
        if i['kind'] in NORMATIVE and i['selection_status']=='selected' and not allowed(i,p):status='已草稿采纳但不在本期规范范围'
        return text(f'【{status}；{i["epistemic_status"]}；{i["applies_to"]}】{i["id"]} — {i["statement"]}'+provenance(i),[i['id']])
    require(any(s['context_refs'] or s['normative_refs'] or s['discussion_refs'] for s in plan['sections']),
            'OUTPUT_EMPTY','章节建议没有任何当前有效引用；不能将结构示例当作项目文档')
    for n,s in enumerate(plan['sections']):
        title=SECTION_TITLES[s['section_key']]
        blocks=[]
        for j,key in enumerate(s['context_refs']):
            at=f'sections[{n}].context_refs[{j}]'
            require(key in contract['context_ref_ids'],'REFERENCE_INVALID',at+' '+key+' 不是当前已核对的非空产品上下文字段')
            if key not in context_placed:
                blocks.append(text(CONTEXT_LABELS[key]+'：'+current_context[key]))
                context_placed[key]=title
        for j,r in enumerate(s['normative_refs']):
            at=f'sections[{n}].normative_refs[{j}]'
            require_item(r,at,p,item_ref_ids)
            require(r in contract['normative_item_ids'],'SEMANTIC_BLOCKED',at+' '+r+' 不在本期已选规范白名单；需业务决定，不能自动采纳或改写为确定性叙述')
            if r in coverage:
                blocks.append(text('参见「'+placed[r]+'」中的 '+r+'，原文与依据不变。',[r]))
                continue
            blocks.append(dict(kind=items[r]['kind'],ref_ids=[r],text=None))
            blocks.append(text(f'{r}：{items[r]["epistemic_status"]}；草稿采纳不代表业务负责人批准'+provenance(items[r]),[r]))
            placed[r]=title
        for j,r in enumerate(s['discussion_refs']):
            if r in items and represented_revision(items[r],items):
                require(False,'SEMANTIC_BLOCKED',f'sections[{n}].discussion_refs[{j}] {r} 已由稳定条目 {items[r]["target_item_id"]} 完整代表；历史修订保留在项目审计，不能再作为待采纳讨论项')
            at=f'sections[{n}].discussion_refs[{j}]'
            require_item(r,at,p,item_ref_ids)
            require(r in contract['discussion_item_ids'],'REFERENCE_INVALID',at+' '+r+' 已在本期规范白名单，只能放入 normative_refs')
            if r not in discussed:discussion_order.append(r)
            discussed.add(r)
        if not blocks and s['discussion_refs']:
            # A model-only discussion section has no business-body content;
            # its validated refs are placed in the explicit appendix below.
            continue
        require(blocks,'OUTPUT_EMPTY',f'sections[{n}] 没有可呈现的新引用；不要生成空章节或只重复 context_refs')
        sid=section(title,blocks)
        for b in blocks:
            if b['kind'] in NORMATIVE:
                for r in b['ref_ids']:coverage.setdefault(r,[]).append(sid)
    # Preserve all verified context and current normative clauses even if the plan omits them.
    missing_context=[key for key in current_context if key not in context_placed]
    missing_normative=[i for i in p['items'] if allowed(i,p) and i['id'] not in coverage]
    if missing_context or missing_normative:
        blocks=[text('以下已核对事实与已选条款未由章节建议指定位置，程序按原文补列供核对。')]
        blocks += [text(CONTEXT_LABELS[key]+'：'+current_context[key]) for key in missing_context]
        for item in missing_normative:
            blocks.append(dict(kind=item['kind'],ref_ids=[item['id']],text=None))
            blocks.append(text(f'{item["id"]}：{item["epistemic_status"]}；草稿采纳不代表业务负责人批准'+provenance(item),[item['id']]))
        sid=section('补列的已核对事实与规范条款',blocks)
        for item in missing_normative:coverage[item['id']]=[sid]
    # Discussion is never interleaved with current normative body sections.
    # Keep model-selected order first, then retain every other unselected item.
    appendix_ids=discussion_order+[i['id'] for i in p['items'] if i['id'] not in coverage
                  and i['id'] not in discussed and not represented_revision(i,items)]
    if appendix_ids:
        sid=section('附录：未采纳与历史讨论（不作为本期要求）',[discussion(items[r]) for r in appendix_ids])
        discussion_locations={r:sid for r in appendix_ids}
    questions=[]
    for q in p['questions']:
        if q['status']=='answered':
            related='\n'.join(items[r]['statement'] for r in q.get('related_refs',[]) if r in items)
            answer_sources=[s['id']+'/'+e['id'] for s in p['sources'] for e in s['excerpts'] if e['text']==q['answer']]
            applied=any(r in items and items[r].get('selection_status')=='selected'
                        for r in q.get('applied_requirement_ids',[])) and q.get('understanding_status')=='applied'
            state='已应用于当前草稿条款' if applied else '已有回答，关联条款修订待核对采纳'
            questions.append(text(f'【{state}；不代表业务负责人批准】{q["question"]}\n回答：{q["answer"]}\n回答来源：'+('、'.join(answer_sources) or '未找到独立回答来源，见问题记录')+f'\n关联条目当前快照：{related or "无关联条目"}'+provenance(q),[q['id']]))
        else:
            state='已拆分，具体子问题见工作台' if q.get('superseded_by') else '本期范围外，仍未知' if q.get('out_of_scope_reason') else '尚未决定'
            questions.append(text(q['question']+'（'+state+'）'+provenance(q),[q['id']], 'open_question'))
    if questions:section('已有回答与未决问题',questions)
    prof=profile(kind,p.get('reference_mode')=='builtin')
    ui_notice=context(p)['D_unknowns_limits']['ui']
    material_limits=['材料「'+s['title']+'」尚有读取限制：'+(s.get('failure_reason') or '需核对未读取或不清晰的内容') for s in p['sources'] if not s['excluded'] and s['parse_status']!='read']
    if omitted:material_limits.append('本轮未完整送入的来源片段：'+'、'.join(omitted))
    planning_limits=(['章节建议未指定的已核对上下文字段：'+'、'.join(missing_context)] if missing_context else [])+(
        ['章节建议未指定的已选规范条目：'+'、'.join(i['id'] for i in missing_normative)] if missing_normative else [])
    pending=section('文档边界与资料限制',[text(ui_notice)]+[
        text(x,block_kind='open_question') for x in dict.fromkeys(material_limits+planning_limits)])
    historical=historical_notes(p)
    if historical:
        section('历史分析提示（非当前事实）',[
            text('以下保留历史来源，可能已被后续材料更新；不作为本版材料数量、模板或业务确认状态。')]+[
            text(f'第{h["round"]}轮 · {h["stage"]} · {h["created"] or "时间未记录"}\n'+'\n'.join(h['limitations'])) for h in historical])
    maps=build_reference_mapping(p,kind,sections,coverage,verified_context=current_context,
                                 pending_section_id=pending,discussion_locations=discussion_locations)
    document=dict(document_type=kind,content_profile_id=prof['id'],content_profile_version=prof['version'],title=p['name'],
        sections=sections,coverage=[dict(item_id=r,section_ids=list(dict.fromkeys(ids))) for r,ids in coverage.items()],reference_mapping=maps)
    return dict(schema_version='1.1',stage='prd',summary='已组装需求讨论稿；请核对规范原文、回答与未知。',proposals=[],questions=[],findings=[],used_source_refs=[],limitations=material_limits+planning_limits,result=document)
