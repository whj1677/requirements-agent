"""Use a local-only copy of the current BMS project; any model request fails."""
import argparse,asyncio,copy,json,mimetypes,shutil,sqlite3,tempfile
from pathlib import Path
from urllib.parse import urlsplit
from tests.check_prewrite_batch_browser import ROOT,ORIGIN,Provider,create_app,daily_fingerprint,httpx,async_playwright,expect

PID='P-e9e927a868c249d8'

class NoModel(Provider):
    def __init__(self):super().__init__(ROOT/'evidence/bms-prd-direct-20261026/absent.env')
    async def request(self,*args,**kwargs):raise AssertionError('Local PRD generation must never call a model')

async def run(args):
    args.output.mkdir(parents=True);before=daily_fingerprint();report=dict(status='running',model_calls=0,page_errors=[],api_events=[])
    with tempfile.TemporaryDirectory(prefix='ra-bms-prd-') as temporary:
        data=Path(temporary)/'data';data.mkdir()
        source=sqlite3.connect((ROOT/'data/requirements.sqlite3').as_uri()+'?mode=ro',uri=True)
        target=sqlite3.connect(data/'requirements.sqlite3');source.backup(target);target.close();source.close()
        if (ROOT/'data/sources').exists():shutil.copytree(ROOT/'data/sources',data/'sources')
        app=create_app(data,provider=NoModel());store=app.state.store;p=store.get(PID);initial=copy.deepcopy(p)
        report['input_revision']=p['revision']
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=ORIGIN) as client,async_playwright() as pw:
            browser=await pw.chromium.launch();page=await browser.new_page(viewport=dict(width=1280,height=720));page.on('pageerror',lambda e:report['page_errors'].append(str(e)))
            async def route(r):
                request=r.request;url=urlsplit(request.url);assert url.netloc=='127.0.0.1:8765'
                if url.path.startswith('/api/'):
                    response=await client.request(request.method,url.path+('?' +url.query if url.query else ''),content=request.post_data_buffer,headers={k:v for k,v in request.headers.items() if k.lower() not in ('host','content-length','connection','accept-encoding')})
                    report['api_events'].append(dict(method=request.method,path=url.path,status=response.status_code))
                    await r.fulfill(status=response.status_code,body=response.content,headers={k:v for k,v in response.headers.items() if k.lower() not in ('content-length','content-encoding','transfer-encoding')})
                else:
                    file=args.static_build/(url.path.lstrip('/') or 'index.html')
                    await r.fulfill(content_type=mimetypes.guess_type(file.name)[0] or 'application/octet-stream',body=file.read_bytes())
            await page.route('**/*',route)
            try:
                await page.goto(ORIGIN,wait_until='networkidle');await page.locator('aside.sidebar button[title="'+p['name']+'"]').click()
                work=page.locator('.connected-workspace:visible');await work.get_by_role('button',name='继续核对已有草稿',exact=True).click()
                batch=work.get_by_role('region',name='成文检查确认表',exact=True);await expect(batch).to_be_visible()
                # A quoted old clause must be visible without enabling all fields.
                old=next(i for i in p['items'] if i['id']=='AC-fbaad6a5b4ea4c17')
                table=batch.get_by_role('table',name='本次统一确认的条款修改',exact=True)
                assert await table.get_by_text(old['statement'],exact=True).count()>0
                assert store.get(PID)==initial
                for width,height in ((1280,720),(1093,614)):
                    await page.set_viewport_size(dict(width=width,height=height))
                    for button in (batch.get_by_role('button',name='先生成待确认评审稿',exact=True),batch.get_by_role('button',name='确认全部修改并复检',exact=True)):
                        bounds=await button.bounding_box();assert bounds and bounds['y']>=0 and bounds['y']+bounds['height']<=height,(width,height,bounds)
                await page.set_viewport_size(dict(width=1280,height=720))
                await page.screenshot(path=str(args.output/'01-review-choice.png'))
                await batch.get_by_role('button',name='先生成待确认评审稿',exact=True).click()
                reader=page.get_by_role('article',name='PRD 正文',exact=True);await expect(reader).to_be_visible(timeout=20000)
                task_status=page.get_by_role('region',name='PRD 生成任务状态',exact=True)
                await expect(task_status).to_contain_text('本机生成 PRD 评审稿')
                assert '尚未生成新版 PRD' not in await task_status.inner_text()
                current=store.get(PID);artifact=current['documents']['prd'];assert artifact['assembly_origin']=='saved_content' and artifact['model_calls']==0
                for key in ('items','questions','sources','product_context','stage_checks','prewrite_review'):assert current.get(key)==initial.get(key)
                assert artifact['prewrite_snapshot']['blocking_count']==initial['prewrite_review']['blocking_count']
                assert len(artifact['content']['coverage'])>=len([i for i in p['items'] if i['kind']=='requirement' and i['selection_status']=='selected'])
                await expect(reader.locator('.document-detail-table table').first).to_be_visible(timeout=15000)
                await page.wait_for_function("() => {const e=document.querySelector('.document-reading-area');return e&&e.getBoundingClientRect().top>=-1&&e.getBoundingClientRect().top<300}")
                assert await reader.locator('.document-detail-table table').count()>=3
                assert await reader.locator('.document-detail-diagram img').first.evaluate('i=>i.complete&&i.naturalWidth>0')
                for name,ext in [('下载评审稿 MD','md'),('下载评审稿 DOCX','docx')]:
                    async with page.expect_download() as event:await reader.get_by_role('button',name=name,exact=True).click()
                    await (await event.value).save_as(str(args.output/('bms-prd-review.'+ext)))
                text=(args.output/'bms-prd-review.md').read_text('utf-8');assert '非正式交付' in text
                from docx import Document
                assert len(Document(args.output/'bms-prd-review.docx').tables)>=3
                formal=await client.get('/api/projects/'+PID+'/documents/prd/docx',params={'document_id':artifact['id']});assert formal.status_code==409
                assert not report['page_errors']
                await page.screenshot(path=str(args.output/'02-prd-downloaded.png'))
                report.update(status='passed',artifact_id=artifact['id'],download_verified=['md','docx'],pending_preserved=artifact['prewrite_snapshot']['blocking_count'])
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
