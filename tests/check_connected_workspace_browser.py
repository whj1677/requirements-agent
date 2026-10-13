"""Formal 8765 workbench + isolated real ASGI APIs, with no model request.

Examples:
  .venv/Scripts/python.exe -X utf8 tests/check_connected_workspace_browser.py
  .venv/Scripts/python.exe -X utf8 tests/check_connected_workspace_browser.py --static-build web/node_modules/.cache/connected-workspace-20261008

The optional build is served through Playwright interception on the formal
origin. API requests always go to create_app with temporary data, never the
daily service. This is UI/API behavior evidence, not model or product acceptance.
"""

import argparse
import asyncio
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
from app.provider import DEFAULT, Provider, origin
from tests.helpers import prepared


ORIGIN = 'http://127.0.0.1:8765'


class OfflineInterviewProvider(Provider):
    """Bounded local reply: no network code path, one interview call only."""

    def __init__(self, folder):
        super().__init__(env_path=folder / 'nonexistent.env', credential_dir=folder / 'credentials')
        self.keys[origin(DEFAULT)] = 'OFFLINE_BROWSER_FIXTURE_ONLY'
        self.requests = []
        self.evidence_calls = 0

    async def request(self, config, messages, evidence=None):
        self.requests.append((config, messages))
        require(len(self.requests) == 1, 'unexpected extra provider request')
        value = {'schema_version': '1.1', 'stage': 'interview',
                 'summary': '已保存你的想法。电价版本边界仍需逐项澄清。',
                 'proposals': [], 'questions': [], 'findings': [], 'used_source_refs': [],
                 'limitations': [], 'result': {'can_organize': True}}
        require(evidence is not None, 'provider call lacked persisted evidence')
        evidence.before_send(config)
        evidence.response(content=json.dumps(value, ensure_ascii=False), http_status=200,
                          finish_reason='stop', usage={'prompt_tokens': 10, 'completion_tokens': 10, 'total_tokens': 20},
                          response_model='OFFLINE_BROWSER_FIXTURE')
        self.evidence_calls += 1
        return value, {'request_model': config['model'], 'response_model': 'OFFLINE_BROWSER_FIXTURE',
                       'usage': {'prompt_tokens': 10, 'completion_tokens': 10, 'total_tokens': 20},
                       'origin': origin(config), 'finish_reason': 'stop'}


def require(condition, message):
    if not condition:
        raise AssertionError(message)


async def wait_store(store, pid, predicate, message, attempts=50):
    for _ in range(attempts):
        project = store.get(pid)
        if predicate(project):
            return project
        await asyncio.sleep(0.1)
    raise AssertionError(message)


def seed(store):
    first = prepared(store)
    with store.edit(first['id'], first['revision'], '合成独立决定与候选') as (project, _):
        old = project['questions'].pop()
        for index, question in enumerate(('电价能否追溯生效？', '迟到电量用哪个电价版本？', '何时重算迟到电量？')):
            project['questions'].append(dict(old, id=f'Q-part-{index}', question=question,
                decision_group_id='QG-price', decision_group_title='电价追溯、选价和重算',
                decision_coverage='pending', related_refs=['RULE-0001'], status='open', answer=None))
        target = next(item for item in project['items'] if item['id'] == 'RULE-0001')
        project['items'].append(dict(target, id='RULE-CANDIDATE', title='未来生效候选',
            statement='新电价只能未来生效。', selection_status='candidate', action='revise', target_item_id='RULE-0001',
            answer_refs=['Q-part-0'], related_refs=['RULE-0001']))
    second = store.create('另一合成项目')
    return first['id'], second['id']


async def run(args):
    static_build = args.static_build.resolve() if args.static_build else None
    if static_build:
        require((static_build / 'index.html').is_file(), 'static build index.html missing')
        require((static_build / 'assets').is_dir(), 'static build assets missing')
    if args.output:
        args.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='ra-connected-browser-') as scratch:
        folder = Path(scratch)
        provider = OfflineInterviewProvider(folder) if args.chat_only else Provider(env_path=folder / 'nonexistent.env', credential_dir=folder / 'credentials')
        app = create_app(folder / 'data', provider=provider)
        store = app.state.store
        pid, other_pid = seed(store)
        trace = {'scope': 'formal_8765_static_with_isolated_real_asgi', 'static_build': str(static_build) if static_build else 'deployed_8765',
                 'project_id': pid, 'other_project_id': other_pid, 'api_requests': [], 'failed_saves': 0, 'page_errors': [], 'checks': []}
        fail_next_draft = False
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN, timeout=30) as client:
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(headless=True)
                context = await browser.new_context(viewport={'width': 1440, 'height': 900}, accept_downloads=True)

                async def dispatch(route):
                    nonlocal fail_next_draft
                    request = route.request
                    path = urlsplit(request.url).path
                    if path.startswith('/api/'):
                        trace['api_requests'].append(request.method + ' ' + path)
                        if fail_next_draft and path.endswith('/workspace-draft') and request.method == 'POST':
                            fail_next_draft = False
                            trace['failed_saves'] += 1
                            await route.fulfill(status=503, content_type='application/json', body=json.dumps({'code': 'TEST_FAILURE', 'message': '隔离测试注入的短暂失败'}, ensure_ascii=False))
                            return
                        headers = {key: value for key, value in request.headers.items() if key.lower() not in ('host', 'content-length', 'connection', 'accept-encoding')}
                        headers['host'] = '127.0.0.1:8765'
                        target = path + ('?' + urlsplit(request.url).query if urlsplit(request.url).query else '')
                        response = await client.request(request.method, target, content=request.post_data_buffer, headers=headers)
                        await route.fulfill(status=response.status_code, headers={'content-type': response.headers.get('content-type', 'application/json')}, body=response.content)
                        return
                    if static_build and (path == '/' or path.startswith('/assets/')):
                        target = (static_build / (path.lstrip('/') or 'index.html')).resolve()
                        require(target.is_relative_to(static_build) and target.is_file(), 'static asset missing: ' + path)
                        await route.fulfill(status=200, content_type=mimetypes.guess_type(target.name)[0] or 'application/octet-stream', body=target.read_bytes())
                        return
                    await route.continue_()

                await context.route('**/*', dispatch)
                page = await context.new_page()
                page.on('pageerror', lambda error: trace['page_errors'].append(str(error)))
                await page.goto(ORIGIN + '/', wait_until='networkidle')
                if args.chat_only:
                    await page.get_by_role('button', name='合成工程验证项目').first.click()
                    work = page.locator('.connected-workspace:visible')
                    await work.wait_for()
                    original_items = [(i['id'], i['selection_status'], i['statement']) for i in store.get(pid)['items']]
                    await work.get_by_role('button', name='直接填写需求', exact=True).click()
                    await work.get_by_label('填写当前需求的原文底稿').fill('本次底稿仅描述电价版本边界，历史重算仍待澄清。')
                    await work.get_by_role('button', name='保存工作底稿').click()
                    await work.get_by_text('底稿已保存；候选建议仍须逐条审核。').wait_for()
                    await work.get_by_text('选择本次供模型参考的项目资料').click()
                    choices = work.locator('details').filter(has_text='选择本次供模型参考的项目资料').first.locator('input[type=checkbox]')
                    await choices.first.uncheck()
                    for index in range(1, await choices.count()):
                        if await choices.nth(index).is_checked():
                            await choices.nth(index).uncheck()
                    await work.get_by_role('button', name='保存项目语境').click()
                    await wait_store(store, pid, lambda p: (p.get('domain_context') or {}).get('selected_source_ids') == [], 'source selection was not saved')
                    await work.get_by_role('button', name='和助手聊需求').click()
                    await work.get_by_label('你的想法').fill('先聊聊电价版本什么时候生效。')
                    await work.get_by_role('button', name='发送', exact=True).click()
                    await wait_store(store, pid, lambda p: any(m.get('role') == 'assistant' and m.get('stage') == 'interview' for m in p['messages']), 'offline interview did not finish', attempts=100)
                    final = store.get(pid)
                    require(len(provider.requests) == 1, 'interview invoked provider more than once')
                    require(provider.evidence_calls == 1, 'provider evidence lifecycle was incomplete')
                    sent = json.dumps(provider.requests[0][1], ensure_ascii=False)
                    require('本次底稿仅描述电价版本边界' in sent and '管理员维护电价时段；重叠拒绝' not in sent,
                            'selected source scope was not applied to actual interview input')
                    request_names = trace['api_requests']
                    require(any(name.endswith('/workspace-draft') and name.startswith('POST ') for name in request_names)
                            and request_names.index('POST /api/projects/' + pid + '/actions/plan')
                            < request_names.index('POST /api/projects/' + pid + '/actions'),
                            'chat did not plan before submitting with current revision')
                    require([(i['id'], i['selection_status'], i['statement']) for i in final['items']] == original_items,
                            'chat changed or adopted clauses')
                    require(final['workflow_mode'] == 'shared-draft' and final['workspace_draft']['entry_mode'] == 'chat',
                            'chat did not first save shared draft mode')
                    require(final['domain_context']['selected_source_ids'] == [], 'chat widened explicit reference scope')
                    trace['checks'].append('chat_before_send_revision_and_selected_sources')
                    trace['provider_calls'] = len(provider.requests)
                    require(not trace['page_errors'], 'browser runtime errors: ' + repr(trace['page_errors']))
                    if args.output:
                        (args.output / 'chat-result.json').write_text(json.dumps(trace, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
                    print(json.dumps({'checks': trace['checks'], 'provider_calls': trace['provider_calls'],
                                      'page_errors': trace['page_errors']}, ensure_ascii=False))
                    await browser.close()
                    return
                if args.scope_only:
                    await page.get_by_role('button', name='另一合成项目').click()
                    await page.locator('.connected-workspace:visible').wait_for()
                    await page.locator('.connected-workspace:visible').get_by_role('button', name='直接填写需求', exact=True).click()
                    await page.locator('.connected-workspace:visible').get_by_label('填写当前需求的原文底稿').fill('新项目需要明确本期需求。')
                    await page.locator('.connected-workspace:visible').get_by_role('button', name='保存工作底稿').click()
                    await page.locator('.connected-workspace:visible').get_by_text('底稿已保存；候选建议仍须逐条审核。').wait_for()
                    await page.locator('.connected-workspace:visible .connected-steps').get_by_role('button', name='完善需求').click()
                    require(await page.locator('.connected-workspace:visible').get_by_role('button', name='补充验收条件').count() == 0, 'acceptance shown without a scoped requirement')
                    await page.locator('#refine-scope-' + other_pid).wait_for(state='visible')
                    require(await page.locator('.project-workspace:visible details.connected-details').get_attribute('open') is None, 'refine opened advanced stages')
                    trace['checks'].append('acceptance_prerequisite_locates_scope_editor')
                    require(not trace['page_errors'], 'browser runtime errors: ' + repr(trace['page_errors']))
                    if args.output:
                        (args.output / 'scope-result.json').write_text(json.dumps(trace, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
                    print(json.dumps({'checks': trace['checks'], 'page_errors': trace['page_errors']}, ensure_ascii=False))
                    await browser.close()
                    return
                await page.get_by_role('button', name='合成工程验证项目').first.click()
                await page.locator('.connected-workspace:visible').wait_for()
                require(await page.get_by_role('button', name='和助手聊需求').count() == 1, 'formal shared workspace unavailable')
                await page.get_by_role('button', name='和助手聊需求').click()
                require(await page.locator('.connected-workspace:visible .intake-chat').count() == 1, 'chat mounted more than once')
                await page.get_by_label('你的想法').fill('先讨论电价版本的生效边界。')
                await page.get_by_role('button', name='直接填写需求', exact=True).click()
                await page.get_by_role('button', name='和助手聊需求').click()
                require(await page.get_by_label('你的想法').input_value() == '先讨论电价版本的生效边界。', 'entry switch lost unsent chat input')
                trace['checks'].append('formal_workspace_loaded')

                # Direct input is a real shared draft, and transient save failure keeps it.
                await page.get_by_role('button', name='直接填写需求', exact=True).click()
                draft_box = page.get_by_label('填写当前需求的原文底稿')
                await draft_box.fill('储能云平台：电价版本只允许未来生效，历史选价和重算仍待确认。')
                fail_next_draft = True
                await page.get_by_role('button', name='保存工作底稿').click()
                await page.locator('.connected-error').filter(has_text='隔离测试注入的短暂失败').wait_for()
                require('历史选价和重算仍待确认' in await draft_box.input_value(), 'failed save lost working input')
                await page.get_by_role('button', name='保存工作底稿').click()
                await page.get_by_text('底稿已保存；候选建议仍须逐条审核。').wait_for()
                saved = store.get(pid)
                require(saved['workflow_mode'] == 'shared-draft' and '历史选价' in saved['workspace_draft']['text'], 'draft did not persist through real API')
                trace['checks'].append('direct_draft_failure_recovery_and_persist')

                # Existing candidate remains editable in place; discussion is a real server projection.
                await page.locator('.connected-steps').get_by_role('button', name='完善需求').click()
                card = page.locator('.connected-item').filter(has_text='电价时段维护')
                await card.get_by_role('button', name='原处修改').click()
                editor = card.get_by_role('textbox', name='需求原文', exact=True)
                await editor.fill('管理员能够维护本期电价时段，并保留历史版本。')
                await page.get_by_role('button', name='保存条款修改').click()
                await wait_store(store, pid, lambda p: '保留历史版本' in next(i for i in p['items'] if i['id'] == 'REQ-0001')['statement'], 'in-place item edit not saved')
                await page.locator('.connected-steps').get_by_role('button', name='确认交付').click()
                await page.get_by_role('button', name='预览当前讨论稿').click()
                await page.get_by_text('待确认 / 非正式交付').wait_for()
                require('保留历史版本' in await page.locator('.connected-discussion').inner_text(), 'discussion draft omitted saved item')
                async with page.expect_download() as download_info:
                    await page.get_by_role('button', name='下载讨论稿').click()
                download = await download_info.value
                require(download.suggested_filename.endswith('-讨论稿.md'), 'discussion download filename wrong')
                trace['checks'].append('in_place_edit_and_real_discussion_preview_download')
                blocker_actions = page.locator('.connected-blocker-row .connected-actions button')
                if await blocker_actions.count():
                    await blocker_actions.first.click()
                    require(await page.locator('.project-workspace:visible').count() == 1, 'delivery blocker lost current project')
                    trace['checks'].append('delivery_blocker_locates_processing_area')

                # A saved group answer covers one child, binds one candidate, and leaves siblings open.
                await page.locator('.connected-steps').get_by_role('button', name='完善需求').click()
                await page.get_by_label('我的回答原文').fill('新电价只能未来生效。')
                await page.get_by_role('button', name='保存原话').click()
                await page.get_by_text('原话已保存；每个决定是否明确仍待逐项核对。').wait_for()
                require('待明确' in await page.locator('.connected-side').inner_text(), 'saved raw answer was not visibly distinct from decision coverage')
                if args.output:
                    await page.locator('.connected-side').screenshot(path=str(args.output / 'saved-answer-pending.png'))
                saved_answer = await wait_store(store, pid, lambda p: bool(p.get('decision_answers')), 'group answer not saved')
                answer_record = saved_answer['decision_answers'][-1]
                # Model-origin candidates created after this answer carry this server-checked binding.
                # The isolated fixture injects its equivalent without calling a paid model.
                with store.edit(pid, saved_answer['revision'], '合成已绑定修订候选') as (current, _):
                    candidate = next(i for i in current['items'] if i['id'] == 'RULE-CANDIDATE')
                    candidate['decision_answer_bindings'] = {'Q-part-0': {'answer_id': answer_record['id'], 'answer_hash': answer_record['answer_hash']}}
                await page.reload(wait_until='networkidle')
                await page.locator('.connected-steps').get_by_role('button', name='完善需求').click()
                await page.locator('.connected-workspace:visible .connected-coverage summary').first.click()
                first_decision = page.locator('.connected-workspace:visible .connected-decision').filter(has_text='电价能否追溯生效？')
                await first_decision.get_by_label('覆盖判断').select_option('clear')
                await first_decision.get_by_label('回答原文中的依据').fill('新电价只能未来生效。')
                await page.get_by_role('button', name='查看覆盖与修改预览').click()
                await page.get_by_text('本次覆盖与关联修改').wait_for()
                group = page.locator('.connected-group').filter(has_text='未来生效候选')
                await group.locator('input[type=checkbox]').check()
                await page.get_by_role('button', name='确认覆盖并写入所选修改').click()
                await page.wait_for_timeout(500)
                require(not await page.get_by_role('alert').count(),
                    'decision commit failed: ' + ' | '.join(await page.get_by_role('alert').all_inner_texts()))
                await page.locator('.connected-review').wait_for(state='detached')
                require('结果已保留在当前问题' in await page.locator('.connected-side').inner_text(), 'commit did not preserve current context')
                require(await page.get_by_label('选择问题', exact=True).input_value() == 'Q-part-0', 'commit silently switched to another question')
                result = store.get(pid)
                children = {q['id']: q for q in result['questions']}
                require(children['Q-part-0']['status'] == 'answered' and all(children[f'Q-part-{n}']['status'] == 'open' for n in (1, 2)), 'partial answer closed another child')
                require(next(i for i in result['items'] if i['id'] == 'RULE-0001')['statement'] == '新电价只能未来生效。', 'selected candidate not applied')
                trace['checks'].append('partial_answer_single_review_atomic_application')

                # Project change and reload do not reuse a temporary batch selection.
                await page.get_by_role('button', name='查看覆盖与修改预览').click()
                await page.wait_for_timeout(500)
                require(await page.get_by_text('本次覆盖与关联修改').count() > 0,
                    'repeat preview unavailable: ' + ' | '.join(await page.get_by_role('alert').all_inner_texts()))
                require(not await page.locator('.connected-group input[type=checkbox]').first.is_checked() if await page.locator('.connected-group input[type=checkbox]').count() else True, 'selection reused after commit')
                await page.reload(wait_until='networkidle')
                await page.locator('.connected-workspace:visible').wait_for()
                require(await page.locator('.connected-review').count() == 0, 'preview survived reload as temporary selection')
                await page.get_by_role('button', name='另一合成项目').click()
                await page.locator('.connected-workspace:visible').wait_for()
                require(await page.locator('.connected-workspace:visible').get_by_label('填写当前需求的原文底稿').count() <= 1, 'project switching duplicated visible shared draft')
                trace['checks'].append('temporary_selection_cleared_on_refresh_and_project_switch')
                await page.locator('.connected-workspace:visible').get_by_role('button', name='直接填写需求', exact=True).click()
                await page.locator('.connected-workspace:visible').get_by_label('填写当前需求的原文底稿').fill('新项目需要明确本期需求。')
                await page.locator('.connected-workspace:visible').get_by_role('button', name='保存工作底稿').click()
                await page.locator('.connected-workspace:visible').get_by_text('底稿已保存；候选建议仍须逐条审核。').wait_for()
                await page.locator('.connected-workspace:visible .connected-steps').get_by_role('button', name='完善需求').click()
                await page.locator('#refine-scope-' + other_pid).wait_for(state='visible')
                require(await page.locator('.project-workspace:visible details.connected-details').get_attribute('open') is None,
                    'refine opened advanced stages')
                trace['checks'].append('acceptance_prerequisite_locates_scope_editor')

                # The three-stage navigation and controls remain reachable at a narrow viewport.
                await page.set_viewport_size({'width': 390, 'height': 844})
                await page.locator('.connected-steps').get_by_role('button', name='提供信息').focus()
                await page.keyboard.press('Tab')
                require(await page.evaluate('document.activeElement?.tagName') == 'BUTTON', 'keyboard focus lost in stage navigation')
                require(await page.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 2'), 'narrow layout has horizontal overflow')
                if args.output:
                    await page.screenshot(path=str(args.output / 'connected-mobile.png'), full_page=True)
                trace['checks'].append('keyboard_and_narrow_layout')
                await browser.close()
        require(not trace['page_errors'], 'browser runtime errors: ' + repr(trace['page_errors']))
        require(trace['failed_saves'] == 1, 'failure injection did not execute')
        print(json.dumps(trace, ensure_ascii=False, indent=2))
        if args.output:
            (args.output / 'result.json').write_text(json.dumps(trace, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--static-build', type=Path, help='Optional current-source build; intercepted on the existing 8765 origin')
    parser.add_argument('--output', type=Path, help='Optional evidence directory')
    parser.add_argument('--scope-only', action='store_true', help='Only check the acceptance prerequisite and scope locator')
    parser.add_argument('--chat-only', action='store_true', help='Only check an offline provider chat send and selected sources')
    asyncio.run(run(parser.parse_args()))
