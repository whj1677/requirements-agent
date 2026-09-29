"""Bounded document planning contract; canonical text and bookkeeping stay local."""
import copy
import re
import jsonschema
from .core import digest, dumps, require
from .contracts import profile
from .document_mapping import build_reference_mapping
from .requirements import delivery_items, item_content
from .product_flow import INTAKE, SCOPE, status as flow_status

NORMATIVE = ('requirement', 'rule', 'acceptance')
DISCUSSION_APPENDIX_TITLE = '附录：条目身份与历史讨论'
MRD_NORMATIVE_APPENDIX_TITLE = '附录：对应产品要求'
REFS = dict(type='array', items=dict(type='string', minLength=1, maxLength=120), maxItems=100, uniqueItems=True)
SECTION_TITLES = {
    'background':'背景与现状', 'goal':'目标与价值', 'scope':'本期范围与边界',
    'users':'使用者与权限', 'function':'功能需求', 'rules':'业务规则',
    'acceptance':'验收条件', 'discussion':'待核对内容', 'limits':'资料限制与未决事项',
}
CONTEXT_LABELS = {**INTAKE, **SCOPE}
CONTEXT_SECTION_V4 = {
    'product':'background', 'module':'background', 'current_state':'background',
    'intent':'goal', 'value':'goal', 'priority':'goal',
    'users':'users',
    'change_scope':'scope', 'preserve_scope':'scope', 'out_of_scope':'scope',
}
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

EXPLANATION_REFS = {
    'type':'object', 'additionalProperties':False,
    'required':['context_refs','normative_refs','business_claim_refs'],
    'properties':{'context_refs':REFS, 'normative_refs':REFS,
                  'business_claim_refs':REFS},
}
PLAN_SCHEMA_V3 = copy.deepcopy(PLAN_SCHEMA)
PLAN_SCHEMA_V3['properties']['plan_version'] = {'const':'3'}
PLAN_SCHEMA_V3['properties']['sections']['items']['required'].append('explanations')
PLAN_SCHEMA_V3['properties']['sections']['items']['properties']['explanations'] = {
    'type':'array','maxItems':12,'items':{
        'type':'object','additionalProperties':False,
        'required':['text','evidence_refs'],
        'properties':{'text':{'type':'string','minLength':1,'maxLength':1200},
                      'evidence_refs':EXPLANATION_REFS}}}
PLAN_SCHEMA_V4 = copy.deepcopy(PLAN_SCHEMA_V3)
PLAN_SCHEMA_V4['properties']['plan_version'] = {'const':'4'}
PLAN_SCHEMA_V4['properties']['sections']['items']['properties']['explanations']['items']['properties']['text']['description'] = (
    '直接交付读者的成稿正文，不是写作计划、作者指令或组装过程说明；无来源信息增量时 explanations 使用空数组。')
WRITING_INSTRUCTION = re.compile(
    r'(?:^|[。！？\n])\s*(?:本期)?(?:PRD|MRD|文档正文|规则层|验收层|功能层)\s*(?:需要|应当?|不应|必须|须)'
    r'|(?:值班人员|用户|操作者)需要在文档中')


def verified_business_context(p):
    """Only supported, unconflicted selected excerpts are factual prose evidence."""
    from .business_context import selected_excerpts
    verified={}
    for source in p.get('sources', []):
        if (not source.get('business_context') or source.get('parse_status')!='read' or
                source.get('excluded') or
                not (source.get('business_selection') or {}).get('module_ids')):
            continue
        claims={c['id']:c for c in source['business_context']['claims']}
        for excerpt in selected_excerpts(source):
            claim_id=excerpt['business_claim_id']
            if not excerpt.get('business_usable'):
                continue
            ref=source['id']+'/'+claim_id
            verified[ref]=dict(id=ref,text=claims[claim_id]['text'],
                               source_id=source['id'],excerpt_id=excerpt['id'],
                               origin=excerpt['business_origin'],
                               evidence_status=excerpt['business_status'])
    return verified


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


def plan_contract(p, kind=None):
    if kind is not None:
        require(kind in ('mrd','prd'),'SCHEMA_INVALID','文档类型仅支持 MRD/PRD')
        context_ids=list(verified_context(p))
        normative_ids=[i['id'] for i in p['items'] if allowed(i,p)]
        discussion_ids=[i['id'] for i in discussion_items(p) if not allowed(i,p)]
        claim_ids=list(verified_business_context(p))
        section_key=('goal' if kind=='mrd' else 'function')
        example=None
        if context_ids or normative_ids or claim_ids:
            sample=dict(section_key=section_key,context_refs=context_ids[:1],
                        normative_refs=normative_ids[:1],discussion_refs=[],explanations=[])
            if not context_ids and not normative_ids:
                sample['explanations']=[dict(text='仅演示结构，不是项目结论。',
                    evidence_refs=dict(context_refs=[],normative_refs=[],
                                       business_claim_refs=claim_ids[:1]))]
            example=dict(plan_version='4',sections=[sample])
            jsonschema.Draft202012Validator(PLAN_SCHEMA_V4).validate(example)
        elif discussion_ids:
            example=dict(plan_version='4',sections=[dict(section_key='discussion',context_refs=[],
                normative_refs=[],discussion_refs=discussion_ids[:1],explanations=[])])
        focus=('MRD 解释问题、使用者、价值依据、目标、衡量方式和业务影响；没有数据时只写具名待决。'
               if kind=='mrd' else
               'PRD 解释场景、操作流程、状态、数据、权限、异常和可观察验收；未知行为只写具名待决。')
        return dict(version='prd-plan-contract-7',plan_version='4',document_type=kind,
            example=example,can_generate=example is not None,
            example_notice='仅演示合法引用结构，不是项目答案。' if example else '没有可引用的已核对事实或条目，不可生成。',
            section_keys=([key for key in ('background','users','goal','scope')] if kind=='mrd' else
                          [key for key in SECTION_TITLES if key not in ('limits','discussion')]),
            context_ref_ids=context_ids,normative_item_ids=normative_ids,
            discussion_item_ids=discussion_ids,business_claim_ref_ids=claim_ids,
            rules='根节点只有 plan_version=4、sections；每节只有 section_key、context_refs、normative_refs、discussion_refs、explanations。'
                  '每节至少有一项当前有效引用；explanations 为可选的有来源解释，每项只含 text 和 evidence_refs；'
                  'evidence_refs 必须完整含 context_refs、normative_refs、business_claim_refs，至少一项非空，且仅引用对应白名单。'
                  '解释只写有来源的信息增量，不在各节换词复述同一功能。MRD聚焦问题、场景、价值、目标范围和影响；PRD聚焦流程、状态数据、边界和可观察验收。'
                  '已核对上下文若已在有该 context_ref 的解释里完整引用原句，不再重复回填展示；原始快照仍保留。'+
                  ('MRD正文只安排背景、使用者、目标、范围；全部已选规范原文统一在末尾产品要求附录单次展示。'
                   if kind=='mrd' else 'PRD的requirement、rule、acceptance原文固定分别进入功能需求、业务规则、验收条件；行为字段同步展示。')+
                  'context_refs、normative_refs 所列原文及 PRD 行为字段会自动展示；explanations 没有新的有用关系或业务影响时用 []，不得换词复述或写组装过程。'
                  '解释只可归纳引用的已核对事实，不能发明数字、目标、权限、状态转换、默认行为或新规则；未知交还澄清候选。'
                  '已选规范由程序逐字回填，解释不能更改其含义；候选条款、推断及冲突观察不能成为无标签正文事实。'
                  'context_refs、normative_refs、discussion_refs 的权限和附录规则沿用 v2。'+focus+
                  '本文为待人工核对草稿；模型解释无法由机器保证语义保真，须继续语义审查和人工评审。')
    context_ids=list(verified_context(p))
    normative_ids=[i['id'] for i in p['items'] if allowed(i,p)]
    discussion_ids=[i['id'] for i in discussion_items(p) if not allowed(i,p)]
    example=None
    for key,field,refs in (('background','context_refs',context_ids),
                           ('function','normative_refs',normative_ids),
                           ('discussion','discussion_refs',discussion_ids)):
        if refs:
            section=dict(section_key=key,context_refs=[],normative_refs=[],discussion_refs=[])
            section[field]=refs[:1]
            example=dict(plan_version='2',sections=[section])
            jsonschema.Draft202012Validator(PLAN_SCHEMA).validate(example)
            break
    return dict(version='prd-plan-contract-4',example=example,can_generate=example is not None,
        example_notice='示例仅演示当前合法引用的结构，不是完整章节安排，也不作为失败回退产物。' if example else
                       '当前没有可引用的已核对上下文或条目，不可生成章节建议；example=null，不得虚构 ID 或空章节。',
        section_keys=[key for key in SECTION_TITLES if key!='limits'],context_ref_ids=context_ids,
        normative_item_ids=normative_ids,discussion_item_ids=discussion_ids,
        rules='根节点只有且必须包含 plan_version=2、sections。'
        '所有 sections 元素只有且必须包含 section_key、context_refs、normative_refs、discussion_refs。'
        'section_key 只从 section_keys 选择；章节标题由程序生成。不得输出 title、narration、limitations 或任何自由业务文案。'
        '每个章节至少包含一项当前有效引用；没有内容的章节直接省略，禁止空章节。context_refs 不得跨章节重复。'
        'context_refs 只能使用当前已核对 context_ref_ids；未核对的 product_context 不可引用。'
        'normative_refs 只能使用 normative_item_ids；白名单为空时必须全部为 []。'
        'discussion_refs 只能使用 discussion_item_ids，程序统一移入条目身份与历史讨论附录，逐条保留实际采纳状态与范围，不与规范正文混排。问题、方案、来源 ID 不得放入这两个字段。'
        '已有回答、未决问题、来源读取限制和未覆盖项由程序保留，不输出 limits 占位章节或自由说明。'
        'example 仅示范引用结构；can_generate=false 时不可生成，不把 null 或空结构当作章节建议。')


def repair_instruction(error,p,kind=None):
    if kind is not None:
        contract=plan_contract(p,kind)
        return ('重新输出完整 v4 文档计划，只修结构、出处与可追溯解释。保留所有有效引用，'
                '逐项对照 context_ref_ids、normative_item_ids、discussion_item_ids、business_claim_ref_ids；'
                'explanations 每段至少一条有效依据；有出处且无冲突的源码/配置观察可说明快照现状，无需逐条人工确认；候选、推断、未知和冲突不能写成确定事实，'
                '不能在解释中新增规则、数值或改变规范原文。空章节省略；未决问题返回澄清阶段。'
                '\n实际错误：'+error.message+'\n唯一文档计划契约：'+dumps(contract))
    common=('重新输出完整 v2 章节引用对象，只修结构或引用；不重选方向、不补答、不改规则或采纳状态。业务原文由程序回填。'
            '本轮检查全部章节，逐项对照 context_ref_ids、normative_item_ids、discussion_item_ids 三类当前白名单；'
            '严格保留有效引用，不得改变规范身份，不把已选规范移入讨论，也不把候选升为规范。'
            '省略空章节；context_refs 不得跨章节重复；限制与未决事项由程序保留，不输出 limits 占位章节。')
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
            notice='本组条目均不在本期规范白名单，不能放入 normative_refs。selected 可能是范围外已采纳条目，不能据此推翻 plan_contract.normative_item_ids。只有明确列入 discussion_item_ids 的条目才可放入 discussion_refs，其余仅用于核对背景。selected仅为当前草稿采纳，不代表业务负责人批准；原条目和后续回答可能存在差异，必须并列保留。'),
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
    version=plan.get('plan_version')
    schema={'2':PLAN_SCHEMA,'3':PLAN_SCHEMA_V3,'4':PLAN_SCHEMA_V4}.get(version,PLAN_SCHEMA_V4)
    errors=list(jsonschema.Draft202012Validator(schema).iter_errors(plan))
    details=[''.join('['+str(x)+']' if isinstance(x,int) else ('.' if j else '')+x for j,x in enumerate(e.absolute_path)) or '根节点' for e in errors]
    def error_message(e):
        if e.validator in ('maxItems','minItems','maxLength','minLength'):
            return f'{e.validator}={e.validator_value}，实际长度={len(e.instance)}'
        return e.message[:400]
    require(not errors,'SCHEMA_INVALID','成文章节建议错误：'+'；'.join(at+' '+error_message(e) for at,e in zip(details,errors)))
    items={i['id']:i for i in p['items']}
    explained=version in ('3','4')
    v4=version=='4'
    contract=plan_contract(p,kind if explained else None)
    item_ref_ids=set(contract['normative_item_ids'])|set(contract['discussion_item_ids'])
    current_context=verified_context(p)
    # The exact checked context survives in the project snapshot. Its reader
    # line need not repeat a sourced explanation that contains the full text.
    covered_context=set()
    if v4:
        for planned in plan['sections']:
            for explanation in planned['explanations']:
                for key in explanation['evidence_refs']['context_refs']:
                    value=current_context.get(key)
                    if value and value.strip() in explanation['text']:
                        covered_context.add(key)
    sections=[]; coverage={}; discussed=set(); discussion_order=[]; placed={}; context_placed={}; discussion_locations={}
    prd_order=[]; prd_seen=set()
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
    require(any(s['context_refs'] or s['normative_refs'] or s['discussion_refs'] or
                (explained and s['explanations']) for s in plan['sections']),
            'OUTPUT_EMPTY','章节建议没有任何当前有效引用；不能将结构示例当作项目文档')
    for n,s in enumerate(plan['sections']):
        title=SECTION_TITLES[s['section_key']]
        blocks=[]
        for j,key in enumerate(s['context_refs']):
            at=f'sections[{n}].context_refs[{j}]'
            require(key in contract['context_ref_ids'],'REFERENCE_INVALID',at+' '+key+' 不是当前已核对的非空产品上下文字段')
            if key not in context_placed:
                if key not in covered_context:
                    blocks.append(text(CONTEXT_LABELS[key]+'：'+current_context[key]))
                context_placed[key]=title
        for j,r in enumerate(s['normative_refs']):
            at=f'sections[{n}].normative_refs[{j}]'
            require_item(r,at,p,item_ref_ids)
            require(r in contract['normative_item_ids'],'SEMANTIC_BLOCKED',at+' '+r+' 不在本期已选规范白名单；需业务决定，不能自动采纳或改写为确定性叙述')
            if v4 and kind=='mrd':
                # The MRD body uses clauses as evidence only. The exact texts
                # and coverage are placed once in its product-requirement annex.
                continue
            if v4 and kind=='prd':
                if r not in prd_seen:
                    prd_order.append(r)
                    prd_seen.add(r)
                continue
            if r in coverage:
                blocks.append(text('参见「'+placed[r]+'」中的 '+r+'，原文与依据不变。',[r]))
                continue
            blocks.append(dict(kind=items[r]['kind'],ref_ids=[r],text=None))
            blocks.append(text(f'{r}：{items[r]["epistemic_status"]}；草稿采纳不代表业务负责人批准'+provenance(items[r]),[r]))
            placed[r]=title
        if explained:
            require(s['section_key'] in (contract['section_keys'] if v4 else
                                         [key for key in SECTION_TITLES if key not in ('limits','discussion')]) or
                    (s['section_key']=='discussion' and s['discussion_refs']),
                    'REFERENCE_INVALID',f'sections[{n}].section_key 不适用于 {kind.upper()} 正文')
            for j,explanation in enumerate(s['explanations']):
                basis=explanation['evidence_refs']
                at=f'sections[{n}].explanations[{j}].evidence_refs'
                require(not (v4 and WRITING_INSTRUCTION.search(explanation['text'])),
                        'SCHEMA_INVALID',f'sections[{n}].explanations[{j}].text 是写作指令而非读者正文；直接写有来源的产品事实或使用 []')
                require(any(basis.values()),'REFERENCE_INVALID',at+' 缺少已核对依据')
                for group,allowed_refs in (('context_refs',set(contract['context_ref_ids'])),
                    ('normative_refs',set(contract['normative_item_ids'])),
                    ('business_claim_refs',set(contract['business_claim_ref_ids']))):
                    for k,ref in enumerate(basis[group]):
                        require(ref in allowed_refs,'REFERENCE_INVALID',
                                f'{at}.{group}[{k}] {ref} 不在当前已核对依据白名单')
                blocks.append(dict(kind='explanation',ref_ids=basis['normative_refs'],
                                   evidence_refs=copy.deepcopy(basis),text=explanation['text']))
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
        if v4 and not blocks and s['normative_refs']:
            continue
        if v4 and not blocks and s['context_refs'] and all(key in covered_context for key in s['context_refs']):
            # A later explanation may carry this section's complete context.
            # Omit the now-empty heading instead of demanding a paid repair.
            continue
        require(blocks,'OUTPUT_EMPTY',f'sections[{n}] 没有可呈现的新引用；不要生成空章节或只重复 context_refs')
        sid=section(title,blocks)
        for b in blocks:
            if b['kind'] in NORMATIVE:
                for r in b['ref_ids']:coverage.setdefault(r,[]).append(sid)
    # Preserve all verified context and current normative clauses even if the plan omits them.
    missing_context=[key for key in current_context if key not in context_placed and key not in covered_context]
    missing_normative=[] if v4 else [i for i in p['items'] if allowed(i,p) and i['id'] not in coverage]
    if v4:
        for key in missing_context:
            title=SECTION_TITLES[CONTEXT_SECTION_V4[key]]
            target=next((existing for existing in sections if existing['title']==title),None)
            if target is None:
                section(title,[])
                target=sections[-1]
            target['blocks'].append(text(CONTEXT_LABELS[key]+'：'+current_context[key]))
    elif missing_context or missing_normative:
        blocks=[text('以下已核对事实与已选条款未由章节建议指定位置，程序按原文补列供核对。')]
        blocks += [text(CONTEXT_LABELS[key]+'：'+current_context[key]) for key in missing_context]
        for item in missing_normative:
            blocks.append(dict(kind=item['kind'],ref_ids=[item['id']],text=None))
            blocks.append(text(f'{item["id"]}：{item["epistemic_status"]}；草稿采纳不代表业务负责人批准'+provenance(item),[item['id']]))
        sid=section('补列的已核对事实与规范条款',blocks)
        for item in missing_normative:coverage[item['id']]=[sid]
    if v4 and kind=='prd':
        prd_order.extend(i['id'] for i in p['items'] if allowed(i,p) and i['id'] not in prd_seen)
        for item_kind,title in (('requirement',SECTION_TITLES['function']),
                                ('rule',SECTION_TITLES['rules']),
                                ('acceptance',SECTION_TITLES['acceptance'])):
            ordered=[items[ref] for ref in prd_order if items[ref]['kind']==item_kind]
            if not ordered:
                continue
            target=next((s for s in sections if s['title']==title),None)
            if target is None:
                sid=section(title,[])
                target=sections[-1]
            else:
                sid=target['section_id']
            normative_blocks=[]
            for item in ordered:
                normative_blocks.append(dict(kind=item_kind,ref_ids=[item['id']],text=None))
                normative_blocks.append(text(f'{item["id"]}：{item["epistemic_status"]}；草稿采纳不代表业务负责人批准'+provenance(item),[item['id']]))
                coverage[item['id']]=[sid]
            target['blocks'][:0]=normative_blocks
    # The MRD keeps every selected clause, after the product body, in one annex.
    # PRD and historical plan versions retain their original clause placement.
    if v4 and kind=='mrd':
        normative=[i for i in p['items'] if allowed(i,p)]
        if normative:
            blocks=[]
            for item in normative:
                blocks.append(dict(kind=item['kind'],ref_ids=[item['id']],text=None))
                blocks.append(text(f'{item["id"]}：{item["epistemic_status"]}；草稿采纳不代表业务负责人批准'+provenance(item),[item['id']]))
            sid=section(MRD_NORMATIVE_APPENDIX_TITLE,blocks)
            for item in normative:coverage[item['id']]=[sid]
    # Discussion is never interleaved with current normative body sections.
    # Keep model-selected order first, then retain other non-normative items
    # with their own selection/scope identity, including selected goals.
    appendix_ids=discussion_order+[i['id'] for i in p['items'] if i['id'] not in coverage
                  and i['id'] not in discussed and not represented_revision(i,items)]
    if appendix_ids:
        sid=section(DISCUSSION_APPENDIX_TITLE,[discussion(items[r]) for r in appendix_ids])
        discussion_locations={r:sid for r in appendix_ids}
    questions=[]
    for q in p['questions']:
        if q['status']=='answered':
            related='\n'.join(items[r]['statement'] for r in q.get('related_refs',[]) if r in items)
            answer_sources=[s['id']+'/'+e['id'] for s in p['sources'] for e in s['excerpts'] if e['text']==q['answer']]
            applied=any(r in items and items[r].get('selection_status')=='selected'
                        for r in q.get('applied_item_ids',q.get('applied_requirement_ids',[]))) and q.get('understanding_status')=='applied'
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
