"""Real chapter UI over frozen v24 and isolated ASGI, with zero model requests.

All 8765 requests are intercepted; no HTTP listener or daily database is used.
The provided historical backup is opened mode=ro for only this project's rev23
and its chapter run. Task progress is explicitly simulated, never model output.
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
QID = 'Q-9b532e37ecef43ec'
RULE = 'RULE-f8353ea2bdea4a72'
SECTION = 'PRD-2aa7d32ae820'
OTHER_SECTION = 'PRD-3e895b840001'
ANSWER = '离线交互测试：本例将运维人员仅作为文档角色示例，沿用已经确认的模板可见范围原文，不新增角色权限。此回答不是实际业务确认。'


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def freeze(args):
    directory, history = args.fixture_dir.resolve(), args.history_db.resolve()
    assert directory.is_relative_to(ROOT / 'evidence') and history.is_relative_to(ROOT / 'evidence')
    project_file, records_file = directory / 'frozen-project-v24.json', directory / 'frozen-task-run-records.json'
    project = json.loads(project_file.read_text(encoding='utf-8'))
    records = json.loads(records_file.read_text(encoding='utf-8'))
    assert project['id'] == PID and project['revision'] == 24
    chapter = [record for record in records if record['kind'] == 'user_task' and
               ((record['payload'].get('retry_input') or {}).get('target') or {}).get('section_id') == SECTION]
    assert len(chapter) == 1 and len(chapter[0]['payload']['run_ids']) == 1
    run_id = chapter[0]['payload']['run_ids'][0]
    with sqlite3.connect(history.as_uri() + '?mode=ro', uri=True) as db:
        snapshot = db.execute('SELECT payload FROM revisions WHERE project_id=? AND revision=23', (PID,)).fetchone()
        run = db.execute("SELECT payload FROM records WHERE project_id=? AND id=? AND kind='run'", (PID, run_id)).fetchone()
    assert snapshot and run, 'Exact historical chapter input/run is required; never infer ownership'
    revision23, run_payload = json.loads(snapshot[0]), json.loads(run[0])
    assert revision23['id'] == PID and revision23['revision'] == 23
    assert run_payload['revision'] == 23 and run_payload['result_applied'] is True
    assert QID not in {question['id'] for question in revision23['questions']}
    assert QID in run_payload['decision_review']['proposal_question_ids']
    for record in records:
        if record['id'] == run_id:
            record['payload'] = run_payload
    return project, records, revision23, dict(
        project_file=str(project_file), project_sha256=hashlib.sha256(project_file.read_bytes()).hexdigest(),
        records_file=str(records_file), records_sha256=hashlib.sha256(records_file.read_bytes()).hexdigest(),
        history_db=str(history), history_access='mode=ro; this PID rev23 and one chapter run only',
        historical_run_id=run_id, historical_revision=23, historical_payload_sha256=digest(revision23))


def seed(store, project, records, revision23):
    from app.core import dumps, now
    with store.connect() as db:
        db.execute('INSERT INTO projects VALUES(?,?,?)', (PID, project['revision'], dumps(project)))
        for snapshot in (revision23, project):
            db.execute('INSERT INTO revisions VALUES(?,?,?,?,?)', (PID, snapshot['revision'], dumps(snapshot),
                '既有冻结副本；仅离线交互验证', now()))
        for record in records:
            db.execute('INSERT INTO records VALUES(?,?,?,?,?)',
                (record['id'], PID, record['kind'], dumps(record['payload']), record['created']))


def state_hash(store):
    """Logical equality includes records/history, not only the displayed revision."""
    with store.connect() as db:
        data = dict(project=[list(row) for row in db.execute('SELECT * FROM projects WHERE id=?', (PID,))],
                    revisions=[list(row) for row in db.execute('SELECT * FROM revisions WHERE project_id=? ORDER BY revision', (PID,))],
                    records=[list(row) for row in db.execute('SELECT * FROM records WHERE project_id=? ORDER BY id', (PID,))])
    return digest(data)


async def wait_state(store, predicate, message):
    for _ in range(100):
        value = store.get(PID)
        if predicate(value):
            return value
        await asyncio.sleep(.1)
    raise AssertionError(message)


async def exercise(page, store, initial, output, report, control, expect):
    from app.core import now
    from app.product_flow import status
    from app.requirement_confirmation import confirmation_current
    doc = initial['documents']['prd']
    rule = next(item for item in initial['items'] if item['id'] == RULE)
    requirements = {item['id']: item for item in initial['items'] if item['id'] in initial['product_context']['scope_ids']}
    assert len(requirements) == 10 and all(confirmation_current(initial, item) for item in requirements.values())
    assert len(initial['messages']) == 20
    baseline = state_hash(store)

    async def document_page():
        await page.goto(ORIGIN, wait_until='networkidle')
        await page.locator('aside.sidebar button[title="电价管理"]').click()
        workspace = page.locator('.project-workspace:not([hidden])')
        connected = workspace.locator('.connected-workspace')
        await connected.get_by_role('navigation', name='需求工作流程').get_by_role(
            'button', name=re.compile(r'^3\s*确认交付$')).click()
        await connected.get_by_role('button', name='查看与核对正式文档', exact=True).click()
        await expect(workspace.locator('details.connected-details')).to_have_attribute('open', '')

    async def open_section(section=SECTION):
        heading = page.locator('[id="' + doc['id'] + '-' + section + '"]')
        await heading.locator('..').get_by_role('button', name='讨论此章节', exact=True).click()
        drawer = page.get_by_role('dialog', name='章节讨论', exact=True)
        await expect(drawer).to_be_visible()
        return drawer

    async def close_drawer(drawer):
        await drawer.get_by_role('button', name='返回原章节', exact=True).first.click()
        await expect(drawer).not_to_be_visible()

    await document_page()
    summary = page.locator('.document-discussion-summary:visible')
    await expect(summary.get_by_role('button')).to_have_count(1)
    assert await summary.locator('textarea').count() == 0, 'document summary exposed a separate answer form'
    drawer = await open_section()
    await expect(drawer.locator('.chapter-history summary')).to_contain_text('本章已发送的讨论 · 1')
    await expect(drawer.locator('.chapter-history article')).to_have_count(1)
    assert '已保存的讨论历史 · 20' not in await drawer.inner_text()
    answer = drawer.get_by_role('textbox', name='继续补充这次讨论', exact=True)
    await expect(answer).to_be_visible()
    assert await drawer.locator('textarea:visible').count() == 1
    assert await drawer.get_by_label('我的回答原文', exact=True).count() == 0
    assert await drawer.locator('.answer-clause-editor textarea').count() == 0
    await drawer.locator('.chapter-original > summary').click()
    original_section = next(section for section in doc['content']['sections'] if section['section_id'] == SECTION)
    original_text = '\n\n'.join(block.get('text') or '\n'.join(next((item.get('statement', '')
        for item in doc.get('item_snapshot', []) if item['id'] == iid), '')
        for iid in block.get('ref_ids', [])) for block in original_section['blocks'])
    assert original_text
    await expect(drawer.locator('.chapter-original')).to_contain_text(original_text)
    assert state_hash(store) == baseline
    report['checks'].append('chapter_button_opens_original_context_one_chapter_history_and_one_compact_continuation')
    await page.screenshot(path=str(output / '01-compact-chapter.png'))

    draft_a, draft_b = '离线未发送草稿：继续核对角色说明。', '另一章节草稿：仅核对背景表达。'
    await answer.fill(draft_a)
    await close_drawer(drawer)
    drawer = await open_section()
    await expect(drawer.get_by_role('textbox', name='继续补充这次讨论', exact=True)).to_have_value(draft_a)
    await close_drawer(drawer)
    other = await open_section(OTHER_SECTION)
    composer = other.get_by_role('textbox', name='继续讨论这章', exact=True)
    await expect(composer).to_have_value('')
    await composer.fill(draft_b)
    await close_drawer(other)
    drawer = await open_section()
    await expect(drawer.get_by_role('textbox', name='继续补充这次讨论', exact=True)).to_have_value(draft_a)
    await close_drawer(drawer)
    await document_page()
    drawer = await open_section()
    await expect(drawer.get_by_role('textbox', name='继续补充这次讨论', exact=True)).to_have_value(draft_a)
    await close_drawer(drawer)
    other = await open_section(OTHER_SECTION)
    await expect(other.get_by_role('textbox', name='继续讨论这章', exact=True)).to_have_value(draft_b)
    assert state_hash(store) == baseline, 'typing/closing/reopening/reloading/changing chapter wrote any isolated store row'
    report['checks'].append('draft_survives_close_reopen_refresh_and_is_isolated_by_chapter_with_all_store_rows_unchanged')

    await other.get_by_role('button', name='发送本章讨论', exact=True).click()
    plan = page.get_by_role('dialog', name='核对本次任务', exact=True)
    await expect(plan).to_be_visible()
    await expect(plan).to_contain_text(draft_b)
    await expect(plan).to_contain_text(OTHER_SECTION)
    await plan.get_by_role('button', name='返回保留输入', exact=True).click()
    await expect(other).to_be_visible()
    await expect(other.get_by_role('textbox', name='继续讨论这章', exact=True)).to_have_value(draft_b)
    assert state_hash(store) == baseline and report['simulated_task_requests'] == 0
    report['checks'].append('send_discussion_opens_real_plan_only_and_return_keeps_readable_draft_in_drawer')
    await close_drawer(other)
    drawer = await open_section()
    answer = drawer.get_by_role('textbox', name='继续补充这次讨论', exact=True)

    await page.evaluate("""() => { window.__chapterSetItem=Storage.prototype.setItem;
      Storage.prototype.setItem=function(key,value){if(key.startsWith('ra-discussion-draft-v1:'))
        throw new DOMException('Offline injected quota failure','QuotaExceededError');
        return window.__chapterSetItem.call(this,key,value);}; }""")
    await answer.fill(draft_a + '（存储失败注入）')
    await expect(drawer).to_contain_text('浏览器未能保留讨论草稿')
    await drawer.get_by_role('button', name='返回原章节', exact=True).first.click()
    guard = page.get_by_role('dialog', name='未保存的修改', exact=True)
    await expect(guard).to_be_visible()
    await guard.get_by_role('button', name='留在当前页', exact=True).click()
    await expect(answer).to_have_value(draft_a + '（存储失败注入）')
    await page.evaluate('() => { Storage.prototype.setItem=window.__chapterSetItem;delete window.__chapterSetItem; }')
    await answer.fill(ANSWER)
    assert state_hash(store) == baseline
    report['checks'].append('quota_failure_keeps_typed_words_and_blocks_leaving_until_storage_is_restored')

    control['reject_answer_save'] = True
    await drawer.get_by_role('button', name='核对原文的落实位置', exact=True).click()
    implementation=drawer.locator('.answer-implementation-target')
    await implementation.get_by_role('combobox').select_option('statement')
    await implementation.get_by_role('checkbox', name='我已核对此原文表达了答案', exact=True).check()
    await drawer.get_by_role('button', name='预览本次落实核对', exact=True).click()
    await expect(drawer).to_contain_text('离线注入：回答保存失败')
    await expect(answer).to_have_value(ANSWER)
    assert state_hash(store) == baseline
    assert await drawer.locator('.answer-clause-editor textarea').count() == 0
    report['checks'].append('injected_answer_save_failure_keeps_words_and_never_opens_another_clause_form_or_writes_store')
    await page.screenshot(path=str(output / '02-failed-save-preserves-continuation.png'))

    await drawer.get_by_role('button', name='预览本次落实核对', exact=True).click()
    preview = drawer.locator('.answer-change-preview')
    await expect(preview).to_be_visible()
    await expect(preview.locator('.answer-difference')).to_have_count(1)
    await expect(preview.locator('.answer-before-after > div').nth(0)).to_contain_text(rule['statement'])
    await expect(preview.locator('.answer-before-after > div').nth(1)).to_contain_text(rule['statement'])
    assert store.get(PID)['items'] == initial['items'] and store.get(PID)['documents'] == initial['documents']
    assert not status(store.get(PID))[2]['complete']
    report['checks'].append('real_manual_preview_shows_exact_original_rule_before_after_without_editing_or_auto_confirming')

    for width, height in ((1440, 900), (900, 900), (390, 844)):
        await page.set_viewport_size(dict(width=width, height=height))
        await expect(preview).to_be_visible()
        metrics = await page.evaluate("""() => {const drawer=document.querySelector('.chapter-discussion-drawer');
          return {viewport:innerWidth,page:document.documentElement.scrollWidth,
            drawer:drawer.clientWidth,drawerContent:drawer.scrollWidth};}""")
        report['viewports'].append(metrics)
        assert metrics['page'] <= metrics['viewport'] + 1 and metrics['drawerContent'] <= metrics['drawer'] + 1, metrics
        await page.screenshot(path=str(output / f'03-real-preview-{width}.png'))
    await page.set_viewport_size(dict(width=1440, height=900))
    report['checks'].append('1440_900_390_viewports_have_no_page_or_drawer_horizontal_overflow')

    saved_hash = state_hash(store)
    control['commit_conflict'] = True
    await preview.get_by_role('button', name='确认本次落实核对', exact=True).click()
    await expect(drawer).to_contain_text('离线注入：版本冲突')
    await expect(answer).to_have_value(ANSWER)
    await expect(preview).to_be_visible()
    assert state_hash(store) == saved_hash
    report['checks'].append('injected_commit_revision_conflict_keeps_input_and_visible_preview_without_writes')
    await page.screenshot(path=str(output / '04-conflict-keeps-preview.png'))

    await preview.get_by_role('button', name='确认本次落实核对', exact=True).click()
    confirmed = await wait_state(store, lambda value: status(value)[2]['complete'], 'chapter commit did not close stage 3 in its own transaction')
    await expect(drawer).to_contain_text('已确认本次变更')
    assert await drawer.get_by_role('button', name='确认本次补充已处理', exact=True).count() == 0
    assert not any(event['path'].endswith('/stage-checks/3') for event in report['api_requests'])
    assert confirmed['documents'] == initial['documents']
    assert next(item for item in confirmed['items'] if item['id'] == RULE) == rule
    assert all(next(item for item in confirmed['items'] if item['id'] == iid) == original for iid, original in requirements.items())
    assert all(confirmation_current(confirmed, original) for original in requirements.values())
    commit = next(event for event in reversed(report['api_requests']) if event['path'].endswith('/decision-review/commit') and event['status'] == 200)
    assert commit['body']['document_discussion'] == dict(document_id=doc['id'], section_id=SECTION)
    assert commit['response']['receipt']['document_discussion']['checkpoint_saved'] is True
    report['confirmed'] = dict(revision=confirmed['revision'], stage_3_complete=True, same_transaction_checkpoint=True,
                             ten_original_requirements_unchanged=True, original_rule_unchanged=True, documents_unchanged=True)
    report['checks'].append('one_real_commit_confirms_chapter_and_stage3_atomically_without_second_form_or_checkpoint_and_keeps_documents_ten_requirements_unchanged')
    await page.screenshot(path=str(output / '05-one-confirmation-complete.png'))

    await expect(drawer.get_by_role('button', name='更新当前文档', exact=True)).to_be_enabled()
    before_plan = state_hash(store)
    await drawer.get_by_role('button', name='更新当前文档', exact=True).click()
    await expect(plan).to_be_visible()
    assert report['api_requests'][-1]['path'].endswith('/actions/plan') or any(
        event.get('body', {}).get('action') == 'document' and event['path'].endswith('/actions/plan') for event in report['api_requests'])
    await plan.get_by_role('button', name='返回保留输入', exact=True).click()
    assert state_hash(store) == before_plan and report['simulated_task_requests'] == 0
    report['checks'].append('update_current_document_opens_real_document_plan_and_cancel_calls_no_model_or_document_write')

    # This is a declared UI-only simulated task, after the real transaction was
    # independently accepted above. It is never counted as model validation.
    drawer = await open_section()
    composer = drawer.get_by_role('textbox', name='继续讨论这章', exact=True)
    message = '离线模拟进度：本章另一个待讨论意见，不执行模型。'
    await composer.fill(message)
    await drawer.get_by_role('button', name='发送本章讨论', exact=True).click()
    await expect(plan).to_be_visible()
    control['simulate_actions'] = True
    await plan.get_by_role('button', name=re.compile(r'^(授权本次范围并运行|运行本次任务)$')).click()
    await expect(plan).not_to_be_visible()
    await expect(drawer).to_be_visible()
    await expect(drawer.get_by_role('progressbar', name='分析步骤进度')).to_be_visible()
    await expect(composer).to_have_value('')
    queued = store.records(PID, 'user_task')[-1]
    assert queued['status'] == 'queued' and queued['calls'] == 0
    control['finish_simulated']()
    await expect(drawer).to_contain_text('离线模拟跟进：请继续核对本章补充。', timeout=15000)
    await expect(drawer.get_by_role('textbox', name='继续补充这次讨论', exact=True)).to_be_visible()
    assert await drawer.locator('.answer-clause-editor textarea').count() == 0
    report['checks'].append('simulated_queued_to_succeeded_progress_stays_in_same_drawer_and_exposes_followup_without_any_model_request')
    report['task_progress'] = dict(mode='EXPLICIT_OFFLINE_SIMULATION', model_calls=0,
                                  task_id=queued['id'], queued_then_succeeded=True, drawer_retained=True)
    await page.screenshot(path=str(output / '06-simulated-task-followup.png'))


async def run(args):
    output, build = args.output.resolve(), args.static_build.resolve()
    assert output.is_relative_to(ROOT / 'evidence') and not output.exists()
    assert (build / 'index.html').is_file()
    initial, records, revision23, fixture = freeze(args)
    output.mkdir(parents=True)
    report = dict(status='running', fixture=fixture, static_build=str(build), model_calls=0,
                  daily_data_access=False, listener_started=False, business_acceptance='NOT_RUN',
                  model_quality='NOT_RUN', checks=[], api_requests=[], page_errors=[], routing_errors=[],
                  served_static={}, simulated_task_requests=0, viewports=[])
    control = dict(reject_answer_save=False, commit_conflict=False, simulate_actions=False)
    with tempfile.TemporaryDirectory(prefix='ra-chapter-discussion-') as scratch:
        os.environ['RA_DATA_DIR'] = str(Path(scratch) / 'import-data')
        import httpx
        from playwright.async_api import async_playwright, expect
        from app.main import create_app
        from app.core import now
        from tests.check_refine_workspace_browser import NoModelProvider

        class OfflinePlanningProvider(NoModelProvider):
            def key(self, config):
                return 'OFFLINE_CHAPTER_FIXTURE_ONLY'

            def key_status(self, config):
                return dict(key_configured=True, key_env='OFFLINE_ONLY', key_source='fixture',
                            bound_origin='offline', persistent_key_supported=False, key_storage_error='')

        provider = OfflinePlanningProvider(Path(scratch))
        app = create_app(Path(scratch) / 'fixture-data', provider=provider)
        store = app.state.store
        seed(store, initial, records, revision23)

        def simulate_submission(body):
            assert control['simulate_actions'] and body['target']['kind'] == 'document'
            task_id, run_id = 'TASK-OFFLINE-CHAPTER-PROGRESS', 'RUN-OFFLINE-CHAPTER-PROGRESS'
            task = dict(id=task_id, action='change', label='离线模拟章节讨论进度', status='queued',
                        created=now(), calls=0, cost=None, max_calls=None, source_ids=[], stages=['change'],
                        completed_steps=0, step_states=[dict(stage='change', status='queued')], run_ids=[run_id],
                        input_revision=store.get(PID)['revision'], retry_input={key: body[key] for key in
                            ('action', 'message', 'document_type', 'target')}, message='离线模拟：讨论任务已排队。')
            run = dict(stage='change', status='queued', created=now(), revision=store.get(PID)['revision'],
                       calls=0, cost=None, max_calls=None, result_applied=False, discussion_target=body['target'],
                       user_task_id=task_id, events=[], attempts=[], response=None, message='离线模拟排队')
            store.record(PID, 'user_task', {key: value for key, value in task.items() if key != 'id'}, task_id)
            store.record(PID, 'run', run, run_id)

            def finish():
                with store.edit(PID, store.get(PID)['revision'], '仅浏览器回归：显式模拟章节跟进结果') as (project, _):
                    question = copy.deepcopy(next(value for value in initial['questions'] if value['id'] == QID))
                    question.update(id='Q-OFFLINE-CHAPTER-PROGRESS', question='离线模拟跟进：请继续核对本章补充。',
                                    topic_key='offline_chapter_progress', decision_review_proposal=None,
                                    generation_evidence=dict(run_id=run_id), answer=None, status='open')
                    project['questions'].append(question)
                store.update_record(PID, run_id, 'run', status='succeeded', result_applied=True,
                                    completed=now(), discussion_result=dict(question_ids=['Q-OFFLINE-CHAPTER-PROGRESS'], item_ids=[]))
                store.update_record(PID, task_id, 'user_task', status='succeeded', completed_steps=1,
                                    completed=now(), message='离线模拟已生成跟进问题，模型调用为零。',
                                    step_states=[dict(stage='change', status='succeeded')])

            control['finish_simulated'] = finish
            return task

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=True)
                context = await browser.new_context(viewport=dict(width=1440, height=900))

                async def dispatch(route):
                    request, url = route.request, urlsplit(route.request.url)
                    try:
                        assert url.netloc == '127.0.0.1:8765', 'external network forbidden'
                        if url.path.startswith('/api/'):
                            assert request.method == 'GET' or request.method == 'POST' and url.path.endswith((
                                '/decision-review/answer', '/decision-review/manual-preview', '/decision-review/commit',
                                '/actions/plan', '/actions')), 'unexpected mutation: ' + url.path
                            event = dict(method=request.method, path=url.path)
                            if request.method != 'GET':
                                event['body'] = request.post_data_json
                            report['api_requests'].append(event)
                            if control['reject_answer_save'] and url.path.endswith('/decision-review/answer'):
                                control['reject_answer_save'] = False
                                event.update(status=409, injected=True)
                                await route.fulfill(status=409, content_type='application/json', body=json.dumps(dict(
                                    code='OFFLINE_INJECTED_FAILURE', message='离线注入：回答保存失败，输入应保留。'), ensure_ascii=False))
                                return
                            if control['commit_conflict'] and url.path.endswith('/decision-review/commit'):
                                control['commit_conflict'] = False
                                event.update(status=409, injected=True)
                                await route.fulfill(status=409, content_type='application/json', body=json.dumps(dict(
                                    code='STALE_REVISION', message='离线注入：版本冲突，输入和预览应保留。'), ensure_ascii=False))
                                return
                            if request.method == 'POST' and url.path.endswith('/actions'):
                                report['simulated_task_requests'] += 1
                                task = simulate_submission(request.post_data_json)
                                event.update(status=200, simulated=True)
                                await route.fulfill(status=200, content_type='application/json', body=json.dumps(task, ensure_ascii=False))
                                return
                            headers = {key: value for key, value in request.headers.items() if key.lower()
                                       not in ('host', 'content-length', 'connection', 'accept-encoding')}
                            headers['host'] = '127.0.0.1:8765'
                            response = await client.request(request.method, url.path + ('?' + url.query if url.query else ''),
                                                            headers=headers, content=request.post_data_buffer)
                            event['status'] = response.status_code
                            if request.method != 'GET':
                                result = response.json()
                                event['response'] = {key: result[key] for key in ('id', 'revision', 'code', 'message',
                                    'preview_id', 'preview_hash', 'document_discussion', 'receipt', 'pending_codes', 'checkpoint_saved',
                                    'missing', 'target_label') if key in result}
                            await route.fulfill(status=response.status_code, body=response.content,
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
                    await exercise(page, store, initial, output, report, control, expect)
                    assert provider.calls == 0 and not report['page_errors'] and not report['routing_errors']
                    report['status'] = 'passed'
                except Exception as exc:
                    report.update(status='failed', failure=str(exc), traceback=traceback.format_exc())
                    await page.screenshot(path=str(output / 'failure.png'), full_page=True)
                finally:
                    report['model_calls'] = provider.calls
                    report['final_store_sha256'] = state_hash(store)
                    write(output / 'result.json', report)
                    await context.close()
                    await browser.close()
    print(json.dumps({key: (str(report.get(key, '')).split('\n')[0] if key == 'failure' else report.get(key))
        for key in ('status', 'checks', 'failure', 'model_calls')}, ensure_ascii=False))
    if report['status'] != 'passed':
        raise SystemExit(1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('static-build', 'output', 'fixture-dir', 'history-db'):
        parser.add_argument('--' + name, type=Path, required=True)
    asyncio.run(run(parser.parse_args()))
