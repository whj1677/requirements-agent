"""Optional action-wide request admission, persisted before the HTTP boundary.

Slots are conservative reservations, not provider tokens or bills. A process
death between reservation and dispatch leaves an unknown slot occupied.
"""
from .core import dumps, execution_hash, now, require


def validate_limit(limit):
    require(limit is None or type(limit) is int and limit >= 1,
            'BUDGET_INVALID', 'max_calls 必须为空或大于等于 1 的整数')
    return limit


def initial_budget(limit):
    return dict(limit=validate_limit(limit), used=0, reservations=[])


def check_available(budget):
    require(budget['used'] < budget['limit'], 'BUDGET_EXHAUSTED',
            f"本次请求额度已用 {budget['used']}/{budget['limit']}；已有结果保留，后续步骤未执行。继续需重新核对计划并明确授权。")


class RequestBudget:
    def __init__(self, store, pid, rid, limit):
        self.store, self.pid, self.rid = store, pid, rid
        self.limit = validate_limit(limit)

    def _records(self, db):
        rows = []
        run = next((r for r in self.store.records(self.pid, 'run', db) if r['id']==self.rid), None)
        require(run is not None, 'NOT_FOUND', '模型步骤不存在', 404)
        require(run['provider'].get('max_calls')==self.limit,
                'BUDGET_AUTH_CHANGED', '执行上限与已批准快照不一致', 409)
        rows.append(run)
        if run.get('user_task_id'):
            task = next((t for t in self.store.records(self.pid,'user_task',db)
                         if t['id']==run['user_task_id']), None)
            require(task is not None and task.get('max_calls')==self.limit and
                    task['authorized_configs'][run['stage']].get('max_calls')==self.limit,
                    'BUDGET_AUTH_CHANGED', '步骤上限与本次授权任务不一致', 409)
            rows.append(task)
        require(all(r.get('request_budget',{}).get('limit')==self.limit for r in rows),
                'BUDGET_AUTH_CHANGED', '额度记录与已批准快照不一致', 409)
        return rows

    def available(self):
        with self.store.connect() as db:
            check_available(self._records(db)[-1]['request_budget'])

    def reserve(self, call_id, config):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            rows = self._records(db)
            require(config.get('max_calls')==self.limit, 'BUDGET_AUTH_CHANGED',
                    '出站请求上限与批准快照不一致', 409)
            require({k:v for k,v in config.items() if k not in ('proxy','max_tokens')}==
                    {k:v for k,v in rows[0]['provider'].items() if k!='max_tokens'} and
                    config['max_tokens']<=rows[0]['authorization']['approved_max_tokens'],
                    'BUDGET_AUTH_CHANGED','出站配置超出已批准快照',409)
            require(all(r['status'] in ('queued','running') for r in rows),
                    'CANCELLED', '任务已停止，未发送新的请求')
            p = self.store.get(self.pid, db)
            require(execution_hash(p)==rows[0]['input_state_hash'], 'STALE_REVISION',
                    '模型发送前输入已变化；请重新核对计划', 409)
            from .provider import origin
            grant=p['grants'].get(origin(config),{})
            require({s['id'] for s in p['sources'] if not s['excluded']} <= set(grant.get('source_ids',[]))
                    and origin(config) in p['grants'], 'DATA_AUTH_REQUIRED', '模型发送前资料授权已变化', 403)
            check_available(rows[-1]['request_budget'])
            require(not any(v['call_id']==call_id for v in rows[-1]['request_budget']['reservations']),
                    'REQUEST_ALREADY_RESERVED', '此请求已预留额度，不得重复发送', 409)
            for row in rows:
                budget=row['request_budget']; budget['used']+=1
                budget['reservations'].append(dict(call_id=call_id,run_id=self.rid,
                    state='reserved_unknown',reserved_at=now()))
                db.execute('UPDATE records SET payload=? WHERE id=?',(dumps(row),row['id']))

    def mark(self, call_id, state):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            rows=self._records(db)
            if state=='dispatch_unknown':
                require(all(r['status'] in ('queued','running') for r in rows),
                        'CANCELLED','预留后任务已停止，未启动发送；额度保守占用')
            for row in rows:
                entry=next(v for v in row['request_budget']['reservations'] if v['call_id']==call_id)
                if state=='dispatch_unknown':
                    require(entry['state']=='reserved_unknown', 'REQUEST_ALREADY_RESERVED', '不得重发已启动请求', 409)
                    row['calls']+=1
                elif state=='response_received':
                    require(entry['state'] in ('dispatch_unknown','response_received'),
                            'BUDGET_AUTH_CHANGED', '未启动的请求不能登记为已收到响应', 409)
                entry.update(state=state,updated_at=now())
                db.execute('UPDATE records SET payload=? WHERE id=?',(dumps(row),row['id']))
