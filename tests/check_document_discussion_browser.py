"""Offline chapter-discussion regression on a frozen tariff project copy.

Reads daily SQLite through mode=ro once, stores the input in ignored evidence,
then routes browser requests to an isolated ASGI app on the sole 8765 origin.
No listener, model, daily write, video, document rewrite or business acceptance.
The typed answer is explicitly a synthetic test of retaining the original rule.
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
ANSWER = '离线交互测试：本例将运维人员仅作为文档角色示例，沿用已经确认的模板可见范围原文，不新增角色权限。此回答不是实际业务确认。'


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def frozen_daily_input(output):
    database = (ROOT / 'data/requirements.sqlite3').resolve()
    with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as db:
        payload = db.execute('SELECT payload FROM projects WHERE id=?', (PID,)).fetchone()[0]
        project = json.loads(payload)
        assert project['revision'] == 24, 'This regression requires the reported tariff v24 snapshot'
        records = [dict(id=rid, kind=kind, payload=json.loads(value), created=created)
            for rid, kind, value, created in db.execute(
                "SELECT id,kind,payload,created FROM records WHERE project_id=? AND kind IN ('user_task','run') ORDER BY rowid", (PID,))]
    write(output / 'frozen-project-v24.json', project)
    write(output / 'frozen-task-run-records.json', records)
    return project, records, hashlib.sha256(payload.encode()).hexdigest()


def seed(store, project, records):
    from app.core import dumps, now
    with store.connect() as db:
        db.execute('INSERT INTO projects VALUES(?,?,?)', (PID, project['revision'], dumps(project)))
        db.execute('INSERT INTO revisions VALUES(?,?,?,?,?)', (PID, project['revision'], dumps(project),
            '电价管理v24离线交互冻结副本；后续回答仅为测试', now()))
        for record in records:
            db.execute('INSERT INTO records VALUES(?,?,?,?,?)',
                (record['id'], PID, record['kind'], dumps(record['payload']), record['created']))


async def wait_state(store, predicate, message):
    for _ in range(100):
        value = store.get(PID)
        if predicate(value):
            return value
        await asyncio.sleep(.1)
    raise AssertionError(message)


async def assert_in_document(details, connected, expect):
    await expect(details).to_have_attribute('open', '')
    await expect(details.locator('.workflow-page')).to_have_attribute('data-phase', '4')
    await expect(connected.get_by_role('table', name='需求确认表', exact=True)).not_to_be_visible()


async def exercise(page, client, store, initial, output, report, control, expect):
    from app.product_flow import status
    from app.requirement_confirmation import confirmation_current
    requirements = {item['id']: item for item in initial['items']
                    if item['id'] in initial['product_context']['scope_ids']}
    assert len(requirements) == 10
    assert all(confirmation_current(initial, item) for item in requirements.values())
    question = next(q for q in initial['questions'] if q['id'] == QID)
    rule = next(item for item in initial['items'] if item['id'] == RULE)
    assert question['related_refs'] == [RULE] and question['answer'] is None
    assert status(initial)[0]['complete'] and status(initial)[1]['complete']
    assert not status(initial)[2]['complete']

    await page.goto(ORIGIN, wait_until='networkidle')
    await page.locator('aside.sidebar button[title="电价管理"]').click()
    workspace = page.locator('.project-workspace:not([hidden])')
    connected = workspace.locator('.connected-workspace')
    await connected.get_by_role('navigation', name='需求工作流程').get_by_role(
        'button', name=re.compile(r'^3\s*确认交付$')).click()
    await expect(connected.get_by_role('heading', name='确认交付版本')).to_be_visible()
    await connected.get_by_role('button', name='查看与核对正式文档', exact=True).click()
    details = workspace.locator('details.connected-details')
    await assert_in_document(details, connected, expect)
    panel = details.get_by_role('region', name='文档章节讨论', exact=True)
    await expect(panel.get_by_role('heading', name='本篇文档的讨论补充')).to_be_visible()
    await expect(panel.locator('.document-discussion-list button')).to_have_count(1)
    await expect(panel).to_contain_text(question['question'])
    await expect(panel).to_contain_text(rule['title'])
    assert store.get(PID) == initial, 'Viewing chapter results wrote data'
    report['checks'].append('Delivery opens the original PRD and its one chapter question; ten confirmed requirements remain current')
    await page.screenshot(path=str(output / '01-document-discussion.png'))

    prerequisite = details.get_by_role('button', name=re.compile(r'^处理本次文档讨论'))
    await expect(prerequisite).to_have_count(1)
    await prerequisite.click()
    await assert_in_document(details, connected, expect)
    await expect(panel.get_by_label('我的回答原文', exact=True)).to_be_visible()
    report['checks'].append('The prerequisite action focuses this chapter follow-up and stays in formal documents')

    # Use the document's own problem action, not a direct switch to refinement.
    reader = details.get_by_role('article', name='PRD 正文', exact=True)
    problem = reader.locator('.document-ready-problem').filter(has_text=question['question'])
    if not await problem.count():
        review = details.locator('details.review-sidebar')
        if await review.get_attribute('open') is None:
            await review.locator(':scope > summary').click()
        problem = review.locator('.review-problem').filter(has_text=question['question'])
    await expect(problem).to_have_count(1)
    await problem.get_by_role('button', name='去回答', exact=True).click()
    await assert_in_document(details, connected, expect)
    await expect(panel.get_by_label('我的回答原文', exact=True)).to_be_visible()
    report['checks'].append('The question action inside document review focuses its local answer without returning to refinement')

    await expect(panel.get_by_role('button', name='确认本次补充已处理', exact=True)).to_have_count(0)
    stage = status(store.get(PID))[2]
    session = (await client.get('/api/session')).json()
    response = await client.post('/api/projects/' + PID + '/stage-checks/3', json=dict(
        expected_revision=initial['revision'], expected_hash=stage['content_hash']),
        headers={'origin': ORIGIN, 'x-csrf-token': session['csrf']})
    report['api_requests'].append(dict(method='POST', path='/api/projects/' + PID + '/stage-checks/3',
        status=response.status_code, purpose='direct_negative_gate_check', response=response.json()))
    assert response.status_code == 409 and response.json().get('code') == 'STAGE_BLOCKED', response.text
    assert store.get(PID) == initial
    report['checks'].append('Unanswered chapter follow-up cannot confirm stage 3 (UI gate and real isolated endpoint rejection)')

    answer = panel.get_by_label('我的回答原文', exact=True)
    await answer.fill(ANSWER)
    assert store.get(PID) == initial, 'Typing an answer approved or saved it'
    await details.get_by_role('tab', name='MRD 评审稿', exact=True).click()
    unsaved = page.get_by_role('dialog', name='未保存的修改', exact=True)
    await expect(unsaved).to_be_visible()
    await expect(unsaved).to_contain_text('文档讨论的回答')
    await unsaved.get_by_role('button', name='留在当前页', exact=True).click()
    await expect(details.get_by_role('tab', name='PRD 评审稿', exact=True)).to_have_attribute('aria-selected', 'true')
    await expect(answer).to_have_value(ANSWER)
    await assert_in_document(details, connected, expect)
    assert store.get(PID) == initial, 'Cancelling tab navigation saved or discarded input'
    report['checks'].append('Unsaved PRD answer blocks switching to MRD; choosing stay retains the PRD and exact typed words without writes')
    await page.screenshot(path=str(output / '02-unsaved-answer-retained.png'))
    control['reject_answer_save'] = True
    await panel.get_by_role('button', name='保存回答原话', exact=True).click()
    await expect(panel.get_by_role('alert')).to_contain_text('离线注入：回答保存失败')
    await expect(answer).to_have_value(ANSWER)
    await assert_in_document(details, connected, expect)
    assert store.get(PID) == initial
    await page.screenshot(path=str(output / '02-failed-save-preserves-input.png'))
    report['checks'].append('Injected answer-save rejection retains the typed words and document position, with no state writes')

    await panel.get_by_role('button', name='保存回答原话', exact=True).click()
    saved = await wait_state(store, lambda p: next(q for q in p['questions'] if q['id'] == QID).get('answer') == ANSWER,
        'Synthetic answer did not save in isolated copy')
    assert not status(saved)[2]['complete']
    await expect(panel.get_by_role('button', name='保存回答原话', exact=True)).to_have_count(0)
    await panel.get_by_role('button', name='直接修改需求', exact=True).click()
    editors = panel.locator('.answer-clause-editor')
    await expect(editors).to_have_count(1)
    await expect(editors.get_by_role('heading', name=rule['title'], exact=True)).to_be_visible()
    await expect(editors.get_by_role('textbox', name=rule['title'] + '（修改后）', exact=True)).to_have_value(rule['statement'])
    await panel.get_by_role('button', name='查看修改并确认', exact=True).click()
    preview = panel.locator('.answer-change-preview')
    await expect(preview).to_be_visible()
    await expect(preview.locator('.answer-difference')).to_have_count(1)
    await expect(preview.locator('.answer-before-after > div').nth(0)).to_contain_text(rule['statement'])
    await expect(preview.locator('.answer-before-after > div').nth(1)).to_contain_text(rule['statement'])
    assert store.get(PID)['items'] == initial['items'], 'Preview silently changed clauses'
    await page.screenshot(path=str(output / '03-one-rule-before-after.png'))
    report['checks'].append('Manual review displays only the directly related original rule and its unchanged before/after; preview is read-only')

    await preview.get_by_role('button', name='确认并继续', exact=True).click()
    applied = await wait_state(store, lambda p: next(q for q in p['questions'] if q['id'] == QID).get('understanding_status') == 'applied',
        'Synthetic manual confirmation did not apply')
    await assert_in_document(details, connected, expect)
    assert applied['documents'] == initial['documents']
    assert all(next(item for item in applied['items'] if item['id'] == iid) == item for iid, item in requirements.items())
    assert applied['sources'][:len(initial['sources'])] == initial['sources']
    assert len(applied['sources']) == len(initial['sources']) + 1, 'Only the synthetic raw-answer source may be added'
    for item in applied['items']:
        if item['id'] == RULE:
            assert item == rule, 'Reviewing unchanged rule modified its business content'
    await expect(panel.get_by_role('button', name='确认本次补充已处理', exact=True)).to_be_enabled()
    await panel.get_by_role('button', name='确认本次补充已处理', exact=True).click()
    final = await wait_state(store, lambda p: status(p)[2]['complete'], 'Stage 3 explicit completion was not saved')
    await expect(panel).to_contain_text('本次补充已核对，可以继续核对文档。')
    await assert_in_document(details, connected, expect)
    assert status(final)[4]['available'], 'Formal document stage remained unavailable after the local follow-up was reviewed'
    assert final['documents'] == initial['documents']
    assert all(confirmation_current(final, item) for item in requirements.values())
    report['checks'].append('Explicit local answer/rule review then stage-3 checkpoint stays in PRD; document work becomes available without confirming ten rows again')
    await page.screenshot(path=str(output / '04-local-review-complete.png'))
    write(output / 'project-after-isolated-test.json', final)
    report['final'] = dict(revision=final['revision'], current_requirement_count=len(requirements),
        original_sources_unchanged=True, synthetic_answer_sources_added=1, documents_unchanged=True,
        stage_3_complete=status(final)[2]['complete'], formal_document_stage_available=status(final)[4]['available'])


async def run(args):
    output, build = args.output.resolve(), args.static_build.resolve()
    assert output.is_relative_to(ROOT / 'evidence') and not output.exists()
    assert (build / 'index.html').is_file()
    output.mkdir(parents=True)
    initial, records, daily_hash = frozen_daily_input(output)
    report = dict(status='running', fixture='FROZEN_TARIFF_V24_WITH_SYNTHETIC_ANSWER',
        model_calls=0, daily_access='SQLite mode=ro only', daily_writes=0, listener_started=False,
        video_recorded=False, business_acceptance='NOT_RUN', model_quality='NOT_RUN', formal_confirmation='NOT_RUN',
        checks=[], api_requests=[], routing_errors=[], page_errors=[], static_build=str(build), served_static={},
        original_project_payload_sha256=daily_hash, synthetic_answer=ANSWER)
    control = {'reject_answer_save': False}
    with tempfile.TemporaryDirectory(prefix='ra-document-discussion-') as scratch:
        os.environ['RA_DATA_DIR'] = str(Path(scratch) / 'import-data')
        import httpx
        from playwright.async_api import async_playwright, expect
        from app.main import create_app
        from tests.check_refine_workspace_browser import NoModelProvider
        provider = NoModelProvider(Path(scratch))
        app = create_app(Path(scratch) / 'fixture-data', provider=provider)
        store = app.state.store
        seed(store, initial, records)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=True)
                context = await browser.new_context(viewport=dict(width=1600, height=1000))
                async def dispatch(route):
                    request, url = route.request, urlsplit(route.request.url)
                    try:
                        assert url.netloc == '127.0.0.1:8765', 'External requests forbidden'
                        if url.path.startswith('/api/'):
                            assert (request.method == 'GET' or request.method == 'POST' and url.path.endswith((
                                '/decision-review/answer', '/decision-review/manual-preview', '/decision-review/commit', '/stage-checks/3'))), \
                                'Unexpected mutation or model call: ' + url.path
                            if control['reject_answer_save'] and url.path.endswith('/decision-review/answer'):
                                control['reject_answer_save'] = False
                                report['api_requests'].append(dict(method=request.method, path=url.path, status=409, injected=True))
                                await route.fulfill(status=409, content_type='application/json', body=json.dumps(dict(
                                    code='OFFLINE_INJECTED_FAILURE', message='离线注入：回答保存失败，输入应保留。'), ensure_ascii=False))
                                return
                            headers = {key: value for key, value in request.headers.items()
                                if key.lower() not in ('host', 'content-length', 'connection', 'accept-encoding')}
                            headers['host'] = '127.0.0.1:8765'
                            response = await client.request(request.method, url.path + ('?' + url.query if url.query else ''),
                                headers=headers, content=request.post_data_buffer)
                            event = dict(method=request.method, path=url.path, status=response.status_code)
                            if request.method != 'GET':
                                event.update(body=request.post_data_json, response=response.json())
                            report['api_requests'].append(event)
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
                    await exercise(page, client, store, initial, output, report, control, expect)
                    assert provider.calls == 0 and not report['page_errors'] and not report['routing_errors']
                    report['status'] = 'passed'
                except Exception as exc:
                    report.update(status='failed', failure=str(exc), traceback=traceback.format_exc())
                    await page.screenshot(path=str(output / 'failure.png'), full_page=True)
                finally:
                    report['model_calls'] = provider.calls
                    write(output / 'result.json', report)
                    await context.close()
                    await browser.close()
    print(json.dumps({key: (str(report.get(key, '')).split('\n')[0] if key == 'failure' else report.get(key))
        for key in ('status', 'checks', 'failure', 'model_calls')}, ensure_ascii=False))
    if report['status'] != 'passed':
        raise SystemExit(1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--static-build', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    asyncio.run(run(parser.parse_args()))
