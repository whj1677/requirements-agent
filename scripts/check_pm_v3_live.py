"""Opt-in real provider check with synthetic fixtures; never daily writes.

Uses the existing Actions/Workflow/Provider and their evidence, validation and
bounded repair policies. Does not connect the static preview buttons to AI.
"""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def daily_fingerprint():
    with sqlite3.connect((ROOT / 'data/requirements.sqlite3').as_uri() + '?mode=ro', uri=True) as db:
        return {table: dict(count=len(rows), sha256=hashlib.sha256(json.dumps(rows, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest())
                for table in ('projects', 'revisions', 'records', 'settings', 'migrations')
                for rows in [db.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall()]}


async def run(args):
    assert args.execute_real_model, 'Explicit real-model execution is required'
    session = args.session.resolve()
    assert session.is_relative_to(ROOT / 'evidence') and session != ROOT / 'evidence'
    if args.phase == 'ingest':
        assert not session.exists(), 'Never silently recreate/retry a prior session'
        session.mkdir(parents=True)
    else:
        assert (session / 'project-id.txt').is_file(), 'Start with a recorded ingest result'
    phase_folder = session / args.phase
    assert not phase_folder.exists(), 'Phase already executed; preserve its result'
    phase_folder.mkdir()
    before = daily_fingerprint()
    write(phase_folder / 'daily-before.json', before)
    os.environ['RA_DATA_DIR'] = str(session / 'data')
    from app.store import Store
    from app.provider import Provider, DEFAULT, origin
    from app.workflow import Workflow
    from app.actions import Actions
    from app.core import Problem

    with urllib.request.urlopen('http://127.0.0.1:8765/api/models', timeout=5) as response:
        settings = json.load(response)
    config = {key: settings['model'].get(key, default) for key, default in DEFAULT.items()}
    assert origin(config) == 'https://api.deepseek.com', 'This test only authorizes the configured DeepSeek origin'
    provider = Provider(ROOT / '.env', credential_dir=ROOT / 'data/.credentials')
    assert provider.key(config), 'Configured key is unavailable; no request sent'
    safe_config = {key: config.get(key) for key in ('name', 'model', 'base_url', 'timeout', 'max_tokens', 'json_mode', 'thinking_disabled', 'input_price', 'output_price')}
    write(phase_folder / 'provider.json', {**safe_config, 'key_present': True})
    store = Store(session / 'data')
    store.setting('model', config)
    flow = Workflow(store, provider)
    actions = Actions(store, flow)

    if args.phase == 'ingest':
        fixture = None
        if args.fixture:
            fixture = json.loads(args.fixture.read_text(encoding='utf-8'))
            assert fixture.get('synthetic') is True, 'Only declared synthetic test material is allowed'
            assert fixture.get('sources') and fixture.get('case_id')
            assert all(s.get('text', '').strip() and s.get('purpose') in ('current', 'goal', 'reference', 'template') for s in fixture['sources'])
            write(session / 'fixture.json', fixture)
            raw = '\n\n'.join(s['title'] + '\n用途：' + s['purpose'] + '\n' + s['text'] for s in fixture['sources'])
            input_source = str(args.fixture.resolve().relative_to(ROOT)).replace('\\', '/')
        else:
            fixture_source = (ROOT / 'web/src/pm-prototype/model.ts').read_text(encoding='utf-8')
            match = re.search(r'export const SAMPLE = `([\s\S]*?)`;', fixture_source)
            assert match, 'Missing exact prototype fixture'
            raw = match.group(1)
            input_source = 'web/src/pm-prototype/model.ts:SAMPLE'
        (session / 'input.txt').write_text(raw, encoding='utf-8')
        write(session / 'input-identity.json', {'sha256': hashlib.sha256(raw.encode()).hexdigest(), 'characters': len(raw), 'source': input_source, 'synthetic': True})
        project = store.create(fixture['project_name'] if fixture else 'V3真实模型测试 · 客户跟进工作台（合成）')
        pid = project['id']
        (session / 'project-id.txt').write_text(pid, encoding='utf-8')
        if fixture:
            import httpx
            from app.main import create_app
            app = create_app(session / 'data', provider=provider)
            bindings = {}
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
                csrf = (await client.get('/api/session')).json()['csrf']
                headers = {'origin': 'http://testserver', 'x-csrf-token': csrf}
                for source in fixture['sources']:
                    reply = await client.post(f'/api/projects/{pid}/sources/text', headers=headers,
                        json={'expected_revision': store.get(pid)['revision'], **{k:source[k] for k in ('title','purpose','text')}})
                    assert reply.status_code == 200, 'Source API rejected synthetic material'
                    bindings[source['key']] = reply.json()['id']
                if fixture.get('intake_values'):
                    reply = await client.post(f'/api/projects/{pid}/intake/direct', headers=headers,
                        json={'expected_revision': store.get(pid)['revision'], 'values': fixture['intake_values']})
                    assert reply.status_code == 200, 'Direct intake API rejected known fixture information'
            write(session / 'source-bindings.json', bindings)
            message = fixture['message']
        else:
            message = '请将以下已有信息整理为可审阅的需求草稿，保留已知范围、规则与未定事项，不替用户作业务决定。\n\n' + raw
        action, target = 'organize', None
    else:
        pid = (session / 'project-id.txt').read_text(encoding='utf-8').strip()
        assert args.decisions, 'Clarification requires explicit synthetic decisions'
        decisions = json.loads(args.decisions.read_text(encoding='utf-8'))
        assert decisions['project_id'] == pid and decisions['source'] == 'reviewed_synthetic_test_decisions'
        import httpx
        from app.main import create_app
        # Exercise the real answer API in process, without another listening service.
        app = create_app(session / 'data', provider=provider)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
            csrf = (await client.get('/api/session')).json()['csrf']
            if decisions.get('intake_values'):
                project = store.get(pid)
                reply = await client.post(f'/api/projects/{pid}/intake/direct',
                    json={'expected_revision': project['revision'], 'values': decisions['intake_values']},
                    headers={'origin': 'http://testserver', 'x-csrf-token': csrf})
                assert reply.status_code == 200, 'Direct intake API rejected known fixture information'
            for decision in decisions['answers']:
                project = store.get(pid)
                existing = next(q for q in project['questions'] if q['id'] == decision['question_id'])
                if existing.get('answer') == decision['answer']:
                    continue
                reply = await client.post(f'/api/projects/{pid}/questions/{decision["question_id"]}',
                    json={'expected_revision': project['revision'], 'answer': decision['answer']},
                    headers={'origin': 'http://testserver', 'x-csrf-token': csrf})
                assert reply.status_code == 200, 'Answer API rejected the synthetic decision'
        message = decisions['message']
        action, target = 'clarify', decisions.get('target')

    project = store.get(pid)
    write(phase_folder / 'project-before.json', project)
    body = dict(expected_revision=project['revision'], action=action, message=message, document_type='prd', option_id=None, target=target)
    outcome = {'scope': 'real_provider_existing_workflow_synthetic_case', 'project_id': pid, 'phase': args.phase, 'frontend_live_connection': 'NOT_IMPLEMENTED', 'human_product_acceptance': 'NOT_RUN'}
    try:
        plan = actions.plan(pid, body)
        write(phase_folder / 'plan.json', plan)
        if plan['missing']:
            raise Problem('ACTION_BLOCKED', 'Task preconditions not satisfied: ' + '; '.join(plan['missing']))
        task = actions.start(pid, body, plan['plan_hash'], 'v3-live-' + str(uuid.uuid4()), True)
        tid = task['id']; last = None
        write(phase_folder / 'task-initial.json', task)
        while True:
            task = store.get_record(pid, tid, 'user_task')
            marker = (task['status'], task.get('calls'), task.get('message'))
            if marker != last:
                print(json.dumps({'phase': args.phase, 'status': task['status'], 'calls': task.get('calls'), 'message': task.get('message')}, ensure_ascii=False), flush=True)
                last = marker
            if task['status'] not in ('queued', 'running'):
                break
            await asyncio.sleep(.5)
        write(phase_folder / 'task-final.json', task)
        runs = store.records(pid, 'run')
        write(phase_folder / 'runs.json', runs)
        outcome.update(task_id=tid, task_status=task['status'], calls=task.get('calls'), cost=task.get('cost'), run_ids=task.get('run_ids'))
    except Problem as error:
        outcome.update(task_status='blocked', error_code=error.code, error_message=error.message)
    finally:
        write(phase_folder / 'project-after.json', store.get(pid))
        after = daily_fingerprint()
        write(phase_folder / 'daily-after.json', after)
        outcome['daily_unchanged'] = before == after
        write(phase_folder / 'outcome.json', outcome)
    assert outcome['daily_unchanged'], 'Daily content changed; inspect before continuing'
    print(json.dumps(outcome, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute-real-model', action='store_true')
    parser.add_argument('--session', required=True, type=Path)
    parser.add_argument('--fixture', type=Path, help='Optional synthetic multi-source fixture JSON; default is the V3 sample')
    parser.add_argument('--phase', choices=('ingest', 'clarify'), default='ingest')
    parser.add_argument('--decisions', type=Path)
    asyncio.run(run(parser.parse_args()))
