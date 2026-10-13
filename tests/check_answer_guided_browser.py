"""Guided answer UI against isolated real ASGI APIs; no model execution.

The supplied static build is intercepted at the formal 8765 origin. This script
does not start another server or read/write the daily workbench database.
"""
import argparse
import asyncio
import copy
import json
import mimetypes
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from playwright.async_api import async_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core import now
from app.main import create_app
from app.provider import DEFAULT, origin
from tests.check_refine_workspace_browser import NoModelProvider, require, wait_store
from tests.helpers import prepared

ORIGIN = 'http://127.0.0.1:8765'
QUESTION = '储能站电价时段重叠时怎样处理？'
ANSWER = '保存时段重叠时拒绝保存，原数据不变，编辑内容保留。'
ORDER_NAME = '合成储能云回答顺序验收'
ORDER_QUESTIONS = ['第一题：电价模板由谁维护？', '第二题：时段修改何时生效？', '第三题：生效结果在哪里查看？']


def seed(store):
    initial = prepared(store)
    with store.edit(initial['id'], initial['revision'], '冻结人工核对已采纳电价规则') as (project, _):
        project['name'] = '合成储能云回答引导验收'
        # Existing selected clause already expresses the frozen answer. No
        # candidate is added; user confirmation must supply the missing review.
        project['sources'][0]['excerpts'][0]['text'] = '管理员维护电价时段。' + ANSWER
        project['questions'] = [dict(id='Q-overlap', topic_key='overlap',
            question=QUESTION, why='明确储能站电价保存行为', options=[],
            blocking=True, related_refs=['RULE-0001'], source_refs=[], status='open', answer=None,
            decision_group_id='QG-overlap', decision_group_title=QUESTION,
            decision_coverage='pending', understanding_status='pending')]
        project['workflow_mode'] = 'shared-draft'
        project['workspace_draft'] = dict(text='本期确认储能站电价时段维护的重叠处理规则。',
                                          entry_mode='direct', saved_at=now())
        project.setdefault('product_context', {})['scope_ids'] = ['REQ-0001']
    return initial['id']


def seed_order(store, base_pid):
    """Independent questions remain pending until explicit content review."""
    pid = store.create(ORDER_NAME)['id']
    template = json.dumps(store.get(base_pid), ensure_ascii=False)
    for prefix in ['REQ', 'RULE', 'AC', 'SRC', 'EX']:
        template = template.replace(prefix + '-0001', prefix + '-order-0001')
    template = json.loads(template)
    with store.edit(pid, store.get(pid)['revision'], '冻结三问题顺序') as (project, _):
        for key in ['sources', 'items', 'workflow_mode', 'workspace_draft', 'product_context']:
            project[key] = template[key]
        question = template['questions'][0]
        project['questions'] = [dict(copy.deepcopy(question), id=f'Q-order-{index}',
            topic_key=f'order-{index}', question=title, decision_group_id=f'QG-order-{index}',
            decision_group_title=title) for index, title in enumerate(ORDER_QUESTIONS)]
    return pid


async def run(args):
    build = args.static_build.resolve()
    require((build / 'index.html').is_file(), 'static build missing')
    args.output.mkdir(parents=True, exist_ok=True)
    trace = dict(scope='formal_8765_intercepted_build_with_isolated_real_asgi',
                 static_build=str(build), api_requests=[], checks=[], page_errors=[],
                 provider_calls=0, executed_task_requests=0, daily_data_access=False)
    with tempfile.TemporaryDirectory(prefix='ra-guided-answer-') as scratch:
        folder = Path(scratch)
        provider = NoModelProvider(folder)
        # A fake local credential permits planning. Provider.request raises if
        # any execution is accidentally attempted; no real key is loaded.
        provider.keys[origin(DEFAULT)] = 'OFFLINE_GUIDED_ANSWER_FIXTURE_ONLY'
        app = create_app(folder / 'data', provider=provider)
        store = app.state.store
        pid = seed(store)
        order_pid = seed_order(store, pid)
        frozen_items = copy.deepcopy(store.get(pid)['items'])
        trace['project_id'] = pid
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN, timeout=30) as client:
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(headless=True)
                context = await browser.new_context(viewport=dict(width=1440, height=900))

                async def dispatch(route):
                    request = route.request
                    split = urlsplit(request.url)
                    path = split.path
                    if path.startswith('/api/'):
                        entry = dict(method=request.method, path=path)
                        trace['api_requests'].append(entry)
                        if path.endswith('/actions') and request.method == 'POST':
                            trace['executed_task_requests'] += 1
                            await route.fulfill(status=403, content_type='application/json',
                                body=json.dumps(dict(code='TEST_NO_MODEL', message='隔离验收禁止模型执行')))
                            return
                        headers = {key: value for key, value in request.headers.items()
                                   if key.lower() not in ('host', 'content-length', 'connection', 'accept-encoding')}
                        headers['host'] = '127.0.0.1:8765'
                        response = await client.request(request.method,
                            path + ('?' + split.query if split.query else ''),
                            content=request.post_data_buffer, headers=headers)
                        entry['status'] = response.status_code
                        if request.method == 'POST' and '/decision-review' in path:
                            entry['request'] = request.post_data_json
                            entry['response'] = response.json()
                        await route.fulfill(status=response.status_code,
                            headers={'content-type': response.headers.get('content-type', 'application/json')},
                            body=response.content)
                        return
                    if split.netloc == '127.0.0.1:8765' and (path == '/' or path.startswith('/assets/')):
                        target = (build / (path.lstrip('/') or 'index.html')).resolve()
                        require(target.is_relative_to(build) and target.is_file(), 'unexpected static file: ' + path)
                        await route.fulfill(status=200, content_type=mimetypes.guess_type(target.name)[0]
                                            or 'application/octet-stream', body=target.read_bytes())
                        return
                    await route.abort()

                await context.route('**/*', dispatch)
                page = await context.new_page()
                page.set_default_timeout(12000)
                page.on('pageerror', lambda error: trace['page_errors'].append(str(error)))
                try:
                    await page.goto(ORIGIN, wait_until='networkidle')
                    await page.get_by_role('button', name='合成储能云回答引导验收').first.click()
                    work = page.locator('.connected-workspace:visible')
                    await work.locator('.connected-steps').get_by_role('button', name='完善需求').click()
                    pane = work.locator('.connected-question-side:visible')
                    await expect(pane.get_by_role('heading', name=QUESTION, exact=True)).to_be_visible()
                    require(await work.locator('.refine-task-current main:visible').count() == 0,
                            'answer task simultaneously exposed another editor')
                    answer = pane.get_by_label('我的回答原文')
                    await answer.fill(ANSWER)
                    await pane.get_by_role('button', name='保存回答并继续', exact=True).click()
                    saved = await wait_store(store, pid,
                        lambda p: bool(p.get('decision_answers')), 'raw answer was not saved')
                    await expect(pane.get_by_role('button', name='让 AI 整理这条回答', exact=True)).to_be_enabled()
                    require(await pane.get_by_role('button', name='核对修改建议', exact=True).count() == 0,
                            'empty pending answer exposed a useless preview')
                    require(not any(row['path'].endswith('/actions/plan') for row in trace['api_requests']),
                            'saving an answer automatically planned an AI call')
                    require(saved['questions'][0]['decision_coverage'] == 'pending'
                            and saved['questions'][0]['understanding_status'] == 'pending',
                            'saving text approved or applied the answer')
                    trace['checks'].append('saved_answer_has_one_useful_ai_cta_without_empty_preview_or_approval')
                    await page.screenshot(path=str(args.output / 'saved-answer-next-action.png'), full_page=True)

                    await pane.get_by_role('button', name='让 AI 整理这条回答', exact=True).click()
                    plan = page.get_by_role('dialog', name='核对本次任务', exact=True)
                    await expect(plan).to_be_visible()
                    require(any(row['path'].endswith('/actions/plan') for row in trace['api_requests']),
                            'AI CTA did not request a bounded task plan')
                    require('本次对象' in await plan.inner_text(), 'task plan did not state its target')
                    require(provider.calls == 0 and trace['executed_task_requests'] == 0,
                            'opening plan executed model')
                    await page.screenshot(path=str(args.output / 'ai-scope-plan-only.png'))
                    await plan.get_by_role('button', name='返回保留输入').click()
                    require(await answer.input_value() == ANSWER, 'closing AI plan lost saved answer')
                    trace['checks'].append('ai_cta_opens_scope_confirmation_only_and_cancel_preserves_answer')

                    async def manual_preview():
                        manual = pane.locator('details.refine-manual-answer')
                        if await manual.get_attribute('open') is None:
                            await manual.locator(':scope > summary').click()
                        coverage = manual.locator('details.connected-coverage')
                        if await coverage.get_attribute('open') is None:
                            await coverage.locator(':scope > summary').click()
                        await coverage.get_by_role('combobox').select_option('clear')
                        await coverage.get_by_label('回答原文中的依据', exact=True).fill(ANSWER)
                        existing = manual.locator('details.refine-existing-clauses')
                        if await existing.get_attribute('open') is None:
                            await existing.locator(':scope > summary').click()
                        require(await existing.get_by_role('checkbox').count() == 1,
                                'manual targets included unrelated selected clauses')
                        await existing.get_by_role('checkbox').check()
                        await pane.get_by_role('button', name='核对修改建议', exact=True).click()
                        preview = pane.locator('.connected-review')
                        await expect(preview).to_be_visible()
                        await expect(preview.locator('.connected-group')).to_have_count(1)
                        require('修改前：' + ANSWER in await preview.inner_text()
                                and '修改后：' + ANSWER in await preview.inner_text(),
                                'manual preview omitted the actual before/after clause')
                        require(not await preview.get_by_role('checkbox').is_checked(),
                                'preview silently selected approval')
                        return preview

                    preview = await manual_preview()
                    require(store.get(pid)['items'] == frozen_items
                            and store.get(pid)['questions'][0]['understanding_status'] == 'pending',
                            'preview mutated clauses or approved application')
                    trace['checks'].append('explicit_manual_targets_preview_before_after_without_auto_selection')
                    await page.screenshot(path=str(args.output / 'manual-before-after.png'), full_page=True)

                    await answer.fill(ANSWER + '仍需核对显示文案。')
                    await expect(pane.locator('.connected-review')).to_have_count(0)
                    require(not store.records(pid, 'decision_receipt'), 'editing answer committed old review')
                    await answer.fill(ANSWER)
                    trace['checks'].append('answer_edit_discards_old_preview_without_commit')
                    preview = await manual_preview()

                    with store.edit(pid, store.get(pid)['revision'], '隔离并发版本变化') as (project, _):
                        project['name'] = '合成储能云回答引导验收（并发版本）'
                    after_concurrent = copy.deepcopy(store.get(pid))
                    await preview.get_by_role('checkbox').check()
                    await preview.get_by_role('button', name='确认并继续', exact=True).click()
                    await expect(pane.get_by_role('alert')).to_contain_text('尚未写入修改')
                    require(store.get(pid) == after_concurrent, 'stale review changed project state')
                    require(not store.records(pid, 'decision_receipt'), 'stale review produced a receipt')
                    require(any(row['path'].endswith('/decision-review/commit') and row.get('status') == 409
                                for row in trace['api_requests']), 'version rejection was not enforced by server')
                    await expect(pane.get_by_role('heading', name=QUESTION, exact=True)).to_be_visible()
                    trace['checks'].append('concurrent_version_rejects_commit_without_changes')
                    await page.screenshot(path=str(args.output / 'version-conflict.png'), full_page=True)

                    # Reload after the failed commit has refreshed current data.
                    # Raw answer was already saved; draft-only review controls
                    # must be selected again by actual user interaction.
                    await page.reload(wait_until='networkidle')
                    await expect(pane.get_by_label('我的回答原文')).to_have_value(ANSWER)
                    preview = await manual_preview()
                    await preview.get_by_role('button', name='保存核对结果（仍需完善）', exact=True).click()
                    partial = await wait_store(store, pid,
                        lambda p: p['questions'][0].get('decision_coverage') == 'clear',
                        'partial confirmation did not preserve reviewed coverage')
                    require(partial['questions'][0]['understanding_status'] == 'pending'
                            and partial['items'] == frozen_items,
                            'coverage-only confirmation applied an unselected clause')
                    await expect(pane.get_by_role('heading', name=QUESTION, exact=True)).to_be_visible()
                    await expect(pane.get_by_role('status')).to_contain_text('仍有未落实的内容')
                    require(len(store.records(pid, 'decision_receipt')) == 1,
                            'partial review receipt missing or duplicated')
                    trace['checks'].append('partial_coverage_confirmation_stays_current_and_explains_remaining_work')
                    preview = await manual_preview()
                    await preview.get_by_role('checkbox').check()
                    await preview.get_by_role('button', name='确认并继续', exact=True).click()
                    final = await wait_store(store, pid,
                        lambda p: p['questions'][0].get('understanding_status') == 'applied',
                        'explicit manual review did not complete answer application')
                    require(final['questions'][0]['decision_coverage'] == 'clear', 'coverage was not confirmed')
                    require(set(final['questions'][0].get('answer_applications', {})) == {'RULE-0001'},
                            'answer application changed target or omitted its review record')
                    require(final['items'] == frozen_items, 'manual review created a candidate or altered a clause')
                    require(len(store.records(pid, 'decision_receipt')) == 2, 'successful review receipt missing or duplicated')
                    await expect(work.locator('.refine-current-heading h3')).to_have_text('产品或系统')
                    require(await work.locator('.connected-question-side').get_attribute('hidden') is not None,
                            'completed answer editor still occupies the current task')
                    require(await work.locator('.refine-task-current main:visible').count() == 1,
                            'confirmed answer did not expose the next single task')
                    trace['checks'].append('manual_commit_completes_server_understanding_without_candidates_or_model')
                    await page.screenshot(path=str(args.output / 'manual-completed-next.png'), full_page=True)
                    work_bounds = await work.bounding_box()
                    heading_bounds = await work.locator('.refine-current-heading').bounding_box()
                    trace['next_task_bounds'] = dict(work=work_bounds, heading=heading_bounds)
                    require(heading_bounds and work_bounds and heading_bounds['y'] >= work_bounds['y']
                            and heading_bounds['y'] + heading_bounds['height'] <= work_bounds['y'] + work_bounds['height'],
                            'automatically continued task heading is outside current viewport')
                    require(store.get(pid) == final, 'automatic next-task navigation changed business state')
                    trace['checks'].append('confirmed_answer_advances_to_next_visible_task_without_auto_stage_approval')

                    # Three unanswered questions reveal wrapping and stale
                    # selection bugs that a one-question fixture cannot detect.
                    await page.get_by_role('button', name=ORDER_NAME).first.click()
                    await work.locator('.connected-steps').get_by_role('button', name='完善需求').click()
                    plans_before_order = sum(row['path'].endswith('/actions/plan') for row in trace['api_requests'])
                    queue = work.get_by_role('navigation', name='完善需求待办')
                    await queue.locator(':scope > button').filter(has_text=ORDER_QUESTIONS[1]).click()
                    await expect(pane.get_by_role('heading', name=ORDER_QUESTIONS[1], exact=True)).to_be_visible()
                    await work.get_by_role('button', name='暂留此项，继续下一项 →', exact=True).click()
                    await expect(pane.get_by_role('heading', name=ORDER_QUESTIONS[2], exact=True)).to_be_visible()
                    require(not store.get(order_pid).get('decision_answers'), 'skip navigation saved or approved answers')
                    trace['checks'].append('next_from_middle_question_follows_visible_order_without_wrapping')
                    await queue.locator(':scope > button').filter(has_text=ORDER_QUESTIONS[1]).click()
                    stale_manual = pane.locator('details.refine-manual-answer')
                    if await stale_manual.get_attribute('open') is None:
                        await stale_manual.locator(':scope > summary').click()
                    stale_coverage = stale_manual.locator('details.connected-coverage')
                    if await stale_coverage.get_attribute('open') is None:
                        await stale_coverage.locator(':scope > summary').click()
                    await stale_coverage.get_by_role('combobox').select_option('partial')
                    await pane.get_by_label('我的回答原文').fill('审核完成后手工选择生效时间。')
                    await pane.get_by_role('button', name='保存回答并继续', exact=True).click()
                    await expect(pane.get_by_role('heading', name=ORDER_QUESTIONS[2], exact=True)).to_be_visible()
                    order_saved = store.get(order_pid)
                    require(len(order_saved.get('decision_answers', [])) == 1
                            and order_saved['decision_answers'][0]['question_id'] == 'QG-order-1',
                            'continue saved wrong question or more than the current answer')
                    require(all(question['decision_coverage'] == 'pending'
                                and question['understanding_status'] == 'pending'
                                for question in order_saved['questions']), 'save and continue approved an answer')
                    trace['checks'].append('saving_middle_answer_advances_only_to_next_unanswered_question')
                    trace['checks'].append('saving_changed_raw_answer_invalidates_stale_manual_coverage_and_continues')
                    await pane.get_by_label('我的回答原文').fill('在电价模板详情查看生效结果。')
                    await pane.get_by_role('button', name='保存回答并继续', exact=True).click()
                    await expect(pane.get_by_role('heading', name=ORDER_QUESTIONS[2], exact=True)).to_be_visible()
                    await expect(pane.get_by_role('button', name='让 AI 整理这条回答', exact=True)).to_be_enabled()
                    require(len(store.get(order_pid).get('decision_answers', [])) == 2,
                            'last answer not saved exactly once')
                    require(store.get(order_pid)['questions'][0]['status'] == 'open',
                            'unanswered first question was silently completed')
                    require(await work.get_by_role('heading', name='确认交付版本', exact=True).count() == 0,
                            'saving last raw answer bypassed content review and opened delivery')
                    trace['checks'].append('last_raw_answer_stays_for_review_without_wrapping_or_approving_unanswered_siblings')
                    await page.screenshot(path=str(args.output / 'last-answer-needs-review.png'), full_page=True)
                    # The final visible task must lead to the checklist even
                    # though earlier pending questions still remain.
                    task_buttons = queue.locator(':scope > button')
                    await task_buttons.last.click()
                    final_task_title = await work.locator('.refine-current-heading h3').inner_text()
                    await work.get_by_role('button', name='查看剩余事项 →', exact=True).click()
                    await expect(work.locator('.refine-current-heading h3')).to_have_text('确认需求核对')
                    require(final_task_title != ORDER_QUESTIONS[0], 'fixture did not exercise a final task')
                    require(store.get(order_pid)['questions'][0]['status'] == 'open',
                            'checklist navigation approved earlier missing answer')
                    require(sum(row['path'].endswith('/actions/plan') for row in trace['api_requests']) == plans_before_order,
                            'save/continue or queue navigation silently planned a model call')
                    require(not store.records(order_pid, 'decision_receipt'),
                            'raw answer navigation produced a confirmation receipt')
                    trace['checks'].append('last_task_goes_to_checklist_instead_of_looping_to_first_pending_task')
                    require(provider.calls == 0 and trace['executed_task_requests'] == 0, 'model execution occurred')
                    require(not trace['page_errors'], 'browser runtime errors: ' + repr(trace['page_errors']))
                    trace['status'] = 'executed_checks_passed'
                except Exception as error:
                    trace['status'] = 'failed'
                    trace['failure'] = str(error)
                    await page.screenshot(path=str(args.output / 'failure.png'), full_page=True)
                    (args.output / 'failure.html').write_text(await page.content(), encoding='utf-8')
                    raise
                finally:
                    trace['provider_calls'] = provider.calls
                    (args.output / 'result.json').write_text(json.dumps(trace, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
                    await browser.close()
    print(json.dumps(dict(status=trace['status'], checks=trace['checks'], provider_calls=trace['provider_calls']), ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--static-build', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    asyncio.run(run(parser.parse_args()))
