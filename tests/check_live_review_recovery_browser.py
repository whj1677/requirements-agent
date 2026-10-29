"""Opt-in real-model recovery through the displayed batch review and PRD UI.

Only resumes a frozen synthetic session under evidence/. No listener or daily
writes. Each round explicitly confirms the displayed proposal as a test operator.
"""
import argparse,asyncio,contextlib,copy,json,mimetypes,os,re,shutil,sqlite3,sys,time
from pathlib import Path
from urllib.parse import urlsplit
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
os.environ.setdefault('RA_DATA_DIR',str(ROOT/'evidence/full-acceptance-20261027/recovery-import'))
import httpx
from playwright.async_api import async_playwright,expect
from app.main import create_app
from app.provider import Provider,DEFAULT,origin
from scripts.check_shared_draft_live import daily_fingerprint


class LiveProvider(Provider):
    def __init__(self):
        super().__init__(ROOT/'.env',credential_dir=ROOT/'data/.credentials');self.requests=[]
    async def request(self,config,messages,evidence=None):
        self.requests.append(dict(stage=evidence.value['stage'],call_id=evidence.value['call_id']))
        return await super().request(config,messages,evidence=evidence)


async def run(args):
    assert args.execute_real_model
    source=args.resume_data.resolve();output=args.output.resolve()
    assert source.is_relative_to(ROOT/'evidence') and output.is_relative_to(ROOT/'evidence')
    assert not output.exists();output.mkdir(parents=True)
    daily=daily_fingerprint();started=time.monotonic();provider=LiveProvider()
    app=create_app(output/'data',provider=provider);store=app.state.store
    with sqlite3.connect((source/'requirements.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        p=json.loads(db.execute('select payload from projects').fetchone()[0])
        assert '合成' in p['name']
        assert all(json.loads(r[0])['status'] not in ('queued','running') for r in db.execute("select payload from records where kind in ('run','user_task')"))
        with sqlite3.connect(output/'data/requirements.sqlite3') as target:db.backup(target)
    for name in ('sources','evidence'):
        if (source/name).is_dir():shutil.copytree(source/name,output/'data'/name)
    with sqlite3.connect((ROOT/'data/requirements.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        config=dict(DEFAULT,**json.loads(db.execute("select payload from settings where id='model'").fetchone()[0]))
    assert origin(config)=='https://api.deepseek.com' and config['model']=='deepseek-flash' and provider.key(config)
    store.setting('model',config);pid=p['id'];initial=copy.deepcopy(store.get(pid))
    report=dict(status='running',synthetic_only=True,page_errors=[],rounds=[],operator='frozen synthetic test operator',real_model_calls=0)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1:8765') as client,async_playwright() as pw:
        browser=await pw.chromium.launch();context=await browser.new_context(viewport=dict(width=1366,height=768))
        async def route(r):
            req=r.request;url=urlsplit(req.url);assert url.netloc=='127.0.0.1:8765'
            if url.path.startswith('/api/'):
                if url.path.endswith('/refinement-confirmation'):
                    (output/'confirmation-request.json').write_bytes(req.post_data_buffer)
                response=await client.request(req.method,url.path+('?' +url.query if url.query else ''),content=req.post_data_buffer,headers={k:v for k,v in req.headers.items() if k.lower() not in ('host','content-length','connection','accept-encoding')})
                await r.fulfill(status=response.status_code,body=response.content,headers={k:v for k,v in response.headers.items() if k.lower() not in ('content-length','content-encoding','transfer-encoding')})
            else:
                f=(args.static_build.resolve()/(url.path.lstrip('/') or 'index.html')).resolve();assert f.is_relative_to(args.static_build.resolve())
                await r.fulfill(content_type=mimetypes.guess_type(f.name)[0] or 'application/octet-stream',body=f.read_bytes())
        await context.route('**/*',route);page=await context.new_page();page.on('pageerror',lambda e:report['page_errors'].append(str(e)))
        async def execute(action):
            dialog=page.get_by_role('dialog',name='核对本次任务',exact=True);await expect(dialog).to_be_visible(timeout=15000)
            old={t['id'] for t in store.records(pid,'user_task')}
            await dialog.get_by_role('button',name=re.compile('^(授权本次范围并运行|运行本次任务)$')).click()
            for _ in range(3600):
                tasks=[t for t in store.records(pid,'user_task') if t['id'] not in old and t['action']==action]
                if tasks and tasks[-1]['status'] not in ('queued','running'):
                    task=tasks[-1];report['rounds'].append(dict(action=action,status=task['status'],calls=task['calls'],task_id=task['id']))
                    print(json.dumps(report['rounds'][-1]),flush=True)
                    assert task['status'] in ('succeeded','partial','awaiting_user'),task.get('message')
                    return task
                await asyncio.sleep(.1)
            raise AssertionError('task did not finish: '+action)
        try:
            await page.goto('http://127.0.0.1:8765/',wait_until='networkidle')
            await page.locator('aside.sidebar button[title="'+p['name']+'"]').click()
            work=page.locator('.connected-workspace:visible')
            await work.get_by_role('button',name='继续核对已有草稿',exact=True).click()
            await work.locator('.refinement-view-switch').get_by_role('button',name=re.compile('^成文检查')).click()
            assert not provider.requests and store.get(pid)==initial
            await page.screenshot(path=str(output/'00-existing-checks.png'))
            if args.retry_check_first:
                await work.get_by_role('button',name='重试本次检查',exact=True).click()
                await execute('prewrite')
                await page.screenshot(path=str(output/'00-retried-check.png'))
            for round_index in range(3):
                review=store.get(pid).get('prewrite_review',{})
                if review.get('status')=='passed':break
                batch=work.get_by_role('region',name='成文检查确认表',exact=True)
                await expect(batch).to_be_visible(timeout=15000)
                edited=False
                if args.operator_decisions:
                    choices=json.loads(args.operator_decisions.read_text('utf8'))
                    current=store.get(pid)
                    checks=[f for f in current['prewrite_review']['findings'] if f.get('status')!='resolved'
                        and (f.get('severity')=='blocker' or f.get('blocks_document') or f.get('requires_confirmation'))]
                    for index,finding in enumerate(checks,1):
                        q=next((q for q in current['questions'] if q['id']==finding.get('question_id')),None)
                        for choice in choices:
                            if not q or choice['contains'] not in q['question']:continue
                            label=f'检查点 {index} 的回答'
                            edit=batch.get_by_role('button',name='修改'+label,exact=True)
                            if await edit.count():await edit.click()
                            await batch.get_by_role('textbox',name=label,exact=True).fill(choice['answer'])
                            report.setdefault('operator_edits',[]).append(dict(question=q['question'],answer=choice['answer']))
                            edited=True
                if not (args.confirm_existing_first and round_index==0):
                    await batch.get_by_role('button',name='AI 批量起草全部修改',exact=True).click();await execute('organize')
                await expect(batch.get_by_role('button',name='确认全部修改并复检',exact=True)).to_be_enabled(timeout=15000)
                await page.screenshot(path=str(output/f'{round_index+1}-draft.png'))
                await batch.get_by_role('button',name='确认全部修改并复检',exact=True).click()
                compare=work.get_by_role('region',name='比较并处理冲突',exact=True)
                if await compare.is_visible():
                    choice='保留我的修改' if edited else '采用 AI 建议'
                    while await compare.get_by_role('button',name=choice,exact=True).count():await compare.get_by_role('button',name=choice,exact=True).first.click()
                    await batch.get_by_role('button',name='确认全部修改并复检',exact=True).click()
                await execute('prewrite')
                await page.screenshot(path=str(output/f'{round_index+1}-rechecked.png'))
            assert store.get(pid)['prewrite_review']['status']=='passed','Review still has unresolved points after three explicit rounds'
            await expect(work.get_by_text('可以继续生成 PRD。',exact=True)).to_be_visible(timeout=15000)
            await work.get_by_role('button',name='返回并继续生成 PRD',exact=True).click();await execute('document')
            reader=page.get_by_role('article',name='PRD 正文',exact=True);await expect(reader).to_be_visible(timeout=15000)
            artifact=store.get(pid)['documents']['prd'];assert artifact
            for name,extension in [('下载评审稿 MD','md'),('下载评审稿 DOCX','docx')]:
                async with page.expect_download() as event:await reader.get_by_role('button',name=name,exact=True).click()
                await (await event.value).save_as(str(output/('prd-review.'+extension)))
            from docx import Document
            assert len(Document(output/'prd-review.docx').paragraphs)>5
            assert not report['page_errors'];await page.screenshot(path=str(output/'99-prd.png'))
            report.update(status='passed',document_id=artifact['id'],downloads=['md','docx'])
        except Exception as error:
            import traceback
            report.update(status='failed',error=str(error)[:3000],traceback=traceback.format_exc());await page.screenshot(path=str(output/'failure.png'),full_page=True)
        finally:await browser.close()
    report.update(real_model_calls=len(provider.requests),model_requests=provider.requests,daily_data_unchanged=daily_fingerprint()==daily,seconds=round(time.monotonic()-started,2))
    (output/'project.json').write_text(json.dumps(store.get(pid),ensure_ascii=False,indent=2),'utf8')
    (output/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),'utf8')
    print(json.dumps({k:v for k,v in report.items() if k not in ('traceback','error')},ensure_ascii=False),flush=True)
    assert report['status']=='passed' and report['daily_data_unchanged']


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--resume-data',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--static-build',type=Path,required=True);p.add_argument('--execute-real-model',action='store_true');p.add_argument('--retry-check-first',action='store_true');p.add_argument('--confirm-existing-first',action='store_true');p.add_argument('--operator-decisions',type=Path);asyncio.run(run(p.parse_args()))
