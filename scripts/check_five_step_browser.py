"""Current five-step browser smoke and negative checks; no model requests.

Run with --web-dist evidence/repair-20260925/dist-frontend. The app and
browser use a temporary data directory and loopback port, then stop.
"""
import argparse
import asyncio
import importlib
import json
import os
import socket
import sys
import tempfile
import traceback
from datetime import datetime, timezone
import threading
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--web-dist', type=Path, required=True)
parser.add_argument('--output', type=Path, default=Path('evidence/repair-20260925/five-step-browser-result.json'))
args = parser.parse_args()
RESULTS = []
temporary = tempfile.TemporaryDirectory(prefix='ra-five-step-')
os.environ['RA_DATA_DIR'] = str(Path(temporary.name) / 'module-data')
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uvicorn
from playwright.async_api import async_playwright
from starlette.staticfiles import StaticFiles
from app.main import create_app
from tests.helpers import prepared

synthetic_url = 'https://synthetic.example.test/accepted'
main_module = importlib.import_module('app.main')
original_webpage = main_module.webpage
async def isolated_webpage(url, dynamic=False):
    if url == synthetic_url:
        raw = b'<html><body>synthetic public requirement</body></html>'
        return raw, 'synthetic public requirement', 'http-text', url
    return await original_webpage(url, dynamic)
main_module.webpage = isolated_webpage


async def main():
    dist = args.web_dist.resolve()
    assert (dist / 'index.html').is_file(), dist
    folder = Path(temporary.name)
    app = create_app(folder / 'test-data', access_token='synthetic-browser-token', env_path=folder / 'absent.env')
    slow = {'enabled': False, 'inflight': 0, 'maximum': 0}
    @app.middleware('http')
    async def delayed_project_read(request, call_next):
        if slow['enabled'] and request.method == 'GET' and request.url.path == '/api/projects/' + project['id']:
            slow['inflight'] += 1
            slow['maximum'] = max(slow['maximum'], slow['inflight'])
            try:
                await asyncio.sleep(1.5)
                return await call_next(request)
            finally:
                slow['inflight'] -= 1
        return await call_next(request)
    app.router.routes[:] = [r for r in app.router.routes if getattr(r, 'name', None) != 'web']
    app.mount('/', StaticFiles(directory=dist, html=True), name='isolated-web')
    project = prepared(app.state.store)
    with app.state.store.edit(project['id'], project['revision'], '五步浏览器夹具') as (draft, _):
        draft.setdefault('product_context', {})['scope_ids'] = ['REQ-0001']
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='error', access_log=False))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    results = RESULTS
    screenshots = args.output.resolve().parent / 'browser-shots'
    screenshots.mkdir(parents=True, exist_ok=True)
    base = f'http://127.0.0.1:{port}'
    try:
        for _ in range(100):
            if server.started:
                break
            await asyncio.sleep(.05)
        assert server.started
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                page = await browser.new_page(viewport={'width': 1440, 'height': 900})
                errors = []
                page.on('pageerror', lambda err: errors.append(str(err)))
                await page.goto(base)
                await page.get_by_label('本机访问令牌').fill('synthetic-browser-token')
                await page.get_by_role('button', name='进入工作台').click()
                await page.get_by_role('button', name='合成工程验证项目').first.click()
                work = page.locator('.project-workspace:not([hidden])')
                await work.get_by_role('heading', name='合成工程验证项目', exact=True).wait_for()
                expected = [
                    ('提供现状与诉求', '描述这次想解决的问题'),
                    ('核对现状、价值与改动范围', '核对本次改动范围'),
                    ('澄清流程与规则', '核对业务行为与验收'),
                    ('评审 MRD 与 PRD', '核对两份交付文档'),
                    ('确认与交接', '核对指定版本'),
                ]
                for index, (step, heading) in enumerate(expected, 1):
                    await work.get_by_role('navigation', name='需求工作阶段').get_by_role('button', name=step, exact=False).click()
                    await work.locator('.workflow-page:not([hidden]) .workflow-heading').get_by_role('heading', name=step, exact=True).wait_for(state='visible', timeout=8000)
                    shot = screenshots / f'step-{index}.png'
                    await page.screenshot(path=str(shot), full_page=True)
                    results.append({'case': step, 'status': 'UI_VISIBLE', 'screenshot': str(shot)})
                await work.get_by_role('navigation', name='需求工作阶段').get_by_role('button', name='澄清流程与规则', exact=False).click()
                question = '相接边界如何处理？'
                await work.get_by_role('button', name='暂不确定，保留未知').click()
                await work.get_by_text('仍未解决，不会自动解除阻塞', exact=False).wait_for()
                await work.get_by_label(question, exact=True).fill('合成回答：相接边界允许。')
                behavior = work.locator('.step-check-2 .behavior-details')
                await behavior.locator('summary').first.click()
                item = behavior.locator('details').filter(has=page.locator('summary', has_text='REQ-0001')).first
                await item.locator('summary').click()
                await item.get_by_label('主要流程').fill('合成操作流程：保存并显示时段。')
                await work.get_by_role('navigation', name='需求工作阶段').get_by_role('button', name='核对现状、价值与改动范围', exact=False).click()
                draft_dialog = page.get_by_role('dialog', name='未保存的修改')
                await draft_dialog.wait_for()
                await draft_dialog.get_by_role('button', name='保存后离开').click()
                await draft_dialog.wait_for(state='hidden')
                saved = app.state.store.get(project['id'])
                assert saved['questions'][0]['answer'] == '合成回答：相接边界允许。'
                assert saved['items'][0]['behavior']['flow'] == '合成操作流程：保存并显示时段。'
                results.append({'case': 'answer-and-behavior-save-on-leave', 'status': 'VERIFIED'})
                await work.get_by_role('navigation', name='需求工作阶段').get_by_role('button', name='澄清流程与规则', exact=False).click()
                await work.locator('.answered-editor').locator('summary').click()
                assert await work.locator('.answered-editor').get_by_label(question, exact=True).input_value() == '合成回答：相接边界允许。'
                assert await work.locator('.answered-editor').get_by_role('button', name='暂不确定，保留未知').count() == 0
                assert await work.locator('.answered-editor').get_by_text('仍未解决，不会自动解除阻塞', exact=False).count() == 0
                results.append({'case': 'answered-unknown-action-hidden', 'status': 'VERIFIED'})
                await work.get_by_role('button', name='项目资料', exact=False).first.click()
                drawer = page.get_by_role('dialog', name='项目资料')
                url = drawer.get_by_label('公开网页 URL')
                async def reject_url(route):
                    await route.abort()
                await page.route('**/api/projects/*/sources/url', reject_url)
                await url.fill('https://example.invalid/test')
                await drawer.get_by_role('button', name='确认有权读取并添加').click()
                await drawer.get_by_role('alert').wait_for()
                assert await url.input_value() == 'https://example.invalid/test'
                await page.unroute('**/api/projects/*/sources/url', reject_url)
                results.append({'case': 'rejected-url-keeps-input', 'status': 'VERIFIED'})
                await url.fill('not-a-url')
                await drawer.get_by_role('button', name='确认有权读取并添加').click()
                await drawer.get_by_role('alert').wait_for()
                assert await url.input_value() == 'not-a-url'
                assert app.state.store.get(project['id'])['sources'][-1]['parse_status'] == 'failed'
                results.append({'case': 'stored-url-read-failure-keeps-input', 'status': 'VERIFIED'})
                async def accepted_url(route):
                    await asyncio.sleep(.35)
                    await route.fulfill(status=200, content_type='application/json', body=json.dumps({'parse_status': 'read'}))
                await page.route('**/api/projects/*/sources/url', accepted_url)
                await url.fill('https://example.invalid/first')
                await drawer.get_by_role('button', name='确认有权读取并添加').click()
                await url.fill('https://example.invalid/new-input')
                await page.wait_for_timeout(650)
                assert await url.input_value() == 'https://example.invalid/new-input'
                await url.fill('https://example.invalid/second')
                await drawer.get_by_role('button', name='确认有权读取并添加').click()
                await page.wait_for_timeout(650)
                assert await url.input_value() == ''
                await page.unroute('**/api/projects/*/sources/url', accepted_url)
                results.append({'case': 'successful-url-clears-only-submitted-value', 'status': 'UI_STATE_VERIFIED_WITH_SYNTHETIC_RESPONSE'})
                source_count = len(app.state.store.get(project['id'])['sources'])
                await url.fill(synthetic_url)
                async with page.expect_response(lambda response: response.request.method == 'POST' and response.url.endswith('/sources/url') and response.status == 200) as accepted:
                    await drawer.get_by_role('button', name='确认有权读取并添加').click()
                source = await (await accepted.value).json()
                assert source['parse_status'] == 'read'
                await page.wait_for_function("() => document.querySelector('input[placeholder=\"https://…\"]')?.value === ''")
                assert len(app.state.store.get(project['id'])['sources']) == source_count + 1
                await drawer.get_by_role('button', name='关闭项目资料').click()
                await drawer.wait_for(state='hidden')
                assert await page.get_by_role('dialog', name='未保存的修改').count() == 0
                await work.get_by_role('navigation', name='需求工作阶段').get_by_role('button', name='核对现状、价值与改动范围', exact=False).click()
                await work.get_by_role('navigation', name='需求工作阶段').get_by_role('button', name='澄清流程与规则', exact=False).click()
                assert len(app.state.store.get(project['id'])['sources']) == source_count + 1
                results.append({'case': 'successful-url-persists-once-and-clears-dirty', 'status': 'VERIFIED', 'source_id': source['id']})
                task_id = app.state.store.record(project['id'], 'user_task', dict(action='clarify', label='合成慢任务', status='running', message='仅测试轮询，无模型调用', calls=0, max_calls=0, run_ids=[], source_ids=[], completed_steps=0, stages=['clarify'], cost=None))
                await page.reload()
                await page.get_by_role('button', name='合成工程验证项目').first.click()
                work = page.locator('.project-workspace:not([hidden])')
                await work.locator('.task-status').wait_for()
                slow['enabled'] = True
                for _ in range(50):
                    if slow['inflight']:
                        break
                    await asyncio.sleep(.05)
                assert slow['inflight'] == 1
                await work.locator('.answered-editor summary').click()
                await work.locator('.answered-editor').get_by_label(question, exact=True).fill('慢轮询中保存的回答。')
                behavior = work.locator('.step-check-2 .behavior-details')
                await behavior.locator('summary').first.click()
                item = behavior.locator('details').filter(has=page.locator('summary', has_text='REQ-0001')).first
                await item.locator('summary').click()
                await item.get_by_label('主要流程').fill('慢轮询中保存的流程。')
                await work.get_by_role('navigation', name='需求工作阶段').get_by_role('button', name='核对现状、价值与改动范围', exact=False).click()
                poll_draft = page.get_by_role('dialog', name='未保存的修改')
                await poll_draft.get_by_role('button', name='保存后离开').click()
                app.state.store.update_record(project['id'], task_id, 'user_task', status='succeeded', message='合成终态')
                await poll_draft.wait_for(state='hidden', timeout=15000)
                saved = app.state.store.get(project['id'])
                assert saved['questions'][0]['answer'] == '慢轮询中保存的回答。'
                assert saved['items'][0]['behavior']['flow'] == '慢轮询中保存的流程。'
                await work.locator('.task-status').wait_for(state='hidden', timeout=10000)
                slow['enabled'] = False
                assert slow['maximum'] <= 1, slow
                results.append({'case': 'slow-poll-and-two-drafts-final-state', 'status': 'VERIFIED', 'maximum_project_reads': slow['maximum']})
                await page.reload()
                await page.get_by_role('button', name='合成工程验证项目').first.click()
                work = page.locator('.project-workspace:not([hidden])')
                await work.get_by_role('navigation', name='需求工作阶段').get_by_role('button', name='澄清流程与规则', exact=False).click()
                await work.locator('.answered-editor summary').click()
                await work.locator('.answered-editor').get_by_label(question, exact=True).fill('我的修改：相接需提示。')
                current = app.state.store.get(project['id'])
                with app.state.store.edit(project['id'], current['revision'], '合成外部同题编辑') as (draft, _):
                    draft['questions'][0]['answer'] = '外部最新回答：相接需拒绝。'
                await work.get_by_role('button', name='保存本次回答').click()
                conflict = work.locator('.questions-panel').get_by_role('alert')
                await conflict.get_by_text('旧值', exact=True).wait_for()
                assert '外部最新回答' in await conflict.inner_text()
                assert '我的修改' in await conflict.inner_text()
                assert app.state.store.get(project['id'])['questions'][0]['answer'] == '外部最新回答：相接需拒绝。'
                await conflict.get_by_role('button', name='已核对，保留我的修改并重新保存').click()
                async with page.expect_response(lambda response: response.request.method == 'POST' and response.url.endswith('/answers') and response.status == 200):
                    await work.get_by_role('button', name='保存本次回答').click()
                assert app.state.store.get(project['id'])['questions'][0]['answer'] == '我的修改：相接需提示。'
                await page.wait_for_function("() => [...document.querySelectorAll('button')].some(b => b.textContent?.includes('保存本次回答') && b.disabled)")
                results.append({'case': 'same-question-conflict-review-and-save', 'status': 'VERIFIED'})
                behavior = work.locator('.step-check-2 .behavior-details')
                await behavior.locator('summary').first.click()
                item = behavior.locator('details').filter(has=page.locator('summary', has_text='REQ-0001')).first
                await item.locator('summary').click()
                await item.get_by_label('主要流程').fill('我的行为修改。')
                current = app.state.store.get(project['id'])
                with app.state.store.edit(project['id'], current['revision'], '合成外部行为编辑') as (draft, _):
                    draft['items'][0]['behavior']['flow'] = '外部最新行为。'
                await item.get_by_role('button', name='保存此需求行为').click()
                behavior_conflict = item.get_by_role('alert')
                await behavior_conflict.get_by_text('旧值', exact=True).wait_for()
                assert '外部最新行为' in await behavior_conflict.inner_text()
                assert '我的行为修改' in await behavior_conflict.inner_text()
                await behavior_conflict.get_by_role('button', name='已核对，保留我的修改并重新保存').click()
                async with page.expect_response(lambda response: response.request.method == 'POST' and response.url.endswith('/behavior') and response.status == 200):
                    await item.get_by_role('button', name='保存此需求行为').click()
                assert app.state.store.get(project['id'])['items'][0]['behavior']['flow'] == '我的行为修改。'
                results.append({'case': 'same-behavior-conflict-review-and-save', 'status': 'VERIFIED'})
                await work.locator('.secondary-tools').filter(has_text='核对条目与业务行为').locator('summary').first.click()
                await work.get_by_role('button', name='编辑原文').first.click()
                original_dialog = page.get_by_role('dialog', name='编辑需求原文')
                await original_dialog.get_by_label('需求原文').fill('我的原文修改。')
                current = app.state.store.get(project['id'])
                with app.state.store.edit(project['id'], current['revision'], '合成外部原文编辑') as (draft, _):
                    draft['items'][0]['statement'] = '外部最新原文。'
                await original_dialog.get_by_role('button', name='保存原文').click()
                await original_dialog.get_by_text('旧值', exact=True).wait_for()
                assert '外部最新原文' in await original_dialog.inner_text()
                assert '我的原文修改' in await original_dialog.inner_text()
                await original_dialog.get_by_role('button', name='已核对，保留我的修改并重新保存').click()
                async with page.expect_response(lambda response: response.request.method == 'POST' and response.url.endswith('/items/REQ-0001') and response.status == 200):
                    await original_dialog.get_by_role('button', name='保存原文').click()
                assert app.state.store.get(project['id'])['items'][0]['statement'] == '我的原文修改。'
                results.append({'case': 'same-item-text-conflict-review-and-save', 'status': 'VERIFIED'})
                await page.get_by_title('模型设置').click()
                settings = page.locator('.settings-surface')
                await settings.get_by_text('高级模型配置、密钥与连接测试').click()
                name = settings.get_by_label('服务方名称')
                await name.fill('合成服务方设置')
                await settings.get_by_label('配置用途').select_option('vision')
                settings_dialog = page.get_by_role('dialog', name='未保存的修改')
                await settings_dialog.wait_for()
                await settings_dialog.get_by_role('button', name='留在当前页').click()
                assert await name.input_value() == '合成服务方设置'
                assert await settings.get_by_label('配置用途').input_value() == 'model'
                await page.route('**/api/models/model', reject_url)
                await settings.get_by_role('button', name='保存模型配置').click()
                await page.get_by_role('alert').filter(has_text='无法连接服务').wait_for()
                assert await name.input_value() == '合成服务方设置'
                await page.unroute('**/api/models/model', reject_url)
                await settings.get_by_label('配置用途').select_option('vision')
                await settings_dialog.get_by_role('button', name='保存后离开').click()
                await settings_dialog.wait_for(state='hidden')
                assert await settings.get_by_label('配置用途').input_value() == 'vision'
                assert app.state.store.setting('model')['name'] == '合成服务方设置'
                assert await page.evaluate("!localStorage.getItem('ra-model-key')")
                results.append({'case': 'model-settings-save-discard-and-failure', 'status': 'VERIFIED'})
                session = await (await page.request.get(base + '/api/session')).json()
                stale = await page.request.patch(base + '/api/projects/' + project['id'], data={'expected_revision': 999, 'name': 'must-not-save'}, headers={'Origin': base, 'X-CSRF-Token': session['csrf']})
                assert stale.status == 409
                results.append({'case': 'stale-revision-rejected', 'status': 'VERIFIED'})
                assert not errors, errors
                assert all(task['calls'] == 0 for task in app.state.store.records(project['id'], 'user_task'))
                return {'results': results, 'model_calls': 0, 'page_errors': errors}
            finally:
                await browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        temporary.cleanup()


if __name__ == '__main__':
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = asyncio.run(main())
        record = {'status': 'VERIFIED', **result}
    except Exception as exc:
        record = {'status': 'FAILED', 'results': RESULTS, 'error': repr(exc), 'traceback': traceback.format_exc()}
        raise
    finally:
        record['time_utc'] = datetime.now(timezone.utc).isoformat()
        output.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
        with output.with_name('five-step-browser-attempts.jsonl').open('a', encoding='utf-8') as log:
            log.write(json.dumps(record, ensure_ascii=False) + '\n')
    print(json.dumps(record, ensure_ascii=False))
