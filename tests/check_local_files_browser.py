"""Select real PDF/PPTX/HTML in chat and project materials over isolated ASGI."""
import argparse
import asyncio
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import sys
import tempfile
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tests.test_local_document_sources import HTML,pdf_bytes
from tests.test_office_sources import fixtures


async def run(args):
    build,output=args.static_build.resolve(),args.output.resolve()
    assert not output.exists() and output.is_relative_to(ROOT/'evidence')
    output.mkdir(parents=True)
    report=dict(status='running',page_errors=[],routing_errors=[],requests=[],model_calls=0,daily_data_access=False,listener_started=False)
    with tempfile.TemporaryDirectory(prefix='ra-local-files-') as scratch:
        scratch=Path(scratch)
        os.environ['RA_DATA_DIR']=str(scratch/'import-data')
        from app.main import create_app
        from tests.check_refine_workspace_browser import NoModelProvider
        from playwright.async_api import async_playwright,expect
        import httpx
        provider=NoModelProvider(scratch)
        app=create_app(scratch/'data',provider=provider,env_path=scratch/'absent.env')
        store=app.state.store
        project=store.create('合成储能云：文件入口核对')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1:8765') as client:
            async with async_playwright() as pw:
                browser=await pw.chromium.launch(headless=True)
                context=await browser.new_context(viewport=dict(width=1280,height=720))
                async def dispatch(route):
                    request=route.request;url=urlsplit(request.url)
                    try:
                        assert url.netloc=='127.0.0.1:8765','External request forbidden'
                        if url.path.startswith('/api/'):
                            assert request.method=='GET' or request.method=='POST' and url.path.endswith('/sources/file'),'Unexpected write '+url.path
                            headers={k:v for k,v in request.headers.items() if k.lower() not in ('host','connection','content-length','accept-encoding')}
                            response=await client.request(request.method,url.path,headers=headers,content=request.post_data_buffer)
                            report['requests'].append(dict(method=request.method,path=url.path,status=response.status_code))
                            await route.fulfill(status=response.status_code,body=response.content,content_type=response.headers.get('content-type','application/json'))
                        else:
                            path=(build/(url.path.lstrip('/') or 'index.html')).resolve()
                            assert path.is_relative_to(build) and path.is_file()
                            await route.fulfill(status=200,body=path.read_bytes(),content_type=mimetypes.guess_type(path.name)[0] or 'application/octet-stream')
                    except Exception as error:
                        report['routing_errors'].append(str(error));await route.abort()
                await context.route('**/*',dispatch)
                page=await context.new_page();page.set_default_timeout(12000)
                page.on('pageerror',lambda error:report['page_errors'].append(str(error)))
                try:
                    await page.goto('http://127.0.0.1:8765/',wait_until='networkidle')
                    await page.locator('aside.sidebar button[title="'+project['name']+'"]').click()
                    picker=page.get_by_label('选择本轮聊天附件',exact=True)
                    for extension in ('.pdf','.ppt','.pptx','.html','.htm'):
                        assert extension in await picker.get_attribute('accept')
                    payloads=[dict(name='合成核对.pdf',mimeType='application/pdf',buffer=pdf_bytes()),
                        dict(name='合成核对.pptx',mimeType='application/vnd.openxmlformats-officedocument.presentationml.presentation',buffer=fixtures()['.pptx']),
                        dict(name='合成核对.html',mimeType='text/html',buffer=HTML.encode())]
                    await picker.set_input_files(payloads)
                    for expected in payloads:
                        await expect(page.get_by_role('button',name='移除本轮附件 '+expected['name'],exact=True)).to_be_visible()
                    assert not store.get(project['id'])['sources'],'Unsent chat attachments must stay local'
                    await page.screenshot(path=str(output/'01-chat-three-document-types.png'))
                    await page.get_by_role('button',name='查看项目资料',exact=True).click()
                    await page.locator('details.source-add > summary').click()
                    await page.get_by_text('文件、截图、文字或网页',exact=True).click()
                    material_picker=page.locator('.source-import__picker input')
                    assert '.html' in await material_picker.get_attribute('accept')
                    material_payloads=payloads+[dict(name='合成补充.htm',mimeType='text/html',buffer='<h1>本期只读查看告警</h1>'.encode())]
                    for index,expected in enumerate(material_payloads,1):
                        await expect(material_picker).to_be_enabled()
                        await material_picker.set_input_files(expected)
                        for _ in range(120):
                            p=store.get(project['id'])
                            if len(p['sources'])==index:break
                            await asyncio.sleep(.1)
                        assert len(p['sources'])==index,p
                        source=p['sources'][-1]
                        assert source['sha256']==hashlib.sha256(expected['buffer']).hexdigest()
                        assert source['parse_status'] in ('read','partial') and source['excerpts']
                        await expect(page.locator('.source-import').get_by_text('选择另一份文件',exact=True)).to_be_visible()
                    assert p['sources'][-1]['parse_status']=='read'
                    await expect(page.get_by_text('合成补充.htm',exact=True).first).to_be_visible()
                    await page.screenshot(path=str(output/'02-project-material-html.png'))
                    assert provider.calls==0 and not p['items'] and not p.get('active_baseline_id')
                    assert not report['routing_errors'] and not report['page_errors']
                    report.update(status='passed',checks=['聊天可选择 PDF/PPTX/HTML；未发送的附件仅在本地保留','项目资料实际读取 PDF/PPTX/HTML/HTM，原文、状态和哈希可核对，0模型请求'])
                except Exception as error:
                    import traceback
                    report.update(status='failed',failure=str(error),traceback=traceback.format_exc())
                    await page.screenshot(path=str(output/'failure.png'))
                finally:
                    (output/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),'utf-8')
                    await context.close();await browser.close()
    print(json.dumps({key:report.get(key) for key in ('status','checks','failure','model_calls')},ensure_ascii=False))
    if report['status']!='passed':raise SystemExit(1)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--static-build',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    asyncio.run(run(parser.parse_args()))
