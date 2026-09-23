import asyncio
import copy
import time
from .core import KIT, Problem, brief_hash, digest, dumps, hashes, ident, now, require, ui_view_hash
from .contracts import gate, review_target, validate_response, PROFILES
from .provider import DEFAULT, STAGES, assemble, origin

PREFIX = {'requirement':'REQ','rule':'RULE','acceptance':'AC','ui_decision':'UID','goal':'GOAL','actor':'ROLE'}


def replace_refs(value, mapping):
    if isinstance(value, str):
        return mapping.get(value, value)
    if isinstance(value, list):
        return [replace_refs(v, mapping) for v in value]
    if isinstance(value, dict):
        return {k:replace_refs(v,mapping) for k,v in value.items()}
    return value


def apply_response(p, response):
    mapping = {x['temp_id']:ident(PREFIX.get(x['kind'], 'ITEM')) for x in response['proposals']}
    mapping.update({q['temp_id']:ident('Q') for q in response['questions']})
    r = replace_refs(response, mapping)
    for item in r['proposals']:
        # A proposed revision is a separate candidate, never an overwrite.
        p['items'].append(dict(id=item['temp_id'], **{k:v for k,v in item.items() if k != 'temp_id'}, selection_status='candidate', revision=p['revision']+1))
    for q in r['questions']:
        prior = next((x for x in p['questions'] if x['topic_key'] == q['topic_key']), None)
        if prior:
            continue
        p['questions'].append(dict(id=q['temp_id'], **{k:v for k,v in q.items() if k != 'temp_id'}, status='open', answer=None))
    stage = r['stage']
    if stage == 'brainstorm':
        for option in r['result']['options']:
            p['options'].append(dict(option, id=ident('OPT'), selection_status='candidate'))
    if stage == 'ui':
        next_ui = dict(spec=r['result']['spec'], brief_hash=brief_hash(p), created=now())
        if p['ui'] and ui_view_hash(p['ui']['spec']) != ui_view_hash(next_ui['spec']):
            p.setdefault('ui_candidates', []).append(dict(id=ident('UIC'), **next_ui,
                base_ui_hash=digest(p['ui']['spec']), status='candidate'))
        elif not p['ui']:
            p['ui'] = next_ui
    if stage == 'prd':
        kind = r['result']['document_type']
        p['documents'][kind] = dict(id=ident('DOC'), content=r['result'], brief_hash=brief_hash(p), draft_revision=p['revision'], item_snapshot=copy.deepcopy(p['items']), created=now(), style_version='1', generator_version='1.1', reference_hashes={s['reference_id']:s['sha256'] for s in PROFILES['sources']} if p.get('reference_mode')!='builtin' else {}, limitations=r['limitations'])
    if stage == 'review':
        p['review'] = dict(target_hash=review_target(p), response=r, created=now())
    p['messages'].append(dict(role='assistant', stage=stage, text=r['summary'], response=r, created=now()))


class Workflow:
    def __init__(self, store, provider):
        self.store, self.provider = store, provider
        self.tasks = {}

    def config(self, stage):
        return self.store.setting('vision' if stage == 'vision' else 'model') or dict(DEFAULT)

    def start(self, pid, revision, stage, message, kind='prd', resumed_from=None):
        require(stage in STAGES and stage != 'handoff', 'STAGE_INVALID', '请选择支持的分析阶段')
        config = self.config(stage)
        p = self.store.get(pid)
        require(p['revision'] == revision, 'STALE_REVISION', '页面已过期', 409)
        run = dict(stage=stage, status='queued', revision=revision, message=message, document_type=kind, calls=0, attempts=[], events=[], created=now(), error=None, resumed_from=resumed_from, provider={k:v for k,v in config.items() if k not in ('proxy',)}, prompt_version='1.1', cost=None)
        rid = self.store.record(pid, 'run', run)
        task = asyncio.create_task(self.execute(pid, rid, p, config))
        self.tasks[rid] = task
        task.add_done_callback(lambda _: self.tasks.pop(rid, None))
        return dict(id=rid, **run)

    async def execute(self, pid, rid, p, config):
        started = time.monotonic()
        run = self.store.get_record(pid, rid, 'run')
        calls, repair_count, retries = 0, 0, 0
        attempts, events = [], []
        try:
            require(self.provider.key(config), 'CONFIG_MISSING', '请先在模型设置中配置此接收端的 Key')
            require(origin(config) in p['grants'], 'DATA_AUTH_REQUIRED', '请先确认该接收端和项目材料的发送授权')
            current_sources = {s['id'] for s in p['sources'] if not s['excluded']}
            require(current_sources <= set(p['grants'][origin(config)]['source_ids']), 'DATA_AUTH_REQUIRED', '新增材料尚未授权发送，请检查材料范围')
            messages, excerpts, omitted = assemble(p, run['stage'], run['message'], config, self.store.folder, run['document_type'])
            self.store.update_run(pid, rid, status='running', sent_excerpt_ids=[x['id'] for x in excerpts], omitted_excerpt_ids=omitted, input_hash=digest(messages))
            while True:
                state = self.store.get_record(pid, rid, 'run')
                require(state['status'] != 'cancelled', 'CANCELLED', '任务已取消；已发请求仍可能计费')
                require(calls < config['max_calls'] and time.monotonic()-started < config['action_seconds'], 'BUDGET_EXHAUSTED', '动作预算耗尽；保存进度，可明确续跑')
                calls += 1
                events.append(dict(time=now(), phase='调用模型', call=calls))
                self.store.update_run(pid, rid, calls=calls, events=events)
                value = None
                try:
                    value, meta = await asyncio.wait_for(self.provider.request(config, messages), timeout=max(1, config['action_seconds']-(time.monotonic()-started)))
                    attempts.append(dict(call=calls, **meta))
                    require(self.store.get_record(pid, rid, 'run')['status'] != 'cancelled', 'CANCELLED', '已取消，结果未采纳')
                    validate_response(value, run['stage'], p, excerpts, run['document_type'])
                    break
                except (Problem, asyncio.TimeoutError) as e:
                    if isinstance(e, asyncio.TimeoutError):
                        e = Problem('BUDGET_EXHAUSTED', '动作时间预算耗尽')
                    attempts.append(dict(call=calls, error=e.code))
                    self.store.update_run(pid, rid, attempts=attempts)
                    if e.code in ('NETWORK_ERROR','TIMEOUT','RATE_LIMITED','PROVIDER_ERROR') and retries < 2:
                        retries += 1
                        await asyncio.sleep(min(retries, 2))
                        continue
                    if e.code in ('SCHEMA_INVALID','REFERENCE_INVALID','OUTPUT_EMPTY','OUTPUT_TRUNCATED') and repair_count < 2:
                        repair_count += 1
                        messages = messages[:2] + [{'role':'user', 'content': (KIT/'prompts/10_repair.md').read_text('utf-8') + '\n校验错误：' + e.message + '\n原输出（不可信）：' + dumps(value)}]
                        continue
                    raise e
            with self.store.edit(pid, p['revision'], '模型：' + run['stage'], bump=bool(value['proposals'] or value['questions'])) as (current, db):
                state=next(x for x in self.store.records(pid,'run',db) if x['id']==rid)
                require(state['status']!='cancelled','CANCELLED','已取消，结果未采纳')
                if run['stage']=='review':
                    require(review_target(current)==review_target(p),'STALE_REVISION','审查期间文档或页面已改变，请重新审查',409)
                current['messages'].append(dict(role='user', stage=run['stage'], text=run['message'], created=now()))
                candidate_count=len(current.get('ui_candidates', []))
                apply_response(current, value)
                if run['stage']=='prd':
                    artifact=current['documents'][run['document_type']]
                    current['stale_document_kinds']=[kind for kind in current.get('stale_document_kinds',[]) if kind!=run['document_type']]
                    current['document_update_needed']=bool(current['stale_document_kinds'])
                    self.store.record(pid,'document_artifact',artifact,db=db)
                elif run['stage']=='ui':
                    artifact=current['ui_candidates'][-1] if len(current.get('ui_candidates', []))>candidate_count else current['ui']
                    self.store.record(pid,'ui_artifact',artifact,db=db)
                elif run['stage']=='review':
                    self.store.record(pid,'review',current['review'],db=db)
                if run['stage']=='vision':
                    sent_sources={x['source_id'] for x in excerpts}
                    for s in current['sources']:
                        if s['image_mime'] and s['id'] in sent_sources:
                            s['parse_status']='read'
                            s['vision_run_id']=rid
            cost=None
            if config.get('input_price') is not None and config.get('output_price') is not None:
                usages=[a.get('usage') for a in attempts if 'error' not in a]
                if usages and all(u and 'prompt_tokens' in u and 'completion_tokens' in u for u in usages):
                    cost=sum((u['prompt_tokens']*config['input_price']+u['completion_tokens']*config['output_price'])/1000000 for u in usages)
            status = 'partial' if omitted or value['limitations'] or any(s['parse_status'] in ('failed','partial') for s in p['sources'] if not s['excluded']) else ('awaiting_user' if value['questions'] else 'succeeded')
            self.store.update_run(pid, rid, status=status, calls=calls, attempts=attempts, completed=now(), response=value, cost=cost, repair_count=repair_count, events=events + [dict(time=now(),phase='校验并保存')])
        except Exception as e:
            error = e if isinstance(e, Problem) else Problem('INTERNAL_ERROR', '处理失败：' + type(e).__name__)
            state = 'cancelled' if error.code == 'CANCELLED' else 'paused_budget' if error.code == 'BUDGET_EXHAUSTED' else 'failed'
            self.store.update_run(pid, rid, status=state, error=error.code, message=error.message, calls=calls, attempts=attempts, completed=now())


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
        selected = {i['id'] for i in p['items'] if i['selection_status'] == 'selected' and i['applies_to'] == 'to_be'}
        require(set(body['scope_ids']) == selected, 'SCOPE_CONFLICT', '确认范围与当前已选目标不一致')
        cid, bid = ident('CONF'), ident('BASE')
        confirmation = dict(baseline_id=bid, revision=p['revision'], **hashes(p), scope_ids=sorted(selected), actor='authenticated_local_user', created=now(), idempotency_key=body['idempotency_key'], request_hash=request_hash)
        store.record(pid, 'confirmation', confirmation, cid, db)
        store.record(pid, 'baseline', dict(project=copy.deepcopy(p), confirmation_id=cid, hashes=hashes(p), parent_baseline_id=p['active_baseline_id']), bid, db)
        p['active_baseline_id'] = bid
        db.execute('UPDATE projects SET payload=? WHERE id=?', (dumps(p), pid))
        store.record(pid, 'audit', dict(actor='authenticated_local_user', action='确认指定 PRD 内容版本', confirmation_id=cid, baseline_id=bid), db=db)
        return dict(id=cid, **confirmation)
