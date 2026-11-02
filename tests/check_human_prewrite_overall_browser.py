"""Captured rev23 batch-confirmation boundary; isolated ASGI and no models."""
from __future__ import annotations
import argparse, asyncio, copy, hashlib, json, mimetypes, os, re, sys, tempfile, traceback
from pathlib import Path
from urllib.parse import urlsplit
ROOT=Path(__file__).resolve().parents[1]
WORK=ROOT/'work'/'human-real-20261031'
sys.path.insert(0,str(ROOT))
IMPORT=tempfile.TemporaryDirectory(prefix='ra-overall-import-')
os.environ['RA_DATA_DIR']=str(Path(IMPORT.name)/'data')
os.environ['RA_ACCESS_TOKEN']='off'
import httpx
from playwright.async_api import async_playwright,expect
from app.core import dumps,now
from app.main import create_app
from tests.check_human_real_recovery_browser import ORIGIN,PERSISTED_KEYS,RejectingProvider,open_table

RID='REQ-34e5293bcc6e424d'
CID='REQ-4fd1e9e90e504e1b'
AC7='AC-ef349cd9564546dd'
AC7_CANDIDATE='AC-de36585eeb424f66'

class DirectRecheckVerified(Exception):
    pass

def seed(store,fixture):
    keys=set(PERSISTED_KEYS)|{'id','name','revision','created','updated_at','context_confirmations',
        'refinement_completion','requirement_confirmation_mode','document_update_needed','stale_document_kinds',
        'prewrite_review','prewrite_responses','prewrite_corrections','requirement_relations'}
    p={k:copy.deepcopy(v) for k,v in fixture.items() if k in keys}
    assert p['revision']>=23
    assert any(f['id']=='PW-assessment' and not f.get('related_refs') and not f.get('question_id') for f in p['prewrite_review']['findings'])
    with store.connect() as db:
        db.execute('INSERT INTO projects VALUES(?,?,?)',(p['id'],p['revision'],dumps(p)))
        db.execute('INSERT INTO revisions VALUES(?,?,?,?,?)',(p['id'],p['revision'],dumps(p),'冻结授权合成rev23',now()))
    return p

async def run(args):
    build,output,snapshot=(p.resolve() for p in (args.static_build,args.output,args.snapshot))
    assert (build/'index.html').is_file() and output.is_relative_to(WORK) and not output.exists()
    assert snapshot.is_relative_to(WORK)
    output.mkdir(parents=True)
    raw=snapshot.read_bytes();fixture=json.loads(raw)
    assert fixture['revision']==23 and fixture['refinement_completion']['complete']
    if args.scenario.startswith('direct'):
        accepted=args.accepted_from.resolve()
        assert accepted.is_relative_to(WORK)
        fixture=copy.deepcopy(json.loads(accepted.read_text(encoding='utf-8'))['confirmation_requests'][0]['response']['project'])
        fixture['prewrite_review']['findings']=[f for f in fixture['prewrite_review']['findings'] if f['id']=='PW-assessment']
        fixture.pop('refinement_completion',None)
        if args.scenario=='direct_completion':
            fixture['refinement_completion']=dict(mode='prewrite',complete=True,applied_revision=fixture['revision'],
                covered_requirement_ids=[RID],candidate_ids=[],answers={})
        if args.scenario in ('direct_rule_missing','direct_rule_empty'):
            rule=dict(id='RULE-EMPTY-BEHAVIOR',kind='rule',title='合成既答规则',statement='保留已保存规则原文。',
                applies_to='to_be',selection_status='selected',related_refs=[RID],source_refs=[])
            if args.scenario=='direct_rule_empty':rule['behavior']={}
            fixture['items'].append(rule)
            fixture['questions'].append(dict(id='Q-EMPTY-BEHAVIOR',question='是否沿用原规则？',answer='沿用原规则。',
                status='answered',blocking=False,understanding_status='applied',related_refs=[rule['id']],source_refs=[]))
    if args.scenario=='other_rootless':
        fixture['prewrite_review']['findings'].append(dict(id='PW-UNMAPPED-REAL',severity='blocker',status='open',
            message='合成真实未定位问题，不能当总评跳过。',suggested_resolution='先核对实际问题位置。',related_refs=[],quotes=[]))
    report=dict(status='running',expect_blocked=args.expect_blocked,scenario=args.scenario,static_build=str(build),
        snapshot_sha256=hashlib.sha256(raw).hexdigest(),listener_started=False,daily_data_access=False,
        provider_attempts=0,confirmation_requests=[],plans=[],task_submissions=[],blocked_writes=[],
        checks=[],failures=[],page_errors=[],routing_errors=[])
    with tempfile.TemporaryDirectory(prefix='ra-overall-') as scratch:
        folder=Path(scratch);provider=RejectingProvider(folder)
        app=create_app(folder/'data',provider=provider,env_path=folder/'absent.env');store=app.state.store
        initial=seed(store,fixture);pid=initial['id']
        if args.scenario=='busy':
            store.record(pid,'user_task',dict(action='prewrite',label='隔离忙态夹具',status='running',calls=0,
                completed_steps=0,max_calls=None,cost=None,created=now(),input_revision=initial['revision'],
                run_ids=[],stages=['review'],step_states=[dict(stage='review',status='running')],retry_input={}))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=ORIGIN,timeout=60) as client:
            async with async_playwright() as pw:
                browser=await pw.chromium.launch(headless=True)
                context=await browser.new_context(viewport=dict(width=1366,height=768),service_workers='block')
                async def dispatch(route):
                    req,url=route.request,urlsplit(route.request.url)
                    try:
                        assert url.scheme=='http' and url.netloc=='127.0.0.1:8765'
                        if url.path.startswith('/api/'):
                            event=None
                            if req.method=='POST' and url.path.endswith('/refinement-confirmation'):
                                event=dict(input=req.post_data_json);report['confirmation_requests'].append(event)
                                if args.scenario.startswith('direct'):
                                    event.update(status=200,response=dict(confirmed=False,message='隔离验证：无变化路径不应提交空确认。'))
                                    await route.fulfill(status=200,content_type='application/json',body=json.dumps(event['response'],ensure_ascii=False))
                                    return
                            elif req.method=='POST' and url.path.endswith('/actions/plan'):
                                event=dict(input=req.post_data_json);report['plans'].append(event)
                            elif req.method not in ('GET','HEAD'):
                                key='task_submissions' if url.path.endswith('/actions') or '/runs' in url.path else 'blocked_writes'
                                report[key].append(dict(method=req.method,path=url.path))
                                await route.fulfill(status=503,content_type='application/json',body='{"detail":"Forbidden offline operation"}')
                                return
                            headers={k:v for k,v in req.headers.items() if k.lower() not in ('host','content-length','connection','accept-encoding')}
                            headers['host']='127.0.0.1:8765'
                            response=await client.request(req.method,url.path+('?' +url.query if url.query else ''),content=req.post_data_buffer,headers=headers)
                            if event is not None:event.update(status=response.status_code,response=response.json())
                            await route.fulfill(status=response.status_code,body=response.content,content_type=response.headers.get('content-type','application/json'))
                        else:
                            path=(build/(url.path.lstrip('/') or 'index.html')).resolve()
                            assert path.is_relative_to(build) and path.is_file()
                            await route.fulfill(status=200,body=path.read_bytes(),content_type=mimetypes.guess_type(path.name)[0] or 'application/octet-stream')
                    except Exception as exc:
                        report['routing_errors'].append(str(exc));await route.abort()
                await context.route('**/*',dispatch)
                page=await context.new_page();page.set_default_timeout(15000)
                page.on('pageerror',lambda error:report['page_errors'].append(str(error)))
                try:
                    await page.goto(ORIGIN+'/',wait_until='networkidle')
                    work=await open_table(page,pid)
                    await work.locator('.refinement-view-switch').get_by_role('button',name=re.compile('^成文检查')).click()
                    batch=work.get_by_role('region',name='成文检查确认表',exact=True)
                    await expect(batch.locator('[data-check-id="PW-assessment"]')).to_be_visible()
                    if not args.expect_blocked:
                        await expect(batch.locator('[data-check-id="PW-assessment"]')).to_contain_text('总评待复检')
                        assert await batch.locator('[data-check-id="PW-assessment"] .batch-ready').count()==0
                    if args.scenario=='busy':
                        await expect(batch.locator('.prewrite-batch-footer .primary')).to_be_disabled()
                        await expect(batch.get_by_role('button',name='AI 批量起草全部修改',exact=True)).to_be_disabled()
                        await expect(batch.get_by_role('textbox',name='告警认领、释放与处理记录 确认后验收 7',exact=True)).to_be_disabled()
                        assert not report['confirmation_requests'] and not report['plans'] and store.get(pid)==initial
                        report['checks'].append('Busy fixture disables confirmation, drafting and AC editing without a model request.')
                        raise DirectRecheckVerified
                    if args.scenario.startswith('direct'):
                        button=batch.locator('.prewrite-batch-footer .primary')
                        report['initial_button_label']=await button.inner_text()
                        await button.click()
                        if report['initial_button_label']!='直接重新整批复检':
                            await page.wait_for_timeout(600)
                            raise AssertionError('No business edits: expected direct recheck, actual '+report['initial_button_label']+
                                '; confirmation requests='+str(len(report['confirmation_requests'])))
                        dialog=page.get_by_role('dialog',name='核对本次任务',exact=True)
                        await expect(dialog).to_be_visible()
                        assert not report['confirmation_requests'] and len(report['plans'])==1
                        assert report['plans'][0]['input']['action']=='prewrite'
                        await dialog.get_by_role('button',name='返回保留输入',exact=True).click()
                        assert store.get(pid)==initial
                        report['checks'].append('Only overall assessment and no business changes: original recheck plan opens directly without empty confirmation.')
                        await page.screenshot(path=str(output/'overall-boundary.png'))
                        assert not report['task_submissions'] and provider.calls==0
                        raise DirectRecheckVerified
                    ac7=batch.get_by_role('textbox',name='告警认领、释放与处理记录 确认后验收 7',exact=True)
                    old=await ac7.input_value();assert '仍未甲' in old
                    corrected=old.replace('仍未甲','仍为甲')
                    await ac7.fill(corrected)
                    # Flush the actual local draft writer without a server save.
                    await ac7.blur();await page.wait_for_timeout(550)
                    local=await page.evaluate('key=>JSON.parse(sessionStorage.getItem(key))',pid+'-prewrite-batch')
                    row=local['sheet']['rows'][RID]
                    assert row['candidate_id']==CID and len(row['acceptance_items'])==16
                    chosen=next(ac for ac in row['acceptance_items'] if ac['id']==AC7)
                    assert chosen['statement']==corrected and chosen['candidate_id']==AC7_CANDIDATE
                    report['local_draft_identity']=dict(candidate_id=row['candidate_id'],acceptance_count=len(row['acceptance_items']),ac7=chosen)
                    confirm=batch.get_by_role('button',name='确认全部修改并复检',exact=True)
                    await confirm.click()
                    if args.expect_blocked or args.scenario=='other_rootless':
                        await expect(batch.locator('.prewrite-batch-footer')).to_contain_text('1 项待起草')
                        await expect(batch).to_contain_text('还有 1 个检查点缺少可确认的修改')
                        assert not report['confirmation_requests'] and not report['plans']
                        assert store.get(pid)==initial
                        await expect(ac7).to_have_value(corrected)
                        report['checks'].append('Unmapped actual finding remains blocked; manual AC7 and candidate/16AC identities are retained.' if args.scenario=='other_rootless' else
                            'Reproduced old build: rootless PW-assessment leaves one item forever despite complete candidate and corrected local AC7.')
                    else:
                        dialog=page.get_by_role('dialog',name='核对本次任务',exact=True)
                        await expect(dialog).to_be_visible(timeout=20000)
                        assert len(report['confirmation_requests'])==1 and len(report['plans'])==1
                        call=report['confirmation_requests'][0]
                        assert call['status']==200 and call['response']['confirmed'],call['response']
                        assessment=next(f for f in call['response']['project']['prewrite_review']['findings'] if f['id']=='PW-assessment')
                        assert assessment['status']!='resolved', 'Saving clauses must not automatically approve the overall review'
                        payload=next(row for row in call['input']['rows'] if row['requirement_id']==RID)
                        assert payload['candidate_id']==CID and len(payload['acceptance_items'])==16
                        submitted=next(ac for ac in payload['acceptance_items'] if ac['id']==AC7)
                        assert submitted['statement']==corrected and submitted['candidate_id']==AC7_CANDIDATE
                        assert report['plans'][0]['input']['action']=='prewrite'
                        assert not report['task_submissions'] and provider.calls==0
                        await dialog.get_by_role('button',name='返回保留输入',exact=True).click()
                        report['checks'].append('Explicitly confirmed actual isolated refinement payload, then opened original prewrite plan; cancelled without a model task.')
                    await page.screenshot(path=str(output/'overall-boundary.png'))
                    assert provider.calls==0 and not report['task_submissions'] and not report['blocked_writes']
                except DirectRecheckVerified:
                    pass
                except Exception as exc:
                    report['failures'].append(dict(error=str(exc),traceback=traceback.format_exc()))
                    await page.screenshot(path=str(output/'failure.png'),full_page=True)
                    (output/'failure.txt').write_text(await page.locator('body').inner_text(),encoding='utf-8')
                finally:
                    report['provider_attempts']=provider.calls
                    await context.close();await browser.close()
    report['status']='failed' if any(report[k] for k in ('failures','page_errors','routing_errors','provider_attempts','task_submissions','blocked_writes')) else ('reproduced' if args.expect_blocked else 'passed')
    (output/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(status=report['status'],checks=report['checks'],failures=[r['error'].split('\n')[0] for r in report['failures']],
        confirmations=len(report['confirmation_requests']),plans=len(report['plans']),provider_attempts=provider.calls),ensure_ascii=False))
    if report['status']=='failed':raise SystemExit(1)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--static-build',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--snapshot',type=Path,default=WORK/'prewrite-batch-draft-project.json')
    parser.add_argument('--expect-blocked',action='store_true')
    parser.add_argument('--scenario',choices=['changed','direct_plain','direct_rule_missing','direct_rule_empty','direct_completion','other_rootless','busy'],default='changed')
    parser.add_argument('--accepted-from',type=Path,default=WORK/'prewrite-overall-new-final1'/'result.json')
    asyncio.run(run(parser.parse_args()))
