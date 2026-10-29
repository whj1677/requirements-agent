"""Click historical recovery in the real UI using isolated synthetic evidence."""
import argparse
import asyncio
import json
import mimetypes
import os
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


async def run(args):
    output=args.output.resolve();output.mkdir(parents=True,exist_ok=False)
    os.environ['RA_DATA_DIR']=str(output/'import-data')
    from playwright.async_api import async_playwright,expect
    from tests.test_response_normalization import seed_failed_runtime
    c,app,p,value,proof=seed_failed_runtime(output/'data')
    raw=proof.path.read_bytes()
    async def forbidden(*a,**kw):raise AssertionError('Recovery must not call a model')
    app.state.provider.request=forbidden
    writes=[];errors=[]
    async with async_playwright() as pw:
        browser=await pw.chromium.launch(headless=True)
        page=await browser.new_page(viewport={'width':1366,'height':768})
        page.on('pageerror',lambda e:errors.append(str(e)))
        async def route(r):
            req=r.request;url=urlsplit(req.url)
            assert url.netloc=='127.0.0.1:8765'
            if url.path.startswith('/api/'):
                if req.method!='GET':writes.append(url.path)
                headers={k:v for k,v in req.headers.items() if k.lower() not in ('host','content-length','connection','accept-encoding','cookie')}
                if 'origin' in headers:headers['origin']='http://testserver'
                if 'referer' in headers:headers['referer']='http://testserver/'
                response=c.request(req.method,url.path+('?' +url.query if url.query else ''),headers=headers,content=req.post_data_buffer)
                await r.fulfill(status=response.status_code,content_type=response.headers.get('content-type'),body=response.content)
            else:
                build=args.static_build.resolve();f=(build/(url.path.lstrip('/') or 'index.html')).resolve()
                assert f.is_relative_to(build) and f.is_file()
                await r.fulfill(content_type=mimetypes.guess_type(f.name)[0] or 'application/octet-stream',body=f.read_bytes())
        await page.route('**/*',route)
        try:
            await page.goto('http://127.0.0.1:8765/',wait_until='networkidle')
            await page.locator('aside.sidebar button[title="'+p['name']+'"]').click()
            close=page.get_by_role('button',name='关闭进度提示',exact=True)
            if await close.is_visible():await close.click()
            work=page.locator('.connected-workspace:visible')
            await work.get_by_role('button',name='查看任务与恢复',exact=True).click()
            recover=work.locator('.connected-task-details').get_by_role('button',name=re.compile('^恢复草稿'))
            await expect(recover).to_be_visible()
            await recover.click()
            await expect(work.get_by_role('table',name='需求确认表',exact=True)).to_be_visible()
            saved=app.state.store.get(p['id'])
            assert saved['items'][:len(p['items'])]==p['items'] and saved['questions']==p['questions']
            assert saved['items'][-1]['statement']==value['proposals'][0]['statement']
            assert saved['items'][-1]['selection_status']=='candidate' and saved['active_baseline_id'] is None
            assert proof.path.read_bytes()==raw and not errors
            assert not any(path.endswith('/actions') or path.endswith('/runs') for path in writes)
            await page.screenshot(path=str(output/'recovered-draft.png'))
            report=dict(status='passed',provider_calls=0,original_clauses_unchanged=True,raw_evidence_unchanged=True,page_errors=errors)
        except Exception as e:
            await page.screenshot(path=str(output/'failure.png'))
            (output/'failure-body.txt').write_text(await page.locator('body').inner_text(),'utf-8')
            report=dict(status='failed',error=str(e),page_errors=errors)
        finally:await browser.close();c.close()
    (output/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),'utf-8')
    print(json.dumps(report,ensure_ascii=False));assert report['status']=='passed'


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--static-build',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    asyncio.run(run(p.parse_args()))
