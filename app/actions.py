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
          'prototype':'生成功能草图','document':'生成评审稿','review':'审查当前内容','change':'讨论修改方案'}
STAGES = {'interview':['interview'],'explore':['brainstorm'],'clarify':['clarify'],'prototype':['ui'],
          'document':['prd'],'review':['review'],'change':['change']}
ACTIVE = ('queued','running')


def target_context(p, target):
    """Resolve the local helper against this snapshot, never a client supplied label."""
    if not target:return ''
    require(set(target)<= {'kind','id','section_id'},'TARGET_INVALID','不支持的修改对象字段')
    kind=target.get('kind');oid=target.get('id')
    if kind in ('item','question'):
        item=next((x for x in p['items' if kind=='item' else 'questions'] if x['id']==oid),None)
        require(item is not None,'TARGET_INVALID','此需求或问题已不存在',409)
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
        require(body['action'] in LABELS,'ACTION_INVALID','业务动作不存在')
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
        require(not target_label or body['action'] in ('clarify','change','prototype'), 'TARGET_INVALID','此动作不支持局部修改')
        from .domain_context import active_source_ids, attachment_source_ids
        active=[s for s in p['sources'] if s['id'] in active_source_ids(p) | attachment_source_ids(p,attachments)]
        images=[s for s in active if s.get('image_mime') and not s.get('vision_run_id')]
        stages=STAGES.get(body['action']) or ['vision']*math.ceil(len(images)/3) + ['ingest']
        if body['action']=='interview':
            stages=['vision']*math.ceil(len(images)/3)+['interview']
        limit=validate_limit(body.get('max_calls'))
        configs={s:dict(self.workflow.config(s),max_calls=limit) for s in stages}
        if config_snapshot is not None:
            config_snapshot.update(copy.deepcopy(configs))
        blockers=[]
        for stage in stages:
            blockers.extend(execution_issues(p,stage,body['document_type'],body.get('target')))
        if not any(s['excerpts'] for s in active) and not p['items'] and not body['message'].strip():
            blockers.append('请先描述目标或添加项目资料。')
        if body['action'] in ('prototype','document','review') and not p['items']:
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
                prior=p['grants'].get(endpoint,{})
                recipients.append(dict(origin=endpoint, model=c['model'], source_ids=ids,
                    needs_authorization=bool(body['message'].strip() or benchmark or attachments) or endpoint not in p['grants'] or not set(ids)<=set(prior.get('source_ids',[]))))
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
        from .requirement_recommendation import scope as recommendation_scope
        local = recommendation_scope(p, body.get('target'), body['action'])
        if local:
            result.update(label='完善这条需求建议', requirement_recommendation_scope=local)
            result['context_scope'] += ' 仅为“'+local['title']+'”整理正文、适用业务说明和验收建议；所有建议待人工确认，不改其他需求与共享条款。'
        if p.get('workflow_mode')=='shared-draft' and any(stage in ('ingest','clarify','change') for stage in stages):
            result['context_scope']+=' 新增或变化的问题及当前回答将额外进行一次批量覆盖复核，沿用同一接收端和总请求额度；复核仅提供待人工确认的建议。'
            result['decision_review_policy']=dict(mode='one_batch',extra_requests_max=1,within_task_budget=True)
        if benchmark:
            result.update(label='对标并填充需求草稿',benchmark=benchmark,
                context_scope=result['context_scope']+' 指定对标资料仅作为外部参考，差异及借鉴内容为待审核建议；保留已填写内容。')
        result['budget_policies']={stage:policy_summary(config) for stage,config in configs.items()}
        # Even if a failed action changed no content, its approval cannot be
        # reused for a new paid action. Idempotent replay returns the old task.
        result['plan_hash']=digest(dict(input=execution_hash(p),request=body,configs=configs,
            prior_task_ids=[t['id'] for t in self.store.records(pid,'user_task')]))
        return result

    def start(self,pid,body,plan_hash,idempotency_key,authorize):
        request_hash=digest(dict(body=body,plan_hash=plan_hash))
        tid=ident('TASK')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            existing=next((t for t in self.store.records(pid,'user_task',db) if t['idempotency_key']==idempotency_key),None)
            if existing:
                require(existing['request_hash']==request_hash,'IDEMPOTENCY_CONFLICT','此提交标识已对应另一任务',409)
                return existing
            p=self.store.get(pid,db)
            configs={}
            plan=self.plan(pid,body,p,config_snapshot=configs)
            require(plan['plan_hash']==plan_hash,'STALE_PLAN','输入或接收端配置变化，请重新核对，不会自动发送',409)
            require(not plan['missing'],'ACTION_BLOCKED','；'.join(plan['missing']),409)
            require(authorize or not any(r['needs_authorization'] for r in plan['recipients']),
                    'DATA_AUTH_REQUIRED','请在当前任务中核对接收端及新增内容范围',403)
            benchmark=plan.get('benchmark')
            message=body['message']
            if body['action']=='interview' and body.get('attachment_source_ids') and not message.strip():
                image_only=all(s.get('image_mime') for s in p['sources'] if s['id'] in body['attachment_source_ids'])
                message='请结合这些图片继续梳理需求。' if image_only else '请结合这些资料继续梳理需求。'
            if benchmark and not message.strip():
                message='对标对象：'+benchmark['target']+'\n参考范围：'+benchmark['focus']
            if message.strip():
                source=save_source(self.store,'对标任务输入.txt' if benchmark else '对话输入.txt',message.encode('utf-8'),'goal')
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
            if authorize:
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
            if plan['max_calls'] is not None:
                task['request_budget']=initial_budget(plan['max_calls'])
            if body['action']=='organize' and not benchmark and not body.get('target') and not plan['generation_target']:
                from .refinement_preparation import DRAFT_SCHEMA, input_identity
                preparation=input_identity(self.store,p,db)
                task.update(preparation_schema=preparation['schema'], preparation_draft_schema=DRAFT_SCHEMA,
                    preparation_input_hash=preparation['input_hash'],
                    preparation_source_ids=preparation['source_ids'])
            if plan.get('requirement_recommendation_scope'):
                task['requirement_recommendation_scope']=copy.deepcopy(plan['requirement_recommendation_scope'])
            task['retry_input'] = {k:copy.deepcopy(body[k]) for k in
                                   ('action','message','document_type','option_id','target','attachment_source_ids') if k in body}
            if benchmark:
                task['benchmark']=copy.deepcopy(benchmark)
                task['retry_input']['benchmark']=copy.deepcopy(benchmark)
            if plan['max_calls'] is not None:task['retry_input']['max_calls']=plan['max_calls']
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
                    user_task_id=tid,config_override=config,generation_target=target,
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
            p=self.store.get(pid)
            problems=[s for s in p['sources'] if s['id'] in task['source_ids'] and not s['excluded'] and s['parse_status']!='read']
            runs=[self.store.get_record(pid,rid,'run') for rid in ids]
            partial=bool(problems) or any(r['status']=='partial' for r in runs)
            message='可用内容已处理，仍有资料或输出限制，请核对来源。' if partial else '本次内容已生成，请核对建议与未决问题。'
            if body['action'] in ('clarify','change'):
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
