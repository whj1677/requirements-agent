"""Rev27 opinion saving and explicit review planning; isolated ASGI only."""
from __future__ import annotations
import argparse,asyncio,copy,hashlib,json,mimetypes,os,re,sys,tempfile,traceback
from pathlib import Path
from urllib.parse import urlsplit
ROOT=Path(__file__).resolve().parents[1];WORK=ROOT/'work'/'human-real-20261031'
sys.path.insert(0,str(ROOT))
IMPORT=tempfile.TemporaryDirectory(prefix='ra-dispute-import-')
os.environ['RA_DATA_DIR']=str(Path(IMPORT.name)/'data');os.environ['RA_ACCESS_TOKEN']='off'
import httpx
from playwright.async_api import async_playwright,expect
from app.core import dumps,now
from app.main import create_app
from tests.check_human_real_recovery_browser import ORIGIN,PERSISTED_KEYS,RejectingProvider,open_table

REASON='当前条款已标注告警处理归属与本次操作结果，并明确T4按服务端结果落位，请按当前版本重新核对。'
SECOND_REASON='第二个检查项的本地意见，尚未提交。'
OTHER_REASON='对照项目相同检查编号的本地意见。'

def seed(store,fixture,*,pid=None):
    keys=set(PERSISTED_KEYS)|{'id','name','revision','created','updated_at','context_confirmations',
        'refinement_completion','requirement_confirmation_mode','document_update_needed','stale_document_kinds',
        'prewrite_review','prewrite_responses','prewrite_corrections','requirement_relations'}
    p={k:copy.deepcopy(v) for k,v in fixture.items() if k in keys}
    assert p['revision']==27
    if pid:p.update(id=pid,name='隔离对照项目：异议草稿身份')
    with store.connect() as db:
        db.execute('INSERT INTO projects VALUES(?,?,?)',(p['id'],p['revision'],dumps(p)))
        db.execute('INSERT INTO revisions VALUES(?,?,?,?,?)',(p['id'],p['revision'],dumps(p),'冻结授权合成rev27',now()))
    return p

async def show_card(page,pid,fid):
    work=await open_table(page,pid)
    await work.locator('.refinement-view-switch').get_by_role('button',name=re.compile('^成文检查')).click()
    details=work.locator('.prewrite-optional-details')
    if await details.get_attribute('open') is None:await details.locator(':scope > summary').click()
    await work.get_by_role('combobox',name='定位到成文检查项',exact=True).select_option(fid)
    guard=page.get_by_role('dialog',name='未保存的修改',exact=True)
    if await guard.is_visible():await guard.get_by_role('button',name='保存后离开',exact=True).click()
    card=work.locator('[data-finding-id="'+fid+'"]')
    await expect(card).to_be_visible()
    return work,card

async def run(args):
    build,output,snapshot=(p.resolve() for p in (args.static_build,args.output,args.snapshot))
    assert (build/'index.html').is_file() and output.is_relative_to(WORK) and not output.exists()
    assert snapshot.is_relative_to(WORK);output.mkdir(parents=True)
    raw=snapshot.read_bytes();fixture=json.loads(raw)
    opened=[f['id'] for f in fixture['prewrite_review']['findings'] if f['status']!='resolved']
    resolved=next(f['id'] for f in fixture['prewrite_review']['findings'] if f['status']=='resolved')
    assert len(opened)>=2
    report=dict(status='running',static_build=str(build),snapshot_sha256=hashlib.sha256(raw).hexdigest(),
        listener_started=False,daily_data_access=False,provider_attempts=0,responses=[],plans=[],task_submissions=[],
        invalid_api_probes=[],checks=[],failures=[],page_errors=[],routing_errors=[])
    with tempfile.TemporaryDirectory(prefix='ra-dispute-browser-') as scratch:
        async with async_playwright() as pw:
            browser=await pw.chromium.launch(headless=True)
            for scenario in ('main','stale','busy'):
                folder=Path(scratch)/scenario;provider=RejectingProvider(folder)
                app=create_app(folder/'data',provider=provider,env_path=folder/'absent.env');store=app.state.store
                initial=seed(store,fixture);pid=initial['id']
                other=seed(store,fixture,pid='P-DISPUTE-OTHER')['id']
                if scenario=='busy':store.record(pid,'user_task',dict(action='prewrite',label='合成忙态',status='running',
                    calls=0,completed_steps=0,max_calls=None,cost=None,created=now(),input_revision=27,run_ids=[],
                    stages=['review'],step_states=[dict(stage='review',status='running')],retry_input={}))
                context=await browser.new_context(viewport=dict(width=1366,height=768),service_workers='block')
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=ORIGIN,timeout=30) as client:
                    async def dispatch(route):
                        req,url=route.request,urlsplit(route.request.url)
                        try:
                            assert url.scheme=='http' and url.netloc=='127.0.0.1:8765'
                            if url.path.startswith('/api/'):
                                event=None
                                if req.method=='POST' and '/prewrite-review/findings/' in url.path and url.path.endswith('/response'):
                                    event=dict(scenario=scenario,path=url.path,input=req.post_data_json);report['responses'].append(event)
                                elif req.method=='POST' and url.path.endswith('/actions/plan'):
                                    event=dict(scenario=scenario,input=req.post_data_json);report['plans'].append(event)
                                elif req.method not in ('GET','HEAD'):
                                    report['task_submissions'].append(dict(scenario=scenario,path=url.path))
                                    await route.fulfill(status=503,content_type='application/json',body='{"detail":"Operation forbidden in offline dispute checks"}');return
                                headers={k:v for k,v in req.headers.items() if k.lower() not in ('host','content-length','connection','accept-encoding')}
                                headers['host']='127.0.0.1:8765'
                                response=await client.request(req.method,url.path+('?' +url.query if url.query else ''),headers=headers,content=req.post_data_buffer)
                                if event is not None:event.update(status=response.status_code,response=response.json())
                                await route.fulfill(status=response.status_code,body=response.content,content_type=response.headers.get('content-type','application/json'))
                            else:
                                path=(build/(url.path.lstrip('/') or 'index.html')).resolve();assert path.is_relative_to(build) and path.is_file()
                                await route.fulfill(status=200,body=path.read_bytes(),content_type=mimetypes.guess_type(path.name)[0] or 'application/octet-stream')
                        except Exception as exc:report['routing_errors'].append(dict(scenario=scenario,error=str(exc)));await route.abort()
                    await context.route('**/*',dispatch)
                    page=await context.new_page();page.set_default_timeout(12000)
                    page.on('pageerror',lambda error:report['page_errors'].append(str(error)))
                    try:
                        await page.goto(ORIGIN+'/',wait_until='networkidle')
                        work,card=await show_card(page,pid,opened[0])
                        text=card.get_by_role('textbox')
                        save=card.get_by_role('button',name=re.compile('保存.*(?:意见|异议|说明)'))
                        if scenario=='busy':
                            await expect(text).to_be_disabled();await expect(save).to_be_disabled()
                            for button in await card.get_by_role('button',name=re.compile('复检|重新检查')).all():await expect(button).to_be_disabled()
                            report['checks'].append('Busy: opinion editing, saving and explicit recheck are locked.')
                        elif scenario=='stale':
                            await text.fill(REASON)
                            with store.edit(pid,store.get(pid)['revision'],'隔离注入并发版本变化') as (_p,_db):pass
                            await save.click()
                            await expect(card.get_by_role('textbox')).to_have_value(REASON)
                            await page.wait_for_timeout(250)
                            calls=[r for r in report['responses'] if r['scenario']=='stale']
                            assert len(calls)==1 and calls[0]['status']==409
                            assert not store.get(pid).get('prewrite_responses')
                            report['checks'].append('Actual stale-revision 409 retains the complete typed opinion without retrying or review planning.')
                        else:
                            # Switch cards immediately: unmounting must not drop an unsaved local draft.
                            await text.fill(REASON)
                            work,second=await show_card(page,pid,opened[1]);await second.get_by_role('textbox').fill(SECOND_REASON)
                            work,card=await show_card(page,pid,opened[0]);await expect(card.get_by_role('textbox')).to_have_value(REASON)
                            # Same finding ID in a distinct isolated project must own a distinct draft.
                            _other,other_card=await show_card(page,other,opened[0]);await other_card.get_by_role('textbox').fill(OTHER_REASON)
                            work,card=await show_card(page,pid,opened[0]);await expect(card.get_by_role('textbox')).to_have_value(REASON)
                            await page.reload(wait_until='networkidle')
                            work,card=await show_card(page,pid,opened[0]);text=card.get_by_role('textbox');save=card.get_by_role('button',name=re.compile('保存.*(?:意见|异议|说明)'))
                            await expect(text).to_have_value(REASON)
                            assert not report['responses'] and not report['plans']
                            report['checks'].append('Card switches, project switches with matching finding IDs, and reload preserve separate local opinion drafts without a write/model call.')
                            await text.fill('   ')
                            if await save.is_enabled():await save.click()
                            assert not report['responses'],'Empty opinion must not be submitted'
                            await text.fill('中'*6001)
                            report['ui_long_reason']=dict(requested_characters=6001,visible_characters=len(await text.input_value()),max_length=await text.get_attribute('maxlength'))
                            if len(await text.input_value())>6000:
                                if await save.is_enabled():await save.click()
                                assert not [r for r in report['responses'] if r.get('status')==200]
                            await text.fill(REASON)
                            session=(await client.get('/api/session')).json()
                            body=dict(expected_revision=27,review_id=initial['prewrite_review']['id'],input_hash=initial['prewrite_review']['input_hash'],mode='dispute')
                            headers={'Origin':ORIGIN,'X-CSRF-Token':session['csrf']}
                            for bad in ('','中'*6001):
                                response=await client.post(f'/api/projects/{pid}/prewrite-review/findings/{opened[0]}/response',json=dict(body,reason=bad),headers=headers)
                                assert response.status_code==400,response.text
                                report['invalid_api_probes'].append(dict(reason_length=len(bad),status=response.status_code))
                            before_success=len(report['responses'])
                            await save.click()
                            await page.wait_for_timeout(400)
                            successful=[r for r in report['responses'][before_success:] if r.get('status')==200]
                            assert len(successful)==1 and successful[0]['input']==dict(body,reason=REASON),successful
                            written=store.get(pid)
                            assert written['items']==initial['items'] and written['documents']==initial['documents']
                            assert written['prewrite_review']['id']==initial['prewrite_review']['id']
                            assert next(f for f in written['prewrite_review']['findings'] if f['id']==opened[0])['status']=='open'
                            current=(await client.get(f'/api/projects/{pid}')).json()
                            assert current['prewrite_review']['current'] is False,'Saving a dispute must not grant a fresh review pass'
                            assert not report['plans'] and provider.calls==0
                            report['checks'].append('One real response API succeeds with exact identity and dispute mode; findings remain open, current review expires, and requirements/AC/documents remain unchanged.')
                            await page.screenshot(path=str(output/'opinion-saved-no-plan.png'))
                            work,card=await show_card(page,pid,opened[0])
                            review=card.get_by_role('button',name=re.compile('复检|重新检查'))
                            unsaved=REASON+'补充：尚未保存的新说明。'
                            await card.get_by_role('textbox').fill(unsaved)
                            if await review.count() and await review.is_enabled():await review.click()
                            await page.wait_for_timeout(200)
                            if report['plans']:
                                report['failures'].append(dict(scenario='main_unsaved_edit',
                                    error='A newly edited but unsaved opinion opened a review plan while the API still stores the previous reason.'))
                                unexpected=page.get_by_role('dialog',name='核对本次任务',exact=True)
                                await expect(unexpected).to_be_visible()
                                await page.screenshot(path=str(output/'unsaved-edit-unexpected-plan.png'))
                                await unexpected.get_by_role('button',name='返回保留输入',exact=True).click()
                            await expect(card.get_by_role('textbox')).to_have_value(unsaved)
                            assert store.get(pid)['prewrite_responses'][-1]['reason']==REASON
                            if not report['plans']:report['checks'].append('Editing a saved opinion keeps the new text local and blocks recheck until it is saved or restored; the saved API reason is unchanged.')
                            await card.get_by_role('textbox').fill(REASON)
                            prior_plans=len(report['plans'])
                            await review.click()
                            dialog=page.get_by_role('dialog',name='核对本次任务',exact=True);await expect(dialog).to_be_visible()
                            assert len(report['plans'])==prior_plans+1 and report['plans'][-1]['input']['action']=='prewrite'
                            await dialog.get_by_role('button',name='返回保留输入',exact=True).click()
                            report['checks'].append('Only the separate explicit recheck click opens the existing review plan; cancellation creates no task/model request.')
                            work,card=await show_card(page,pid,resolved)
                            for element in await card.get_by_role('textbox').all():
                                assert await element.is_disabled() or await element.get_attribute('readonly') is not None
                            for button in await card.get_by_role('button',name=re.compile('保存.*(?:意见|异议|说明)|复检|重新检查')).all():await expect(button).to_be_disabled()
                            report['checks'].append('Resolved finding is read-only and cannot save another opinion or recheck from this card.')
                            fresh_context=await browser.new_context(viewport=dict(width=1366,height=768),service_workers='block')
                            await fresh_context.route('**/*',dispatch)
                            fresh=await fresh_context.new_page();fresh.set_default_timeout(12000)
                            fresh.on('pageerror',lambda error:report['page_errors'].append(str(error)))
                            await fresh.goto(ORIGIN+'/',wait_until='networkidle')
                            draft_key='ra-prewrite-response-'+pid+'-'+opened[0]
                            assert await fresh.evaluate('key=>sessionStorage.getItem(key)',draft_key) is None
                            _fresh_work,fresh_card=await show_card(fresh,pid,opened[0])
                            await expect(fresh_card.get_by_role('textbox')).to_have_value(REASON)
                            await expect(fresh_card.get_by_role('button',name=re.compile('复检|重新检查'))).to_be_enabled()
                            report['checks'].append('Fresh browser with no local draft initializes from the actual saved server reason without a new API write or plan.')
                            await fresh_card.get_by_role('textbox').fill('')
                            await fresh_card.get_by_role('textbox').blur();await fresh.wait_for_timeout(550)
                            assert await fresh.evaluate('key=>sessionStorage.getItem(key)',draft_key)=='""'
                            await fresh.reload(wait_until='networkidle')
                            _fresh_work,fresh_card=await show_card(fresh,pid,opened[0])
                            await expect(fresh_card.get_by_role('textbox')).to_have_value('')
                            recheck=fresh_card.get_by_role('button',name=re.compile('复检|重新检查'))
                            assert await recheck.count()==0 or not await recheck.is_enabled()
                            assert store.get(pid)['prewrite_responses'][-1]['reason']==REASON
                            assert len(report['plans'])==prior_plans+1
                            await fresh.screenshot(path=str(output/'explicit-empty-draft-retained.png'))
                            report['checks'].append('An explicitly empty local draft survives reload and takes precedence over the saved server reason; recheck is unavailable and the server reason remains unchanged.')
                            await fresh_context.close()
                        assert not report['task_submissions'] and provider.calls==0
                        await page.screenshot(path=str(output/(scenario+'-final.png')))
                    except Exception as exc:
                        report['failures'].append(dict(scenario=scenario,error=str(exc),traceback=traceback.format_exc()))
                        await page.screenshot(path=str(output/(scenario+'-failure.png')),full_page=True)
                        (output/(scenario+'-failure.txt')).write_text(await page.locator('body').inner_text(),encoding='utf-8')
                    finally:report['provider_attempts']+=provider.calls;await context.close()
            await browser.close()
    report['status']='failed' if any(report[k] for k in ('failures','page_errors','routing_errors','provider_attempts','task_submissions')) else 'passed'
    (output/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(status=report['status'],checks=len(report['checks']),failures=[dict(scenario=r['scenario'],error=r['error'].split('\n')[0]) for r in report['failures']],provider_attempts=report['provider_attempts'],responses=len(report['responses']),plans=len(report['plans'])),ensure_ascii=False))
    if report['status']!='passed':raise SystemExit(1)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--static-build',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--snapshot',type=Path,default=WORK/'documents-generated-project.json')
    asyncio.run(run(parser.parse_args()))
