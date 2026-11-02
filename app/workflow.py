import asyncio
import copy
import time
from .core import KIT, Problem, digest, dumps, hashes, ident, now, require, execution_hash
from .contracts import gate, review_target, validate_response
from .provider import DEFAULT, origin
from .request_context import STAGES, assemble, input_metrics
from .response_application import apply_response, replace_refs, replace_business_ids
from .model_evidence import ModelCallEvidence
from .prd import compile_plan, repair_instruction
from .requirements import delivery_items
from .product_flow import execution_issues
from . import ingest
from .understanding import normalize_classification_labels
from .budgets import stage_config, output_ceiling, check_context, policy_summary

class Workflow:
    def __init__(self, store, provider):
        self.store, self.provider = store, provider
        self.tasks = {}

    def _task_progress(self, pid, rid, tid, phase):
        """Only the current running task/run binding may publish a phase."""
        if not tid:return
        import json
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            saved=db.execute("SELECT payload FROM records WHERE project_id=? AND id=? AND kind='user_task'",(pid,tid)).fetchone()
            current=json.loads(saved[0]) if saved else None
            if not current or current.get('status')!='running' or rid not in current.get('run_ids',[]):return
            saved_run=db.execute("SELECT payload FROM records WHERE project_id=? AND id=? AND kind='run'",(pid,rid)).fetchone()
            bound_run=json.loads(saved_run[0]) if saved_run else {}
            if bound_run.get('status')!='running' or bound_run.get('user_task_id')!=tid:return
            current['message']=phase
            db.execute("UPDATE records SET payload=? WHERE project_id=? AND id=? AND kind='user_task'",(dumps(current),pid,tid))

    def config(self, stage):
        settings=dict(DEFAULT, **(self.store.setting('vision' if stage == 'vision' else 'model') or {}))
        if origin(settings) == 'https://api.deepseek.com':
            settings.setdefault('business_draft_protocol', getattr(self.provider, 'business_draft_protocol', None))
            if (getattr(self.provider, 'business_draft_protocol', None)
                    and settings.get('json_mode') and settings['model'] in ('deepseek-flash', 'deepseek-v4-pro')):
                settings['structured_output_protocol'] = 'responses-json-schema-v1'
        # Old installations may retain fixed/function-first quota settings.
        # New work always follows the current uncapped execution policy.
        settings.update(budget_mode='unlimited', max_calls=None)
        config=stage_config(settings, stage)
        # Part of the authorized plan/config snapshot, never changed mid-request.
        if stage=='review' and origin(config)=='https://api.deepseek.com':
            config['thinking_disabled']=not config.get('review_thinking',True)
        if stage in ('ingest','clarify','change'):
            config['business_source_policy']='noninitial-source-only-1'
            config['detail_review_protocol_version']=3
            config['detail_property_reference_encoding']='detail-property-reference-encoding-1'
            config['detail_unknown_scope_protocol']='detail-unknown-scope-1'
            config['detail_default_scope_protocol']='detail-technical-scope-2'
            config['detail_recovery_guard']=True
            config['recovery_outcome_checks']=True
            config['detail_correction_protocol_version']=2
            config['detail_local_graph_kind_protocol']='detail-local-graph-kind-1'
        if stage in ('ingest','clarify','change') and origin(config)=='https://api.deepseek.com':
            config['detail_review_protocol_retries']=1
            config['detail_review_repair_protocol']='detail-review-entry-patch-1'
            if config.get('structured_output_protocol')=='responses-json-schema-v1' and config.get('json_mode') and config['model'] in ('deepseek-flash','deepseek-v4-pro'):
                config['detail_review_transport_protocol']='responses-json-schema-v1'
                if stage in ('ingest','clarify'):
                    config['detail_correction_policy']='two-corrections-source-seventeen-run-calls-2'
            config['detail_review_reasoning_effort']='high'
            config['detail_review_options']={'thinking_disabled':not config.get('review_thinking',True)}
            config['detail_review_options']['max_tokens']=min(config['review_max_tokens'],output_ceiling(config))
            config['detail_review_options']['timeout']=max(config['timeout'],300) if config.get('review_thinking',True) else config['timeout']
            if config.get('review_thinking',True):config['detail_review_options']['reasoning_effort']=config['detail_review_reasoning_effort']
        return config

    def start(self, pid, revision, stage, message, kind='prd', resumed_from=None, *, user_task_id=None, config_override=None, generation_target=None, discussion_target=None, benchmark=None, prewrite=False, allow_review_draft=False, draft_request=None, intake_resume=None):
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
        if draft_request:
            from .refinement_completion import validate as validate_draft_request
            validate_draft_request(self.store.get(pid),draft_request)
            require(user_task_id and draft_request==approved.get('draft_request') and stage=='ingest',
                    'DRAFT_REQUEST_INVALID','补全范围须与已批准任务一致',409)
        require(not benchmark or (stage=='ingest' and user_task_id),'BENCHMARK_INVALID','对标分析须使用已核对的任务入口')
        require(not prewrite or (stage=='review' and user_task_id and approved['action'] in ('prewrite','document')),
            'STAGE_INVALID','成文检查须通过明确核对的任务入口。')
        require(not any(t['status'] in ('queued','running') and t['id'] != user_task_id for t in self.store.records(pid,'user_task')), 'PROJECT_BUSY','当前项目任务正在执行，请等待或取消',409)
        require(not any(t['status'] in ('queued','running') for t in self.store.records(pid,'run')), 'PROJECT_BUSY','当前项目已有模型任务',409)
        p = self.store.get(pid)
        require(p['revision'] == revision, 'STALE_REVISION', '页面已过期', 409)
        if stage=='prd' and kind=='prd':
            from .prewrite import status as prewrite_status
            checked=prewrite_status(p)
            allowed=(allow_review_draft and user_task_id and approved['action']=='document'
                and approved.get('retry_input',{}).get('allow_review_draft') is True)
            require(checked['current'] and checked['status'] in ('passed','needs_confirmation')
                and (checked['status']=='passed' or allowed)
                and not any(f.get('hard_block') and f.get('status')!='resolved' for f in checked['findings']),
                'PREWRITE_REQUIRED','请通过生成 PRD 任务先执行当前版本成文检查；关键问题需处理或明确选择带问题评审稿。',409)
        if discussion_target:
            from .actions import target_context
            target_context(p, discussion_target)
        from .requirement_recommendation import scope as recommendation_scope
        local = recommendation_scope(p, discussion_target, stage)
        if local and user_task_id:
            require(local == approved.get('requirement_recommendation_scope'),
                    'STALE_PLAN', '本条需求范围与已核对计划不一致，请重新核对', 409)
        if intake_resume:
            from .ingest_recovery import derive_intake_resume
            require(user_task_id and approved.get('intake_resume')==intake_resume,
                'INTAKE_RESUME_UNAVAILABLE','续审必须绑定新核对的业务任务。',409)
            require(derive_intake_resume(self.store,p,intake_resume['task_id'])['identity']==intake_resume,
                'INTAKE_RESUME_UNAVAILABLE','原候选证据已变化。',409)
        issues=execution_issues(p,stage,kind,discussion_target,
            prewrite=prewrite and approved['action']=='prewrite')
        require(not issues,'STAGE_BLOCKED','；'.join(issues),409)
        run = dict(stage=stage, status='queued', revision=revision, message=message, document_type=kind, max_calls=limit, calls=0, attempts=[], events=[], created=now(), error=None, resumed_from=resumed_from, provider={k:v for k,v in config.items() if k not in ('proxy',)}, prompt_version='1.1', cost=None)
        if intake_resume:run.update(intake_resume=copy.deepcopy(intake_resume),resumed_from=intake_resume['run_id'])
        if stage=='vision' and user_task_id and approved.get('action')=='interview':
            run['vision_for_interview']=True
        if limit is not None:run['request_budget']=initial_budget(limit)
        rid = self.store.record(pid, 'run', run)
        self.store.update_run(pid,rid,user_task_id=user_task_id,generation_target=generation_target,input_state_hash=execution_hash(p),
            prewrite=prewrite,allow_review_draft=allow_review_draft,
            **({'draft_request':copy.deepcopy(draft_request)} if draft_request else {}),
            **({'requirement_recommendation_scope':local} if local else {}),
            **({'discussion_target':copy.deepcopy(discussion_target)} if discussion_target else {}),
            **({'benchmark':benchmark} if benchmark else {}))
        if prewrite:
            from .prewrite import input_hash
            with self.store.edit(pid,p['revision'],'开始成文一致性检查',bump=False) as (current,db):
                previous=copy.deepcopy(current.get('prewrite_review') or {})
                current['prewrite_review']=dict(previous,id=previous.get('id') or ident('PWR'),status='running',
                    input_hash=input_hash(current),input_revision=current['revision'],run_id=rid,
                    findings=previous.get('findings',[]),created=now(),message='正在检查当前需求的一致性。')
        task = asyncio.create_task(self.execute(pid, rid, p, config))
        self.tasks[rid] = task
        task.add_done_callback(lambda _: self.tasks.pop(rid, None))
        return dict(id=rid, **run)

    async def execute(self, pid, rid, p, config):
        run = self.store.get_record(pid, rid, 'run')
        from .request_budget import RequestBudget
        budget=RequestBudget(self.store,pid,rid,config['max_calls']) if config.get('max_calls') is not None else None
        calls, repair_count, retries = run.get('calls',0), 0, 0
        repair_parent = None
        pending_repair = False
        repair_anchor = None
        repair_original = None
        document_reference_repair_target = None
        prd_repair_original = None
        interview_anchor = None
        syntax_anchor = None
        prewrite_repair = None
        business_reference_repair = None
        business_reference_plan = None
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
            from .prewrite import review_project
            reviewed_project=review_project(p) if run.get('prewrite') else p
            resume_packet=None
            if run.get('intake_resume'):
                from .ingest_recovery import derive_intake_resume
                resume_packet=derive_intake_resume(self.store,p,run['intake_resume']['task_id'])
                require(resume_packet['identity']==run['intake_resume'],'INTAKE_RESUME_UNAVAILABLE','续审出站前原证据已变化。',409)
                messages,excerpts,omitted=resume_packet['messages'],resume_packet['excerpts'],[]
            else:
                messages, excerpts, omitted = assemble(reviewed_project, run['stage'], run['message'], config, self.store.folder, run['document_type'], run.get('generation_target'), pending_images_only=bool(run.get('user_task_id')),vision_for_interview=run.get('vision_for_interview',False),
                    requirement_recommendation_scope=run.get('requirement_recommendation_scope'),discussion_target=run.get('discussion_target'),**({'draft_request':run['draft_request']} if run.get('draft_request') else {}),**({'benchmark':run['benchmark']} if run.get('benchmark') else {}))
            import json
            header=json.loads(messages[0]['content'].split('可信任务头：',1)[1])
            from .skill_guidance import freeze as freeze_skills, preserve as preserve_skills
            frozen_skills=freeze_skills(messages[0]['content'])
            business_protocol = header.get('response_protocol') == 'business-draft-v1'
            if business_protocol and header.get('detail_required') and header.get('draft_scope')=='single_row' and not resume_packet:
                from .ingest_recovery import rejected_detail_reference
                from .business_draft import attach_rejected_detail_reference
                reference=rejected_detail_reference(self.store,p,run,json.loads(messages[1]['content'][0]['text']),excerpts)
                if reference is not None:
                    messages=attach_rejected_detail_reference(messages,reference,config)
                    audit={k:copy.deepcopy(v) for k,v in reference.items() if k!='detail_contract'}
                    self.store.update_run(pid,rid,rejected_detail_reference=audit)
                    events.append(dict(time=now(),phase=('已带入完整未采纳详细候选，仍须正常生成与审查' if reference['status']=='unaccepted_ai_candidate'
                        else '未复用完整旧详细候选：'+reference['reason'])))
            from .dsh_intake import eligible as dsh_eligible
            use_dsh = not resume_packet and config.get('intake_engine') == 'deepseek-harness' and dsh_eligible(run, header)
            from .dsh_intake import SDK_VERSION as dsh_version, VERSION as dsh_intake_version
            intake_runtime=dict(engine='deepseek-harness' if use_dsh else 'direct',
                version=dsh_version if use_dsh else None,adapter_version=dsh_intake_version if use_dsh else None,
                transport='chat_completions' if use_dsh else config.get('structured_output_protocol','chat_completions'))
            self.store.update_run(pid,rid,intake_engine=intake_runtime['engine'],intake_runtime=intake_runtime)
            bounded_detail=bool(business_protocol and header.get('detail_required') and
                (run.get('draft_request') or {}).get('mode')!='question' and config.get('detail_correction_policy'))
            if bounded_detail:
                require(config['detail_correction_policy'] in ('two-corrections-seven-run-calls-1','two-corrections-source-seventeen-run-calls-2'),
                    'DETAIL_CORRECTION_POLICY_INVALID','不支持的详细定义有限纠正策略。')
            detail_run_cap=17 if config.get('detail_correction_policy')=='two-corrections-source-seventeen-run-calls-2' else 7
            def available_calls(count=1):
                if bounded_detail:
                    used=self.store.get_record(pid,rid,'run')['calls']
                    require(used+count<=detail_run_cap,'DETAIL_RUN_CALL_LIMIT',
                        f'本轮详细定义请求已用 {used}/{detail_run_cap}，后续步骤需要至少 {count} 次；未发送，原候选与审查结果保留。')
                if budget:budget.available(count)
            assembled_context=copy.deepcopy(resume_packet['context']) if resume_packet else json.loads(messages[1]['content'][0]['text'])
            platform_background=assembled_context.get('platform_knowledge')
            authorization=dict(user_task_id=run.get('user_task_id'),run_id=rid,approved_max_tokens=output_ceiling(config),
                initial_max_tokens=out_budget,budget_policy=policy_summary(config),
                approved_max_calls=config['max_calls'],config_hash=digest(run['provider']),
                prompt_hash=digest(messages[0]['content'].split('可信任务头：',1)[0]),schema_hash=digest(header['schema']),
                profile_hash=digest(header.get('content_profile')),input_revision=p['revision'],assembly_version=header.get('response_protocol') or ('prd-plan-4' if run['stage']=='prd' else 'runtime-1.1'))
            if frozen_skills:
                authorization['builtin_skills']=copy.deepcopy(frozen_skills['manifest'])
            authorization['intake_runtime']=copy.deepcopy(intake_runtime)
            if platform_background:
                authorization['platform_knowledge']={k:platform_background[k]
                    for k in ('bundle_id','sha256','generated_at','classification','retrieval')}
            self.store.update_run(pid, rid, status='running', sent_excerpt_ids=[x['id'] for x in excerpts], omitted_excerpt_ids=omitted, input_hash=digest(messages),authorization=authorization,input_metrics=input_metrics(messages),source_snapshot=source_snapshot(p,excerpts))
            auxiliary_evidence={}
            detail_review_protocol_retries=0
            detail_review_repaired_candidates=set()
            async def auxiliary(aux_messages,phase,protocol,parent,*,source_review=False):
                """Same authorized run/config/budget; no hidden semantic requests."""
                nonlocal calls
                require(self.store.get_record(pid,rid,'run')['status']!='cancelled','CANCELLED','任务已取消，未继续核对。')
                if resume_packet:
                    from .ingest_recovery import derive_intake_resume
                    require(derive_intake_resume(self.store,self.store.get(pid),run['intake_resume']['task_id'])['identity']==run['intake_resume'],
                        'INTAKE_RESUME_UNAVAILABLE','续审出站前原来源或证据已变化。',409)
                available_calls()
                effective=dict(config,max_tokens=out_budget)
                config_role='detail_source_review' if source_review and config.get('detail_review_options') else None
                if config_role:effective.update(config['detail_review_options'])
                if config_role and 'max_tokens' in config['detail_review_options']:
                    effective['max_tokens']=min(effective['max_tokens'],authorization['approved_max_tokens'])
                check_context(aux_messages,effective)
                if budget:budget.available()
                aux_id=ident('CALL')
                aux_header=json.loads(aux_messages[0]['content'].split('可信任务头：',1)[1])
                if source_review and config.get('detail_review_transport_protocol') is not None:
                    require(config['detail_review_transport_protocol']=='responses-json-schema-v1'
                        and origin(effective)=='https://api.deepseek.com' and effective['model'] in ('deepseek-flash','deepseek-v4-pro')
                        and effective.get('json_mode') and effective.get('structured_output_protocol')=='responses-json-schema-v1'
                        and getattr(self.provider,'business_draft_protocol',None)
                        and aux_header.get('transport_protocol')==config['detail_review_transport_protocol']
                        and aux_header.get('strict_json_protocol')=='detail-review-strict-json-1',
                        'DETAIL_REVIEW_TRANSPORT_INVALID','当前接收端、模型或审查消息不支持已冻结的严格结构化传输；未发送审查请求。')
                aux_auth=dict(authorization,repair_protocol=protocol,schema_hash=digest(aux_header['schema']),
                    prompt_hash=digest(aux_messages[0]['content'].split('可信任务头：',1)[0]))
                safe_effective={k:v for k,v in effective.items() if k!='proxy'}
                aux_auth.update(config_role=config_role,effective_config=safe_effective,effective_config_hash=digest(safe_effective))
                aux_evidence=ModelCallEvidence(self.store.folder,aux_id,rid,run['stage'],config['model'],
                    origin(config),parent,self.provider.key(config),request_budget=budget)
                auxiliary_evidence[aux_id]=aux_evidence
                aux_evidence.request_context(effective,aux_messages,aux_auth,input_metrics(aux_messages))
                number=self.store.get_record(pid,rid,'run')['calls']+1 if budget else calls+1
                if not budget:calls+=1
                aux_attempt=dict(call=number,call_id=aux_id,repair_of=parent,max_tokens=effective['max_tokens'],
                    protocol=protocol,authorization_hash=digest(aux_auth),actual_input_hash=digest(aux_messages),
                    config_role=config_role,effective_config_hash=digest(safe_effective))
                events.append(dict(time=now(),phase=phase,call=number,call_id=aux_id))
                self.store.update_run(pid,rid,events=events,**({} if budget else {'calls':calls}))
                self._task_progress(pid,rid,run.get('user_task_id'),phase+f'（本步骤第 {number} 次模型请求）')
                started=time.monotonic()
                try:
                    result,metadata=await asyncio.wait_for(self.provider.request(effective,aux_messages,
                        evidence=aux_evidence),timeout=effective['timeout'])
                    if budget:
                        require(aux_evidence.value.get('request_state')=='response_received','BUDGET_AUTH_REQUIRED','辅助核对缺少出站计数绑定。')
                        calls=self.store.get_record(pid,rid,'run')['calls']
                    require(self.store.get_record(pid,rid,'run')['status']!='cancelled','CANCELLED','任务已取消，核对结果未采用。')
                    aux_attempt.update(metadata,validation_result='response_received')
                    aux_evidence.finish('response_received',round(time.monotonic()-started,3))
                    return result,aux_id
                except (Problem,asyncio.TimeoutError) as error:
                    if budget:calls=self.store.get_record(pid,rid,'run')['calls']
                    if isinstance(error,asyncio.TimeoutError):error=Problem('TIMEOUT','语义核对请求超时，原候选和已有检查结果保留。')
                    error.auxiliary_call_id=aux_id
                    aux_attempt.update(error=error.code,validation_result=error.code)
                    aux_evidence.finish(error.code,round(time.monotonic()-started,3))
                    raise error
                except asyncio.CancelledError:
                    if budget:calls=self.store.get_record(pid,rid,'run')['calls']
                    aux_attempt.update(error='CANCELLED',validation_result='CANCELLED')
                    aux_evidence.finish('CANCELLED',round(time.monotonic()-started,3))
                    raise
                except Exception:
                    if budget:calls=self.store.get_record(pid,rid,'run')['calls']
                    aux_attempt.update(error='INTERNAL_ERROR',validation_result='INTERNAL_ERROR')
                    aux_evidence.finish('INTERNAL_ERROR',round(time.monotonic()-started,3))
                    raise
                finally:
                    aux_attempt['evidence_limitations']=aux_evidence.limitations[:]
                    attempts.append(aux_attempt)
                    self.store.update_run(pid,rid,attempts=sorted(attempts,key=lambda a:a['call']))
            def protocol_recovery_slot(required):
                nonlocal detail_review_protocol_retries
                if (header.get('initial_intake') or detail_run_cap!=17 or
                    not config.get('detail_review_protocol_retries') or detail_review_protocol_retries>=1):return False
                available_calls(required)
                detail_review_protocol_retries+=1
                return True

            def protocol_failure(call_id,error):
                record=auxiliary_evidence[call_id]
                record.value['semantic_protocol_error']=dict(code=error.code,message=error.message)
                record.finish(error.code)
                next(a for a in attempts if a['call_id']==call_id).update(validation_result=error.code)
                self.store.update_run(pid,rid,attempts=sorted(attempts,key=lambda a:a['call']))

            async def checked_source_challenge(review_messages,phase,parent,validate,*,source_review=False):
                # One fresh independent review, never editing the invalid hash
                # or reusing an old pass. Semantic findings are valid results.
                while True:
                    review_id=None;response=None
                    try:
                        response,review_id=await auxiliary(review_messages,phase,
                            'intake-grounding-challenge-2',parent,source_review=source_review)
                        checked=validate(response)
                        return response,review_id,checked
                    except Problem as error:
                        failed_id=review_id or getattr(error,'auxiliary_call_id',None)
                        if error.code not in ('INTAKE_GROUNDING_INVALID','SCHEMA_INVALID') or not failed_id:raise
                        protocol_failure(failed_id,error)
                        expected=json.loads(review_messages[0]['content'].split('可信任务头：',1)[1])['schema']['properties']
                        if (isinstance(response,dict) and response.get('findings') and
                            all(response.get(key)==expected[key]['const'] for key in ('candidate_hash','input_hash'))):
                            # A malformed finding cannot erase other, already
                            # bound semantic findings through a fresh empty pass.
                            raise
                        if not protocol_recovery_slot(1):raise
                        auxiliary_evidence[failed_id].value['protocol_recovery']=dict(mode='fresh_same_input_review',
                            input_hash=digest(review_messages),shared_run_allowance=1)
                        auxiliary_evidence[failed_id].save()
                        parent=failed_id;phase='重新独立核对来源协议（同一候选，仅一次）'

            async def source_patch_request(patch_messages,phase,parent):
                try:
                    return await auxiliary(patch_messages,phase,'intake-grounding-patch-2',parent)
                except Problem as error:
                    from .format_repair import anchor,instruction,literal_atoms
                    failed_id=getattr(error,'auxiliary_call_id',None)
                    if error.code!='SCHEMA_INVALID' or not failed_id:raise
                    failed=auxiliary_evidence[failed_id];original_anchor=anchor(failed)
                    if original_anchor is None:raise
                    # A valid JSON with wrong schema, duplicate members or
                    # business errors is not a syntax-only repair candidate.
                    from .core import strict_model_json
                    raw=failed.value['final_output'].strip()
                    if raw.startswith('```json\n') and raw.endswith('\n```'):raw=raw[8:-4]
                    try:strict_model_json(raw)
                    except json.JSONDecodeError:pass
                    except ValueError:raise error
                    else:raise error
                    if not protocol_recovery_slot(2):raise
                    messages=patch_messages+[dict(role='user',content=instruction(error.message,failed.value['final_output']))]
                    failed.value['protocol_recovery']=dict(mode='literal_preserving_patch_format',
                        anchor_hash=digest(original_anchor),shared_run_allowance=1)
                    failed.save()
                    result,repair_id=await auxiliary(messages,'仅修复来源补丁JSON格式；原文与引用保持不变',
                        'intake-grounding-patch-2',failed_id)
                    record=auxiliary_evidence[repair_id]
                    record.value['source_patch_format_repair']=dict(original_call_id=failed_id,
                        anchor_hash=digest(original_anchor),status='pending')
                    record.save()
                    if anchor(record)!=original_anchor or literal_atoms(json.dumps(result,ensure_ascii=False,allow_nan=False))!=original_anchor:
                        record.value['source_patch_format_repair']['status']='rejected'
                        error=Problem('SEMANTIC_BLOCKED','来源补丁格式修复改变了原文字面内容、顺序或证据身份。')
                        protocol_failure(repair_id,error)
                        raise error
                    record.value['source_patch_format_repair']['status']='literal_preserved';record.save()
                    return result,repair_id

            async def challenge_detail(plan,result,review_id):
                if not bounded_detail or any(r['status']!='passed' or r['findings'] for r in result.values()):
                    return result,review_id
                from . import detail_semantics,intake_grounding
                packet=detail_semantics.challenge_plan(plan,result)
                review_evidence=auxiliary_evidence[review_id]
                review_evidence.value['detail_source_challenge']=dict(status='pending',
                    candidate_hash=packet['candidate_hash'],input_hash=packet['input_hash'],full_review_outcomes=copy.deepcopy(result))
                review_evidence.save()
                response,challenge_id,merged=await checked_source_challenge(
                    detail_semantics.challenge_messages(packet,transport_protocol=config.get('detail_review_transport_protocol')),
                    '正在独立核对详细属性自身来源、条件与范围',review_id,
                    lambda response:detail_semantics.challenge_outcomes(plan,packet,response,result),source_review=True)
                challenge_evidence=auxiliary_evidence[challenge_id]
                challenge_evidence.value['detail_source_challenge']=dict(full_review_call_id=review_id,
                    candidate_hash=packet['candidate_hash'],input_hash=packet['input_hash'],outcomes=copy.deepcopy(merged))
                challenge_evidence.save()
                review_evidence.value['detail_source_challenge'].update(status='completed',call_id=challenge_id)
                review_evidence.save()
                return merged,challenge_id
            async def review_detail(plan,phase,parent,scope_feedback=None):
                nonlocal detail_review_protocol_retries
                from . import detail_semantics
                review_messages=detail_semantics.scope_correction_messages(
                    detail_semantics.messages(plan,transport_protocol=config.get('detail_review_transport_protocol')),scope_feedback)
                while True:
                    try:
                        original,review_id=await auxiliary(review_messages,phase,plan['version'],parent,source_review=True)
                    except Problem as error:
                        failed_id=getattr(error,'auxiliary_call_id',None)
                        if error.code!='SCHEMA_INVALID' or not failed_id or not protocol_recovery_slot(2):raise
                        protocol_failure(failed_id,error)
                        auxiliary_evidence[failed_id].value['protocol_recovery']=dict(mode='fresh_same_input_review',
                            input_hash=digest(review_messages),shared_run_allowance=1)
                        auxiliary_evidence[failed_id].save()
                        parent=failed_id;phase='重新完整核对审查协议（同一候选，仅一次）'
                        continue
                    normalized,changes=detail_semantics.normalize_review(original)
                    if changes:
                        normalization=dict(rule='supported-empty-correction-scope-2',input_hash=digest(original),
                            output_hash=digest(normalized),changes=changes)
                        review_evidence=auxiliary_evidence[review_id]
                        review_evidence.value.setdefault('response_normalizations',[]).append(normalization)
                        review_evidence.value['normalized_detail_review']=copy.deepcopy(normalized)
                        review_evidence.save()
                        next(a for a in attempts if a['call_id']==review_id).setdefault('response_normalizations',[]).append(normalization)
                    try:
                        normalized,decoding=detail_semantics.decode_review(plan,normalized)
                        if decoding is not None:
                            review_evidence=auxiliary_evidence[review_id]
                            review_evidence.value.update(property_reference_decoding=decoding,canonical_detail_review=copy.deepcopy(normalized))
                            review_evidence.save()
                        result=detail_semantics.validate(plan,normalized)
                        structures={key:value['recovery_structure_guard'] for key,value in result.items() if 'recovery_structure_guard' in value}
                        if structures:
                            auxiliary_evidence[review_id].value['recovery_structure_guard']=structures
                            auxiliary_evidence[review_id].save()
                        resolutions={key:value['quote_reference_resolution'] for key,value in result.items() if 'quote_reference_resolution' in value}
                        if resolutions:
                            auxiliary_evidence[review_id].value['quote_reference_resolution']=resolutions
                            auxiliary_evidence[review_id].save()
                        return await challenge_detail(plan,result,review_id)
                    except Problem as error:
                        if error.code!='DETAIL_REVIEW_INVALID':raise
                        auxiliary_evidence[review_id].value['semantic_protocol_error']=dict(code=error.code,message=error.message)
                        auxiliary_evidence[review_id].finish(error.code)
                        review_attempt=next(a for a in attempts if a['call_id']==review_id)
                        review_attempt.update(validation_result=error.code,semantic_protocol_error=dict(code=error.code,message=error.message))
                        self.store.update_run(pid,rid,attempts=sorted(attempts,key=lambda a:a['call']))
                        if config.get('detail_review_repair_protocol')=='detail-review-entry-patch-1':
                            from . import detail_review_repair
                            candidate_identity=digest([(r['row_key'],r['candidate_hash']) for r in plan['rows']])
                            if (candidate_identity in detail_review_repaired_candidates or
                                (detail_run_cap==17 and detail_review_protocol_retries>=1) or
                                not config.get('detail_review_protocol_retries')):raise
                            packet=detail_review_repair.prepare(plan,normalized)
                            # Ambiguous/global defects remain rejected. Never fabricate
                            # placeholder checks to discover or conceal further errors.
                            if not packet:
                                # Only a missing/mismatched global quote-pool
                                # identity permits a fresh full review. Other
                                # mixed/partial defects retain the original reject.
                                if isinstance(normalized,dict) and normalized.get('quote_pool_hash')==plan.get('quote_pool_hash'):raise
                                if not protocol_recovery_slot(2):raise
                                auxiliary_evidence[review_id].value['protocol_recovery']=dict(mode='fresh_same_input_review',
                                    input_hash=digest(review_messages),shared_run_allowance=1)
                                auxiliary_evidence[review_id].save()
                                parent=review_id;phase='重新完整核对审查协议（同一候选，仅一次）'
                                continue
                            auxiliary_evidence[review_id].value['review_entry_diagnostics']={k:copy.deepcopy(v) for k,v in packet.items() if k!='original'}
                            auxiliary_evidence[review_id].save()
                            if detail_run_cap==17:available_calls(2)
                            elif budget:budget.available()
                            detail_review_repaired_candidates.add(candidate_identity)
                            if detail_run_cap==17:detail_review_protocol_retries+=1
                            patch,patch_id=await auxiliary(detail_review_repair.messages(plan,packet,transport_protocol=config.get('detail_review_transport_protocol')),
                                '核对错误审查条目（同一候选，仅一次）',detail_review_repair.PROTOCOL,review_id,source_review=True)
                            patch_evidence=auxiliary_evidence[patch_id]
                            patch_evidence.value['review_entry_repair_input']=dict(original_review_call_id=review_id,
                                plan_hash=packet['plan_hash'],normalized_review_hash=packet['review_hash'],
                                raw_review_hash=digest(original),diagnostics=copy.deepcopy(packet['slots']))
                            try:
                                merged,result,audit=detail_review_repair.apply(plan,packet,patch,assembly=patch_evidence.value)
                            except Problem as patch_error:
                                if 'review_entry_repair' in patch_evidence.value:
                                    patch_evidence.value['review_entry_repair']['full_strict_validation']=patch_error.code
                                patch_evidence.value['semantic_protocol_error']=dict(code=patch_error.code,message=patch_error.message)
                                patch_evidence.finish(patch_error.code)
                                next(a for a in attempts if a['call_id']==patch_id).update(validation_result=patch_error.code)
                                self.store.update_run(pid,rid,attempts=sorted(attempts,key=lambda a:a['call']))
                                raise
                            patch_evidence.value.update(merged_detail_review=merged,review_entry_repair=audit,
                                quote_reference_resolution={key:value['quote_reference_resolution'] for key,value in result.items() if 'quote_reference_resolution' in value},
                                recovery_structure_guard={key:value['recovery_structure_guard'] for key,value in result.items() if 'recovery_structure_guard' in value})
                            patch_evidence.finish('passed')
                            next(a for a in attempts if a['call_id']==patch_id).update(validation_result='passed',review_entry_repair=audit)
                            self.store.update_run(pid,rid,attempts=sorted(attempts,key=lambda a:a['call']))
                            return await challenge_detail(plan,result,patch_id)
                        if detail_run_cap==17:
                            if isinstance(normalized,dict) and normalized.get('quote_pool_hash')==plan.get('quote_pool_hash'):raise
                            if not protocol_recovery_slot(2):raise
                            auxiliary_evidence[review_id].value['protocol_recovery']=dict(mode='fresh_same_input_review',
                                input_hash=digest(review_messages),shared_run_allowance=1)
                            auxiliary_evidence[review_id].save()
                            parent=review_id;phase='重新完整核对审查协议（同一候选，仅一次）'
                            continue
                        if detail_review_protocol_retries>=min(config.get('detail_review_protocol_retries',0),1):raise
                        if budget:budget.available()
                        detail_review_protocol_retries+=1
                        review_messages=detail_semantics.scope_correction_messages(
                    detail_semantics.messages(plan,transport_protocol=config.get('detail_review_transport_protocol')),scope_feedback)+[dict(role='user',content=dumps(dict(
                            instruction='上次审查协议无效。候选与来源保持原样，仅重新完成审查；按精确错误核对原引用，不能补造引文、改候选或因修复协议而改成通过。全部属性、状态和路径仍须完整核对。',
                            validator_error=dict(code=error.code,message=error.message),previous_review=original)))]
                        parent=review_id
                        phase='重新核对审查协议与来源引用（同一候选，仅一次）'
            memory_trace=None
            from . import project_memory
            if not resume_packet and project_memory.enabled(p,run['stage']):
                def memory_progress(trace):
                    self.store.update_run(pid,rid,memory_trace=copy.deepcopy(trace))
                memory_trace=await project_memory.prepare(self.store,p,excerpts,run['stage'],auxiliary,config,memory_progress)
                messages=project_memory.attach(messages,memory_trace,run['stage'],excerpts)
                check_context(messages,config)
                self.store.update_run(pid,rid,input_hash=digest(messages),input_metrics=input_metrics(messages))
            while True:
                available_calls()
                messages=preserve_skills(messages,frozen_skills)
                authorization['prompt_hash']=digest(messages[0]['content'].split('可信任务头：',1)[0])
                state = self.store.get_record(pid, rid, 'run')
                require(state['status'] != 'cancelled', 'CANCELLED', '任务已取消；已发请求仍可能计费')
                check_context(messages, config)
                if budget:budget.available()
                if pending_repair:
                    repair_count += 1
                    pending_repair = False
                if resume_packet:
                    from .ingest_recovery import IntakeResumeAudit
                    call_id=resume_packet['identity']['call_id']
                    evidence=IntakeResumeAudit(self.store,pid,rid,resume_packet['identity'])
                    evidence.save()
                    attempt={}
                else:
                    if not budget and not use_dsh:calls += 1
                    call_id = ident('CALL')
                    evidence = ModelCallEvidence(self.store.folder, call_id, rid, run['stage'],
                                                 config['model'], origin(config), repair_parent,
                                                 self.provider.key(config),request_budget=budget)
                    evidence.request_context(dict(config,max_tokens=out_budget),messages,authorization,input_metrics(messages))
                    attempt = dict(call=calls if not budget else self.store.get_record(pid,rid,'run')['calls']+1, call_id=call_id, repair_of=repair_parent,max_tokens=out_budget,
                        authorization_hash=digest(authorization),actual_input_hash=digest(messages))
                    if memory_trace is not None and memory_trace.get('status')=='ready' and not use_dsh:
                        original_before_send=evidence.before_send
                        def memory_main_dispatch(effective, original=original_before_send, number=attempt['call']):
                            # Keep reservation/dispatch evidence first; no new request or retry.
                            original(effective)
                            self._task_progress(pid,rid,run.get('user_task_id'),f'正在处理本轮需求（本步骤第 {number} 次模型请求）')
                        evidence.before_send=memory_main_dispatch
                    events.append(dict(time=now(), phase='调用模型', call=len(attempts)+1, call_id=call_id))
                    self.store.update_run(pid, rid, events=events,**({} if budget else {'calls':calls}))
                value = None
                raw_prd_plan = None
                call_started = time.monotonic()
                try:
                    if resume_packet:
                        value,meta=copy.deepcopy(resume_packet['candidate']),{}
                    elif use_dsh:
                        from .dsh_intake import request as dsh_request
                        base_calls = calls
                        def dsh_dispatch(count):
                            nonlocal calls
                            calls = self.store.get_record(pid,rid,'run')['calls'] if budget else base_calls + count
                            if not budget:self.store.update_run(pid,rid,calls=calls)
                        def dsh_progress(phase):
                            events.append(dict(time=now(),phase=phase,call_id=call_id))
                            self.store.update_run(pid,rid,events=events)
                            self._task_progress(pid,rid,run.get('user_task_id'),phase)
                        value,meta = await dsh_request(dict(config,max_tokens=out_budget),messages,
                            self.provider.key(config),evidence,on_dispatch=dsh_dispatch,on_progress=dsh_progress)
                    else:
                        value, meta = await asyncio.wait_for(self.provider.request(dict(config, max_tokens=out_budget), messages, evidence=evidence), timeout=config['timeout'])
                    if memory_trace is not None and project_memory.present_in(messages,memory_trace):
                        memory_trace.update(consumed=True,consumed_stages=[run['stage']],consumed_input_hash=digest(messages))
                        memory_progress(memory_trace)
                    if budget and not use_dsh and not resume_packet:
                        require(evidence.value.get('request_state')=='response_received','BUDGET_AUTH_REQUIRED','模型响应缺少出站计数绑定，未采纳结果')
                        calls=self.store.get_record(pid,rid,'run')['calls']
                    attempt.update(meta)
                    require(self.store.get_record(pid, rid, 'run')['status'] != 'cancelled', 'CANCELLED', '已取消，结果未采纳')
                    if syntax_anchor is not None:
                        from .format_repair import anchor as format_anchor
                        require(format_anchor(evidence)==syntax_anchor,'SEMANTIC_BLOCKED',
                                '格式修复改变了原回复的业务字面内容或顺序，结果未写入；原始证据保留。')
                    if prewrite_repair is not None:
                        from .prewrite_repair import apply as apply_review_patch
                        patch=copy.deepcopy(value)
                        value=apply_review_patch(prewrite_repair,patch)
                        evidence.value['prewrite_metadata_repair']=dict(patch=patch,
                            original_hash=digest(prewrite_repair['original']),output_hash=digest(value))
                        evidence.save()
                    if business_reference_repair is not None:
                        from .business_reference_repair import apply as apply_business_refs
                        patch=copy.deepcopy(value)
                        value=apply_business_refs(business_reference_repair,patch)
                        evidence.value['business_reference_repair']=dict(patch=patch,
                            original_hash=digest(business_reference_repair['original']),output_hash=digest(value))
                        evidence.save()
                    if business_protocol:
                        if repair_count==0 and (run.get('draft_request') or {}).get('mode')=='prewrite':
                            from .business_reference_repair import prepare as prepare_business_refs
                            business_reference_plan=prepare_business_refs(value,excerpts)
                            if business_reference_plan:raise Problem('BUSINESS_REFERENCE_INVALID','业务草稿存在无效来源编号，需要核对本轮材料。')
                        from .business_draft import adapt
                        raw_business=copy.deepcopy(value)
                        raw_hash = digest(value)
                        value, business_draft_review = adapt(value, p, excerpts, run['stage'], run.get('requirement_recommendation_scope'),draft_request=run.get('draft_request'),initial_intake_scope=header.get('initial_intake',False))
                        if header.get('initial_intake'):
                            from . import intake_grounding
                            grounding=dict(protocol=intake_grounding.VERSION,status='checking',corrections=0,
                                review_protocol=intake_grounding.PAIRED_VERSION,coverage_protocol=intake_grounding.COVERAGE_VERSION,
                                challenge_protocol=intake_grounding.CHALLENGE_VERSION,
                                challenge_call_ids=[],review_call_ids=[],reason='正在逐字段核对来源，并检查已答决定是否完整带入。',
                                original_candidate_hash=raw_hash,candidate_hash=raw_hash,
                                input_hash=digest(assembled_context),candidate_applied=False)
                            grounding_history=[]
                            def save_grounding():
                                evidence.value['intake_grounding']=copy.deepcopy(grounding)
                                evidence.value['intake_grounding_history']=copy.deepcopy(grounding_history)
                                evidence.save()
                                self.store.update_run(pid,rid,intake_grounding=copy.deepcopy(grounding))
                            save_grounding()
                            try:
                                for grounding_round in range(intake_grounding.MAX_CORRECTIONS+1):
                                    grounding_plan=intake_grounding.prepare(raw_business,assembled_context)
                                    grounding['candidate_hash']=grounding_plan['candidate_hash']
                                    available_calls(2)
                                    grounding.update(coverage_status=None,challenge_status=None,
                                        reason='正在核对全部已答子决定是否完整带入，并检查重复追问。')
                                    save_grounding()
                                    response,review_id=await auxiliary(intake_grounding.coverage_messages(grounding_plan),
                                        grounding['reason'],intake_grounding.COVERAGE_VERSION,call_id)
                                    grounding['review_call_ids'].append(review_id)
                                    grounding_history.append(dict(call_id=review_id,kind='coverage',
                                        candidate_hash=grounding_plan['candidate_hash'],response=copy.deepcopy(response)))
                                    save_grounding()
                                    checked=intake_grounding.validate_coverage(grounding_plan,response)
                                    grounding_history[-1]['validation']=copy.deepcopy(checked)
                                    auxiliary_evidence[review_id].value['intake_grounding_validation']=copy.deepcopy(checked)
                                    auxiliary_evidence[review_id].save()
                                    grounding.update(coverage_status=checked['status'],
                                        reason='正在独立质疑来源关系：核查位置、权限、现状、时序与不必要追问。')
                                    save_grounding()
                                    challenge_response,challenge_id=await auxiliary(
                                        intake_grounding.challenge_messages(grounding_plan),grounding['reason'],
                                        intake_grounding.CHALLENGE_VERSION,review_id)
                                    grounding['challenge_call_ids'].append(challenge_id)
                                    grounding_history.append(dict(call_id=challenge_id,kind='source_challenge',
                                        candidate_hash=grounding_plan['candidate_hash'],response=copy.deepcopy(challenge_response)))
                                    save_grounding()
                                    challenge=intake_grounding.validate_challenge(grounding_plan,challenge_response)
                                    grounding_history[-1]['validation']=copy.deepcopy(challenge)
                                    auxiliary_evidence[challenge_id].value['intake_grounding_challenge']=copy.deepcopy(challenge)
                                    auxiliary_evidence[challenge_id].save()
                                    grounding['challenge_status']=challenge['status']
                                    checked['findings'].extend(copy.deepcopy(challenge['findings']))
                                    grounding['local_findings']=intake_grounding.local_findings(grounding_plan)
                                    checked['findings'].extend(copy.deepcopy(grounding['local_findings']))
                                    checked['status']='blocked' if checked['findings'] else 'passed'
                                    review_id=challenge_id
                                    save_grounding()
                                    if checked['status']=='passed':
                                        grounding.update(status='repaired' if grounding_round else 'passed',
                                            reason='已完成已答覆盖与独立来源核查，初稿仍待人工核对。')
                                        break
                                    if grounding_round==intake_grounding.MAX_CORRECTIONS:
                                        grounding.update(status='blocked',reason='纠正后仍有来源或已答完整性问题；候选只保留为证据，未写入初稿，已有内容不变。')
                                        raise Problem('INTAKE_GROUNDING_BLOCKED',grounding['reason'],model_repair_allowed=False)
                                    # Do not spend a patch request without room to review the
                                    # entire result and source challenge. Every outbound uses auxiliary.
                                    available_calls(3)
                                    grounding.update(status='correcting',reason=f'正在进行第{grounding_round+1}轮纠正：补齐漏引和已答约束，纠正无依据断言；最多纠正两轮。')
                                    save_grounding()
                                    patch_schema,patch_messages=intake_grounding.correction_messages(grounding_plan,checked)
                                    patch,patch_id=await auxiliary(patch_messages,grounding['reason'],
                                        intake_grounding.PATCH_VERSION,review_id)
                                    grounding_history.append(dict(call_id=patch_id,kind='patch',patch=copy.deepcopy(patch)))
                                    save_grounding()
                                    corrected=intake_grounding.apply_correction(grounding_plan,patch,patch_schema)
                                    evidence.value['intake_grounding_corrected_candidate']=copy.deepcopy(corrected)
                                    evidence.save()
                                    trial,trial_report=adapt(corrected,p,excerpts,run['stage'],
                                        run.get('requirement_recommendation_scope'),draft_request=run.get('draft_request'),initial_intake_scope=True)
                                    require(trial_report['complete'],'INTAKE_GROUNDING_BLOCKED',
                                        '纠正候选未通过完整结构与引用检查；候选只保留为证据，已有内容不变。')
                                    raw_business=corrected;value=trial;business_draft_review=trial_report
                                    evidence.value['corrected_business_candidate']=copy.deepcopy(corrected)
                                    grounding.update(corrections=grounding_round+1,status='rechecking',candidate_hash=digest(corrected),
                                        reason='正在重新核对整份修后初稿及全部已答子决定，防止纠正时丢失约束。')
                                    save_grounding()
                                business_draft_review['intake_grounding']=copy.deepcopy(grounding)
                                business_draft_review['source_validation']='model_semantic_checked_not_human_approval'
                                save_grounding()
                            except (Exception,asyncio.CancelledError) as grounding_error:
                                code=getattr(grounding_error,'code','CANCELLED' if isinstance(grounding_error,asyncio.CancelledError) else 'INTERNAL_ERROR')
                                if grounding['status']!='blocked':
                                    grounding.update(status='unverified',reason='来源与已答完整性核对未完成；候选只保留为证据，未写入初稿，已有内容不变。')
                                grounding['error']=code
                                grounding['error_type']=type(grounding_error).__name__
                                if isinstance(grounding_error,Problem):
                                    grounding['error_reason']=grounding_error.message
                                save_grounding()
                                if isinstance(grounding_error,asyncio.CancelledError) or code in ('CANCELLED','BUDGET_EXHAUSTED','INTAKE_GROUNDING_BLOCKED'):
                                    raise
                                raise Problem('INTAKE_GROUNDING_UNVERIFIED',grounding['reason'],model_repair_allowed=False) from grounding_error
                        body_source_review=None
                        if not header.get('initial_intake') and raw_business.get('requirements') and config.get('business_source_policy')=='noninitial-source-only-1':
                            from . import intake_grounding
                            body_source_review=dict(protocol='noninitial-source-only-1',status='pending',corrections=0,
                                scope='generated_requirement_statement_behavior_acceptance_only',source_only=True,coverage_review=False,
                                candidate_hash=digest(raw_business),input_hash=digest(assembled_context),history=[])
                            def save_body_source():
                                evidence.value['business_source_review']=copy.deepcopy(body_source_review)
                                evidence.save()
                                self.store.update_run(pid,rid,business_source_review=copy.deepcopy(body_source_review))
                            save_body_source()
                            try:
                                for source_round in range(3):
                                    source_plan=intake_grounding.source_plan(raw_business,assembled_context)
                                    body_source_review['candidate_hash']=source_plan['candidate_hash']
                                    source_response,source_id,checked=await checked_source_challenge(intake_grounding.challenge_messages(source_plan),
                                        '正在核对本次正文与验收条款各自的来源、主体及适用条件',call_id,
                                        lambda response:intake_grounding.validate_challenge(source_plan,response))
                                    entry=dict(call_id=source_id,candidate_hash=source_plan['candidate_hash'],response=copy.deepcopy(source_response))
                                    body_source_review['history'].append(entry);save_body_source()
                                    entry['validation']=copy.deepcopy(checked)
                                    auxiliary_evidence[source_id].value['business_source_review']=copy.deepcopy(checked)
                                    auxiliary_evidence[source_id].save()
                                    if checked['status']=='passed':
                                        body_source_review.update(status='passed',reviewed_fields_hash=digest(source_plan['fields']));save_body_source();break
                                    body_source_review['status']='blocked';save_body_source()
                                    if source_round==2:
                                        raise Problem('BUSINESS_SOURCE_BLOCKED','本次正文或验收仍有来源问题，候选仅保留为证据，原内容未改。',model_repair_allowed=False)
                                    # Reserve both a located correction and its fresh source check.
                                    available_calls(2)
                                    schema,patch_messages=intake_grounding.source_correction_messages(source_plan,checked)
                                    patch,patch_id=await source_patch_request(patch_messages,
                                        '正在按真实来源定位纠正本次正文或验收；不改详细定义',source_id)
                                    body_source_review['history'].append(dict(call_id=patch_id,kind='patch',response=copy.deepcopy(patch)))
                                    save_body_source()
                                    corrected=intake_grounding.apply_correction(source_plan,patch,schema)
                                    evidence.value['business_source_corrected_candidate']=copy.deepcopy(corrected);evidence.save()
                                    trial,trial_report=adapt(corrected,p,excerpts,run['stage'],run.get('requirement_recommendation_scope'),
                                        draft_request=run.get('draft_request'),initial_intake_scope=False)
                                    intake_grounding.validate_source_correction(source_plan,patch,trial_report)
                                    raw_business=corrected;value=trial;business_draft_review=trial_report
                                    body_source_review.update(status='pending',corrections=source_round+1)
                                    save_body_source()
                            except (Exception,asyncio.CancelledError) as source_error:
                                body_source_review.update(status='blocked' if body_source_review['status']=='blocked' else 'pending',
                                    error=getattr(source_error,'code','CANCELLED' if isinstance(source_error,asyncio.CancelledError) else 'INTERNAL_ERROR'))
                                save_body_source()
                                if isinstance(source_error,asyncio.CancelledError):raise
                                if isinstance(source_error,Problem) and source_error.code in (
                                    'CANCELLED','BUDGET_EXHAUSTED','STALE_REVISION','DATA_AUTH_REQUIRED','BUDGET_AUTH_CHANGED','BUDGET_AUTH_REQUIRED'):
                                    raise
                                raise Problem('BUSINESS_SOURCE_UNVERIFIED' if body_source_review['status']=='pending' else 'BUSINESS_SOURCE_BLOCKED',
                                    '本次正文与验收来源核对未通过，原内容保留；未重生成主稿。',model_repair_allowed=False) from source_error
                        from . import detail_semantics
                        if config.get('detail_correction_protocol_version')==2:
                            from . import detail_preflight
                            local_findings=detail_preflight.prepare(raw_business,business_draft_review,p,excerpts,
                                run['stage'],run.get('requirement_recommendation_scope'),draft_request=run.get('draft_request'),
                                graph_kind_protocol=config.get('detail_local_graph_kind_protocol'))
                            if local_findings:
                                local_audit=dict(protocol='detail-local-property-binding-1',original_hash=raw_hash,
                                    findings=copy.deepcopy(local_findings),status='pending')
                                evidence.value['detail_preflight']=local_audit
                                evidence.save()
                                try:
                                    patch_schema,patch_messages=detail_semantics.correction_messages(raw_business,
                                        dict(correction_protocol=2),local_findings,excerpts)
                                    patch,patch_id=await auxiliary(patch_messages,
                                        '正在核对局部技术绑定；仅纠正明确定位的属性，尚非业务通过',
                                        'detail-local-property-binding-1',call_id)
                                    repair_count+=1
                                    local_audit['call_id']=patch_id
                                    assembly=[];normalizations=[]
                                    try:
                                        corrected=detail_semantics.apply_correction(raw_business,patch,patch_schema,local_findings,
                                            protocol_version=2,patch_audit=assembly,normalization_audit=normalizations)
                                    finally:
                                        auxiliary_evidence[patch_id].value['detail_correction_patch_assembly']=assembly
                                        auxiliary_evidence[patch_id].value['detail_correction_normalizations']=normalizations
                                        auxiliary_evidence[patch_id].save()
                                    trial,trial_report=adapt(corrected,p,excerpts,run['stage'],
                                        run.get('requirement_recommendation_scope'),draft_request=run.get('draft_request'),initial_intake_scope=header.get('initial_intake',False))
                                    require(detail_preflight.accepted(trial_report,trial,local_findings),
                                        'DETAIL_CORRECTION_INVALID','局部属性纠正后仍未通过完整结构、引用、来源或拓扑核对。')
                                    raw_business=corrected;value=trial;business_draft_review=trial_report
                                    local_audit.update(status='corrected_pending_semantic_review',corrected_hash=digest(corrected))
                                    evidence.value['corrected_business_candidate']=copy.deepcopy(corrected)
                                except Problem as error:
                                    local_audit.update(status='failed',code=error.code,reason=error.message)
                                    if error.code in ('CANCELLED','STALE_REVISION','DATA_AUTH_REQUIRED','BUDGET_AUTH_CHANGED','BUDGET_AUTH_REQUIRED'):raise
                                    events.append(dict(time=now(),phase='局部技术绑定纠正未完成：'+error.message,call=calls))
                                except asyncio.CancelledError:
                                    local_audit.update(status='cancelled',code='CANCELLED',reason='任务已取消，原候选保留。')
                                    raise
                                finally:evidence.save()
                                business_draft_review['detail_preflight']=copy.deepcopy(local_audit)
                        semantic_plan=detail_semantics.prepare(raw_business,value,business_draft_review,excerpts,
                            project=p,generation_context=assembled_context,protocol_version=config.get('detail_review_protocol_version',2),recovery_guard=config.get('detail_recovery_guard',False),correction_protocol=config.get('detail_correction_protocol_version',1),recovery_outcome_checks=config.get('recovery_outcome_checks',False),reference_encoding=config.get('detail_property_reference_encoding'),unknown_scope=config.get('detail_unknown_scope_protocol'),default_scope=config.get('detail_default_scope_protocol'))
                        if semantic_plan:
                            semantic_history=[]
                            outcomes=detail_semantics.pending(semantic_plan,'DETAIL_REVIEW_PENDING','详细定义尚待来源语义核对。')
                            try:
                                outcomes,review_id=await review_detail(semantic_plan,
                                    '正在逐操作核对流程结果、角色、状态和来源',call_id)
                                semantic_history.append(dict(call_id=review_id,outcomes=copy.deepcopy(outcomes)))
                                business_corrections=0
                                while any(r['status']=='failed' for r in outcomes.values()) and business_corrections<(2 if bounded_detail else 1):
                                    scope_feedback=detail_semantics.scope_correction_feedback(outcomes)
                                    candidate_outcomes=detail_semantics.candidate_correction_outcomes(outcomes)
                                    scope_only=bool(scope_feedback) and not any(r['status']=='failed' for r in candidate_outcomes.values())
                                    if bounded_detail:available_calls(2 if scope_only else 3)
                                    elif business_corrections:available_calls(2)
                                    business_corrections+=1
                                    if scope_only:
                                        prior_review_id=review_id
                                        outcomes,review_id=await review_detail(semantic_plan,
                                            '重新完整核对范围分类依据；原候选值及未知状态保持',prior_review_id,scope_feedback)
                                        semantic_history.append(dict(call_id=review_id,correction_kind='scope_review_evidence',
                                            correction_number=business_corrections,previous_review_call_id=prior_review_id,
                                            candidate_unchanged=True,outcomes=copy.deepcopy(outcomes)))
                                        continue
                                    correction_schema,correction_messages=detail_semantics.correction_messages(raw_business,semantic_plan,candidate_outcomes,excerpts)
                                    patch,correction_id=await auxiliary(correction_messages,'正在按已有依据纠正本次详细定义；不新增业务决定',
                                        'detail-property-correction-2' if semantic_plan.get('correction_protocol',1)==2 else 'detail-source-correction-1',review_id)
                                    repair_count+=1
                                    correction_normalizations=[];patch_assembly=[]
                                    try:
                                        corrected=detail_semantics.apply_correction(raw_business,patch,correction_schema,candidate_outcomes,
                                            normalization_audit=correction_normalizations,
                                            protocol_version=semantic_plan.get('correction_protocol',1),patch_audit=patch_assembly)
                                    finally:
                                        if patch_assembly:
                                            auxiliary_evidence[correction_id].value['detail_correction_patch_assembly']=copy.deepcopy(patch_assembly)
                                            auxiliary_evidence[correction_id].save()
                                        if correction_normalizations:
                                            auxiliary_evidence[correction_id].value['detail_correction_normalizations']=copy.deepcopy(correction_normalizations)
                                            auxiliary_evidence[correction_id].save()
                                    trial,trial_report=adapt(corrected,p,excerpts,run['stage'],run.get('requirement_recommendation_scope'),draft_request=run.get('draft_request'),initial_intake_scope=header.get('initial_intake',False))
                                    rejection_details=[]
                                    for rejected in [*trial_report.get('rejected',[]),*trial_report.get('field_rejected',[])]:
                                        issues=rejected.get('issues') or [dict(code=rejected.get('code'),path=rejected.get('path'),message=rejected.get('reason'))]
                                        rejection_details.extend(dict(row_key=rejected.get('row_key'),**copy.deepcopy(issue)) for issue in issues)
                                    auxiliary_evidence[correction_id].value['detail_correction_validation']=dict(
                                        corrected_candidate_hash=digest(corrected),adapt_report=copy.deepcopy(trial_report),
                                        rejection_details=rejection_details,status='checking')
                                    auxiliary_evidence[correction_id].save()
                                    revised_plan=detail_semantics.prepare(corrected,trial,trial_report,excerpts,
                                        project=p,generation_context=assembled_context,protocol_version=config.get('detail_review_protocol_version',2),recovery_guard=config.get('detail_recovery_guard',False),correction_protocol=config.get('detail_correction_protocol_version',1),recovery_outcome_checks=config.get('recovery_outcome_checks',False),reference_encoding=config.get('detail_property_reference_encoding'),unknown_scope=config.get('detail_unknown_scope_protocol'),default_scope=config.get('detail_default_scope_protocol'))
                                    complete=revised_plan is not None and {r['row_key'] for r in revised_plan['rows']}==set(outcomes)
                                    auxiliary_evidence[correction_id].value['detail_correction_validation']['status']='passed_pending_full_semantic_review' if complete else 'rejected'
                                    auxiliary_evidence[correction_id].save()
                                    first=rejection_details[0] if rejection_details else None
                                    reason=('首项：'+str(first.get('code') or '')+' '+str(first.get('path') or '')+' '+str(first.get('message') or '')[:220]
                                        if first else '未完整通过原结构/引用/拓扑校验')
                                    require(complete,'DETAIL_CORRECTION_INVALID','纠正后的详细定义未采用；'+reason+'。原候选、补丁和诊断已保留。')
                                    raw_business=corrected
                                    semantic_plan=revised_plan
                                    outcomes=detail_semantics.pending(semantic_plan,'DETAIL_REVIEW_PENDING','纠正候选尚待完整复审。')
                                    outcomes,review_id=await review_detail(semantic_plan,
                                        '正在完整复核纠正后的所有操作与来源',correction_id,scope_feedback)
                                    semantic_history.append(dict(call_id=review_id,outcomes=copy.deepcopy(outcomes)))
                            except Problem as error:
                                if error.code in ('CANCELLED','STALE_REVISION','DATA_AUTH_REQUIRED','BUDGET_AUTH_CHANGED','BUDGET_AUTH_REQUIRED'):raise
                                outcomes={key:dict(result,status='pending' if result['status']!='failed' else 'failed',
                                    code=error.code,reason=(result['reason']+' 后续核对未完成：'+error.message)[:1800]) for key,result in outcomes.items()}
                                events.append(dict(time=now(),phase='详细定义核对未完成：'+error.message,call=calls))
                            for business_row in raw_business['requirements']:
                                if business_row['row_key'] in outcomes:
                                    outcomes[business_row['row_key']].update(raw_candidate_hash=digest(business_row['detail_contract']),
                                        input_revision=p['revision'])
                            value,business_draft_review=adapt(raw_business,p,excerpts,run['stage'],run.get('requirement_recommendation_scope'),
                                draft_request=run.get('draft_request'),detail_semantic_reviews=outcomes,initial_intake_scope=header.get('initial_intake',False))
                            business_draft_review['detail_semantic_reviews']=copy.deepcopy(outcomes)
                            business_draft_review['detail_semantic_history']=semantic_history
                            evidence.value['detail_semantic_review']=dict(protocol=semantic_plan['version'],
                                original_hash=raw_hash,reviewed_hash=digest(raw_business),history=semantic_history,outcomes=copy.deepcopy(outcomes))
                            if digest(raw_business)!=raw_hash:evidence.value['corrected_business_candidate']=copy.deepcopy(raw_business)
                        if evidence.value.get('detail_preflight'):
                            business_draft_review['detail_preflight']=copy.deepcopy(evidence.value['detail_preflight'])
                        if body_source_review is not None:
                            require(body_source_review['status']=='passed' and body_source_review['reviewed_fields_hash']==
                                digest(intake_grounding.source_plan(raw_business,assembled_context)['fields']),
                                'BUSINESS_SOURCE_BLOCKED','后续处理改变了已核对正文或验收，未采用候选。')
                            business_draft_review['business_source_review']=copy.deepcopy(body_source_review)
                            business_draft_review['source_validation']='body_source_semantic_checked_not_human_approval'
                        business_draft_review.update(run_id=rid, input_revision=p['revision'], raw_response_hash=raw_hash)
                        evidence.value['business_draft_review'] = copy.deepcopy(business_draft_review)
                        evidence.save()
                        self.store.update_run(pid, rid, business_draft_review=business_draft_review)
                    if run['stage'] in ('ingest', 'clarify', 'change'):
                        from .response_normalization import normalize as normalize_response_slots
                        original_hash = digest(value)
                        value, slot_changes = normalize_response_slots(value, p, excerpts, run['stage'])
                        if slot_changes:
                            normalization = dict(rule='evidence-bound-response-slots-1', input_hash=original_hash,
                                output_hash=digest(value), changes=slot_changes)
                            evidence.value.setdefault('response_normalizations', []).append(normalization)
                            evidence.save()
                            attempt.setdefault('response_normalizations', []).append(normalization)
                            events.append(dict(time=now(), phase='已在本机校正临时编号与可唯一核对的引用，未重跑模型；业务内容保留', call=calls))
                            self.store.update_run(pid, rid, events=events)
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
                        original_hash = digest(value)
                        value, classifications = normalize_classification_labels(value, excerpts)
                        if classifications:
                            normalization = dict(rule='source-verified-change-classification-1',
                                input_hash=original_hash, output_hash=digest(value), changes=classifications)
                            evidence.value.setdefault('response_normalizations', []).append(normalization)
                            evidence.save()
                            attempt.setdefault('response_normalizations', []).append(normalization)
                            events.append(dict(time=now(), phase='按已引用的现状与改动证据统一变化分类；业务原文与来源保持不变', call=calls))
                            self.store.update_run(pid,rid,classification_corrections=classifications,events=events)
                    if run['stage']=='prd':
                        from .prd import normalize_structured_explanations, preserve_repair_explanation_refs
                        raw_prd_plan=copy.deepcopy(value)
                        if prd_repair_original is not None:
                            value,ref_changes=preserve_repair_explanation_refs(prd_repair_original,value,p,run['document_type'])
                            if ref_changes:
                                normalization=dict(rule='prd-unchanged-paragraph-reference-preservation-1',
                                    original_plan_hash=digest(prd_repair_original),input_hash=digest(raw_prd_plan),
                                    output_hash=digest(value),changes=ref_changes)
                                evidence.value.setdefault('response_normalizations',[]).append(normalization)
                                evidence.value['normalized_prd_plan']=copy.deepcopy(value)
                                evidence.save()
                                attempt.setdefault('response_normalizations',[]).append(normalization)
                        original_hash=digest(value)
                        value,layout_changes=normalize_structured_explanations(value,p,run['document_type'])
                        if layout_changes:
                            normalization=dict(rule='program-rendered-detail-no-duplicate-explanation-1',
                                input_hash=original_hash,output_hash=digest(value),changes=layout_changes)
                            evidence.value.setdefault('response_normalizations',[]).append(normalization);evidence.save()
                            attempt.setdefault('response_normalizations',[]).append(normalization)
                            events.append(dict(time=now(),phase='按已核对条款生成完整正文，重复解释保留在任务记录',call=calls))
                        document_plan_version = value.get('plan_version')
                        for semantic_attempt in range(3):
                            try:
                                compiled=compile_plan(value,p,run['document_type'],omitted,include_sketch=False)
                                value=compiled
                                self.store.update_run(pid,rid,prd_semantic_pending=None,pending_prd_plan=None)
                                break
                            except Problem as semantic_error:
                                from .prd import semantic_correction,apply_semantic_correction
                                prepared=semantic_correction(value,semantic_error,p,run['document_type'])
                                if prepared is not None:
                                    self.store.update_run(pid,rid,pending_prd_plan=copy.deepcopy(value),
                                        prd_semantic_pending=dict(path=semantic_error.semantic_path,
                                            findings=copy.deepcopy(prepared['findings']),status='needs_correction'),result_applied=False)
                                if prepared is None or semantic_attempt==2:raise
                                patch,patch_id=await auxiliary(prepared['messages'],
                                    '正在按真实依据纠正成文解释；规范原文保持不变','prd-explanation-semantic-correction-1',call_id)
                                repair_count+=1
                                original_plan=copy.deepcopy(value)
                                value=apply_semantic_correction(value,prepared,patch)
                                evidence.value.setdefault('prd_semantic_corrections',[]).append(dict(call_id=patch_id,
                                    path=semantic_error.semantic_path,findings=prepared['findings'],
                                    original=prepared['original'],replacement=copy.deepcopy(patch['explanation']),
                                    original_hash=digest(original_plan),output_hash=digest(value)))
                                evidence.value['normalized_prd_plan']=copy.deepcopy(value)
                                evidence.save()
                    if run.get('prewrite'):
                        from .prewrite import normalize_quotes, normalize_decisions
                        value,decision_changes=normalize_decisions(p,value)
                        if decision_changes:
                            normalization=dict(rule='prewrite-exact-required-decision-binding-1',changes=decision_changes)
                            evidence.value.setdefault('response_normalizations',[]).append(normalization)
                            evidence.save()
                            attempt.setdefault('response_normalizations',[]).append(normalization)
                        original_hash=digest(value)
                        value,quote_changes=normalize_quotes(p,value)
                        if quote_changes:
                            normalization=dict(rule='prewrite-verified-quotes-2',
                                input_hash=original_hash,output_hash=digest(value),changes=quote_changes)
                            evidence.value.setdefault('response_normalizations',[]).append(normalization)
                            evidence.save()
                            attempt.setdefault('response_normalizations',[]).append(normalization)
                            events.append(dict(time=now(),phase='已核对检查引文；无法核实的非阻塞提示明确标注，原输出保留',call=calls))
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
                        if repair_anchor is not None:
                            ingest.preserve(repair_anchor, value, excerpts)
                            if run.get('benchmark'):
                                from .benchmark import preserve_analysis
                                preserve_analysis(repair_original,value,p,excerpts)
                    try:
                        validate_response(value, run['stage'], reviewed_project, excerpts, run['document_type'],
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
                    if run.get('prewrite'):
                        from .prewrite import validate_result
                        require(not omitted,'PREWRITE_CONTEXT_INCOMPLETE','成文检查的来源未完整输入，本次不能视为通过。')
                        validate_result(p,value)
                    if interview_anchor is not None:
                        from .interview import format_atoms
                        require(format_atoms(json.dumps(value,ensure_ascii=False))==interview_anchor,
                                'SEMANTIC_BLOCKED','访谈格式修复改变了原有字面内容或顺序；原始回复保留，未保存改写后的聊天。')
                    if repair_anchor is not None and run['stage'] in ('clarify','change','brainstorm','review'):
                        reference_normalizations=ingest.preserve_response(repair_anchor,value,excerpts,run['stage'],
                            original=repair_original,project=p,document_target=document_reference_repair_target)
                        if reference_normalizations:
                            audit=dict(input_hash=digest(repair_original),output_hash=digest(value),changes=reference_normalizations)
                            evidence.value['reference_repair_normalization']=copy.deepcopy(audit)
                            attempt['reference_repair_normalization']=copy.deepcopy(audit)
                            evidence.save()
                    if run['stage']=='interview' and 'context_draft' in value:
                        from .interview_context import assess
                        interview_context_review=assess(value['context_draft'],p,excerpts)
                        evidence.value['interview_context_review']=copy.deepcopy(interview_context_review)
                        attempt['interview_context_review']=copy.deepcopy(interview_context_review)
                        evidence.save()
                    evidence.finish('accepted', round(time.monotonic()-call_started, 3))
                    attempt['validation_result']='accepted'
                    attempt['evidence_limitations']=evidence.limitations[:]
                    if not resume_packet:attempts.append(attempt)
                    attempts.sort(key=lambda a:a['call'])
                    break
                except (Problem, asyncio.TimeoutError) as e:
                    if budget:calls=self.store.get_record(pid,rid,'run')['calls']
                    if isinstance(e, asyncio.TimeoutError):
                        e = Problem('TIMEOUT', '单次模型请求超时；已发请求可能计费')
                    evidence.finish(e.code, round(time.monotonic()-call_started, 3))
                    attempt.update(error=e.code, validation_result=e.code,
                                   usage=evidence.value.get('usage'),finish_reason=evidence.value.get('finish_reason'),
                                   evidence_limitations=evidence.limitations[:])
                    if not resume_packet:attempts.append(attempt)
                    attempts.sort(key=lambda a:a['call'])
                    self.store.update_run(pid, rid, attempts=attempts)
                    if resume_packet or use_dsh:
                        # Harness owns its loop; do not stack the direct provider's
                        # retry/repair loop onto a second runtime session.
                        raise e
                    # One extra request may fix punctuation in a complete output.
                    # It uses the original schema/context, never the legacy database
                    # repair prompt for a business-only packet. No recursive retry.
                    if syntax_anchor is not None:
                        raise e
                    if business_reference_repair is not None:
                        raise e
                    if e.code=='BUSINESS_REFERENCE_INVALID' and business_reference_plan and repair_count==0:
                        from .business_reference_repair import messages as business_ref_messages, SCHEMA as business_ref_schema
                        business_reference_repair=business_reference_plan
                        messages=business_ref_messages(business_reference_repair)
                        pending_repair=True;repair_parent=call_id
                        authorization=dict(authorization,repair_protocol='business-reference-patch-1',
                            repair_original_hash=digest(value),schema_hash=digest(business_ref_schema),
                            prompt_hash=digest(messages[0]['content'].split('可信任务头：',1)[0]))
                        self.store.update_run(pid,rid,repair_anchor_hash=digest(value))
                        events.append(dict(time=now(),phase='正在核对并修复错误来源编号，业务原文和有效引用保持不变',call=calls))
                        continue
                    if prewrite_repair is not None:
                        # A failed slot repair is never recursively rewritten.
                        raise e
                    if run.get('prewrite') and repair_count==0 and e.model_repair_allowed and e.code in (
                            'REFERENCE_INVALID','PREWRITE_DECISION_BINDING_REQUIRED'):
                        from .prewrite_repair import prepare as prepare_review_patch, messages as review_patch_messages, SCHEMA as review_patch_schema
                        prewrite_repair=prepare_review_patch(p,value)
                        if prewrite_repair:
                            messages=review_patch_messages(prewrite_repair)
                            pending_repair=True;repair_parent=call_id
                            authorization=dict(authorization,repair_protocol='prewrite-metadata-patch-1',
                                repair_original_hash=digest(value),schema_hash=digest(review_patch_schema),
                                prompt_hash=digest(messages[0]['content'].split('可信任务头：',1)[0]))
                            self.store.update_run(pid,rid,repair_anchor_hash=digest(value))
                            events.append(dict(time=now(),phase='正在修复检查问题的关联与引文，保留原问题和修改建议',call=calls))
                            continue
                    if run['stage']!='interview' and e.code=='SCHEMA_INVALID' and e.model_repair_allowed and repair_count==0:
                        from .format_repair import anchor as format_anchor, instruction as format_instruction
                        syntax_candidate=format_anchor(evidence)
                        if syntax_candidate is not None and (business_protocol or value is None):
                            syntax_anchor=syntax_candidate
                            pending_repair=True;repair_parent=call_id
                            self.store.update_run(pid,rid,repair_anchor_hash=digest(syntax_anchor))
                            messages=messages[:2]+[dict(role='user',content=format_instruction(e.message,evidence.value['final_output']))]
                            events.append(dict(time=now(),phase='追加一次格式修复，保留原始内容与引用；修复后重新校验',call=calls))
                            continue
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
                        if (run['stage']=='prd' and run['document_type']=='prd' and prd_repair_original is None
                            and isinstance(raw_prd_plan,dict)):
                            prd_repair_original=copy.deepcopy(raw_prd_plan)
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
                        if e.code=='REFERENCE_INVALID' and run['stage']=='change' and (run.get('discussion_target') or {}).get('kind')=='document':
                            from .request_context import document_discussion_contract
                            document_reference_repair_target=copy.deepcopy(run['discussion_target'])
                            repair+='\n本次章节讨论引用边界：'+dumps(document_discussion_contract(p,run['discussion_target']))
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
                    if not resume_packet:attempts.append(attempt)
                    attempts.sort(key=lambda a:a['call'])
                    raise Problem('CANCELLED','任务已取消；本地已停止等待，接收端已发请求仍可能计费')
                except Exception:
                    if budget:calls=self.store.get_record(pid,rid,'run')['calls']
                    evidence.finish('INTERNAL_ERROR', round(time.monotonic()-call_started, 3))
                    attempt.update(error='INTERNAL_ERROR', validation_result='INTERNAL_ERROR',
                                   evidence_limitations=evidence.limitations[:])
                    if not resume_packet:attempts.append(attempt)
                    attempts.sort(key=lambda a:a['call'])
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
                if run['message'].strip() and not run.get('benchmark') and not resume_packet:
                    staged['messages'].append(dict(role='user',stage=run['stage'],text=run['message'],created=now()))
                draft_mapping = apply_response(staged, value, identity_only=business_protocol,
                    preserve_context_proposal=preserve_context_proposal)
                if not business_protocol:
                    from .decision_run import review_generated_draft
                    calls, decision_review = await review_generated_draft(self.store,self.provider,p,staged,
                        run,config,excerpts,budget,attempts,events,calls)
            has_context_fields = bool(interview_context_review and interview_context_review['fields'])
            with self.store.edit(pid, p['revision'], '模型：' + run['stage'], bump=bool(staged or value['proposals'] or value['questions'] or has_context_fields or run.get('prewrite') or (run.get('draft_request') or {}).get('mode')=='prewrite')) as (current, db):
                state=next(x for x in self.store.records(pid,'run',db) if x['id']==rid)
                require(state['status']!='cancelled','CANCELLED','已取消，结果未采纳')
                require(execution_hash(current)==execution_hash(p),'STALE_REVISION','任务执行期间输入或产物发生变化；结果已保留在调用证据，未激活',409)
                if run['stage']=='review':
                    require(review_target(current)==review_target(p),'STALE_REVISION','审查期间文档或页面已改变，请重新审查',409)
                if staged is None and not resume_packet and run['message'].strip() and not run.get('benchmark') and not (run['stage']=='interview' and run.get('user_task_id')):
                    current['messages'].append(dict(role='user', stage=run['stage'], text=run['message'], created=now()))
                candidate_count=len(current.get('ui_candidates', []))
                if staged is None:
                    if run.get('prewrite'):
                        from .prewrite import apply_result
                        prior_rid=(p.get('prewrite_review') or {}).get('run_id')
                        previous_response=next((r.get('response') for r in self.store.records(pid,'run',db)
                            if r['id']==prior_rid and r.get('result_applied')),None)
                        apply_result(current,value,rid,previous_response=previous_response,
                            previous_review=p.get('prewrite_review'))
                        current['messages'].append(dict(role='assistant',stage='prewrite',text=value['summary'],created=now(),run_id=rid))
                        draft_mapping={}
                    else:
                        draft_mapping = apply_response(current, value, identity_only=business_protocol,
                            preserve_context_proposal=preserve_context_proposal)
                else:
                    current.clear()
                    current.update(staged)
                if business_draft_review is not None:
                    # Keep rejected rows inspectable; they never enter items or
                    # any confirmation request. Accepted rows remain candidates.
                    saved_review = replace_business_ids(business_draft_review, draft_mapping)
                    if saved_review.get('intake_grounding'):
                        saved_review['intake_grounding']['candidate_applied']=True
                    from .detail_business_scope import bind_stored_reviews
                    bind_stored_reviews(saved_review,value,draft_mapping,current)
                    current.setdefault('business_draft_reviews', []).append(saved_review)
                    if run.get('draft_request'):
                        previous=copy.deepcopy(current.get('refinement_completion') or {})
                        result=copy.deepcopy(saved_review.get('completion',{}))
                        single=run['draft_request']['mode']=='question'
                        row_ids={row['row_key']:row['requirement_id'] for row in saved_review['accepted']}
                        if not single:
                            result['change_types'].update({row_ids[key]:kind for key,kind in result.pop('change_type_rows',{}).items() if key in row_ids})
                            result['scope_ids']=list(dict.fromkeys(result['scope_ids']+[row_ids[key] for key in result.pop('scope_rows',[]) if key in row_ids]))
                        answers={**previous.get('answers',{}),**result.pop('answers',{})}
                        if single:result={key:val for key,val in result.items() if key not in ('profile','scope_ids','change_types')}
                        current['refinement_completion']=dict(previous if single else {},**result,answers=answers,
                            mode=previous.get('mode','question') if single else run['draft_request']['mode'],
                            last_mode=run['draft_request']['mode'],run_id=rid,input_revision=p['revision'],
                            candidate_ids=previous.get('candidate_ids',[]) if single else [item for row in saved_review['accepted'] for item in row.get('candidate_temp_ids',[])])
                        if run['draft_request']['mode']=='prewrite':
                            current['refinement_completion'].update(applied_revision=current['revision']+1,
                                covered_requirement_ids=[row['requirement_id'] for row in saved_review['accepted']],
                                complete=saved_review['complete'])
                    if run['stage'] == 'ingest':
                        # Empty protocol fields and rejected/unknown suggestions
                        # cannot erase prior drafts or borrow their citations.
                        summaries = copy.deepcopy(p.get('product_context_proposal') or {})
                        summary_evidence = copy.deepcopy(p.get('product_context_proposal_evidence') or {})
                        for field, row in saved_review.get('summary_fields', {}).items():
                            saved = str((p.get('product_context') or {}).get(field, ''))
                            state = (p.get('context_field_states') or {}).get(field, {}).get('status')
                            text = value.get('product_context_proposal', {}).get(field, '')
                            if row['basis'] == 'unknown':
                                from .scope_context import context_review_statuses
                                prior=summary_evidence.get(field) or {}
                                # Replace only an identifiable, still-unreviewed AI baseline.
                                # Empty unknowns and human decisions continue to preserve prior content.
                                if not (field=='current_state' and str(row.get('text','')).strip()
                                        and prior.get('evidence_status')=='unreviewed' and prior.get('run_id')
                                        and prior.get('text')==summaries.get(field) and not saved.strip()
                                        and state in (None,'unknown') and not context_review_statuses(p).get(field)):
                                    continue
                                text=row['text']
                            summaries[field] = text
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
                    if run['document_type']=='prd':artifact['prewrite_required']=True
                    from .detail_business_scope import current_reviews as current_detail_scopes
                    scoped_details=[review for item in delivery_items(current) if item.get('kind')=='requirement' and not item.get('target_item_id') for review in current_detail_scopes(current,item)] if run['document_type']=='prd' else []
                    if scoped_details:artifact['detail_unknown_scope_reviews']=scoped_details
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
                    self.store.record(pid,'prewrite_review' if run.get('prewrite') else 'review',
                        current['prewrite_review'] if run.get('prewrite') else current['review'],db=db)
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
            if business_draft_review is not None and business_draft_review.get('intake_grounding'):
                business_draft_review['intake_grounding']['candidate_applied']=True
                evidence.value['intake_grounding']=copy.deepcopy(business_draft_review['intake_grounding'])
                evidence.value['business_draft_review']=copy.deepcopy(business_draft_review)
                evidence.save()
                self.store.update_run(pid,rid,intake_grounding=copy.deepcopy(business_draft_review['intake_grounding']),
                    business_draft_review=copy.deepcopy(business_draft_review))
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
            if run.get('prewrite') and current['prewrite_review']['status']!='passed':status='awaiting_user'
            if interview_context_review is not None:
                self.store.update_run(pid,rid,interview_context_review=interview_context_review)
                if interview_context_review['rejected_fields']:
                    status='partial'
                    events.append(dict(time=now(),phase=interview_context_review['limitations'][0]))
            self.store.update_run(pid, rid, status=status, calls=calls, attempts=attempts, completed=now(), response=value, result_applied=True, cost=cost, repair_count=repair_count, output_state_hash=execution_hash(output_state), events=events + [dict(time=now(),phase='校验并保存')])
        except asyncio.CancelledError:
            if budget:calls=self.store.get_record(pid,rid,'run')['calls']
            self.store.update_run(pid,rid,status='cancelled',error='CANCELLED',message='任务已取消；未采纳后续结果',calls=calls,attempts=attempts,events=events,completed=now())
            if run.get('prewrite'):self._fail_prewrite(pid,rid,'检查已取消，尚未通过。')
        except Exception as e:
            if budget:calls=self.store.get_record(pid,rid,'run')['calls']
            error = e if isinstance(e, Problem) else Problem('INTERNAL_ERROR', '处理失败：' + type(e).__name__)
            state = 'cancelled' if error.code == 'CANCELLED' else 'paused_budget' if error.code == 'BUDGET_EXHAUSTED' else 'failed'
            self.store.update_run(pid, rid, status=state, error=error.code, message=error.message, calls=calls, attempts=attempts, repair_count=repair_count, events=events, completed=now())
            if run.get('prewrite'):self._fail_prewrite(pid,rid,error.message)

    def _fail_prewrite(self,pid,rid,message):
        latest=self.store.get(pid)
        with self.store.edit(pid,latest['revision'],'成文检查未完成',bump=False) as (current,db):
            report=current.get('prewrite_review') or {}
            if report.get('run_id')==rid:report.update(status='failed',message=message)

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
