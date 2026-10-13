"""Opt-in real-provider trial, starting with the production assistant UI.

The browser serves the root app/build through an in-process ASGI transport.
There is no listener, project fixture, hidden business seed, or daily Store.
Each invocation performs one explicit phase and preserves failures verbatim.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import re
import sqlite3
import sys
import traceback
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
ORIGIN = 'http://127.0.0.1:8765'
DEFAULT_OUTPUT = ROOT / 'work/chat-prd-real-20261013'
TITLE = '真实对话验证：多站点告警批量派单'
ACTIVE = ('queued', 'running')


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def safe_config(value):
    """Only a documented allowlist; never serialize credentials or proxy URLs."""
    names = ('name', 'base_url', 'model', 'key_env', 'business_draft_protocol',
             'max_tokens', 'timeout', 'action_seconds', 'context_chars', 'max_calls',
             'json_mode', 'thinking_disabled', 'review_thinking')
    return {name: value.get(name) for name in names if name in value}


def load_config():
    from app.provider import DEFAULT
    with sqlite3.connect((ROOT / 'data/requirements.sqlite3').as_uri() + '?mode=ro', uri=True) as db:
        row = db.execute("SELECT payload FROM settings WHERE id='model'").fetchone()
    current = {**DEFAULT, **(json.loads(row[0]) if row else {})}
    # Exercise the normal configured Agent; do not tune budgets to pass this run.
    return current, {}


def call_evidence(folder):
    results = []
    for path in sorted((folder / 'evidence/model-calls').glob('*.json')):
        row = json.loads(path.read_text('utf-8'))
        text = row.get('final_output')
        results.append({**{key: row.get(key) for key in (
            'call_id', 'run_id', 'stage', 'model', 'origin', 'repair_of', 'usage',
            'response_received', 'validation_result', 'actual_input_hash')},
            'platform_knowledge': row.get('authorization', {}).get('platform_knowledge'),
            'evidence_file': str(path),
            'raw_output_sha256': sha(text.encode('utf-8')) if isinstance(text, str) else None})
    return results


def assert_idle(folder):
    """Inspect before constructing Store, which marks interrupted runs paused."""
    path = folder / 'requirements.sqlite3'
    if not path.exists():
        return
    with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
        rows = db.execute("SELECT payload FROM records WHERE kind IN ('run','user_task')").fetchall()
    active = [json.loads(row[0])['id'] for row in rows if json.loads(row[0]).get('status') in ACTIVE]
    if active:
        raise RuntimeError('Previous phase still has active records; do not retry: ' + ', '.join(active))


async def run(args):
    output = args.output.resolve()
    if not output.is_relative_to((ROOT / 'work').resolve()):
        raise ValueError('Output must be under repository work/')
    if args.phase != 'inspect' and not args.allow_real_provider:
        raise ValueError('Real model execution requires --allow-real-provider')
    if args.phase == 'chat' and not args.message_file:
        raise ValueError('chat requires --message-file')
    message = args.message_file.read_text('utf-8-sig') if args.message_file else ''
    if args.phase == 'chat' and not message.strip():
        raise ValueError('Chat message must not be empty')
    output.mkdir(parents=True, exist_ok=True)
    folder = output / 'isolated-store'
    assert_idle(folder)
    # Set before importing main; its compatibility app must also be isolated.
    os.environ['RA_DATA_DIR'] = str(folder)
    import app.core as core
    core.DATA = folder
    from app.main import create_app
    from app.provider import Provider, origin
    from app.platform_knowledge import RELATIVE_PATH, load as load_knowledge
    import httpx
    from playwright.async_api import async_playwright, expect

    provider = Provider(ROOT / '.env', ROOT / 'data/.credentials')
    settings, overrides = load_config()
    if origin(settings) != 'https://api.deepseek.com':
        raise RuntimeError('This trial is restricted to the configured official DeepSeek endpoint')
    if args.phase != 'inspect' and not provider.key_status(settings)['key_configured']:
        raise RuntimeError('No configured real provider key; no request sent')
    app = create_app(folder, provider=provider)
    store = app.state.store
    store.setting('model', settings)
    source = ROOT / 'data' / RELATIVE_PATH
    destination = folder / RELATIVE_PATH
    raw = source.read_bytes()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and destination.read_bytes() != raw:
        raise RuntimeError('Installed knowledge changed between phases; retain prior evidence and review explicitly')
    if not destination.exists():
        destination.write_bytes(raw)
    _, knowledge = load_knowledge(folder)
    if knowledge['state'] != 'available':
        raise RuntimeError('Installed shared knowledge is not valid')

    state_path = output / 'validation-state.json'
    state = json.loads(state_path.read_text('utf-8')) if state_path.exists() else {}
    phases = output / 'phases'
    phases.mkdir(exist_ok=True)
    number = max([int(p.name.split('-')[0]) for p in phases.iterdir() if p.is_dir()] or [0]) + 1
    phase_dir = phases / f'{number:03d}-{args.phase}'
    phase_dir.mkdir()
    (phase_dir / 'input.txt').write_text(message, encoding='utf-8')
    report = dict(schema='chat-to-prd-real-validation-1', phase=args.phase, status='running',
        source_type='synthetic_test_conversation', user_business_acceptance='NOT_RUN',
        daily_data_mutation=False, daily_settings_access='read-only sqlite',
        listener_started=False, business_seed=False, model_cost='unknown',
        settings=safe_config(settings), overrides=overrides,
        shared_knowledge={**knowledge, 'source_file': str(source), 'copied_sha256': sha(raw)},
        static_build=str(ROOT / 'web/dist'), backend_runtime=app.state.runtime['runtime_id'],
        api_events=[], page_errors=[], routing_errors=[], served_static={}, tasks=[],
        automatic_new_task_retry=False)
    report['evidence_before'] = call_evidence(folder)
    write(phase_dir / 'report.json', report)
    pid = state.get('project_id')
    page = None
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN, timeout=60) as client:
        # Creation and empty mode setup use the production API, never Store.edit.
        if not pid:
            session = (await client.get('/api/session')).json()
            headers = {'Origin': ORIGIN, 'X-CSRF-Token': session['csrf']}
            created = await client.post('/api/projects', json={'name': args.name}, headers=headers)
            created.raise_for_status()
            project = created.json()
            pid = project['id']
            changed = await client.patch('/api/projects/' + pid,
                json={'expected_revision': project['revision'], 'reference_mode': 'builtin'}, headers=headers)
            changed.raise_for_status()
            changed = await client.post('/api/projects/' + pid + '/workspace-draft',
                json={'expected_revision': changed.json()['revision'], 'text': '', 'entry_mode': 'chat'}, headers=headers)
            changed.raise_for_status()
            project = store.get(pid)
            assert not project['items'] and not project['sources'] and not project['messages']
            assert not project.get('product_context')
            state = {'project_id': pid, 'name': project['name'], 'business_seed': False}
            write(state_path, state)
            write(output / 'empty-project.json', project)
            report['creation_api'] = ['/api/projects', '/api/projects/{id} PATCH reference_mode=builtin',
                                      '/api/projects/{id}/workspace-draft POST empty chat']
        report['project_id'] = pid
        write(phase_dir / 'project-before.json', store.get(pid))
        old_ids = {task['id'] for task in store.records(pid, 'user_task')}
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            context = await browser.new_context(viewport={'width': 1600, 'height': 1100}, accept_downloads=True)
            action_submissions = set()

            async def dispatch(route):
                try:
                    request, url = route.request, urlsplit(route.request.url)
                    if url.netloc != '127.0.0.1:8765':
                        raise RuntimeError('Unexpected external browser request')
                    if url.path.startswith('/api/'):
                        headers = {key: value for key, value in request.headers.items()
                                   if key.lower() not in ('host', 'content-length', 'connection', 'accept-encoding')}
                        headers['host'] = '127.0.0.1:8765'
                        body = request.post_data_json if request.post_data else None
                        if request.method == 'POST' and url.path.endswith('/actions'):
                            if args.phase == 'inspect':
                                raise RuntimeError('Inspect phase cannot start model tasks')
                            key = body.get('idempotency_key')
                            if action_submissions and key not in action_submissions:
                                raise RuntimeError('Only one explicit user task is permitted per phase')
                            action_submissions.add(key)
                        response = await client.request(request.method, url.path + ('?' + url.query if url.query else ''),
                                                        headers=headers, content=request.post_data_buffer)
                        event = {'method': request.method, 'path': url.path, 'status': response.status_code}
                        if request.method != 'GET':
                            event['body'] = body
                            if 'application/json' in response.headers.get('content-type', ''):
                                event['response'] = response.json()
                        report['api_events'].append(event)
                        await route.fulfill(status=response.status_code, body=response.content,
                                            content_type=response.headers.get('content-type', 'application/json'))
                    else:
                        build = ROOT / 'web/dist'
                        path = (build / (url.path.lstrip('/') or 'index.html')).resolve()
                        if not path.is_relative_to(build.resolve()) or not path.is_file():
                            raise RuntimeError('Missing/unexpected static path: ' + url.path)
                        content = path.read_bytes()
                        report['served_static'][url.path] = sha(content)
                        await route.fulfill(status=200, body=content,
                                            content_type=mimetypes.guess_type(path.name)[0] or 'application/octet-stream')
                except BaseException as error:
                    report['routing_errors'].append(str(error))
                    await route.abort()

            await context.route('**/*', dispatch)
            page = await context.new_page()
            page.set_default_timeout(20000)
            page.on('pageerror', lambda error: report['page_errors'].append(str(error)))
            try:
                await page.goto(ORIGIN, wait_until='networkidle')
                await page.locator('aside.sidebar button[title="' + state['name'] + '"]').click()
                chat = page.get_by_role('article', name='需求聊天', exact=True)
                await expect(chat.get_by_role('textbox', name='你的想法', exact=True)).to_be_visible()
                await page.screenshot(path=str(phase_dir / '01-assistant-entry.png'))
                report['assistant_entry_verified'] = True
                if args.phase == 'chat':
                    await chat.get_by_role('textbox', name='你的想法', exact=True).fill(message)
                    await page.screenshot(path=str(phase_dir / '02-message-before-send.png'))
                    await chat.get_by_role('button', name='发送', exact=True).click()
                elif args.phase == 'organize':
                    await chat.get_by_role('button', name='整理并进入完善需求', exact=True).click()
                elif args.phase == 'document':
                    from scripts.run_detail_prd_agent_validation import run_action, export_review
                    await run_action(store, app.state.actions, pid, 'document', message,
                                     None, phase_dir)
                    await export_review(store, pid, phase_dir)
                if args.phase in ('chat', 'organize'):
                    # Text-chat normally authorizes its displayed receiver in Send;
                    # preparation can show the production explicit task dialog.
                    for _ in range(200):
                        fresh = [t for t in store.records(pid, 'user_task') if t['id'] not in old_ids]
                        if fresh:
                            break
                        dialog = page.get_by_role('dialog', name='核对本次任务', exact=True)
                        if await dialog.is_visible():
                            await page.screenshot(path=str(phase_dir / '03-task-authorization.png'))
                            button = dialog.get_by_role('button', name=re.compile('^(授权本次范围并运行|运行本次任务)$'))
                            if not await button.is_enabled():
                                raise RuntimeError('Task plan is blocked: ' + await dialog.inner_text())
                            await button.click()
                        if report['routing_errors']:
                            raise RuntimeError('Browser routing failed; no new paid task retry')
                        await asyncio.sleep(0.1)
                    else:
                        raise RuntimeError('No task was created from the UI: ' + await page.locator('body').inner_text())
                    if len(fresh) != 1:
                        raise RuntimeError('Unexpected number of phase tasks')
                    task = fresh[0]
                    print(json.dumps({'event': 'task_started', 'phase': args.phase, 'task_id': task['id']}, ensure_ascii=False), flush=True)
                    pending = app.state.actions.jobs.get(task['id'])
                    if pending:
                        while not pending.done():
                            await asyncio.wait({pending}, timeout=15)
                            current = store.get_record(pid, task['id'], 'user_task')
                            print(json.dumps({key: current.get(key) for key in ('id', 'status', 'calls', 'message')}, ensure_ascii=False), flush=True)
                        await pending
                    final_task = store.get_record(pid, task['id'], 'user_task')
                    report['task_outcome'] = {key: final_task.get(key) for key in ('id', 'status', 'calls', 'error', 'message')}
                    if args.phase == 'chat':
                        await page.locator('.connected-workspace:visible').get_by_role('button', name='刷新', exact=True).click()
                        await expect(chat.get_by_role('textbox', name='你的想法', exact=True)).to_be_enabled()
                        await chat.get_by_role('log', name='聊天记录', exact=True).evaluate('(node)=>{node.scrollTop=node.scrollHeight}')
                    else:
                        await page.wait_for_timeout(1000)
                    if final_task['status'] not in ('succeeded', 'partial', 'awaiting_user'):
                        raise RuntimeError('Actual Agent task did not succeed; preserved without retry: ' + final_task.get('message', ''))
                await page.screenshot(path=str(phase_dir / '04-phase-result.png'), full_page=True)
                if args.phase == 'organize':
                    report['refinement_table_visible'] = await page.get_by_role('table', name='需求确认表', exact=True).is_visible()
                if report['routing_errors'] or report['page_errors']:
                    raise RuntimeError('Browser reported errors; see evidence')
                report['status'] = 'passed'
            except BaseException:
                report['status'] = 'failed'
                report['error'] = traceback.format_exc()
                await page.screenshot(path=str(phase_dir / 'failure.png'), full_page=True)
                raise
            finally:
                # An observation failure must not cancel an already charged request.
                if app.state.actions.jobs:
                    await asyncio.gather(*list(app.state.actions.jobs.values()), return_exceptions=True)
                (phase_dir / 'visible-page.txt').write_text(await page.locator('body').inner_text(), encoding='utf-8')
                project = store.get(pid)
                write(phase_dir / 'project-after.json', project)
                write(phase_dir / 'conversation.json', project.get('interview', project.get('messages', [])))
                report['tasks'] = [t for t in store.records(pid, 'user_task') if t['id'] not in old_ids]
                report['evidence_after'] = call_evidence(folder)
                report['phase_model_calls'] = len(report['evidence_after']) - len(report['evidence_before'])
                report['total_model_calls'] = len(report['evidence_after'])
                report['row_confirmations'] = sum(bool(i.get('requirement_confirmation')) for i in project['items'])
                write(phase_dir / 'report.json', report)
                write(output / 'latest-report.json', report)
                await context.close()
                await browser.close()
    print(json.dumps({'phase': args.phase, 'status': report['status'], 'project_id': pid,
                      'phase_model_calls': report['phase_model_calls'], 'evidence': str(phase_dir)}, ensure_ascii=False), flush=True)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase', choices=('inspect', 'chat', 'organize', 'document'), required=True)
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--message-file', type=Path)
    parser.add_argument('--name', default=TITLE)
    parser.add_argument('--allow-real-provider', action='store_true')
    asyncio.run(run(parser.parse_args()))
