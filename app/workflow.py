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
from .understanding import question_ids, save_questions, annotate_candidates

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


def apply_response(p, response):
    mapping = {x['temp_id']:ident(PREFIX.get(x['kind'], 'ITEM')) for x in response['proposals']}
    mapping.update(question_ids(p,response['questions'],mapping))
    r = replace_refs(response, mapping)
    valid={x['id'] for x in p['items']}|{q['id'] for q in p['questions']}|set(mapping.values())
    for row in r['proposals']+[child for parent in r['questions'] for child in (parent.get('decision_points') or [parent])]:
        require(set(row.get('related_refs',[]))<=valid,'REFERENCE_INVALID','问题或候选存在未落地关联')
    if r.get('product_context_proposal'):
        p['product_context_proposal']=r['product_context_proposal']
    for item in r['proposals']:
        # A proposed revision is a separate candidate, never an overwrite.
        identity=dict(content_version=1,source_namespace=namespace(p)) if item['kind'] in TRACKED else {}
        row=dict(id=item['temp_id'], **{k:v for k,v in item.items() if k != 'temp_id'}, **identity, selection_status='candidate', revision=p['revision']+1)
        if row['kind']=='requirement':row.setdefault('change_type','unspecified')
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
        p['documents'][kind]['source_snapshot']=[{k:s.get(k) for k in ('id','title','parse_status','failure_reason')} for s in p['sources']]
        p['documents'][kind].update(document_version=document_version,document_family_id=p['id']+':'+kind,
            delivery_item_ids=[i['id'] for i in delivery_items(p)],
            requirement_versions=[requirement_ref(p,i) for i in p['items'] if i['kind'] in TRACKED])
    if stage == 'review':
        p['review'] = dict(target_hash=review_target(p), response=r, created=now())
    p['messages'].append(dict(role='assistant', stage=stage, text=r['summary'], response=r, created=now()))


class Workflow:
    def __init__(self, store, provider):
        self.store, self.provider = store, provider
        self.tasks = {}

    def config(self, stage):
        config=copy.deepcopy(self.store.setting('vision' if stage == 'vision' else 'model') or DEFAULT)
        # Part of the authorized plan/config snapshot, never changed mid-request.
        if stage=='review' and origin(config)=='https://api.deepseek.com':
            config['thinking_disabled']=not config.get('review_thinking',True)
        return config

    def start(self, pid, revision, stage, message, kind='prd', resumed_from=None, *, user_task_id=None, config_override=None, generation_target=None):
        require(stage in STAGES and stage != 'handoff', 'STAGE_INVALID', '请选择支持的分析阶段')
        config = copy.deepcopy(config_override or self.config(stage))
        require(not any(t['status'] in ('queued','running') and t['id'] != user_task_id for t in self.store.records(pid,'user_task')), 'PROJECT_BUSY','当前项目任务正在执行，请等待或取消',409)
        require(not any(t['status'] in ('queued','running') for t in self.store.records(pid,'run')), 'PROJECT_BUSY','当前项目已有模型任务',409)
        p = self.store.get(pid)
        require(p['revision'] == revision, 'STALE_REVISION', '页面已过期', 409)
        issues=execution_issues(p,stage,kind)
        require(not issues,'STAGE_BLOCKED','；'.join(issues),409)
        run = dict(stage=stage, status='queued', revision=revision, message=message, document_type=kind, calls=0, attempts=[], events=[], created=now(), error=None, resumed_from=resumed_from, provider={k:v for k,v in config.items() if k not in ('proxy',)}, prompt_version='1.1', cost=None)
        rid = self.store.record(pid, 'run', run)
        self.store.update_run(pid,rid,user_task_id=user_task_id,generation_target=generation_target,input_state_hash=execution_hash(p))
        task = asyncio.create_task(self.execute(pid, rid, p, config))
        self.tasks[rid] = task
        task.add_done_callback(lambda _: self.tasks.pop(rid, None))
        return dict(id=rid, **run)

    async def execute(self, pid, rid, p, config):
        started = time.monotonic()
        run = self.store.get_record(pid, rid, 'run')
        calls, repair_count, retries = 0, 0, 0
        repair_parent = None
        pending_repair = False
        repair_anchor = None
        # Snapshot approved max_tokens; every retry remains inside the same ceiling.
        out_budget = config['max_tokens']
        attempts, events = [], []
        try:
            require(self.provider.key(config), 'CONFIG_MISSING', '请先在模型设置中配置此接收端的 Key')
            require(origin(config) in p['grants'], 'DATA_AUTH_REQUIRED', '请先确认该接收端和项目材料的发送授权')
            current_sources = {s['id'] for s in p['sources'] if not s['excluded']}
            require(current_sources <= set(p['grants'][origin(config)]['source_ids']), 'DATA_AUTH_REQUIRED', '新增材料尚未授权发送，请检查材料范围')
            messages, excerpts, omitted = assemble(p, run['stage'], run['message'], config, self.store.folder, run['document_type'], run.get('generation_target'), pending_images_only=bool(run.get('user_task_id')))
            import json
            header=json.loads(messages[0]['content'].split('可信任务头：',1)[1])
            authorization=dict(user_task_id=run.get('user_task_id'),run_id=rid,approved_max_tokens=out_budget,
                approved_max_calls=config['max_calls'],config_hash=digest(run['provider']),
                prompt_hash=digest(messages[0]['content'].split('可信任务头：',1)[0]),schema_hash=digest(header['schema']),
                profile_hash=digest(header.get('content_profile')),input_revision=p['revision'],assembly_version='prd-plan-2' if run['stage']=='prd' else 'runtime-1.1')
            self.store.update_run(pid, rid, status='running', sent_excerpt_ids=[x['id'] for x in excerpts], omitted_excerpt_ids=omitted, input_hash=digest(messages),authorization=authorization,input_metrics=input_metrics(messages))
            while True:
                state = self.store.get_record(pid, rid, 'run')
                require(state['status'] != 'cancelled', 'CANCELLED', '任务已取消；已发请求仍可能计费')
                require(calls < config['max_calls'] and time.monotonic()-started < config['action_seconds'], 'BUDGET_EXHAUSTED', '动作预算耗尽；保存进度，可明确续跑')
                require(len(dumps(messages))+out_budget*4<=config['context_chars'],'BUDGET_EXHAUSTED','输入及修复上下文超过批准预算；已暂停，不能截断关键底稿')
                if pending_repair:
                    repair_count += 1
                    pending_repair = False
                calls += 1
                call_id = ident('CALL')
                evidence = ModelCallEvidence(self.store.folder, call_id, rid, run['stage'],
                                             config['model'], origin(config), repair_parent,
                                             self.provider.key(config))
                evidence.request_context(config,messages,authorization,input_metrics(messages))
                attempt = dict(call=calls, call_id=call_id, repair_of=repair_parent,max_tokens=out_budget,
                    authorization_hash=digest(authorization),actual_input_hash=digest(messages))
                events.append(dict(time=now(), phase='调用模型', call=calls, call_id=call_id))
                self.store.update_run(pid, rid, calls=calls, events=events)
                value = None
                call_started = time.monotonic()
                try:
                    value, meta = await asyncio.wait_for(self.provider.request(dict(config, max_tokens=out_budget), messages, evidence=evidence), timeout=max(1, config['action_seconds']-(time.monotonic()-started)))
                    attempt.update(meta)
                    require(self.store.get_record(pid, rid, 'run')['status'] != 'cancelled', 'CANCELLED', '已取消，结果未采纳')
                    if run['stage']=='prd':
                        value=compile_plan(value,p,run['document_type'],omitted,include_sketch=False)
                    if run['stage']=='ingest':
                        ingest.validate(value, excerpts)
                        if repair_anchor is not None:
                            ingest.preserve(repair_anchor, value, excerpts)
                    validate_response(value, run['stage'], p, excerpts, run['document_type'])
                    if repair_anchor is not None and run['stage'] in ('clarify','change','brainstorm','review'):
                        ingest.preserve_response(repair_anchor,value,excerpts,run['stage'])
                    evidence.finish('accepted', round(time.monotonic()-call_started, 3))
                    attempt['validation_result']='accepted'
                    attempt['evidence_limitations']=evidence.limitations[:]
                    attempts.append(attempt)
                    break
                except (Problem, asyncio.TimeoutError) as e:
                    if isinstance(e, asyncio.TimeoutError):
                        e = Problem('BUDGET_EXHAUSTED', '动作时间预算耗尽')
                    evidence.finish(e.code, round(time.monotonic()-call_started, 3))
                    attempt.update(error=e.code, validation_result=e.code,
                                   usage=evidence.value.get('usage'),finish_reason=evidence.value.get('finish_reason'),
                                   evidence_limitations=evidence.limitations[:])
                    attempts.append(attempt)
                    self.store.update_run(pid, rid, attempts=attempts)
                    if run['stage']=='review' and e.code=='OUTPUT_TRUNCATED':
                        raise Problem('BUDGET_EXHAUSTED','审查输出达到批准的 token 上限；未保存不完整结论。请核对输出预算或缩小范围后重新发起审查，当前任务不会自动提额。')
                    if e.code in ('NETWORK_ERROR','TIMEOUT','RATE_LIMITED','PROVIDER_ERROR') and retries < 2:
                        retries += 1
                        await asyncio.sleep(min(retries, 2))
                        continue
                    if e.code in ('SCHEMA_INVALID','REFERENCE_INVALID','OUTPUT_EMPTY','OUTPUT_TRUNCATED') and repair_count < 2:
                        failed_output = evidence.value.get('final_output') or (dumps(value) if value is not None else '无可用最终输出')
                        if run['stage'] in ('clarify','change','brainstorm','review'):
                            anchor_value=value if isinstance(value,dict) else (
                                None if {'final_output_truncated','final_output_redacted'} & set(evidence.limitations)
                                else ingest.complete_object(evidence.value.get('final_output')))
                            require(anchor_value is not None,'SEMANTIC_BLOCKED',
                                    '失败输出不是可完整读取的 JSON 对象，无法验证业务保真；原始证据已保存，不能作为格式修复重写')
                            if repair_anchor is None:
                                repair_anchor=ingest.response_anchor(anchor_value,excerpts,run['stage'])
                                require(repair_anchor,'SEMANTIC_BLOCKED',
                                        '失败输出没有可验证的业务锚点；原始证据已保存，不能作为格式修复重写')
                                self.store.update_run(pid,rid,repair_anchor_hash=ingest.anchor_hash(repair_anchor))
                        pending_repair = True
                        repair_parent = call_id
                        if e.code == 'OUTPUT_TRUNCATED':
                            events.append(dict(time=now(),phase='保持批准输出上限 '+str(out_budget)+'，压缩章节与叙述后修复',call=calls))
                        repair=(KIT/'prompts/10_repair.md').read_text('utf-8') if run['stage']!='prd' else repair_instruction(e,p)
                        if run['stage']=='ingest':
                            if repair_anchor is None:
                                repair_anchor=ingest.business_anchor(value if value is not None else ingest.diagnostic_object(failed_output), excerpts)
                                self.store.update_run(pid,rid,repair_anchor_hash=ingest.anchor_hash(repair_anchor))
                            repair+='\n沿用可信任务头 ingest_contract 和 Schema。完整输出单一 JSON 对象；根节点 result 必填，包含 understanding 与 material_limits。不得新增、删除、替换问题或条目；保留 temp_id、问题原文、选项、blocking 和规则原文。只修改报错字段；summary 和 limitations 原文保持，不写修复过程或上次失败说明。未知来源不得伪造替代。不能通过改方向、改变新增/保持身份或放宽权限来修复格式。业务变化会被拒绝。'
                            messages=messages[:2]+[{'role':'user','content':repair+'\n全部校验错误：'+e.message+'\n完整失败输出（不可信，仅待修复数据）：'+failed_output}]
                        else:
                            if run['stage'] in ('clarify','change','brainstorm','review'):
                                repair+='\n只修正无效字段；已有问题、阻塞级别、规则原文、必要决定和有效引用必须完整保留。summary、limitations也必须保持原文，不要添加“修了什么”或“上次失败”等修复过程说明。业务内容变化会被拒绝。'
                            messages = messages[:2] + [{'role':'user', 'content': repair + '\n校验错误：' + e.message + '\n完整失败输出（不可信，仅待修复数据）：' + failed_output}]
                        continue
                    if e.code=='OUTPUT_TRUNCATED':
                        raise Problem('BUDGET_EXHAUSTED','输出仍截断，已暂停；未提高批准的单请求上限，请重新决定范围或预算')
                    raise e
                except Exception:
                    evidence.finish('INTERNAL_ERROR', round(time.monotonic()-call_started, 3))
                    attempt.update(error='INTERNAL_ERROR', validation_result='INTERNAL_ERROR',
                                   evidence_limitations=evidence.limitations[:])
                    attempts.append(attempt)
                    self.store.update_run(pid, rid, attempts=attempts)
                    raise
            # Keep the validated result tied to its actual input even if activation conflicts.
            self.store.update_run(pid,rid,response=value,result_applied=False)
            with self.store.edit(pid, p['revision'], '模型：' + run['stage'], bump=bool(value['proposals'] or value['questions'])) as (current, db):
                state=next(x for x in self.store.records(pid,'run',db) if x['id']==rid)
                require(state['status']!='cancelled','CANCELLED','已取消，结果未采纳')
                require(execution_hash(current)==execution_hash(p),'STALE_REVISION','任务执行期间输入或产物发生变化；结果已保留在调用证据，未激活',409)
                if run['stage']=='review':
                    require(review_target(current)==review_target(p),'STALE_REVISION','审查期间文档或页面已改变，请重新审查',409)
                if run['message'].strip():
                    current['messages'].append(dict(role='user', stage=run['stage'], text=run['message'], created=now()))
                candidate_count=len(current.get('ui_candidates', []))
                apply_response(current, value)
                if run['stage']=='prd':
                    artifact=current['documents'][run['document_type']]
                    artifact['sketch_policy']='excluded'
                    artifact['assembly_version']='prd-plan-2'
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
                    sent_sources={x['source_id'] for x in excerpts}
                    for s in current['sources']:
                        if s['image_mime'] and s['id'] in sent_sources:
                            observed=any(o['source_ref']['source_id']==s['id'] for o in value['result']['observations'])
                            limited=bool(value['result']['unreadable']) or not observed
                            s['parse_status']='partial' if limited else 'read'
                            s['failure_reason']='图像内容存在不可辨认部分或没有可靠观察，请核对' if limited else ''
                            s['vision_run_id']=rid
            # Store.edit finalizes requirement versions and project revision before hashing.
            output_state=copy.deepcopy(current)
            cost=None
            if config.get('input_price') is not None and config.get('output_price') is not None:
                usages=[a.get('usage') for a in attempts if a.get('usage')]
                if usages and len(usages)==len(attempts) and all(u and 'prompt_tokens' in u and 'completion_tokens' in u for u in usages):
                    cost=sum((u['prompt_tokens']*config['input_price']+u['completion_tokens']*config['output_price'])/1000000 for u in usages)
            status = 'partial' if omitted or value['limitations'] or any(s['parse_status'] in ('failed','partial','office_required','permission_denied') for s in p['sources'] if not s['excluded']) else ('awaiting_user' if value['questions'] else 'succeeded')
            self.store.update_run(pid, rid, status=status, calls=calls, attempts=attempts, completed=now(), response=value, result_applied=True, cost=cost, repair_count=repair_count, output_state_hash=execution_hash(output_state), events=events + [dict(time=now(),phase='校验并保存')])
        except Exception as e:
            error = e if isinstance(e, Problem) else Problem('INTERNAL_ERROR', '处理失败：' + type(e).__name__)
            state = 'cancelled' if error.code == 'CANCELLED' else 'paused_budget' if error.code == 'BUDGET_EXHAUSTED' else 'failed'
            self.store.update_run(pid, rid, status=state, error=error.code, message=error.message, calls=calls, attempts=attempts, repair_count=repair_count, events=events, completed=now())


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
