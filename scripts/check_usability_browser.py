"""Exercise request feedback and recovery against the candidate UI and isolated ASGI app.

The script uses only a temporary data directory, a loopback HTTP model stub, and
Playwright's isolated Chromium context. It never uses the daily workbench, user
browser profiles, or external model endpoints.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import uvicorn
from fastapi.testclient import TestClient
from playwright.async_api import async_playwright, expect
from starlette.staticfiles import StaticFiles

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

CASE_NAMES = [
    'project_create_pending_deduplicated_and_not_closable',
    'project_create_network_failure_visible_name_retained_and_retry_recovers',
    'intake_save_pending_deduplicated_and_inputs_locked',
    'http_200_html_is_reported_as_failure_with_intake_retained',
    'confirmation_committed_refresh_failed_is_not_retryable_as_unsaved',
    'model_test_timeout_visible_then_synthetic_retry_recovers_without_project_data',
    'history_get_network_failure_visible_and_retry_recovers',
]


class ModelStub:
    def __init__(self):
        self.requests = []
        self.delay = 0.0
        self.server = None
        self.thread = None

    def __enter__(self):
        state = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                length = int(self.headers.get('Content-Length', '0'))
                state.requests.append(json.loads(self.rfile.read(length)))
                time.sleep(state.delay)
                body = json.dumps({
                    'model': 'SYNTHETIC_LOCAL_MODEL',
                    'choices': [{'message': {'content': '{"connection":"ok"}'}, 'finish_reason': 'stop'}],
                    'usage': {'prompt_tokens': 3, 'completion_tokens': 3, 'total_tokens': 6},
                }).encode('utf-8')
                try:
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}/v1'
        return self

    def __exit__(self, *_args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)


async def run(dist: Path, evidence: Path) -> bool:
    evidence.mkdir(parents=True, exist_ok=True)
    results = {name: {'status': 'NOT_RUN', 'screenshot': None, 'error': None} for name in CASE_NAMES}
    errors = []
    server = thread = sock = browser = page = None
    current_case = None
    original_timeout = None
    original_provider_request = None
    application = None
    original_data = os.environ.get('RA_DATA_DIR')
    temp = tempfile.TemporaryDirectory(prefix='ra-usability-browser-')
    isolated = Path(temp.name)
    os.environ['RA_DATA_DIR'] = str(isolated / 'data')

    try:
        import app.config
        import app.core
        app.config.ROOT = isolated
        assert app.core.ROOT.resolve() == REPO.resolve(), 'app.core.ROOT must remain the repository root'
        import app.main as main
        from app.provider import DEFAULT
        from tests.helpers import prepared
        from tests.product_flow_helpers import check_prepared

        original_timeout = main.MODEL_TEST_TIMEOUT_SECONDS
        application = main.create_app(isolated / 'case', access_token='off', env_path=isolated / 'absent.env')
        original_provider_request = application.state.provider.request
        with ModelStub() as model, TestClient(application) as client:
            login = client.post('/api/session/login', json={'key': 'off'})
            assert login.status_code == 200, login.text
            csrf = login.json()['csrf']
            client.headers.update({'Origin': 'http://testserver', 'X-CSRF-Token': csrf})
            config = dict(DEFAULT, name='本机合成连接测试', base_url=model.url, local_allowed=True,
                          key_env='RA_USABILITY_SYNTHETIC_KEY', model='SYNTHETIC_LOCAL_MODEL')
            for slot in ('model', 'vision'):
                response = client.put('/api/models/' + slot, json=config)
                assert response.status_code == 200, response.text
                response = client.put('/api/models/' + slot + '/key', json={'key': 'synthetic-local-only'})
                assert response.status_code == 200, response.text
            warmup = client.post('/api/models/model/test')
            assert warmup.status_code == 200, warmup.text
            assert len(model.requests) == 1
            main.MODEL_TEST_TIMEOUT_SECONDS = 0.15

            ready = prepared(application.state.store)
            ready_name = '合成已准备确认的项目'
            with application.state.store.edit(ready['id'], ready['revision'], '合成测试命名') as (draft, _):
                draft['name'] = ready_name
            check_prepared(application.state.store, ready['id'])

            application.router.routes[:] = [r for r in application.router.routes if not (
                getattr(r, 'path', None) == '' and getattr(r, 'name', None) == 'web')]
            application.mount('/', StaticFiles(directory=dist, html=True), name='candidate-web')
            sock = socket.socket()
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
            server = uvicorn.Server(uvicorn.Config(application, log_level='error', access_log=False))
            thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
            thread.start()
            for _ in range(100):
                if server.started:
                    break
                await asyncio.sleep(.05)
            assert server.started, 'isolated ASGI test server did not start'

            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=True)
                context = await browser.new_context(viewport={'width': 1440, 'height': 1000})
                page = await context.new_page()
                page_errors = []
                page.on('pageerror', lambda e: page_errors.append(str(e)))
                await page.goto(f'http://127.0.0.1:{port}/')
                await page.get_by_label('本机访问令牌').fill('off')
                await page.get_by_role('button', name='进入工作台').click()
                await expect(page.get_by_role('button', name='＋ 新建需求项目')).to_be_visible()
                await expect(page.get_by_role('button', name=ready_name, exact=True)).to_be_visible()

                async def screenshot(case):
                    path = evidence / f'{case}.png'
                    await page.screenshot(path=str(path), full_page=True)
                    results[current_case]['screenshot'] = str(path)

                # New project is a real API write. Delay only its response after the ASGI write.
                current_case = CASE_NAMES[0]
                create_calls = 0
                async def delayed_create(route):
                    nonlocal create_calls
                    if route.request.method != 'POST':
                        await route.continue_()
                        return
                    create_calls += 1
                    response = await route.fetch()
                    await asyncio.sleep(.8)
                    await route.fulfill(response=response)
                await page.route('**/api/projects', delayed_create)
                await page.get_by_role('button', name='＋ 新建需求项目').click()
                dialog = page.get_by_role('dialog', name='新建需求项目')
                name_input = dialog.get_by_label('新项目名称')
                await name_input.fill('合成延迟创建项目')
                await dialog.get_by_role('button', name='创建项目').click()
                feedback = page.get_by_role('complementary', name='操作反馈')
                await expect(feedback).to_contain_text('正在创建项目')
                await expect(dialog.get_by_role('button', name='正在创建…')).to_be_disabled()
                await expect(dialog.get_by_role('button', name='取消')).to_be_disabled()
                await page.keyboard.press('Escape')
                await expect(dialog).to_be_visible()
                assert create_calls == 1, f'duplicate project writes: {create_calls}'
                await screenshot('project-create-pending')
                await expect(page.get_by_role('button', name='合成延迟创建项目', exact=True)).to_be_visible(timeout=8000)
                await expect(dialog).to_have_count(0, timeout=8000)
                await page.unroute('**/api/projects', delayed_create)
                results[current_case]['status'] = 'PASS'

                # A failed create must keep the entered name and permit a retry.
                current_case = CASE_NAMES[1]
                await page.get_by_role('button', name='＋ 新建需求项目').click()
                dialog = page.get_by_role('dialog', name='新建需求项目')
                name_input = dialog.get_by_label('新项目名称')
                await name_input.fill('合成失败后可恢复项目')
                failed_create = True
                async def fail_once_create(route):
                    nonlocal failed_create
                    if route.request.method != 'POST':
                        await route.continue_()
                    elif failed_create:
                        failed_create = False
                        await route.abort('failed')
                    else:
                        await route.continue_()
                await page.route('**/api/projects', fail_once_create)
                await dialog.get_by_role('button', name='创建项目').click()
                await expect(feedback.get_by_role('alert')).to_be_visible(timeout=8000)
                await expect(name_input).to_have_value('合成失败后可恢复项目')
                await screenshot('project-create-error')
                await page.get_by_role('button', name='关闭提示').click()
                await dialog.get_by_role('button', name='创建项目').click()
                await expect(page.get_by_role('button', name='合成失败后可恢复项目', exact=True)).to_be_visible(timeout=8000)
                await expect(dialog).to_have_count(0, timeout=8000)
                await page.unroute('**/api/projects', fail_once_create)
                assert client.get('/api/projects').status_code == 200
                results[current_case]['status'] = 'PASS'

                if await feedback.get_by_role('button', name='关闭提示').count():
                    await feedback.get_by_role('button', name='关闭提示').click()
                project_button = page.get_by_role('button', name='合成延迟创建项目', exact=True)
                await project_button.click()
                workspace = page.locator('.project-workspace:not([hidden])')
                await expect(workspace.get_by_label('核心诉求')).to_be_visible()

                # Delay the response after the real intake mutation, and check field and write locks.
                current_case = CASE_NAMES[2]
                # Derive the project path from the UI-created record rather than a guessed id.
                projects = client.get('/api/projects').json()
                delayed_project = next(p for p in projects if p['name'] == '合成延迟创建项目')
                intake_root = '/projects/' + delayed_project['id']
                intake_calls = 0
                async def delayed_intake(route):
                    nonlocal intake_calls
                    intake_calls += 1
                    response = await route.fetch()
                    await asyncio.sleep(.8)
                    await route.fulfill(response=response)
                await page.route(f'**/api{intake_root}/intake', delayed_intake)
                checkpoint = workspace.locator('.checkpoint.step-check-0:visible')
                textarea = checkpoint.locator('textarea[aria-label="核心诉求"]:visible')
                await textarea.fill('合成输入：延迟期间应保留原文。')
                save_button = checkpoint.get_by_role('button', name='保存草稿')
                await save_button.click()
                await expect(feedback).to_contain_text('正在提交当前操作')
                await expect(save_button).to_be_disabled()
                await expect(textarea).to_be_disabled()
                await page.keyboard.press('Escape')
                await expect(textarea).to_have_value('合成输入：延迟期间应保留原文。')
                assert intake_calls == 1, f'duplicate intake writes: {intake_calls}; pending feedback={await feedback.inner_text()}; button disabled={await save_button.is_disabled()}'
                await screenshot('intake-save-pending')
                await expect(checkpoint.get_by_text('输入已保存', exact=True)).to_be_visible(timeout=8000)
                await page.unroute(f'**/api{intake_root}/intake', delayed_intake)
                results[current_case]['status'] = 'PASS'

                # HTTP 200 with HTML is not a successful mutation; local text stays intact.
                current_case = CASE_NAMES[3]
                await textarea.fill('合成输入：HTTP 200 HTML 不可当作成功。')
                async def html_intake(route):
                    await route.fulfill(status=200, content_type='text/html', body='<html>proxy error</html>')
                await page.route(f'**/api{intake_root}/intake', html_intake)
                await checkpoint.get_by_role('button', name='保存草稿').click()
                await expect(checkpoint.get_by_role('alert').filter(has_text='未取得有效处理结果')).to_be_visible(timeout=8000)
                await expect(textarea).to_have_value('合成输入：HTTP 200 HTML 不可当作成功。')
                await screenshot('intake-html-200-error')
                await page.unroute(f'**/api{intake_root}/intake', html_intake)
                results[current_case]['status'] = 'PASS'

                # Resolve the actual unsaved-change guard after the deliberately failed HTML response.
                # The fixture intentionally discards that synthetic draft before opening another project.
                ready_button = page.get_by_role('button', name=ready_name, exact=True)
                await ready_button.click()
                unsaved_dialog = page.get_by_role('dialog', name='未保存的修改')
                await expect(unsaved_dialog).to_be_visible(timeout=8000)
                await unsaved_dialog.get_by_role('button', name='放弃修改').click()
                await expect(page.get_by_role('dialog', name='未保存的修改')).to_have_count(0, timeout=8000)
                # The confirmation POST is real and committed; fail only its following project refresh.
                current_case = CASE_NAMES[4]
                await page.get_by_role('button', name=ready_name, exact=True).click()
                await page.locator('.project-workspace:not([hidden])').get_by_role('button', name='确认与交接', exact=False).click()
                await expect(page.get_by_role('heading', name='核对指定版本', exact=True)).to_be_visible()
                confirmation_root = '/projects/' + ready['id']
                post_count = 0
                failed_refresh = False
                async def confirmation_route(route):
                    nonlocal post_count, failed_refresh
                    if route.request.method == 'POST' and route.request.url.endswith('/confirmations'):
                        post_count += 1
                        await route.continue_()
                    elif route.request.method == 'GET' and route.request.url.endswith(confirmation_root):
                        if post_count and not failed_refresh:
                            failed_refresh = True
                            await route.abort('failed')
                        else:
                            await route.continue_()
                    else:
                        await route.continue_()
                await page.route('**/api/**', confirmation_route)
                await page.get_by_role('button', name='确认此版本的需求与文档').click()
                confirm_dialog = page.get_by_role('dialog', name='确认内容复核')
                await confirm_dialog.get_by_role('button', name='提交服务端确认').click()
                await expect(confirm_dialog).to_have_count(0, timeout=12000)
                await expect(page.get_by_role('alert').filter(has_text='确认已提交，但页面刷新失败')).to_be_visible(timeout=8000)
                assert post_count == 1
                assert len(application.state.store.records(ready['id'], 'confirmation')) == 1
                await screenshot('confirmation-saved-refresh-failed')
                await page.get_by_role('button', name='刷新内容并保留输入').click()
                await expect(page.get_by_text('当前确认基线', exact=False)).to_be_visible(timeout=10000)
                assert post_count == 1, 'refresh recovery must not replay confirmation'
                await page.unroute('**/api/**', confirmation_route)
                results[current_case]['status'] = 'PASS'

                # A local provider delay causes the patched short backend test timeout, then recovers.
                current_case = CASE_NAMES[5]
                await page.get_by_role('button', name='模型设置').click()
                await page.locator('details').filter(has_text='高级模型配置、密钥与连接测试').locator('summary').click()
                # Use a deterministic fake provider delay so the 150ms backend timeout does not depend on local socket scheduling.
                async def slow_fake_provider(config, messages, evidence=None):
                    model.requests.append({'messages': messages, 'synthetic_timeout_fixture': True})
                    await asyncio.sleep(.5)
                    return {'connection': 'ok'}, {'request_model': config['model'], 'response_model': config['model'], 'usage': {}, 'finish_reason': 'stop', 'origin': model.url}
                application.state.provider.request = slow_fake_provider
                await page.get_by_role('button', name='测试连接（调用模型）').click()
                await expect(page.get_by_role('complementary', name='操作反馈').get_by_role('alert')).to_contain_text('连接测试等待超时', timeout=8000)
                await screenshot('model-test-timeout')
                assert len(model.requests) == 2, f'expected setup warmup and one timed-out synthetic request, got {len(model.requests)}'
                assert all('项目' not in json.dumps(message, ensure_ascii=False) for request in model.requests for message in request['messages']), model.requests
                recovery_requests = []
                async def successful_fake_provider(config, messages, evidence=None):
                    recovery_requests.append(messages)
                    return {'connection': 'ok'}, {'request_model': config['model'], 'response_model': config['model'], 'usage': {}, 'finish_reason': 'stop', 'origin': model.url}
                application.state.provider.request = successful_fake_provider
                await page.get_by_role('button', name='关闭提示').click()
                await page.get_by_role('button', name='测试连接（调用模型）').click()
                await expect(page.get_by_role('status').filter(has_text='接口连接成功')).to_be_visible(timeout=8000)
                assert len(recovery_requests) == 1, f'expected one synthetic recovery provider request, got {len(recovery_requests)}'
                assert all('项目' not in json.dumps(message, ensure_ascii=False) for messages in recovery_requests for message in messages), recovery_requests
                results[current_case]['status'] = 'PASS'

                # A real GET transport failure is visible; the next read succeeds without a reload.
                current_case = CASE_NAMES[6]
                await page.get_by_role('button', name='返回当前任务').click()
                await page.get_by_role('button', name=ready_name, exact=True).click()
                history_calls = 0
                async def history_outage_once(route):
                    nonlocal history_calls
                    history_calls += 1
                    if history_calls == 1:
                        await route.abort('failed')
                    else:
                        await route.continue_()
                await page.route(f'**/api{confirmation_root}/history', history_outage_once)
                await page.get_by_role('button', name='历史版本', exact=True).click()
                await expect(page.get_by_role('complementary', name='操作反馈').get_by_role('alert')).to_be_visible(timeout=8000)
                await screenshot('history-network-failure')
                await page.get_by_role('button', name='关闭提示').click()
                await page.get_by_role('button', name='历史版本', exact=True).click()
                await expect(page.get_by_role('dialog', name='历史版本')).to_be_visible(timeout=8000)
                assert history_calls == 2
                await page.screenshot(path=str(evidence / 'history-network-recovered.png'), full_page=True)
                await page.unroute(f'**/api{confirmation_root}/history', history_outage_once)
                results[current_case]['status'] = 'PASS'

                assert not page_errors, page_errors
                await browser.close()
                browser = None
    except Exception as e:
        message = f'{type(e).__name__}: {e}'
        errors.append({'case': current_case, 'error': message})
        if current_case:
            results[current_case]['status'] = 'FAIL'
            results[current_case]['error'] = message
            if page:
                try:
                    path = evidence / f'{current_case}-failure.png'
                    await page.screenshot(path=str(path), full_page=True)
                    results[current_case]['screenshot'] = str(path)
                except Exception:
                    pass
    finally:
        if page:
            try:
                await page.unroute_all(behavior='ignoreErrors')
            except Exception:
                pass
        if browser:
            await browser.close()
        if server:
            server.should_exit = True
        if thread:
            await asyncio.to_thread(thread.join, 5)
        if sock:
            sock.close()
        if original_timeout is not None:
            try:
                import app.main as main
                main.MODEL_TEST_TIMEOUT_SECONDS = original_timeout
                if original_provider_request is not None and application is not None:
                    application.state.provider.request = original_provider_request
            except Exception:
                pass
        if original_data is None:
            os.environ.pop('RA_DATA_DIR', None)
        else:
            os.environ['RA_DATA_DIR'] = original_data
        temp.cleanup()
        report = {
            'mode': 'candidate dist + isolated loopback ASGI app + synthetic local model; no daily data or external calls',
            'status': 'PASS' if all(x['status'] == 'PASS' for x in results.values()) and not errors else 'FAIL',
            'cases': results,
            'errors': errors,
            'isolated_data_root': str(isolated / 'data'),
            'page_errors': page_errors if 'page_errors' in locals() else [],
        }
        (evidence / 'usability-browser-checks.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'status': 'PASS' if all(x['status'] == 'PASS' for x in results.values()) and not errors else 'FAIL',
                      'cases': results, 'errors': errors}, ensure_ascii=True))
    return all(x['status'] == 'PASS' for x in results.values()) and not errors


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--dist', type=Path, required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    args = parser.parse_args()
    ok = asyncio.run(run(args.dist.resolve(), args.evidence.resolve()))
    raise SystemExit(0 if ok else 1)
