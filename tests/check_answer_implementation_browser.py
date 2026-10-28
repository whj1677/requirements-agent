"""Storage-cloud answer implementation UI over isolated ASGI; no listener/models.

All browser requests for the sole 8765 origin are intercepted. Synthetic source
text and chapter metadata are fixtures, never real-model or business acceptance.
"""
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tests.check_decision_document_replay import (ORIGIN, REQ, RULE, Q_PERMISSION, Q_TIME,
    PERMISSION, TIMING, isolated_app, seed_storage_case, write)


def question(project, qid):
    return next(q for q in project['questions'] if q['id'] == qid)


async def wait_saved(store, pid, predicate, message):
    for _ in range(100):
        value = store.get(pid)
        if predicate(value): return value
        await asyncio.sleep(.1)
    raise AssertionError(message)


async def enter(page, initial, expect, *, homepage=False):
    await page.goto(ORIGIN, wait_until='networkidle')
    await page.locator('aside.sidebar button[title="' + initial['name'] + '"]').click()
    work = page.locator('.connected-workspace:visible')
    if homepage:
        await expect(work.get_by_role('button', name='和助手聊需求', exact=True)).to_have_attribute('aria-pressed', 'true')
        await expect(work.get_by_role('textbox', name='你的想法', exact=True)).to_be_visible()
        await expect(page.locator('.project-workspace:not([hidden]) details.connected-details')).not_to_have_attribute('open', '')
    table = work.get_by_role('table', name='需求确认表', exact=True)
    if not await table.is_visible():
        await work.get_by_role('button', name='2 完善需求', exact=True).click()
    await expect(table).to_be_visible()
    return work, table


async def choose(row, title, expect, field='statement'):
    target = row.locator('.answer-implementation-target').filter(has=row.page.get_by_role('combobox', name=title+' 答案落文位置', exact=True))
    await target.get_by_role('combobox').select_option(field)
    await expect(target.locator('blockquote')).to_be_visible()
    check = target.get_by_role('checkbox', name='我已核对此原文表达了答案', exact=True)
    await expect(check).not_to_be_checked()
    await check.check()
    await expect(check).to_be_checked()


async def table_case(page, store, initial, output, report, expect):
    pid = initial['id']
    work, table = await enter(page, initial, expect, homepage=True)
    assert store.get(pid) == initial
    report['checks'].append('默认助手首页、你的想法和收起文档详情入口保持可用；手工进入表格不写后端')
    qrow = table.locator('tr[data-cell-key="question-'+Q_PERMISSION+'"]')
    trow = table.locator('tr[data-cell-key="question-'+Q_TIME+'"]')
    await qrow.get_by_role('button', name=PERMISSION, exact=True).click()
    await trow.get_by_role('button', name=TIMING, exact=True).click()
    await expect(qrow.get_by_role('combobox')).to_have_count(2)
    assert await qrow.get_by_role('checkbox').count() == 0
    assert store.get(pid) == initial, 'recommended answer is local draft only'
    report['checks'].append('推荐回答仅填本地答案；两个直接关联条款没有自动选择原文或应用答案')
    await work.get_by_role('button', name='保存并继续核对', exact=True).click()
    pending = await wait_saved(store, pid, lambda p: question(p,Q_PERMISSION).get('status')=='answered', 'answer was not saved')
    assert question(pending,Q_PERMISSION)['understanding_status']=='pending'
    assert question(pending,Q_TIME)['understanding_status']=='pending'
    await expect(table).to_be_visible()
    await expect(work).to_contain_text('2 项回答待落实')
    await expect(work.get_by_role('button', name='AI 补充建议', exact=True)).to_be_visible()
    await qrow.scroll_into_view_if_needed()
    await page.screenshot(path=str(output/'01-pending-saved-in-table.png'))
    report['checks'].append('整表保存无见证时回答/条款已保存且两题pending；停留本表并显示一次AI补充建议入口')

    await choose(qrow,'站点告警查看',expect)
    await choose(qrow,'站点权限边界',expect)
    answer=qrow.get_by_role('textbox',name=question(initial,Q_PERMISSION)['question']+' 我的回答',exact=True)
    await answer.fill(PERMISSION+'核对草稿。')
    assert await qrow.get_by_role('checkbox').count()==0, 'answer edit must discard prior witnesses'
    await answer.fill(PERMISSION)
    await choose(qrow,'站点告警查看',expect)
    await choose(qrow,'站点权限边界',expect)
    statement=table.locator('tr[data-cell-key="requirement-'+REQ+'-statement"]').get_by_role('textbox')
    original=next(i['statement'] for i in initial['items'] if i['id']==REQ)
    await statement.fill(original+'临时本地编辑。')
    req_target=qrow.locator('.answer-implementation-target').filter(has=page.get_by_role('combobox',name='站点告警查看 答案落文位置',exact=True))
    rule_target=qrow.locator('.answer-implementation-target').filter(has=page.get_by_role('combobox',name='站点权限边界 答案落文位置',exact=True))
    await expect(req_target.get_by_role('checkbox')).not_to_be_checked()
    await expect(rule_target.get_by_role('checkbox')).to_be_checked()
    await statement.fill(original)
    await choose(qrow,'站点告警查看',expect)
    await work.get_by_role('button',name='保存草稿',exact=True).click()
    unchanged=store.get(pid)
    await page.reload(wait_until='networkidle')
    work,table=await enter(page,initial,expect)
    qrow=table.locator('tr[data-cell-key="question-'+Q_PERMISSION+'"]')
    await expect(qrow.get_by_role('checkbox')).to_have_count(2)
    for check in await qrow.get_by_role('checkbox').all(): await expect(check).to_be_checked()
    assert store.get(pid)==unchanged
    report['checks'].append('编辑答案清除本题见证；修改所选字段清除该引用；草稿reload保留新人工选择且无后端写入')
    await qrow.scroll_into_view_if_needed()
    await page.screenshot(path=str(output/'02-two-original-quotes-checked.png'))
    await work.get_by_role('button',name='保存并继续核对',exact=True).click()
    saved=await wait_saved(store,pid,lambda p:question(p,Q_PERMISSION).get('understanding_status')=='applied','two-target original reuse not applied')
    assert question(saved,Q_TIME)['understanding_status']=='pending'
    for iid in (REQ,RULE):
        actual=next(i for i in saved['items'] if i['id']==iid);before=next(i for i in initial['items'] if i['id']==iid)
        assert actual['statement']==before['statement'] and actual['behavior']==before['behavior']
    assert question(saved,Q_PERMISSION)['applied_item_ids']==sorted([REQ,RULE])
    assert '资料未说明' in next(i for i in saved['items'] if i['id']==REQ)['behavior']['flow']
    await expect(table).to_be_visible()
    await expect(work).to_contain_text('1 项回答待落实')
    report['checks'].append('两个直接targets显式核对原句后权限应用成功，正文行为未改字；另一时点未知仍pending且保留原文')
    await page.screenshot(path=str(output/'03-permission-applied-time-pending.png'))

    # Simulate a separate-page human answer save: old v2 audit records stay,
    # but service-invalidated current application identities are removed.
    with store.edit(pid,saved['revision'],'合成跨页答案变化前置') as (p,_):
        question(p,Q_PERMISSION).update(answer=PERMISSION+'仅本地新回答。',understanding_status='pending',applied_item_ids=[])
    await page.reload(wait_until='networkidle')
    work,table=await enter(page,initial,expect)
    qrow=table.locator('tr[data-cell-key="question-'+Q_PERMISSION+'"]')
    assert await qrow.get_by_role('checkbox').count()==0, 'pending old v2 records must never recheck automatically'
    report['checks'].append('跨页答案变化保留旧v2审计记录时，新表不会自动勾选或重用旧见证')


def chapter_fixture(store):
    from app.core import now, brief_hash
    from app.contracts import profile
    p=seed_storage_case(store,suffix='-CHAPTER')
    with store.edit(p['id'],p['revision'],'合成章节原文核对前置') as (p,_):
        p['name']='合成大型储能云：章节原文复用核对'
        question(p,Q_PERMISSION).update(status='answered',answer=PERMISSION,understanding_status='pending')
        doc=dict(id='DOC-STORAGE-CLOSURE',project_id=p['id'],draft_revision=p['revision'],document_version=1,
            requirement_name=p['name'],brief_hash=brief_hash(p),created=now(),status='draft',
            content=dict(document_type='prd',content_profile_id=profile('prd')['id'],
                content_profile_version=profile('prd')['version'],title=p['name'],
                coverage=[],reference_mapping=[],sections=[dict(section_id='PRD-STORAGE-PERM',
                title='站点告警权限',level=1,parent_section_id=None,
                blocks=[dict(kind='requirement',ref_ids=[REQ+'-CHAPTER'],text=PERMISSION)])]),
            item_snapshot=copy.deepcopy(p['items']),question_snapshot=copy.deepcopy(p['questions']),source_snapshot=copy.deepcopy(p['sources']),
            delivery_item_ids=[REQ+'-CHAPTER',RULE+'-CHAPTER'],coverage={},reference_mapping=[])
        p['documents']={'prd':doc}
    p=store.get(p['id'])
    store.record(p['id'],'run',dict(stage='change',status='awaiting_user',revision=p['revision'],result_applied=True,
        calls=0,events=[],created=now(),completed=now(),discussion_target=dict(kind='document',id=doc['id'],section_id='PRD-STORAGE-PERM'),
        discussion_result=dict(question_ids=[Q_PERMISSION],item_ids=[])))
    return p


async def chapter_case(page,store,initial,output,report,expect):
    await page.goto(ORIGIN,wait_until='networkidle')
    await page.locator('aside.sidebar button[title="'+initial['name']+'"]').click()
    workspace=page.locator('.project-workspace:not([hidden])');work=workspace.locator('.connected-workspace')
    await work.get_by_role('navigation',name='需求工作流程').get_by_role('button',name=re.compile(r'^3\s*确认交付$')).click()
    await work.get_by_role('button',name='查看与核对正式文档',exact=True).click()
    heading=page.locator('[id="DOC-STORAGE-CLOSURE-PRD-STORAGE-PERM"]')
    await heading.locator('..').get_by_role('button',name='讨论此章节',exact=True).click()
    dialog=page.get_by_role('dialog',name='章节讨论',exact=True)
    await expect(dialog.get_by_role('textbox',name='继续补充这次讨论',exact=True)).to_have_value(PERMISSION)
    before=store.get(initial['id']);events=len(report['api_requests'])
    await dialog.get_by_role('button',name='核对原文的落实位置',exact=True).click()
    await expect(dialog.get_by_text('答案落实到哪里',exact=True)).to_be_visible()
    assert store.get(initial['id'])==before
    assert not [r for r in report['api_requests'][events:] if r['method']!='GET']
    assert await dialog.get_by_role('checkbox').count()==0
    report['checks'].append('章节沿用原文入口先展示共享原句选择，不直接应用、不写预览或条款')
    await choose(dialog,'站点告警查看',expect)
    await choose(dialog,'站点权限边界',expect)
    await page.screenshot(path=str(output/'04-chapter-original-quotes.png'))
    await dialog.get_by_role('button',name='预览本次落实核对',exact=True).click()
    await expect(dialog.get_by_role('heading',name='请核对回答与原文的对应',exact=True)).to_be_visible()
    preview_event=next(r for r in reversed(report['api_requests']) if r['path'].endswith('/manual-preview'))
    assert {w['item_id'] for w in preview_event['body']['answer_implementations'][Q_PERMISSION]}=={REQ+'-CHAPTER',RULE+'-CHAPTER'}
    assert not preview_event['response'].get('implementation_pending_ids')
    assert question(store.get(initial['id']),Q_PERMISSION)['understanding_status']=='pending'
    await dialog.get_by_role('button',name='确认本次落实核对',exact=True).click()
    saved=await wait_saved(store,initial['id'],lambda p:question(p,Q_PERMISSION).get('understanding_status')=='applied','chapter original reuse was not applied')
    for iid in (REQ+'-CHAPTER',RULE+'-CHAPTER'):
        actual=next(i for i in saved['items'] if i['id']==iid);old=next(i for i in initial['items'] if i['id']==iid)
        assert actual['statement']==old['statement'] and actual['behavior']==old['behavior']
    report['checks'].append('章节核对全部直接targets后真实manual-preview传见证；显式确认才应用，原文行为未改字')
    await page.screenshot(path=str(output/'05-chapter-after-confirmation.png'))


async def run(args, exercise=None):
    output,build=args.output.resolve(),args.static_build.resolve()
    assert output.is_relative_to(ROOT/'evidence') and not output.exists() and (build/'index.html').is_file()
    output.mkdir(parents=True)
    report=dict(status='running',checks=[],api_requests=[],page_errors=[],routing_errors=[],served_static={},
        model_calls=0,listener_started=False,daily_data_access=False,business_acceptance='NOT_RUN',static_build=str(build))
    with tempfile.TemporaryDirectory(prefix='ra-witness-browser-') as scratch:
        os.environ['RA_DATA_DIR']=str(Path(scratch)/'import-data')
        import httpx
        from playwright.async_api import async_playwright,expect
        app,provider=isolated_app(Path(scratch));store=app.state.store
        table=seed_storage_case(store);chapter=chapter_fixture(store)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=ORIGIN) as client:
            async with async_playwright() as pw:
                browser=await pw.chromium.launch(headless=True)
                context=await browser.new_context(viewport=dict(width=1600,height=980))
                async def dispatch(route):
                    request,url=route.request,urlsplit(route.request.url)
                    try:
                        assert url.netloc=='127.0.0.1:8765','Foreign request forbidden'
                        if url.path.startswith('/api/'):
                            assert request.method=='GET' or request.method=='POST' and url.path.endswith(('/refinement-confirmation','/decision-review/answer','/decision-review/manual-preview','/decision-review/commit')),'Unexpected write '+url.path
                            headers={k:v for k,v in request.headers.items() if k.lower() not in ('host','content-length','connection','accept-encoding')}
                            headers['host']='127.0.0.1:8765'
                            response=await client.request(request.method,url.path+('?' +url.query if url.query else ''),headers=headers,content=request.post_data_buffer)
                            event=dict(method=request.method,path=url.path,status=response.status_code)
                            if request.post_data:event.update(body=request.post_data_json,response=response.json())
                            report['api_requests'].append(event)
                            await route.fulfill(status=response.status_code,body=response.content,content_type=response.headers.get('content-type','application/json'))
                        else:
                            path=(build/(url.path.lstrip('/') or 'index.html')).resolve()
                            assert path.is_relative_to(build) and path.is_file(),str(path)
                            report['served_static'][url.path]=hashlib.sha256(path.read_bytes()).hexdigest()
                            await route.fulfill(status=200,body=path.read_bytes(),content_type=mimetypes.guess_type(path.name)[0] or 'application/octet-stream')
                    except Exception as exc:
                        report['routing_errors'].append(str(exc));await route.abort()
                await context.route('**/*',dispatch)
                page=await context.new_page();page.set_default_timeout(12000)
                page.on('pageerror',lambda e:report['page_errors'].append(str(e)))
                try:
                    if exercise:
                        await exercise(page,store,table,chapter,output,report,expect)
                    else:
                        await table_case(page,store,table,output,report,expect)
                        await chapter_case(page,store,chapter,output,report,expect)
                    assert provider.calls==0 and not report['routing_errors'] and not report['page_errors']
                    report['status']='passed'
                except Exception as exc:
                    report.update(status='failed',failure=str(exc),traceback=traceback.format_exc())
                    await page.screenshot(path=str(output/'failure.png'),full_page=True)
                finally:
                    report['model_calls']=provider.calls;write(output/'result.json',report)
                    await context.close();await browser.close()
    summary={k:report.get(k) for k in ('status','checks','model_calls')}
    if report.get('failure'):summary['failure']=report['failure'].split('\n')[0]
    print(json.dumps(summary,ensure_ascii=False))
    if report['status']!='passed':raise SystemExit(1)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--static-build',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    asyncio.run(run(parser.parse_args()))
