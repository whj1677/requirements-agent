"""Exercise the guided refinement UI against isolated real ASGI state.

All browser traffic on the formal 8765 origin is intercepted. The cloud-platform
fixture is synthetic; no daily data, model call, second service or formal
baseline is used. Existing item approval is a frozen prerequisite, not claimed
as browser-tested adoption.
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
from playwright.async_api import async_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.main import create_app
from app.product_flow import BEHAVIOR, status
from tests.check_refine_routes_browser import seed_routes
from tests.check_refine_workspace_browser import NoModelProvider, require, wait_store

ORIGIN = 'http://127.0.0.1:8765'
NAME = '合成储能云逐项完善验收'


def seed_guided(store):
    pid = seed_routes(store)
    current = store.get(pid)
    with store.edit(pid, current['revision'], '冻结逐项操作的合成前置条件') as (p, _):
        p['name'] = NAME
        p['product_context']['change_scope'] = ''
        p['context_field_states']['change_scope'] = dict(status='unknown')
        p['product_context_proposal']['change_scope'] = '合成建议：本期增加储能站电价时段维护。'
        req = next(i for i in p['items'] if i['id'] == 'REQ-0001')
        req['selection_status'] = 'selected'
        req['behavior'] = {key: '冻结合成业务说明：' + label for key, label in BEHAVIOR.items()}
    rows = status(store.get(pid))[:3]
    require({a.get('field') for state in rows for row in state['blockers'] for a in row['actions']} ==
            {'intent', 'users', 'change_scope'}, 'Fixture must have exactly three context obligations')
    return pid


async def run(args):
    build = args.static_build.resolve()
    require((build / 'index.html').is_file(), 'Static build index.html missing')
    args.output.mkdir(parents=True, exist_ok=True)
    trace = dict(scope='formal_8765_static_isolated_asgi', static_build=str(build), checks=[], api_requests=[],
                 page_errors=[], external_model_calls=0, formal_confirmation='NOT_RUN',
                 not_run=['real model output', 'daily project writes', 'formal baseline', 'full-project bulk adoption'],
                 fixture='Synthetic storage cloud electricity-price maintenance; existing items already reviewed')
    with tempfile.TemporaryDirectory(prefix='ra-refine-guided-') as scratch:
        folder = Path(scratch)
        provider = NoModelProvider(folder)
        app = create_app(folder / 'data', provider=provider)
        store = app.state.store
        pid = seed_guided(store)
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
                    await page.get_by_role('button', name=NAME).first.click()
                    work = page.locator('.connected-workspace:visible')
                    await work.locator('.connected-steps').get_by_role('button', name='完善需求').click()
                    guided = work.locator('.refine-guided')
                    current = guided.locator('.refine-task-current')
                    nav = guided.get_by_role('navigation', name='完善需求待办')
                    heading = current.locator('.refine-current-heading h3')
                    field = lambda key: current.locator('#refine-context-field-' + pid + '-' + key)

                    async def assert_area(area):
                        selectors = {'context': '.refine-context', 'scope': '.refine-requirements',
                                     'question': '.connected-question-side', 'review': '.refine-readiness'}
                        await expect(current.locator(selectors[area])).to_be_visible()
                        for other, selector in selectors.items():
                            require(await current.locator(selector + ':visible').count() == int(other == area),
                                    'Several task editors visible together: ' + area + ' / ' + other)
                        require(await page.locator('.scope-sequence:visible').count() == 0, 'Legacy scope editor reappeared')
                        require(await page.locator('.project-workspace:visible details.connected-details').get_attribute('open') is None,
                                'Guided workflow unexpectedly opened formal/legacy section')

                    async def other_content(button):
                        details = nav.locator('details')
                        if await details.get_attribute('open') is None:
                            await details.locator('summary').click()
                        await details.get_by_role('button', name=button, exact=True).click()

                    initial = copy.deepcopy(store.get(pid))
                    await expect(heading).to_have_text('希望达到的目标')
                    await assert_area('context')
                    require(await field('intent').get_by_label(re.compile('^希望达到的目标内容')).input_value() ==
                            initial['product_context_proposal']['intent'], 'Initial required item did not show its suggestion')
                    require(await nav.locator(':scope > button').count() == 3, 'Expected exactly three real pending tasks')
                    require(store.get(pid) == initial, 'Choosing the first task changed project data')
                    trace['checks'].append('one_visible_editor_starts_at_first_real_required_task_without_writes')
                    await page.screenshot(path=str(args.output / 'initial-current-task.png'))
                    work_bounds = await work.bounding_box()
                    first_action_bounds = await field('intent').get_by_role('button', name='确认这项内容', exact=True).bounding_box()
                    trace['initial_action_bounds'] = dict(work=work_bounds, action=first_action_bounds)
                    require(first_action_bounds and work_bounds and first_action_bounds['y'] >= work_bounds['y'] and
                            first_action_bounds['y'] + first_action_bounds['height'] <= work_bounds['y'] + work_bounds['height'],
                            'Initial primary action is clipped by the work area; visible action requires scrolling before any editing')
                    trace['checks'].append('initial_primary_action_fully_visible_without_scrolling')

                    await nav.get_by_role('button', name=re.compile('目标使用者')).click()
                    await expect(heading).to_have_text('目标使用者')
                    await field('users').get_by_role('button', name='确认这项内容', exact=True).click()
                    confirmed_user = await wait_store(store, pid, lambda p: p['context_field_states']['users']['status'] == 'known',
                                                      'Single suggestion was not confirmed')
                    await expect(current.get_by_role('button', name='处理下一项 →', exact=True)).to_be_visible()
                    await expect(heading).to_have_text('目标使用者')
                    require(confirmed_user['product_context']['users'] == initial['product_context_proposal']['users'], 'Wrong field suggestion confirmed')
                    for key in ('intent', 'change_scope'):
                        require(confirmed_user['product_context'][key] == initial['product_context'][key] and
                                confirmed_user['context_field_states'][key] == initial['context_field_states'][key], 'Unrelated field silently confirmed: ' + key)
                    require(confirmed_user['items'] == initial['items'] and confirmed_user['stage_checks'] == initial['stage_checks'],
                            'Field confirmation approved items or checkpoints')
                    require(confirmed_user['product_context_proposal'] == initial['product_context_proposal'], 'Confirmation rewrote proposals')
                    require(await nav.locator(':scope > button').filter(has_text='目标使用者').count() == 0, 'Completed field remains in pending queue')
                    require(await current.locator('.refine-context:visible').count() == 0,
                            'Completed item should show its result instead of leaving an editable task visible')
                    await expect(current.locator('.refine-task-done')).to_contain_text('内容已保留')
                    trace['checks'].append('confirm_one_suggestion_preserves_other_fields_and_stays_on_completed_item')
                    await page.screenshot(path=str(args.output / 'field-confirmed-stays.png'))

                    await current.get_by_role('button', name='处理下一项 →', exact=True).click()
                    await expect(heading).to_have_text('本次改动')
                    trace['checks'].append('explicit_next_moves_forward_from_middle_completed_field')
                    await nav.get_by_role('button', name=re.compile('希望达到的目标')).click()
                    intent_text = field('intent').get_by_label(re.compile('^希望达到的目标内容'))
                    unknown_text = '电价发布后是否需要回溯历史结算尚未确定。'
                    await intent_text.fill(unknown_text)
                    await field('intent').locator('.refine-context-state-options > summary').click()
                    await field('intent').get_by_label(re.compile('^希望达到的目标的状态')).select_option('unknown')
                    await field('intent').get_by_role('button', name='保存这项内容', exact=True).click()
                    unknown = await wait_store(store, pid, lambda p: p['product_context']['intent'] == unknown_text,
                                               'Unknown text was not saved')
                    require(unknown['context_field_states']['intent']['status'] == 'unknown', 'Unknown was silently treated as confirmed')
                    await expect(field('intent').get_by_role('status')).to_contain_text('仍待确认')
                    require(await nav.locator(':scope > button').filter(has_text='希望达到的目标').count() == 1, 'Unknown field disappeared from required queue')
                    require(await current.locator('.refine-task-done:visible').count() == 0, 'Unknown field misleadingly reported complete')
                    await expect(heading).to_have_text('希望达到的目标')
                    await page.screenshot(path=str(args.output / 'unknown-keeps-pending.png'))
                    await current.get_by_role('button', name='暂留此项，继续下一项 →', exact=True).click()
                    await expect(heading).to_have_text('本次改动')
                    trace['checks'].append('saved_unknown_stays_required_and_explicit_skip_opens_next_task')

                    change_text = field('change_scope').get_by_label(re.compile('^本次改动内容'))
                    edited_scope = '本期仅增加储能站电价时段维护与重叠校验。'
                    await change_text.fill(edited_scope)
                    await nav.get_by_role('button', name=re.compile('希望达到的目标')).click()
                    guard = page.get_by_role('dialog', name='未保存的修改', exact=True)
                    await guard.wait_for()
                    await guard.get_by_role('button', name='留在当前页', exact=True).click()
                    await expect(heading).to_have_text('本次改动')
                    require(await change_text.input_value() == edited_scope, 'Cancelled task switch lost unsaved field text')
                    require(store.get(pid)['product_context']['change_scope'] == '', 'Cancelled task switch implicitly saved field')
                    await assert_area('context')
                    trace['checks'].append('dirty_sidebar_switch_cancel_preserves_current_task_and_unsaved_input')
                    await field('change_scope').get_by_role('button', name='保存这项内容', exact=True).click()
                    await wait_store(store, pid, lambda p: p['product_context']['change_scope'] == edited_scope, 'Edited scope was not saved')
                    await expect(current.get_by_role('button', name='查看剩余事项 →', exact=True)).to_be_visible()
                    await expect(heading).to_have_text('本次改动')
                    trace['checks'].append('edited_field_save_stays_in_place_until_explicit_next')
                    await current.get_by_role('button', name='查看剩余事项 →', exact=True).click()
                    await expect(heading).to_have_text('确认需求核对')

                    # Manual editing and readiness remain available without
                    # discarding or bypassing the outstanding business decision.
                    before_manual = copy.deepcopy(store.get(pid))
                    await other_content('编辑全部需求')
                    await assert_area('scope')
                    require(await current.locator('.refine-requirement-card:visible').count() == 1, 'Manual requirements view did not show current requirement')
                    await page.screenshot(path=str(args.output / 'manual-requirements.png'))
                    await other_content('检查完成情况')
                    await assert_area('review')
                    await expect(current.get_by_role('button', name='检查待处理事项', exact=True)).to_be_visible()
                    require(await current.get_by_role('button', name='确认需求核对并进入交付', exact=True).count() == 0,
                            'Unknown business decision incorrectly enables final check')
                    require(store.get(pid) == before_manual, 'Manual editor or readiness silently wrote/approved content')
                    trace['checks'].append('manual_edit_and_readiness_routes_keep_one_editor_and_respect_unknown_gate')

                    await nav.get_by_role('button', name=re.compile('希望达到的目标')).click()
                    final_intent = '管理员能够维护储能站电价时段，并在重叠时得到明确的拒绝提示。'
                    await intent_text.fill(final_intent)
                    await field('intent').get_by_role('button', name='保存这项内容', exact=True).click()
                    ready = await wait_store(store, pid, lambda p: p['product_context']['intent'] == final_intent and
                                             p['context_field_states']['intent']['status'] == 'known', 'Final explicit field save did not complete')
                    await expect(current.get_by_role('button', name='进入阶段确认 →', exact=True)).to_be_visible()
                    await expect(heading).to_have_text('希望达到的目标')
                    require(not any(state['blockers'] for state in status(ready)[:3]), 'Actual backend obligations remain before review')
                    require(any(q['status'] == 'open' and not q['blocking'] for q in ready['questions']), 'Fixture optional question was incorrectly answered')
                    require(await nav.locator(':scope > button').count() == 0, 'Resolved context tasks remain in required queue')
                    await current.get_by_role('button', name='进入阶段确认 →', exact=True).click()
                    await assert_area('review')
                    await expect(current.get_by_role('button', name='确认需求核对并进入交付', exact=True)).to_be_visible()
                    trace['checks'].append('required_queue_resolves_without_forcing_optional_questions_and_explicit_next_opens_review')
                    await page.screenshot(path=str(args.output / 'ready-for-confirmation.png'))

                    request_start = len(trace['api_requests'])
                    expected_steps = [str(state['step']) for state in status(ready)[:3] if not state['complete']]
                    await current.get_by_role('button', name='确认需求核对并进入交付', exact=True).click()
                    confirmed = await wait_store(store, pid, lambda p: all(state['complete'] for state in status(p)[:3]),
                                                 'Explicit confirmation did not persist actual stage gates')
                    await work.get_by_role('heading', name='确认交付版本', exact=True).wait_for()
                    require(await guided.count() == 0, 'Successful confirmation did not leave refinement for delivery')
                    require(confirmed['items'] == ready['items'] and confirmed['product_context'] == ready['product_context'] and
                            confirmed['product_context_proposal'] == ready['product_context_proposal'] and confirmed['questions'] == ready['questions'],
                            'Stage confirmation rewrote or approved business content')
                    stage_calls = [r for r in trace['api_requests'][request_start:] if r['method'] == 'POST' and '/stage-checks/' in r['path']]
                    require([r['path'].rsplit('/', 1)[-1] for r in stage_calls] == expected_steps, 'Stage sequence or number of confirmations changed')
                    require([r['body']['expected_revision'] for r in stage_calls] == [ready['revision'] + n for n in range(len(expected_steps))],
                            'Serial stage confirmation reused a stale revision')
                    trace['checks'].append('explicit_confirmation_records_actual_stages_then_automatically_shows_delivery')
                    await page.screenshot(path=str(args.output / 'automatic-delivery.png'))

                    # Separate fixture transition: two pending requirements
                    # share the saved scope, but a task shows only one item.
                    fixture_current = store.get(pid)
                    with store.edit(pid, fixture_current['revision'], '冻结单条任务批选隔离场景') as (p, _):
                        first_req = next(i for i in p['items'] if i['id'] == 'REQ-0001')
                        first_req['selection_status'] = 'candidate'
                        second_req = copy.deepcopy(first_req)
                        second_req.update(id='REQ-0002', title='合成电价历史版本查询', statement='管理员按储能站查询已保存的电价历史版本。', related_refs=[])
                        p['items'].append(second_req)
                        p['product_context']['scope_ids'] = ['REQ-0001', 'REQ-0002']
                        p['sources'][0]['excerpts'][0]['text'] += ' 管理员按储能站查询已保存的电价历史版本。'
                    await page.reload(wait_until='networkidle')
                    await work.locator('.connected-steps').get_by_role('button', name='完善需求').click()
                    await nav.get_by_role('button', name=re.compile('储能站电价时段维护')).click()
                    await expect(heading).to_have_text('储能站电价时段维护')
                    await assert_area('scope')
                    cards = current.locator('.refine-requirement-card:visible')
                    require(await cards.count() == 1, 'Single-item task exposed unrelated requirements')
                    actionbar = current.locator('.refine-actionbar:visible')

                    async def select_visible_requirement():
                        select_action = actionbar.get_by_role('button', name=re.compile('^选择'))
                        if await select_action.count():
                            await select_action.click()
                        else:
                            await cards.get_by_label(re.compile('选入本次采纳核对')).check()
                        await expect(actionbar.get_by_role('button', name='批量核对并采纳（1）', exact=True)).to_be_enabled()
                        require(await cards.locator('input[type=checkbox]:checked').count() == 1, 'Selection does not match the visible requirement')

                    await select_visible_requirement()
                    await nav.get_by_role('button', name=re.compile('合成电价历史版本查询')).click()
                    await expect(heading).to_have_text('合成电价历史版本查询')
                    require(await cards.count() == 1, 'Second single-item task exposed unrelated requirements')
                    await expect(actionbar.get_by_role('button', name='批量核对并采纳（0）', exact=True)).to_be_disabled()
                    require(await cards.locator('input[type=checkbox]:checked').count() == 0, 'Previous item selection leaked into the next task')
                    trace['checks'].append('single_item_selection_excludes_hidden_items_and_clears_when_changing_tasks')
                    await select_visible_requirement()
                    await actionbar.get_by_role('button', name='批量核对并采纳（1）', exact=True).click()
                    modal = page.get_by_role('dialog', name='核对并批量采纳需求', exact=True)
                    await modal.wait_for()
                    require(await modal.locator('.bulk-adoption-item').count() == 1, 'Single-item review includes hidden scoped items')
                    await expect(modal.locator('.bulk-adoption-item h3')).to_have_text('合成电价历史版本查询')
                    before_adopt = copy.deepcopy(store.get(pid))
                    await modal.locator('.bulk-adoption-actions input[type=checkbox]').check()
                    await modal.get_by_role('button', name='确认并一键采纳 1 条', exact=True).click()
                    await modal.wait_for(state='detached')
                    adopted = await wait_store(store, pid, lambda p: next(i for i in p['items'] if i['id'] == 'REQ-0002')['selection_status'] == 'selected',
                                               'Single visible requirement was not adopted')
                    first_after = next(i for i in adopted['items'] if i['id'] == 'REQ-0001')
                    require(first_after == next(i for i in before_adopt['items'] if i['id'] == 'REQ-0001') and first_after['selection_status'] == 'candidate',
                            'Single-item approval silently adopted or changed hidden requirement')
                    require(adopted['product_context'] == before_adopt['product_context'] and adopted['questions'] == before_adopt['questions'],
                            'Single-item adoption rewrote scope or question decisions')
                    trace['checks'].append('single_item_adoption_dialog_and_write_affect_only_the_reviewed_visible_requirement')
                    await page.screenshot(path=str(args.output / 'single-item-adoption.png'))
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
    parser.add_argument('--static-build', type=Path, default=ROOT / 'evidence/refine-guided-20261009/build')
    parser.add_argument('--output', type=Path, required=True)
    asyncio.run(run(parser.parse_args()))
