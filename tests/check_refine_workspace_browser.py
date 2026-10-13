"""Real browser / isolated ASGI checks for the three-step refinement workspace.

No daily data, external provider, or formal confirmation is permitted. The build
is intercepted on the formal 8765 origin; no second server is started.
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
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.main import create_app
from app.provider import Provider
from app.core import now
from tests.helpers import prepared

ORIGIN = 'http://127.0.0.1:8765'


def require(condition, message):
    if not condition:
        raise AssertionError(message)


class NoModelProvider(Provider):
    def __init__(self, folder):
        super().__init__(env_path=folder / 'absent.env', credential_dir=folder / 'credentials')
        self.calls = 0

    async def request(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError('Model requests are forbidden in refinement checks')


async def wait_store(store, pid, predicate, message):
    for _ in range(80):
        current = store.get(pid)
        if predicate(current):
            return current
        await asyncio.sleep(.1)
    raise AssertionError(message)


def seed(store):
    project = prepared(store)
    pid = project['id']
    with store.edit(pid, project['revision'], '冻结合成储能云需求') as (p, _):
        p['name'] = '合成储能云范围核对'
        req = next(i for i in p['items'] if i['id'] == 'REQ-0001')
        req.update(title='储能站电价时段维护', statement='云平台管理员维护储能站电价时段，保留历史版本。', selection_status='candidate', change_type='new')
        second = copy.deepcopy(req)
        second.update(id='REQ-0002', title='电价版本查询', statement='云平台管理员按储能站查询已保存的电价历史版本。', related_refs=[])
        p['items'].append(second)
        base_question = copy.deepcopy(p['questions'][0])
        p['questions'].append(dict(base_question, id='Q-CHANGE-RECORD', topic_key='change-record', question='电价修改记录需要保留哪些信息？', related_refs=['REQ-0001']))
        p['questions'].append(dict(base_question, id='Q-VERSION-QUERY', topic_key='version-query', question='历史电价版本查询按什么条件筛选？', related_refs=['REQ-0002']))
        # Scroll-only synthetic requirements remain outside scope and candidate.
        for index in range(1, 9):
            filler = copy.deepcopy(second)
            filler.update(id=f'REQ-LONG-{index:02d}', title=f'合成储能云滚动核对条目 {index}',
                          statement=f'冻结合成条款 {index}，仅验证长列表操作位置，不作为实际业务需求。', related_refs=[])
            p['items'].append(filler)
        p['sources'][0]['excerpts'][0]['text'] = '云平台管理员维护储能站电价时段，保留历史版本；按储能站查询已保存的电价历史版本；重叠拒绝。'
        p.setdefault('product_context', {})['scope_ids'] = []
        p['workspace_draft'] = dict(text='本期核对储能云电价维护和历史版本查询。', entry_mode='direct', saved_at=now())
        p['workflow_mode'] = 'shared-draft'
    store.record(pid, 'user_task', dict(action='organize', label='合成整理当前信息', status='succeeded', created=now(),
        input_revision=store.get(pid)['revision'], source_ids=['SRC-0001'], stages=['ingest'], run_ids=[], max_calls=1,
        calls=0, cost=None, message='合成离线任务记录，非模型结果', completed_steps=1,
        step_states=[dict(stage='ingest', status='succeeded')]), 'TASK-REFINE-FIXTURE')
    return pid


async def run(args):
    build = args.static_build.resolve()
    require((build / 'index.html').is_file(), 'Static build index.html missing')
    args.output.mkdir(parents=True, exist_ok=True)
    trace = dict(scope='formal_8765_static_isolated_asgi', static_build=str(build), checks=[], api_requests=[], page_errors=[],
                 external_model_calls=0, formal_confirmation='NOT_RUN', viewport_checks=[],
                 not_run=['real model output', 'formal document review or confirmation', 'revision-candidate decision application', 'Office materials'])
    with tempfile.TemporaryDirectory(prefix='ra-refine-browser-') as scratch:
        folder = Path(scratch)
        provider = NoModelProvider(folder)
        app = create_app(folder / 'data', provider=provider)
        store = app.state.store
        pid = seed(store)
        other_pid = store.create('另一合成项目')['id']
        trace['project_id'] = pid
        trace['other_project_id'] = other_pid
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN, timeout=30) as client:
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(headless=True)
                context = await browser.new_context(viewport=dict(width=1280, height=720))

                async def dispatch(route):
                    request = route.request
                    parsed = urlsplit(request.url)
                    path = parsed.path
                    if path.startswith('/api/'):
                        require(not (request.method != 'GET' and (path.endswith('/actions') or '/confirm' in path or '/runs' in path)), 'Forbidden task or confirmation API: ' + path)
                        headers = {k: v for k, v in request.headers.items() if k.lower() not in ('host', 'content-length', 'connection', 'accept-encoding')}
                        headers['host'] = '127.0.0.1:8765'
                        response = await client.request(request.method, path + ('?' + parsed.query if parsed.query else ''), content=request.post_data_buffer, headers=headers)
                        trace['api_requests'].append(dict(method=request.method, path=path, status=response.status_code))
                        await route.fulfill(status=response.status_code, headers={'content-type': response.headers.get('content-type', 'application/json')}, body=response.content)
                        return
                    require(parsed.netloc == '127.0.0.1:8765', 'External request forbidden: ' + request.url)
                    target = (build / (path.lstrip('/') or 'index.html')).resolve()
                    require(target.is_relative_to(build) and target.is_file(), 'Missing static asset: ' + path)
                    await route.fulfill(status=200, content_type=mimetypes.guess_type(target.name)[0] or 'application/octet-stream', body=target.read_bytes())

                await context.route('**/*', dispatch)
                page = await context.new_page()
                page.on('pageerror', lambda error: trace['page_errors'].append(str(error)))
                page.set_default_timeout(12000)
                try:
                    await page.goto(ORIGIN, wait_until='networkidle')
                    await page.get_by_role('button', name='合成储能云范围核对').first.click()
                    work = page.locator('.connected-workspace:visible')
                    details = page.locator('.project-workspace:visible details.connected-details')
                    nav = work.locator('.connected-steps')
                    async def refine():
                        await nav.get_by_role('button', name='完善需求').click()
                        await work.locator('.refine-requirements').wait_for()
                        require(await details.get_attribute('open') is None, 'Refinement opened old five-step details')

                    await refine()
                    trace['checks'].append('refine_enters_without_old_navigation')
                    first = work.locator('#refine-item-' + pid + '-REQ-0001')
                    second = work.locator('#refine-item-' + pid + '-REQ-0002')
                    late_card = work.locator('#refine-item-' + pid + '-REQ-LONG-04')
                    actionbar = work.locator('.refine-actionbar')
                    async def assert_in_view(locator, message):
                        box = await locator.bounding_box()
                        viewport = page.viewport_size
                        work_box = await work.bounding_box()
                        valid = box is not None and work_box is not None and box['x'] >= work_box['x'] - 2 and box['x'] + box['width'] <= work_box['x'] + work_box['width'] + 2 and box['y'] >= max(0, work_box['y']) - 2 and box['y'] + box['height'] <= min(viewport['height'], work_box['y'] + work_box['height']) + 2
                        if not valid:
                            trace['actionbar_geometry'] = await actionbar.evaluate('''(bar)=>{
                              const rect=el=>{const r=el.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height,top:r.top,bottom:r.bottom}};
                              const css=getComputedStyle(bar), ancestors=[];
                              for(let el=bar.parentElement;el;el=el.parentElement){const s=getComputedStyle(el);ancestors.push({tag:el.tagName,class:el.className,position:s.position,overflow:s.overflow,overflowX:s.overflowX,overflowY:s.overflowY,rect:rect(el)})}
                              return {position:css.position,bottom:css.bottom,bar:rect(bar),parent:rect(bar.parentElement),work:rect(bar.closest('.connected-workspace')),ancestors};
                            }''')
                            trace['actionbar_geometry']['button'] = box
                            trace['actionbar_geometry']['check'] = message
                        require(valid, message)
                    await first.get_by_role('button', name='纳入本期', exact=True).click()
                    await second.get_by_role('button', name='纳入本期', exact=True).click()
                    require(await first.locator('input[type=checkbox]').is_disabled() and await second.locator('input[type=checkbox]').is_disabled(), 'Unsaved scope permits adoption selection')
                    await late_card.scroll_into_view_if_needed()
                    scope_save = actionbar.get_by_role('button', name='保存本期范围', exact=True)
                    require(await work.get_by_role('button', name='保存本期范围', exact=True).count() == 1, 'Scope save action duplicated')
                    await assert_in_view(scope_save, 'Scope save action outside workspace viewport at middle card REQ-LONG-04')
                    await page.screenshot(path=str(args.output/'sticky-scope-save.png'))
                    await work.get_by_role('button', name='保存本期范围', exact=True).click()
                    saved = await wait_store(store, pid, lambda p: set(p['product_context']['scope_ids']) == {'REQ-0001', 'REQ-0002'}, 'Scope not saved')
                    require(all(i['selection_status'] == 'candidate' for i in saved['items'] if i['id'] in ('REQ-0001','REQ-0002')), 'Scope save auto-adopted')
                    trace['checks'].append('scope_save_persists_without_adoption')
                    trace['checks'].append('unique_scope_save_stays_in_view_at_late_card')

                    # Card and sidebar share the current requirement context.
                    panel = work.locator('.connected-question-side')
                    picker = panel.locator('#decision-picker-' + pid)
                    await first.get_by_role('button', name='核对关联问题', exact=True).click()
                    require(await picker.locator('option:not([value=""])').evaluate_all('(options)=>options.map(o=>o.value)') == ['Q-0001','Q-CHANGE-RECORD'], 'Sidebar contains unrelated questions for first requirement')
                    require(await panel.get_by_role('heading', name='储能站电价时段维护', exact=True).count() == 1, 'Sidebar title does not identify first requirement')
                    require('refine-current-item' in (await first.get_attribute('class') or ''), 'First current card lacks highlight')
                    await first.scroll_into_view_if_needed()
                    context_picker = await picker.bounding_box()
                    trace['question_context'] = dict(item_id='REQ-0001', question_ids=await picker.locator('option:not([value=""])').evaluate_all('(options)=>options.map(o=>o.value)'), picker_box=context_picker)
                    await page.screenshot(path=str(args.output/'context-questions.png'))
                    await second.get_by_role('button', name='核对关联问题', exact=True).click()
                    require(await picker.locator('option:not([value=""])').evaluate_all('(options)=>options.map(o=>o.value)') == ['Q-VERSION-QUERY'], 'Switching requirement retained unrelated question choices')
                    require(await panel.get_by_role('heading', name='电价版本查询', exact=True).count() == 1, 'Sidebar title failed to follow requirement switch')
                    require('refine-current-item' in (await second.get_attribute('class') or '') and 'refine-current-item' not in (await first.get_attribute('class') or ''), 'Current requirement highlight failed to switch')
                    trace['checks'].append('requirement_context_filters_questions_updates_title_and_highlight')

                    reply = panel.get_by_label('我的回答原文')
                    unsaved_answer = '查询条件尚未确认，这是一段未保存的人工回答。'
                    await reply.fill(unsaved_answer)
                    await first.get_by_role('button', name='核对关联问题', exact=True).click()
                    question_guard = page.get_by_role('dialog', name='未保存的修改', exact=True)
                    await question_guard.wait_for()
                    await question_guard.get_by_role('button', name='留在当前页', exact=True).click()
                    require(await picker.input_value() == 'Q-VERSION-QUERY' and await reply.input_value() == unsaved_answer, 'Cancelled requirement switch changed question or lost unsaved answer')
                    require('refine-current-item' in (await second.get_attribute('class') or ''), 'Cancelled requirement switch moved highlight')
                    require(not any(a.get('text') == unsaved_answer for a in store.get(pid).get('decision_answers', [])), 'Cancelled switch implicitly persisted answer')
                    await reply.fill('')
                    trace['checks'].append('dirty_answer_requirement_switch_guard_cancel_preserves_context')

                    # Use a visible bottom navigation button, so the test does not
                    # scroll to the top before the app records the departure.
                    back = work.get_by_role('button', name='返回提供信息', exact=True)
                    await back.scroll_into_view_if_needed()
                    scroll_before = await work.evaluate('(el)=>el.scrollTop')
                    require(scroll_before > 500, 'Long-list fixture did not establish a meaningful scroll position')
                    await back.click()
                    await refine()
                    scroll_after = await work.evaluate('(el)=>el.scrollTop')
                    scroll_max = await work.evaluate('(el)=>el.scrollHeight-el.clientHeight')
                    # Saved-scope transient notice disappears when remounting. At
                    # the bottom the browser must clamp to the new physical max.
                    expected_scroll = min(scroll_before, scroll_max)
                    require(abs(scroll_after - expected_scroll) <= 8, f'Refinement scroll not restored: {scroll_before} -> {scroll_after}, max={scroll_max}')
                    trace['scroll_restore'] = dict(before=scroll_before, after=scroll_after, returned_max=scroll_max, expected=expected_scroll)
                    trace['checks'].append('refine_scroll_position_restored_after_information_roundtrip')

                    async def select_two():
                        await first.locator('input[type=checkbox]').check()
                        await second.locator('input[type=checkbox]').check()
                        await late_card.scroll_into_view_if_needed()
                        adoption_button = actionbar.get_by_role('button', name='批量核对并采纳（2）', exact=True)
                        require(await work.get_by_role('button', name='批量核对并采纳（2）', exact=True).count() == 1, 'Batch adoption action duplicated')
                        await assert_in_view(adoption_button, 'Batch adoption action outside workspace viewport at middle card REQ-LONG-04')
                        trace['latest_adoption_open_scroll'] = await work.evaluate('(el)=>el.scrollTop')
                        await adoption_button.click()
                        modal = page.get_by_role('dialog', name='核对并批量采纳需求', exact=True)
                        await modal.wait_for()
                        return modal

                    modal = await select_two()
                    open_scroll = trace['latest_adoption_open_scroll']
                    await modal.get_by_role('button', name='取消', exact=True).click()
                    await page.wait_for_timeout(100)
                    cancel_scroll = await work.evaluate('(el)=>el.scrollTop')
                    trace['adoption_cancel_scroll'] = dict(before_open=open_scroll, after_cancel=cancel_scroll,
                        work_box=await work.bounding_box(), bar_box=await actionbar.bounding_box(),
                        note='Middle-card scroll was established before clicking an already visible adoption button; no locator scroll was needed for open/cancel.')
                    require(abs(cancel_scroll-open_scroll)<=8, f'Cancelling adoption displaced middle-card context: {open_scroll} -> {cancel_scroll}')
                    await page.screenshot(path=str(args.output/'midpoint-adoption-cancel.png'))
                    require(not await first.locator('input[type=checkbox]').is_checked() and not await second.locator('input[type=checkbox]').is_checked(), 'Cancel retained temporary selection')
                    require(all(i['selection_status'] == 'candidate' for i in store.get(pid)['items'] if i['id'] in ('REQ-0001','REQ-0002')), 'Cancel adopted candidates')
                    trace['checks'].append('cancel_review_clears_selection_without_adoption')
                    trace['checks'].append('unique_batch_adoption_stays_in_view_at_late_card')

                    # Leaving the refinement component discards transient selection, not scope.
                    await first.locator('input[type=checkbox]').check()
                    await nav.get_by_role('button', name='提供信息').click()
                    await refine()
                    require(not await first.locator('input[type=checkbox]').is_checked(), 'Return reused temporary selection')
                    require(set(store.get(pid)['product_context']['scope_ids']) == {'REQ-0001','REQ-0002'}, 'Navigation lost business scope')
                    trace['checks'].append('leave_return_clears_selection_preserves_scope')

                    await first.locator('input[type=checkbox]').check()
                    await page.get_by_role('button', name='另一合成项目').first.click()
                    await page.locator('.project-workspace:visible .project-identity h1').filter(has_text='另一合成项目').wait_for()
                    await page.get_by_role('button', name='合成储能云范围核对').first.click()
                    await work.locator('.refine-requirements').wait_for()
                    require(not await first.locator('input[type=checkbox]').is_checked(), 'Project switch reused temporary selection')
                    require(set(store.get(pid)['product_context']['scope_ids']) == {'REQ-0001','REQ-0002'}, 'Project switch changed saved scope')
                    require(store.get(other_pid).get('product_context', {}).get('scope_ids', []) == [], 'Selection leaked to another project')
                    trace['checks'].append('project_switch_clears_selection_preserves_each_scope')

                    # Inject a concurrent change after a real review API snapshot.
                    modal = await select_two()
                    current = store.get(pid)
                    with store.edit(pid, current['revision'], '隔离测试并发版本修改') as (p, _):
                        next(i for i in p['items'] if i['id']=='REQ-0002')['statement'] += ' 查询结果保留版本标识。'
                    await modal.locator('.bulk-adoption-actions input[type=checkbox]').check()
                    await modal.get_by_role('button', name='确认并一键采纳 2 条', exact=True).click()
                    await modal.get_by_role('alert').first.wait_for()
                    require(all(i['selection_status']=='candidate' for i in store.get(pid)['items'] if i['id'] in ('REQ-0001','REQ-0002')), 'Stale review adopted old content')
                    require(await modal.get_by_role('button', name='确认并一键采纳 2 条', exact=True).is_disabled(), 'Stale confirmation remains enabled')
                    await modal.get_by_role('button', name='取消', exact=True).click()
                    trace['checks'].append('stale_snapshot_rejected_by_real_api')

                    modal = await select_two()
                    await modal.locator('.bulk-adoption-actions input[type=checkbox]').check()
                    await modal.get_by_role('button', name='确认并一键采纳 2 条', exact=True).click()
                    await modal.wait_for(state='detached')
                    await wait_store(store, pid, lambda p: all(i['selection_status']=='selected' for i in p['items'] if i['id'] in ('REQ-0001','REQ-0002')), 'Confirmed adoption not saved')
                    trace['checks'].append('confirmed_bulk_adoption_updates_both_items')

                    await first.get_by_role('button', name='原处修改', exact=True).click()
                    editor = first.get_by_label('需求原文')
                    changed = '云平台管理员维护储能站电价时段，保留历史版本和修改记录。'
                    await editor.fill(changed)
                    await first.get_by_role('button', name='保存条款修改', exact=True).click()
                    await wait_store(store, pid, lambda p: next(i for i in p['items'] if i['id']=='REQ-0001')['statement']==changed, 'Inline edit not saved')
                    await editor.fill(changed + ' 未保存内容。')
                    await nav.get_by_role('button', name='提供信息').click()
                    guard = page.get_by_role('dialog', name='未保存的修改', exact=True)
                    await guard.wait_for()
                    await guard.get_by_role('button', name='留在当前页', exact=True).click()
                    require(await editor.input_value()==changed+' 未保存内容。', 'Cancelled navigation lost text')
                    require(next(i for i in store.get(pid)['items'] if i['id']=='REQ-0001')['statement']==changed, 'Cancelled navigation saved draft implicitly')
                    await editor.fill(changed)
                    trace['checks'].append('inline_edit_saved_and_dirty_navigation_cancel_preserves_text')

                    await first.get_by_role('button', name='补充验收条件', exact=True).click()
                    acceptance = page.get_by_role('dialog', name='补充关联验收条件', exact=True)
                    require(await acceptance.get_by_label('关联的本期需求').input_value()=='REQ-0001', 'Acceptance lost requirement binding')
                    await acceptance.get_by_label('验收名称').fill('电价修改记录核对')
                    await acceptance.get_by_label('验收原文').fill('管理员保存电价时段后，查询历史版本应看到本次修改记录。')
                    await acceptance.get_by_role('button', name='保存验收候选', exact=True).click()
                    final = await wait_store(store,pid,lambda p:any(i.get('title')=='电价修改记录核对' for i in p['items']),'Acceptance not saved')
                    item = next(i for i in final['items'] if i.get('title')=='电价修改记录核对')
                    require(item['selection_status']=='candidate' and 'REQ-0001' in item['related_refs'], 'Acceptance auto-adopted or misbound')
                    trace['checks'].append('acceptance_candidate_bound_to_selected_requirement')

                    # Isolate an unadopted acceptance so the server exposes its real blocker.
                    with store.edit(pid, final['revision'], '隔离验收候选定位检查') as (p, _):
                        next(i for i in p['items'] if i['id']=='AC-0001')['selection_status']='deferred'
                    await page.reload(wait_until='networkidle')
                    await nav.get_by_role('button', name='确认交付').click()
                    blocked = work.locator('.connected-blocker-row').filter(has=page.get_by_role('button', name='核对关联验收候选'))
                    await blocked.get_by_role('button', name='核对关联验收候选').click()
                    acceptance_card = work.locator('#refine-item-' + pid + '-' + item['id'])
                    await acceptance_card.get_by_label('需求原文').wait_for(state='visible')
                    require(await details.get_attribute('open') is None, 'Acceptance blocker opened the old editor')
                    require(not await first.get_by_label('需求原文').count(), 'Acceptance blocker edited the requirement instead')
                    require(next(i for i in store.get(pid)['items'] if i['id']==item['id'])['selection_status']=='candidate', 'Blocker navigation adopted acceptance')
                    trace['checks'].append('acceptance_blocker_opens_linked_candidate_without_adoption')

                    await work.get_by_role('button', name='查看任务与恢复', exact=True).click()
                    await work.locator('.connected-task-details').wait_for()
                    await work.get_by_role('button', name='查看分析结果', exact=True).click()
                    require(await details.get_attribute('open') is None, 'Task result opened old five-step editor')
                    require(await work.locator('.refine-requirements').is_visible(), 'Task result failed to locate refinement')
                    trace['checks'].append('task_history_result_locates_refine_in_place')

                    await details.locator(':scope > summary').click()
                    require(await details.get_attribute('open') is not None, 'Explicit advanced editor failed to open')
                    await details.get_by_role('button', name='返回完善需求', exact=True).click()
                    require(await details.get_attribute('open') is None, 'Return failed to close advanced editor')
                    require(await work.locator('.refine-requirements').is_visible(), 'Return failed to locate main refinement')
                    trace['checks'].append('explicit_advanced_editor_and_return')

                    for width, height, label in [(1280,720,'desktop'),(390,844,'mobile')]:
                        await page.set_viewport_size(dict(width=width,height=height))
                        close_notice = page.get_by_role('button', name='关闭提示', exact=True)
                        if await close_notice.count():
                            await close_notice.click()
                        await nav.get_by_role('button',name='提供信息').focus()
                        await page.keyboard.press('Tab')
                        require(await page.evaluate('document.activeElement?.tagName')=='BUTTON', 'Stage keyboard focus lost')
                        require(await page.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 2'), label+' horizontal overflow')
                        await nav.scroll_into_view_if_needed()
                        await page.screenshot(path=str(args.output / (label+'.png')), full_page=True)
                        if label == 'desktop':
                            await first.scroll_into_view_if_needed()
                            await page.screenshot(path=str(args.output / 'desktop-adopted-cards.png'))
                        trace['viewport_checks'].append(dict(viewport=label,width=width,height=height,status='executed'))
                    require(not trace['page_errors'], 'Runtime errors: '+repr(trace['page_errors']))
                    require(provider.calls==0, 'Unexpected provider invocation')
                    require(not store.get(pid).get('active_baseline_id'), 'Unexpected formal baseline')
                    trace['status']='executed_checks_passed'
                except Exception as error:
                    trace['status']='failed'
                    trace['failure']=str(error)
                    await page.screenshot(path=str(args.output/'failure.png'),full_page=True)
                    (args.output/'failure.html').write_text(await page.content(),encoding='utf-8')
                    raise
                finally:
                    trace['external_model_calls']=provider.calls
                    (args.output/'result.json').write_text(json.dumps(trace,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
                    await browser.close()
    print(json.dumps(trace,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--static-build',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    asyncio.run(run(parser.parse_args()))
