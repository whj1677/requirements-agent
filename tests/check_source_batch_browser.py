"""Synthetic batch source upload through actual UI and isolated ASGI; no listener.

The simulated lost response occurs *after* the isolated backend commits, so the
client must not retry the unknown item. All files are synthetic and local.
"""
import argparse
import asyncio
import base64
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
from tests.test_local_document_sources import HTML, pdf_bytes


async def run(args):
    build, output = args.static_build.resolve(), args.output.resolve()
    assert not output.exists() and output.is_relative_to(ROOT / 'evidence')
    assert (build / 'index.html').is_file()
    output.mkdir(parents=True)
    report = dict(status='running', checks=[], page_errors=[], routing_errors=[], requests=[],
                  model_calls=0, daily_data_access=False, listener_started=False)
    with tempfile.TemporaryDirectory(prefix='ra-source-batch-') as scratch:
        scratch = Path(scratch)
        os.environ['RA_DATA_DIR'] = str(scratch / 'import-data')
        from app.main import create_app
        from tests.check_refine_workspace_browser import NoModelProvider
        from playwright.async_api import async_playwright, expect
        import httpx
        provider = NoModelProvider(scratch)
        app = create_app(scratch / 'data', provider=provider, env_path=scratch / 'absent.env')
        store = app.state.store
        project = store.create('合成储能云：批量资料测试')
        other = store.create('合成储能云：批次隔离项目')
        first_started, release_first = asyncio.Event(), asyncio.Event()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1:8765') as client:
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=True)
                context = await browser.new_context(viewport=dict(width=1280, height=720))

                async def dispatch(route):
                    request, url = route.request, urlsplit(route.request.url)
                    try:
                        assert url.netloc == '127.0.0.1:8765', 'external requests forbidden'
                        if url.path.startswith('/api/'):
                            assert request.method == 'GET' or request.method == 'POST' and url.path.endswith('/sources/file'), 'unexpected write ' + url.path
                            headers = {k:v for k,v in request.headers.items() if k.lower() not in ('host','connection','content-length','accept-encoding')}
                            body = request.post_data_buffer or b''
                            name = re.search(rb'filename="([^"]+)"', body)
                            rev = re.search(rb'name="expected_revision"\r\n\r\n(\d+)', body)
                            filename = name.group(1).decode() if name else None
                            if filename == 'read.txt':
                                first_started.set()
                                await asyncio.wait_for(release_first.wait(), 15)
                            response = await client.request(request.method, url.path + ('?' + url.query if url.query else ''), headers=headers, content=body)
                            report['requests'].append(dict(method=request.method, path=url.path, status=response.status_code,
                                filename=filename, expected_revision=int(rev.group(1)) if rev else None))
                            if filename == 'unknown.txt':
                                assert response.status_code == 200, response.text
                                await route.abort('failed')
                            else:
                                await route.fulfill(status=response.status_code, body=response.content,
                                    content_type=response.headers.get('content-type','application/json'))
                        else:
                            path = (build / (url.path.lstrip('/') or 'index.html')).resolve()
                            assert path.is_relative_to(build) and path.is_file(), str(path)
                            await route.fulfill(status=200, body=path.read_bytes(), content_type=mimetypes.guess_type(path.name)[0] or 'application/octet-stream')
                    except Exception as error:
                        report['routing_errors'].append(str(error))
                        await route.abort()

                await context.route('**/*', dispatch)
                page = await context.new_page()
                # Playwright route interception withholds native upload events until
                # fulfillment. Expose only the synthetic first XHR so the test can
                # separately exercise the post-upload waiting UI while ASGI is held.
                await page.add_init_script('''(() => {
                  const send = XMLHttpRequest.prototype.send;
                  XMLHttpRequest.prototype.send = function(body) {
                    if (body instanceof FormData && body.get('file')?.name === 'read.txt') window.__syntheticSourceXHR = this;
                    return send.call(this, body);
                  };
                })();''')
                page.set_default_timeout(15000)
                page.on('pageerror', lambda error: report['page_errors'].append(str(error)))
                try:
                    await page.goto('http://127.0.0.1:8765/', wait_until='networkidle')
                    from tests.browser_navigation import select_project
                    await select_project(page, project['id'])
                    chat = page.get_by_role('textbox', name='你的想法', exact=True)
                    await chat.fill('合成未发送聊天草稿，应当保留。')
                    await page.get_by_label('选择本轮聊天附件', exact=True).set_input_files(
                        dict(name='chat-draft.txt', mimeType='text/plain', buffer=b'Unsent synthetic attachment.'))
                    grants_before = store.get(project['id']).get('grants')
                    await page.get_by_role('button', name='查看项目资料', exact=True).click()
                    dialog = page.get_by_role('dialog', name='项目资料')
                    await dialog.locator('details.source-add > summary').click()
                    picker = dialog.locator('.source-import__picker input[type=file]')
                    assert await picker.get_attribute('multiple') is not None
                    source_posts = lambda: [r for r in report['requests'] if r['method']=='POST' and r['path'].endswith('/sources/file')]
                    await picker.set_input_files([
                        dict(name='empty.txt', mimeType='text/plain', buffer=b''),
                        dict(name='large.txt', mimeType='text/plain', buffer=b'x'*(12*1024*1024+1)),
                        dict(name='unsupported.bin', mimeType='application/octet-stream', buffer=b'synthetic'),
                    ])
                    await expect(dialog.locator('.source-import__result').filter(has_text='large.txt')).to_contain_text('未上传')
                    assert not source_posts()
                    assert not store.get(project['id'])['sources']
                    report['checks'].append('空文件、超限和不支持格式本地预检拒绝，无上传请求')

                    await picker.set_input_files([
                        dict(name='read.txt', mimeType='text/plain', buffer=b'Synthetic requirement.'),
                        dict(name='partial.html', mimeType='text/html', buffer=HTML.encode('utf-8')),
                        dict(name='failed.pdf', mimeType='application/pdf', buffer=pdf_bytes((None,))),
                    ])
                    await asyncio.wait_for(first_started.wait(), 15)
                    row = dialog.locator('.source-import__result').filter(has_text='read.txt')
                    await expect(row).to_contain_text('正在上传')
                    await expect(row.get_by_role('progressbar')).to_be_visible()
                    await page.evaluate("window.__syntheticSourceXHR.upload.dispatchEvent(new ProgressEvent('load'))")
                    await expect(row).to_contain_text('服务器正在读取')
                    await expect(row).to_contain_text('已等待 1 秒', timeout=4000)
                    await expect(dialog.get_by_role('button', name='关闭项目资料')).to_be_disabled()
                    release_first.set()
                    await expect(dialog.locator('.source-import__result').filter(has_text='failed.pdf')).to_contain_text('读取失败', timeout=60000)
                    await expect(dialog.locator('.source-import__result').filter(has_text='read.txt')).to_contain_text('已读取')
                    await expect(dialog.locator('.source-import__result').filter(has_text='partial.html')).to_contain_text('部分读取')
                    requests = source_posts()
                    assert [r['filename'] for r in requests] == ['read.txt','partial.html','failed.pdf']
                    assert [r['expected_revision'] for r in requests] == [0,1,2]
                    sources = store.get(project['id'])['sources']
                    assert [s['parse_status'] for s in sources] == ['read','partial','failed']
                    report['checks'].append('真实文件串行上传，版本 0/1/2；拦截延迟显示上传进度，模拟 upload 完成后显示读取等待和计时；分别显示已读/部分读取/失败')

                    await expect(picker).to_be_enabled()
                    await picker.set_input_files([
                        dict(name='success.txt',mimeType='text/plain',buffer=b'Synthetic prior success.'),
                        dict(name='unknown.txt',mimeType='text/plain',buffer=b'Synthetic committed response lost.'),
                        dict(name='remaining.txt',mimeType='text/plain',buffer=b'Synthetic last file.'),
                    ])
                    unknown = dialog.locator('.source-import__result').filter(has_text='unknown.txt')
                    remaining = dialog.locator('.source-import__result').filter(has_text='remaining.txt')
                    await expect(unknown).to_contain_text('导入状态待核对', timeout=60000)
                    await expect(remaining).to_contain_text('尚未上传')
                    assert [r['filename'] for r in source_posts()][-2:] == ['success.txt','unknown.txt']
                    await dialog.get_by_role('button',name='关闭项目资料').click()
                    await expect(chat).to_have_value('合成未发送聊天草稿，应当保留。')
                    await expect(page.get_by_role('button', name='移除本轮附件 chat-draft.txt', exact=True)).to_be_visible()
                    assert store.get(project['id']).get('grants') == grants_before
                    await page.get_by_role('button', name='移除本轮附件 chat-draft.txt', exact=True).click()
                    compact = page.locator('details.compact-projects')
                    await compact.locator('summary').click()
                    await page.get_by_label('切换项目', exact=True).select_option(other['id'])
                    leave = page.get_by_role('dialog', name='未保存的修改')
                    await expect(leave).to_be_visible()
                    await leave.get_by_role('button', name='保存后离开').click()
                    await expect(leave).to_be_hidden()
                    if await compact.get_attribute('open') is not None:
                        await compact.locator('summary').click()
                    other_workspace = page.locator('.project-workspace:not([hidden])')
                    await expect(other_workspace.get_by_role('heading', name=other['name'], exact=True)).to_be_visible()
                    await other_workspace.get_by_role('button', name='项目资料 · 0', exact=True).click()
                    other_dialog = page.get_by_role('dialog', name='项目资料')
                    await other_dialog.locator('details.source-add > summary').click()
                    assert await other_dialog.locator('.source-import__result').count() == 0
                    assert len(source_posts()) == 5, 'switching projects must not upload any queued file'
                    await other_dialog.get_by_role('button',name='关闭项目资料').click()
                    await select_project(page, project['id'])
                    await expect(chat).to_have_value('合成未发送聊天草稿，应当保留。')
                    await page.get_by_role('button', name='查看项目资料', exact=True).click()
                    dialog = page.get_by_role('dialog', name='项目资料')
                    await dialog.locator('details.source-add > summary').click()
                    await expect(dialog.get_by_text('关闭资料窗口不会清除这些临时记录；刷新页面会清空记录，未提交文件需重新选择，结果未知的文件请先在材料列表核对。')).to_be_visible()
                    unknown = dialog.locator('.source-import__result').filter(has_text='unknown.txt')
                    remaining = dialog.locator('.source-import__result').filter(has_text='remaining.txt')
                    await expect(unknown).to_contain_text('导入状态待核对')
                    await expect(remaining).to_contain_text('尚未上传')
                    await dialog.get_by_role('button', name='继续上传明确未提交的 1 份文件').click()
                    await expect(remaining).to_contain_text('已读取', timeout=60000)
                    for _ in range(100):
                        if len(source_posts()) == 6: break
                        await asyncio.sleep(.05)
                    requests = source_posts()
                    assert [r['filename'] for r in requests] == ['read.txt','partial.html','failed.pdf','success.txt','unknown.txt','remaining.txt'], requests
                    assert [r['expected_revision'] for r in requests] == list(range(6))
                    events = report['requests']
                    positions = [i for i,event in enumerate(events) if event in requests]
                    for previous, current in zip(positions, positions[1:]):
                        assert any(event['method']=='GET' and event['path']==f'/api/projects/{project["id"]}' and event['status']==200
                                   for event in events[previous+1:current]), 'each subsequent file needs a fresh project read'
                    current = store.get(project['id'])
                    assert [s['title'] for s in current['sources']] == [r['filename'] for r in requests]
                    assert len({s['sha256'] for s in current['sources']}) == 6
                    await expect(unknown).to_contain_text('导入状态待核对')
                    report['checks'].append('第二项已提交但响应丢失时暂停；仅继续第三项，不重传成功项或未知项')

                    await dialog.get_by_role('button',name='关闭项目资料').click()
                    await expect(chat).to_have_value('合成未发送聊天草稿，应当保留。')
                    assert store.get(project['id']).get('grants') == grants_before
                    await page.get_by_role('button', name='查看项目资料', exact=True).click()
                    dialog = page.get_by_role('dialog', name='项目资料')
                    await dialog.locator('details.source-add > summary').click()
                    await expect(dialog.locator('.source-import__result').filter(has_text='unknown.txt')).to_contain_text('导入状态待核对')
                    await expect(dialog.locator('.source-import__result').filter(has_text='remaining.txt')).to_contain_text('已读取')
                    await page.reload(wait_until='networkidle')
                    await select_project(page, project['id'])
                    await page.get_by_role('button', name='查看项目资料', exact=True).click()
                    dialog = page.get_by_role('dialog', name='项目资料')
                    await dialog.locator('details.source-add > summary').click()
                    assert await dialog.locator('.source-import__result').count() == 0
                    assert len(store.get(project['id'])['sources']) == 6 and len(source_posts()) == 6
                    report['checks'].append('项目隔离且切换不自动续传；关闭重开保留临时记录，刷新清除临时记录但不影响已保存资料')
                    assert await dialog.get_by_role('combobox',name='材料用途').input_value() == 'goal'
                    async with page.expect_file_chooser() as chooser:
                        await dialog.get_by_role('button', name='导入墨刀原型截图').click()
                    await (await chooser.value).set_files(dict(name='reference.png',mimeType='image/png',buffer=base64.b64decode(
                        'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=')))
                    for _ in range(150):
                        current = store.get(project['id'])
                        if len(current['sources']) == 7: break
                        await asyncio.sleep(.1)
                    assert len(current['sources']) == 7 and current['sources'][-1]['purpose'] == 'reference'
                    report['checks'].append('通过墨刀截图入口导入时，截图实际归入设计参考')
                    assert provider.calls == 0 and not report['routing_errors'] and not report['page_errors']
                    report.update(status='passed')
                except Exception as error:
                    report.update(status='failed',failure=str(error),traceback=traceback.format_exc())
                    await page.screenshot(path=str(output/'failure.png'))
                finally:
                    release_first.set()
                    report['model_calls'] = provider.calls
                    (output/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),'utf-8')
                    await context.close()
                    await browser.close()
    print(json.dumps({k:report.get(k) for k in ('status','checks','failure','model_calls')},ensure_ascii=False))
    if report['status'] != 'passed': raise SystemExit(1)


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--static-build',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    asyncio.run(run(parser.parse_args()))
