import asyncio
import base64
import json
import io
from PIL import Image
import time
from urllib.parse import urlsplit
import httpx
from .core import KIT, Problem, brief_hash, dumps, now, read_json, require
from .contracts import SCHEMA, WIRE, profile
from .config import ProjectEnvironment, usable_key
from .prd import PLAN_SCHEMA, plan_contract, context as document_context
from . import ingest
from .understanding import contract as understanding_contract

DEFAULT = dict(name='DeepSeek 官方', base_url='https://api.deepseek.com', model='deepseek-flash',
               key_env='RA_DEEPSEEK_API_KEY', vision='documented', json_mode=True, timeout=120,
               max_calls=8, max_tokens=16000, context_chars=180000, action_seconds=300,
               thinking_disabled=True, review_thinking=True, documented_at='2026-09-23', local_allowed=False, proxy='', input_price=None, output_price=None)
STAGES = read_json(KIT / 'prompts/registry.json')['stage_files']


def delimiter_hint(content):
    """Diagnose bracket mismatches without repairing or accepting invalid output."""
    stack=[];quoted=False;escaped=False
    for pos,char in enumerate(content):
        if quoted:
            if escaped:escaped=False
            elif char=='\\':escaped=True
            elif char=='"':quoted=False
            continue
        if char=='"':quoted=True
        elif char in '[{':stack.append((char,pos))
        elif char in ']}':
            if not stack:return f'字符偏移 {pos} 的 {char} 没有对应开括号。'
            opener,start=stack[-1];expected=']' if opener=='[' else '}'
            if char!=expected:return f'括号层级错误：字符偏移 {start} 的 {opener} 尚未关闭；偏移 {pos} 处应先用 {expected} 关闭它，实际却为 {char}。不要只在结尾增加大括号。'
            stack.pop()
    if stack:return '输出结束时仍有未关闭容器：'+str(stack[-4:])+'；按从内到外次序关闭数组与对象。'
    return ''


def origin(config):
    u = urlsplit(config['base_url'])
    require(u.scheme in ('https', 'http') and u.hostname and not u.username and not u.password and not u.query and not u.fragment, 'CONFIG_INVALID', '模型地址无效')
    require(u.scheme == 'https' or config.get('local_allowed'), 'CONFIG_INVALID', 'HTTP 模型需要显式本地白名单授权')
    return f'{u.scheme}://{u.netloc}'


class Provider:
    def __init__(self, env_path=None):
        self.keys = {}
        self.environment = ProjectEnvironment(env_path)
        self.env_origins = {'RA_DEEPSEEK_API_KEY':'https://api.deepseek.com'}

    def resolve_key(self, config):
        endpoint=origin(config)
        if endpoint in self.keys:
            value=self.keys[endpoint]
            return (value,'session') if usable_key(value) else ('','none')
        env=config.get('key_env','')
        return self.environment.credential(env) if self.env_origins.get(env)==endpoint else ('','none')

    def key(self, config):
        return self.resolve_key(config)[0]

    def key_status(self, config):
        value,source=self.resolve_key(config)
        return dict(key_configured=bool(value),key_env=config.get('key_env',''),key_source=source,bound_origin=origin(config))

    async def request(self, config, messages, evidence=None):
        # A removed/replaced customer license also blocks the next paid model call.
        from .core import FROZEN
        if FROZEN:
            from .licensing import LicenseManager, LicenseError
            try:
                await asyncio.to_thread(LicenseManager().verify_cached)
            except LicenseError as error:
                raise Problem(error.code, error.message, 403) from error
        key = self.key(config)
        require(key, 'CONFIG_MISSING', '尚未配置此接收端的 API Key；未发送材料')
        body = dict(model=config['model'], messages=messages, max_tokens=config['max_tokens'], stream=False)
        if config['json_mode']:
            body['response_format'] = {'type': 'json_object'}
        if origin(config) == 'https://api.deepseek.com':
            body['thinking'] = {'type': 'disabled' if config.get('thinking_disabled') else 'enabled'}
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=config['timeout'], follow_redirects=False, trust_env=False, proxy=config.get('proxy') or None) as client:
                response = await client.post(config['base_url'].rstrip('/') + '/chat/completions', json=body, headers={'Authorization': 'Bearer ' + key})
        except httpx.TimeoutException:
            raise Problem('TIMEOUT', '模型请求超时；已发请求可能计费')
        except httpx.HTTPError:
            raise Problem('NETWORK_ERROR', '无法连接模型接收端')
        codes = {400:'PARAMETER_UNSUPPORTED', 401:'AUTH_FAILED', 403:'AUTH_FAILED', 402:'PROVIDER_QUOTA', 404:'MODEL_UNSUPPORTED', 429:'RATE_LIMITED'}
        if evidence and response.status_code != 200:
            evidence.response(http_status=response.status_code, elapsed_seconds=round(time.monotonic()-started, 3))
        require(response.status_code == 200, codes.get(response.status_code, 'PROVIDER_ERROR'), '模型接口 HTTP ' + str(response.status_code))
        try:
            raw = response.json()
            choice = raw['choices'][0]
            content = choice['message'].get('content')
            meta = dict(request_model=config['model'], response_model=raw.get('model'), usage=raw.get('usage'), finish_reason=choice.get('finish_reason'), elapsed_seconds=round(time.monotonic()-started, 3), origin=origin(config))
        except (KeyError, ValueError, IndexError, TypeError):
            if evidence:
                evidence.response(http_status=response.status_code, elapsed_seconds=round(time.monotonic()-started, 3))
            raise Problem('SCHEMA_INVALID', '模型协议响应无法读取')
        if evidence:
            # Persist only final content, before JSON parsing and all validation.
            evidence.response(content, http_status=response.status_code, finish_reason=meta['finish_reason'],
                              usage=meta['usage'], response_model=meta['response_model'], elapsed_seconds=meta['elapsed_seconds'])
        require(choice.get('finish_reason') != 'length', 'OUTPUT_TRUNCATED', '模型输出截断；请缩减生成范围或提高输出预算')
        require(isinstance(content, str) and content.strip(), 'OUTPUT_EMPTY', '模型返回空内容')
        require(key not in content, 'SECURITY_BLOCKED', '模型输出包含凭据信息，已拒绝保存')
        # Never persist hidden reasoning; only final response is processed.
        try:
            value = json.loads(content, parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
        except json.JSONDecodeError as error:
            raise Problem('SCHEMA_INVALID', f'模型最终内容不是完整 JSON：第 {error.lineno} 行，第 {error.colno} 列，字符偏移 {error.pos}；{error.msg}。'+delimiter_hint(content)+'请检查该位置的引号、逗号与括号配对，重新输出完整对象；不得删除业务内容。')
        except ValueError:
            raise Problem('SCHEMA_INVALID', '模型最终内容不是完整 JSON')
        return value, meta


def request_schema(stage):
    if stage=='prd':return PLAN_SCHEMA
    if stage=='ingest':
        return ingest.schema()
    if stage not in ('ui','review'):return SCHEMA
    # The provider cannot load local $ref files. Inline the actual UI dependencies,
    # preserving the validator's schema and its no-draft-mutation rule.
    def expand(value):
        if isinstance(value,list):return [expand(x) for x in value]
        if not isinstance(value,dict):return value
        if '$ref' in value:
            ref=value['$ref']
            return expand(WIRE if ref=='wireframe.schema.json' else SCHEMA['$defs'][ref.rsplit('/',1)[1]])
        return {k:expand(v) for k,v in value.items()}
    props={**SCHEMA['properties'],'stage':{'const':stage},'proposals':{'const':[]},'questions':{'const':[]},'result':SCHEMA['$defs']['uiResult' if stage=='ui' else 'reviewResult']}
    if stage=='review':props.pop('product_context_proposal',None)
    return expand(dict(type='object',properties=props,required=SCHEMA['required'],additionalProperties=False))


def review_documents(p):
    """Send the actual readable text once, without duplicate artifact snapshots.

    Canonical facts and questions are already sent separately. The reader expands
    each document's own frozen statements/behavior; current facts do not replace
    old text that the reviewer must be able to identify as stale or contradictory.
    """
    from .document_reader import reader_document
    result={}
    for kind,artifact in p['documents'].items():
        reader=reader_document(artifact)
        result[kind]=dict(id=artifact['id'],document_version=artifact.get('document_version'),
            draft_revision=artifact.get('draft_revision'),
            current=artifact.get('brief_hash')==brief_hash(p) and kind not in p.get('stale_document_kinds',[]),
            content=dict(document_type=kind,title=reader['title'],sections=[
                {key:s[key] for key in ('section_id','title','level','parent_section_id','blocks')}
                for s in reader['sections']]),
            coverage=artifact['content'].get('coverage',[]),
            reference_mapping=artifact['content'].get('reference_mapping',[]),
            snapshot_recorded='item_snapshot' in artifact,
            notice='这是该文档实际阅读/导出的正文；与当前条目不一致时报告差异，不用当前条目替换正文后声称一致。')
    return result


def ui_structure_example():
    # Structural illustration only, never used as a model failure fallback.
    component=dict(component_id='EXAMPLE-TEXT',type='text',label='结构占位，不是项目内容',description='',
        ref_ids=[],fields=[],columns=[],rows=[],interaction=dict(action='none',target_id=None,target_state=None),provisional=True)
    page=dict(page_id='EXAMPLE-PAGE',title='结构示例',requirement_refs=[],regions=[dict(name='main',components=[component])],
        states=['normal'],state_messages=dict(normal='结构示例',loading=None,empty=None,error=None,forbidden=None),not_applicable_states=[])
    return dict(schema_version='1.1',stage='ui',summary='仅演示数组与对象嵌套，不能作为项目产物',proposals=[],questions=[],findings=[],used_source_refs=[],limitations=[],
        result=dict(spec=dict(schema_version='1.1',title='结构示例',draft_revision=0,design_intent='仅演示协议结构',demo_data_label='模拟数据，仅用于原型演示',pages=[page])))


def assemble(p, stage, user_message, config, folder, kind='prd', generation_target=None, pending_images_only=False):
    header = dict(stage=stage, project_id=p['id'], current_revision=p['revision'], mode=p['mode'], schema=request_schema(stage), remaining_budget=config['max_calls'])
    if stage!='ui':header['delivery_policy']='工作台已取消业务草图生成与核对。交付 MRD/PRD；原页面截图仍可作为现状参考。不要要求生成或批准草图才能成文，不把历史草图当作本轮交付。业务入口、字段、流程及异常仍必须在需求中表达。'
    if stage=='ingest':header['context_contract']='整理后必须输出 product_context_proposal 的全部字段，供用户核对，未知值填空字符串；不自动采纳或批准。已有条目仅因信息实质变化才提出修订，不重复建同义候选。'
    if stage=='ingest':header['ingest_contract']=ingest.contract()
    if stage in ('ingest','clarify','change'):header['understanding_contract']=understanding_contract()
    if generation_target:
        header['generation_target']=generation_target
    if stage == 'prd':
        header.update(document_type=kind, content_profile=profile(kind,p.get('reference_mode')=='builtin'),plan_contract=plan_contract(p))
    if stage == 'ui':header['structure_example']=ui_structure_example()
    base = (KIT / 'prompts/00_system.md').read_text('utf-8')
    if stage=='prd':base=base.split('## 输出')[0]
    system = base + '\n' + (KIT / 'prompts' / STAGES[stage]).read_text('utf-8') + '\n可信任务头：' + dumps(header)
    context = {k:p[k] for k in ('items','questions','options','documents','ui')}
    context['discussion_direction'] = [dict(option_id=o['id'], name=o['name'], status=o['direction_status'])
                                       for o in p['options'] if o.get('direction_status')]
    context['direction_semantics'] = 'direction_status仅记录讨论方向；关联proposed_item_refs不代表条目已采纳，以各条目的selection_status和epistemic_status为准。旧selection_status是历史整包操作。'
    context['user_message'] = user_message
    context['product_context']=p.get('product_context',{})
    context['sketch_review']=p.get('sketch_review',{})
    if stage!='ui':
        context['ui']=None
        context['sketch_review']={}
    context['requirement_relations']=p.get('requirement_relations',[])
    context['recent_messages'] = p['messages'][-8:]
    if stage=='clarify':
        from .product_flow import clarification_focus
        context['stage_focus']=dict(**clarification_focus(p),
            instruction='content_gaps 检查本期候选内容，adoption_gaps 仅是人工采纳状态；不得因尚未采纳而忽略候选的行为和验收缺项。'
            '优先核对已发送材料、已有回答及待采纳修订，提出保留原编号的完整修订候选；已有同义候选不重复建立。'
            '已明确行为须同时提出 acceptance 候选，用 related_refs 指向原需求并引用支持预期结果的来源；把前提、操作、可观察结果写清，不编造时限、数量、权限或异常规则。'
            'answer_refs 是原需求的回答应用跟踪字段：仅在 kind=requirement、action=revise 且 target_item_id 位于 answer_revision_targets 对应 requirement_ids 时填写。'
            '规则修订和新增 acceptance 不填写 answer_refs；它们通过 source_refs 引用实际回答片段（见问题的 answer_source_refs 及本轮 excerpts），通过 related_refs 关联原需求。'
            '核对已回答问题的每个 related_refs：其中 rule 仍写待决定或旧口径时，也需为该 rule 提出 revise 候选，用回答来源补齐规则原文。只修订 requirement 的 behavior 不能解除关联规则中的冲突；不得一面说已定、一面保留未决规范。'
            '同一功能的字段/默认值/筛选组合优先在该需求 behavior 及关联 rule/acceptance 表达，不机械拆成缺少上下文的独立功能。'
            '回答 understanding_status=applied 时不再重复提交相同需求修订或 answer_refs；仅补真实内容差异。已有相同验收（含 candidate 和 selected）则复用其编号，不再 action=add。规则修订也不需要顺带复制已经完整的需求与验收。'
            '已知权限和异常等通过 behavior 与相关来源表达；仅当材料明确适用于该需求时复用，不把空字段自动填成“不适用”。'
            '来源须覆盖本条各项事实，不能只引用标题片段。材料已有答案不要重复提问或在 limitations 中重新宣称未知。'
            '只有材料确实没有答案才提问；缺少结构关联不等于缺少业务决定。移出范围的问题仍未知，不再次当成本期前提。'
            '不要把材料未要求的额外量化指标提升为本期成文前置决定。'
            'summary 说明实际补齐内容及仍待人工核对事项，不替程序宣布成文或正式确认已满足。')
        context['recent_messages']=[{k:m.get(k) for k in ('role','stage','text','created')} for m in p['messages'][-8:]]
        context['context_notice']='当前条目、问题、范围理由和实际材料优先；历史摘要可能过期，旧结构化输出不重复发送。'
    if stage=='ui':
        context['document_status']={kind:{k:doc.get(k) for k in ('id','draft_revision','brief_hash')} for kind,doc in context.pop('documents').items() if doc}
        context['recent_messages']=[{k:m.get(k) for k in ('role','stage','text','created')} for m in p['messages'][-8:]]
        context['context_notice']='本次生成页面：当前条目、问答、方案、活动原型及材料完整保留；文档仅提供版本信息，历史结构化输出不重复发送。历史摘要可能过期，以当前快照及材料为准。'
    active_sources={s['id'] for s in p['sources'] if not s['excluded']}
    context['vision_observations']=[m['response'] for m in p['messages'] if m.get('stage')=='vision'
        and m.get('response') and all(r['source_id'] in active_sources for r in m['response']['used_source_refs'])]
    context['source_status'] = [{k:s.get(k) for k in ('id','title','parse_status','failure_reason','excluded','purpose','parent_source_id','sha256','container_source_id','container_locator')} for s in p['sources']]
    if stage=='review':
        from .requirements import delivery_items
        from .product_flow import status as flow_status
        from .prd import represented_revision, verified_context
        context['documents']=review_documents(p)
        context['recent_messages']=[{k:m.get(k) for k in ('role','stage','created')} for m in p['messages'][-8:]]
        current_items={i['id']:i for i in p['items']}
        context['human_checks']=dict(
            stages=[{k:s[k] for k in ('step','complete','content_hash','needs_recheck')} for s in flow_status(p)[:3]],
            verified_product_context=verified_context(p),
            represented_revisions=[dict(revision_id=i['id'],current_item_id=i['target_item_id'],
                basis='暂缓修订的完整内容与已采纳稳定条目完全相同，原条目与历史修订仍一并提供供核对')
                for i in p['items'] if represented_revision(i,current_items)],
            notice='产品上下文核对与独立条目采纳是分别记录的操作；同文内容可已在上下文核对，但尚未作为独立条目采纳。未采纳身份本身不能推翻已核对字段，也不能将该候选升级为规范。')
        context['review_scope']=dict(
            normative_item_ids=[i['id'] for i in delivery_items(p)],
            documents=[dict(kind=kind,id=doc['id']) for kind,doc in p['documents'].items()],
            instruction='逐份核对 documents 实际正文中的规范、讨论叙述、限制和当前决定，并交叉比较 MRD/PRD。'
            '当前条目、回答应用状态、scope 和本轮来源是事实依据；历史模型摘要与判断不是事实来源。'
            'findings 的 related_refs/reviewed_refs 仅使用现有条目或问题 ID，文档 ID 与章节标题写在 message 中定位。'
            '重点查已知被说成未知、已应用修订被说成待采纳、无来源的保持承诺；发现实际矛盾应 needs_changes。'
            'required_decisions 只列真正需要产品决定的业务未知，文案纠错写 findings/suggested_resolution。')
    if stage=='prd':
        context=document_context(p, include_sketch=False)
        context['user_message']=user_message
    remaining = config['context_chars'] - len(system) - len(dumps(context)) - config['max_tokens'] * 4 - 4000
    require(remaining > 0, 'BUDGET_EXHAUSTED', '关键底稿与输出预留已超过上下文预算；不能截断已选规则')
    selected, omitted, images = [], [], []
    for source in p['sources']:
        if source['excluded'] or (stage=='vision' and pending_images_only and source.get('image_mime') and source.get('vision_run_id')):
            continue
        for ex in source['excerpts']:
            cost = len(dumps(ex)) + (12000 if source['image_mime'] and stage == 'vision' else 0)
            if cost > remaining or (source['image_mime'] and stage=='vision' and len(images)>=3) or (source['image_mime'] and stage != 'vision' and not source.get('vision_run_id')):
                omitted.append(ex['id'])
                continue
            if source['image_mime'] and stage == 'vision':
                require(config['vision'] in ('documented','verified'), 'VISION_UNAVAILABLE', '所选视觉模型能力不支持或未知；图片未分析')
                data = (folder / 'sources' / source['id']).read_bytes()
                with Image.open(io.BytesIO(data)) as original:
                    preview=original.convert('RGB');preview.thumbnail((1600,1600))
                    output=io.BytesIO();preview.save(output,format='PNG')
                images.append({'type':'image_url', 'image_url': {'url':'data:image/png;base64,' + base64.b64encode(output.getvalue()).decode()}})
            selected.append(ex)
            remaining -= cost
    if stage == 'vision':
        require(images, 'VISION_UNAVAILABLE', '没有实际图片可发送')
    context['excerpts'] = selected
    context['omitted_excerpt_ids'] = omitted
    if stage=='vision':context['image_transport']='每批最多3张，最长边1600像素；原图保留在本机。看不清须列unreadable，不猜字段。'
    content = [{'type':'text','text':dumps(context)}] + images
    return [{'role':'system','content':system},{'role':'user','content':content}], selected, omitted


def input_metrics(messages):
    """Serialized characters/UTF-8 bytes only; never pretend these are token usage."""
    system,raw=messages[0]['content'].split('可信任务头：',1)
    header=json.loads(raw);ctx=json.loads(messages[1]['content'][0]['text'])
    parts=dict(system_prompts=system,schema=header['schema'],reference_profile=header.get('content_profile',{}),
        brief={k:v for k,v in ctx.items() if k not in ('excerpts','recent_messages')},
        history=ctx.get('recent_messages',[]),raw_materials=ctx.get('excerpts',[]),repair_context=messages[2:])
    return {k:dict(characters=len(v if isinstance(v,str) else dumps(v)),utf8_bytes=len((v if isinstance(v,str) else dumps(v)).encode('utf-8'))) for k,v in parts.items()}
