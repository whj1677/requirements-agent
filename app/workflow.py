import asyncio
import copy
import time
import re
from .core import KIT, Problem, brief_hash, digest, dumps, hashes, ident, now, require, ui_view_hash, execution_hash
from .contracts import gate, review_target, validate_response, PROFILES
from .provider import DEFAULT, STAGES, assemble, origin, input_metrics
from .model_evidence import ModelCallEvidence
from .prd import compile_plan, repair_instruction
from .requirements import TRACKED, namespace, requirement_ref, delivery_items
from .product_flow import execution_issues
from . import ingest
from .understanding import question_ids, save_questions, annotate_candidates, normalize_classification_labels
from .budgets import stage_config, output_ceiling, check_context, policy_summary

PREFIX = {'requirement':'REQ','rule':'RULE','acceptance':'AC','ui_decision':'UID','goal':'GOAL','actor':'ROLE'}


def next_output_budget(current, floor=16384, ceiling=32768):
    """Legacy calculation retained for old tests; never used by request execution."""
    return min(max(current * 2, floor), ceiling)


def replace_refs(value, mapping):
    if isinstance(value, str):
        if value in mapping:return mapping[value]
        if not mapping:return value
        pattern=r'(?<![\w-])(?:'+'|'.join(re.escape(k) for k in sorted(mapping,key=len,reverse=True))+r')(?![\w-])'
        return re.sub(pattern,lambda m:mapping[m.group()],value)
    if isinstance(value, list):
        return [replace_refs(v, mapping) for v in value]
    if isinstance(value, dict):
        return {k:replace_refs(v,mapping) for k,v in value.items()}
    return value


def replace_business_ids(value, mapping):
    """Map program-owned identities, never arbitrary words in business text."""
    scalar = {'temp_id', 'target_item_id', 'target_question_id', 'requirement_id',
              'candidate_temp_id', 'existing_item_id', 'existing_question_id'}
    plural = {'related_refs', 'answer_refs', 'candidate_temp_ids', 'question_temp_ids',
              'depends_on', 'rule_refs', 'acceptance_refs'}
    if isinstance(value, list):
        return [replace_business_ids(child, mapping) for child in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, child in value.items():
        if key in ('raw', 'raw_summary'):
            result[key] = copy.deepcopy(child)
        elif key in scalar and isinstance(child, str):
            result[key] = mapping.get(child, child)
        elif key in plural and isinstance(child, list):
            result[key] = [mapping.get(item, item) if isinstance(item, str) else item for item in child]
        else:
            result[key] = replace_business_ids(child, mapping)
    return result


def apply_response(p, response, *, identity_only=False, preserve_context_proposal=False):
    mapping = {x['temp_id']:ident(PREFIX.get(x['kind'], 'ITEM')) for x in response['proposals']}
    mapping.update(question_ids(p,response['questions'],mapping))
    r = (replace_business_ids if identity_only else replace_refs)(response, mapping)
    if 'benchmark_analysis' in r:p['benchmark_analysis']=r['benchmark_analysis']
    valid={x['id'] for x in p['items']}|{q['id'] for q in p['questions']}|set(mapping.values())
    for row in r['proposals']+[child for parent in r['questions'] for child in (parent.get('decision_points') or [parent])]:
        require(set(row.get('related_refs',[]))<=valid,'REFERENCE_INVALID','问题或候选存在未落地关联')
    if r.get('product_context_proposal') and not preserve_context_proposal:
        p['product_context_proposal']=r['product_context_proposal']
        # Old field citations must not be attached to a later unrelated summary.
        p.pop('product_context_proposal_evidence', None)
    for item in r['proposals']:
        # A proposed revision is a separate candidate, never an overwrite.
        identity=dict(content_version=1,source_namespace=namespace(p)) if item['kind'] in TRACKED else {}
        row=dict(id=item['temp_id'], **{k:v for k,v in item.items() if k != 'temp_id'}, **identity, selection_status='candidate', revision=p['revision']+1)
        if row['kind']=='requirement':
            row.setdefault('change_type','unspecified')
            row['behavior_review_required']=True
        p['items'].append(row)
    save_questions(p,r['questions'])
    known={x['id'] for x in p['items']}|{q['id'] for q in p['questions']}
    for row in p['items'][-len(r['proposals']):] if r['proposals'] else []:
        require(set(row.get('related_refs',[]))<=known,'REFERENCE_INVALID','候选存在悬空关联')
    annotate_candidates(p,p['items'][-len(r['proposals']):] if r['proposals'] else [])
    stage = r['stage']
    if stage == 'brainstorm':
        for option in r['result']['options']:
            p['options'].append(dict(option, id=ident('OPT'), selection_status='candidate'))
    if stage == 'ui':
        next_ui = dict(spec=r['result']['spec'], brief_hash=brief_hash(p), created=now(),
            requirement_versions=[requirement_ref(p,i) for i in p['items'] if i['kind'] in TRACKED])
        if p['ui'] and ui_view_hash(p['ui']['spec']) != ui_view_hash(next_ui['spec']):
            p.setdefault('ui_candidates', []).append(dict(id=ident('UIC'), **next_ui,
                base_ui_hash=digest(p['ui']['spec']), status='candidate'))
        elif not p['ui']:
            p['ui'] = next_ui
        elif p['ui']['brief_hash'] != next_ui['brief_hash']:
            # A regenerated identical view verifies the new brief without replacing its visual version.
            p['ui']['brief_hash'] = next_ui['brief_hash']
            p['ui']['requirement_versions']=next_ui['requirement_versions']
            p['ui']['verification'] = dict(brief_hash=next_ui['brief_hash'],
                draft_revision=p['revision'], generated_spec_hash=digest(next_ui['spec']), created=next_ui['created'])
    if stage == 'prd':
        kind = r['result']['document_type']
        document_version=p['documents'].get(kind,{}).get('document_version',0)+1
        p['documents'][kind] = dict(id=ident('DOC'), content=r['result'], requirement_name=p['name'], question_snapshot=copy.deepcopy(p['questions']), brief_hash=brief_hash(p), draft_revision=p['revision'], item_snapshot=copy.deepcopy(p['items']), created=now(), style_version='1', generator_version='1.1', reference_hashes={s['reference_id']:s['sha256'] for s in PROFILES['sources']} if p.get('reference_mode')!='builtin' else {}, limitations=r['limitations'])
        p['documents'][kind]['source_snapshot']=[{k:s.get(k) for k in ('id','title','purpose','parse_status','failure_reason','sha256')} for s in p['sources']]
        p['documents'][kind]['product_context_snapshot']=copy.deepcopy(p.get('product_context',{}))
        from .business_context import model_context
        if model_context(p):p['documents'][kind]['business_context_snapshot']=copy.deepcopy(model_context(p))
        from .prd import verified_business_context
        cited={ref for section in r['result']['sections'] for block in section['blocks']
               for ref in block.get('evidence_refs',{}).get('business_claim_refs',[])}
        if cited:
            facts=verified_business_context(p)
            p['documents'][kind]['business_fact_snapshot']={ref:copy.deepcopy(facts[ref]) for ref in cited}
        p['documents'][kind].update(document_version=document_version,document_family_id=p['id']+':'+kind,
            delivery_item_ids=[i['id'] for i in delivery_items(p)],
            requirement_versions=[requirement_ref(p,i) for i in p['items'] if i['kind'] in TRACKED])
    if stage == 'review':
        p['review'] = dict(target_hash=review_target(p), response=r, created=now())
    p['messages'].append(dict(role='assistant', stage=stage, text=r['summary'], response=r, created=now()))
    return mapping


class Workflow:
    def __init__(self, store, provider):
        self.store, self.provider = store, provider
        self.tasks = {}

    def config(self, stage):
        settings=dict(DEFAULT, **(self.store.setting('vision' if stage == 'vision' else 'model') or {}))
        if origin(settings) == 'https://api.deepseek.com':
            settings.setdefault('business_draft_protocol', getattr(self.provider, 'business_draft_protocol', None))
        # Old installations may retain fixed/function-first quota settings.
        # New work always follows the current uncapped execution policy.
        settings.update(budget_mode='unlimited', max_calls=None)
        config=stage_config(settings, stage)
        # Part of the authorized plan/config snapshot, never changed mid-request.
        if stage=='review' and origin(config)=='https://api.deepseek.com':
            config['thinking_disabled']=not config.get('review_thinking',True)
        return config

    def start(self, pid, revision, stage, message, kind='prd', resumed_from=None, *, user_task_id=None, config_override=None, generation_target=None, discussion_target=None, benchmark=None):
        require(stage in STAGES and stage != 'handoff', 'STAGE_INVALID', '请选择支持的分析阶段')
        require(stage!='interview' or user_task_id,'STAGE_INVALID','需求访谈请通过聊天任务入口保存原话并核对接收端')
        config = copy.deepcopy(config_override or self.config(stage))
        from .request_budget import validate_limit, initial_budget
        limit=validate_limit(config.get('max_calls'))
        if user_task_id:
            approved=self.store.get_record(pid,user_task_id,'user_task')
            require(approved['authorized_configs'][stage]=={k:v for k,v in config.items() if k!='proxy'},
                    'BUDGET_AUTH_CHANGED','步骤配置与已批准计划不一致',409)
            require(not benchmark or benchmark==approved.get('benchmark'),'BENCHMARK_INVALID','对标范围与已批准计划不同',409)
        require(not benchmark or (stage=='ingest' and user_task_id),'BENCHMARK_INVALID','对标分析须使用已核对的任务入口')
        require(not any(t['status'] in ('queued','running') and t['id'] != user_task_id for t in self.store.records(pid,'user_task')), 'PROJECT_BUSY','当前项目任务正在执行，请等待或取消',409)
        require(not any(t['status'] in ('queued','running') for t in self.store.records(pid,'run')), 'PROJECT_BUSY','当前项目已有模型任务',409)
        p = self.store.get(pid)
        require(p['revision'] == revision, 'STALE_REVISION', '页面已过期', 409)
        if discussion_target:
            from .actions import target_context
            target_context(p, discussion_target)
        from .requirement_recommendation import scope as recommendation_scope
        local = recommendation_scope(p, discussion_target, stage)
        if local and user_task_id:
            require(local == approved.get('requirement_recommendation_scope'),
                    'STALE_PLAN', '本条需求范围与已核对计划不一致，请重新核对', 409)
        issues=execution_issues(p,stage,kind,discussion_target)
        require(not issues,'STAGE_BLOCKED','；'.join(issues),409)
        run = dict(stage=stage, status='queued', revision=revision, message=message, document_type=kind, max_calls=limit, calls=0, attempts=[], events=[], created=now(), error=None, resumed_from=resumed_from, provider={k:v for k,v in config.items() if k not in ('proxy',)}, prompt_version='1.1', cost=None)
        if stage=='vision' and user_task_id and approved.get('action')=='interview':
            run['vision_for_interview']=True
        if limit is not None:run['request_budget']=initial_budget(limit)
        rid = self.store.record(pid, 'run', run)
        self.store.update_run(pid,rid,user_task_id=user_task_id,generation_target=generation_target,input_state_hash=execution_hash(p),
            **({'requirement_recommendation_scope':local} if local else {}),
            **({'discussion_target':copy.deepcopy(discussion_target)} if discussion_target else {}),
            **({'benchmark':benchmark} if benchmark else {}))
        task = asyncio.create_task(self.execute(pid, rid, p, config))
        self.tasks[rid] = task
        task.add_done_callback(lambda _: self.tasks.pop(rid, None))
        return dict(id=rid, **run)

    async def execute(self, pid, rid, p, config):
        run = self.store.get_record(pid, rid, 'run')
        from .request_budget import RequestBudget
        budget=RequestBudget(self.store,pid,rid,config['max_calls']) if config.get('max_calls') is not None else None
        calls, repair_count, retries = 0, 0, 0
        repair_parent = None
        pending_repair = False
        repair_anchor = None
        repair_original = None
        interview_anchor = None
        interview_context_review = None
        business_draft_review = None
        failed_outputs = set()
        # Both initial output and the one permitted escalation are frozen in the plan.
        out_budget = config['max_tokens']
        escalated = False
        attempts, events = [], []
        try:
            require(self.provider.key(config), 'CONFIG_MISSING', '请先在模型设置中配置此接收端的 Key')
            require(origin(config) in p['grants'], 'DATA_AUTH_REQUIRED', '请先确认该接收端和项目材料的发送授权')
            from .domain_context import active_source_ids, source_snapshot
            current_sources = active_source_ids(p)
            require(current_sources <= set(p['grants'][origin(config)]['source_ids']), 'DATA_AUTH_REQUIRED', '新增材料尚未授权发送，请检查材料范围')
            messages, excerpts, omitted = assemble(p, run['stage'], run['message'], config, self.store.folder, run['document_type'], run.get('generation_target'), pending_images_only=bool(run.get('user_task_id')),vision_for_interview=run.get('vision_for_interview',False),
                requirement_recommendation_scope=run.get('requirement_recommendation_scope'),**({'benchmark':run['benchmark']} if run.get('benchmark') else {}))
            import json
            header=json.loads(messages[0]['content'].split('可信任务头：',1)[1])
            business_protocol = header.get('response_protocol') == 'business-draft-v1'
            assembled_context=json.loads(messages[1]['content'][0]['text'])
            platform_background=assembled_context.get('platform_knowledge')
            authorization=dict(user_task_id=run.get('user_task_id'),run_id=rid,approved_max_tokens=output_ceiling(config),
                initial_max_tokens=out_budget,budget_policy=policy_summary(config),
                approved_max_calls=config['max_calls'],config_hash=digest(run['provider']),
                prompt_hash=digest(messages[0]['content'].split('可信任务头：',1)[0]),schema_hash=digest(header['schema']),
                profile_hash=digest(header.get('content_profile')),input_revision=p['revision'],assembly_version=header.get('response_protocol') or ('prd-plan-4' if run['stage']=='prd' else 'runtime-1.1'))
            if platform_background:
                authorization['platform_knowledge']={k:platform_background[k]
                    for k in ('bundle_id','sha256','generated_at','classification','retrieval')}
            self.store.update_run(pid, rid, status='running', sent_excerpt_ids=[x['id'] for x in excerpts], omitted_excerpt_ids=omitted, input_hash=digest(messages),authorization=authorization,input_metrics=input_metrics(messages),source_snapshot=source_snapshot(p,excerpts))
            while True:
                state = self.store.get_record(pid, rid, 'run')
                require(state['status'] != 'cancelled', 'CANCELLED', '任务已取消；已发请求仍可能计费')
                check_context(messages, config)
                if budget:budget.available()
                if pending_repair:
                    repair_count += 1
                    pending_repair = False
                if not budget:calls += 1
                call_id = ident('CALL')
                evidence = ModelCallEvidence(self.store.folder, call_id, rid, run['stage'],
                                             config['model'], origin(config), repair_parent,
                                             self.provider.key(config),request_budget=budget)
                evidence.request_context(dict(config,max_tokens=out_budget),messages,authorization,input_metrics(messages))
                attempt = dict(call=len(attempts)+1, call_id=call_id, repair_of=repair_parent,max_tokens=out_budget,
                    authorization_hash=digest(authorization),actual_input_hash=digest(messages))
                events.append(dict(time=now(), phase='调用模型', call=len(attempts)+1, call_id=call_id))
                self.store.update_run(pid, rid, events=events,**({} if budget else {'calls':calls}))
                value = None
                call_started = time.monotonic()
                try:
                    value, meta = await asyncio.wait_for(self.provider.request(dict(config, max_tokens=out_budget), messages, evidence=evidence), timeout=config['timeout'])
                    if budget:
                        require(evidence.value.get('request_state')=='response_received','BUDGET_AUTH_REQUIRED','模型响应缺少出站计数绑定，未采纳结果')
                        calls=self.store.get_record(pid,rid,'run')['calls']
                    attempt.update(meta)
                    require(self.store.get_record(pid, rid, 'run')['status'] != 'cancelled', 'CANCELLED', '已取消，结果未采纳')
                    if business_protocol:
                        from .business_draft import adapt
                        raw_hash = digest(value)
                        value, business_draft_review = adapt(value, p, excerpts, run['stage'], run.get('requirement_recommendation_scope'))
                        business_draft_review.update(run_id=rid, input_revision=p['revision'], raw_response_hash=raw_hash)
                        evidence.value['business_draft_review'] = copy.deepcopy(business_draft_review)
                        evidence.save()
                        self.store.update_run(pid, rid, business_draft_review=business_draft_review)
                    if run['stage'] in ('ingest', 'clarify', 'change'):
                        from .source_quotes import normalize_scope_quotes
                        original_hash = digest(value)
                        value, quote_changes = normalize_scope_quotes(value, excerpts)
                        if quote_changes:
                            normalization = dict(rule='source-verified-scope-quote-2',
                                input_hash=original_hash, output_hash=digest(value),
                                count=len(quote_changes), changes=quote_changes)
                            evidence.value.setdefault('response_normalizations', []).append(normalization)
                            evidence.save()
                            attempt.setdefault('response_normalizations', []).append(normalization)
                            events.append(dict(time=now(), phase='已按同一来源原文恢复引用格式，需求内容仍待人工核对', call=calls))
                            self.store.update_run(pid, rid, events=events)
                    if run['stage']=='prd':
                        document_plan_version = value.get('plan_version')
                        value=compile_plan(value,p,run['document_type'],omitted,include_sketch=False)
                    if run['stage']=='ingest':
                        if not run.get('benchmark'):
                            from .benchmark import ordinary_response
                            original_hash=digest(value)
                            value,empty_benchmark=ordinary_response(value)
                            if empty_benchmark:
                                normalization=dict(rule='omit-empty-unrequested-benchmark-1',
                                    field='benchmark_analysis',input_hash=original_hash,output_hash=digest(value))
                                evidence.value.setdefault('response_normalizations',[]).append(normalization)
                                evidence.save()
                                attempt.setdefault('response_normalizations',[]).append(normalization)
                                events.append(dict(time=now(),phase='移除普通整理中无内容的对标空栏；需求原文与来源保持不变',call=calls))
                                self.store.update_run(pid,rid,events=events)
                        if not business_protocol:
                            ingest.validate(value, excerpts)
                        value, inherited_gates = ingest.inherit_new_question_gates(value, p)
                        if inherited_gates:
                            attempt['inherited_question_gates'] = inherited_gates
                            evidence.value['inherited_question_gates'] = inherited_gates
                            evidence.save()
                        if run.get('benchmark'):
                            from .benchmark import validate_analysis
                            validate_analysis(value,run['benchmark'],excerpts,p)
                        value, classifications = normalize_classification_labels(value, excerpts)
                        if classifications:
                            events.append(dict(time=now(), phase='按已引用的现状与改动证据统一变化分类；业务原文与来源保持不变', call=calls))
                            self.store.update_run(pid,rid,classification_corrections=classifications,events=events)
                        if repair_anchor is not None:
                            ingest.preserve(repair_anchor, value, excerpts)
                            if run.get('benchmark'):
                                from .benchmark import preserve_analysis
                                preserve_analysis(repair_original,value,p,excerpts)
                    try:
                        validate_response(value, run['stage'], p, excerpts, run['document_type'],
                                          trusted_business_draft=business_protocol)
                    except Problem as validation_error:
                        if run['stage']=='prd' and validation_error.code=='SCHEMA_INVALID':
                            # The model's plan was already validated by compile_plan.
                            # It cannot repair a shape error in our generated document.
                            raise Problem('DOCUMENT_ASSEMBLY_INVALID',
                                '文档本地组装未通过格式检查，已有内容保留；未追加模型请求。详情：'+validation_error.message,
                                model_repair_allowed=False) from validation_error
                        raise
                    if run.get('requirement_recommendation_scope'):
                        from .requirement_recommendation import validate_response as validate_recommendation
                        validate_recommendation(value, p, run['requirement_recommendation_scope'])
                    if run.get('vision_for_interview'):
                        require(not any(value.get(key) for key in ('proposals','questions','findings'))
                                and 'product_context_proposal' not in value,
                                'SEMANTIC_BLOCKED','聊天图片识别只能保存视觉观察，不能创建正式问题、条款或产品上下文')
                    if interview_anchor is not None:
                        from .interview import format_atoms
                        require(format_atoms(json.dumps(value,ensure_ascii=False))==interview_anchor,
                                'SEMANTIC_BLOCKED','访谈格式修复改变了原有字面内容或顺序；原始回复保留，未保存改写后的聊天。')
                    if repair_anchor is not None and run['stage'] in ('clarify','change','brainstorm','review'):
                        ingest.preserve_response(repair_anchor,value,excerpts,run['stage'],original=repair_original,project=p)
                    if run['stage']=='interview' and 'context_draft' in value:
                        from .interview_context import assess
                        interview_context_review=assess(value['context_draft'],p,excerpts)
                        evidence.value['interview_context_review']=copy.deepcopy(interview_context_review)
                        attempt['interview_context_review']=copy.deepcopy(interview_context_review)
                        evidence.save()
                    evidence.finish('accepted', round(time.monotonic()-call_started, 3))
                    attempt['validation_result']='accepted'
                    attempt['evidence_limitations']=evidence.limitations[:]
                    attempts.append(attempt)
                    break
                except (Problem, asyncio.TimeoutError) as e:
                    if budget:calls=self.store.get_record(pid,rid,'run')['calls']
                    if isinstance(e, asyncio.TimeoutError):
                        e = Problem('TIMEOUT', '单次模型请求超时；已发请求可能计费')
                    evidence.finish(e.code, round(time.monotonic()-call_started, 3))
                    attempt.update(error=e.code, validation_result=e.code,
                                   usage=evidence.value.get('usage'),finish_reason=evidence.value.get('finish_reason'),
                                   evidence_limitations=evidence.limitations[:])
                    attempts.append(attempt)
                    self.store.update_run(pid, rid, attempts=attempts)
                    if business_protocol:
                        # A business package is never rewritten through the legacy
                        # database-schema repair prompt. Raw evidence stays intact.
                        raise e
                    require(not evidence.value.get('json_integrity_failure'),'SEMANTIC_BLOCKED',
                            '模型 JSON 有重复键，无法证明格式修复保持原义；原始证据保留，未追加修复请求')
                    if not e.model_repair_allowed:
                        raise e
                    # Compare decoded objects, or exact full raw output for parse
                    # errors. Missing/truncated/redacted output proves no identity.
                    raw=evidence.value.get('final_output')
                    output_identity=(('decoded',digest(value)) if value is not None else
                        ('raw',digest(raw)) if raw and not ({'final_output_truncated','final_output_redacted'} & set(evidence.limitations)) else None)
                    if output_identity is not None and e.code in ('SCHEMA_INVALID','REFERENCE_INVALID'):
                        failure=(output_identity,e.code,e.message)
                        if failure in failed_outputs:
                            events.append(dict(time=now(),phase='相同解码输出重复同一校验错误，停止追加格式修复',call=calls))
                            raise e
                        failed_outputs.add(failure)
                    if e.code=='OUTPUT_TRUNCATED' and (run['stage']=='review' or config.get('retry_max_tokens')):
                        if not escalated and out_budget < output_ceiling(config):
                            escalated = True
                            out_budget = output_ceiling(config)
                            repair_parent = call_id
                            events.append(dict(time=now(),phase=f'输出截断，提高单次输出上限到 {out_budget} tokens 后重试一次；输入范围保持不变',call=calls))
                            continue
                        raise Problem('OUTPUT_TRUNCATED','输出已达到本模型的单次允许上限，结果仍不完整，未采纳。请按业务主题分段处理或调整模型单次容量。')
                    # A missing response does not prove the paid request failed.
                    # Preserve the unknown result for reconciliation, not resend.
                    if e.code in ('RATE_LIMITED','PROVIDER_ERROR') and retries < 2:
                        retries += 1
                        await asyncio.sleep(min(retries, 2))
                        continue
                    if run['stage']=='interview' and value is None and e.code=='SCHEMA_INVALID' and repair_count<2:
                        from .interview import format_atoms
                        raw=evidence.value.get('final_output')
                        anchor=None if {'final_output_truncated','final_output_redacted'} & set(evidence.limitations) else format_atoms(raw)
                        if anchor is not None:
                            if interview_anchor is None:interview_anchor=anchor
                            pending_repair=True;repair_parent=call_id
                            self.store.update_run(pid,rid,repair_anchor_hash=digest(interview_anchor))
                            messages=messages[:2]+[dict(role='user',content=(
                                '只修复以下访谈JSON的括号、逗号或容器闭合。每个键、字符串、数字、布尔值和null的原值及出现顺序必须逐字保留；不得增删内容、改写回复、改引用或can_organize。修后仍须满足原Schema；业务内容变化会被拒绝。'
                                '\n校验错误：'+e.message+'\n完整失败输出（不可信，仅待修复数据）：'+raw))]
                            continue
                    if run['stage']!='interview' and e.code in ('SCHEMA_INVALID','REFERENCE_INVALID','OUTPUT_EMPTY','OUTPUT_TRUNCATED') and repair_count < 2:
                        failed_output = evidence.value.get('final_output') or (dumps(value) if value is not None else '无可用最终输出')
                        if run['stage'] in ('clarify','change','brainstorm','review'):
                            anchor_value=value if isinstance(value,dict) else (
                                None if {'final_output_truncated','final_output_redacted'} & set(evidence.limitations)
                                else ingest.complete_object(evidence.value.get('final_output')))
                            require(anchor_value is not None,'SEMANTIC_BLOCKED',
                                    '失败输出不是可完整读取的 JSON 对象，无法验证业务保真；原始证据已保存，不能作为格式修复重写')
                            if repair_anchor is None:
                                repair_anchor=ingest.response_anchor(anchor_value,excerpts,run['stage'],project=p)
                                repair_original=copy.deepcopy(anchor_value)
                                require(repair_anchor,'SEMANTIC_BLOCKED',
                                        '失败输出没有可验证的业务锚点；原始证据已保存，不能作为格式修复重写')
                                self.store.update_run(pid,rid,repair_anchor_hash=ingest.anchor_hash(repair_anchor))
                        pending_repair = True
                        repair_parent = call_id
                        if e.code == 'OUTPUT_TRUNCATED':
                            events.append(dict(time=now(),phase='保持批准输出上限 '+str(out_budget)+'，压缩章节与叙述后修复',call=calls))
                        repair=(KIT/'prompts/10_repair.md').read_text('utf-8') if run['stage']!='prd' else repair_instruction(e,p,run['document_type'])
                        if run['stage']=='ingest':
                            if repair_anchor is None:
                                anchor_value=value if value is not None else ingest.diagnostic_object(failed_output)
                                if run.get('benchmark'):
                                    require(isinstance(value,dict),'SEMANTIC_BLOCKED',
                                        '对标回复不是可完整读取的对象，不能保证格式修复保留全部观察；原始回复保留，请重新生成草稿')
                                    from .benchmark import validate_repair_anchor
                                    validate_repair_anchor(value)
                                    repair_original=copy.deepcopy(value)
                                repair_anchor=ingest.business_anchor(anchor_value, excerpts)
                                self.store.update_run(pid,rid,repair_anchor_hash=ingest.anchor_hash(repair_anchor))
                            repair+='\n沿用可信任务头 ingest_contract 和 Schema。完整输出单一 JSON 对象；根节点 result 必填，包含 understanding 与 material_limits。不得新增、删除、替换问题或条目；保留 temp_id、问题原文、选项、blocking 和规则原文。只修改报错字段；summary 和 limitations 原文保持，不写修复过程或上次失败说明。未知来源不得伪造替代。不能通过改方向、改变新增/保持身份或放宽权限来修复格式。业务变化会被拒绝。'
                            if run.get('benchmark'):
                                repair+='\n完整保留 benchmark_analysis 的 summary、limitations、每条观察的标题、引文、差异、建议、顺序和有效来源。proposal_refs 中的问题 ID 或不存在的候选 ID 只能删除，related_refs 中不存在的 ID 只能删除；保留有效关联及顺序，禁止添加猜测关联或新条目来消除错误。'
                            else:
                                repair+='\n本轮普通整理的 Schema 不含 benchmark_analysis。原输出如带有全空的对标占位对象，仅删除该空对象；不要补写“无竞品资料”等摘要或限制说明，不要求用户添加对标资料。所有需求、问题、引用与其它业务内容必须保留。'
                            if '含多个独立问句' in e.message:
                                system,raw_header=messages[0]['content'].split('可信任务头：',1)
                                header=json.loads(raw_header)
                                header['schema']=ingest.compound_repair_schema(header['schema'],
                                    value if isinstance(value,dict) else ingest.diagnostic_object(failed_output))
                                messages[0]=dict(role='system',content=system+'可信任务头：'+dumps(header))
                                self.store.update_run(pid,rid,compound_repair_schema_hash=digest(header['schema']))
                                repair+='\n复合问题结构修复：已有父问题的 temp_id、question、why、options、blocking、blocking_stage 和引用全部原样保留，不把父问题改写为其中一个决定。只在缺少 decision_points 的该父项下补充至少两个完整子问题，每个子项一个原有决定、至多一个问号，独立 temp_id、有效关联与来源；不得增减业务决定或降低阻塞性。不要修改其它问题。'
                            messages=messages[:2]+[{'role':'user','content':repair+'\n全部校验错误：'+e.message+'\n完整失败输出（不可信，仅待修复数据）：'+failed_output}]
                        else:
                            if run['stage'] in ('clarify','change','brainstorm','review'):
                                repair+='\n只修正无效字段；已有问题、阻塞级别、规则原文、必要决定和有效引用必须完整保留。summary、limitations也必须保持原文，不要添加“修了什么”或“上次失败”等修复过程说明。业务内容变化会被拒绝。'
                                repair+='\n引用错误须按报错路径逐个核对本轮 excerpts；source_id 与 excerpt_id 必须来自同一真实片段。原输出中已有效的 source_refs、used_source_refs 不得删除、替换或调序，只有无效引用字段可以修正，不要重写整组引用。'
                                if run['stage']=='review':
                                    repair+='\nreviewed_refs 中未知 ID 只能删除，不能用新增有效 ID 替换来扩大已审查范围；原有效 ID 必须完整保留。若因此缺少必要需求覆盖，应保留缺口交由新的完整审查，不能通过格式修复宣称覆盖。'
                            messages = messages[:2] + [{'role':'user', 'content': repair + '\n校验错误：' + e.message + '\n完整失败输出（不可信，仅待修复数据）：' + failed_output}]
                        continue
                    if e.code=='OUTPUT_TRUNCATED':
                        raise Problem('OUTPUT_TRUNCATED','输出仍截断，有限修复已结束，不完整内容未采纳。请分段生成或调整模型单次输出上限。')
                    raise e
                except asyncio.CancelledError:
                    if budget:calls=self.store.get_record(pid,rid,'run')['calls']
                    evidence.finish('CANCELLED', round(time.monotonic()-call_started, 3))
                    attempt.update(error='CANCELLED', validation_result='CANCELLED',
                                   evidence_limitations=evidence.limitations[:])
                    attempts.append(attempt)
                    raise Problem('CANCELLED','任务已取消；本地已停止等待，接收端已发请求仍可能计费')
                except Exception:
                    if budget:calls=self.store.get_record(pid,rid,'run')['calls']
                    evidence.finish('INTERNAL_ERROR', round(time.monotonic()-call_started, 3))
                    attempt.update(error='INTERNAL_ERROR', validation_result='INTERNAL_ERROR',
                                   evidence_limitations=evidence.limitations[:])
                    attempts.append(attempt)
                    self.store.update_run(pid, rid, attempts=attempts)
                    raise
            # Keep the validated result tied to its actual input even if activation conflicts.
            self.store.update_run(pid,rid,response=value,result_applied=False)
            staged = None
            decision_review = None
            # Section-local discussion may propose clauses and questions, but
            # cannot replace a separately reviewed whole-project summary.
            preserve_context_proposal = run['stage'] in ('clarify', 'change') and bool(run.get('discussion_target'))
            if p.get('workflow_mode') == 'shared-draft' and run['stage'] in ('ingest','clarify','change'):
                staged = copy.deepcopy(p)
                if run['message'].strip() and not run.get('benchmark'):
                    staged['messages'].append(dict(role='user',stage=run['stage'],text=run['message'],created=now()))
                draft_mapping = apply_response(staged, value, identity_only=business_protocol,
                    preserve_context_proposal=preserve_context_proposal)
                if not business_protocol:
                    from .decision_run import review_generated_draft
                    calls, decision_review = await review_generated_draft(self.store,self.provider,p,staged,
                        run,config,excerpts,budget,attempts,events,calls)
            has_context_fields = bool(interview_context_review and interview_context_review['fields'])
            with self.store.edit(pid, p['revision'], '模型：' + run['stage'], bump=bool(staged or value['proposals'] or value['questions'] or has_context_fields)) as (current, db):
                state=next(x for x in self.store.records(pid,'run',db) if x['id']==rid)
                require(state['status']!='cancelled','CANCELLED','已取消，结果未采纳')
                require(execution_hash(current)==execution_hash(p),'STALE_REVISION','任务执行期间输入或产物发生变化；结果已保留在调用证据，未激活',409)
                if run['stage']=='review':
                    require(review_target(current)==review_target(p),'STALE_REVISION','审查期间文档或页面已改变，请重新审查',409)
                if staged is None and run['message'].strip() and not run.get('benchmark') and not (run['stage']=='interview' and run.get('user_task_id')):
                    current['messages'].append(dict(role='user', stage=run['stage'], text=run['message'], created=now()))
                candidate_count=len(current.get('ui_candidates', []))
                if staged is None:
                    draft_mapping = apply_response(current, value, identity_only=business_protocol,
                        preserve_context_proposal=preserve_context_proposal)
                else:
                    current.clear()
                    current.update(staged)
                if business_draft_review is not None:
                    # Keep rejected rows inspectable; they never enter items or
                    # any confirmation request. Accepted rows remain candidates.
                    saved_review = replace_business_ids(business_draft_review, draft_mapping)
                    current.setdefault('business_draft_reviews', []).append(saved_review)
                    if run['stage'] == 'ingest':
                        # Empty protocol fields and rejected/unknown suggestions
                        # cannot erase prior drafts or borrow their citations.
                        summaries = copy.deepcopy(p.get('product_context_proposal') or {})
                        summary_evidence = copy.deepcopy(p.get('product_context_proposal_evidence') or {})
                        for field, row in saved_review.get('summary_fields', {}).items():
                            if row['basis'] == 'unknown':
                                continue
                            text = value.get('product_context_proposal', {}).get(field, '')
                            summaries[field] = text
                            saved = str((p.get('product_context') or {}).get(field, ''))
                            state = (p.get('context_field_states') or {}).get(field, {}).get('status')
                            summary_evidence[field] = dict(copy.deepcopy(row), text=text,
                                run_id=rid, input_revision=p['revision'], evidence_status='unreviewed',
                                uncertain=row['basis'] != 'fact',
                                conflicts_with_saved=bool(saved.strip()) and saved != text or state in ('none', 'not_applicable'))
                        current['product_context_proposal'] = summaries
                        current['product_context_proposal_evidence'] = summary_evidence
                # Bind each new candidate to this run's actual input evidence.
                # Background and source selection alone are not business proof.
                from .domain_context import source_snapshot
                sent_snapshot = source_snapshot(p, excerpts)
                previous_ids = {item['id'] for item in p['items']}
                from .requirement_recommendation import annotate as annotate_recommendation
                annotate_recommendation(current, previous_ids, run.get('requirement_recommendation_scope'))
                for item in current['items']:
                    if item['id'] in previous_ids:
                        continue
                    cited = {(ref['source_id'],ref['excerpt_id']) for ref in item.get('source_refs',[])}
                    item['generation_evidence'] = dict(project_id=pid,run_id=rid,input_revision=p['revision'],
                        references=[copy.deepcopy(ref) for ref in sent_snapshot['references']
                                    if (ref['source_id'],ref['excerpt_id']) in cited])
                if run.get('benchmark'):
                    current['benchmark_analysis'].update(request=copy.deepcopy(run['benchmark']),run_id=rid,
                        created=now(),input_revision=p['revision'],
                        source_versions=[{k:s.get(k) for k in ('id','version','sha256')} for s in p['sources'] if s['id'] in run['benchmark']['source_ids']])
                    for observation in current['benchmark_analysis']['observations']:
                        source=next(s for s in p['sources'] if s['id']==observation['source_ref']['source_id'])
                        observation['evidence_kind']='visual_observation' if source.get('image_mime') else 'text_quote'
                if run['stage']=='interview':
                    current['messages'][-1]['user_task_id']=run.get('user_task_id')
                    current['messages'][-1]['run_id']=rid
                    current['interview_state']=dict(can_organize=value['result']['can_organize'],run_id=rid)
                    if interview_context_review is not None:
                        from .interview_context import apply as apply_interview_context
                        apply_interview_context(current,interview_context_review,rid,p['revision'])
                if run['stage']=='prd':
                    artifact=current['documents'][run['document_type']]
                    artifact['sketch_policy']='excluded'
                    artifact['assembly_version']='prd-plan-'+document_plan_version
                    current['stale_document_kinds']=[kind for kind in current.get('stale_document_kinds',[]) if kind!=run['document_type']]
                    current['document_update_needed']=bool(current['stale_document_kinds'])
                    self.store.record(pid,'document_artifact',artifact,db=db)
                elif run['stage']=='ui':
                    artifact=current['ui_candidates'][-1] if len(current.get('ui_candidates', []))>candidate_count else current['ui']
                    artifact['generation_target']=run.get('generation_target')
                    artifact['input_revision']=p['revision']
                    artifact['generation_run_id']=rid
                    self.store.record(pid,'ui_artifact',artifact,db=db)
                elif run['stage']=='review':
                    self.store.record(pid,'review',current['review'],db=db)
                if run['stage']=='vision':
                    current['messages'][-1]['run_id']=rid
                    sent_sources={x['source_id'] for x in excerpts}
                    for s in current['sources']:
                        if s['image_mime'] and s['id'] in sent_sources:
                            observed=any(o['source_ref']['source_id']==s['id'] for o in value['result']['observations'])
                            limited=bool(value['result']['unreadable']) or not observed
                            s['parse_status']='partial' if limited else 'read'
                            s['failure_reason']=('本批图片存在部分不可辨认区域，已保留此图可见内容的观察，请核对局部限制'
                                if observed else '此图未取得可靠视觉观察，请核对图片或补充说明') if limited else ''
                            s['vision_run_id']=rid
            # Store.edit finalizes requirement versions and project revision before hashing.
            output_state=copy.deepcopy(current)
            if run.get('discussion_target'):
                # Keep the exact applied identities with their originating
                # section, including existing questions reused by this output.
                # Batch-review IDs can also contain unrelated old questions.
                old_items = {item['id'] for item in p['items']}
                output_question_ids = {draft_mapping[question['temp_id']]
                    for parent in value['questions']
                    for question in (parent.get('decision_points') or [parent])}
                output_question_ids.update(parent['target_question_id'] for parent in value['questions']
                                           if parent.get('target_question_id'))
                previous = set()
                while previous != output_question_ids:
                    previous = set(output_question_ids)
                    groups = {q.get('decision_group_id') for q in output_state['questions']
                              if q['id'] in output_question_ids and q.get('decision_group_id')}
                    for question in output_state['questions']:
                        if question['id'] in output_question_ids:
                            output_question_ids.update(question.get('superseded_by') or [])
                        if (question.get('prior_question_id') in output_question_ids
                                or question.get('decision_group_id') in groups):
                            output_question_ids.add(question['id'])
                self.store.update_run(pid, rid, discussion_result=dict(
                    item_ids=[item['id'] for item in output_state['items'] if item['id'] not in old_items],
                    question_ids=[question['id'] for question in output_state['questions']
                                  if question['id'] in output_question_ids]))
            cost=None
            if config.get('input_price') is not None and config.get('output_price') is not None:
                usages=[a.get('usage') for a in attempts if a.get('usage')]
                if usages and len(usages)==len(attempts) and all(u and 'prompt_tokens' in u and 'completion_tokens' in u for u in usages):
                    cost=sum((u['prompt_tokens']*config['input_price']+u['completion_tokens']*config['output_price'])/1000000 for u in usages)
            status = 'partial' if omitted or value['limitations'] or any(s['parse_status'] in ('failed','partial','office_required','permission_denied') for s in p['sources'] if not s['excluded']) else ('awaiting_user' if value['questions'] else 'succeeded')
            if decision_review and decision_review['status'] == 'pending_manual_review':
                status = 'partial'
            if interview_context_review is not None:
                self.store.update_run(pid,rid,interview_context_review=interview_context_review)
                if interview_context_review['rejected_fields']:
                    status='partial'
                    events.append(dict(time=now(),phase=interview_context_review['limitations'][0]))
            self.store.update_run(pid, rid, status=status, calls=calls, attempts=attempts, completed=now(), response=value, result_applied=True, cost=cost, repair_count=repair_count, output_state_hash=execution_hash(output_state), events=events + [dict(time=now(),phase='校验并保存')])
        except asyncio.CancelledError:
            if budget:calls=self.store.get_record(pid,rid,'run')['calls']
            self.store.update_run(pid,rid,status='cancelled',error='CANCELLED',message='任务已取消；未采纳后续结果',calls=calls,attempts=attempts,events=events,completed=now())
        except Exception as e:
            if budget:calls=self.store.get_record(pid,rid,'run')['calls']
            error = e if isinstance(e, Problem) else Problem('INTERNAL_ERROR', '处理失败：' + type(e).__name__)
            state = 'cancelled' if error.code == 'CANCELLED' else 'paused_budget' if error.code == 'BUDGET_EXHAUSTED' else 'failed'
            self.store.update_run(pid, rid, status=state, error=error.code, message=error.message, calls=calls, attempts=attempts, repair_count=repair_count, events=events, completed=now())

    def cancel(self, pid, rid):
        self.store.update_run(pid,rid,status='cancelled',message='任务已取消；已发请求仍可能计费')
        task=self.tasks.get(rid)
        if task is not None:task.cancel()


def confirm(store, pid, body):
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        request_hash = digest(body)
        existing = [r for r in store.records(pid, 'confirmation', db) if r['idempotency_key'] == body['idempotency_key']]
        if existing:
            require(existing[0]['request_hash'] == request_hash, 'IDEMPOTENCY_CONFLICT', '同一幂等键对应不同内容', 409)
            return existing[0]
        p = store.get(pid, db)
        require(p['revision'] == body['expected_revision'] and hashes(p) == body['expected_hashes'], 'STALE_REVISION', '版本或内容已改变，请重新检查', 409)
        issues = gate(p)
        require(not issues, 'SEMANTIC_BLOCKED', '；'.join(issues))
        selected = {i['id'] for i in delivery_items(p)}
        require(set(body['scope_ids']) == selected, 'SCOPE_CONFLICT', '确认范围与当前已选目标不一致')
        cid, bid = ident('CONF'), ident('BASE')
        confirmation = dict(baseline_id=bid, revision=p['revision'], **hashes(p), scope_ids=sorted(selected), actor='authenticated_local_user', created=now(), idempotency_key=body['idempotency_key'], request_hash=request_hash)
        store.record(pid, 'confirmation', confirmation, cid, db)
        store.record(pid, 'baseline', dict(project=copy.deepcopy(p), confirmation_id=cid, hashes=hashes(p), parent_baseline_id=p['active_baseline_id']), bid, db)
        p['active_baseline_id'] = bid
        p['confirmed_hashes']=hashes(p)
        db.execute('UPDATE projects SET payload=? WHERE id=?', (dumps(p), pid))
        store.record(pid, 'audit', dict(actor='authenticated_local_user', action='确认指定 PRD 内容版本', confirmation_id=cid, baseline_id=bid), db=db)
        return dict(id=cid, **confirmation)
