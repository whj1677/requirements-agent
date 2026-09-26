"""Isolated browser/HTTP check for manual acceptance and in-flight source text."""
import argparse
import asyncio
import json
import os
import socket
import sys
import tempfile
import threading
import traceback
from datetime import datetime, timezone
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--web-dist', type=Path, required=True)
parser.add_argument('--output', type=Path, default=Path('evidence/release-20260925/manual-acceptance-browser.json'))
args = parser.parse_args()


async def run(folder: Path):
    os.environ['RA_DATA_DIR'] = str(folder / 'module-data')
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import uvicorn
    from playwright.async_api import async_playwright
    from starlette.staticfiles import StaticFiles
    from app.main import create_app
    from app.product_flow import BEHAVIOR, INTAKE, SCOPE, checkpoint, status
    from tests.helpers import prepared

    dist = args.web_dist.resolve()
    assert (dist / 'index.html').is_file(), dist
    app = create_app(folder / 'data', access_token='synthetic-browser-token', env_path=folder / 'absent.env')
    app.router.routes[:] = [r for r in app.router.routes if getattr(r, 'name', None) != 'web']
    app.mount('/', StaticFiles(directory=dist, html=True), name='isolated-web')
    project = prepared(app.state.store)
    with app.state.store.edit(project['id'], project['revision'], '合成缺验收条件') as (draft, _):
        draft['items'] = [item for item in draft['items'] if item['kind'] != 'acceptance']
        draft['items'][0]['related_refs'] = ['RULE-0001']
        draft['items'][0]['behavior'] = {key: '已明确：' + label for key, label in BEHAVIOR.items()}
        draft['product_context'] = {key: '合成核对：' + label for key, label in {**INTAKE, **SCOPE}.items()}
        draft['product_context']['scope_ids'] = ['REQ-0001']
        checkpoint(draft, 1, status(draft)[0]['content_hash'])
        checkpoint(draft, 2, status(draft)[1]['content_hash'])
    project = app.state.store.get(project['id'])
    assert any('缺少已采纳的关联验收条件' in issue for issue in status(project)[2]['missing'])

    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='error', access_log=False))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    result = {'status': 'FAILED', 'cases': [], 'model_calls': 0, 'http_writes': [], 'page_errors': [],
              'time_utc': datetime.now(timezone.utc).isoformat()}
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
                page.on('pageerror', lambda error: result['page_errors'].append(str(error)))
                page.on('response', lambda response: result['http_writes'].append({
                    'path': response.url.split('/api/')[-1], 'status': response.status})
                    if response.request.method == 'POST' and '/api/projects/' in response.url else None)
                await page.goto(base)
                await page.get_by_label('本机访问令牌').fill('synthetic-browser-token')
                await page.get_by_role('button', name='进入工作台').click()
                await page.get_by_role('button', name='合成工程验证项目').first.click()
                work = page.locator('.project-workspace:not([hidden])')
                await work.get_by_role('navigation', name='需求工作阶段').get_by_role('button', name='澄清流程与规则', exact=False).click()
                assert await work.locator('.step-check-2').get_by_text('缺少已采纳的关联验收条件', exact=False).count() == 1
                await work.get_by_role('button', name='补充关联验收条件').click()
                modal = page.get_by_role('dialog', name='补充关联验收条件')
                await modal.get_by_label('关联的本期需求').select_option('REQ-0001')
                await modal.get_by_label('验收名称').fill('边界验收')
                statement = '管理员输入重叠时段并保存，应看到拒绝提示，原数据不变。'
                await modal.get_by_label('验收原文').fill(statement)
                async with page.expect_response(lambda response: response.request.method == 'POST' and response.url.endswith('/items/REQ-0001/acceptance') and response.status == 200):
                    await modal.get_by_role('button', name='保存验收候选').click()
                await modal.wait_for(state='hidden')
                saved = app.state.store.get(project['id'])
                candidate = next(i for i in saved['items'] if i['kind'] == 'acceptance')
                assert candidate['selection_status'] == 'candidate' and candidate['related_refs'] == ['REQ-0001']
                assert candidate['statement'] == statement and candidate['source_refs']
                assert any('缺少已采纳的关联验收条件' in issue for issue in status(saved)[2]['missing'])
                result['cases'].append({'case': 'manual-acceptance-created-as-source-backed-candidate', 'status': 'VERIFIED', 'item_id': candidate['id']})

                await work.locator('.secondary-tools').filter(has_text='核对条目与业务行为').locator('summary').first.click()
                summary = work.locator('.secondary-tools').filter(has_text='核对条目与业务行为')
                row = summary.locator('.summary-item').filter(has_text='边界验收').first
                async with page.expect_response(lambda response: response.request.method == 'POST' and response.url.endswith('/items/' + candidate['id']) and response.status == 200):
                    await row.get_by_role('button', name='采纳', exact=True).click()
                await page.wait_for_function("() => !document.querySelector('.step-check-2')?.textContent?.includes('缺少已采纳的关联验收条件')")
                chosen = app.state.store.get(project['id'])
                assert not any('缺少已采纳的关联验收条件' in issue for issue in status(chosen)[2]['missing'])
                result['cases'].append({'case': 'explicit-adoption-clears-rule-gate', 'status': 'VERIFIED'})

                await work.get_by_role('button', name='项目资料', exact=False).first.click()
                drawer = page.get_by_role('dialog', name='项目资料')
                text = drawer.get_by_label('粘贴材料')
                old = '提交时的原文字资料。'
                newer = '提交期间新写的文字资料，不应被清空。'
                await text.fill(old)
                async def delay_text(route):
                    await asyncio.sleep(1.0)
                    await route.continue_()
                await page.route('**/api/projects/*/sources/text', delay_text)
                prior_count = len(app.state.store.get(project['id'])['sources'])
                await drawer.get_by_role('button', name='保存文字材料').click()
                await text.fill(newer)
                await page.wait_for_timeout(1500)
                assert await text.input_value() == newer
                assert len(app.state.store.get(project['id'])['sources']) == prior_count + 1
                assert any(old in ex['text'] for ex in app.state.store.get(project['id'])['sources'][-1]['excerpts'])
                await page.unroute('**/api/projects/*/sources/text', delay_text)
                result['cases'].append({'case': 'source-text-new-input-survives-slow-save', 'status': 'VERIFIED'})
                async def reject_text(route):
                    await route.abort()
                await page.route('**/api/projects/*/sources/text', reject_text)
                await drawer.get_by_role('button', name='保存文字材料').click()
                await drawer.get_by_role('alert').wait_for()
                assert await text.input_value() == newer
                await page.unroute('**/api/projects/*/sources/text', reject_text)
                result['cases'].append({'case': 'source-text-failure-keeps-input', 'status': 'VERIFIED'})
                assert not result['page_errors'], result['page_errors']
                result['status'] = 'VERIFIED'
            finally:
                await browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=8)
        assert not thread.is_alive(), '临时浏览器服务未停止'
    return result


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='ra-manual-acceptance-') as temporary:
        try:
            payload = asyncio.run(run(Path(temporary)))
        except Exception:
            payload = {'status': 'FAILED', 'error': traceback.format_exc(), 'model_calls': 0,
                       'time_utc': datetime.now(timezone.utc).isoformat()}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        sys.exit(0 if payload['status'] == 'VERIFIED' else 1)
