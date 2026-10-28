"""Navigation and confirmation-state browser regression over isolated ASGI.

Synthetic prepared projects only. All browser requests are intercepted on the
sole 8765 origin; no listener, daily data, model calls or backend writes.
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
import sys
import tempfile
import traceback
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
ORIGIN = 'http://127.0.0.1:8765'


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def seed(store, action):
    from app.core import brief_hash, now
    from app.contracts import review_target
    from app.product_flow import checkpoint, review_document, status
    from app.requirement_confirmation import confirmation_current, row_fingerprint
    from tests.helpers import prepared
    from tests.product_flow_helpers import check_prepared

    project = prepared(store)
    pid = project['id']
    with store.edit(pid, project['revision'], '合成导航回归输入') as (p, _):
        # prepared() uses fixed item IDs; namespace each fixture so the real
        # store's cross-project identity guard remains enabled.
        ids = {item['id']: item['id'] + '-' + action.upper() for item in p['items']}
        def renamed(value):
            if isinstance(value, dict):
                return {ids.get(key, key): renamed(child) for key, child in value.items()}
            if isinstance(value, list):
                return [renamed(child) for child in value]
            return ids.get(value, value) if isinstance(value, str) else value
        isolated = renamed(p)
        p.clear()
        p.update(isolated)
        p['name'] = '合成储能云导航核对 · ' + action
        p['workspace_draft'] = dict(text='合成电价维护导航回归，非实际业务规则。', entry_mode='direct', saved_at=now())
        p['workflow_mode'] = 'shared-draft'
        next(i for i in p['items'] if i['kind'] == 'requirement')['change_type'] = 'modified'
    project = check_prepared(store, pid, through=5)
    with store.edit(pid, project['revision'], '冻结已有人工确认的合成夹具', bump=False) as (p, _):
        p['requirement_confirmation_mode'] = 'table'
        for item in p['items']:
            if item['kind'] == 'requirement':
                item['requirement_confirmation'] = dict(schema='requirement-confirmation-1',
                    actor='authenticated_local_user', content_hash=row_fingerprint(p, item), reviewed_at=now())
        for document in p['documents'].values():
            document['item_snapshot'] = copy.deepcopy(p['items'])
            document['brief_hash'] = brief_hash(p)
            document['draft_revision'] = p['revision']
        p['ui']['brief_hash'] = brief_hash(p)
        p['review']['target_hash'] = review_target(p)
        for step in range(1, 6):
            if step == 5:
                for kind, document in p['documents'].items():
                    review_document(p, kind, document['id'])
            checkpoint(p, step, status(p)[step - 1]['content_hash'])
    initial = store.get(pid)
    assert all(row['complete'] for row in status(initial)[:5])
    assert all(confirmation_current(initial, item) for item in initial['items'] if item['kind'] == 'requirement')
    # A matching synthetic historical ingest lets refresh reuse its completed
    # preparation. It must not ask for another model task just to restore edits.
    store.record(pid, 'run', dict(stage='ingest', status='succeeded', result_applied=True,
        revision=initial['revision'], created=now(), completed=now(), calls=0,
        message='合成已完成整理记录，仅用于刷新恢复测试。'), 'RUN-NAV-PREP-' + action.upper())
    store.record(pid, 'user_task', dict(action=action,
        label='合成 MRD 讨论稿' if action == 'document' else '合成内容审查', status='succeeded',
        created=now(), completed=now(), input_revision=initial['revision'], source_ids=['SRC-0001'],
        stages=['prd' if action == 'document' else 'review'], run_ids=[], max_calls=1, calls=0,
        cost=None, message='合成离线任务记录，非真实模型结果。', completed_steps=1,
        step_states=[dict(stage='prd' if action == 'document' else 'review', status='succeeded')],
        retry_input=dict(action=action, message='合成导航验收', document_type='mrd')), 'TASK-NAV-' + action.upper())
    return pid, initial


async def open_project(page, initial, expect):
    await page.locator('aside.sidebar button[title="' + initial['name'] + '"]').click()
    workspace = page.locator('.project-workspace:not([hidden])')
    connected = workspace.locator('.connected-workspace')
    await expect(workspace.get_by_role('heading', name=initial['name'], exact=True)).to_be_visible()
    await expect(connected.locator('.connected-task')).to_be_visible()
    return workspace, connected, workspace.locator('details.connected-details')


async def assert_three_steps(details, expect):
    await expect(details.locator('.workflow-heading .eyebrow')).to_contain_text('第 3 / 3 步')
    assert not re.search(r'第\s*[45]\s*/\s*5\s*步', await details.inner_text()), 'Old five-step label leaked into formal view'


async def assert_document(details, kind, expect):
    await expect(details).to_have_attribute('open', '')
    await expect(details.locator('.workflow-page')).to_have_attribute('data-phase', '4')
    await expect(details.get_by_role('tab', name=kind.upper() + ' 评审稿', exact=True)).to_have_attribute('aria-selected', 'true')
    await expect(details.get_by_role('article', name=kind.upper() + ' 正文', exact=True)).to_be_visible()
    await assert_three_steps(details, expect)


async def enter_table_from_delivery(connected, details, expect):
    await connected.get_by_role('button', name='查看与核对正式文档', exact=True).click()
    await expect(details).to_have_attribute('open', '')
    await details.get_by_role('button', name='修改整体需求', exact=True).click()
    await expect(connected.get_by_role('table', name='需求确认表', exact=True)).to_be_visible()
    await expect(connected).to_have_attribute('data-step', 'refine')
    await expect(details).not_to_have_attribute('open', '')


async def assert_table_scroll(page, connected, output, report, expect):
    scroller = connected.locator('.refinement-sheet-scroll')
    footer = connected.locator('.refinement-sheet-footer')
    await expect(footer.get_by_role('button', name='返回交付', exact=True)).to_be_visible()
    dimensions = await scroller.evaluate('e=>({height:e.clientHeight,scrollHeight:e.scrollHeight,scrollTop:e.scrollTop})')
    report['table_geometry'] = dimensions
    # The current laptop layout accepts >=190px at the smallest supported
    # effective viewport; retain independent scroll/footer checks below.
    assert dimensions['height'] >= 190, '1280x720 table viewport too short: ' + str(dimensions)
    assert dimensions['scrollHeight'] > dimensions['height'], 'Fixture does not exercise scrolling'
    assert await footer.evaluate('e=>{const r=e.getBoundingClientRect();return r.top>=0&&r.bottom<=innerHeight&&r.left>=0&&r.right<=innerWidth}'), 'Table footer is clipped'
    before = await footer.bounding_box()
    bounds = await scroller.bounding_box()
    await page.mouse.move(bounds['x'] + bounds['width'] * .7, bounds['y'] + bounds['height'] * .55)
    await page.mouse.wheel(0, 1300)
    await page.wait_for_timeout(200)
    assert await scroller.evaluate('e=>e.scrollTop') > dimensions['scrollTop'], 'Mouse wheel did not scroll the table'
    after = await footer.bounding_box()
    assert abs(before['y'] - after['y']) < 3, 'Footer moved with the table'
    assert await footer.evaluate('e=>e.getBoundingClientRect().bottom<=innerHeight'), 'Footer left viewport after scrolling'
    report['checks'].append('1280x720: table viewport >=190px; real wheel scrolls rows and footer remains fully visible')
    await page.screenshot(path=str(output / '04-table-scroll.png'))


async def exercise(page, store, fixtures, output, report, expect):
    await page.goto(ORIGIN, wait_until='networkidle')
    doc_id, initial = fixtures['document']
    workspace, connected, details = await open_project(page, initial, expect)
    nav = connected.get_by_role('navigation', name='需求工作流程')
    delivery = nav.get_by_role('button', name=re.compile(r'^3\s*确认交付$'))
    await delivery.click()
    await connected.get_by_role('button', name='打开正式确认', exact=True).click()
    await expect(details.locator('.workflow-page')).to_have_attribute('data-phase', '5')
    await assert_three_steps(details, expect)
    for kind in ('mrd', 'prd'):
        card = details.locator('.delivery-version').filter(has=page.locator('b', has_text=re.compile('^' + kind.upper() + '$')))
        await expect(card).to_have_count(1)
        await card.get_by_role('button', name='查看文档', exact=True).click()
        await assert_document(details, kind, expect)
        await page.screenshot(path=str(output / ('01-formal-' + kind + '.png')))
        await details.get_by_role('navigation', name='正式文档与确认').get_by_role('button', name='正式确认', exact=True).click()
        await expect(details.locator('.workflow-page')).to_have_attribute('data-phase', '5')
    report['checks'].append('Formal confirmation MRD and PRD cards each open their own document and use step 3/3')

    # A fresh browser navigation starts at the main three-step workspace, with
    # its latest task opened through the visible task record control.
    await page.reload(wait_until='networkidle')
    workspace, connected, details = await open_project(page, initial, expect)
    await expect(details).not_to_have_attribute('open', '')
    await connected.get_by_role('button', name='查看任务与恢复', exact=True).click()
    await connected.get_by_role('region', name='当前任务记录', exact=True).get_by_role('button', name='查看讨论稿', exact=True).click()
    await assert_document(details, 'mrd', expect)
    report['checks'].append('Latest document task opens expanded MRD formal view using its saved document_type')
    await page.screenshot(path=str(output / '02-document-task-result.png'))

    review_id, review_initial = fixtures['review']
    workspace, review_connected, review_details = await open_project(page, review_initial, expect)
    await review_connected.get_by_role('button', name='查看任务与恢复', exact=True).click()
    await review_connected.get_by_role('region', name='当前任务记录', exact=True).get_by_role('button', name='查看本步结果', exact=True).click()
    await assert_document(review_details, 'mrd', expect)
    review = review_details.get_by_role('region', name='当前版本内容审查', exact=True)
    await expect(review_details.locator('details.review-sidebar')).to_have_attribute('open', '')
    await expect(review).to_be_in_viewport()
    await expect(review).to_be_focused()
    report['checks'].append('Latest review task expands the formal document review and focuses current-version content review')
    await page.screenshot(path=str(output / '03-review-task-result.png'))

    # Return through the formal document's visible edit control, without invoking
    # the preparation/model workflow from the information step.
    workspace, connected, details = await open_project(page, initial, expect)
    await expect(connected.get_by_role('textbox', name='你的想法', exact=True)).to_be_visible()
    await connected.get_by_role('navigation', name='需求工作流程').get_by_role('button', name=re.compile(r'^3\s*确认交付$')).click()
    await enter_table_from_delivery(connected, details, expect)
    await expect(connected.get_by_role('table', name='需求确认表', exact=True)).to_be_visible()
    await expect(connected.locator('.refinement-sheet-footer').get_by_role('button', name='返回交付', exact=True)).to_be_enabled()
    await assert_table_scroll(page, connected, output, report, expect)
    nav = connected.get_by_role('navigation', name='需求工作流程')
    delivery = nav.get_by_role('button', name=re.compile(r'^3\s*确认交付$'))
    before_requests = len(report['api_requests'])
    await delivery.click()
    await expect(connected.get_by_role('heading', name='确认交付版本', exact=True)).to_be_visible()
    await expect(connected).to_have_attribute('data-step', 'delivery')
    assert store.get(doc_id) == initial, 'Returning with valid confirmation changed project data'
    assert not any(r['method'] != 'GET' for r in report['api_requests'][before_requests:]), 'Top return submitted a write'
    report['checks'].append('Unedited valid confirmation: top step returns to delivery with no POST, revision or project change')

    await enter_table_from_delivery(connected, details, expect)
    await connected.locator('.refinement-sheet-footer').get_by_role('button', name='返回交付', exact=True).click()
    await expect(connected).to_have_attribute('data-step', 'delivery')
    assert store.get(doc_id) == initial
    report['checks'].append('Unedited valid confirmation: footer return also remains navigation-only')

    await enter_table_from_delivery(connected, details, expect)
    field = connected.get_by_role('textbox', name='希望达到的目标', exact=True)
    changed = initial['product_context']['intent'] + '；合成本地编辑，必须重新确认。'
    await field.fill(changed)
    await expect(connected.locator('.refinement-sheet-footer').get_by_role('button', name='确认并进入交付', exact=True)).to_be_visible()
    await delivery.click()
    await expect(connected).to_have_attribute('data-step', 'refine')
    await expect(connected.locator('.refinement-sheet-footer').get_by_role('button', name='确认并进入交付', exact=True)).to_be_focused()
    await expect(field).to_have_value(changed)
    assert store.get(doc_id) == initial, 'Editing or blocked navigation changed server state'
    report['checks'].append('Unsaved edit cannot use the top delivery step to bypass explicit table confirmation')

    await connected.locator('.refinement-sheet-footer').get_by_role('button', name='保存草稿', exact=True).click()
    await expect(connected.locator('.refinement-sheet-footer').get_by_role('status')).to_contain_text('草稿已保存在当前浏览器')
    await delivery.click()
    await expect(connected).to_have_attribute('data-step', 'refine')
    await expect(field).to_have_value(changed)
    assert store.get(doc_id) == initial
    report['checks'].append('Saving a browser draft does not restore old confirmation or bypass the top-step gate')
    await page.screenshot(path=str(output / '05-saved-draft-blocked.png'))

    await page.reload(wait_until='networkidle')
    workspace, connected, details = await open_project(page, initial, expect)
    await expect(connected.get_by_role('textbox', name='你的想法', exact=True)).to_be_visible()
    await connected.get_by_role('button', name='2 完善需求', exact=True).click()
    await expect(connected.get_by_role('table', name='需求确认表', exact=True)).to_be_visible()
    field = connected.get_by_role('textbox', name='希望达到的目标', exact=True)
    await expect(field).to_have_value(changed)
    await expect(connected.locator('.refinement-sheet-footer').get_by_role('button', name='确认并进入交付', exact=True)).to_be_visible()
    await connected.get_by_role('navigation', name='需求工作流程').get_by_role('button', name=re.compile(r'^3\s*确认交付$')).click()
    await expect(connected).to_have_attribute('data-step', 'refine')
    await expect(field).to_have_value(changed)
    assert store.get(doc_id) == initial
    report['checks'].append('Reload restores the exact saved edit and keeps it behind explicit table confirmation')
    await page.screenshot(path=str(output / '06-restored-draft-blocked.png'))
    assert store.get(review_id) == review_initial, 'Review navigation changed project data'


async def run(args):
    output, build = args.output.resolve(), args.static_build.resolve()
    assert output.is_relative_to(ROOT / 'evidence') and output != ROOT / 'evidence'
    assert not output.exists() and (build / 'index.html').is_file()
    output.mkdir(parents=True)
    report = dict(status='running', fixture='SYNTHETIC_STORAGE_CLOUD_NAVIGATION',
        model_calls=0, daily_data_access=False, listener_started=False, video_recorded=False,
        checks=[], api_requests=[], routing_errors=[], page_errors=[], static_build=str(build), served_static={},
        model_quality='NOT_RUN', business_acceptance='NOT_RUN', formal_confirmation='NOT_RUN')
    with tempfile.TemporaryDirectory(prefix='ra-navigation-consistency-') as scratch:
        os.environ['RA_DATA_DIR'] = str(Path(scratch) / 'import-data')
        import httpx
        from playwright.async_api import async_playwright, expect
        from app.main import create_app
        from tests.check_refine_workspace_browser import NoModelProvider
        provider = NoModelProvider(Path(scratch))
        app = create_app(Path(scratch) / 'fixture-data', provider=provider)
        store = app.state.store
        fixtures = {action: seed(store, action) for action in ('document', 'review')}
        write(output / 'projects-before.json', {action: initial for action, (_, initial) in fixtures.items()})
        report['project_ids'] = {action: pid for action, (pid, _) in fixtures.items()}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=True)
                context = await browser.new_context(viewport=dict(width=1280, height=720))

                async def dispatch(route):
                    request, url = route.request, urlsplit(route.request.url)
                    try:
                        assert url.netloc == '127.0.0.1:8765', 'External requests forbidden'
                        if url.path.startswith('/api/'):
                            assert request.method == 'GET', 'Unexpected write or model endpoint: ' + request.method + ' ' + url.path
                            headers = {k: v for k, v in request.headers.items()
                                if k.lower() not in ('host', 'content-length', 'connection', 'accept-encoding')}
                            headers['host'] = '127.0.0.1:8765'
                            response = await client.request('GET', url.path + ('?' + url.query if url.query else ''), headers=headers)
                            report['api_requests'].append(dict(method='GET', path=url.path, status=response.status_code))
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
                    await exercise(page, store, fixtures, output, report, expect)
                    assert all(store.get(pid) == initial for pid, initial in fixtures.values())
                    assert provider.calls == 0 and not report['page_errors'] and not report['routing_errors']
                    report.update(status='passed', fixture_unchanged=True)
                except Exception as exc:
                    report.update(status='failed', failure=str(exc), traceback=traceback.format_exc())
                    await page.screenshot(path=str(output / 'failure.png'), full_page=True)
                finally:
                    report['model_calls'] = provider.calls
                    write(output / 'projects-after.json', {action: store.get(pid) for action, (pid, _) in fixtures.items()})
                    write(output / 'result.json', report)
                    await context.close()
                    await browser.close()
    print(json.dumps({key: report.get(key) for key in ('status', 'checks', 'failure', 'model_calls', 'table_geometry')}, ensure_ascii=False))
    if report['status'] != 'passed':
        raise SystemExit(1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--static-build', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    asyncio.run(run(parser.parse_args()))
