"""Isolated rendered pre-writing workflow; all provider replies are labelled fixtures."""
from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import re
import sys
import tempfile
import traceback
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
ORIGIN='http://127.0.0.1:8765'


async def run(args):
    build,output=args.static_build.resolve(),args.output.resolve()
    assert build.is_relative_to(ROOT) and (build/'index.html').is_file()
    assert output.is_relative_to(ROOT/'evidence') and not output.exists()
    output.mkdir(parents=True)
    report=dict(status='running',checks=[],api_events=[],routing_errors=[],page_errors=[],
        provider='OFFLINE_FIXTURE',real_model_calls=0,listener_started=False,daily_project_writes=False)
    with tempfile.TemporaryDirectory(prefix='ra-prewrite-browser-') as temp:
        folder=Path(temp)
        os.environ['RA_DATA_DIR']=str(folder/'global-import')
        from app.main import create_app
        from app.prewrite import apply_result, status
        from app.provider import DEFAULT
        from tests.helpers import EXAMPLES
        from tests.test_runtime import FakeProvider
        from scripts.run_prewrite_agent_validation import seed, DECISION, CONTRADICTION
        import httpx
        from playwright.async_api import async_playwright,expect

        provider=FakeProvider([])
        app=create_app(folder/'store',provider=provider)
        store=app.state.store
        p=seed(store);pid=p['id']
        response=copy.deepcopy(EXAMPLES['review'])
        response.update(summary='离线夹具：只有结果未知允许查询，交互前置条件矛盾。',proposals=[],questions=[],findings=[dict(
            type='contradiction',severity='blocker',topic_key='query-allowed-status',
            message='交互前置条件允许所有状态查询，与正文和流程终态禁止查询冲突。',
            suggested_resolution='请确认仅结果未知可以查询，并同步交互前置条件。',
            decision_question='哪些派单结果状态允许手工查询？',related_refs=['REQ-QUERY'],source_refs=[],
            evidence_quotes=[dict(object_id='REQ-QUERY',path='detail_contract.interactions.I1.preconditions',quote=CONTRADICTION),
                dict(object_id='REQ-QUERY',path='statement',quote=p['items'][0]['statement'])])])
        response['result'].update(assessment='needs_changes',required_decisions=['哪些派单结果状态允许手工查询？'],reviewed_refs=[i['id'] for i in p['items']])
        with store.edit(pid,p['revision'],'离线审查夹具') as (p,_):
            apply_result(p,response,'RUN-OFFLINE-PREWRITE')
        original=copy.deepcopy(store.get(pid))
        ready=copy.deepcopy(response);ready['findings']=[]
        ready['result'].update(assessment='ready_for_human_review',required_decisions=[])
        provider.results=iter([ready])
        store.setting('model',dict(DEFAULT,max_calls=1))

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=ORIGIN) as client:
            async with async_playwright() as pw:
                browser=await pw.chromium.launch(headless=True)
                context=await browser.new_context(viewport=dict(width=1280,height=720))
                async def dispatch(route):
                    request,url=route.request,urlsplit(route.request.url)
                    try:
                        assert url.netloc=='127.0.0.1:8765','Foreign browser request'
                        if url.path.startswith('/api/'):
                            assert request.method=='GET' or request.method=='POST' and url.path.endswith((
                                '/decision-review/answer','/decision-review/manual-preview','/decision-review/commit',
                                '/actions/plan','/actions','/response','/workspace-draft')),'Unexpected mutation '+url.path
                            headers={k:v for k,v in request.headers.items() if k.lower() not in ('host','content-length','connection','accept-encoding')}
                            result=await client.request(request.method,url.path+('?' +url.query if url.query else ''),headers=headers,content=request.post_data_buffer)
                            event=dict(method=request.method,path=url.path,status=result.status_code)
                            if request.post_data:event['body']=request.post_data_json
                            report['api_events'].append(event)
                            await route.fulfill(status=result.status_code,body=result.content,content_type=result.headers.get('content-type','application/json'))
                        else:
                            path=(build/(url.path.lstrip('/') or 'index.html')).resolve()
                            assert path.is_relative_to(build) and path.is_file()
                            await route.fulfill(status=200,body=path.read_bytes(),content_type=mimetypes.guess_type(path.name)[0] or 'application/octet-stream')
                    except Exception as error:
                        report['routing_errors'].append(str(error));await route.abort()
                await context.route('**/*',dispatch)
                page=await context.new_page();page.set_default_timeout(15000)
                page.on('pageerror',lambda error:report['page_errors'].append(str(error)))
                try:
                    await page.goto(ORIGIN,wait_until='networkidle')
                    await page.locator('aside.sidebar button[title="'+p['name']+'"]').click()
                    work=page.locator('.connected-workspace:visible')
                    await expect(work.get_by_role('textbox',name='你的想法',exact=True)).to_be_visible()
                    await work.get_by_role('button',name='不使用 AI，直接完善',exact=True).click()
                    await work.locator('.refinement-view-switch').get_by_role('button',name=re.compile('^成文检查')).click()
                    panel=work.get_by_role('region',name='成文检查',exact=True)
                    await expect(panel).to_contain_text(CONTRADICTION)
                    assert not provider.received and store.get(pid)['items']==original['items']
                    answer=panel.get_by_role('textbox',name='我的回答原文',exact=True)
                    await answer.fill(DECISION)
                    await work.get_by_role('button',name='需求列表',exact=True).click()
                    await work.locator('.refinement-view-switch').get_by_role('button',name=re.compile('^成文检查')).click()
                    await expect(answer).to_have_value(DECISION)
                    for width,height in ((1280,720),(1093,614)):
                        await page.set_viewport_size(dict(width=width,height=height))
                        await panel.scroll_into_view_if_needed()
                        assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
                        await page.screenshot(path=str(output/f'{width}x{height}-decision.png'))
                    report['checks'].append('默认助手入口；打开检查不调用模型；同区域切换保留回答；两种笔记本视口无页面横向溢出')
                    await page.set_viewport_size(dict(width=1280,height=720))
                    await panel.get_by_role('button',name='保存回答并整理修改',exact=True).click()
                    clarify_dialog=page.get_by_role('dialog',name='核对本次任务',exact=True)
                    await expect(clarify_dialog).to_be_visible()
                    plans=[e for e in report['api_events'] if e['path'].endswith('/actions/plan')]
                    assert plans[-1]['body']['action']=='clarify'
                    assert not provider.received and store.get(pid)['items']==original['items']
                    await clarify_dialog.get_by_role('button',name='返回保留输入',exact=True).click()
                    report['checks'].append('保存回答并整理修改进入局部澄清任务确认，取消不调用模型、不改需求')
                    await panel.get_by_role('button',name='直接修改需求',exact=True).click()
                    await panel.locator('.answer-detail-editor > summary').click()
                    editor=panel.locator('[data-detail-requirement="REQ-QUERY"]')
                    await editor.locator('[data-detail-section="interactions"] > summary').click()
                    row=editor.locator('[data-cell-key="requirement-REQ-QUERY-detail_contract.interactions.I1"]')
                    await row.locator(':scope > summary').click()
                    field=row.get_by_role('textbox',name=re.compile('前置'))
                    await field.fill('仅结果未知时显示并启用查询按钮；成功和失败均是终态，不允许再次查询。')
                    target=panel.locator('.answer-implementation-target')
                    await target.get_by_role('combobox').select_option('detail_contract.interactions.I1.preconditions')
                    await target.get_by_role('checkbox',name='我已核对此原文表达了答案',exact=True).check()
                    await panel.get_by_role('button',name='查看修改并确认',exact=True).click()
                    await expect(panel.get_by_role('region',name='详细定义修改前后',exact=True)).to_contain_text(CONTRADICTION)
                    await expect(panel.get_by_role('region',name='详细定义修改前后',exact=True)).to_contain_text('仅结果未知时显示并启用查询按钮')
                    saved=store.get(pid)
                    assert saved['items']==original['items'] and status(saved)['status']!='passed'
                    await page.screenshot(path=str(output/'detail-change-preview.png'),full_page=True)
                    report['checks'].append('手工详细属性修改进入精确前后预览；保存答案/预览不写入需求、不关闭检查项')
                    await panel.get_by_role('button',name='确认修改并复检',exact=True).click()
                    dialog=page.get_by_role('dialog',name='核对本次任务',exact=True)
                    await expect(dialog).to_be_visible()
                    changed=store.get(pid)
                    assert CONTRADICTION not in changed['items'][0]['detail_contract']['interactions'][0]['preconditions']
                    assert status(changed)['status']!='passed' and not provider.received
                    await dialog.get_by_role('button',name=re.compile('授权本次范围并运行|运行本次任务')).click()
                    await expect(panel.get_by_text('可以继续生成 PRD。',exact=True)).to_be_visible(timeout=30000)
                    assert status(store.get(pid))['status']=='passed' and len(provider.received)==1
                    assert store.get(pid)['prewrite_review']['resolved_count']==1
                    await page.screenshot(path=str(output/'after-recheck.png'),full_page=True)
                    report['checks'].append('一次确认写入已展示详细修改；明确运行复检后才通过，原检查卡保留已解决记录')
                    assert not report['routing_errors'] and not report['page_errors']
                    report['status']='passed'
                except Exception as error:
                    report.update(status='failed',error=str(error),traceback=traceback.format_exc())
                    await page.screenshot(path=str(output/'failure.png'),full_page=True)
                    (output/'visible-page.txt').write_text(await page.locator('body').inner_text(),encoding='utf-8')
                finally:
                    report['fixture_provider_requests']=len(provider.received)
                    report['static_index_sha256']=hashlib.sha256((build/'index.html').read_bytes()).hexdigest()
                    (output/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
                    await context.close();await browser.close()
    print(json.dumps({k:report.get(k) for k in ('status','checks','error','fixture_provider_requests')},ensure_ascii=False))
    if report['status']!='passed':raise SystemExit(1)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--static-build',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    asyncio.run(run(parser.parse_args()))
