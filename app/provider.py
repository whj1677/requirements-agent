import asyncio
import base64
import json
import time
from urllib.parse import urlsplit
import httpx
from .core import KIT, Problem, dumps, now, read_json, require
from .contracts import SCHEMA, WIRE, profile
from .config import ProjectEnvironment, usable_key
from .prd import PLAN_SCHEMA, plan_contract, context as document_context

DEFAULT = dict(name='DeepSeek 官方', base_url='https://api.deepseek.com', model='deepseek-flash',
               key_env='RA_DEEPSEEK_API_KEY', vision='documented', json_mode=True, timeout=120,
               max_calls=8, max_tokens=6000, context_chars=180000, action_seconds=300,
               thinking_disabled=True, documented_at='2026-09-23', local_allowed=False, proxy='', input_price=None, output_price=None)
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
        key = self.key(config)
        require(key, 'CONFIG_MISSING', '尚未配置此接收端的 API Key；未发送材料')
        body = dict(model=config['model'], messages=messages, max_tokens=config['max_tokens'], stream=False)
        if config['json_mode']:
            body['response_format'] = {'type': 'json_object'}
        if config.get('thinking_disabled') and origin(config) == 'https://api.deepseek.com':
            body['thinking'] = {'type': 'disabled'}
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
        # Current requests need editable scope suggestions. Historical envelopes remain readable.
        schema=json.loads(dumps(SCHEMA))
        schema['required']=SCHEMA['required']+['product_context_proposal']
        schema['properties']['product_context_proposal']['required']=list(schema['properties']['product_context_proposal']['properties'])
        return schema
    if stage!='ui':return SCHEMA
    # The provider cannot load local $ref files. Inline the actual UI dependencies,
    # preserving the validator's schema and its no-draft-mutation rule.
    def expand(value):
        if isinstance(value,list):return [expand(x) for x in value]
        if not isinstance(value,dict):return value
        if '$ref' in value:
            ref=value['$ref']
            return expand(WIRE if ref=='wireframe.schema.json' else SCHEMA['$defs'][ref.rsplit('/',1)[1]])
        return {k:expand(v) for k,v in value.items()}
    props={**SCHEMA['properties'],'stage':{'const':'ui'},'proposals':{'const':[]},'questions':{'const':[]},'result':SCHEMA['$defs']['uiResult']}
    return expand(dict(type='object',properties=props,required=SCHEMA['required'],additionalProperties=False))


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
        from .product_flow import missing
        context['stage_focus']=dict(content_gaps=missing(p,3),
            deferred_questions=[dict(id=q['id'],reason=q['out_of_scope_reason']) for q in p['questions'] if q.get('out_of_scope_reason')],
            instruction='优先用现有材料整理本步实际缺项，提出保留原编号的修订候选；已知规则通过 behavior 和 related_refs 关联需求，不要求用户重复回答。只有材料确实没有答案才提问。缺少结构关联不等于缺少业务决定。移出范围的问题仍未知，不再次当成本期前提。')
        context['recent_messages']=[{k:m.get(k) for k in ('role','stage','text','created')} for m in p['messages'][-8:]]
        context['context_notice']='当前条目、问题、范围理由和实际材料优先；历史摘要可能过期，旧结构化输出不重复发送。'
    if stage=='ui':
        context['document_status']={kind:{k:doc.get(k) for k in ('id','draft_revision','brief_hash')} for kind,doc in context.pop('documents').items() if doc}
        context['recent_messages']=[{k:m.get(k) for k in ('role','stage','text','created')} for m in p['messages'][-8:]]
        context['context_notice']='本次生成页面：当前条目、问答、方案、活动原型及材料完整保留；文档仅提供版本信息，历史结构化输出不重复发送。历史摘要可能过期，以当前快照及材料为准。'
    active_sources={s['id'] for s in p['sources'] if not s['excluded']}
    context['vision_observations']=[m['response'] for m in p['messages'] if m.get('stage')=='vision'
        and m.get('response') and all(r['source_id'] in active_sources for r in m['response']['used_source_refs'])]
    context['source_status'] = [{k:s.get(k) for k in ('id','title','parse_status','failure_reason','excluded','purpose')} for s in p['sources']]
    if stage=='prd':
        context=document_context(p, include_sketch=False)
        context['user_message']=user_message
    remaining = config['context_chars'] - len(system) - len(dumps(context)) - config['max_tokens'] * 4 - 4000
    require(remaining > 0, 'BUDGET_EXHAUSTED', '关键底稿与输出预留已超过上下文预算；不能截断已选规则')
    selected, omitted, images = [], [], []
    for source in p['sources']:
        if source['excluded'] or (stage=='vision' and pending_images_only and source.get('image_mime') and source['parse_status']=='read'):
            continue
        for ex in source['excerpts']:
            cost = len(dumps(ex)) + (12000 if source['image_mime'] and stage == 'vision' else 0)
            if cost > remaining or (source['image_mime'] and stage != 'vision' and not source.get('vision_run_id')):
                omitted.append(ex['id'])
                continue
            if source['image_mime'] and stage == 'vision':
                require(config['vision'] in ('documented','verified'), 'VISION_UNAVAILABLE', '所选视觉模型能力不支持或未知；图片未分析')
                data = (folder / 'sources' / source['id']).read_bytes()
                images.append({'type':'image_url', 'image_url': {'url':'data:' + source['image_mime'] + ';base64,' + base64.b64encode(data).decode()}})
            selected.append(ex)
            remaining -= cost
    if stage == 'vision':
        require(images, 'VISION_UNAVAILABLE', '没有实际图片可发送')
    context['excerpts'] = selected
    context['omitted_excerpt_ids'] = omitted
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
