"""Small, explicit business actions over existing runs; no autonomous routing."""
import asyncio
import copy
import math

from .core import Problem, digest, dumps, execution_hash, ident, now, require
from .provider import origin
from .sources import save_source
from .product_flow import execution_issues, question_open
from .budgets import policy_summary
from .request_budget import validate_limit, initial_budget, check_available

LABELS = {'interview':'聊清需求想法','organize':'整理当前信息','explore':'探索可选方案','clarify':'根据回答更新理解',
          'prototype':'生成功能草图','document':'生成评审稿','review':'审查当前内容','change':'讨论修改方案','prewrite':'检查成文一致性'}
STAGES = {'interview':['interview'],'explore':['brainstorm'],'clarify':['clarify'],'prototype':['ui'],
          'document':['prd'],'review':['review'],'change':['change'],'prewrite':['review']}
ACTIVE = ('queued','running')


def target_context(p, target):
    """Resolve the local helper against this snapshot, never a client supplied label."""
    if not target:return ''
    require(set(target)<= {'kind','id','section_id','detail_required'},'TARGET_INVALID','不支持的修改对象字段')
    if target.get('detail_required') is not None:
        require(type(target['detail_required']) is bool and target.get('kind')=='item',
                'TARGET_INVALID','详细定义生成意图只适用于需求行，且须为布尔值')
    kind=target.get('kind');oid=target.get('id')
    if kind in ('item','question'):
        item=next((x for x in p['items' if kind=='item' else 'questions'] if x['id']==oid),None)
        require(item is not None,'TARGET_INVALID','此需求或问题已不存在',409)
        if target.get('detail_required') is not None:
            require(item.get('kind')=='requirement' and not item.get('target_item_id'),
                    'TARGET_INVALID','请从原需求行发起详细定义建议',409)
        label=item.get('title') or item.get('question')
        return f'{oid}｜{label}（当前底稿 v{p["revision"]}）'
    if kind=='ui':
        require(p.get('ui') and oid==digest(p['ui']['spec']),'TARGET_INVALID','草图已变化，请重新打开局部修改',409)
        return f'当前功能草图｜{p["ui"]["spec"]["title"]}（内容标识 {oid}）'
    if kind=='document':
        doc=next((d for d in p['documents'].values() if d['id']==oid),None)
        require(doc is not None,'TARGET_INVALID','文档已变化，请重新打开章节',409)
        section=next((s for s in doc['content']['sections'] if s['section_id']==target.get('section_id')),None)
        require(section is not None,'TARGET_INVALID','章节不属于当前文档',409)
        return f'{oid}｜{section["section_id"]}｜{section["title"]}（修改涉及业务含义时必须提出需求候选，不直接改文档）'
    raise Problem('TARGET_INVALID','请选择实际需求、问题、草图或文档章节')


class Actions:
    def __init__(self, store, workflow):
        self.store, self.workflow = store, workflow
        self.jobs = {}

    def plan(self, pid, body, p=None, config_snapshot=None):
        p = p or self.store.get(pid)
        require(p['revision']==body['expected_revision'],'STALE_REVISION','内容版本已变化，请保留输入并重新核对',409)
        intake_resume=None
        if body.get('intake_resume_task_id'):
            from .ingest_recovery import derive_intake_resume
            intake_resume=derive_intake_resume(self.store,p,body['intake_resume_task_id'],body)['identity']
        require(body['action'] in LABELS,'ACTION_INVALID','业务动作不存在')
        from .refinement_completion import validate as validate_draft_request
        draft_request=validate_draft_request(p,body.get('draft_request'))
        require(not draft_request or body['action']=='organize' and not body.get('target') and not body.get('benchmark'),
                'DRAFT_REQUEST_INVALID','补全草稿请从完善需求表格发起')
        benchmark=body.get('benchmark')
        if benchmark:
            require(body['action']=='organize' and not body.get('target'), 'BENCHMARK_INVALID', '对标请通过需求助手的草稿填充入口发起')
            from .benchmark import validate_request
            benchmark=validate_request(p,benchmark)
        attachments=body.get('attachment_source_ids') or []
        require(not attachments or body['action']=='interview', 'ATTACHMENT_INVALID','附件请通过需求助手发送')
        require(len(attachments)==len(set(attachments)), 'ATTACHMENT_INVALID','附件重复，请核对后再发送')
        eligible={s['id'] for s in p['sources'] if (s.get('image_mime') or s.get('origin')=='interview_attachment') and not s.get('excluded')}
        require(set(attachments)<=eligible, 'ATTACHMENT_INVALID','附件已被排除或不属于当前项目，请重新选择',409)
        require(body['action']!='interview' or body['message'].strip() or attachments,'INPUT_REQUIRED','请先发一条消息或添加资料，零碎的想法也可以。')
        target_label=target_context(p,body.get('target'))
        require((body.get('target') or {}).get('detail_required') is None or body['action']=='clarify',
                'TARGET_INVALID','详细定义生成意图仅用于完善此需求')
        require(not target_label or body['action'] in ('clarify','change','prototype'), 'TARGET_INVALID','此动作不支持局部修改')
        from .domain_context import active_source_ids, attachment_source_ids
        active=[s for s in p['sources'] if s['id'] in active_source_ids(p) | attachment_source_ids(p,attachments)]
        images=[s for s in active if s.get('image_mime') and not s.get('vision_run_id')]
        stages=STAGES.get(body['action']) or ['vision']*math.ceil(len(images)/3) + ['ingest']
        if intake_resume:stages=['ingest']
        reuse_prewrite=False
        if body['action']=='document' and body['document_type']=='prd':
            from .prewrite import status as prewrite_status
            checked=prewrite_status(p)
            reuse_prewrite=not body.get('message','').strip() and checked['current'] and (
                checked['status']=='passed' or body.get('allow_review_draft') and checked['status']=='needs_confirmation')
            stages=['prd'] if reuse_prewrite else ['review','prd']
        if body['action']=='interview':
            stages=['vision']*math.ceil(len(images)/3)+['interview']
        limit=validate_limit(body.get('max_calls'))
        configs={s:dict(self.workflow.config(s),max_calls=limit) for s in stages}
        if config_snapshot is not None:
            config_snapshot.update(copy.deepcopy(configs))
        blockers=[]
        for stage in stages:
            blockers.extend(execution_issues(p,stage,body['document_type'],body.get('target'),
                prewrite=body['action']=='prewrite'))
        if not any(s['excerpts'] for s in active) and not p['items'] and not body['message'].strip():
            blockers.append('请先描述目标或添加项目资料。')
        if body['action'] in ('prototype','document','review','prewrite') and not p['items']:
            blockers.append('请先整理当前信息，形成可引用的内容；不要求预先采纳全部候选。')
        if body['action']=='review' and not p['documents'].get('prd'):
            blockers.append('尚无 PRD 讨论稿，请先生成讨论稿。')
        target=None
        if body['action']=='prototype':
            selected=next((o for o in p['options'] if o.get('direction_status')=='selected'),None)
            oid=body.get('option_id') or (selected['id'] if selected else None)
            if oid and (not selected or oid!=selected['id']):
                blockers.append('本批仅支持为当前讨论方向生成原型；未选方案尚不支持独立生成，也不会改变方向。')
            target=dict(option_id=oid, input_revision=p['revision'], name=selected['name'] if selected and oid==selected['id'] else '当前项目内容')
        recipients=[]
        for stage,c in configs.items():
            endpoint=origin(c)
            if not self.workflow.provider.key(c):
                blockers.append(('图片分析' if stage=='vision' else '主分析')+'尚未配置可用 Key；可继续整理资料和查看已有内容。')
            if stage=='vision' and c['vision'] not in ('documented','verified'):
                blockers.append('图片模型能力未配置；可排除待分析图片后先整理文字。')
            if (endpoint,c['model']) not in [(r['origin'],r['model']) for r in recipients]:
                ids=[s['id'] for s in active]
                recipients.append(dict(origin=endpoint, model=c['model'], source_ids=ids,
                    needs_authorization=False))
        busy=any(r['status'] in ACTIVE for kind in ('run','user_task') for r in self.store.records(pid,kind))
        if busy:
            blockers.append('当前项目任务正在执行，请等待结果或取消当前任务。')
        result=dict(action=body['action'],label=LABELS[body['action']],stages=stages,
            expected_revision=p['revision'],max_calls=limit,recipients=recipients,
            sources=[dict(id=s['id'],title=s['title'],status=s['parse_status']) for s in active],
            pending_text=body['message'], context_scope='当前底稿、问题和讨论记录；排除材料不删除已经形成的讨论内容。',
            missing=list(dict.fromkeys(blockers)),generation_target=target,
            open_questions=sum(question_open(q) for q in p['questions']))
        result['pending_image_count']=len(images)
        result['target_label']=target_label
        if body['action']=='document' and body['document_type']=='prd':
            from .document_preview import build_prd_preview
            # Read-only local comparison. Planning a preview never calls a model.
            result['document_preview']=build_prd_preview(p)
        if draft_request:
            result['label']=('为这道问题起草回答' if draft_request['mode']=='question' else
                '一次起草成文检查全部修改' if draft_request['mode']=='prewrite' else 'AI 补全必填项')
            result['context_scope']+=' 按本次问题编号起草可直接核对的回答和必填草稿；资料未明确的选择标为建议，不保存为人工回答或确认。'
            if 'detail_required_ids' in draft_request:
                from .refinement_completion import detail_rows
                from .business_draft import _roots
                roots=_roots(p)
                result['detail_required_rows']=[dict(requirement_id=roots[key]['id'],row_key=key,title=roots[key].get('title',''))
                    for key in detail_rows(p,draft_request)]
                titles='、'.join(row['title'] or row['row_key'] for row in result['detail_required_rows'])
                result['context_scope']+=(' 本次同时起草详细定义：'+titles+'；其它行不新增详细定义。' if titles
                    else ' 本次没有启用详细定义的需求行。')
        if body['action']=='prewrite' or (body['action']=='document' and body['document_type']=='prd'):
            from .prewrite import structural_findings
            result['prewrite_local_findings']=structural_findings(p)
            result['prewrite_policy']=dict(mode='before_document',review_draft=bool(body.get('allow_review_draft')),
                stop_on_business_decisions=not body.get('allow_review_draft'),semantic_review=True,
                reused=reuse_prewrite,review_id=(p.get('prewrite_review') or {}).get('id') if reuse_prewrite else None)
            result['context_scope']+=' 先核对本期需求、字段、交互、流程、状态和验收的一致性；发现关键问题时返回完善需求处理。'
            if reuse_prewrite:result['context_scope']+=' 复用同一版本已完成的成文检查，本次不重复调用检查模型。'
            if body.get('allow_review_draft'):
                result['context_scope']+=' 已明确选择带问题评审稿：保留未决问题，不代表可正式交付。检查失败或结构损坏仍停止。'
        from .requirement_recommendation import scope as recommendation_scope
        local = recommendation_scope(p, body.get('target'), body['action'])
        if local:
            result.update(label='完善这条需求建议', requirement_recommendation_scope=local)
            result['context_scope'] += ' 仅为“'+local['title']+'”整理正文、适用业务说明和验收建议；所有建议待人工确认，不改其他需求与共享条款。'
            if local.get('detail_required'):
                result['context_scope'] += ' 本次同时起草该需求的结构化详细定义，并在同一请求额度内核对来源；生成结果仍为待核对候选。'
        if p.get('workflow_mode')=='shared-draft' and any(stage in ('ingest','clarify','change') for stage in stages):
            result['context_scope']+=' 新增或变化的问题及当前回答将额外进行一次批量覆盖复核，沿用同一接收端和总请求额度；复核仅提供待人工确认的建议。'
            result['decision_review_policy']=dict(mode='one_batch',extra_requests_max=1,within_task_budget=True)
        if benchmark:
            result.update(label='对标并填充需求草稿',benchmark=benchmark,
                context_scope=result['context_scope']+' 指定对标资料仅作为外部参考，差异及借鉴内容为待审核建议；保留已填写内容。')
        from .project_memory import plan as memory_plan
        memory_policy=memory_plan(p,stages)
        if memory_policy:result['project_memory']=memory_policy
        result['budget_policies']={stage:policy_summary(config) for stage,config in configs.items()}
        result['detail_review_options']={stage:copy.deepcopy(config['detail_review_options'])
            for stage,config in configs.items() if 'detail_review_options' in config}
        result['detail_unknown_scope_review']={stage:dict(protocol=config['detail_unknown_scope_protocol'],slot='detail/field_conditions',all_frozen_unknown_keys_required=True,classification='model_source_bound_review',confirmation_unknown_gate_unchanged=not bool(config.get('detail_default_scope_protocol')),extra_requests_max=0,within_task_budget=True) for stage,config in configs.items() if 'detail_unknown_scope_protocol' in config}
        from .detail_business_scope import scope_attributes
        result['detail_default_scope_review']={stage:dict(protocol=config['detail_default_scope_protocol'],attributes=scope_attributes(config['detail_default_scope_protocol']),authority='full_review_and_current_source_binding',preserve_unknown=True,extra_requests_max=0,within_task_budget=True) for stage,config in configs.items() if 'detail_default_scope_protocol' in config}
        result['detail_property_reference_encoding']={stage:dict(protocol=config['detail_property_reference_encoding'],fields=['candidate_keys','affected_keys'],scope='per_row_frozen_candidate',source_human_aliases=False,extra_requests_max=0,within_task_budget=True) for stage,config in configs.items() if 'detail_property_reference_encoding' in config}
        result['detail_review_protocol_version']={stage:config['detail_review_protocol_version'] for stage,config in configs.items() if 'detail_review_protocol_version' in config}
        result['detail_review_transport']={stage:dict(protocol=config['detail_review_transport_protocol'],
            calls=['full_review','review_entry_patch'],strict_json=True,extra_requests_max=0,within_task_budget=True)
            for stage,config in configs.items() if 'detail_review_transport_protocol' in config}
        result['detail_correction_protocol_version']={stage:config['detail_correction_protocol_version'] for stage,config in configs.items() if 'detail_correction_protocol_version' in config}
        result['detail_local_graph_kind_preflight']={stage:dict(protocol=config['detail_local_graph_kind_protocol'],
            shares_existing_single_preflight=True,extra_requests_max=0,within_task_budget=True,
            writable='only_exact_node_kind_and_evidence_kind',new_objects=False,deletions=False)
            for stage,config in configs.items() if config.get('detail_local_graph_kind_protocol')=='detail-local-graph-kind-1'}
        result['recovery_outcome_checks']={stage:config['recovery_outcome_checks'] for stage,config in configs.items() if 'recovery_outcome_checks' in config}
        result['detail_recovery_guard']={stage:config['detail_recovery_guard'] for stage,config in configs.items() if 'detail_recovery_guard' in config}
        result['detail_review_protocol_retry']={stage:dict(max_retries=config['detail_review_protocol_retries'],within_task_budget=True)
            for stage,config in configs.items() if config.get('detail_review_protocol_retries')}
        result['detail_review_entry_repair']={stage:dict(protocol=config['detail_review_repair_protocol'],
            max_repairs_per_candidate=1,max_reviewed_candidates=2,max_business_corrections=1,
            detail_chain_requests_max=7,includes_optional_local_preflight_correction=True,
            other_task_auxiliary_requests_included=False,within_task_budget=True)
            for stage,config in configs.items() if config.get('detail_review_repair_protocol')=='detail-review-entry-patch-1'}
        from .business_draft import enabled as business_enabled, _detail_requested, initial_intake
        from .refinement_completion import detail_rows
        requested_details=detail_rows(p,draft_request) if draft_request else None
        result['initial_intake']=bool('ingest' in configs and not benchmark and
            business_enabled(configs['ingest'],'ingest',benchmark,local) and
            initial_intake(p,'ingest',local,draft_request,body.get('message','')))
        if result['initial_intake']:
            result['intake_grounding']=dict(protocol='intake-grounding-1',max_corrections=2,
                review_protocol='intake-grounding-paired-7',coverage_protocol='intake-grounding-coverage-1',challenge_protocol='intake-grounding-challenge-2',
                additional_requests_min=2,additional_requests_max=8,within_task_budget=True,
                includes_full_corrected_candidate_review=True,
                failure_policy='retain_evidence_without_applying_candidate')
            result['context_scope']+=' 本轮只读取资料并整理基础需求草稿，不生成结构化详细定义；后续详细定义及交付检查仍按原要求执行。'
            for key in tuple(result):
                if (key.startswith('detail_') or key=='recovery_outcome_checks') and isinstance(result[key],dict):
                    result[key].pop('ingest',None)
        bounded_stages=[stage for stage,config in configs.items()
            if config.get('detail_correction_policy') in ('two-corrections-seven-run-calls-1','two-corrections-source-seventeen-run-calls-2')
            and business_enabled(config,stage,benchmark,local)
            and not initial_intake(p,stage,local,draft_request,body.get('message',''))
            and (bool(requested_details) if requested_details is not None else _detail_requested(p) or (local or {}).get('detail_required'))
            and (draft_request or {}).get('mode')!='question']
        for stage in bounded_stages:
            result['detail_review_entry_repair'][stage].update(max_business_corrections=2,max_reviewed_candidates=3,
                policy=configs[stage]['detail_correction_policy'],all_run_provider_requests_max=17 if configs[stage]['detail_correction_policy']=='two-corrections-source-seventeen-run-calls-2' else 7,
                other_task_auxiliary_requests_included=True,request_count_scope='all_provider_requests_in_this_business_detail_run',
                includes_generation_and_all_retries=True,correction_requires_three_remaining_slots=True,
                per_candidate_patch_subject_to_remaining_run_budget=True)
        source_stages=[stage for stage,config in configs.items()
            if config.get('business_source_policy')=='noninitial-source-only-1'
            and business_enabled(config,stage,benchmark,local)
            and not initial_intake(p,stage,local,draft_request,body.get('message',''))
            and (draft_request or {}).get('mode')!='question']
        if source_stages:
            result['business_source_review']={stage:dict(protocol='noninitial-source-only-1',
                scope='generated_requirement_statement_behavior_acceptance_only',coverage_review=False,
                additional_requests_min=1,additional_requests_max=5,max_corrections=2,
                shared_protocol_recovery_max=int(configs[stage].get('detail_correction_policy')=='two-corrections-source-seventeen-run-calls-2' and bool(configs[stage].get('detail_review_protocol_retries'))),protocol_recovery_within_run_cap=True,
                failure_policy='retain_evidence_without_applying_candidate',within_task_budget=True) for stage in source_stages}
            result['context_scope']+=' 本次新增或修订的正文、行为与验收在保存前单独核对自身来源；最多两次定位纠正，不重新生成整稿，核对未通过则保留原内容。'
        for stage in bounded_stages:
            if configs[stage]['detail_correction_policy']=='two-corrections-source-seventeen-run-calls-2':
                result['detail_review_entry_repair'][stage].update(detail_chain_requests_max=17,
                    max_protocol_repairs_per_run=1,protocol_recovery_scope=['body_source_patch_format','body_source_challenge','detail_full_review','detail_source_challenge','detail_review_entry_patch'],request_components=dict(generation=1,optional_memory=1,
                        optional_preflight=1,body_source_reviews=3,body_source_corrections=2,
                        detail_full_reviews=3,detail_challenges=3,detail_corrections=2,protocol_repair=1))
        # Even if a failed action changed no content, its approval cannot be
        # reused for a new paid action. Idempotent replay returns the old task.
        if intake_resume:
            require(result['initial_intake'],'INTAKE_RESUME_UNAVAILABLE','当前计划不再是初始整理，不能复用旧候选。',409)
            result.update(label='继续核对上次需求候选',intake_resume=intake_resume)
            result['context_scope']='复用上次未应用候选与原来源上下文；仍调用模型重新完整核对已答规则和自身来源，不重新生成，不代表已通过。'
            result.pop('project_memory',None)
        plan_identity=dict(input=execution_hash(p),request=body,configs=configs,
            prior_task_ids=[t['id'] for t in self.store.records(pid,'user_task')])
        if intake_resume:plan_identity['intake_resume']=intake_resume
        result['plan_hash']=digest(plan_identity)
        return result

    def start(self,pid,body,plan_hash,idempotency_key,authorize=False):
        request_hash=digest(dict(body=body,plan_hash=plan_hash))
        tid=ident('TASK')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            existing=next((t for t in self.store.records(pid,'user_task',db) if t.get('idempotency_key')==idempotency_key),None)
            if existing:
                require(existing['request_hash']==request_hash,'IDEMPOTENCY_CONFLICT','此提交标识已对应另一任务',409)
                return existing
            p=self.store.get(pid,db)
            configs={}
            plan=self.plan(pid,body,p,config_snapshot=configs)
            require(plan['plan_hash']==plan_hash,'STALE_PLAN','输入或接收端配置变化，请重新核对，不会自动发送',409)
            require(not plan['missing'],'ACTION_BLOCKED','；'.join(plan['missing']),409)
            benchmark=plan.get('benchmark')
            message=body['message']
            if body['action']=='interview' and body.get('attachment_source_ids') and not message.strip():
                image_only=all(s.get('image_mime') for s in p['sources'] if s['id'] in body['attachment_source_ids'])
                message='请结合这些图片继续梳理需求。' if image_only else '请结合这些资料继续梳理需求。'
            if benchmark and not message.strip():
                message='对标对象：'+benchmark['target']+'\n参考范围：'+benchmark['focus']
            if message.strip() and not plan.get('intake_resume'):
                from .interview import input_title
                source=save_source(self.store,input_title(p,message,benchmark=bool(benchmark)),message.encode('utf-8'),'goal')
                if body['action']=='interview' or benchmark:source['origin']='benchmark_intake' if benchmark else 'interview'
                p['sources'].append(source)
                p['current_task_source_id'] = source['id']
                p['revision']+=1
                if body['action']=='interview' or benchmark:
                    from .interview import record_input
                    record_input(p,message,tid,source['id'],body.get('attachment_source_ids'))
                if benchmark:
                    for s in p['sources']:
                        if s['id'] in benchmark['source_ids']:s['benchmark_reference']=True
                db.execute('INSERT INTO revisions VALUES(?,?,?,?,?)',(pid,p['revision'],dumps(p),'保存当前任务输入为来源',now()))
            from .domain_context import active_source_ids
            ids=sorted(active_source_ids(p))
            # Explicit action submission approves this frozen task, including its
            # newly saved input. The legacy authorize flag no longer gates it.
            for r in plan['recipients']:
                p['grants'][r['origin']]=dict(source_ids=ids,created=now(),actor='authenticated_local_user',user_task_id=tid)
            db.execute('UPDATE projects SET revision=?,payload=? WHERE id=?',(p['revision'],dumps(p),pid))
            task=dict(action=body['action'],label=plan['label'],status='queued',created=now(),
                input_revision=p['revision'],input_state_hash=execution_hash(p),source_ids=ids,
                stages=plan['stages'],run_ids=[],max_calls=plan['max_calls'],calls=0,cost=None,
                request_hash=request_hash,idempotency_key=idempotency_key,message='等待开始',
                authorized_configs={s:{k:v for k,v in c.items() if k!='proxy'} for s,c in configs.items()},
                approved_plan_hash=plan_hash,
                generation_target=plan['generation_target'],completed_steps=0,
                step_states=[dict(stage=s,status='NOT_RUN') for s in plan['stages']])
            if body.get('draft_request'):task['draft_request']=copy.deepcopy(body['draft_request'])
            if plan.get('intake_resume'):task['intake_resume']=copy.deepcopy(plan['intake_resume'])
            if plan['max_calls'] is not None:
                task['request_budget']=initial_budget(plan['max_calls'])
            if body['action']=='organize' and not benchmark and not body.get('target') and not plan['generation_target'] and (body.get('draft_request') or {}).get('mode')!='question':
                from .refinement_preparation import DRAFT_SCHEMA, input_identity
                preparation=input_identity(self.store,p,db)
                task.update(preparation_schema=preparation['schema'], preparation_draft_schema=DRAFT_SCHEMA,
                    preparation_input_hash=preparation['input_hash'],
                    preparation_source_ids=preparation['source_ids'])
            if plan.get('requirement_recommendation_scope'):
                task['requirement_recommendation_scope']=copy.deepcopy(plan['requirement_recommendation_scope'])
            task['retry_input'] = {k:copy.deepcopy(body[k]) for k in
                                   ('action','message','document_type','option_id','target','attachment_source_ids','allow_review_draft') if k in body}
            if body.get('draft_request'):task['retry_input']['draft_request']=copy.deepcopy(body['draft_request'])
            if benchmark:
                task['benchmark']=copy.deepcopy(benchmark)
                task['retry_input']['benchmark']=copy.deepcopy(benchmark)
            if plan['max_calls'] is not None:task['retry_input']['max_calls']=plan['max_calls']
            if plan.get('intake_resume'):task['retry_input']['intake_resume_task_id']=tid
            self.store.record(pid,'user_task',task,tid,db)
        job=asyncio.create_task(self.execute(pid,tid,copy.deepcopy(body),copy.deepcopy(configs)))
        self.jobs[tid]=job
        job.add_done_callback(lambda _:self.jobs.pop(tid,None))
        return dict(id=tid,**task)

    async def execute(self,pid,tid,body,configs):
        calls=0
        try:
            task=self.store.get_record(pid,tid,'user_task')
            expected=task['input_state_hash']
            completed=0
            for index,stage in enumerate(task['stages']):
                current=self.store.get_record(pid,tid,'user_task')
                require(current['status']!='cancelled','CANCELLED','已停止后续步骤；已发送请求可能计费')
                p=self.store.get(pid)
                require(execution_hash(p)==expected,'STALE_REVISION','任务输入发生外部变化；已完成结果保留，请重新核对',409)
                if current.get('request_budget'):
                    check_available(current['request_budget'])
                config=copy.deepcopy(configs[stage])
                target=copy.deepcopy(task['generation_target'])
                if target: target['input_revision']=p['revision']
                message=body['message']
                if body.get('target'):
                    message='本次局部讨论对象：'+target_context(p,body['target'])+'\n仅围绕此对象提出候选；保留其他需求、未知和采纳状态。\n用户修改意图：'+message
                run=self.workflow.start(pid,p['revision'],stage,message,body['document_type'],
                    **({'intake_resume':task['intake_resume']} if task.get('intake_resume') else {}),
                    user_task_id=tid,config_override=config,generation_target=target,
                    prewrite=stage=='review' and (body['action']=='prewrite' or body['action']=='document' and body['document_type']=='prd'),
                    allow_review_draft=bool(body.get('allow_review_draft')),
                    **({'draft_request':body['draft_request']} if body.get('draft_request') and stage=='ingest' else {}),
                    discussion_target=body.get('target'),**({'benchmark':task['benchmark']} if stage=='ingest' and task.get('benchmark') else {}))
                ids=current['run_ids']+[run['id']]
                step_states=copy.deepcopy(current['step_states'])
                step_states[index].update(status='running',run_id=run['id'])
                self.store.update_record(pid,tid,'user_task',status='running',run_ids=ids,step_states=step_states,message='正在'+('分析图片' if stage=='vision' else LABELS[body['action']]))
                await self.workflow.tasks[run['id']]
                result=self.store.get_record(pid,run['id'],'run')
                step_states[index]['status']=result['status']
                self.store.update_record(pid,tid,'user_task',step_states=step_states)
                calls+=result['calls']
                self.store.update_record(pid,tid,'user_task',calls=calls)
                require(self.store.get_record(pid,tid,'user_task')['status']!='cancelled','CANCELLED','任务已取消，后续步骤未执行')
                require(result['status'] in ('succeeded','partial','awaiting_user'),result.get('error') or 'RUN_FAILED',result.get('message') or '步骤未完成')
                completed+=1
                expected=result['output_state_hash']
                self.store.update_record(pid,tid,'user_task',completed_steps=completed)
                if result.get('prewrite'):
                    from .prewrite import status as prewrite_status
                    checked=prewrite_status(self.store.get(pid))
                    self.store.update_record(pid,tid,'user_task',prewrite_review_id=checked.get('id'))
                    hard=any(f.get('hard_block') and f.get('status')!='resolved' for f in checked['findings'])
                    if checked['status']!='passed' and (not body.get('allow_review_draft') or hard or body['action']=='prewrite'):
                        self.store.update_record(pid,tid,'user_task',status='awaiting_user',completed=now(),
                            message='成文检查发现需要处理的问题，请在完善需求中核对并复检；尚未生成新版 PRD。',
                            output_state_hash=expected,cost=result.get('cost'))
                        return
            p=self.store.get(pid)
            problems=[s for s in p['sources'] if s['id'] in task['source_ids'] and not s['excluded'] and s['parse_status']!='read']
            runs=[self.store.get_record(pid,rid,'run') for rid in ids]
            partial=bool(problems) or any(r['status']=='partial' for r in runs)
            message='可用内容已处理，仍有资料或输出限制，请核对来源。' if partial else '本次内容已生成，请核对建议与未决问题。'
            if body['action'] in ('clarify','change'):
                target=body.get('target') or {}
                if task.get('requirement_recommendation_scope') or (target.get('kind')=='item' and any(i['id']==target.get('id') and i.get('kind')=='requirement' for i in p['items'])):
                    message+=' 本次只生成需求候选，请在需求确认表核对建议与未决问题后采纳。'
                elif target.get('kind')=='document':
                    message+=' 本次只生成讨论或修订候选，未直接改写文档；核对采纳后，请在文档页更新讨论稿。'
            preparation_result={}
            if task.get('preparation_input_hash'):
                ingest=next((r for r in reversed(runs) if r.get('stage')=='ingest' and r.get('result_applied') is True),None)
                if ingest:
                    preparation_result['preparation_result']=dict(schema=task['preparation_schema'],
                        draft_schema=task.get('preparation_draft_schema'),
                        input_hash=task['preparation_input_hash'],run_id=ingest['id'],
                        status='partial' if partial else 'succeeded',completed=now())
            self.store.update_record(pid,tid,'user_task',status='partial' if partial else 'succeeded',completed=now(),
                message=message,
                cost=sum(r['cost'] for r in runs) if all(r.get('cost') is not None for r in runs) else None,
                **preparation_result)
        except Exception as error:
            error=error if isinstance(error,Problem) else Problem('INTERNAL_ERROR','任务处理失败，已有内容保留')
            if self.store.get_record(pid,tid,'user_task').get('request_budget'):
                calls=self.store.get_record(pid,tid,'user_task')['calls']
            self.store.update_record(pid,tid,'user_task',status='cancelled' if error.code=='CANCELLED' else 'paused_budget' if error.code=='BUDGET_EXHAUSTED' else 'failed',error=error.code,message=error.message,completed=now(),calls=calls)

    def cancel(self,pid,tid):
        task=self.store.get_record(pid,tid,'user_task')
        require(task['status'] in ACTIVE,'RUN_FINISHED','任务已经结束')
        self.store.update_record(pid,tid,'user_task',status='cancelled',message='已取消后续步骤；已发送请求可能计费')
        for rid in task['run_ids']:
            if self.store.get_record(pid,rid,'run')['status'] in ACTIVE:
                self.workflow.cancel(pid,rid)
        return self.store.get_record(pid,tid,'user_task')
