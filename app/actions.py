"""Small, explicit business actions over existing runs; no autonomous routing."""
import asyncio
import copy

from .core import Problem, digest, dumps, execution_hash, ident, now, require
from .provider import origin
from .sources import save_source
from .product_flow import execution_issues

LABELS = {'organize':'整理当前信息','explore':'探索可选方案','clarify':'根据回答更新理解',
          'prototype':'生成功能草图','document':'生成评审稿','review':'审查当前内容','change':'讨论修改方案'}
STAGES = {'explore':['brainstorm'],'clarify':['clarify'],'prototype':['ui'],
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
        target_label=target_context(p,body.get('target'))
        require(not target_label or body['action'] in ('clarify','change','prototype'), 'TARGET_INVALID','此动作不支持局部修改')
        active=[s for s in p['sources'] if not s['excluded']]
        images=[s for s in active if s.get('image_mime') and s['parse_status']!='read']
        stages=STAGES.get(body['action']) or (['vision'] if images else []) + ['ingest']
        configs={s:self.workflow.config(s) for s in stages}
        if config_snapshot is not None:
            config_snapshot.update(copy.deepcopy(configs))
        blockers=[]
        for stage in stages:
            blockers.extend(execution_issues(p,stage,body['document_type']))
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
                    needs_authorization=bool(body['message'].strip()) or endpoint not in p['grants'] or not set(ids)<=set(prior.get('source_ids',[]))))
        busy=any(r['status'] in ACTIVE for kind in ('run','user_task') for r in self.store.records(pid,kind))
        if busy:
            blockers.append('当前项目任务正在执行，请等待结果或取消当前任务。')
        result=dict(action=body['action'],label=LABELS[body['action']],stages=stages,
            expected_revision=p['revision'],max_calls=body['max_calls'],recipients=recipients,
            sources=[dict(id=s['id'],title=s['title'],status=s['parse_status']) for s in active],
            pending_text=body['message'], context_scope='当前底稿、问题和讨论记录；排除材料不删除已经形成的讨论内容。',
            missing=list(dict.fromkeys(blockers)),generation_target=target,
            open_questions=sum(q['status']!='answered' for q in p['questions']))
        result['target_label']=target_label
        result['plan_hash']=digest(dict(input=execution_hash(p),request=body,configs=configs))
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
            if body['message'].strip():
                source=save_source(self.store,'对话输入.txt',body['message'].encode('utf-8'),'goal')
                p['sources'].append(source)
                p['revision']+=1
                db.execute('INSERT INTO revisions VALUES(?,?,?,?,?)',(pid,p['revision'],dumps(p),'保存当前任务输入为来源',now()))
            ids=[s['id'] for s in p['sources'] if not s['excluded']]
            if authorize:
                for r in plan['recipients']:
                    p['grants'][r['origin']]=dict(source_ids=ids,created=now(),actor='authenticated_local_user',user_task_id=tid)
            db.execute('UPDATE projects SET revision=?,payload=? WHERE id=?',(p['revision'],dumps(p),pid))
            task=dict(action=body['action'],label=plan['label'],status='queued',created=now(),
                input_revision=p['revision'],input_state_hash=execution_hash(p),source_ids=ids,
                stages=plan['stages'],run_ids=[],max_calls=body['max_calls'],calls=0,cost=None,
                request_hash=request_hash,idempotency_key=idempotency_key,message='等待开始',
                authorized_configs={s:{k:v for k,v in c.items() if k!='proxy'} for s,c in configs.items()},
                approved_plan_hash=plan_hash,
                generation_target=plan['generation_target'],completed_steps=0)
            self.store.record(pid,'user_task',task,tid,db)
        job=asyncio.create_task(self.execute(pid,tid,body,configs))
        self.jobs[tid]=job
        job.add_done_callback(lambda _:self.jobs.pop(tid,None))
        return dict(id=tid,**task)

    async def execute(self,pid,tid,body,configs):
        calls=0
        try:
            task=self.store.get_record(pid,tid,'user_task')
            expected=task['input_state_hash']
            completed=0
            for stage in task['stages']:
                current=self.store.get_record(pid,tid,'user_task')
                require(current['status']!='cancelled','CANCELLED','已停止后续步骤；已发送请求可能计费')
                p=self.store.get(pid)
                require(execution_hash(p)==expected,'STALE_REVISION','任务输入发生外部变化；已完成结果保留，请重新核对',409)
                require(calls<body['max_calls'],'BUDGET_EXHAUSTED','本次任务总请求额度已用完；已完成步骤保留')
                config=dict(configs[stage],max_calls=min(configs[stage]['max_calls'],body['max_calls']-calls))
                target=copy.deepcopy(task['generation_target'])
                if target: target['input_revision']=p['revision']
                message=body['message']
                if body.get('target'):
                    message='本次局部讨论对象：'+target_context(p,body['target'])+'\n仅围绕此对象提出候选；保留其他需求、未知和采纳状态。\n用户修改意图：'+message
                run=self.workflow.start(pid,p['revision'],stage,message,body['document_type'],
                    user_task_id=tid,config_override=config,generation_target=target)
                ids=current['run_ids']+[run['id']]
                self.store.update_record(pid,tid,'user_task',status='running',run_ids=ids,message='正在'+('分析图片' if stage=='vision' else LABELS[body['action']]))
                await self.workflow.tasks[run['id']]
                result=self.store.get_record(pid,run['id'],'run')
                calls+=result['calls']
                self.store.update_record(pid,tid,'user_task',calls=calls)
                require(self.store.get_record(pid,tid,'user_task')['status']!='cancelled','CANCELLED','任务已取消，后续步骤未执行')
                require(result['status'] in ('succeeded','partial','awaiting_user'),result.get('error') or 'RUN_FAILED',result.get('message') or '步骤未完成')
                completed+=1
                expected=result['output_state_hash']
                self.store.update_record(pid,tid,'user_task',completed_steps=completed)
            p=self.store.get(pid)
            problems=[s for s in p['sources'] if not s['excluded'] and s['parse_status'] in ('failed','partial','awaiting_vision')]
            runs=[self.store.get_record(pid,rid,'run') for rid in ids]
            partial=bool(problems) or any(r['status']=='partial' for r in runs)
            self.store.update_record(pid,tid,'user_task',status='partial' if partial else 'succeeded',completed=now(),
                message=('可用内容已处理，仍有资料或输出限制，请核对来源。' if partial else '本次内容已生成，请核对建议与未决问题。'),
                cost=sum(r['cost'] for r in runs) if all(r.get('cost') is not None for r in runs) else None)
        except Exception as error:
            error=error if isinstance(error,Problem) else Problem('INTERNAL_ERROR','任务处理失败，已有内容保留')
            self.store.update_record(pid,tid,'user_task',status='cancelled' if error.code=='CANCELLED' else 'paused_budget' if error.code=='BUDGET_EXHAUSTED' else 'failed',error=error.code,message=error.message,completed=now(),calls=calls)

    def cancel(self,pid,tid):
        task=self.store.get_record(pid,tid,'user_task')
        require(task['status'] in ACTIVE,'RUN_FINISHED','任务已经结束')
        self.store.update_record(pid,tid,'user_task',status='cancelled',message='已取消后续步骤；已发送请求可能计费')
        for rid in task['run_ids']:
            if self.store.get_record(pid,rid,'run')['status'] in ACTIVE:
                self.store.update_run(pid,rid,status='cancelled')
        return self.store.get_record(pid,tid,'user_task')
