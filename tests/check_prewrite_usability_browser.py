"""Reproduce the actual blocked BMS draft before any AI regeneration.
Only a readonly local snapshot is used; filler vendor names are synthetic and
exist solely in the isolated test. The provider is frozen, never external.
"""
import argparse,asyncio,copy,json,mimetypes,re,shutil,sqlite3,tempfile
from pathlib import Path
from urllib.parse import urlsplit
from tests.check_prewrite_batch_browser import ROOT,ORIGIN,BatchProvider,create_app,dumps,daily_fingerprint,httpx,async_playwright,expect

async def run(args):
    args.output.mkdir(parents=True);before=daily_fingerprint();report=dict(status='running',page_errors=[],api_events=[])
    with tempfile.TemporaryDirectory(prefix='ra-prewrite-usability-') as folder:
        provider=BatchProvider(Path(folder));app=create_app(Path(folder)/'data',provider=provider);store=app.state.store
        with sqlite3.connect((ROOT/'data/requirements.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
            p=json.loads(db.execute('select payload from projects where id=?',('P-e9e927a868c249d8',)).fetchone()[0])
            config=json.loads(db.execute("select payload from settings where id='model'").fetchone()[0])
        store.setting('model',config);pid=store.create('隔离使用体验复测')['id'];p.update(id=pid,name='本机隔离副本：BMS 确认按钮复测')
        with store.connect() as db:db.execute('update projects set revision=?,payload=? where id=?',(p['revision'],dumps(p),pid))
        (store.folder/'sources').mkdir(exist_ok=True)
        for source in p['sources']:
            src=(ROOT/'data/sources'/source['id']).resolve();assert src.is_relative_to((ROOT/'data/sources').resolve())
            if src.is_file():shutil.copyfile(src,store.folder/'sources'/source['id'])
        provider.project=lambda:store.get(pid);initial=copy.deepcopy(p)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=ORIGIN) as client,async_playwright() as pw:
            browser=await pw.chromium.launch();context=await browser.new_context(viewport=dict(width=1366,height=768))
            async def route(r):
                request=r.request;url=urlsplit(request.url);assert url.netloc=='127.0.0.1:8765'
                if url.path.startswith('/api/'):
                    response=await client.request(request.method,url.path,content=request.post_data_buffer,headers={k:v for k,v in request.headers.items() if k.lower() not in ('host','content-length','connection','accept-encoding')})
                    report['api_events'].append(dict(method=request.method,path=url.path,status=response.status_code))
                    await r.fulfill(status=response.status_code,body=response.content,headers={k:v for k,v in response.headers.items() if k.lower() not in ('content-length','content-encoding','transfer-encoding')})
                else:
                    build=args.static_build.resolve();file=(build/(url.path.lstrip('/') or 'index.html')).resolve();assert file.is_relative_to(build)
                    await r.fulfill(content_type=mimetypes.guess_type(file.name)[0] or 'application/octet-stream',body=file.read_bytes())
            await context.route('**/*',route);page=await context.new_page();page.on('pageerror',lambda e:report['page_errors'].append(str(e)))
            try:
                await page.goto(ORIGIN,wait_until='networkidle');await page.locator('aside.sidebar button[title="'+p['name']+'"]').click()
                work=page.locator('.connected-workspace:visible');await work.get_by_role('button',name='2 完善需求',exact=True).click()
                await expect(work.get_by_role('button',name='保存草稿',exact=True)).to_be_enabled()
                await work.locator('.refinement-view-switch').get_by_role('button',name=re.compile('^成文检查')).click()
                batch=work.get_by_role('region',name='成文检查确认表',exact=True);confirm=batch.get_by_role('button',name='确认全部修改并复检',exact=True)
                await expect(confirm).to_be_enabled();assert not provider.requests
                await batch.locator('.prewrite-batch-scroll').evaluate('e=>e.scrollTop=e.scrollHeight')
                assert not re.search(r'\b(?:REQ|AC|Q|SRC|EX)-[A-Za-z0-9_-]{8,}',await batch.inner_text()),'Default review must use business names'
                await confirm.click()
                missing=batch.get_by_role('region',name='本表待补内容',exact=True);await expect(missing).to_be_visible()
                await expect(missing.get_by_role('textbox')).to_have_count(2)
                assert '当前硬件厂家' in await missing.inner_text() and '可选值' in await missing.inner_text()
                assert not re.search(r'REQ-|detail_contract|enum_values|F4|F7',await missing.inner_text())
                assert not provider.requests and not [r for r in report['api_events'] if r['method']=='POST' and r['path'].endswith('/refinement-confirmation')]
                assert store.get(pid)==initial,'validation cannot mutate project or adopt AI output'
                for width,height in ((1366,768),(1280,720),(1093,614)):
                    await page.set_viewport_size(dict(width=width,height=height));bounds=await confirm.bounding_box()
                    assert bounds and bounds['y']>=0 and bounds['y']+bounds['height']<=height,(width,height,bounds)
                    assert await confirm.evaluate('e=>{const r=e.getBoundingClientRect();return e.contains(document.elementFromPoint(r.left+r.width/2,r.top+r.height/2))}')
                await page.screenshot(path=str(args.output/'01-missing-in-place.png'))
                fields=missing.get_by_role('textbox')
                await fields.nth(0).press_sequentially('合成甲厂，合成乙厂，合成丙厂',delay=15)
                await expect(fields).to_have_count(2);await expect(fields.nth(0)).to_have_value('合成甲厂，合成乙厂，合成丙厂')
                await fields.nth(1).fill('合成甲厂\n合成乙厂\n合成丙厂')
                assert not provider.requests
                await page.screenshot(path=str(args.output/'02-filled-in-place.png'))
                await confirm.click();dialog=page.get_by_role('dialog',name='核对本次任务',exact=True);await expect(dialog).to_be_visible(timeout=15000)
                assert len(store.records(pid,'refinement_confirmation_receipt'))==1 and not provider.requests
                after=store.get(pid);assert after['documents']==initial['documents']
                for q in initial['questions']:
                    if q.get('answer'):assert next(v for v in after['questions'] if v['id']==q['id'])['answer']==q['answer']
                await dialog.get_by_role('button',name='返回保留输入',exact=True).click()
                await batch.get_by_role('button',name='继续整批复检',exact=True).click()
                await dialog.get_by_role('button',name=re.compile('^(授权本次范围并运行|运行本次任务)$')).click()
                await expect(work.get_by_text('可以继续生成 PRD。',exact=True)).to_be_visible(timeout=20000)
                assert len(provider.requests)==1 and provider.requests[0]['stage']=='review'
                assert len(store.records(pid,'refinement_confirmation_receipt'))==1
                report.update(status='passed',snapshot_revision=initial['revision'],confirmation_requests=1,real_model_calls=0,model_requests=provider.requests,checks=['未重新起草即复现实际缺项','确认按钮可点击校验','两项缺项原地标红并用业务名称显示','填写多值过程不丢字、不关闭编辑','默认不显示技术编号','三个笔记本视口按钮可见可点','确认前零写入零模型调用','一次确认成功且保留原回答与文档','一次整批复检，无额外跳转'])
                await page.screenshot(path=str(args.output/'03-confirmed-and-rechecked.png'))
            except Exception as error:
                import traceback
                report.update(status='failed',error=str(error),traceback=traceback.format_exc());await page.screenshot(path=str(args.output/'failure.png'),full_page=True)
            finally:await browser.close()
    report['daily_data_unchanged']=daily_fingerprint()==before
    (args.output/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),'utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in ('api_events','traceback')},ensure_ascii=False));assert report['status']=='passed' and report['daily_data_unchanged']

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--static-build',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    asyncio.run(run(parser.parse_args()))
