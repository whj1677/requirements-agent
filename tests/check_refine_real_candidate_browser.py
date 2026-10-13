"""Record confirmation of preserved real AI output in an isolated data clone.

This does not generate or rewrite the suggestion. The model was called earlier;
this browser only displays and explicitly confirms that exact output through
the current UI and real local APIs. No additional model request is permitted.
"""
import argparse
import asyncio
import copy
import json
import mimetypes
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import time
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
IMPORT_DATA = tempfile.TemporaryDirectory(prefix='ra-real-candidate-import-')
os.environ['RA_DATA_DIR'] = str((Path(IMPORT_DATA.name)/'data').resolve())

import httpx
from playwright.async_api import async_playwright, expect
from app.main import create_app
from app.requirements import behavior_review_current
from tests.check_refine_workspace_browser import NoModelProvider, require, wait_store
from tests.check_refine_human_flow_browser import ANNOTATION, QUESTION_GOAL

ORIGIN='http://127.0.0.1:8765'


def write(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


async def run(args):
    session,build,out=args.session.resolve(),args.static_build.resolve(),args.output.resolve()
    require(session.is_relative_to(ROOT/'evidence') and out.is_relative_to(ROOT/'evidence'),'Evidence path outside repository')
    require(not out.exists(),'Preserve prior browser evidence; do not overwrite')
    out.mkdir(parents=True)
    source=session/'data'
    clone=out/'data'
    clone.mkdir()
    with sqlite3.connect((source/'requirements.sqlite3').as_uri()+'?mode=ro',uri=True) as original:
        with sqlite3.connect(clone/'requirements.sqlite3') as target:
            original.backup(target)
    for path in source.iterdir():
        if path.name.startswith('requirements.sqlite3'):
            continue
        if path.is_dir():shutil.copytree(path,clone/path.name)
        else:shutil.copy2(path,clone/path.name)
    provider=NoModelProvider(out)
    app=create_app(clone,provider=provider)
    store=app.state.store
    pid=(session/'project-id.txt').read_text('utf-8').strip()
    frozen=store.get(pid)
    candidate=next(i for i in frozen['items'] if i.get('target_item_id')=='REQ-COLLAB' and i['selection_status']=='candidate')
    original=next(i for i in frozen['items'] if i['id']=='REQ-COLLAB')
    require(candidate['statement']==original['statement'],'The real output case no longer has the expected unchanged statement')
    require(candidate['behavior']['permissions']!=original['behavior']['permissions'],'The real output contains no permission change')
    trace=dict(scope='visible_review_of_prior_real_model_output',project_id=pid,
               prior_provider_requests=json.loads((session/'outcome.json').read_text('utf-8'))['provider_requests'],
               provider_requests_during_recording=0,source_snapshot_unchanged=None,
               static_build=str(build),checks=[],api_requests=[],page_errors=[])
    chapters=[]
    start=time.monotonic()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=ORIGIN,timeout=30) as client:
        async with async_playwright() as pw:
            browser=await pw.chromium.launch(headless=True)
            context=await browser.new_context(viewport=dict(width=1440,height=900),
                record_video_dir=str(out/'raw'),record_video_size=dict(width=1440,height=900))
            await context.add_init_script('('+ANNOTATION+')()')
            await context.add_init_script("localStorage.setItem('ra-last-project-id',"+json.dumps(pid)+");sessionStorage.setItem('ra-connected-step-"+pid+"','refine');")

            async def dispatch(route):
                request=route.request;parsed=urlsplit(request.url)
                require(parsed.netloc=='127.0.0.1:8765','No external browser requests')
                if parsed.path.startswith('/api/'):
                    require(not(request.method!='GET' and (parsed.path.endswith('/actions') or '/runs' in parsed.path or '/confirm' in parsed.path)),'Model or formal confirmation forbidden')
                    headers={key:value for key,value in request.headers.items() if key.lower() not in ('host','content-length','connection','accept-encoding')}
                    headers['host']='127.0.0.1:8765'
                    response=await client.request(request.method,parsed.path+('?' + parsed.query if parsed.query else ''),content=request.post_data_buffer,headers=headers)
                    entry=dict(method=request.method,path=parsed.path,status=response.status_code)
                    if request.method!='GET':entry.update(body=request.post_data_json,response=response.json())
                    trace['api_requests'].append(entry)
                    await route.fulfill(status=response.status_code,body=response.content,headers={'content-type':response.headers.get('content-type','application/json')})
                else:
                    file=(build/(parsed.path.lstrip('/') or 'index.html')).resolve()
                    require(file.is_relative_to(build) and file.is_file(),'Unexpected static request')
                    await route.fulfill(status=200,body=file.read_bytes(),content_type=mimetypes.guess_type(file.name)[0] or 'application/octet-stream')

            await context.route('**/*',dispatch)
            page=await context.new_page();video=page.video
            page.set_default_timeout(15000)
            page.on('pageerror',lambda error:trace['page_errors'].append(str(error)))

            async def note(text,seconds=5):
                chapters.append(dict(seconds=round(time.monotonic()-start,2),text=text))
                await page.evaluate("text=>{document.getElementById('recording-note').textContent=text}",
                    '录屏说明 · 非产品界面\n真实 AI 结果 · 合成储能云项目\n此前模型已调用 2 次\n本片不重新调用模型\n\n'+text)
                await page.wait_for_timeout(seconds*1000)

            try:
                await page.goto(ORIGIN,wait_until='networkidle')
                current=page.locator('.connected-workspace:visible .refine-task-current')
                preview=current.locator('.answer-change-preview')
                await expect(preview).to_be_visible()
                await note('打开这一条已保存回答，系统直接展示刚才真实模型生成的需求修改。这里只核对结果，未再调用模型。',7)
                await preview.locator('.answer-before-after').scroll_into_view_if_needed()
                await expect(preview.locator('.answer-before-after')).to_contain_text(original['statement'])
                require(store.get(pid)==frozen,'Opening the preview wrote business state')
                await note('修改前后正文相同。这不是没有修改：实际变化在下方“权限约束”，需要继续核对。',6)
                metadata=preview.locator('.answer-other-change')
                require(await metadata.count()>0,'Real model metadata changes were hidden')
                for index in range(await metadata.count()):
                    row=metadata.nth(index)
                    await row.scroll_into_view_if_needed();await row.hover()
                    await note('按从上到下顺序核对本次的引用、关联和范围依据变化。它们也会随确认写入，不能只看正文。',3)
                trace['checks'].append('changed_metadata_is_displayed_before_business_review')
                permission=preview.locator('.answer-behavior-row').filter(has=page.get_by_text('权限约束',exact=True))
                await permission.scroll_into_view_if_needed()
                await permission.hover()
                await expect(permission).to_contain_text(original['behavior']['permissions'])
                await expect(permission).to_contain_text(candidate['behavior']['permissions'])
                await expect(permission).to_be_in_viewport(ratio=.9)
                await note('原来是“跨人员查看与修改边界需澄清”；AI 建议改为“均可查看，但不能相互修改”。这与本次回答一致。',9)
                await page.screenshot(path=str(out/'01-real-permission-difference.png'),full_page=False)
                trace['checks'].append('real_permission_before_after_visible_while_statement_unchanged')
                button=preview.get_by_role('button',name='确认并继续',exact=True)
                await button.scroll_into_view_if_needed();await button.hover()
                await note('看完修改内容后，点击“确认并继续”。此时才会把真实建议写入当前需求。',5)
                await button.click()
                after=await wait_store(store,pid,lambda p:next(q for q in p['questions'] if q['id']=='Q-PERMISSION').get('understanding_status')=='applied','Real candidate confirmation did not complete the answered decision')
                await expect(current).to_contain_text(QUESTION_GOAL)
                saved=next(i for i in after['items'] if i['id']=='REQ-COLLAB')
                require(saved['behavior']['permissions']==candidate['behavior']['permissions'],'Confirmed permission was not written')
                require(behavior_review_current(saved),'Explicitly reviewed behavior has no current fingerprint')
                require(next(i for i in after['items'] if i['id']==candidate['id'])['selection_status']=='deferred','Revision candidate was not consumed')
                require(next(i for i in after['items'] if i['id']=='GOAL-DELIVERY')==next(i for i in frozen['items'] if i['id']=='GOAL-DELIVERY'),'Unanswered goal changed')
                require(next(q for q in after['questions'] if q['id']=='Q-DELIVERY')['status']=='open','Other required answer was fabricated')
                require(next(q for q in after['questions'] if q['id']=='Q-OPTIONAL')['status']=='open','Optional unknown was fabricated')
                trace['checks'].append('explicit_confirmation_writes_real_candidate_and_advances_to_real_remaining_question')
                await note('权限回答已完成，自动进入下一条“交付形态”。这条仍未回答，所以不会冒充整个阶段都完成；可选日报问题也保留。',9)
                await page.screenshot(path=str(out/'02-confirmed-and-next-question.png'),full_page=False)
                previews=[r for r in trace['api_requests'] if r['method']=='POST' and r['path'].endswith('/decision-review')]
                commits=[r for r in trace['api_requests'] if r['method']=='POST' and r['path'].endswith('/decision-review/commit')]
                require(len(previews)==1 and previews[0]['status']==200,'Current frontend did not create one real preview')
                require(candidate['id'] in previews[0]['body'].get('review_behavior_item_ids',[]),'Frontend omitted explicit displayed behavior review intent')
                require(len(commits)==1 and commits[0]['status']==200,'Current frontend commit failed or repeated')
                require(provider.calls==0 and not trace['page_errors'],'New model invocation or browser error')
                trace.update(status='passed',final_revision=after['revision'])
                write(out/'project-after.json',after)
            except Exception as error:
                trace.update(status='failed',failure=str(error))
                await page.screenshot(path=str(out/'failure.png'),full_page=True)
                (out/'failure.html').write_text(await page.content(),encoding='utf-8')
                raise
            finally:
                with sqlite3.connect((source/'requirements.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
                    untouched=json.loads(db.execute('SELECT payload FROM projects WHERE id=?',(pid,)).fetchone()[0])
                trace['source_snapshot_unchanged']=untouched==frozen
                trace['provider_requests_during_recording']=provider.calls
                write(out/'result.json',trace);write(out/'chapters.json',chapters)
                await context.close()
                await video.save_as(str(out/'真实AI建议确认操作.webm'))
                await browser.close()
    require(trace['source_snapshot_unchanged'],'Preserved real model source snapshot changed')
    print(json.dumps(dict(status=trace['status'],checks=trace['checks'],output=str(out)),ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session',required=True,type=Path)
    parser.add_argument('--static-build',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    asyncio.run(run(parser.parse_args()))
