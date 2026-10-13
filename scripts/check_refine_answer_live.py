"""Opt-in, one real clarification task using the current UI's synthetic case.

No daily writes or second listener. Preserve the terminal task, including
failures; never replay to obtain a green result. The full isolated project is
left with candidate suggestions for a separate visible browser confirmation.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


async def run(args):
    assert args.execute_real_model, 'An explicit real-model run is required'
    session = args.session.resolve()
    assert session.is_relative_to(ROOT / 'evidence') and session != ROOT / 'evidence'
    assert not session.exists(), 'Existing evidence must never be replayed or overwritten'
    session.mkdir(parents=True)
    os.environ['RA_DATA_DIR'] = str((session / 'import-data').resolve())
    import httpx
    from app.provider import Provider, DEFAULT, origin
    from app.main import create_app
    from scripts.check_shared_draft_live import daily_fingerprint, safe_run_metrics
    from tests.check_refine_human_flow_browser import seed_human_flow, ANSWER_PERMISSION

    class CountedProvider(Provider):
        requests = 0

        async def request(self, config, messages, evidence=None):
            self.requests += 1
            write(session / 'provider-requests.json', dict(requests=self.requests, status='request_started'))
            return await super().request(config, messages, evidence=evidence)

    before = daily_fingerprint()
    write(session / 'daily-before.json', before)
    outcome = dict(scope='one_real_clarify_task_with_synthetic_storage_alarm_case',
                   provider_requests=0, cost=None, paid_request_status='NOT_STARTED',
                   human_product_acceptance='NOT_RUN', ui_recording='NOT_RUN')
    provider = None
    app = None
    pid = None
    try:
        async with httpx.AsyncClient(trust_env=False) as live:
            response = await live.get('http://127.0.0.1:8765/api/models', timeout=10)
            response.raise_for_status()
            settings = response.json()['model']
        config = {key: settings.get(key, default) for key, default in DEFAULT.items()}
        provider = CountedProvider(ROOT / '.env', credential_dir=ROOT / 'data/.credentials')
        assert provider.key(config), 'Current configured key unavailable; no request sent'
        write(session / 'provider.json', {**{key:config.get(key) for key in
            ('name','model','base_url','timeout','max_tokens','json_mode','thinking_disabled','input_price','output_price')},
            'origin':origin(config), 'key_present':True})
        app = create_app(session / 'data', provider=provider)
        store = app.state.store
        store.setting('model', config)
        pid, frozen = seed_human_flow(store)
        (session / 'project-id.txt').write_text(pid, encoding='utf-8')
        write(session / 'fixture-project.json', frozen)
        outcome['project_id'] = pid
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
            csrf = (await client.get('/api/session')).json()['csrf']
            headers = {'origin':'http://testserver', 'x-csrf-token':csrf}

            async def post(path, body):
                reply = await client.post(f'/api/projects/{pid}' + path, headers=headers, json=body)
                assert reply.status_code == 200, f'{path}: HTTP {reply.status_code}: {reply.text[:900]}'
                return reply.json()

            saved = await post('/decision-review/answer', dict(expected_revision=frozen['revision'],
                               question_id='Q-PERMISSION', answer=ANSWER_PERMISSION))
            write(session / 'saved-answer.json', saved)
            project = store.get(pid)
            write(session / 'project-before.json', project)
            body = dict(expected_revision=project['revision'], action='clarify',
                message='只根据已保存回答，整理当前问题 Q-PERMISSION 对关联的场站告警协作需求的修改。'
                        '回答原话是“不能相互修改，但都能查看。”。更新相应权限说明，保留其它已有业务内容。'
                        '这是本期新增独立模块，不改成既有系统的功能改造。交付形态与日报问题没有本次答案，继续待定。'
                        '产出供人工核对的候选，不自动采纳。',
                document_type='prd', option_id=None, target=dict(kind='question',id='Q-PERMISSION'))
            plan = await post('/actions/plan', body)
            write(session / 'plan.json', plan)
            assert not plan['missing'], '; '.join(plan['missing'])
            task = await post('/actions', dict(body, plan_hash=plan['plan_hash'],
                             idempotency_key='refine-live-'+str(uuid.uuid4()), authorize=True))
            write(session / 'task-initial.json', task)
            outcome.update(task_id=task['id'], paid_request_status='TASK_STARTED')
            started = time.monotonic()
            marker = None
            while True:
                tasks = (await client.get(f'/api/projects/{pid}/actions')).json()
                task = next(row for row in tasks if row['id'] == task['id'])
                latest_marker = (task['status'], task.get('calls'), task.get('message'))
                if latest_marker != marker:
                    print(json.dumps(dict(status=task['status'], calls=task.get('calls'),
                                          message=task.get('message')), ensure_ascii=False), flush=True)
                    marker = latest_marker
                if task['status'] not in ('queued','running'):
                    break
                await asyncio.sleep(.5)
            write(session / 'task-final.json', task)
            runs = (await client.get(f'/api/projects/{pid}/runs')).json()
            write(session / 'runs.json', runs)
            write(session / 'metrics.json', safe_run_metrics(runs))
            shown = (await client.get(f'/api/projects/{pid}')).json()
            write(session / 'project-view.json', shown)
            outcome.update(task_status=task['status'], task_calls=task.get('calls'),
                           provider_requests=provider.requests, cost=task.get('cost'),
                           elapsed_seconds=round(time.monotonic()-started,3),
                           paid_request_status='TASK_TERMINAL_NO_REPLAY',
                           candidates=[dict(id=item['id'],kind=item['kind'],target_item_id=item.get('target_item_id'),
                                            answer_refs=item.get('answer_refs')) for item in shown['items']
                                       if item['selection_status']=='candidate'],
                           proposals=[dict(id=q['id'],current=q.get('decision_review_proposal_current'),
                                           status=(q.get('decision_review_proposal') or {}).get('status'))
                                      for q in shown['questions'] if q.get('decision_review_proposal')])
    except Exception as error:
        outcome.update(status='failed_or_blocked', error_code=getattr(error,'code',type(error).__name__),
                       error_message=str(error), provider_requests=provider.requests if provider else 0)
    finally:
        if app and pid:
            write(session / 'project-after.json', app.state.store.get(pid))
        after = daily_fingerprint()
        write(session / 'daily-after.json', after)
        outcome['daily_unchanged'] = before == after
        write(session / 'provider-requests.json', dict(
            requests=provider.requests if provider else 0,
            status=outcome['paid_request_status']))
        write(session / 'outcome.json', outcome)
        print(json.dumps(outcome, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute-real-model', action='store_true')
    parser.add_argument('--session', required=True, type=Path)
    asyncio.run(run(parser.parse_args()))
