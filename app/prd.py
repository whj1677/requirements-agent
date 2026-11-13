"""Bounded document planning contract; canonical text and bookkeeping stay local."""
import copy
import re
import jsonschema
from .core import digest, dumps, require
from .document_schema import SCHEMA, profile
from .document_mapping import build_reference_mapping
from .requirements import delivery_items, item_content
from .product_flow import INTAKE, SCOPE, status as flow_status

NORMATIVE = ('requirement', 'rule', 'acceptance')
FLOW_RULES = (
    'nodes.id 必须唯一，edges.from/to 必须逐字引用 nodes.id，不填写 label；节点名称可以重复，不能据此猜测连线。'
    '流程图每个节点与分支分别核对同一业务对象、同一动作及同一前提的当前原文和真实来源。'
    '新增、修改、查看是不同动作；即使共用页面或来源段落，也不能互借失败处理、提示原因、手工重试、记录写入或权限规则。'
    '提示原因、保留输入、保留原数据和允许重试分别核对，不把其中一项有据推成其余均有据。'
    '仅明确共同适用于多个对象或动作的原句才可共享；某对象新增失败的专有规则不得迁给另一对象或修改失败。'
    '页面、入口及区域的位置限定也必须由同一动作的原文支持，不能因新增位于某页就推定查看或修改也在该页。'
    '若原文只明确新增位置，start及分流前所有公共节点都使用不带页面的业务对象名（如“处理记录操作”）；'
    '不能先“进入某页”再分查看、新增、修改，这会把新增位置强加给全部后续动作。页面名只放在新增分支内，'
    '查看、修改保留位置中性表达，也不为未知位置新增必须由用户决定的业务问题。'
    '只读查看须有独立结束或有据的等待路径；空态与列表态都不能强制接续新增或修改。'
    '新增、修改必须由用户显式选择进入，保留已明确的合法写入分支，不以删去写入分支规避路径问题。'
    '保留有据的失败反馈和重试，不统一删除，也不为补齐图形而虚造未约定行为；历史流程图不是新业务来源。'
    '异常恢复的等待、计时或重试仅作用于已进入该异常的路径，不能强加给正常首次执行或正常上电路径。'
    '原文区分正常、异常、异常后的恢复时，必须画出相应分流及正常路径；先判是否需要恢复，再判断恢复计时，'
    '不能让所有有效输入无条件经过恢复等待。不同异常的恢复计时、清零条件和解除条件不得互借。'
    '逐条用当前验收示例沿图走一遍，尤其核对首次执行、恰好到阈值、异常与保护同时存在的路径；'
    '节点和边不得改变正文中的计算顺序、状态优先级、等待适用范围或发送与执行的区别。'
    '采用紧凑业务图：一个节点表达一个业务动作或真正分流的判断，不将字段清单、每项必填或长度规则逐条展开为节点；'
    '同一对象同一动作的共同前置校验合并为一个校验节点，具体条件保留在正文与边条件中。'
    '合并完全相同且来源作用域相同的处理和结束节点，不重复画失败终点；不同动作的反馈或权限不同则不得为精简而合并。'
    '不新增没有业务分支的占位节点，不为追求节点数量删去真实成功、失败、未知结果、查看结束或有据重试路径。'
)
READABILITY_RULES = (
    '面向产品经理快速阅读：只写有信息增量的短段落，同一事实不在概述、功能、规则、验收中反复展开。'
    'explanations 不复述程序已回填的条款。先结论后条件；复杂段落用显式 Markdown ## 小标题和空行，'
    '可比较的参数、状态或输入输出可用标准管道表格，禁止把整篇规则或多场景验收放在一个表格单元格。'
    '简洁不得省略数值、单位、触发条件、时序、异常、权限或未决事项；不以减少页数作为内容质量目标。'
)
DISCUSSION_APPENDIX_TITLE = '附录：条目身份与历史讨论'
MRD_NORMATIVE_APPENDIX_TITLE = '附录：对应产品要求'
REFS = dict(type='array', items=dict(type='string', minLength=1, maxLength=120), maxItems=100, uniqueItems=True)
SECTION_TITLES = {
    'background':'背景与现状', 'goal':'目标与价值', 'scope':'本期范围与边界',
    'users':'使用者与权限', 'function':'功能需求', 'rules':'业务规则',
    'acceptance':'验收条件', 'discussion':'待核对内容', 'limits':'资料限制与未决事项',
}
CLOUD_PRD_SECTIONS = {'interaction':'交互逻辑', 'business_flow':'业务流程说明'}
EMPTY_BEHAVIOR = ('未知','待确认','资料未说明','未提及异常分支','不适用')
MRD_SECTION_TITLES_V4 = {
    **SECTION_TITLES,
    'priority':'优先级与决策依据',
    'validation':'试点验证与成功判据',
    'risks':'依赖、风险与待决',
}
CONTEXT_LABELS = {**INTAKE, **SCOPE}
CONTEXT_SECTION_V4 = {
    'product':'background', 'module':'background', 'current_state':'background',
    'intent':'goal', 'value':'goal', 'priority':'goal',
    'users':'users',
    'change_scope':'scope', 'preserve_scope':'scope', 'out_of_scope':'scope',
}
PRD_ASSOCIATION_TITLES = {
    'requirement':'功能需求关联说明',
    'rule':'业务规则关联说明',
    'acceptance':'验收条件关联说明',
    'mixed':'跨条目业务流程与关联说明',
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
from .requirement_details import DETAIL_SCHEMA
PLAN_SCHEMA_V4['properties']['flowcharts'] = dict(type='array', maxItems=100, items=dict(
    type='object', additionalProperties=False, required=['requirement_id','flow'], properties=dict(
        requirement_id=dict(type='string'), flow=copy.deepcopy(DETAIL_SCHEMA['properties']['flow']))))
PLAN_SCHEMA_V4['properties']['sections']['items']['properties']['section_key'] = {
    'enum':list(MRD_SECTION_TITLES_V4)+list(CLOUD_PRD_SECTIONS)}
PLAN_SCHEMA_V4['properties']['sections']['items']['properties']['explanations']['items']['properties']['text']['description'] = (
    '直接交付读者的成稿正文，不是写作计划、作者指令或组装过程说明；无来源信息增量时 explanations 使用空数组。')
WRITING_INSTRUCTION = re.compile(
    r'(?:^|[。！？\n])\s*(?:本期)?(?:PRD|MRD|文档正文|规则层|验收层|功能层)\s*(?:需要|应当?|不应|必须|须)'
    r'|(?:值班人员|用户|操作者)需要在文档中')


def cloud_prd_required(p, kind):
    if kind != 'prd':
        return False
    # Current product identity takes precedence over an old project title.
    product = str(p.get('product_context',{}).get('product') or p.get('name','')).lower()
    return (not any(word in product for word in ('不是云平台','非云平台','与云平台无关','not a cloud platform','not cloud platform'))
            and any(word in product for word in ('云平台','cloud platform')))


def normalize_flow_endpoint_labels(plan):
    """Resolve exact, unique label aliases in model plans; never guess endpoints."""
    result = copy.deepcopy(plan)
    changes = []
    if not isinstance(result, dict) or result.get('plan_version') != '4':
        return result, changes
    charts = result.get('flowcharts', [])
    if not isinstance(charts, list) or any(not isinstance(chart, dict) for chart in charts):
        return result, changes
    for chart_index, chart in enumerate(charts):
        flow = chart.get('flow', {})
        if list(jsonschema.Draft202012Validator(DETAIL_SCHEMA['properties']['flow']).iter_errors(flow)):
            continue
        nodes, edges = flow['nodes'], flow['edges']
        ids = [node['id'] for node in nodes]
        if len(set(ids)) != len(ids):
            continue
        labels = {}
        for node in nodes:
            labels.setdefault(node.get('label'), []).append(node['id'])
        pending = []
        for edge_index, edge in enumerate(edges):
            for field in ('from', 'to'):
                endpoint = edge.get(field)
                if endpoint in ids:
                    continue
                matches = labels.get(endpoint, []) if endpoint else []
                if len(matches) != 1:
                    pending = None
                    break
                pending.append(dict(path=['flowcharts', chart_index, 'flow', 'edges', edge_index, field],
                                    original=endpoint, node_id=matches[0]))
            if pending is None:
                break
        for change in pending or []:
            edges[change['path'][-2]][change['path'][-1]] = change['node_id']
            changes.append(change)
    return result, changes


def validate_flowchart(flow, requirement, project):
    from .requirement_confirmation import confirmation_current, _sources_valid
    from .detail_rendering import flow_layout
    require(requirement['id'] in {i['id'] for i in delivery_items(project)}
            and requirement['kind']=='requirement' and not requirement.get('detail_contract')
            and confirmation_current(project, requirement), 'REFERENCE_INVALID', '流程图必须绑定当前已确认的基础需求')
    _sources_valid(project, requirement)
    errors=list(jsonschema.Draft202012Validator(DETAIL_SCHEMA['properties']['flow']).iter_errors(flow))
    require(not errors,'SCHEMA_INVALID','流程图结构无效')
    ids=[edge['id'] for edge in flow['edges']]
    require(len(ids)==len(set(ids)),'REFERENCE_INVALID','流程连线编号重复')
    node_ids=[node['id'] for node in flow['nodes']]
    require(len(node_ids)==len(set(node_ids)),'REFERENCE_INVALID','流程节点编号重复；每个 nodes.id 必须唯一')
    invalid=[edge.get('id','')+'.'+field+'='+str(edge.get(field)) for edge in flow['edges']
             for field in ('from','to') if edge.get(field) not in node_ids]
    require(not invalid,'REFERENCE_INVALID','流程连线端点必须引用 nodes.id：'+'；'.join(invalid))
    normalized=copy.deepcopy(flow)
    outgoing={edge['from'] for edge in normalized['edges']}
    for node in normalized['nodes']:
        if node['kind']=='end' and node['id'] in outgoing:node['kind']='action'
    try:flow_layout(normalized)
    except (ValueError,KeyError,TypeError) as error:
        require(False,'SEMANTIC_BLOCKED',str(error))
    return normalized


def saved_prd_plan(p):
    contract=plan_contract(p,'prd')
    plan=dict(plan_version='4',sections=[dict(section_key='function',context_refs=contract['context_ref_ids'],
        normative_refs=contract['normative_item_ids'],discussion_refs=[],explanations=[])])
    if cloud_prd_required(p,'prd'):
        items={item['id']:item for item in p['items']}
        for section,fields in (('interaction',('entry','exceptions')),('business_flow',('flow','result'))):
            explanations=[]
            for iid in contract['required_detail_requirement_ids']:
                behavior=items[iid].get('behavior',{})
                text='\n'.join(behavior[field] for field in fields if behavior.get(field))
                if text:explanations.append(dict(text=text,evidence_refs=dict(context_refs=[],normative_refs=[iid],business_claim_refs=[])))
            if explanations:plan['sections'].append(dict(section_key=section,context_refs=[],normative_refs=[],discussion_refs=[],explanations=explanations))
    return plan


def basic_flow_ids(p):
    from .requirement_confirmation import confirmation_current
    requirements=[i for i in delivery_items(p) if i['kind']=='requirement']
    if not requirements or any(i.get('detail_contract') or not confirmation_current(p,i) for i in requirements):return []
    if any(not str(i.get('behavior',{}).get('flow','')).strip().rstrip('。')
           or str(i.get('behavior',{}).get('flow','')).strip().rstrip('。') in EMPTY_BEHAVIOR for i in requirements):return []
    return [i['id'] for i in requirements]


def cloud_prd_requirements(p):
    # Structured requirements are rendered locally; only legacy requirements
    # need model-authored explanations of the seven behavior fields.
    return [i['id'] for i in delivery_items(p) if allowed(i,p) and i['kind']=='requirement'
            and not i.get('detail_contract')]


def normative_evidence(item, *, include_behavior=False):
    text = item['statement']
    if include_behavior and item['kind']=='requirement':
        text += '\n'+'\n'.join(str(value) for value in item.get('behavior',{}).values() if value)
    return text


def cloud_prd_gaps(doc, p):
    if not cloud_prd_required(p, doc['document_type']):
        return []
    required = set(cloud_prd_requirements(p))
    if not required:
        return []
    gaps = []
    for title in CLOUD_PRD_SECTIONS.values():
        blocks = [b for s in doc['sections'] if s['title']==title for b in s['blocks']]
        covered = {r for b in blocks if b['kind']=='explanation' and (b.get('text') or '').strip()
                   for r in b.get('evidence_refs',{}).get('normative_refs',[])}
        missing = required-covered
        if missing:
            gaps.append('云平台 PRD 缺少'+title+'：'+'、'.join(sorted(missing)))
        if any('【资料待确认】' in (b.get('text') or '') for b in blocks):
            gaps.append('云平台 PRD 的'+title+'仍有具名资料待确认，须核对后再交付。')
    from .product_flow import BEHAVIOR
    for item in delivery_items(p):
        if item['id'] not in required:
            continue
        missing=[label for key,label in BEHAVIOR.items()
                 if not str(item.get('behavior',{}).get(key,'')).strip()
                 or str(item.get('behavior',{}).get(key,'')).strip().rstrip('。')
                in EMPTY_BEHAVIOR]
        if missing:
            gaps.append('云平台 PRD 的 '+item['id']+' 缺少已核对行为：'+'、'.join(missing)+
                        '；按资料补充交互与流程，确实不适用须说明依据。')
    return gaps


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
    states=p.get('context_field_states') or {}
    proposal_evidence=p.get('product_context_proposal_evidence') or {}
    def unresolved(key):
        text=current.get(key,'')
        evidence=proposal_evidence.get(key) or {}
        confirmation=(p.get('context_confirmations') or {}).get(key) or {}
        confirmed=(confirmation.get('actor')=='authenticated_local_user' and
            confirmation.get('content_hash')==digest(dict(text=text,state=states.get(key))))
        # Completion of a page is not evidence that a proposed priority or
        # permission became a business fact. Keep explicit qualifiers intact.
        return (isinstance(text,str) and any(word in text for word in ('待确认','待决定','待产品确认'))
            or evidence.get('text')==text and evidence.get('basis') in ('recommendation','unknown')
            and evidence.get('evidence_status')=='unreviewed' and not confirmed)
    return {key:copy.deepcopy(current[key]) for key in CONTEXT_LABELS
            if isinstance(current.get(key),str) and current[key].strip()
            and (states.get(key) or {}).get('status') not in ('unknown','not_applicable','none') and not unresolved(key)}


def sourced_acceptance_ids(p):
    """Selected ACs with a reference to an available, nonexcluded excerpt."""
    pairs={(source['id'],excerpt['id']) for source in p.get('sources',[])
           if not source.get('excluded') and source.get('parse_status') in ('read','partial')
           for excerpt in source.get('excerpts',[])}
    return {item['id'] for item in p['items'] if allowed(item,p) and item['kind']=='acceptance'
            and any((ref.get('source_id'),ref.get('excerpt_id')) in pairs
                    for ref in item.get('source_refs',[]))}


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
        validation_acceptance_ids=sourced_acceptance_ids(p) if kind=='mrd' else set()
        cloud_required = cloud_prd_required(p,kind) and bool(cloud_prd_requirements(p))
        required_analysis_sections=(['priority'] if kind=='mrd' and 'priority' in context_ids else [])+(
            ['validation'] if kind=='mrd' and {'value','intent'} & set(context_ids)
            and validation_acceptance_ids else [])+(list(CLOUD_PRD_SECTIONS) if cloud_required else [])
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
            if cloud_required:
                for key in CLOUD_PRD_SECTIONS:
                    example['sections'].append(dict(section_key=key,context_refs=[],
                        normative_refs=[],discussion_refs=[],explanations=[dict(
                            text='仅演示引用结构，不是项目结论。',evidence_refs=dict(
                                context_refs=[],normative_refs=cloud_prd_requirements(p)[:1],business_claim_refs=[]))]))
            jsonschema.Draft202012Validator(PLAN_SCHEMA_V4).validate(example)
        elif discussion_ids:
            example=dict(plan_version='4',sections=[dict(section_key='discussion',context_refs=[],
                normative_refs=[],discussion_refs=discussion_ids[:1],explanations=[])])
        focus=('MRD 解释问题、使用者、价值依据、目标、衡量方式和业务影响；没有数据时只写具名待决。'
               if kind=='mrd' else
               'PRD 解释场景、操作流程、状态、数据、权限、异常和可观察验收；未知行为只写具名待决。')
        explanation_ids=[i['id'] for i in p['items'] if i['id'] in normative_ids
                         and not (kind=='prd' and i.get('detail_contract'))]
        return dict(version='prd-plan-contract-17',plan_version='4',document_type=kind,
            example=example,can_generate=example is not None,
            example_notice='仅演示合法引用结构，不是项目答案。' if example else '没有可引用的已核对事实或条目，不可生成。',
            section_keys=([key for key in ('background','users','goal','scope','priority','validation','risks')] if kind=='mrd' else
                          [key for key in SECTION_TITLES if key not in ('limits','discussion')]+
                          (list(CLOUD_PRD_SECTIONS) if cloud_required else [])),
            required_analysis_sections=required_analysis_sections,
            required_detail_requirement_ids=cloud_prd_requirements(p) if cloud_required else [],
            program_rendered_detail_ids=[i['id'] for i in delivery_items(p)
                                         if kind=='prd' and i['kind']=='requirement' and i.get('detail_contract')],
            validation_acceptance_ids=sorted(validation_acceptance_ids),
            context_ref_ids=context_ids,normative_item_ids=normative_ids,
            explanation_normative_item_ids=explanation_ids,
            section_explanation_normative_item_ids={'users':normative_ids} if kind=='prd' else {},
            discussion_item_ids=discussion_ids,business_claim_ref_ids=claim_ids,
            rules=(FLOW_RULES+READABILITY_RULES if kind=='prd' else '')+'根节点为 plan_version=4、sections；PRD为有已确认流程的基础需求生成flowcharts，真实缺口不编造，不新增业务决定。每节只有 section_key、context_refs、normative_refs、discussion_refs、explanations。'
                  '每节至少有一项当前有效引用；explanations 为可选的有来源解释，每项只含 text 和 evidence_refs；'
                  'evidence_refs 必须完整含 context_refs、normative_refs、business_claim_refs，至少一项非空，且仅引用对应白名单。'
                  '每段解释的依据独立绑定；叙述已能登录、查看、导出或其它现有实现时，该段evidence_refs必须引用current_state或相应肯定工程证据。仅引用users或本期要求不能借用其它段的现状证明。章节context_refs的不重复排版约束不限制explanations.evidence_refs重复引用真实依据；现状即使已在背景章展示，使用者章引用其能力时仍须在该段evidence_refs列current_state。'
                  '解释只写有来源的信息增量，不在各节换词复述同一功能。MRD说明使用者的实际工作路径与问题影响；priority章用已核对priority分析为什么先做及决策依据；validation章依据已选验收或已核对价值，说明试点中可观察的成功判据、验证方式和待补数据。耗时、规模、成功率或收益数字仅在本期需要且尚未确定时写具名待决，不编阈值或ROI；用户已明确不要求、不适用或排除的指标、规则与范围，必须保留该决定，不能因没有数值改写成待确认，也不能另造同义衡量要求。risks仅在有据时写依赖、风险和待决。PRD聚焦流程、状态数据、边界和可观察验收。'
                  '已核对上下文若已在有该 context_ref 的解释里完整引用原句，不再重复回填展示；原始快照仍保留。'+
                  ('MRD正文分别安排背景、使用者、目标、范围、优先级与试点验证；可用证据不足时不强制生成试点验证或风险章节。priority已核对原句由程序放在优先级章，不要在目标章重述。全部已选规范原文统一在末尾产品要求附录单次展示。'
                   'MRD归纳角色限制须保留原文的操作与条件范围：管理员不代释放，不能泛化为禁止管理员认领或参与其他未说明的操作；未说明的权限不推断允许或禁止。'
                   'MRD归纳竞争规则须保留规范条件原文：服务端首个成功不能替换为请求先到、先发或先点击者生效；成功判据与到达顺序不是同一条件，不用常见机制替代已定规则。'
                   if kind=='mrd' else 'PRD的requirement、rule、acceptance原文固定分别进入功能需求、业务规则、验收条件；行为字段同步展示。单条规范解释紧随该条目；有来源的多条规范解释进入独立关联说明章节，避免在最后一条需求下复述整套功能。')+
                  'context_refs、normative_refs 所列原文及 PRD 行为字段会自动展示；explanations 没有新的有用关系或业务影响时用 []，不得换词复述或写组装过程。'
                  '解释只可归纳引用的已核对事实，不能发明数字、目标、权限、状态转换、默认行为或新规则；未知交还澄清候选。工程快照中的现有技术只证明快照现状；未经有权决策采纳，不得写成新功能已确定采用的技术选型或业务规范。'
                  '本轮成文事实依据仅为当前规范白名单、已确认语境和有效原来源；旧文档解释不能自证为当前事实。'
                  '角色在某一操作上的限制不授予其它权限或证明其它能力：管理员不代释放不证明管理员能查看告警；未说明的权限不推断允许或禁止，也不为无关权限新增确认要求。'
                  '原文含建议、推荐、待确认时必须保留其身份，引用存在或条目已选择不能把建议变成已确认事实。'
                  '操作权限只说明谁可以或不可以执行，不等于按钮隐藏、禁用或入口不存在；已确认的权限与未定的展示属性分别表达，只有资料明确才写具体入口表现。同样，已定展示对象、页面和范围不能因样式未知而删除、改为二选一；常见交互样式只能标为建议或具名待定。'
                  '现状使用电话跟进不表示本期替代电话；建议记录只读不表示已禁止编辑；建议优先级不表示本期已确定。没有相应用户决定时不得推导这些替换、禁止或优先规则。'
                  'program_rendered_detail_ids中的详细定义由程序生成字段表、交互表、流程图、分支表和状态表；不得在explanations重新写这些字段属性、约束值或流程关系。相关条目只需列入normative_refs。'
                  '章节外层normative_refs使用normative_item_ids；explanations.evidence_refs.normative_refs使用explanation_normative_item_ids，若section_explanation_normative_item_ids有本节键则使用该节白名单。'
                  '仅PRD的users章允许引用当前详细需求，归纳有依据的角色、操作和条件，保留全部真实引用，正文留在使用者与权限章；不得据此新增权限或重写详细定义。其它章节仍不接受程序成文详细定义的重复解释；没有独立有效依据的解释使用[]，保留外层引用即可完整成文。'
                  'MRD的required_analysis_sections必须分别有至少一段explanations，不能仅列引用或空数组；priority解释为什么先做，validation说明如何观察验收，各自引用对应依据。这些必需分析不适用普通章节可以不解释的规则。'
                  '本期决定的数量边界、冲突拒绝及原子处理等规则仍是本期要求；只有current_state或肯定工程证据明确同一规则已经存在，才可称为既有约束/现行规则。不根据沿用、保持或验收前提推断已经实现。'
                  '已选规范由程序逐字回填，解释不能更改其含义；验收条款的“已有”前提可能指部署后的验收环境，不据此声称当前生产已经实现。候选条款、推断及冲突观察不能成为无标签正文事实。'
                  'context_refs、normative_refs、discussion_refs 的权限和附录规则沿用 v2。'+focus+
                  '本文为待人工核对草稿；模型解释无法由机器保证语义保真，须继续语义审查和人工评审。'+
                  ('云平台 PRD 必须独立输出 interaction（交互逻辑）和 business_flow（业务流程说明），各自用 explanations 覆盖 required_detail_requirement_ids 中每项需求；这两节的正文保留在独立章节，不移入功能条目说明。'
                   '正文用需求名称或业务对象识别对应功能，不使用内部编号代替业务说明。'
                   '交互逻辑解释入口与前置、用户操作、输入校验、页面或操作反馈，以及有依据的权限、取消和失败分支；业务流程说明触发条件、参与角色、业务处理顺序、对象及状态变化、结果和异常分支。'
                   '可以引用已选需求的已保存 behavior 字段，但不能据平台通用背景补造操作、状态或控制成功。后台或纯数据需求没有页面交互时，按资料解释其接口/任务触发与结果，不强行新增页面。'
                   '未说明的具体内容用“【资料待确认】”标明缺项及影响，保留已知流程；不写成已确认规则，也不能用“已说明/按要求”占位代替正文。'
                   if cloud_required else ''))
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


def saved_sections_schema(schema, sections):
    """Fit only deterministic saved sections; free model plans stay bounded."""
    rule = schema['properties']['sections']
    rule['maxItems'] = max(rule['maxItems'], len(sections))
    props = rule['items']['properties']
    for field in ('normative_refs', 'discussion_refs'):
        props[field]['maxItems'] = max(props[field]['maxItems'],
            max((len(section[field]) for section in sections), default=0))
    explanations = props['explanations']
    explanations['maxItems'] = max(explanations['maxItems'],
        max((len(section['explanations']) for section in sections), default=0))
    text = explanations['items']['properties']['text']
    text['maxLength'] = max(text['maxLength'],
        max((len(e['text']) for section in sections for e in section['explanations']), default=0))


def plan_schema(p,kind):
    """One document type's wire keys; never advertise another type's chapters."""
    schema=copy.deepcopy(PLAN_SCHEMA_V4)
    contract=plan_contract(p,kind)
    if kind=='prd':
        flow_ids=basic_flow_ids(p)
        flow_schema=schema['properties']['flowcharts']['items']['properties']['flow']
        for group,keys in (('nodes',('id','label','kind')),('edges',('id','from','to','condition'))):
            row=flow_schema['properties'][group]['items']
            row['properties']={key:value for key,value in row['properties'].items() if key in keys}
            row['required']=['id','label','kind'] if group=='nodes' else ['id','from','to']
        flow_schema['properties']['nodes']['items']['properties']['label'].update(
            maxLength=24,description='不超过24字的简短动作或判断名称；完整规则保留在正文，分支条件写在连线上。')
        if flow_ids:
            schema['required'].append('flowcharts')
            schema['properties']['flowcharts'].update(minItems=len(flow_ids),maxItems=len(flow_ids))
            schema['properties']['flowcharts']['items']['properties']['requirement_id']['enum']=flow_ids
            saved_sections=saved_prd_plan(p)['sections']
            saved_sections_schema(schema,saved_sections)
            schema['properties']['sections']['const']=saved_sections
        schema['properties']['flowcharts']['description']='为每个没有detail_contract的已确认基础需求画业务流程图，requirement_id引用当前需求。仅根据其原文、behavior与真实来源绘制节点和分支，不新增角色、业务决定或数据状态。节点使用简短动作或判断名称，不塞入整段规则；完整规则由正文保留，分支条件写在边的condition中。按来源区分发起操作、处理中、收到结果，不能把尚未完成的操作写成已完成后才进入处理中。存在无响应超时分支时，中间连线不得强制已收到服务端响应；页面空态判断在需要该记录的操作之前。重试回路必须重新经过原文适用的提交前校验或权限检查，不得回到校验之后的发送步骤。无明确结果或查询异常应保留原有结果，仅当来源明确原状态未知才维持未知，不得将已有成功或失败改成未知。保留来源已明确的处理中限制、成功和失败后的结束恢复、用户主动重试及保持边界；仅画来源支持的步骤，真实缺口不虚造。详细定义已有图不重复绘制。'
        schema['properties']['flowcharts']['description'] += FLOW_RULES
    else:
        schema['properties'].pop('flowcharts',None)
    keys=list(contract['section_keys'])
    if contract['discussion_item_ids']:
        keys.append('discussion')
    properties=schema['properties']['sections']['items']['properties']
    def restrict(rule,ids):
        if ids:rule['items']={**rule['items'],'enum':ids}
        else:rule['maxItems']=0
    for field,ids in (('context_refs',contract['context_ref_ids']),
                      ('normative_refs',contract['normative_item_ids']),
                      ('discussion_refs',contract['discussion_item_ids'])):
        properties[field]=copy.deepcopy(properties[field])
        restrict(properties[field],ids)
    basis=properties['explanations']['items']['properties']['evidence_refs']['properties']
    for field,ids in (('context_refs',contract['context_ref_ids']),
                      ('business_claim_refs',contract['business_claim_ref_ids'])):
        basis[field]=copy.deepcopy(basis[field])
        restrict(basis[field],ids)
    # Keep the wire schema and compiler on the same chapter-specific whitelist.
    # The users exception does not widen any other chapter's explanations.
    section_rule=schema['properties']['sections']['items']
    def explanation_rule(ids):
        refs=copy.deepcopy(REFS);restrict(refs,ids)
        return {'properties':{'explanations':{'items':{'properties':{
            'evidence_refs':{'properties':{'normative_refs':refs}}}}}}}
    overrides=contract.get('section_explanation_normative_item_ids',{})
    if overrides:
        section_rule['allOf']=[{'if':{'properties':{'section_key':{'const':key}}},
            'then':explanation_rule(ids),
            'else':explanation_rule(contract['explanation_normative_item_ids'])}
            for key,ids in overrides.items()]
    else:
        restrict(basis['normative_refs'],contract['explanation_normative_item_ids'])
    schema['properties']['sections']['items']['properties']['section_key']={'enum':keys}
    if kind=='prd' and contract['required_detail_requirement_ids']:
        required_keys=list(CLOUD_PRD_SECTIONS)
        schema['properties']['sections']['allOf']=[{'contains':{'properties':{
            'section_key':{'const':key}},'required':['section_key']}} for key in required_keys]
        schema['properties']['sections']['items'].setdefault('allOf',[]).extend([
            {'if':{'properties':{'section_key':{'enum':required_keys}}},
             'then':{'properties':{'explanations':{'minItems':1}}}}])
    if kind=='mrd':
        required=[]
        for key in contract['required_analysis_sections']:
            if key=='priority':
                evidence={'context_refs':{'contains':{'const':'priority'}}}
            else:
                evidence={'anyOf':[
                    {'properties':{'context_refs':{'contains':{'enum':['value','intent']}}}},
                    {'properties':{'normative_refs':{'contains':{'enum':contract['validation_acceptance_ids']}}}}]}
            basis_rule=({'properties':evidence} if key=='priority' else evidence)
            required.append({'if':{'properties':{'section_key':{'const':key}}},
                'then':{'properties':{'explanations':{'minItems':1,'contains':{
                    'properties':{'evidence_refs':basis_rule}}}}}})
        if required:schema['properties']['sections']['items']['allOf']=required
    return schema


def repair_instruction(error,p,kind=None):
    if kind is not None:
        contract=plan_contract(p,kind)
        return ('重新输出完整 v4 文档计划，只修结构、出处与可追溯解释。保留所有有效引用，'
                '逐项对照 context_ref_ids、normative_item_ids、discussion_item_ids、business_claim_ref_ids；'
                'explanations 每段至少一条有效依据；有出处且无冲突的源码/配置观察可说明快照现状，无需逐条人工确认；候选、推断、未知和冲突不能写成确定事实，'
                'section_key 只能从本次 document_type 对应的 section_keys 选择；discussion 仅在含有效 discussion_refs 时可用；不丢弃非法章节中的有效正文和依据，不删除业务内容来过关。'
                '不能在解释中新增规则、数值或改变规范原文。工程快照不决定新功能技术选型。'+
                ('MRD按 required_analysis_sections 分别补优先级和试点验证分析，前者引用已核对priority，后者引用已选验收或已核对value/intent；避免重复目标与范围，缺数字保留未知。'
                 if kind=='mrd' else 'PRD不输出MRD专用priority、validation、risks章节；有来源的相关正文保留原意和全部有效依据，使用当前合法章节键，程序按依据归章。explanations.evidence_refs.normative_refs使用explanation_normative_item_ids或section_explanation_normative_item_ids中本节白名单。仅users章允许保留引用当前详细需求的角色、操作与条件解释及全部真实依据，正文留在使用者与权限章。其它章节若解释引用了程序成文的详细定义，删除重复解释并保留章节外层normative_refs，完整字段、流程和交互仍由程序展示；不得只删引用却保留无依据文字，也不得改挂其他条目或伪造上下文。')+
                '空章节省略；未决问题返回澄清阶段。'
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
        D_unknowns_limits=dict(questions=[copy.deepcopy(q) for q in p['questions'] if q['status']!='answered' and not q.get('prewrite_resolution')],
            source_status=[{k:s.get(k) for k in ('id','title','purpose','parse_status','failure_reason','excluded')} for s in p['sources']],
            ui='本工作台已取消业务草图；仅交付需求文档，原页面截图仍是现状参考。' if p.get('_sketch_retired') else '尚无原型' if not p.get('ui') else '已有低保真模拟原型，不能证明业务实现',
            active_ui=None if not p.get('ui') else dict(version=p['ui']['spec']['draft_revision'],
                pages=[dict(title=page['title'],requirement_refs=page['requirement_refs'],
                    components=[dict(type=c['type'],label=c['label'],ref_ids=c['ref_ids']) for region in page['regions'] for c in region['components']]) for page in p['ui']['spec']['pages']],
                notice='这是当前活动原型的实际布局；可能已在原讨论方向上修改布局。不能用旧方案文字覆盖它，也不代表条目采纳或正式确认。'),
            current_material_count=len([s for s in p['sources'] if not s['excluded']]),
            reference_notice='当前章节参考由可信任务头 content_profile 指定；项目材料与章节参考不是同一对象。'))


def preserve_repair_explanation_refs(original,plan,p,kind):
    """Restore current valid evidence only for unchanged, uniquely matched paragraphs."""
    result=copy.deepcopy(plan);changes=[]
    if (kind!='prd' or not isinstance(original,dict) or not isinstance(plan,dict)
        or original.get('plan_version')!='4' or plan.get('plan_version')!='4'
        or not isinstance(original.get('sections'),list) or not isinstance(plan.get('sections'),list)):
        return result,changes
    contract=plan_contract(p,kind)
    for index,section in enumerate(result['sections']):
        if not isinstance(section,dict):continue
        key=section.get('section_key')
        old_sections=[s for s in original['sections'] if isinstance(s,dict) and s.get('section_key')==key]
        new_sections=[s for s in result['sections'] if isinstance(s,dict) and s.get('section_key')==key]
        if len(old_sections)!=1 or len(new_sections)!=1 or key not in contract['section_keys']:continue
        old_rows=old_sections[0].get('explanations');new_rows=section.get('explanations')
        if not isinstance(old_rows,list) or not isinstance(new_rows,list):continue
        allowed=dict(context_refs=contract['context_ref_ids'],business_claim_refs=contract['business_claim_ref_ids'],
            normative_refs=contract['section_explanation_normative_item_ids'].get(key,contract['explanation_normative_item_ids']))
        for position,paragraph in enumerate(new_rows):
            if not isinstance(paragraph,dict) or not isinstance(paragraph.get('text'),str):continue
            matches=[e for e in old_rows if isinstance(e,dict) and e.get('text')==paragraph['text']]
            if len(matches)!=1 or sum(isinstance(e,dict) and e.get('text')==paragraph['text'] for e in new_rows)!=1:continue
            old_basis=matches[0].get('evidence_refs');new_basis=paragraph.get('evidence_refs')
            if not isinstance(old_basis,dict) or not isinstance(new_basis,dict):continue
            for group,refs in allowed.items():
                old=old_basis.get(group);new=new_basis.get(group)
                if not isinstance(old,list) or not isinstance(new,list) or not all(isinstance(r,str) for r in old+new):continue
                valid=[r for r in old if r in refs]
                missing=list(dict.fromkeys(r for r in valid if r not in new))
                if missing:
                    new_basis[group]=list(dict.fromkeys(valid+new))
                    changes.append(dict(path=['sections',index,'explanations',position,'evidence_refs',group],
                        section_key=key,restored_refs=missing))
    return result,changes


def semantic_correction(plan,error,p,kind):
    """One exact explanation slot; no full-plan rewrite or canonical mutation."""
    match=re.fullmatch(r'sections\[(\d+)\]\.explanations\[(\d+)\]\.text',getattr(error,'semantic_path',''))
    if not match or plan.get('plan_version')!='4' or not getattr(error,'semantic_issues',None):return None
    si,ei=map(int,match.groups());section=plan['sections'][si];original=section['explanations'][ei]
    contract=plan_contract(p,kind)
    allowed=set(contract.get('section_explanation_normative_item_ids',{}).get(section['section_key'],contract['explanation_normative_item_ids']))
    schema={'type':'object','additionalProperties':False,'required':['explanation'],
        'properties':{'explanation':copy.deepcopy(PLAN_SCHEMA_V4['properties']['sections']['items']['properties']['explanations']['items'])}}
    schema['properties']['explanation']['properties']['text']['description']=(
        '指定段落纠正后的完整成稿正文；保留有依据内容，改正已指认的无依据断言，不返回写作计划。')
    guidance={
        'current_capability_absence':(
            '该原句把未提供的现状当成已证实的能力缺失。资料列出已有功能或提出新增目标，'
            '不证明现有系统不能做到其它事情。改写该原句中的现状否定断言；'
            '可按已确认intent/value表述本期目标，不得保留或换词重述无依据的能力缺失。'),
        'success_order_condition':(
            '该原句把服务端首个成功的判据改成先到、先提交或先点击。'
            '按来源恢复成功这一条件；不能仅凭请求到达次序断定成功或失败。'),
    }
    context=verified_context(p)
    normative={i['id']:normative_evidence(i) for i in p['items'] if i['id'] in allowed}
    business=verified_business_context(p)
    evidence_maps=dict(context_refs=context,normative_refs=normative,business_claim_refs=business)
    packet=dict(path=error.semantic_path,section_key=section['section_key'],original=copy.deepcopy(original),
        findings=copy.deepcopy(error.semantic_issues),original_evidence=error.semantic_evidence,
        correction_targets=[dict(quote=f['quote'],dimension=f['dimension'],instruction=guidance.get(f['dimension'],
            '改正该原句超出来源的条件或事实断言，保留来源实际支持的内容。')) for f in error.semantic_issues],
        direct_evidence={group:{ref:evidence_maps[group][ref] for ref in original['evidence_refs'][group]
            if ref in evidence_maps[group]} for group in evidence_maps},
        context=context,normative=normative,business=business)
    messages=[dict(role='system',content=(
        '仅修正指定explanation的来源语义错误，不改章节、其他段落、规范、验收、来源或人工决定。'
        '依据findings的原句和original_evidence核对同一对象及条件，保持有来源的内容；只把无依据现状、排他/先后判据等改为来源实际支持的表述。'
        'correction_targets是已定位且必须纠正的断言；逐项改正其quote中的问题，仅改旁句、补引用或原样返回不算纠正。'
        '允许删去无依据子句并保留同段有依据内容及其引用；不得删除整段合法内容，也不要求产品经理确认无关现状。'
        'direct_evidence按原引用键列出直接依据；context/normative/business仅为可选的真实补充依据，新增引用也必须支持对应文字。'
        '缺失资料不证明能力不存在；首成功不等于首提交或先到。不要写修复过程、历史未采纳讨论或新PM问题。'
        '返回explanation.text和完整evidence_refs，只可使用提供context/normative/business的真实键，不能去掉引用保留无据文字。'
        '该补丁仍须重新通过全文语义与引用检查。只返回Schema JSON。\n可信任务头：'+dumps(dict(stage='prd',schema=schema)))),
        dict(role='user',content=[dict(type='text',text=dumps(packet))])]
    return dict(section=si,explanation=ei,original=copy.deepcopy(original),schema=schema,messages=messages,findings=packet['findings'])


def apply_semantic_correction(plan,prepared,patch):
    require(not list(jsonschema.Draft202012Validator(prepared['schema']).iter_errors(patch)),
        'PRD_SEMANTIC_CORRECTION_INVALID','段落语义纠正格式不正确，原文档和原候选保留。')
    value=copy.deepcopy(plan)
    require(value['sections'][prepared['section']]['explanations'][prepared['explanation']]==prepared['original'],
        'PRD_SEMANTIC_CORRECTION_INVALID','待纠正段落发生变化，未应用补丁。')
    value['sections'][prepared['section']]['explanations'][prepared['explanation']]=copy.deepcopy(patch['explanation'])
    return value


def normalize_structured_explanations(plan,p,kind):
    """Recover the explicit layout contract for program-rendered requirements.

    Outside the users chapter, these references cannot support a second explanation. Preserve
    their full normative/context bindings on the chapter and let the existing
    compiler emit the exact saved facts. Keep original prose in diagnostics;
    never detach references while retaining unsupported narrative.
    """
    value=copy.deepcopy(plan);changes=[]
    if kind!='prd' or not isinstance(value,dict) or value.get('plan_version')!='4':return value,changes
    contract=plan_contract(p,kind);rendered=set(contract['program_rendered_detail_ids']);contexts=set(contract['context_ref_ids'])
    if not isinstance(value.get('sections'),list):return value,changes
    retained=[]
    for index,section in enumerate(value['sections']):
        if (isinstance(section,dict)
                and set(section)=={'section_key','context_refs','normative_refs','discussion_refs','explanations'}
                and section['section_key'] in contract['section_keys']
                and section['section_key'] not in contract['required_analysis_sections']
                and all(section[key]==[] for key in ('context_refs','normative_refs','discussion_refs','explanations'))):
            changes.append(dict(path=f'sections[{index}]',raw=copy.deepcopy(section),
                rule='empty-optional-section',reason='可选章节没有内容或引用，省略空标题；条款覆盖仍须校验。'))
        else:retained.append(section)
    # Preserve a fully empty response for the normal OUTPUT_EMPTY/schema gate.
    if retained:value['sections']=retained
    else:changes=[]
    if not rendered:return value,changes
    for index,section in enumerate(value['sections']):
        # Role/operation/condition explanations retain their actual evidence;
        # compile_plan still validates every binding and the narrative itself.
        if isinstance(section,dict) and section.get('section_key')=='users':continue
        if not isinstance(section,dict) or not all(isinstance(section.get(k),list) for k in ('context_refs','normative_refs','explanations')):continue
        if not all(isinstance(ref,str) for ref in section['context_refs']+section['normative_refs']):continue
        kept=[]
        for n,explanation in enumerate(section['explanations']):
            if not isinstance(explanation,dict) or set(explanation)!={'text','evidence_refs'} or not isinstance(explanation['text'],str):kept.append(explanation);continue
            basis=explanation['evidence_refs']
            if (not isinstance(basis,dict) or set(basis)!={'context_refs','normative_refs','business_claim_refs'}
                or not all(isinstance(basis[k],list) and all(isinstance(ref,str) for ref in basis[k]) for k in basis)
                or not basis['normative_refs'] or not set(basis['normative_refs'])<=rendered
                or not set(basis['context_refs'])<=contexts or basis['business_claim_refs']):
                kept.append(explanation);continue
            section['normative_refs']=list(dict.fromkeys(section['normative_refs']+basis['normative_refs']))
            section['context_refs']=list(dict.fromkeys(section['context_refs']+basis['context_refs']))
            changes.append(dict(path=f'sections[{index}].explanations[{n}]',raw=copy.deepcopy(explanation),
                rule='program-rendered-detail-no-duplicate-explanation',
                reason='所引需求已由程序完整成文；保留章节的全部条款及上下文绑定，重复解释原文留作诊断。'))
        section['explanations']=kept
    return value,changes


def compile_plan(plan, p, kind, omitted=(), *, include_sketch=True, saved_content=False):
    if not include_sketch:p=without_sketch(p)
    saved_sections = kind=='prd' and plan.get('plan_version')=='4' and (saved_content or bool(basic_flow_ids(p)))
    if saved_sections:
        plan=dict(plan,sections=saved_prd_plan(p)['sections'])
    version=plan.get('plan_version')
    schema={'2':PLAN_SCHEMA,'3':PLAN_SCHEMA_V3,'4':PLAN_SCHEMA_V4}.get(version,PLAN_SCHEMA_V4)
    if saved_sections:
        schema=copy.deepcopy(schema)
        saved_sections_schema(schema,plan['sections'])
    errors=list(jsonschema.Draft202012Validator(schema).iter_errors(plan))
    details=[''.join('['+str(x)+']' if isinstance(x,int) else ('.' if j else '')+x for j,x in enumerate(e.absolute_path)) or '根节点' for e in errors]
    def error_message(e):
        if e.validator in ('maxItems','minItems','maxLength','minLength'):
            return f'{e.validator}={e.validator_value}，实际长度={len(e.instance)}'
        return e.message[:400]
    require(not errors,'SCHEMA_INVALID','成文章节建议错误：'+'；'.join(at+' '+error_message(e) for at,e in zip(details,errors)))
    from .semantic_quality import current_counting_checks
    require(not current_counting_checks(p),'SEMANTIC_BLOCKED','当前条款/回答的计数口径有冲突，请先澄清、修订与人工核对，不能生成确定规则文档。')
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
    prd_order=[]; prd_seen=set(); prd_explanations={}; prd_context_explanations={}; prd_associations={}; prd_details={}
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
        title=({**MRD_SECTION_TITLES_V4,**CLOUD_PRD_SECTIONS} if v4 else SECTION_TITLES)[s['section_key']]
        blocks=[]
        for j,key in enumerate(s['context_refs']):
            at=f'sections[{n}].context_refs[{j}]'
            require(key in contract['context_ref_ids'],'REFERENCE_INVALID',at+' '+key+' 不是当前已核对的非空产品上下文字段')
            if v4 and kind=='prd':
                # Checked context has stable reader chapters, regardless of
                # where the model first uses it as supporting evidence.
                continue
            if v4 and kind=='mrd' and key=='priority':
                # Priority is displayed once in its own section even if the
                # model referenced it while planning another MRD chapter.
                context_placed[key]=MRD_SECTION_TITLES_V4['priority']
                continue
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
                    ('normative_refs',set(contract.get('section_explanation_normative_item_ids',{}).get(
                        s['section_key'],contract.get('explanation_normative_item_ids',contract['normative_item_ids'])))),
                    ('business_claim_refs',set(contract['business_claim_ref_ids']))):
                    for k,ref in enumerate(basis[group]):
                        require(ref in allowed_refs,'REFERENCE_INVALID',
                                f'{at}.{group}[{k}] {ref} 不在当前已核对依据白名单'+
                                ('；此条目详细定义由程序直接成文，请保留章节引用并移除重复解释。'
                                 if group=='normative_refs' and ref in contract.get('program_rendered_detail_ids',[]) else ''))
                from .semantic_quality import validate_explanation
                structured_refs=[r for r in basis['normative_refs'] if items[r].get('detail_contract')]
                users = v4 and kind=='prd' and s['section_key']=='users'
                require(not (kind=='prd' and structured_refs and not users),'SEMANTIC_BLOCKED',
                        '已结构化详细定义由程序直接成文，不接受模型重复解释规范值：'+'、'.join(structured_refs))
                detail = v4 and kind=='prd' and s['section_key'] in CLOUD_PRD_SECTIONS
                evidence='\n'.join([current_context[r] for r in basis['context_refs']]+[normative_evidence(items[r],include_behavior=detail) for r in basis['normative_refs']]+[verified_business_context(p)[r]['text'] for r in basis['business_claim_refs']])
                existing='\n'.join([current_context[r] for r in basis['context_refs'] if r=='current_state']+[verified_business_context(p)[r]['text'] for r in basis['business_claim_refs']])
                scenario='\n'.join([current_context[r] for r in basis['context_refs'] if r!='current_state']+[normative_evidence(items[r],include_behavior=detail) for r in basis['normative_refs']])
                if users and structured_refs:
                    from .detail_rendering import detail_plaintext
                    saved_details='\n'.join(detail_plaintext(items[r],group) for r in structured_refs
                                            for group in ('fields','interactions','flow','state_transitions'))
                    evidence+='\n'+saved_details
                    scenario+='\n'+saved_details
                validate_explanation(explanation['text'],evidence,existing,at.replace('.evidence_refs','.text'),scenario_evidence=scenario)
                block=dict(kind='explanation',ref_ids=basis['normative_refs'],
                           evidence_refs=copy.deepcopy(basis),text=explanation['text'])
                if v4 and kind=='prd':
                    normative=basis['normative_refs']
                    if users:
                        prd_context_explanations.setdefault('users',[]).append(block)
                    elif detail:
                        prd_details.setdefault(s['section_key'],[]).append(block)
                    elif len(normative)==1:
                        prd_explanations.setdefault(normative[0],[]).append(block)
                    elif normative:
                        kinds={items[ref]['kind'] for ref in normative}
                        association_kind=next(iter(kinds)) if len(kinds)==1 else 'mixed'
                        prd_associations.setdefault(association_kind,[]).append(block)
                    else:
                        context_sections={CONTEXT_SECTION_V4[key] for key in basis['context_refs']}
                        destination=(s['section_key'] if s['section_key'] in context_sections else
                                     'background' if basis['business_claim_refs'] or len(context_sections)>1 else
                                     next(iter(context_sections)) if context_sections else s['section_key'])
                        prd_context_explanations.setdefault(destination,[]).append(block)
                else:
                    blocks.append(block)
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
        if v4 and kind=='prd' and not blocks and s['explanations']:
            # Referenced explanations are attached to their evidence's section.
            continue
        if v4 and not blocks and s['context_refs'] and (kind=='prd' or all(key in covered_context for key in s['context_refs'])):
            # A later explanation may carry this section's complete context.
            # Omit the now-empty heading instead of demanding a paid repair.
            continue
        if (v4 and s['section_key'] not in contract['required_analysis_sections']
                and not any(s[key] for key in ('context_refs','normative_refs','discussion_refs','explanations'))):
            # A legal optional heading carries no content to repair. Required
            # sections and overall reference coverage still validate below.
            continue
        require(blocks,'OUTPUT_EMPTY',f'sections[{n}] 没有可呈现的新引用；不要生成空章节或只重复 context_refs')
        sid=section(title,blocks)
        for b in blocks:
            if b['kind'] in NORMATIVE:
                for r in b['ref_ids']:coverage.setdefault(r,[]).append(sid)
    if v4 and kind=='mrd':
        if 'priority' in contract['required_analysis_sections']:
            require(any(s['section_key']=='priority' and any('priority' in e['evidence_refs']['context_refs']
                    for e in s['explanations']) for s in plan['sections']),
                    'SEMANTIC_BLOCKED','MRD 缺少有已核对 priority 依据的独立优先级分析')
        if 'validation' in contract['required_analysis_sections']:
            accepted=set(contract['validation_acceptance_ids'])
            require(any(s['section_key']=='validation' and any(
                    accepted.intersection(e['evidence_refs']['normative_refs']) or
                    {'value','intent'}.intersection(e['evidence_refs']['context_refs'])
                    for e in s['explanations']) for s in plan['sections']),
                    'SEMANTIC_BLOCKED','MRD 缺少引用已选验收或已核对价值/目标的独立试点验证分析')
    if v4 and kind=='prd':
        for key in contract['required_analysis_sections']:
            covered={ref for block in prd_details.get(key,[]) for ref in block['evidence_refs']['normative_refs']}
            missing=set(contract['required_detail_requirement_ids'])-covered
            require(not missing,'SEMANTIC_BLOCKED','云平台 PRD 缺少'+CLOUD_PRD_SECTIONS[key]+'：'+'、'.join(sorted(missing)))
    # Preserve all verified context and current normative clauses even if the plan omits them.
    missing_context=[key for key in current_context if key not in context_placed and key not in covered_context]
    missing_normative=[] if v4 else [i for i in p['items'] if allowed(i,p) and i['id'] not in coverage]
    if v4:
        for key in missing_context:
            title=(MRD_SECTION_TITLES_V4['priority'] if kind=='mrd' and key=='priority'
                   else SECTION_TITLES[CONTEXT_SECTION_V4[key]])
            target=next((existing for existing in sections if existing['title']==title),None)
            if target is None:
                section(title,[])
                target=sections[-1]
            target['blocks'].append(text(CONTEXT_LABELS[key]+'：'+current_context[key]))
            context_placed[key]=target['section_id']
        if (kind=='mrd' and 'priority' in current_context and
                'priority' not in missing_context and 'priority' not in covered_context):
            title=MRD_SECTION_TITLES_V4['priority']
            target=next((existing for existing in sections if existing['title']==title),None)
            if target is None:
                section(title,[])
                target=sections[-1]
            target['blocks'].insert(0,text(CONTEXT_LABELS['priority']+'：'+current_context['priority']))
        if kind=='prd':
            for key,explanations in prd_context_explanations.items():
                title=SECTION_TITLES[key]
                target=next((existing for existing in sections if existing['title']==title),None)
                if target is None:
                    section(title,[])
                    target=sections[-1]
                target['blocks'].extend(explanations)
    elif missing_context or missing_normative:
        blocks=[text('以下已核对事实与已选条款未由章节建议指定位置，程序按原文补列供核对。')]
        blocks += [text(CONTEXT_LABELS[key]+'：'+current_context[key]) for key in missing_context]
        for item in missing_normative:
            blocks.append(dict(kind=item['kind'],ref_ids=[item['id']],text=None))
            blocks.append(text(f'{item["id"]}：{item["epistemic_status"]}；草稿采纳不代表业务负责人批准'+provenance(item),[item['id']]))
        sid=section('补列的已核对事实与规范条款',blocks)
        for item in missing_normative:coverage[item['id']]=[sid]
    if v4 and kind=='prd':
        for key,blocks in prd_details.items():
            limit=SCHEMA['$defs']['prdResult']['properties']['sections']['items']['properties']['blocks']['maxItems']
            # A missing-flow notice may be appended to the first flow section.
            if key=='business_flow': limit-=1
            for offset in range(0,len(blocks),limit):
                section(CLOUD_PRD_SECTIONS[key],blocks[offset:offset+limit])
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
            block_limit=SCHEMA['$defs']['prdResult']['properties']['sections']['items']['properties']['blocks']['maxItems']
            normative_blocks=[]
            for item in ordered:
                group=[dict(kind=item_kind,ref_ids=[item['id']],text=None),
                       text(f'{item["id"]}：{item["epistemic_status"]}；草稿采纳不代表业务负责人批准'+provenance(item),[item['id']])]
                group.extend(prd_explanations.get(item['id'],[]))
                if (normative_blocks or target['blocks']) and len(target['blocks'])+len(normative_blocks)+len(group)>block_limit:
                    target['blocks'][:0]=normative_blocks
                    sid=section(title,[])
                    target=sections[-1]
                    normative_blocks=[]
                normative_blocks.extend(group)
                coverage[item['id']]=[sid]
            target['blocks'][:0]=normative_blocks
        for association_kind,explanations in prd_associations.items():
            blocks=[]
            for explanation in explanations:
                blocks.append(text('关联条款：'+'、'.join(explanation['ref_ids'])))
                blocks.append(explanation)
            section(PRD_ASSOCIATION_TITLES[association_kind],blocks)
    if kind=='prd':
        from .detail_rendering import detail_block, GROUP_TITLES
        from .prd_sections import function_inventory_block, risk_register_blocks
        inventory=function_inventory_block(p)
        if inventory['ref_ids']:
            section('主要功能清单',[inventory])
            inventory_section=sections.pop()
            function_index=next((index for index,value in enumerate(sections)
                                 if value['title']==SECTION_TITLES['function']),0)
            sections.insert(function_index,inventory_section)
        structured=[i for i in delivery_items(p) if i['kind']=='requirement' and i.get('detail_contract')]
        for group,title in GROUP_TITLES.items():
            if not structured:
                break
            target=next((s for s in sections if s['title']==title),None)
            blocks=[detail_block(item,group) for item in structured]
            if target is None:
                section(title,blocks)
            else:
                target['blocks'].extend(blocks)
        charts=plan.get('flowcharts',[]) if v4 else []
        seen_flows=set()
        for chart in charts:
            ref=chart['requirement_id']
            require(ref in items and ref not in seen_flows,'REFERENCE_INVALID','流程图需求引用不存在或重复：'+ref)
            normalized_flow=validate_flowchart(chart['flow'],items[ref],p)
            from .requirement_confirmation import row_fingerprint
            section(items[ref]['title']+' · 业务流程图',[dict(kind='flowchart',ref_ids=[ref],text=items[ref]['title']+' · 业务流程图',
                flow=normalized_flow,source_hash=row_fingerprint(p,items[ref]))])
            seen_flows.add(ref)
        if not structured and not charts:
            textual_flow=next((s for s in sections if s['title']==GROUP_TITLES['flow']),None)
            if textual_flow is not None:
                textual_flow['blocks'].append(text('当前仅有文字流程说明；尚未生成结构化功能流程图。请在需求详情中核对流程节点、分支与流转条件。'))
            else:
                section('功能流程图',[text('当前没有已核对的结构化流程；未生成流程图。请在需求详情中核对流程节点、分支与流转条件。')])
        section('风险点与待确认',risk_register_blocks(p))
    # The MRD keeps every selected clause after the product body. Split the
    # annex at clause boundaries to respect the compiled document contract.
    # PRD and historical plan versions retain their original clause placement.
    if v4 and kind=='mrd':
        normative=[i for i in p['items'] if allowed(i,p)]
        block_limit=SCHEMA['$defs']['prdResult']['properties']['sections']['items']['properties']['blocks']['maxItems']
        items_per_section=block_limit//2  # Keep each clause and its provenance together.
        for offset in range(0,len(normative),items_per_section):
            batch=normative[offset:offset+items_per_section]
            blocks=[]
            for item in batch:
                blocks.append(dict(kind=item['kind'],ref_ids=[item['id']],text=None))
                blocks.append(text(f'{item["id"]}：{item["epistemic_status"]}；草稿采纳不代表业务负责人批准'+provenance(item),[item['id']]))
            title=MRD_NORMATIVE_APPENDIX_TITLE+(f'（续 {offset//items_per_section+1}）' if offset else '')
            sid=section(title,blocks)
            for item in batch:coverage[item['id']]=[sid]
    # Discussion is never interleaved with current normative body sections.
    # Keep model-selected order first, then retain other non-normative items
    # with their own selection/scope identity, including selected goals.
    appendix_ids=discussion_order+[i['id'] for i in p['items'] if i['id'] not in coverage
                  and i['id'] not in discussed and not represented_revision(i,items)]
    if appendix_ids:
        block_limit=SCHEMA['$defs']['prdResult']['properties']['sections']['items']['properties']['blocks']['maxItems']
        for offset in range(0,len(appendix_ids),block_limit):
            batch=appendix_ids[offset:offset+block_limit]
            # Keep the exact internal title: the reader must hide every audit chunk.
            sid=section(DISCUSSION_APPENDIX_TITLE,[discussion(items[r]) for r in batch])
            discussion_locations.update({r:sid for r in batch})
    questions=[]
    for q in p['questions']:
        if q.get('prewrite_resolution'):continue
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
    # V4 places these exact originals deterministically above. A model's omitted
    # layout choice is not missing content after successful local placement.
    unplaced_context=[key for key in missing_context if key not in context_placed] if v4 else missing_context
    planning_limits=(['章节建议未指定的已核对上下文字段：'+'、'.join(unplaced_context)] if unplaced_context else [])+(
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
