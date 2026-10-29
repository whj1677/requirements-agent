"""Synthetic large-project, storage-failure, navigation and responsive checks.

All requests are intercepted into an isolated ASGI store; no listener or model.
"""
import argparse,asyncio,copy,json,mimetypes,os,re,sys,tempfile,time
from pathlib import Path
from urllib.parse import urlsplit
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
IMPORT=tempfile.TemporaryDirectory(prefix='ra-ui-import-');os.environ['RA_DATA_DIR']=str(Path(IMPORT.name)/'data')
import httpx
from playwright.async_api import async_playwright,expect
from app.main import create_app
from tests.test_runtime import FakeProvider
from tests.check_requirement_entry_browser import seed,OTHER
from scripts.check_shared_draft_live import daily_fingerprint
ORIGIN='http://127.0.0.1:8765'

async def run(args):
    args.output.mkdir(parents=True);daily=daily_fingerprint()
    report=dict(status='running',real_model_calls=0,page_errors=[],api=[],mount_counts=[])
    with tempfile.TemporaryDirectory(prefix='ra-ui-experience-') as temp:
        provider=FakeProvider([]);app=create_app(Path(temp)/'data',provider=provider);store=app.state.store
        pid=seed(store,'entry')
        with store.edit(pid,store.get(pid)['revision'],'合成42条需求与17个问题的性能夹具') as (p,_):
            p['name']='合成体验 · 42条需求';base=copy.deepcopy(next(i for i in p['items'] if i['id']==OTHER));p['items']=[]
            for n in range(42):
                item=copy.deepcopy(base);item.update(id=f'REQ-LOAD-{n}',title=f'电价模板查看 {n+1}',related_refs=[f'AC-LOAD-{n}'])
                p['items'].extend([item,dict(id=f'AC-LOAD-{n}',kind='acceptance',title=f'查看验收 {n+1}',statement='打开模板后显示已保存名称。',related_refs=[item['id']],source_refs=item['source_refs'],selection_status='selected',applies_to='to_be',revision=1)])
            p['product_context']['scope_ids']=[i['id'] for i in p['items'] if i['kind']=='requirement']
            p['questions']=[dict(id=f'Q-LOAD-{n}',question=f'第{n+1}项查看范围如何确认？',why='合成压力场景',related_refs=[f'REQ-LOAD-{n}'],source_refs=base['source_refs'],options=[],blocking=True,blocking_stage=3,status='open',answer=None) for n in range(17)]
            p['messages']=[dict(stage='interview',role='user' if n%2==0 else 'assistant',text=('合成对话：本期只读查看电价模板，不修改现有数据；历史版本筛选范围仍需确认。'*7),user_task_id=f'T-CHAT-{n}') for n in range(6)]
        store.record(pid,'user_task',dict(label='本机生成 PRD 评审稿',action='document',status='succeeded',
            created='2026-10-28T01:00:00Z',input_revision=store.get(pid)['revision'],source_ids=[],stages=['prd'],
            run_ids=[],max_calls=0,calls=0,cost=None,message='合成界面状态，非真实模型结果',completed_steps=1,
            step_states=[dict(stage='prd',status='succeeded')]),rid='TASK-UI-COMPLETED')
        names=[]
        for n in range(10):
            name=f'合成切换项目 {n+1}';store.create(name);names.append(name)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=ORIGIN) as client,async_playwright() as pw:
            browser=await pw.chromium.launch();context=await browser.new_context(viewport=dict(width=1366,height=768))
            await context.add_init_script("""(()=>{const original=Storage.prototype.setItem;window.inputMetrics={writes:0};Storage.prototype.setItem=function(k,v){if(k.endsWith('-refinement-sheet')||k.endsWith('-refinement-sheet-base')){window.inputMetrics.writes++;if(window.rejectDraftStorage)throw new DOMException('full','QuotaExceededError');}return original.call(this,k,v)};})()""")
            async def route(r):
                request=r.request;url=urlsplit(request.url);assert url.netloc=='127.0.0.1:8765'
                if url.path.startswith('/api/'):
                    result=await client.request(request.method,url.path+('?' +url.query if url.query else ''),content=request.post_data_buffer,headers={k:v for k,v in request.headers.items() if k.lower() not in ('host','content-length','connection','accept-encoding')})
                    report['api'].append(dict(method=request.method,path=url.path,status=result.status_code))
                    await r.fulfill(status=result.status_code,content_type=result.headers.get('content-type','application/json'),body=result.content)
                else:
                    build=args.static_build.resolve();p=(build/(url.path.lstrip('/') or 'index.html')).resolve();assert p.is_relative_to(build)
                    await r.fulfill(content_type=mimetypes.guess_type(p.name)[0] or 'application/octet-stream',body=p.read_bytes())
            await context.route('**/*',route);page=await context.new_page();page.on('pageerror',lambda e:report['page_errors'].append(str(e)))
            try:
                await page.goto(ORIGIN,wait_until='networkidle')
                for name in names:
                    await page.locator('aside.sidebar button').filter(has=page.locator('strong',has_text=re.compile('^'+re.escape(name)+'$'))).click()
                    await expect(page.locator('.project-workspace:visible .project-bar h1')).to_have_text(name)
                    report['mount_counts'].append(await page.locator('.project-workspace').count())
                assert max(report['mount_counts'])<=2,report['mount_counts']
                await page.locator('aside.sidebar button[title="合成体验 · 42条需求"]').click()
                work=page.locator('.connected-workspace:visible');await expect(work.get_by_role('button',name='继续核对已有草稿',exact=True)).to_be_enabled()
                assert await work.locator('button.primary:visible').count()==1
                log=work.get_by_role('log',name='聊天记录');composer=work.locator('.chat-compose-dock')
                log_box=await log.bounding_box();composer_box=await composer.bounding_box()
                assert log_box['height']>=120 and log_box['y']+log_box['height']<=composer_box['y']+2
                assert await log.evaluate('e=>e.scrollHeight>e.clientHeight')
                await expect(work.get_by_role('button',name='发送',exact=True)).to_be_in_viewport(ratio=1)
                report['long_chat_record_height']=round(log_box['height'])
                await page.screenshot(path=str(args.output/'assistant-1366.png'))
                await work.get_by_role('navigation',name='需求工作流程').get_by_role('button',name=re.compile('完善需求')).click()
                table=work.get_by_role('table',name='需求确认表',exact=True);await expect(table).to_be_visible()
                field=table.get_by_role('textbox',name='电价模板查看 1 需求内容',exact=True);await expect(field).to_be_visible()
                original=await field.input_value();await page.wait_for_timeout(500)
                await page.evaluate('window.inputMetrics.writes=0')
                await field.click();await field.press('End');start=time.monotonic();await field.press_sequentially('连续输入性能验证ABCDEFGHIJ',delay=8);report['input_seconds']=round(time.monotonic()-start,3)
                await page.wait_for_timeout(500)
                assert await field.input_value()==original+'连续输入性能验证ABCDEFGHIJ'
                report['storage_writes']=await page.evaluate('window.inputMetrics.writes');assert report['storage_writes']<=4
                for width,height in ((1366,768),(1280,720),(2560,1440)):
                    await page.set_viewport_size(dict(width=width,height=height))
                    assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
                    await expect(work.locator('.refinement-sheet-footer .primary')).to_be_in_viewport()
                    box=await work.locator('.refinement-sheet-scroll').bounding_box();report.setdefault('table_heights',[]).append(dict(width=width,height=round(box['height'])))
                    assert box['height']>=min(250,height*.35),box
                    nav=await work.locator('.connected-steps').bounding_box();task=await work.locator('.connected-task').bounding_box()
                    assert abs(nav['y']+nav['height']/2-task['y']-task['height']/2)<3, 'Stage and task feedback share one desktop band'
                    assert task['x']>=nav['x']+nav['width'] and task['x']+task['width']<=width, 'Task feedback must not overlap navigation or leave the viewport'
                    await page.screenshot(path=str(args.output/f'table-{width}.png'))
                await page.set_viewport_size(dict(width=390,height=844))
                await expect(page.locator('.compact-projects>summary')).to_be_visible()
                assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
                await page.screenshot(path=str(args.output/'table-mobile.png'))
                await page.set_viewport_size(dict(width=1366,height=768))
                await page.evaluate('window.rejectDraftStorage=true')
                await field.fill('存储不可用时仍然保留的人工输入');await page.wait_for_timeout(550)
                await expect(field).to_have_value('存储不可用时仍然保留的人工输入')
                await expect(work.get_by_text(re.compile('浏览器未能保存草稿'))).to_be_visible()
                assert not provider.received and not report['page_errors']
                report.update(status='passed',requirements=42,questions=17,storage_failure_input_retained=True)
            except Exception as e:
                import traceback
                report.update(status='failed',error=str(e),traceback=traceback.format_exc());await page.screenshot(path=str(args.output/'failure.png'),full_page=True)
            finally:await browser.close()
    report['daily_unchanged']=daily_fingerprint()==daily
    (args.output/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),'utf8')
    print(json.dumps({k:v for k,v in report.items() if k not in ('api','traceback')},ensure_ascii=False));assert report['status']=='passed' and report['daily_unchanged']
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--static-build',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);asyncio.run(run(parser.parse_args()))
