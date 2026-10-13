"""Actual tariff snapshot UI recovery, with zero additional model requests.

The original real request failed; no candidates are fabricated. A separate copy
is used to inspect ten requirement entries, source reading, cancelled AI plans,
editable local drafts, and the honest missing-acceptance gate. Daily data and
the original snapshot stay unchanged. This is NOT successful AI generation or
human acceptance of the business requirements.
"""
import argparse
import asyncio
import copy
import hashlib
import json
import mimetypes
import os
import re
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
ISOLATED_IMPORT=tempfile.TemporaryDirectory(prefix='ra-real-entry-import-')
os.environ['RA_DATA_DIR']=str(Path(ISOLATED_IMPORT.name)/'data')
import httpx
from playwright.async_api import async_playwright, expect
from app.main import create_app
from tests.check_tariff_entry_acceptance import copy_database
from tests.check_refine_workspace_browser import NoModelProvider
from tests.check_refine_human_flow_browser import ANNOTATION, Recorder

ORIGIN='http://127.0.0.1:8765'


class NoNetworkProvider(NoModelProvider):
    def key(self,config):
        # A dummy key enables the local plan screen only. HTTP mutations that
        # could launch work are independently forbidden by the browser bridge.
        return 'OFFLINE_PLAN_ONLY_NEVER_SENT'


class RealRecoveryRecorder(Recorder):
    async def note(self,text,seconds=3.5):
        self.chapters.append(dict(seconds=round(time.monotonic()-self.started,2),text=text))
        if self.enabled:
            await self.page.evaluate("text=>{document.getElementById('recording-note').textContent=text}",
                '录屏说明 · 非产品界面\n真实电价管理项目副本\n前次真实 AI 请求失败\n本段 0 新增模型请求\n\n'+text)
            await self.page.wait_for_timeout(seconds*1000)


def write(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')


async def run(args):
    session,build,output=args.session.resolve(),args.static_build.resolve(),args.output.resolve()
    assert session.is_relative_to(ROOT/'evidence') and output.is_relative_to(ROOT/'evidence')
    assert not output.exists(),'Preserve previous evidence; choose a fresh output'
    outcome=json.loads((session/'outcome.json').read_text('utf-8'))
    assert outcome['task_status']=='failed' and outcome['provider_requests']==1
    output.mkdir(parents=True)
    source_hash=hashlib.sha256((session/'data/requirements.sqlite3').read_bytes()).hexdigest()
    copy_database(session/'data',output/'data')
    provider=NoNetworkProvider(output)
    app=create_app(output/'data',provider=provider);store=app.state.store
    pid=(session/'project-id.txt').read_text('utf-8').strip()
    frozen=copy.deepcopy(store.get(pid));write(output/'project-before.json',frozen)
    initial_preview_count=len(store.records(pid,'decision_preview'))
    reqs=[i for i in frozen['items'] if i['kind']=='requirement' and i['applies_to']=='to_be' and not i.get('target_item_id')]
    assert frozen['name']=='电价管理' and len(reqs)==10 and not any(i['kind']=='acceptance' for i in frozen['items'])
    report=dict(status='running',source_session=str(session),project_id=pid,static_build=str(build),checks=[],api_requests=[],page_errors=[],
        prior_real_request_status=outcome['task_status'],prior_real_provider_requests=outcome['provider_requests'],real_model_calls_during_recording=0,
        daily_data_access=False,prior_real_generation_succeeded=False,business_acceptance='NOT_RUN',confirmation='NOT_RUN')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=ORIGIN,timeout=30) as client:
        async with async_playwright() as p:
            browser=await p.chromium.launch(headless=True)
            context=await browser.new_context(viewport=dict(width=1440,height=900),record_video_dir=str(output/'video'),record_video_size=dict(width=1440,height=900))
            await context.add_init_script('('+ANNOTATION+')()')
            async def bridge(route):
                request=route.request;url=urlsplit(request.url)
                assert url.netloc=='127.0.0.1:8765','External requests forbidden'
                if url.path.startswith('/api/'):
                    # The existing answer-impact preview writes decision_preview
                    # audit records in this COPY only. It does not mutate project
                    # content, commit answers, or start a provider task.
                    assert request.method=='GET' or url.path.endswith(('/actions/plan','/decision-review')), 'Unexpected write or model start: '+url.path
                    headers={k:v for k,v in request.headers.items() if k.lower() not in ('host','content-length','connection','accept-encoding')}
                    headers['host']='127.0.0.1:8765'
                    response=await client.request(request.method,url.path+('?' +url.query if url.query else ''),headers=headers,content=request.post_data_buffer)
                    row=dict(method=request.method,path=url.path,status=response.status_code)
                    if request.post_data:row['body']=request.post_data_json
                    report['api_requests'].append(row)
                    await route.fulfill(status=response.status_code,body=response.content,content_type=response.headers.get('content-type','application/json'))
                else:
                    path=(build/(url.path.lstrip('/') or 'index.html')).resolve()
                    assert path.is_relative_to(build) and path.is_file()
                    await route.fulfill(status=200,body=path.read_bytes(),content_type=mimetypes.guess_type(path.name)[0] or 'application/octet-stream')
            await context.route('**/*',bridge)
            page=await context.new_page();rec=RealRecoveryRecorder(page,output,True)
            page.on('pageerror',lambda e:report['page_errors'].append(str(e)))
            work=page.locator('.connected-workspace:visible')
            support=work.locator('details.requirement-support')
            async def task(requirement):
                if await support.get_attribute('open') is None:await rec.click(support.locator(':scope > summary'))
                await rec.click(work.get_by_role('navigation',name='完善需求待办').get_by_role('button').filter(has=page.get_by_text(requirement['title'],exact=True)))
                editor=work.get_by_role('region',name=requirement['title']+' 需求确认',exact=True)
                await expect(editor).to_be_visible()
                await expect(editor.get_by_role('heading',name=requirement['title'],exact=True)).to_be_in_viewport(ratio=.8)
                assert await work.locator('.requirement-task-field:visible, [id^="behavior-review-"]:visible').count()==0
                return editor
            try:
                await page.goto(ORIGIN,wait_until='networkidle')
                async with page.expect_response(lambda r:r.url.endswith('/workspace-draft') and r.request.method=='GET'):
                    await rec.click(page.locator('aside.sidebar button[title="电价管理"]'))
                await rec.click(work.get_by_role('button',name='不使用 AI，直接完善',exact=True))
                await expect(work.get_by_role('table',name='需求确认表')).to_be_visible()
                assert store.get(pid)==frozen
                await rec.note('1. 这是你的电价管理 v13 副本\n真实 AI 请求因引用无效失败，原十条需求和十五份资料保留；没有新验收候选。',5)
                await page.screenshot(path=str(output/'01-actual-project.png'))
                seen=[]
                for requirement in reqs:
                    editor=await task(requirement)
                    await expect(editor.get_by_role('textbox',name=requirement['title']+' 需求内容',exact=True)).to_have_value(requirement['statement'])
                    await expect(editor.get_by_role('combobox',name=requirement['title']+' 本次改动',exact=True)).to_have_value('unspecified')
                    await expect(editor.get_by_role('button',name='让 AI 起草业务说明与验收',exact=True)).to_be_visible()
                    seen.append(requirement['id'])
                assert store.get(pid)==frozen and provider.calls==0
                assert not any(r['path'].endswith('/decision-review') for r in report['api_requests']), 'Entering an unanswered task must not automatically preview an unsaved answer'
                assert not any(r['status']>=400 for r in report['api_requests']), 'Unexpected API error on an ordinary task entry'
                report['verified_requirement_ids']=seen
                report['checks'].append('真实10条需求均从support待办进入对应表格，显示原文/未知性质/AI起草入口，不出现旧逐格编辑器')
                first=reqs[0];editor=await task(first)
                await rec.note('2. 从“电价模板库列表”待办进入\n同一张表格核对正文、改动性质和验收；无需再跳回逐项手填页面。',5)
                await page.screenshot(path=str(output/'02-real-task-to-table.png'))
                await rec.click(editor.get_by_role('button',name='查看依据',exact=True))
                attachment=page.get_by_role('dialog',name='附件内容')
                await expect(attachment).to_be_visible()
                exref=first['source_refs'][0]
                src=next(s for s in frozen['sources'] if s['id']==exref['source_id'])
                await expect(attachment).to_contain_text(src['title'])
                await expect(attachment.get_by_role('region',name='已读取内容')).to_contain_text('电价')
                await rec.note('3. 原资料仍可核对\n这里显示实际提取内容和读取限制。模型失败没有覆盖或删除资料。',4)
                await page.screenshot(path=str(output/'03-source-preview.png'))
                await rec.click(attachment.get_by_role('button',name='关闭附件',exact=True))
                await rec.click(page.get_by_role('dialog',name='项目资料',exact=True).get_by_role('button',name='关闭项目资料',exact=True))
                await rec.click(editor.get_by_role('button',name='让 AI 起草业务说明与验收',exact=True))
                dialog=page.get_by_role('dialog',name='核对本次任务',exact=True)
                await expect(dialog).to_be_visible()
                assert provider.calls==0
                await rec.note('4. AI 起草入口可继续使用\n这里只打开本条任务核对。本段选择返回，绝不追加收费请求。',4)
                await page.screenshot(path=str(output/'04-plan-not-executed.png'))
                await rec.click(dialog.get_by_role('button',name='返回保留输入',exact=True))
                plan=next(r for r in reversed(report['api_requests']) if r['path'].endswith('/actions/plan'))
                assert plan['body']['target']['id']==first['id'] and provider.calls==0
                report['checks'].append('真实来源可读，行内AI只规划当前REQ；取消无模型调用且原项目完全不变')
                text=editor.get_by_role('textbox',name=first['title']+' 需求内容',exact=True)
                await rec.fill(text,first['statement']+'\n（界面验收临时草稿，不保存。）')
                await expect(text).to_have_value(first['statement']+'\n（界面验收临时草稿，不保存。）')
                change=editor.get_by_role('combobox',name=first['title']+' 本次改动',exact=True)
                await change.select_option('modified')
                await expect(change).to_have_value('modified')
                await expect(editor.get_by_role('button',name='查看确认内容',exact=True)).to_be_disabled()
                await expect(editor).to_contain_text('本条尚无验收建议')
                assert store.get(pid)==frozen
                await rec.note('5. 失败后仍能编辑本行草稿\n分类切换只是临时演示。真实验收尚未生成，确认入口保持关闭；不编造内容凑通过。',5)
                await page.screenshot(path=str(output/'05-editable-with-honest-gate.png'))
                await text.fill(first['statement']);await change.select_option('unspecified')
                await rec.click(editor.get_by_role('button',name='保存本行草稿',exact=True))
                await page.reload(wait_until='networkidle')
                # Refresh must retain the actual saved project, not temporary
                # UI draft. The temporary local edit was restored before reload.
                if not await work.get_by_role('table',name='需求确认表').count():
                    await rec.click(work.get_by_role('button',name='不使用 AI，直接完善',exact=True))
                editor=await task(first)
                await expect(editor.get_by_role('textbox',name=first['title']+' 需求内容',exact=True)).to_have_value(first['statement'])
                await expect(editor.get_by_role('combobox',name=first['title']+' 本次改动',exact=True)).to_have_value('unspecified')
                assert store.get(pid)==frozen and provider.calls==0
                assert not any(r['path'].endswith('/decision-review') for r in report['api_requests']), 'Refresh must not automatically preview an unanswered question'
                assert not any(r['status']>=400 for r in report['api_requests'])
                report['checks'].append('失败后正文/分类仍可编辑，缺验收不能预览确认；恢复草稿并刷新后原状态保持，未自动批准未知内容')
                await rec.note('6. 刷新后原状态保持\n真实 AI 生成失败仍须解决。本片仅验证入口与恢复，不代表这十条业务已验收。',5)
                assert not report['page_errors']
                report['status']='passed'
                await page.screenshot(path=str(output/'06-restored.png'))
            except Exception as error:
                report.update(status='failed',error=str(error))
                await page.screenshot(path=str(output/'failure.png'))
                (output/'failure-body.txt').write_text(await page.locator('body').inner_text(),encoding='utf-8')
            finally:
                write(output/'project-after.json',store.get(pid))
                with sqlite3.connect((session/'data/requirements.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
                    original=json.loads(db.execute('SELECT payload FROM projects WHERE id=?',(pid,)).fetchone()[0])
                report['source_snapshot_unchanged']=original==frozen
                report['source_database_sha256_unchanged']=hashlib.sha256((session/'data/requirements.sqlite3').read_bytes()).hexdigest()==source_hash
                report['copy_project_unchanged']=store.get(pid)==frozen
                report['copy_preview_audit_records_added']=len(store.records(pid,'decision_preview'))-initial_preview_count
                report['real_model_calls_during_recording']=provider.calls
                rec.save();write(output/'result.json',report)
                video=page.video;await context.close();await video.save_as(str(output/'真实电价项目入口与失败恢复.webm'));await browser.close()
    print(json.dumps({k:report.get(k) for k in ('status','checks','error','source_snapshot_unchanged','copy_project_unchanged','real_model_calls_during_recording')},ensure_ascii=False,indent=2))
    if report['status']!='passed':raise SystemExit(1)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--session',type=Path,required=True)
    parser.add_argument('--static-build',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    asyncio.run(run(parser.parse_args()))
