"""Inspect the actual whole-table model result in an isolated browser, without AI or video.

The supplied session must be a finished check_refinement_autofill_live.py run.
Only its frozen SQLite data and source files are copied. Browser requests are
GET-only, and the only edit/save exercised is the browser's session draft.
This verifies draft availability and interaction, not business correctness or
human adoption of any model output.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import closing
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import tempfile
import traceback
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
ORIGIN = 'http://127.0.0.1:8765'
PID = 'P-d4b61a83e6f14612'


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':')).encode('utf-8')).hexdigest()


def db_fingerprint(path):
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
        db.execute('BEGIN')
        return {table: digest(db.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall())
                for table in ('projects', 'revisions', 'records', 'settings', 'migrations')}


def copy_session(source, destination):
    destination.mkdir()
    with closing(sqlite3.connect((source / 'requirements.sqlite3').as_uri() + '?mode=ro', uri=True)) as original:
        with closing(sqlite3.connect(destination / 'requirements.sqlite3')) as cloned:
            original.backup(cloned)
    shutil.copytree(source / 'sources', destination / 'sources')


async def run(args):
    session, build, output = args.session.resolve(), args.static_build.resolve(), args.output.resolve()
    assert session.is_relative_to(ROOT / 'evidence'), 'Use a finished isolated evidence session'
    assert output.is_relative_to(ROOT / 'evidence') and not output.exists(), 'Use a new evidence output directory'
    assert (build / 'index.html').is_file(), 'A completed static build is required'
    live = session / 'live'
    outcome, frozen, coverage = [read(live / name) for name in
                                ('outcome.json', 'project-after.json', 'coverage-after.json')]
    task = read(live / 'task-final.json')
    assert outcome['provider_requests'] > 0 and outcome['result_applied'] is True
    assert task['status'] in ('succeeded', 'partial')
    assert (frozen['id'], frozen['name']) == (PID, '电价管理')
    assert coverage['requirement_count'] == 10 and coverage['requirements_with_acceptance'] == 10
    rows, items = coverage['rows'], {item['id']: item for item in frozen['items']}
    assert len(rows[0]['acceptance_ids']) == 6, 'This acceptance run expects the actual first six draft criteria'
    session_db = live / 'data/requirements.sqlite3'
    session_before = db_fingerprint(session_db)
    output.mkdir(parents=True)
    report = dict(status='running', checks=[], fixture='ACTUAL_MODEL_WHOLE_TABLE_RESULT',
                  session=str(session), model_task_id=task['id'], revision=frozen['revision'],
                  prior_real_model_requests=outcome['provider_requests'], fresh_model_calls=0,
                  video_recorded=False, listener_started=False, daily_data_access=False,
                  business_correctness='NOT_RUN', human_confirmation='NOT_RUN', formal_delivery='NOT_RUN',
                  page_errors=[], api_requests=[], blocked_requests=[], served_static={}, row_checks=[])
    with tempfile.TemporaryDirectory(prefix='ra-refinement-autofill-') as temporary:
        folder = Path(temporary)
        os.environ['RA_DATA_DIR'] = str(folder / 'import')
        copy_session(live / 'data', folder / 'data')
        import httpx
        from playwright.async_api import async_playwright, expect
        from app.main import create_app
        from tests.check_refine_workspace_browser import NoModelProvider
        provider = NoModelProvider(folder)
        app = create_app(folder / 'data', provider=provider)
        store = app.state.store
        assert store.get(PID) == frozen, 'Copied session must match actual terminal model output'
        isolated_before = db_fingerprint(folder / 'data/requirements.sqlite3')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
            status_response = await client.get(f'/api/projects/{PID}/refinement-preparation')
            status_response.raise_for_status()
            preparation = status_response.json()
            write(output / 'preparation-reuse.json', preparation)
            assert preparation['status'] in ('ready', 'ready_with_limits') and not preparation['needs_organize']
            assert preparation['draft_schema'] == 2 and preparation['draft_coverage']['complete']
            states_response = await client.get(f'/api/projects/{PID}/requirement-confirmation/state')
            states_response.raise_for_status()
            states = {row['requirement_id']: row for row in states_response.json()['rows']}
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=True)
                context = await browser.new_context(viewport=dict(width=1440, height=900))

                async def route_request(route):
                    request, url = route.request, urlsplit(route.request.url)
                    if url.netloc != '127.0.0.1:8765' or (url.path.startswith('/api/') and request.method != 'GET'):
                        report['blocked_requests'].append(dict(method=request.method, url=request.url))
                        await route.fulfill(status=409, content_type='application/json',
                                            body=json.dumps(dict(message='Read-only browser acceptance forbids this request')))
                        return
                    if url.path.startswith('/api/'):
                        response = await client.get(url.path + ('?' + url.query if url.query else ''))
                        report['api_requests'].append(dict(method=request.method, path=url.path, status=response.status_code))
                        await route.fulfill(status=response.status_code, body=response.content,
                                            content_type=response.headers.get('content-type', 'application/json'))
                    else:
                        file = (build / (url.path.lstrip('/') or 'index.html')).resolve()
                        assert file.is_relative_to(build) and file.is_file(), 'Unknown static file: ' + url.path
                        data = file.read_bytes()
                        report['served_static'][url.path] = hashlib.sha256(data).hexdigest()
                        await route.fulfill(status=200, body=data,
                                            content_type=mimetypes.guess_type(file.name)[0] or 'application/octet-stream')

                await context.route('**/*', route_request)
                page = await context.new_page()
                page.set_default_timeout(12000)
                page.on('pageerror', lambda error: report['page_errors'].append(str(error)))
                try:
                    await page.goto(ORIGIN, wait_until='networkidle')
                    await page.locator('aside.sidebar button[title="电价管理"]').click()
                    work = page.locator('.connected-workspace:visible')

                    async def enter_normally():
                        # Both chat and direct entry use the same preparation coordinator.
                        direct = work.get_by_role('button', name='整理并进入完善需求', exact=True)
                        button = direct if await direct.count() else work.get_by_role('button', name='进入完善需求', exact=True)
                        await expect(button).to_be_enabled()
                        await button.click()
                        await expect(work.get_by_role('table', name='需求确认表', exact=True)).to_be_visible()
                        assert not report['blocked_requests'], report['blocked_requests']

                    await enter_normally()
                    table = work.get_by_role('table', name='需求确认表', exact=True)
                    await expect(table.locator('tr[data-requirement-id]')).to_have_count(10)
                    report['checks'].append('从提供信息正常进入完善需求，复用已完成的整表真实模型草稿，无新任务')

                    async def assert_all_criteria(manual=None):
                        for row in rows:
                            rid, title = row['requirement_id'], row['title']
                            cell = table.locator(f'tr[data-cell-key="requirement-{rid}-acceptance"]')
                            await expect(cell.locator('textarea')).to_have_count(len(row['acceptance_ids']))
                            for index, acceptance_id in enumerate(row['acceptance_ids'], 1):
                                item = items[acceptance_id]
                                field = cell.get_by_role('textbox', name=f'{title} 验收建议 {index}', exact=True)
                                value = manual if manual is not None and row is rows[0] and index == 1 else item['statement']
                                await expect(field).to_have_value(value)
                                if item['selection_status'] == 'candidate':
                                    await expect(field.locator('..').get_by_text('AI 建议 · 随本次确认', exact=True)).to_be_visible()
                            for candidate_id in row['acceptance_revision_candidate_ids']:
                                candidate = items[candidate_id]
                                advice = cell.locator('.sheet-group-reference').filter(has_text=candidate['statement'])
                                await expect(advice.get_by_text(candidate['statement'], exact=True)).to_be_visible()
                                await expect(advice.get_by_text('AI 修订建议 · 尚未使用', exact=True)).to_be_visible()

                    await assert_all_criteria()
                    for row in rows:
                        report['row_checks'].append({key: row[key] for key in
                            ('requirement_id', 'title', 'acceptance_ids', 'new_acceptance_ids',
                             'acceptance_revision_candidate_ids')})
                    report['displayed_acceptance_count'] = sum(len(row['acceptance_ids']) for row in rows)
                    report['checks'].append('10 条原需求的全部实际验收文本与待确认 AI 标记逐项匹配模型落库结果')
                    first = rows[0]
                    first_field = table.get_by_role('textbox', name=first['title'] + ' 验收建议 1', exact=True)
                    await first_field.scroll_into_view_if_needed()
                    await page.screenshot(path=str(output / '01-first-acceptance-start.png'))
                    await table.get_by_role('textbox', name=first['title'] + ' 验收建议 6', exact=True).scroll_into_view_if_needed()
                    await page.screenshot(path=str(output / '02-first-acceptance-six.png'))

                    # Use each visible source action, and verify the actual referenced excerpt.
                    for index, row in enumerate(rows):
                        rid = row['requirement_id']
                        root = items[rid]
                        revisions = [item for item in frozen['items'] if item.get('target_item_id') == rid
                                     and item['kind'] == 'requirement' and item['selection_status'] == 'candidate'
                                     and not item.get('superseded_by')]
                        offered = root if states[rid]['current'] or not revisions else revisions[-1]
                        ref = offered['source_refs'][0]
                        source = next(source for source in frozen['sources'] if source['id'] == ref['source_id'])
                        excerpt = next(excerpt for excerpt in source['excerpts'] if excerpt['id'] == ref['excerpt_id'])
                        await table.locator(f'tr[data-requirement-id="{rid}"]').get_by_role('button', name='查看来源', exact=True).click()
                        preview = page.get_by_role('dialog', name='附件内容', exact=True)
                        await expect(preview).to_be_visible()
                        await expect(preview.get_by_role('heading', name=source['title'], exact=True)).to_be_visible()
                        displayed = preview.locator('[id="excerpt-' + ref['excerpt_id'] + '"]')
                        await expect(displayed).to_be_visible()
                        await expect(displayed).to_have_text(excerpt['text'])
                        if index == 0:
                            await page.screenshot(path=str(output / '03-actual-source.png'))
                        report['row_checks'][index]['visible_source'] = dict(ref, source_title=source['title'])
                        await preview.get_by_role('button', name='关闭附件', exact=True).click()
                        await page.get_by_role('dialog', name='项目资料', exact=True).get_by_role('button', name='关闭项目资料', exact=True).click()
                    report['checks'].append('逐条点击 10 条需求的查看来源，实际标题与引用摘录均可见')

                    scroll = work.locator('.refinement-sheet-scroll')
                    await first_field.scroll_into_view_if_needed()
                    before_scroll = await scroll.evaluate('(element)=>element.scrollTop')
                    await scroll.hover()
                    await page.mouse.wheel(0, 800)
                    await page.wait_for_function('(before)=>document.querySelector(".connected-workspace:not([hidden]) .refinement-sheet-scroll").scrollTop>before', arg=before_scroll)
                    await table.locator('textarea').last.scroll_into_view_if_needed()
                    await expect(work.get_by_role('button', name='保存草稿', exact=True)).to_be_in_viewport()
                    await page.screenshot(path=str(output / '04-table-bottom.png'))
                    report['checks'].append('表格支持鼠标滚轮，底部内容可达且保存与确认入口保持可见')

                    manual = items[first['acceptance_ids'][0]]['statement'] + '（仅隔离浏览器草稿保留验证，未提交。）'
                    await first_field.fill(manual)
                    await work.get_by_role('button', name='保存草稿', exact=True).click()
                    await page.reload(wait_until='networkidle')
                    await expect(table).to_be_visible()
                    await assert_all_criteria(manual)
                    await first_field.scroll_into_view_if_needed()
                    await page.screenshot(path=str(output / '05-restored-local-edit.png'))
                    report['checks'].append('修改一条实际验收后保存浏览器草稿，刷新保留修改及其他全部实际建议')
                    await work.locator('.connected-steps').get_by_role('button', name=re.compile('提供信息')).click()
                    await enter_normally()
                    await assert_all_criteria(manual)
                    assert not report['blocked_requests'] and not report['page_errors']
                    assert provider.calls == 0
                    assert store.get(PID) == frozen
                    assert db_fingerprint(folder / 'data/requirements.sqlite3') == isolated_before
                    assert db_fingerprint(session_db) == session_before
                    report['checks'].append('返回提供信息后再次进入仍复用草稿：0 新模型请求、0 后端写入，原始会话及隔离数据不变')
                    report.update(status='passed', copied_store_unchanged=True, source_session_unchanged=True)
                except Exception as error:
                    report.update(status='failed', failure=str(error), traceback=traceback.format_exc())
                    await page.screenshot(path=str(output / 'failure.png'))
                finally:
                    report['fresh_model_calls'] = provider.calls
                    write(output / 'result.json', report)
                    await context.close()
                    await browser.close()
    print(json.dumps({key: report.get(key) for key in
        ('status', 'checks', 'failure', 'displayed_acceptance_count', 'fresh_model_calls', 'video_recorded')}, ensure_ascii=False))
    if report['status'] != 'passed':
        raise SystemExit(1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session', type=Path, required=True)
    parser.add_argument('--static-build', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    asyncio.run(run(parser.parse_args()))
