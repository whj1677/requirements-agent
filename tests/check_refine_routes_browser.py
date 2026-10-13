"""Exercise requirement/context return routes with isolated real ASGI state.

The browser uses the formal 8765 origin with every request intercepted. No
second server, daily database, external model call, or formal baseline is used.
"""
import argparse
import asyncio
import copy
import json
import mimetypes
import re
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.main import create_app
from app.core import now
from app.product_flow import BEHAVIOR, checkpoint, status
from tests.helpers import prepared
from tests.check_refine_workspace_browser import NoModelProvider, require, wait_store

ORIGIN = 'http://127.0.0.1:8765'


def seed_routes(store):
    project = prepared(store)
    pid = project['id']
    with store.edit(pid, project['revision'], '冻结合成储能云返回路径') as (p, _):
        p['name'] = '合成储能云返回路径'
        p['workflow_mode'] = 'shared-draft'
        p['workspace_draft'] = dict(text='本期维护储能站电价时段。', entry_mode='direct', saved_at=now())
        p['intake'] = dict(mode='direct', text='本期维护储能站电价时段。', saved_at=now())
        p['scope_profile'] = dict(scenario='new_product', dependencies='unknown')
        p['product_context'] = dict(product='合成储能云平台', module='电价管理', intent='', users='',
                                    change_scope='新增储能站电价时段维护。', scope_ids=['REQ-0001'])
        p['context_field_states'] = dict(intent=dict(status='unknown'), users=dict(status='unknown'))
        p['product_context_proposal'] = dict(intent='助手建议：维护储能站电价时段。', users='助手建议：场站运维人员。',
                                             value='助手建议：减少电价重复维护。', priority='助手建议：优先级待定。')
        req = next(i for i in p['items'] if i['id'] == 'REQ-0001')
        req.update(selection_status='candidate', change_type='new', title='储能站电价时段维护')
        p['stage_checks'] = {}
        checkpoint(p, 1, status(p)[0]['content_hash'])
    return pid


async def run(args):
    build = args.static_build.resolve()
    require((build / 'index.html').is_file(), 'Static build index.html missing')
    args.output.mkdir(parents=True, exist_ok=True)
    trace = dict(scope='formal_8765_static_isolated_asgi', static_build=str(build), checks=[], api_requests=[],
                 page_errors=[], external_model_calls=0, formal_confirmation='NOT_RUN',
                 not_run=['real model output', 'formal document approval or baseline', 'daily project writes'])
    with tempfile.TemporaryDirectory(prefix='ra-refine-routes-') as scratch:
        folder = Path(scratch)
        provider = NoModelProvider(folder)
        app = create_app(folder / 'data', provider=provider)
        store = app.state.store
        pid = seed_routes(store)
        trace['project_id'] = pid
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN, timeout=30) as client:
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(headless=True)
                context = await browser.new_context(viewport=dict(width=1280, height=820))

                async def dispatch(route):
                    request = route.request
                    parsed = urlsplit(request.url)
                    path = parsed.path
                    require(parsed.netloc == '127.0.0.1:8765', 'External request forbidden: ' + request.url)
                    if path.startswith('/api/'):
                        require(not (request.method != 'GET' and (path.endswith('/actions') or '/confirm' in path or '/runs' in path)),
                                'Forbidden model task or formal confirmation: ' + path)
                        headers = {k: v for k, v in request.headers.items() if k.lower() not in ('host', 'content-length', 'connection', 'accept-encoding')}
                        headers['host'] = '127.0.0.1:8765'
                        response = await client.request(request.method, path + ('?' + parsed.query if parsed.query else ''),
                                                        content=request.post_data_buffer, headers=headers)
                        record = dict(method=request.method, path=path, status=response.status_code)
                        if request.method != 'GET':
                            record['body'] = request.post_data_json
                        trace['api_requests'].append(record)
                        await route.fulfill(status=response.status_code, headers={'content-type': response.headers.get('content-type', 'application/json')}, body=response.content)
                        return
                    target = (build / (path.lstrip('/') or 'index.html')).resolve()
                    require(target.is_relative_to(build) and target.is_file(), 'Missing static asset: ' + path)
                    await route.fulfill(status=200, content_type=mimetypes.guess_type(target.name)[0] or 'application/octet-stream', body=target.read_bytes())

                await context.route('**/*', dispatch)
                page = await context.new_page()
                page.on('pageerror', lambda error: trace['page_errors'].append(str(error)))
                page.set_default_timeout(12000)
                try:
                    await page.goto(ORIGIN, wait_until='networkidle')
                    await page.get_by_role('button', name='合成储能云返回路径').first.click()
                    work = page.locator('.connected-workspace:visible')
                    details = page.locator('.project-workspace:visible details.connected-details')
                    nav = work.locator('.connected-steps')

                    async def assert_refine(message):
                        await work.locator('.refine-requirements').wait_for()
                        require(await details.get_attribute('open') is None, message + ': opened legacy details')
                        require(await page.locator('.scope-sequence:visible').count() == 0, message + ': exposed old scope/adoption sequence')

                    async def delivery():
                        await nav.get_by_role('button', name='确认交付').click()
                        await work.get_by_role('heading', name='确认交付版本', exact=True).wait_for()

                    async def wire():
                        response = await client.get('/api/projects/' + pid)
                        response.raise_for_status()
                        return response.json()

                    await delivery()
                    initial = copy.deepcopy(store.get(pid))
                    payload = await wire()
                    intent_action = next(a for row in payload['confirmation_blockers'] for a in row['actions']
                                         if a.get('kind') == 'context' and a.get('field') == 'intent')
                    await work.get_by_role('button', name=intent_action['label'] + ' →', exact=True).click()
                    field = work.locator('#refine-context-field-' + pid + '-intent')
                    text = field.get_by_label(re.compile('^希望达到的目标内容'))
                    await text.wait_for()
                    await assert_refine('Context blocker')
                    require(await text.input_value() == initial['product_context_proposal']['intent'], 'Current suggestion missing from targeted field')
                    require(store.get(pid) == initial, 'Opening context editor changed project data')
                    trace['checks'].append('delivery_context_blocker_opens_exact_field_in_refine_without_mutation')

                    edited = '管理员可以维护储能站电价时段，并查看修改后的有效版本。'
                    await text.fill(edited)
                    await field.get_by_role('button', name='保存当前字段', exact=True).click()
                    saved = await wait_store(store, pid, lambda p: p['product_context'].get('intent') == edited, 'Current field was not saved')
                    require(saved['context_field_states']['intent']['status'] == 'known', 'Explicitly saved field remains unknown')
                    require({k: v for k, v in saved['product_context'].items() if k != 'intent'} ==
                            {k: v for k, v in initial['product_context'].items() if k != 'intent'}, 'Saving one field changed unrelated values or scope')
                    require(saved['context_field_states']['users'] == initial['context_field_states']['users'], 'Saving intent confirmed user suggestion')
                    require(saved['product_context_proposal'] == initial['product_context_proposal'], 'Saving intent altered AI proposals')
                    require(saved['items'] == initial['items'] and saved['stage_checks'] == initial['stage_checks'], 'Field save adopted an item or confirmed a stage')
                    await assert_refine('Save context field')
                    require(await text.is_visible(), 'Saving field closed the current editor')
                    trace['checks'].append('single_field_save_preserves_other_suggestions_scope_adoption_and_stage_checks')
                    await page.screenshot(path=str(args.output / 'context-saved-in-place.png'))

                    await text.fill(edited + ' 这部分尚未保存。')
                    await nav.get_by_role('button', name='提供信息').click()
                    guard = page.get_by_role('dialog', name='未保存的修改', exact=True)
                    await guard.wait_for()
                    await guard.get_by_role('button', name='留在当前页', exact=True).click()
                    await assert_refine('Cancelled dirty context switch')
                    require(await text.input_value() == edited + ' 这部分尚未保存。', 'Cancelled navigation lost unsaved context')
                    require(store.get(pid)['product_context']['intent'] == edited, 'Cancelled navigation saved the input implicitly')
                    await field.get_by_role('button', name='取消本字段修改', exact=True).click()
                    trace['checks'].append('dirty_context_navigation_cancel_keeps_field_and_input')

                    # This explicit check must expose remaining work without
                    # treating visible AI suggestions or scoped candidates as approved.
                    before_check = copy.deepcopy(store.get(pid))
                    await work.get_by_role('button', name='确认需求核对', exact=True).click()
                    check_panel = work.locator('.refine-readiness')
                    await check_panel.wait_for()
                    await assert_refine('Blocked stage check')
                    require(store.get(pid)['items'] == before_check['items'], 'Blocked checkpoint auto-adopted candidates')
                    require(store.get(pid)['context_field_states']['users']['status'] == 'unknown', 'Blocked checkpoint accepted user suggestion')
                    require('2' not in store.get(pid)['stage_checks'] and '3' not in store.get(pid)['stage_checks'], 'Blocked stage was marked complete')
                    await check_panel.get_by_role('button', name='核对目标使用者 →', exact=True).click()
                    user_field = work.locator('#refine-context-field-' + pid + '-users')
                    await user_field.get_by_label(re.compile('^目标使用者内容')).wait_for()
                    await assert_refine('Stage blocker field route')
                    trace['checks'].append('explicit_checkpoint_reports_remaining_work_and_routes_field_in_place')
                    await page.screenshot(path=str(args.output / 'blocked-check-in-place.png'))

                    await delivery()
                    await work.get_by_role('button', name='前往本步骤核对 →', exact=True).first.click()
                    await assert_refine('Delivery stage 2 return')
                    trace['checks'].append('delivery_stage_two_route_returns_to_refine')

                    # Formal documents remain a separate review surface, but
                    # their incomplete requirement prerequisites must return here.
                    await delivery()
                    await work.get_by_role('button', name='查看与核对正式文档', exact=True).click()
                    await details.locator('.document-stage:visible').wait_for()
                    await details.get_by_role('button', name='去完成前序核对 →', exact=True).click()
                    await assert_refine('Formal document prerequisite')
                    trace['checks'].append('formal_document_prerequisite_returns_to_refine_without_old_form')

                    # A persisted historical phase must never resurrect the old
                    # form when the formal section is explicitly opened.
                    await page.evaluate('(id)=>sessionStorage.setItem("ra-phase-"+id,"1")', pid)
                    await page.reload(wait_until='networkidle')
                    await details.locator(':scope > summary').click()
                    require(await details.get_attribute('open') is not None, 'Explicit formal section did not open')
                    require(await details.locator('.phase-nav:visible').count() == 0, 'Formal section exposed a second five-step navigation')
                    require(await page.locator('.scope-sequence:visible').count() == 0, 'Manual expansion exposed old scope/adoption form')
                    await details.get_by_role('button', name='返回完善需求', exact=True).click()
                    await assert_refine('Formal section explicit return')
                    trace['checks'].append('stale_phase_and_manual_formal_section_never_expose_legacy_workflow')

                    # State transitions below are fixture preparation only, not
                    # claimed as UI adoption. The UI must still require explicit
                    # checkpoint confirmation after all source-backed inputs exist.
                    current = store.get(pid)
                    with store.edit(pid, current['revision'], '冻结已人工核对的测试前置状态') as (p, _):
                        p['product_context']['users'] = '合成场站管理员'
                        p['context_field_states']['users'] = dict(status='known')
                        req = next(i for i in p['items'] if i['id'] == 'REQ-0001')
                        req['selection_status'] = 'selected'
                        req['behavior'] = {key: '合成核对：' + label for key, label in BEHAVIOR.items()}
                        checkpoint(p, 2, status(p)[1]['content_hash'])
                    await page.reload(wait_until='networkidle')
                    await delivery()
                    await work.get_by_role('button', name='前往本步骤核对 →', exact=True).first.click()
                    await assert_refine('Delivery stage 3 return')
                    trace['checks'].append('delivery_stage_three_route_returns_to_refine')

                    # Do not reduce a multi-stage confirmation to a final-stage
                    # smoke check: each write returns a flow array, and the next
                    # stage requires a fresh project revision and fingerprint.
                    current = store.get(pid)
                    with store.edit(pid, current['revision'], '冻结连续范围与规则确认场景') as (p, _):
                        p['stage_checks'].pop('2', None)
                        p['stage_checks'].pop('3', None)
                    await page.reload(wait_until='networkidle')
                    await assert_refine('Reload for serial checkpoints 2 and 3')
                    ready_before = copy.deepcopy(store.get(pid))
                    require(not any(str(n) in ready_before['stage_checks'] for n in (2, 3)), 'Fixture prematurely confirmed stage 2 or 3')
                    request_start = len(trace['api_requests'])
                    await work.get_by_role('button', name='确认需求核对', exact=True).click()
                    confirmed = await wait_store(store, pid, lambda p: all(s['complete'] for s in status(p)[:3]),
                                                 'Single explicit confirmation did not serially persist stages 2 and 3')
                    await work.locator('.refine-readiness').get_by_text('需求核对已记录，可查看交付准备情况。', exact=True).wait_for()
                    require(all(s['complete'] for s in status(confirmed)[:3]), 'Explicit check did not satisfy current stage identities')
                    stage_calls = [r for r in trace['api_requests'][request_start:] if r['method'] == 'POST' and '/stage-checks/' in r['path']]
                    require([r['path'].rsplit('/', 1)[-1] for r in stage_calls] == ['2', '3'], 'Expected exactly serial stage 2 then stage 3 writes')
                    require([r['body']['expected_revision'] for r in stage_calls] == [ready_before['revision'], ready_before['revision'] + 1],
                            'Stage 3 did not use the fresh revision after stage 2')
                    require(confirmed['items'] == ready_before['items'], 'Explicit checkpoint rewrote or adopted items')
                    require(confirmed['product_context_proposal'] == ready_before['product_context_proposal'], 'Explicit checkpoint wrote unrelated proposals')
                    await assert_refine('Successful explicit checkpoint')
                    trace['checks'].append('single_confirmation_serially_records_stages_two_and_three_without_content_changes')
                    await page.screenshot(path=str(args.output / 'requirements-checked-in-place.png'))

                    current = store.get(pid)
                    with store.edit(pid, current['revision'], '冻结输入尚未核对的导航场景') as (p, _):
                        for step in (1, 2, 3):
                            p['stage_checks'].pop(str(step), None)
                    await page.reload(wait_until='networkidle')
                    await delivery()
                    pending = await wire()
                    stage_action = next(a for row in pending['confirmation_blockers'] for a in row['actions'] if a.get('kind') == 'stage')
                    require(stage_action['step'] == 1, 'Stage one route fixture did not expose step one')
                    await work.get_by_role('button', name=stage_action['label'] + ' →', exact=True).first.click()
                    await work.get_by_role('heading', name='从已知信息开始', exact=True).wait_for()
                    require(await details.get_attribute('open') is None and await page.locator('.scope-sequence:visible').count() == 0,
                            'Stage one return exposed legacy editor')
                    trace['checks'].append('delivery_stage_one_route_returns_to_shared_information')

                    await nav.get_by_role('button', name='完善需求').click()
                    await assert_refine('All three stages ready for explicit confirmation')
                    all_before = copy.deepcopy(store.get(pid))
                    require(not any(str(n) in all_before['stage_checks'] for n in (1, 2, 3)), 'Fixture prematurely confirmed one of stages 1 through 3')
                    request_start = len(trace['api_requests'])
                    await work.get_by_role('button', name='确认需求核对', exact=True).click()
                    all_confirmed = await wait_store(store, pid, lambda p: all(s['complete'] for s in status(p)[:3]),
                                                     'Single explicit confirmation did not serially persist stages 1 through 3')
                    await work.locator('.refine-readiness').get_by_text('需求核对已记录，可查看交付准备情况。', exact=True).wait_for()
                    all_calls = [r for r in trace['api_requests'][request_start:] if r['method'] == 'POST' and '/stage-checks/' in r['path']]
                    require([r['path'].rsplit('/', 1)[-1] for r in all_calls] == ['1', '2', '3'], 'Expected exactly serial stage 1, 2, then 3 writes')
                    require([r['body']['expected_revision'] for r in all_calls] == [all_before['revision'] + n for n in range(3)],
                            'Successive stages did not use fresh project revisions')
                    require(all_confirmed['items'] == all_before['items'] and all_confirmed['product_context'] == all_before['product_context'] and
                            all_confirmed['product_context_proposal'] == all_before['product_context_proposal'],
                            'Serial checkpoint confirmation changed existing business content')
                    await assert_refine('Successful three-stage explicit checkpoint')
                    trace['checks'].append('single_confirmation_serially_records_all_three_stages_and_stays_in_refine')

                    require(not trace['page_errors'], 'Runtime errors: ' + repr(trace['page_errors']))
                    require(provider.calls == 0, 'Unexpected provider invocation')
                    require(not store.get(pid).get('active_baseline_id'), 'Unexpected formal baseline')
                    trace['status'] = 'executed_checks_passed'
                except Exception as error:
                    trace['status'] = 'failed'
                    trace['failure'] = str(error)
                    await page.screenshot(path=str(args.output / 'failure.png'), full_page=True)
                    (args.output / 'failure.html').write_text(await page.content(), encoding='utf-8')
                    raise
                finally:
                    trace['external_model_calls'] = provider.calls
                    (args.output / 'result.json').write_text(json.dumps(trace, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
                    await browser.close()
    print(json.dumps(trace, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--static-build', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    asyncio.run(run(parser.parse_args()))
