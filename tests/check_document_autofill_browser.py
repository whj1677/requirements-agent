"""Document click -> AI missing-field draft -> explicit review -> document plan.

The sole browser origin is intercepted into an isolated ASGI app; no listener,
daily data, external model or hidden approval is used.
"""
import argparse
import asyncio
import copy
import contextlib
import json
import os
from pathlib import Path
import re
import sys
import sqlite3
import tempfile

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
IMPORT_SANDBOX=tempfile.TemporaryDirectory(prefix='ra-document-import-')
os.environ['RA_DATA_DIR']=str(Path(IMPORT_SANDBOX.name)/'data')
import httpx
from playwright.async_api import async_playwright, expect
from app.main import create_app
from tests.check_required_autofill_browser import DraftProvider
from tests.check_requirement_entry_browser import Journey, RID, ORIGIN, AFTER
from app.provider import Provider, DEFAULT, origin


class LiveProvider(Provider):
    def __init__(self):
        super().__init__(ROOT/'.env',credential_dir=ROOT/'data/.credentials');self.requests=[]
    async def request(self,config,messages,evidence=None):
        self.requests.append({'stage':evidence.value['stage'],'call_id':evidence.value['call_id']})
        return await super().request(config,messages,evidence=evidence)


async def run(args):
    args.output.mkdir(parents=True,exist_ok=True)
    report={'status':'running','page_errors':[],'api_requests':[],'real_model_calls':0,'checks':[]}
    real=getattr(args,'execute_real_model',False)
    from scripts.check_shared_draft_live import daily_fingerprint
    daily_before=daily_fingerprint()
    if real:
        folder_path=args.output/'live-session';folder_path.mkdir()
    with (contextlib.nullcontext(str(folder_path)) if real else tempfile.TemporaryDirectory(prefix='ra-document-')) as folder:
        provider=LiveProvider() if real else DraftProvider(Path(folder));app=create_app(Path(folder)/'data',provider=provider)
        if real:
            with sqlite3.connect((ROOT/'data/requirements.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
                config=dict(DEFAULT,**json.loads(db.execute("SELECT payload FROM settings WHERE id='model'").fetchone()[0]))
            assert origin(config)=='https://api.deepseek.com' and config['model']=='deepseek-flash' and provider.key(config)
            app.state.store.setting('model',config)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=ORIGIN) as client:
            j=Journey(args,'entry',app,provider,client,args.output,report)
            with app.state.store.edit(j.pid,j.project()['revision'],'合成生成文档缺项') as (p,_):
                p['product_context']['intent']='';p['context_field_states']['intent']={'status':'unknown'}
            before=copy.deepcopy(j.project())
            async with async_playwright() as pw:
                browser=await pw.chromium.launch();context=await browser.new_context(viewport={'width':1366,'height':768})
                await context.route('**/*',j.dispatch);page=await context.new_page();j.page=page
                page.on('pageerror',lambda e:report['page_errors'].append(str(e)))
                try:
                    await page.goto(ORIGIN,wait_until='networkidle')
                    await page.locator('aside.sidebar button[title="'+before['name']+'"]').click()
                    work=j.work()
                    # Navigation alone must not request AI.
                    await work.get_by_role('button',name='不使用 AI，直接完善',exact=True).click()
                    table=work.get_by_role('table',name='需求确认表');await expect(table).to_be_visible()
                    manual=table.locator('[data-cell-key="requirement-'+RID+'-statement"] textarea')
                    await manual.fill(AFTER)
                    await work.get_by_role('button',name='保存草稿',exact=True).click()
                    await work.get_by_role('navigation',name='需求工作流程').get_by_role('button',name=re.compile('1.*提供信息')).click()
                    await work.get_by_role('navigation',name='需求工作流程').get_by_role('button',name=re.compile('3.*确认交付')).click()
                    await work.get_by_role('button',name='查看与核对正式文档',exact=True).click()
                    generate=page.get_by_role('button',name='准备生成 PRD',exact=True)
                    await generate.click()
                    dialog=page.get_by_role('dialog',name='核对本次任务',exact=True)
                    await expect(dialog.get_by_role('heading',name='生成文档前，AI 自动补全必填草稿')).to_be_visible()
                    assert not provider.requests
                    await dialog.get_by_role('button',name='返回保留输入',exact=True).click()
                    assert not provider.requests and j.project()==before
                    await generate.click();await expect(dialog).to_be_visible()
                    if not real:
                        with app.state.store.edit(j.pid,j.project()['revision'],'合成任务计划过期') as (latest,_):
                            latest['name']+='（并发修改）'
                        await dialog.get_by_role('button',name=re.compile('^(授权本次范围并运行|运行本次任务)$')).click()
                        await expect(dialog.get_by_role('button',name='读取最新版本并重新核对',exact=True)).to_be_visible()
                        assert not provider.requests
                        await dialog.get_by_role('button',name='读取最新版本并重新核对',exact=True).click()
                        await expect(dialog.get_by_role('heading',name='生成文档前，AI 自动补全必填草稿')).to_be_visible()
                    await dialog.get_by_role('button',name=re.compile('^(授权本次范围并运行|运行本次任务)$')).click()
                    await expect(table).to_be_visible(timeout=180000 if real else 20000)
                    await expect(table.get_by_role('textbox',name='希望达到的目标',exact=True)).not_to_have_value('',timeout=180000 if real else 20000)
                    await expect(manual).to_have_value(AFTER)
                    assert 1<=len(provider.requests)<=2 if real else len(provider.requests)==1
                    after=j.project()
                    assert after['items'][:len(before['items'])]==before['items']
                    assert after['documents']==before['documents'] and after['stage_checks']==before['stage_checks']
                    assert app.state.store.records(j.pid,'user_task')[-1]['action']=='organize'
                    await page.screenshot(path=str(args.output/'01-draft-confirmation.png'))
                    initial_calls=len(provider.requests)
                    # Repeated document clicks must point to review without paying again.
                    await work.get_by_role('button',name='保存草稿',exact=True).click()
                    await work.get_by_role('navigation',name='需求工作流程').get_by_role('button',name=re.compile('1.*提供信息')).click()
                    await work.get_by_role('navigation',name='需求工作流程').get_by_role('button',name=re.compile('3.*确认交付')).click()
                    await work.get_by_role('button',name='查看与核对正式文档',exact=True).click()
                    await generate.click();await expect(table).to_be_visible()
                    await expect(dialog).to_have_count(0)
                    call_count=len(provider.requests)
                    assert call_count==initial_calls, 'Repeated document clicks cannot repeat the paid task'
                    if real:
                        assert not await work.locator('.sheet-missing-jump').count(), 'Live draft still has missing required fields'
                        report.update(status='passed',real_model_calls=call_count,model_requests=call_count,human_confirmation='NOT_RUN',checks=['真实模型补全已显示','人工草稿保留','重复生成未重跑模型','原需求与文档未自动确认'],calls=provider.requests)
                        (args.output/'project-after.json').write_text(json.dumps(j.project(),ensure_ascii=False,indent=2),'utf-8')
                        continue_after_draft=False
                    else:continue_after_draft=True
                    if not continue_after_draft:
                        await page.screenshot(path=str(args.output/'02-live-confirmation.png'))
                    else:
                        await finish_confirmation(work,page,generate,dialog,j,provider,before,args.output,report)
                    assert not report['page_errors'],report['page_errors']
                except Exception as error:
                    import traceback
                    report.update(status='failed',error=str(error),traceback=traceback.format_exc())
                    await page.screenshot(path=str(args.output/'failure.png'),full_page=True)
                finally:await browser.close()
    report['daily_data_unchanged']=daily_fingerprint()==daily_before
    (args.output/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),'utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='api_requests'},ensure_ascii=False));assert report['status']=='passed'


async def finish_confirmation(work,page,generate,dialog,j,provider,before,output,report):
    assert len(provider.requests)==1
    await work.get_by_role('button',name='确认并进入交付',exact=True).click()
    await expect(work.get_by_role('button',name='查看与核对正式文档',exact=True)).to_be_visible(timeout=15000)
    assert j.project()['stage_checks'].get('3'), j.project().get('stage_checks')
    assert j.project()['documents']==before['documents']
    await work.get_by_role('button',name='查看与核对正式文档',exact=True).click()
    await generate.click();await expect(dialog).to_be_visible()
    await expect(dialog.get_by_role('heading',name='生成评审稿',exact=True)).to_be_visible()
    assert len(provider.requests)==1, 'Confirmation and planning must not call the provider'
    await dialog.get_by_role('button',name='返回保留输入',exact=True).click()
    report.update(status='passed',model_requests=1,checks=['进入和取消不调用模型','过期计划重核后保留补全和确认意图','生成前自动补齐','人工草稿保留','不自动确认或生成文档','重复点击只引导确认','一次确认后进入原文档计划'])
    await page.screenshot(path=str(output/'02-ready-for-document.png'))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--static-build',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--execute-real-model',action='store_true')
    asyncio.run(run(parser.parse_args()))
