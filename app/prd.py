"""Bounded document planning contract; canonical text and bookkeeping stay local."""
import copy
import jsonschema
from .core import digest, dumps, require
from .contracts import profile
from .requirements import delivery_items, item_content

NORMATIVE = ('requirement', 'rule', 'acceptance')
REFS = dict(type='array', items=dict(type='string', minLength=1, maxLength=120), maxItems=100, uniqueItems=True)
PLAN_SCHEMA = {
    '$schema':'https://json-schema.org/draft/2020-12/schema', 'type':'object',
    'required':['plan_version','title','sections','limitations'], 'additionalProperties':False,
    'properties':{
        'plan_version':{'const':'1'}, 'title':dict(type='string',minLength=1,maxLength=200),
        'limitations':dict(type='array',items=dict(type='string',maxLength=1000),maxItems=20),
        'sections':dict(type='array',minItems=1,maxItems=12,items={
            'type':'object','additionalProperties':False,
            'required':['title','normative_refs','discussion_refs','narration'],
            'properties':{'title':dict(type='string',minLength=1,maxLength=200),
                'normative_refs':REFS,'discussion_refs':REFS,
                'narration':dict(type='string',maxLength=1200)}})}}


def allowed(item,p=None):
    return item['kind'] in NORMATIVE and item['selection_status']=='selected' and item['applies_to']=='to_be' and (p is None or any(i['id']==item['id'] for i in delivery_items(p)))


def represented_revision(item, items, placed):
    target=items.get(item.get('target_item_id'))
    # Content deduplication only: no inference that a historical candidate was approved.
    return bool(target and target['id'] in placed and target['selection_status']=='selected'
        and item_content(item)==item_content(target))


def plan_contract(p):
    example=dict(plan_version='1',title='需求讨论稿',sections=[dict(title='背景与待定范围',
        normative_refs=[],discussion_refs=[],narration='')],limitations=[])
    jsonschema.Draft202012Validator(PLAN_SCHEMA).validate(example)
    return dict(version='prd-plan-contract-2',example=example,
        normative_item_ids=[i['id'] for i in p['items'] if allowed(i,p)],
        discussion_item_ids=[i['id'] for i in p['items']],
        rules='根节点只有且必须包含 plan_version、title、sections、limitations。'
        '所有 sections 元素只有且必须包含 title、normative_refs、discussion_refs、narration。'
        'narration 是章节级必填字符串，无内容为 ""，不得省略、放到根节点或使用 null。'
        'normative_refs 只能使用 normative_item_ids；白名单为空时必须全部为 []。'
        'discussion_refs 只能使用 discussion_item_ids。问题、方案、来源 ID 不得放入这两个字段。'
        '已有回答与未决问题由程序单独保留，不需要重复引用；不引用问题 ID 不等于删除问题。'
        '示例仅说明结构，不是本项目答案或失败回退产物。')


def repair_instruction(error,p):
    common='重新输出完整章节建议对象，保留未出错的业务含义；不重选方向、不补答、不改规则或采纳状态，不把候选写成规范或确定性叙述。'
    instructions={
        'SCHEMA_INVALID':'按实际错误位置修复结构或 JSON。根节点不得添加字段；所有 sections 元素必须保留 narration，无需说明时填空字符串，不使用 null。不得静默删除内容后宣称成功。',
        'REFERENCE_INVALID':'按错误位置、具体 ID 和实际对象类型修复引用。问题由程序保留，不能放入条目引用；不能修改底稿或问题记录。',
        'OUTPUT_EMPTY':'返回符合契约的完整对象，不能使用结构示例代替项目内容。',
        'OUTPUT_TRUNCATED':'实际输出截断：在既定预算内压缩叙述或合并章节，仍保留全部必填字段、业务边界和未决事项；不提高 max_tokens，不增加请求次数。'}
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
    return dict(
        incremental_context=copy.deepcopy(p.get('product_context',{})),
        sketch_check=copy.deepcopy(p.get('sketch_review',{})),
        A_normative={kind:[copy.deepcopy(i) for i in p['items'] if allowed(i,p) and i['kind']==kind] for kind in NORMATIVE},
        B_statements_answers=dict(items=[copy.deepcopy(i) for i in p['items'] if not allowed(i,p) and i['selection_status']=='selected' and i['epistemic_status'] not in ('proposed','inferred')],
            answers=[copy.deepcopy(q) for q in p['questions'] if q['status']=='answered'],
            notice='selected仅为当前草稿采纳，不代表业务负责人批准；原条目和后续回答可能存在差异，必须并列保留。'),
        C_discussion=dict(items=[copy.deepcopy(i) for i in p['items'] if not allowed(i,p) and (i['selection_status']!='selected' or i['epistemic_status'] in ('proposed','inferred'))],
            options=copy.deepcopy(p['options']), notice='讨论方向不等于采纳条目；建议和假设不成为规范。'),
        D_unknowns_limits=dict(questions=[copy.deepcopy(q) for q in p['questions'] if q['status']!='answered'],
            source_status=[{k:s.get(k) for k in ('id','title','purpose','parse_status','failure_reason','excluded')} for s in p['sources']],
            ui='本工作台已取消业务草图；仅交付需求文档，原页面截图仍是现状参考。' if p.get('_sketch_retired') else '尚无原型' if not p.get('ui') else '已有低保真模拟原型，不能证明业务实现',
            active_ui=None if not p.get('ui') else dict(version=p['ui']['spec']['draft_revision'],
                pages=[dict(title=page['title'],requirement_refs=page['requirement_refs'],
                    components=[dict(type=c['type'],label=c['label'],ref_ids=c['ref_ids']) for region in page['regions'] for c in region['components']]) for page in p['ui']['spec']['pages']],
                notice='这是当前活动原型的实际布局；可能已在原讨论方向上修改布局。不能用旧方案文字覆盖它，也不代表条目采纳或正式确认。'),
            current_material_count=len([s for s in p['sources'] if not s['excluded']]),
            reference_notice='当前章节参考由可信任务头 content_profile 指定；项目材料与章节参考不是同一对象。'),
        historical_notes=[dict(round=n+1,stage=m.get('stage','unknown'),created=m.get('created'),
            limitations=list(dict.fromkeys((m.get('response') or {}).get('limitations',[]))),
            notice='仅是当轮观察，可能过期。材料数量、参考模板、UI、采纳状态以当前任务头和快照为准；业务未知仍须核对，不能当作已解决。')
            for n,m in enumerate(p['messages']) if (m.get('response') or {}).get('limitations')])


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
    sections=[]; coverage={}; discussed=set(); placed={}; discussion_locations={}
    def section(title,blocks):
        sid=kind.upper()+'-'+digest(dict(title=title,index=len(sections)))[:12]
        # The plan contract supplies parallel topics, not parent relationships.
        # The shared reader projects referenced items/behavior into subheadings;
        # do not guess hierarchy from the model's title text or rewrite snapshots.
        sections.append(dict(section_id=sid,title=title,level=1,parent_section_id=None,blocks=blocks))
        return sid
    def text(value,refs=None,block_kind='narrative'):
        return dict(kind=block_kind,ref_ids=refs or [],text=value)
    def provenance(i):
        return '；来源：'+ ('、'.join(r['source_id']+'/'+r['excerpt_id'] for r in i.get('source_refs',[])) or '未提供')
    def discussion(i):
        status='未采纳' if i['selection_status']!='selected' else '已在草稿采纳，不代表业务负责人批准'
        if i['kind'] in NORMATIVE and i['selection_status']=='selected' and not allowed(i,p):status='已草稿采纳但不在本期规范范围'
        return text(f'【{status}；{i["epistemic_status"]}；{i["applies_to"]}】{i["id"]} — {i["statement"]}'+provenance(i),[i['id']])
    for n,s in enumerate(plan['sections']):
        blocks=[]
        for j,r in enumerate(s['normative_refs']):
            at=f'sections[{n}].normative_refs[{j}]'
            require_item(r,at,p,contract['discussion_item_ids'])
            require(r in contract['normative_item_ids'],'SEMANTIC_BLOCKED',at+' '+r+' 不在本期已选规范白名单；需业务决定，不能自动采纳或改写为确定性叙述')
            if r in coverage:
                blocks.append(text('参见「'+placed[r]+'」中的 '+r+'，原文与依据不变。',[r]))
                continue
            blocks.append(dict(kind=items[r]['kind'],ref_ids=[r],text=None))
            blocks.append(text(f'{r}：{items[r]["epistemic_status"]}；草稿采纳不代表业务负责人批准'+provenance(items[r]),[r]))
            placed[r]=s['title']
        for j,r in enumerate(s['discussion_refs']):
            require_item(r,f'sections[{n}].discussion_refs[{j}]',p,contract['discussion_item_ids'])
            if r in placed:
                blocks.append(text('参见「'+placed[r]+'」中的 '+r+'，身份及原文不变。',[r]))
            else:
                blocks.append(discussion(items[r]));placed[r]=s['title']
            discussed.add(r)
        if s['narration']:
            blocks.append(text('【模型讨论说明，未核实；不构成规范或批准】'+s['narration']))
        sid=section(s['title'],blocks)
        for r in s['discussion_refs']:discussion_locations.setdefault(r,sid)
        for b in blocks:
            if b['kind'] in NORMATIVE:
                for r in b['ref_ids']:coverage.setdefault(r,[]).append(sid)
    # Preserve omitted context and all questions independently of the model's choices.
    remaining=[discussion(i) for i in p['items'] if i['id'] not in coverage and i['id'] not in discussed
               and not represented_revision(i,items,set(coverage)|discussed)]
    if remaining:section('材料陈述、未成文条目与讨论建议',remaining)
    questions=[]
    for q in p['questions']:
        if q['status']=='answered':
            original='；'.join(items[r]['statement'] for r in q.get('related_refs',[]) if r in items)
            answer_sources=[s['id']+'/'+e['id'] for s in p['sources'] for e in s['excerpts'] if e['text']==q['answer']]
            questions.append(text(f'【已有回答；真实性及与旧条目的一致性待核对，不代表业务负责人批准】{q["question"]}\n回答：{q["answer"]}\n回答来源：'+('、'.join(answer_sources) or '未找到独立回答来源，见问题记录')+f'\n关联旧条目原文：{original or "无关联条目"}'+provenance(q),[q['id']]))
        else:questions.append(text(q['question']+'（尚未决定）'+provenance(q),[q['id']], 'open_question'))
    if questions:section('已有回答与未决问题',questions)
    prof=profile(kind,p.get('reference_mode')=='builtin')
    limits=['讨论稿，不构成正式确认；章节语义覆盖待核对。',context(p)['D_unknowns_limits']['ui']]
    limits+=plan['limitations']
    limits+=['材料「'+s['title']+'」尚有读取限制：'+(s.get('failure_reason') or '需核对未读取或不清晰的内容') for s in p['sources'] if not s['excluded'] and s['parse_status']!='read']
    if omitted:limits.append('本轮未完整送入的来源片段：'+'、'.join(omitted))
    limits+=['未成文规范条目：'+i['id'] for i in p['items'] if allowed(i,p) and i['id'] not in coverage]
    pending=section('限制及参考维度待核对',[text(x,block_kind='open_question') for x in dict.fromkeys(limits)])
    historical=context(p)['historical_notes']
    if historical:
        section('历史分析提示（非当前事实）',[
            text('以下保留历史来源，可能已被后续材料更新；不作为本版材料数量、模板或业务确认状态。')]+[
            text(f'第{h["round"]}轮 · {h["stage"]} · {h["created"] or "时间未记录"}\n'+'\n'.join(h['limitations'])) for h in historical])
    maps=[]
    reqs=[i['id'] for i in p['items'] if allowed(i,p) and i['kind']=='requirement']
    picture='PRD-4.F.1' if kind=='prd' else 'MRD-5.1.F.3'
    for d in prof['sections']:
        if not d['mapping_required']:continue
        for scope in (reqs or [None]) if d['repeat_per_function'] else [None]:
            if p.get('_sketch_retired') and d['id']==picture:
                maps.append(dict(profile_section_id=d['id'],scope_ref=scope,disposition='not_applicable',
                    output_section_ids=[],reason='工作台已取消业务草图交付；现状截图仅作参考，不冒充生成的业务页面。'))
                continue
            visible=bool(d['id']==picture and scope in coverage and p.get('ui') and any(scope in page['requirement_refs'] for page in p['ui']['spec']['pages']))
            maps.append(dict(profile_section_id=d['id'],scope_ref=scope,disposition='merged' if visible else 'pending',
                output_section_ids=coverage[scope] if visible else [pending],reason='实际原型关联到已成文功能；低保真模拟。' if visible else '该内容维度的语义覆盖尚待核对；不以空章节视为覆盖。'))
    if p.get('ui') and any(d['id']==picture for d in prof['sections']):
        for r,sid in discussion_locations.items():
            if items[r]['kind']=='requirement' and r not in coverage and any(r in page['requirement_refs'] for page in p['ui']['spec']['pages']):
                maps.append(dict(profile_section_id=picture,scope_ref=r,disposition='merged',output_section_ids=[sid],
                    reason='原型插图对应实际讨论条目原文；不表示已采纳、规范覆盖或正式确认。'))
    document=dict(document_type=kind,content_profile_id=prof['id'],content_profile_version=prof['version'],title=plan['title'],
        sections=sections,coverage=[dict(item_id=r,section_ids=list(dict.fromkeys(ids))) for r,ids in coverage.items()],reference_mapping=maps)
    incomplete=['未成文规范条目：'+i['id'] for i in p['items'] if allowed(i,p) and i['id'] not in coverage]
    return dict(schema_version='1.1',stage='prd',summary='已组装需求讨论稿；请核对规范原文、回答与未知。',proposals=[],questions=[],findings=[],used_source_refs=[],limitations=plan['limitations']+incomplete,result=document)
