"""Opt-in real-model check of the shared draft with a frozen synthetic case.

Preparation/compilation never sends a request. Running requires
--execute-real-model and an isolated new evidence session. A phase is never
replayed automatically, including when the paid request has an unknown result.
"""

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE = ROOT / 'tests/fixtures/large-storage-decision-v1.json'
ALLOWED_FIXTURES = {
    (ROOT / f'tests/fixtures/large-storage-decision-{version}.json').resolve()
    for version in ('v1', 'v2')
}
sys.path.insert(0, str(ROOT))


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def daily_fingerprint():
    database = ROOT / 'data/requirements.sqlite3'
    if not database.is_file():
        return {'database': 'absent'}
    with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as db:
        return {table: dict(count=len(rows), sha256=hashlib.sha256(
            json.dumps(rows, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest())
                for table in ('projects', 'revisions', 'records', 'settings', 'migrations')
                for rows in [db.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall()]}


def valid_session_path(value):
    path = value.resolve()
    evidence = (ROOT / 'evidence').resolve()
    assert path.parent == evidence and path.name.startswith('shared-draft-live-20261008-'), \
        'Session must be evidence/shared-draft-live-20261008-* directly under this repository'
    return path


def session_folder(value, phase):
    path = valid_session_path(value)
    if phase == 'ingest':
        assert not path.exists(), 'New ingest must use a fresh session path'
        path.mkdir()
    else:
        assert (path / 'project-id.txt').is_file(), 'Clarify requires a recorded ingest session'
        ingest = json.loads((path / 'ingest/outcome.json').read_text(encoding='utf-8'))
        assert ingest.get('task_status') not in ('queued', 'running'), 'Ingest is still active'
    output = path / phase
    assert not output.exists(), 'Phase evidence already exists; do not replay a paid action'
    output.mkdir()
    return path, output


def fixture(path):
    path = path.resolve()
    assert path in ALLOWED_FIXTURES, 'Only frozen large-storage-decision v1/v2 fixtures are allowed'
    value = json.loads(path.read_text(encoding='utf-8'))
    assert value['case_id'] == path.stem and value['synthetic'] is True
    assert len(value['sources']) == 3 and all(s['text'].strip() and s['purpose'] in
            ('current', 'goal', 'reference', 'template') for s in value['sources'])
    return value


async def api_post(client, path, headers, payload):
    response = await client.post(path, headers=headers, json=payload)
    assert response.status_code == 200, f'{path}: HTTP {response.status_code} {response.text[:500]}'
    return response.json()


async def prepare_ingest(store, provider, session, output, case):
    import httpx
    from app.main import create_app

    project = store.create(case['project_name'])
    pid = project['id']
    (session / 'project-id.txt').write_text(pid, encoding='utf-8')
    app = create_app(session / 'data', provider=provider)
    bindings = {}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
        csrf = (await client.get('/api/session')).json()['csrf']
        headers = {'origin': 'http://testserver', 'x-csrf-token': csrf}
        for item in case['sources']:
            reply = await api_post(client, f'/api/projects/{pid}/sources/text', headers,
                {'expected_revision': store.get(pid)['revision'],
                 **{key: item[key] for key in ('title', 'purpose', 'text')}})
            bindings[item['key']] = reply['id']
        known = case['intake_values']
        draft = '\n'.join((f'产品：{known["product"]}', f'本次范围：{known["module"]}',
                           f'目标：{known["intent"]}'))
        await api_post(client, f'/api/projects/{pid}/workspace-draft', headers,
            {'expected_revision': store.get(pid)['revision'], 'text': draft,
             'entry_mode': 'direct'})
        await api_post(client, f'/api/projects/{pid}/domain-context', headers,
            {'expected_revision': store.get(pid)['revision'],
             'background': '大型储能集成商云平台产品经理。此背景只帮助理解领域术语和追问，不自动增加模块或业务规则。',
             'selected_source_ids': None})
    assert store.get(pid).get('workflow_mode') == 'shared-draft'
    write(session / 'source-bindings.json', bindings)
    write(output / 'workspace-draft.json', {'sha256': hashlib.sha256(draft.encode()).hexdigest(),
                                           'characters': len(draft), 'mode': 'shared-draft'})
    return pid, case['message'], None


async def prepare_clarify(store, provider, session, output, question_id, target_question_id):
    import httpx
    from app.decision_review import decision_ids
    from app.main import create_app

    pid = (session / 'project-id.txt').read_text(encoding='utf-8').strip()
    project = store.get(pid)
    ids = decision_ids(project, question_id)
    assert ids, 'Question group has no current decision'
    leaf = target_question_id or ids[0]
    assert leaf in ids, 'Target question must be a leaf of the selected group'
    assert project.get('workflow_mode') == 'shared-draft', 'Session is not a shared draft'
    app = create_app(session / 'data', provider=provider)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
        csrf = (await client.get('/api/session')).json()['csrf']
        headers = {'origin': 'http://testserver', 'x-csrf-token': csrf}
        answer = await api_post(client, f'/api/projects/{pid}/decision-review/answer', headers,
            {'expected_revision': project['revision'], 'question_id': question_id,
             'answer': '不要自动重算。'})
    write(output / 'answer-binding.json', {'question_id': question_id,
                                           'decision_ids': ids, 'target_question_id': leaf,
                                           'answer_id': answer['answer_id'],
                                           'answer_hash': answer['answer_hash'],
                                           'answer': '不要自动重算。'})
    message = ('只处理决定组 ' + question_id + ' 的当前原话“不要自动重算。”。'
               '逐项核对数据版本选择、是否自动重算、人工重算触发人的覆盖；'
               '仅被原话明确回答的决定可产生候选修改，未回答项保持待定。'
               '保留所有其它已知需求、来源与未知，不自动采纳。')
    return pid, message, {'kind': 'question', 'id': leaf}


def safe_run_metrics(runs):
    return [{
        'run_id': run['id'], 'stage': run.get('stage'), 'status': run.get('status'),
        'calls': run.get('calls'), 'cost': run.get('cost'),
        'decision_review': run.get('decision_review'),
        'attempts': [{key: attempt.get(key) for key in
                      ('call', 'call_id', 'subphase', 'validation_result', 'error',
                       'usage', 'elapsed_seconds', 'finish_reason')}
                     for attempt in run.get('attempts', [])],
        'input_metrics': run.get('input_metrics'),
        'sent_excerpt_ids': run.get('sent_excerpt_ids'),
        'omitted_excerpt_ids': run.get('omitted_excerpt_ids'),
        'source_snapshot': run.get('source_snapshot'),
    } for run in runs]


async def run(args):
    assert args.execute_real_model, 'Explicit --execute-real-model is required'
    assert args.max_calls is None or args.max_calls >= 1
    if args.phase == 'clarify':
        assert args.question_id, 'Clarify requires an explicit --question-id (Q or QG)'
    candidate = valid_session_path(args.session)
    os.environ['RA_DATA_DIR'] = str(candidate / 'data')
    from app.actions import Actions
    from app.core import Problem
    from app.provider import DEFAULT, Provider, origin
    from app.store import Store
    from app.workflow import Workflow

    with urllib.request.urlopen('http://127.0.0.1:8765/api/models', timeout=5) as response:
        settings = json.load(response)
    config = {key: settings['model'].get(key, default) for key, default in DEFAULT.items()}
    assert origin(config) == args.expected_origin, 'Configured model origin differs from --expected-origin'
    provider = Provider(ROOT / '.env', credential_dir=ROOT / 'data/.credentials')
    assert provider.key(config), 'Configured key is unavailable; no model request sent'
    session, output = session_folder(candidate, args.phase)
    before = daily_fingerprint()
    write(output / 'daily-before.json', before)
    write(output / 'provider.json', {key: config.get(key) for key in
                                     ('name', 'model', 'base_url', 'timeout', 'max_tokens',
                                      'json_mode', 'thinking_disabled', 'input_price',
                                      'output_price')})
    store = Store(session / 'data')
    store.setting('model', config)
    flow = Workflow(store, provider)
    actions = Actions(store, flow)
    outcome = {'scope': 'isolated_shared_draft_real_provider_synthetic_case',
               'case_id': None, 'phase': args.phase,
               'human_product_acceptance': 'NOT_RUN', 'paid_request_status': 'NOT_STARTED'}
    pid = None
    tid = None
    try:
        if args.phase == 'ingest':
            case = fixture(args.fixture)
            write(session / 'fixture.json', case)
            write(session / 'fixture-identity.json', {
                'case_id': case['case_id'],
                'sha256': hashlib.sha256(args.fixture.resolve().read_bytes()).hexdigest(),
                'path': args.fixture.resolve().relative_to(ROOT).as_posix(),
                'synthetic': True})
            outcome['case_id'] = case['case_id']
            pid, message, target = await prepare_ingest(store, provider, session, output, case)
            action = 'organize'
        else:
            assert (session / 'fixture-identity.json').is_file()
            identity = json.loads((session / 'fixture-identity.json').read_text(encoding='utf-8'))
            outcome['case_id'] = identity['case_id']
            pid, message, target = await prepare_clarify(
                store, provider, session, output, args.question_id, args.target_question_id)
            action = 'clarify'
        project = store.get(pid)
        write(output / 'project-before.json', project)
        body = dict(expected_revision=project['revision'], action=action,
                    message=message, document_type='prd', option_id=None,
                    target=target, max_calls=args.max_calls)
        plan = actions.plan(pid, body)
        write(output / 'plan.json', plan)
        if plan['missing']:
            raise Problem('ACTION_BLOCKED', '；'.join(plan['missing']))
        started = time.monotonic()
        task = actions.start(pid, body, plan['plan_hash'],
                             'shared-draft-live-' + str(uuid.uuid4()), True)
        tid = task['id']
        outcome.update(project_id=pid, task_id=tid, paid_request_status='TASK_STARTED')
        write(output / 'task-initial.json', task)
        last = None
        while True:
            current = store.get_record(pid, tid, 'user_task')
            marker = (current['status'], current.get('calls'), current.get('message'))
            if marker != last:
                print(json.dumps({'phase': args.phase, 'status': current['status'],
                                  'calls': current.get('calls'), 'message': current.get('message')},
                                 ensure_ascii=False), flush=True)
                last = marker
            if current['status'] not in ('queued', 'running'):
                break
            await asyncio.sleep(.5)
        outcome.update(task_status=current['status'], calls=current.get('calls'),
                       cost=current.get('cost'), run_ids=current.get('run_ids'),
                       elapsed_seconds=round(time.monotonic() - started, 3),
                       paid_request_status='TASK_TERMINAL_SEE_RUN_ATTEMPTS')
        write(output / 'task-final.json', current)
        runs = store.records(pid, 'run')
        write(output / 'runs.json', runs)
        write(output / 'metrics.json', safe_run_metrics(runs))
    except Problem as error:
        outcome.update(task_status='blocked', error_code=error.code,
                       error_message=error.message)
    finally:
        if pid:
            write(output / 'project-after.json', store.get(pid))
            if tid:
                latest = store.get_record(pid, tid, 'user_task')
                write(output / 'task-last-seen.json', latest)
                if latest['status'] in ('queued', 'running'):
                    outcome['paid_request_status'] = 'UNKNOWN_ON_INTERRUPTION'
                    outcome['task_status'] = latest['status']
                runs = store.records(pid, 'run')
                write(output / 'runs-last-seen.json', runs)
                write(output / 'metrics-last-seen.json', safe_run_metrics(runs))
        after = daily_fingerprint()
        write(output / 'daily-after.json', after)
        outcome['daily_unchanged'] = before == after
        write(output / 'outcome.json', outcome)
    assert outcome['daily_unchanged'], 'Daily data changed; inspect evidence before continuing'
    print(json.dumps(outcome, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute-real-model', action='store_true')
    parser.add_argument('--session', required=True, type=Path,
                        help='Fresh evidence/shared-draft-live-20261008-* session')
    parser.add_argument('--phase', choices=('ingest', 'clarify'), default='ingest')
    parser.add_argument('--fixture', type=Path, default=DEFAULT_FIXTURE,
                        help='Explicit frozen v1 or v2 fixture for ingest; default remains v1')
    parser.add_argument('--question-id', help='Explicit Q or QG from reviewed ingest output')
    parser.add_argument('--target-question-id', help='Optional leaf Q within the selected group')
    parser.add_argument('--max-calls', type=int, default=None,
                        help='Optional user-chosen total request cap; default has no added hard cap')
    parser.add_argument('--expected-origin', default='https://api.deepseek.com')
    asyncio.run(run(parser.parse_args()))
