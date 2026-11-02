"""Explicit local revision adoption from the captured synthetic revision 19.

All GET requests use isolated in-process ASGI stores. Browser writes and model
requests are rejected independently. No listener or daily data is accessed.
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
import sys
import tempfile
import traceback
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'work' / 'human-real-20261031'
sys.path.insert(0, str(ROOT))
IMPORT_SANDBOX = tempfile.TemporaryDirectory(prefix='ra-revision-offer-import-')
os.environ['RA_DATA_DIR'] = str(Path(IMPORT_SANDBOX.name) / 'data')
os.environ['RA_ACCESS_TOKEN'] = 'off'

import httpx
from playwright.async_api import async_playwright, expect
from app.core import dumps, now
from app.main import create_app
from tests.check_human_real_recovery_browser import ORIGIN, PERSISTED_KEYS, RejectingProvider, open_table

TARGET = 'REQ-34e5293bcc6e424d'
OFFER = 'REQ-2ee740529850433f'
OTHER = 'REQ-REVISION-UNRELATED'
MANUAL_TEXT = '人工保留的正文：本次只核对告警协作，勿覆盖。'
MANUAL_FLOW = '人工保留的流程：按已确认顺序处理并留待核对。'
CASES = (
    ('plain-1366', 'plain', (1366, 768)),
    ('plain-846', 'plain', (846, 620)),
    ('manual-statement', 'manual_statement', (1366, 768)),
    ('manual-behavior', 'manual_behavior', (1366, 768)),
    ('missing-recommendation', 'missing_recommendation', (1366, 768)),
    ('latest-qualified', 'latest', (1366, 768)),
    ('wrong-target', 'wrong_target', (1366, 768)),
    ('wrong-recommendation', 'wrong_recommendation', (1366, 768)),
    ('rejected', 'rejected', (1366, 768)),
    ('deferred', 'deferred', (1366, 768)),
    ('superseded', 'superseded', (1366, 768)),
    ('wrong-action', 'wrong_action', (1366, 768)),
    ('wrong-applicability', 'wrong_applicability', (1366, 768)),
    ('busy', 'busy', (1366, 768)),
)
INELIGIBLE = {'wrong_target', 'wrong_recommendation', 'rejected', 'deferred',
              'superseded', 'wrong_action', 'wrong_applicability'}


def seed(store, fixture, variant):
    keys = set(PERSISTED_KEYS) | {'id', 'name', 'revision', 'created', 'updated_at',
        'context_confirmations', 'refinement_completion', 'requirement_confirmation_mode',
        'document_update_needed', 'stale_document_kinds'}
    project = {key: copy.deepcopy(value) for key, value in fixture.items() if key in keys}
    assert project['id'] == 'P-8716b3947c514093' and project['revision'] == 19
    original = next(item for item in project['items'] if item['id'] == TARGET)
    candidate = next(item for item in project['items'] if item['id'] == OFFER)
    # Add a disclosed, unrelated synthetic sentinel to detect broad row replacement.
    other = copy.deepcopy(original)
    other.update(id=OTHER, title='合成无关需求：只验证保留', statement='无关需求的原始正文保持不变。',
                 related_refs=[], detail_required=False)
    other.pop('requirement_confirmation', None)
    project['items'].append(other)
    project['product_context']['scope_ids'].append(OTHER)
    if variant == 'wrong_target': candidate['target_item_id'] = 'REQ-NONMATCH'
    if variant == 'wrong_recommendation': candidate['recommendation_requirement_id'] = 'REQ-NONMATCH'
    if variant == 'missing_recommendation': candidate.pop('recommendation_requirement_id', None)
    if variant in ('rejected', 'deferred'): candidate['selection_status'] = variant
    if variant == 'superseded': candidate['superseded_by'] = 'REQ-LATER'
    if variant == 'wrong_action': candidate['action'] = 'add'
    if variant == 'wrong_applicability': candidate['applies_to'] = 'as_is'
    if variant == 'latest':
        newest = copy.deepcopy(candidate)
        newest.update(id='REQ-LATEST-QUALIFIED', statement='最新合成修订：' + candidate['statement'])
        project['items'].append(newest)
        candidate = newest
    with store.connect() as db:
        db.execute('INSERT INTO projects VALUES(?,?,?)', (project['id'], project['revision'], dumps(project)))
        db.execute('INSERT INTO revisions VALUES(?,?,?,?,?)',
            (project['id'], project['revision'], dumps(project), '冻结合成rev19及无关行保留哨兵', now()))
    if variant == 'busy':
        store.record(project['id'], 'user_task', dict(action='clarify', label='合成忙态：仅供禁用验证',
            status='running', calls=0, completed_steps=0, max_calls=None, cost=None,
            created=now(), input_revision=19, run_ids=[], stages=['clarify'],
            step_states=[dict(stage='clarify', status='running')], retry_input={}))
    assert 'context_review_statuses' not in store.get(project['id'])
    return project, original, candidate


async def local_sheet(page, work, pid):
    await work.get_by_role('button', name='保存草稿', exact=True).click()
    return await page.evaluate('key=>JSON.parse(sessionStorage.getItem(key))', pid+'-refinement-sheet')


async def run(args):
    build, output, snapshot = (path.resolve() for path in (args.static_build, args.output, args.snapshot))
    assert (build/'index.html').is_file()
    assert output.is_relative_to(WORK) and output != WORK and not output.exists()
    assert snapshot.is_relative_to(WORK)
    output.mkdir(parents=True)
    raw = snapshot.read_bytes()
    fixture = json.loads(raw)
    report = dict(status='running', fixture='CAPTURED_SYNTHETIC_REV19_WITH_DISCLOSED_UNRELATED_ROW',
        snapshot=str(snapshot), snapshot_sha256=hashlib.sha256(raw).hexdigest(), static_build=str(build),
        listener_started=False, daily_data_access=False, model_calls=0, provider_attempts=0,
        checks=[], failures=[], requests=[], blocked_writes=[], page_errors=[], routing_errors=[])
    with tempfile.TemporaryDirectory(prefix='ra-revision-offer-') as scratch:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            for label, variant, viewport in CASES:
                folder = Path(scratch)/label
                provider = RejectingProvider(folder)
                app = create_app(folder/'data', provider=provider, env_path=folder/'absent.env')
                store = app.state.store
                canonical, original, offered = seed(store, fixture, variant)
                pid = canonical['id']
                expected_ac = {item['id'] for item in canonical['items'] if item['kind']=='acceptance'
                    and not item.get('target_item_id') and TARGET in item.get('related_refs', [])}
                assert len(expected_ac) == 10
                context = await browser.new_context(viewport=dict(width=viewport[0], height=viewport[1]), service_workers='block')
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN, timeout=30) as client:
                    state = (await client.get(f'/api/projects/{pid}/requirement-confirmation/state')).json()
                    assert next(row for row in state['rows'] if row['requirement_id']==TARGET)['current'] is True

                    async def dispatch(route):
                        request, url = route.request, urlsplit(route.request.url)
                        try:
                            assert url.scheme=='http' and url.netloc=='127.0.0.1:8765', 'External URL blocked: '+request.url
                            if url.path.startswith('/api/'):
                                if request.method not in ('GET', 'HEAD'):
                                    report['blocked_writes'].append(dict(case=label, method=request.method, path=url.path))
                                    await route.fulfill(status=503, content_type='application/json', body='{"detail":"Writes forbidden in local revision checks"}')
                                    return
                                headers={key:value for key,value in request.headers.items() if key.lower() not in
                                    ('host','content-length','connection','accept-encoding')}
                                headers['host']='127.0.0.1:8765'
                                response=await client.request(request.method,url.path+('?' +url.query if url.query else ''),headers=headers)
                                report['requests'].append(dict(case=label,path=url.path,status=response.status_code))
                                await route.fulfill(status=response.status_code,body=response.content,
                                    content_type=response.headers.get('content-type','application/json'))
                            else:
                                target=(build/(url.path.lstrip('/') or 'index.html')).resolve()
                                assert target.is_relative_to(build) and target.is_file(), 'Missing static file: '+url.path
                                await route.fulfill(status=200,body=target.read_bytes(),
                                    content_type=mimetypes.guess_type(target.name)[0] or 'application/octet-stream')
                        except Exception as exc:
                            report['routing_errors'].append(dict(case=label,error=str(exc)))
                            await route.abort()

                    await context.route('**/*',dispatch)
                    page=await context.new_page()
                    page.set_default_timeout(10000)
                    page.on('pageerror',lambda error:report['page_errors'].append(str(error)))
                    try:
                        await page.goto(ORIGIN+'/',wait_until='networkidle')
                        work=await open_table(page,pid)
                        statement=work.locator(f'[data-cell-key="requirement-{TARGET}-statement"] textarea')
                        await expect(statement).to_have_value(original['statement'])
                        button=work.get_by_role('button',name='将此修订带入草稿',exact=True)
                        if variant in INELIGIBLE:
                            await expect(button).to_have_count(0)
                            report['checks'].append(dict(case=label,check='ineligible candidate has no local adoption entry'))
                        else:
                            title=work.get_by_text('有一版 AI 修订待核对',exact=True)
                            await expect(title).to_be_visible()
                            card=title.locator('xpath=ancestor::details[1]')
                            if await card.get_attribute('open') is None:
                                await card.locator(':scope > summary').click()
                            await expect(card.get_by_text(offered['statement'],exact=False)).to_be_visible()
                            if variant=='busy':
                                await expect(button).to_be_visible()
                                await expect(button).to_be_disabled()
                                report['checks'].append(dict(case=label,check='busy workbench disables local revision adoption'))
                                assert store.get(pid)==canonical and provider.calls==0
                                continue
                            await expect(button).to_be_enabled()
                            await card.get_by_text('阅读此版的完整业务说明',exact=True).click()
                            await expect(card.get_by_text(offered['behavior']['flow'],exact=True)).to_be_visible()
                            before=await local_sheet(page,work,pid)
                            assert before['rows'][TARGET]['statement']==original['statement']
                            assert {row['id'] for row in before['rows'][TARGET]['acceptance_items']}==expected_ac
                            if variant=='manual_statement': await statement.fill(MANUAL_TEXT)
                            if variant=='manual_behavior':
                                await work.get_by_role('button',name='展开业务说明：'+original['title'],exact=True).click()
                                flow=work.locator(f'[data-cell-key="requirement-{TARGET}-flow"] textarea')
                                await flow.fill(MANUAL_FLOW)
                            await button.click()
                            after=await local_sheet(page,work,pid)
                            row=after['rows'][TARGET]
                            assert row['candidate_id']==offered['id']
                            assert row['statement']==(MANUAL_TEXT if variant=='manual_statement' else offered['statement'])
                            expected_behavior=copy.deepcopy(offered['behavior'])
                            if variant=='manual_behavior': expected_behavior['flow']=MANUAL_FLOW
                            assert row['behavior']==expected_behavior
                            assert row['detail_contract']==offered['detail_contract']
                            assert {item['id'] for item in row['acceptance_items']}==expected_ac
                            assert len(row['acceptance_items'])==10
                            unrelated=copy.deepcopy(after['rows'][OTHER])
                            assert unrelated.pop('detail_conflicts',[])==[], 'Revision application created an unrelated conflict'
                            untouched=copy.deepcopy(before['rows'][OTHER])
                            untouched.pop('detail_conflicts',None)
                            assert unrelated==untouched, 'Revision application replaced unrelated business content'
                            assert after['scope']==before['scope'] and after['values']==before['values']
                            heading=work.locator(f'tr[data-requirement-id="{TARGET}"]')
                            await expect(heading).not_to_contain_text('已确认内容')
                            if variant.startswith('manual_'):
                                assert after.get('merge_conflicts'), 'Concurrent local business change must remain reviewable'
                                await expect(work).to_contain_text('已保留')
                            await page.screenshot(path=str(output/(label+'-applied.png')))
                            report['checks'].append(dict(case=label,check='explicit local adoption preserves manual edits, all ten acceptance identities and unrelated row',
                                candidate_id=row['candidate_id'],acceptance_count=len(row['acceptance_items']),conflicts=after.get('merge_conflicts',[])))
                        assert store.get(pid)==canonical, 'Viewing/adopting the candidate wrote canonical project data'
                        assert not store.records(pid,'run') and provider.calls==0
                        assert not [task for task in store.records(pid,'user_task') if task.get('status')!='running']
                    except Exception as exc:
                        report['failures'].append(dict(case=label,error=str(exc),traceback=traceback.format_exc()))
                        await page.screenshot(path=str(output/(label+'-failure.png')),full_page=True)
                        (output/(label+'-failure.txt')).write_text(await page.locator('body').inner_text(),encoding='utf-8')
                    finally:
                        report['provider_attempts']+=provider.calls
                        await context.close()
            await browser.close()
    report['status']='failed' if any(report[key] for key in ('failures','blocked_writes','provider_attempts','page_errors','routing_errors')) else 'passed'
    (output/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(status=report['status'],checks=len(report['checks']),
        failures=[dict(case=row['case'],error=row['error'].split('\n')[0]) for row in report['failures']],
        provider_attempts=report['provider_attempts'],blocked_writes=len(report['blocked_writes']),
        page_errors=report['page_errors'],routing_errors=report['routing_errors']),ensure_ascii=False))
    if report['status']!='passed': raise SystemExit(1)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--static-build',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--snapshot',type=Path,default=WORK/'single-retry-running-project.json')
    asyncio.run(run(parser.parse_args()))
