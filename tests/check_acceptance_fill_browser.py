"""Replay saved real-model tariff snapshots through the UI; no model or video.

An isolated ASGI store replaces only the read snapshot when an already-recorded
model result arrives. This tests display/merge, not a new model call or adoption.
"""
from __future__ import annotations
import argparse, asyncio, hashlib, json, mimetypes, os, sys, tempfile, traceback
from pathlib import Path
from urllib.parse import urlsplit
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
ORIGIN='http://127.0.0.1:8765'
PID='P-d4b61a83e6f14612'
TARGETS=[('REQ-57d6a21993724812','电价管理-园区与空状态',4),('REQ-cc93433b204f48dc','批量为园区配置电价',5)]

def write(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')

async def run(args):
    build,output,snapshots=args.static_build.resolve(),args.output.resolve(),args.snapshots.resolve()
    assert output.is_relative_to(ROOT/'evidence') and not output.exists()
    output.mkdir(parents=True)
    frozen={n:json.loads((snapshots/f'tariff-v{n}.json').read_text('utf-8')) for n in (14,16,17)}
    report=dict(status='running',checks=[],fixture='SAVED_REAL_MODEL_TARIFF_SNAPSHOTS_REPLAY',
                fresh_model_calls=0,video_recorded=False,daily_data_access=False,listener_started=False,
                formal_confirmation='NOT_RUN',page_errors=[],api_requests=[],served_static={})
    with tempfile.TemporaryDirectory(prefix='ra-acceptance-fill-') as temporary:
        folder=Path(temporary);os.environ['RA_DATA_DIR']=str(folder/'import')
        import httpx
        from playwright.async_api import async_playwright,expect
        from app.main import create_app
        from app.core import dumps,now
        from tests.check_refine_workspace_browser import NoModelProvider
        provider=NoModelProvider(folder);app=create_app(folder/'data',provider=provider);store=app.state.store
        def install(revision):
            value=frozen[revision]
            with store.connect() as db:
                db.execute('INSERT OR REPLACE INTO projects VALUES(?,?,?)',(PID,revision,dumps(value)))
                db.execute('INSERT OR REPLACE INTO revisions VALUES(?,?,?,?,?)',(PID,revision,dumps(value),'saved model snapshot replay',now()))
        install(14)
        store.record(PID,'user_task',dict(action='clarify',label='已保存真实模型结果回放',status='succeeded',
                     created=now(),source_ids=[],run_ids=[],max_calls=0,calls=0,completed_steps=1,
                     stages=['clarify'],step_states=[dict(stage='clarify',status='succeeded')],
                     message='仅回放历史模型结果，不发起新请求。'),'TASK-AC-REPLAY')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=ORIGIN) as client:
            async with async_playwright() as pw:
                browser=await pw.chromium.launch(headless=True)
                context=await browser.new_context(viewport=dict(width=1440,height=900))
                async def route_request(route):
                    request=route.request;url=urlsplit(request.url)
                    assert url.netloc=='127.0.0.1:8765','External request forbidden'
                    if url.path.startswith('/api/'):
                        assert request.method=='GET','Writes forbidden in read-only replay: '+url.path
                        response=await client.get(url.path+('?' + url.query if url.query else ''))
                        report['api_requests'].append(dict(path=url.path,status=response.status_code))
                        await route.fulfill(status=response.status_code,body=response.content,content_type=response.headers.get('content-type','application/json'))
                    else:
                        file=(build/(url.path.lstrip('/') or 'index.html')).resolve()
                        assert file.is_relative_to(build) and file.is_file()
                        report['served_static'][url.path]=hashlib.sha256(file.read_bytes()).hexdigest()
                        await route.fulfill(status=200,body=file.read_bytes(),content_type=mimetypes.guess_type(file.name)[0] or 'application/octet-stream')
                await context.route('**/*',route_request)
                page=await context.new_page();page.set_default_timeout(10000)
                page.on('pageerror',lambda e:report['page_errors'].append(str(e)))
                try:
                    await page.goto(ORIGIN,wait_until='networkidle')
                    await page.locator('aside.sidebar button[title="电价管理"]').click()
                    work=page.locator('.connected-workspace:visible')
                    await work.get_by_role('button',name='不使用 AI，直接完善',exact=True).click()
                    table=work.get_by_role('table',name='需求确认表',exact=True)
                    await expect(table).to_be_visible()
                    rid,title,_=TARGETS[0]
                    original=next(i for i in frozen[14]['items'] if i['id']==rid)['statement']
                    manual=original+'（仅用于隔离回填测试的人工备注。）'
                    statement=table.get_by_role('textbox',name=title+' 需求内容',exact=True)
                    await statement.fill(manual)
                    await work.get_by_role('button',name='保存草稿',exact=True).click()
                    saved=await page.evaluate('()=>Object.fromEntries(Object.entries(sessionStorage))')
                    report['checks'].append('v14空验收且存在人工需求修改，草稿保存在浏览器')
                    async def expect_acceptance(revision,targets):
                        for reqid,label,count in targets:
                            expected=[i for i in frozen[revision]['items'] if i['kind']=='acceptance' and reqid in i.get('related_refs',[]) and not i.get('target_item_id')]
                            assert len(expected)==count
                            for index,item in enumerate(expected,1):
                                field=table.get_by_role('textbox',name=label+' 验收建议 '+str(index),exact=True)
                                await expect(field).to_have_value(item['statement'])
                            await expect(table.get_by_role('textbox',name=label+' 验收建议 '+str(count),exact=True)).to_be_visible()
                        await expect(statement).to_have_value(manual)
                    for revision,targets in [(16,TARGETS[:1]),(17,TARGETS)]:
                        install(revision)
                        await work.locator('.connected-task').get_by_role('button',name='刷新',exact=True).click()
                        await expect_acceptance(revision,targets)
                        report['checks'].append(f'v{revision}实际模型验收结果自动填入，保留人工正文，无需点击更新建议')
                    await table.get_by_role('textbox',name=TARGETS[1][1]+' 验收建议 4',exact=True).scroll_into_view_if_needed()
                    await page.screenshot(path=str(output/'filled-acceptance.png'))
                    # Reopening an old saved browser draft must merge the same pending results.
                    await page.evaluate('values=>{sessionStorage.clear();for(const [key,value] of Object.entries(values))sessionStorage.setItem(key,value)}',saved)
                    await page.reload(wait_until='networkidle')
                    await expect_acceptance(17,TARGETS)
                    report['checks'].append('恢复v14浏览器旧草稿到v17时，9条真实建议仍回填且人工文字保留')
                    await page.screenshot(path=str(output/'restored-draft.png'))
                    assert store.get(PID)==frozen[17],'Replay navigation changed stored business data'
                    assert provider.calls==0 and not report['page_errors']
                    report.update(status='passed',snapshot_unchanged=True)
                except Exception as exc:
                    report.update(status='failed',failure=str(exc),traceback=traceback.format_exc())
                    await page.screenshot(path=str(output/'failure.png'))
                finally:
                    write(output/'result.json',report)
                    await context.close();await browser.close()
    print(json.dumps({k:report.get(k) for k in ('status','checks','failure','fresh_model_calls','video_recorded')},ensure_ascii=False))
    if report['status']!='passed':raise SystemExit(1)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--static-build',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--snapshots',type=Path,required=True)
    asyncio.run(run(parser.parse_args()))
