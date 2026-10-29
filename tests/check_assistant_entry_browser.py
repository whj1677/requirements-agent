"""Assistant-first project entries over frozen data and isolated ASGI.

No listener or daily data is used. Task status is explicitly overridden only
in GET responses to exercise the global progress bridge without running AI.
The sole allowed browser mutation is creation of one isolated test project.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
import traceback
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
ORIGIN = 'http://127.0.0.1:8765'
PID = 'P-d4b61a83e6f14612'
SECOND_NAME = '合成第二项目：助手入口验证'
NEW_NAME = '合成新建项目：助手入口验证'
DRAFT = '合成已保存工作底稿：入口模式为 direct，项目再次进入仍先显示助手。'


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def logical_hash(store, pid):
    with store.connect() as db:
        rows = dict(projects=[list(row) for row in db.execute('SELECT * FROM projects WHERE id=?', (pid,))],
            revisions=[list(row) for row in db.execute('SELECT * FROM revisions WHERE project_id=? ORDER BY revision', (pid,))],
            records=[list(row) for row in db.execute('SELECT * FROM records WHERE project_id=? ORDER BY id', (pid,))])
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def seed(store, args):
    from app.core import dumps, now
    directory = args.fixture_dir.resolve()
    assert directory.is_relative_to(ROOT / 'evidence')
    project_file = directory / 'frozen-project-v24.json'
    records_file = directory / 'frozen-task-run-records.json'
    project = json.loads(project_file.read_text(encoding='utf-8'))
    records = json.loads(records_file.read_text(encoding='utf-8'))
    assert project['id'] == PID and project['revision'] == 24
    snapshots = []
    if args.history_db:
        history = args.history_db.resolve()
        assert history.is_relative_to(ROOT / 'evidence')
        with sqlite3.connect(history.as_uri() + '?mode=ro', uri=True) as db:
            row = db.execute('SELECT payload FROM revisions WHERE project_id=? AND revision=23', (PID,)).fetchone()
            assert row, 'Exact frozen input revision 23 missing'
            snapshots.append(json.loads(row[0]))
    # Only this explicitly synthetic copy gets the saved entry-mode fixture.
    project['workspace_draft'] = dict(text=DRAFT, entry_mode='direct', saved_at='synthetic-entry-fixture', source_id=None)
    with store.connect() as db:
        db.execute('INSERT INTO projects VALUES(?,?,?)', (PID, project['revision'], dumps(project)))
        for snapshot in [*snapshots, project]:
            db.execute('INSERT INTO revisions VALUES(?,?,?,?,?)', (PID, snapshot['revision'], dumps(snapshot),
                '合成助手入口副本；原冻结材料未修改', now()))
        for record in records:
            db.execute('INSERT INTO records VALUES(?,?,?,?,?)',
                (record['id'], PID, record['kind'], dumps(record['payload']), record['created']))
    second = store.create(SECOND_NAME)
    selected = [dict(record['payload'], id=record['id']) for record in records if record['kind'] == 'user_task'
                and ((record['payload'].get('retry_input') or {}).get('target') or {}).get('kind') == 'document']
    assert selected, 'A real frozen chapter task is required'
    task = selected[-1]
    target = task['retry_input']['target']
    assert any(doc['id'] == target['id'] for doc in project['documents'].values())
    return project, second, task, dict(project_file=str(project_file), records_file=str(records_file),
        project_sha256=hashlib.sha256(project_file.read_bytes()).hexdigest(),
        records_sha256=hashlib.sha256(records_file.read_bytes()).hexdigest(),
        synthetic_saved_draft=True, historical_revision_loaded=bool(snapshots))


async def exercise(page, context, store, initial, second, task, output, report, control, expect):
    old_hash = logical_hash(store, PID)
    second_hash = logical_hash(store, second['id'])
    report['old_project_tables_sha256_before'] = old_hash
    report['second_project_tables_sha256_before'] = second_hash
    name = initial['name']
    workspace = page.locator('.project-workspace:not([hidden])')

    async def home(expected_name):
        await expect(page.get_by_role('heading', level=1, name=expected_name, exact=True)).to_be_visible()
        connected = workspace.locator('.connected-workspace')
        await expect(connected).to_have_attribute('data-step', 'information')
        await expect(connected.get_by_role('button', name='和助手聊需求', exact=True)).to_have_attribute('aria-pressed', 'true')
        await expect(connected.get_by_role('textbox', name='你的想法', exact=True)).to_be_visible()
        await expect(workspace.locator('details.connected-details')).not_to_have_attribute('open', '')
        await expect(page.get_by_role('dialog', name='未保存的修改', exact=True)).not_to_be_visible()
        return connected

    async def select(project_name):
        await page.locator('aside.sidebar button[title="' + project_name + '"]').click()
        return await home(project_name)

    def unchanged():
        assert logical_hash(store, PID) == old_hash, 'Old project tables changed'
        assert logical_hash(store, second['id']) == second_hash, 'Second project tables changed'

    await context.add_init_script('''
      if (location.origin === %s && !sessionStorage.getItem('ra-assistant-entry-test-seeded')) {
        sessionStorage.setItem('ra-assistant-entry-test-seeded', '1');
        localStorage.setItem('ra-last-project-id', %s);
        sessionStorage.setItem('ra-connected-step-'+%s, 'delivery');
        sessionStorage.setItem('ra-phase-'+%s, '5');
        sessionStorage.setItem('ra-refinement-entry-'+%s, 'manual');
      }
    ''' % tuple(json.dumps(value) for value in (ORIGIN, PID, PID, PID, PID)))
    await page.goto(ORIGIN, wait_until='networkidle')
    await home(name)
    unchanged()
    await page.screenshot(path=str(output / '01-restored-project-assistant.png'))
    report['checks'].append('last_project_with_old_delivery_phase_and_saved_direct_mode_restores_information_chat_without_writes')
    await page.evaluate('pid => {sessionStorage.setItem("ra-connected-step-"+pid,"refine");sessionStorage.setItem("ra-phase-"+pid,"4");}', PID)
    # The initialization fixture runs once; reload now uses the explicit
    # refine values instead of replacing them with the initial delivery state.
    await page.reload(wait_until='networkidle')
    await home(name)
    unchanged()
    report['checks'].append('reload_ignores_prior_workflow_position_and_starts_chat_without_preparation')

    connected = workspace.locator('.connected-workspace')
    delivery = connected.get_by_role('navigation', name='需求工作流程').get_by_role('button', name=re.compile(r'^3\s*确认交付$'))
    await delivery.click()
    await expect(connected).to_have_attribute('data-step', 'delivery')
    await connected.get_by_role('button', name='查看与核对正式文档', exact=True).click()
    await expect(workspace.locator('details.connected-details')).to_have_attribute('open', '')
    await select(SECOND_NAME)
    await select(name)
    unchanged()
    report['checks'].append('switch_from_existing_documents_to_second_project_and_back_returns_to_chat_without_old_table_changes')
    await delivery.click()
    await select(name)
    unchanged()
    report['checks'].append('clicking_current_sidebar_project_also_returns_to_assistant_home')

    direct = connected.get_by_role('button', name='直接填写需求', exact=True)
    chat = connected.get_by_role('button', name='和助手聊需求', exact=True)
    await direct.click()
    draft = connected.get_by_role('textbox', name='填写当前需求的原文底稿', exact=True)
    await expect(draft).to_have_value(DRAFT)
    temporary = DRAFT + '\n合成尚未保存的临时补充。'
    await draft.fill(temporary)
    await chat.click()
    await direct.click()
    await expect(draft).to_have_value(temporary)
    await draft.fill(DRAFT)
    await chat.click()
    await select(name)
    await select(SECOND_NAME)
    await select(name)
    unchanged()
    report['checks'].append('manual_direct_view_keeps_saved_and_unsaved_text_and_returning_to_chat_has_no_phantom_dirty_dialog')
    await page.screenshot(path=str(output / '02-direct-draft-and-chat-preserved.png'))

    await page.locator('aside.sidebar').get_by_role('button', name='＋ 新建需求项目', exact=True).click()
    dialog = page.get_by_role('dialog', name='新建需求项目', exact=True)
    await dialog.get_by_role('textbox', name='新项目名称', exact=True).fill(NEW_NAME)
    await dialog.get_by_role('button', name='创建项目', exact=True).click()
    await home(NEW_NAME)
    created = next(project for project in store.list() if project['name'] == NEW_NAME)
    assert created['revision'] == 0 and not created['sources'] and not created['items']
    unchanged()
    report['created_project_id'] = created['id']
    report['checks'].append('real_isolated_new_project_api_enters_chat_without_saving_draft_or_starting_preparation')

    # Historical terminal tasks do not populate global progress on load.
    # Override only the exact task's GET presentation: running -> succeeded.
    control['progress'] = 'running'
    await select(name)
    progress = page.locator('aside.ai-progress')
    await expect(progress).to_be_visible()
    await expect(progress.locator('.ai-progress-stage')).to_contain_text('分析修改建议')
    control['progress'] = 'succeeded'
    await select(SECOND_NAME)
    await expect(progress.locator('.ai-progress-compact')).to_contain_text('AI 结果待核对', timeout=15000)
    await progress.locator('.ai-progress-compact').click()
    await progress.get_by_role('button', name='查看结果并核对', exact=True).click()
    await home(name)
    continuation = workspace.get_by_role('button', name='继续查看所选任务', exact=True)
    await expect(continuation).to_be_visible()
    await expect(page.get_by_role('dialog', name='章节讨论', exact=True)).not_to_be_visible()
    unchanged()
    report['checks'].append('external_progress_task_opens_target_project_chat_first_and_retains_explicit_continuation')
    await page.screenshot(path=str(output / '03-cross-project-task-home.png'))
    await continuation.click()
    await expect(workspace.locator('details.connected-details')).to_have_attribute('open', '')
    chapter = page.get_by_role('dialog', name='章节讨论', exact=True)
    await expect(chapter).to_be_visible()
    target = task['retry_input']['target']
    document = next(doc for doc in initial['documents'].values() if doc['id'] == target['id'])
    section = next(section for section in document['content']['sections'] if section['section_id'] == target['section_id'])
    await expect(chapter.get_by_role('heading', level=2)).to_contain_text(section['title'])
    await expect(chapter.locator('.chapter-history summary')).to_contain_text('本章已发送的讨论')
    assert not await continuation.count()
    unchanged()
    report['task_intent'] = dict(task_id=task['id'], document_id=target['id'], section_id=target['section_id'],
                               progress_status='EXPLICIT_GET_ONLY_SIMULATION', exact_chapter_opened=True)
    report['checks'].append('explicit_continue_opens_exact_original_task_chapter_without_recovery_or_model_posts')
    await page.screenshot(path=str(output / '04-explicit-original-task-chapter.png'))
    report['old_project_tables_sha256_before'] = old_hash
    report['old_project_tables_sha256_after'] = logical_hash(store, PID)
    report['second_project_tables_sha256_before'] = second_hash
    report['second_project_tables_sha256_after'] = logical_hash(store, second['id'])


async def run(args):
    output, build = args.output.resolve(), args.static_build.resolve()
    assert output.is_relative_to(ROOT / 'evidence') and not output.exists()
    assert (build / 'index.html').is_file()
    output.mkdir(parents=True)
    report = dict(status='running', static_build=str(build), checks=[], api_requests=[], routing_errors=[],
                  page_errors=[], served_static={}, model_calls=0, daily_data_access=False, daily_writes=0,
                  listener_started=False, business_acceptance='NOT_RUN', model_quality='NOT_RUN')
    control = dict(progress='off')
    with tempfile.TemporaryDirectory(prefix='ra-assistant-entry-') as scratch:
        os.environ['RA_DATA_DIR'] = str(Path(scratch) / 'import-data')
        import httpx
        from playwright.async_api import async_playwright, expect
        from app.main import create_app
        from tests.check_refine_workspace_browser import NoModelProvider
        provider = NoModelProvider(Path(scratch))
        app = create_app(Path(scratch) / 'fixture-data', provider=provider)
        store = app.state.store
        initial, second, task, fixture = seed(store, args)
        report['fixture'] = fixture
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=True)
                context = await browser.new_context(viewport=dict(width=1440, height=900))

                async def dispatch(route):
                    request, url = route.request, urlsplit(route.request.url)
                    try:
                        assert url.netloc == '127.0.0.1:8765', 'External network forbidden'
                        if url.path.startswith('/api/'):
                            assert not url.path.endswith('/refinement-preparation'), 'Unexpected preparation request'
                            assert request.method == 'GET' or request.method == 'POST' and url.path == '/api/projects', \
                                'Unexpected mutation or model submission: ' + request.method + ' ' + url.path
                            headers = {key:value for key,value in request.headers.items()
                                       if key.lower() not in ('host', 'content-length', 'connection', 'accept-encoding')}
                            headers['host'] = '127.0.0.1:8765'
                            response = await client.request(request.method, url.path + ('?' + url.query if url.query else ''),
                                                            headers=headers, content=request.post_data_buffer)
                            event = dict(method=request.method, path=url.path, status=response.status_code)
                            if request.method != 'GET':
                                event['body'] = request.post_data_json
                            body = response.content
                            if request.method == 'GET' and url.path == '/api/projects/' + PID + '/actions' and control['progress'] != 'off':
                                values = response.json()
                                for index,value in enumerate(values):
                                    if value['id'] != task['id']:
                                        continue
                                    value = copy.deepcopy(value)
                                    value.update(status=control['progress'], calls=0,
                                        message='合成仅GET任务进度展示：未执行模型或保存项目。',
                                        step_states=[dict(stage=stage, status=control['progress']) for stage in value['stages']])
                                    if control['progress'] == 'running':
                                        value.pop('completed', None)
                                    values[index] = value
                                body = json.dumps(values, ensure_ascii=False).encode()
                                event['simulated_status_only'] = control['progress']
                            report['api_requests'].append(event)
                            await route.fulfill(status=response.status_code, body=body,
                                                content_type=response.headers.get('content-type', 'application/json'))
                        else:
                            path = (build / (url.path.lstrip('/') or 'index.html')).resolve()
                            assert path.is_relative_to(build) and path.is_file(), str(path)
                            report['served_static'][url.path] = hashlib.sha256(path.read_bytes()).hexdigest()
                            await route.fulfill(status=200, body=path.read_bytes(),
                                                content_type=mimetypes.guess_type(path.name)[0] or 'application/octet-stream')
                    except Exception as exc:
                        report['routing_errors'].append(str(exc))
                        await route.abort()

                await context.route('**/*', dispatch)
                page = await context.new_page()
                page.set_default_timeout(12000)
                page.on('pageerror', lambda error: report['page_errors'].append(str(error)))
                try:
                    await exercise(page, context, store, initial, second, task, output, report, control, expect)
                    mutations = [event for event in report['api_requests'] if event['method'] != 'GET']
                    assert len(mutations) == 1 and mutations[0]['path'] == '/api/projects' and mutations[0]['status'] == 200
                    assert provider.calls == 0 and not report['page_errors'] and not report['routing_errors']
                    report['status'] = 'passed'
                except Exception as exc:
                    report.update(status='failed', failure=str(exc), traceback=traceback.format_exc())
                    await page.screenshot(path=str(output / 'failure.png'), full_page=True)
                finally:
                    report['model_calls'] = provider.calls
                    report['old_project_tables_sha256_after'] = logical_hash(store, PID)
                    report['second_project_tables_sha256_after'] = logical_hash(store, second['id'])
                    write(output / 'result.json', report)
                    await context.close()
                    await browser.close()
    print(json.dumps({key: report.get(key) for key in ('status', 'checks', 'failure', 'model_calls')}, ensure_ascii=False))
    if report['status'] != 'passed':
        raise SystemExit(1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('static-build', 'output', 'fixture-dir'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--history-db', type=Path)
    asyncio.run(run(parser.parse_args()))
