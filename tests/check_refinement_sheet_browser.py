"""Single-sheet interaction acceptance, synthetic facts, zero model/network calls.

Playwright intercepts the sole workstation URL into an isolated ASGI app. No
listener starts and no daily data is read. Only fixture setup precedes recording;
all later edits and submission use visible controls and real application routes.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import html
import json
import mimetypes
import os
from pathlib import Path
import re
import sys
import tempfile
import time
import traceback
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
ORIGIN = 'http://127.0.0.1:8765'
QUESTION = '是否允许普通成员修改模板？'
ANSWER = '本例只有管理员且仅能查看，无修改功能。'
GOAL = '供电价管理员只读查看模板与历史。'
BEHAVIOR = ('谁操作', '入口及前置条件', '主要流程', '业务数据', '权限约束', '操作结果', '边界和异常')
GROUP_QUESTIONS = ('模板查看是否允许修改？', '历史查询是否允许修改？')
GROUP_ORIGINAL = '模板查看和历史查询均仅供管理员只读查看，不包含修改功能。'


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def seed_sheet(store):
    from app.core import now
    from app.product_flow import checkpoint, status
    from app.requirement_confirmation import confirmation_current, row_fingerprint
    from tests.check_draft_workload_browser import seed_navigation
    pid = seed_navigation(store)
    with store.edit(pid, store.get(pid)['revision'], '冻结单表验收前置，不属于模型产物') as (p, _):
        p['name'] = '合成储能云：单表确认验收'
        p['product_context']['intent'] = ''
        p['context_field_states']['intent'] = dict(status='unknown')
        req = next(i for i in p['items'] if i['id'] == 'REQ-W1')
        source = p['sources'][0]
        source['excerpts'][0]['text'] += ANSWER
        source['sha256'] = hashlib.sha256(source['excerpts'][0]['text'].encode()).hexdigest()
        source['excerpts'][0]['source_hash'] = source['sha256']
        req['behavior']['permissions'] = ANSWER
        old = copy.deepcopy(req)
        old.update(id='REQ-STALE-CANDIDATE', target_item_id=req['id'],
                   selection_status='candidate', statement='过时 AI 候选：不可覆盖当前已确认原文。')
        p['items'].append(old)
        next(i for i in p['items'] if i['id'] == 'AC-W1')['selection_status'] = 'selected'
        req['behavior_review_required'] = False
        req['requirement_confirmation'] = dict(schema='requirement-confirmation-1',
            actor='authenticated_local_user', content_hash=row_fingerprint(p, req), confirmed_at=now())
        p['questions'] = [dict(id='Q-SHEET-PERMISSIONS', topic_key='sheet-permissions',
            question=QUESTION, why='合成关键决定，用于检验同表回答与相关需求覆盖。',
            options=[], blocking=True, related_refs=['REQ-W1'], source_refs=req['source_refs'],
            status='open', answer=None, decision_group_id='Q-SHEET-PERMISSIONS',
            decision_group_title=QUESTION, decision_coverage='pending')]
        checkpoint(p, 1, status(p)[0]['content_hash'])
    initial = store.get(pid)
    assert confirmation_current(initial, next(i for i in initial['items'] if i['id'] == 'REQ-W1'))
    return pid, initial


def seed_readonly_tariff(store):
    from app.core import dumps, now
    from tests.check_draft_workload_browser import BASE
    initial = json.loads(BASE.read_text('utf-8'))
    assert initial['id'] == 'P-d4b61a83e6f14612' and initial['revision'] == 13
    with store.connect() as db:
        db.execute('INSERT INTO projects VALUES(?,?,?)', (initial['id'], initial['revision'], dumps(initial)))
        db.execute('INSERT INTO revisions VALUES(?,?,?,?,?)', (initial['id'], initial['revision'], dumps(initial), '只读电价管理v13冻结副本', now()))
    return initial['id'], initial


def seed_group_scope(store):
    from app.decision_review import save_raw_answer
    from app.product_flow import checkpoint, status
    pid, _ = seed_sheet(store)
    with store.edit(pid, store.get(pid)['revision'], '冻结组原话与范围验收前置') as (p, _):
        p['name'] = '合成储能云：组答案与本期范围验收'
        p['workflow_mode'] = 'shared-draft'
        p['product_context']['intent'] = GOAL
        p['context_field_states']['intent'] = dict(status='known')
        prototype = p['questions'][0]
        p['questions'] = [dict(prototype, id='Q-GROUP-' + str(n),
            question=question, related_refs=['REQ-W' + str(n)],
            decision_group_id='QG-SHEET', decision_group_title='查看功能是否允许修改')
            for n, question in enumerate(GROUP_QUESTIONS, 1)]
        save_raw_answer(p, 'QG-SHEET', GROUP_ORIGINAL, store)
        checkpoint(p, 1, status(p)[0]['content_hash'])
    initial = store.get(pid)
    assert all(q['answer'] is None and q['status'] == 'open' for q in initial['questions'])
    return pid, initial


async def inspect_group_scope(page, work, table, confirm, rec, store, pid, initial, report, output, expect):
    answers = [table.get_by_role('textbox', name=q + ' 我的回答', exact=True) for q in GROUP_QUESTIONS]
    for answer in answers:
        await expect(answer).to_have_value('')
    assert await table.locator('.sheet-group-reference').count() == 2
    for reference in await table.locator('.sheet-group-reference').all():
        await expect(reference).to_contain_text(GROUP_ORIGINAL)
    await rec.note('1. 这是一组已保存原话、尚未逐项确认的问题。原话仅作参考，不自动填成每个问题的答案。')
    await answers[0].scroll_into_view_if_needed()
    await page.screenshot(path=str(output / '01-group-original.png'))
    await rec.click(work.get_by_role('navigation', name='需求工作流程').get_by_role('button', name=re.compile(r'^3\s*确认交付$')))
    await expect(table).to_be_visible()
    await expect(work.get_by_role('alert').filter(has_text='请点击表格底部“确认并进入交付”')).to_be_visible()
    await expect(work.get_by_role('heading', name='确认交付版本', exact=True)).not_to_be_visible()
    assert store.get(pid) == initial and not [r for r in report['api_requests'] if r['method'] != 'GET']
    report['checks'].append('顶部第3步不能绕过表格确认，停留本表并指向唯一确认入口')
    await rec.fill(answers[0], ANSWER)
    await rec.click(confirm)
    await expect(answers[1]).to_be_focused()
    await expect(answers[1].locator('xpath=ancestor::tr')).to_have_class(re.compile(r'sheet-invalid'))
    assert store.get(pid) == initial and not [r for r in report['api_requests'] if r['method'] != 'GET']
    report['checks'].append('已保存的组原话不会自动代答未确认子问题；只答一项仍定位到未答子问题且不写入')
    await rec.note('2. 只确认一个子问题时，另一项仍为空并就地提示；没有把组原话当作全部问题已完成。')
    await page.screenshot(path=str(output / '02-unanswered-leaf.png'))
    scope = table.get_by_role('combobox', name='历史查询 本期范围', exact=True)
    await scope.select_option('excluded')
    deferred = table.locator('tr[data-cell-key="question-Q-GROUP-2"]')
    await expect(answers[1]).not_to_be_visible()
    await expect(deferred).to_contain_text('本期以外')
    await expect(deferred).to_contain_text('关联需求已暂不纳入本期，本次保留原话，不确认回答。')
    assert await deferred.locator('.sheet-required').count() == 0
    await scope.select_option('included')
    await expect(answers[1]).to_have_value('')
    await expect(answers[1].locator('xpath=ancestor::tr').locator('.sheet-required')).to_be_visible()
    await scope.select_option('excluded')
    await deferred.scroll_into_view_if_needed()
    await rec.note('3. 暂不纳入历史查询后，其专属问题同步暂缓，不强制编答案。重新纳入会恢复空白必答项。')
    await page.screenshot(path=str(output / '03-deferred-with-scope.png'))
    await rec.click(confirm)
    await expect(work.get_by_role('heading', name='确认交付版本', exact=True)).to_be_visible(timeout=15000)
    await expect(work.get_by_role('alert').filter(has_text='请点击表格底部“确认并进入交付”')).not_to_be_visible()
    saved = store.get(pid)
    writes = [r for r in report['api_requests'] if r['method'] != 'GET']
    assert len(writes) == 1 and writes[0]['response']['confirmed'], writes
    body = writes[0]['body']
    assert [r['requirement_id'] for r in body['rows']] == ['REQ-W1']
    assert body['answers'] == [dict(question_id='Q-GROUP-1', answer=ANSWER)]
    omitted = next(q for q in saved['questions'] if q['id'] == 'Q-GROUP-2')
    assert omitted['answer'] is None and omitted['status'] == 'open'
    assert omitted['scope_exclusion']['requirement_ids'] == ['REQ-W2']
    assert saved['product_context']['scope_ids'] == ['REQ-W1']
    report['checks'].append('移出本期的专属问题不死锁、不提交虚构答案；重新纳入恢复必填，本期一次确认后自动进入交付且旧导航提示清除')
    await rec.note('4. 一次确认进入交付；仅保存本期内容与已明确答案，范围外子问题保留待处理。')
    await page.screenshot(path=str(output / '04-scoped-delivery.png'))
    write(output / 'project-after.json', saved)


async def inspect_real_sheet(page, work, table, confirm, rec, store, pid, initial, report, output, expect):
    from app.requirement_confirmation import confirmation_state
    from tests.check_draft_workload_browser import BASE
    report['snapshot_sha256'] = hashlib.sha256(BASE.read_bytes()).hexdigest()
    report['project_revision'] = initial['revision']
    report['source_count'] = len(initial['sources'])
    states = {r['requirement_id']: r for r in confirmation_state(initial)['rows']}
    reqs = [r for r in initial['items'] if r['kind'] == 'requirement' and r.get('applies_to') == 'to_be' and not r.get('target_item_id') and not r.get('superseded_by') and r.get('selection_status') not in ('rejected', 'deferred', 'excluded')]
    assert len(reqs) == 10
    assert await table.locator('tr[data-requirement-id]').count() == len(reqs)
    report['rendered_requirement_count'] = len(reqs)
    report['blank_cells'] = []
    for textbox in await table.get_by_role('textbox').all():
        if not (await textbox.input_value()).strip():
            report['blank_cells'].append(await textbox.get_attribute('aria-label'))
    report['visible_required_labels'] = await table.locator('.sheet-required').count()
    report['readiness_text'] = await work.locator('.refinement-sheet-heading').inner_text()
    for req in reqs:
        candidates = [r for r in initial['items'] if r.get('target_item_id') == req['id'] and r['kind'] == 'requirement' and r.get('selection_status') == 'candidate' and not r.get('superseded_by')]
        expected = req if states[req['id']]['current'] else (candidates[-1] if candidates else req)
        await expect(table.get_by_role('textbox', name=req['title'] + ' 需求内容', exact=True)).to_have_value(expected['statement'])
    for field, label in [('intent', '希望达到的目标'), ('users', '目标使用者'), ('change_scope', '本次改动')]:
        await expect(table.get_by_role('textbox', name=label, exact=True)).to_have_value(initial['product_context'][field])
    await expect(confirm).to_be_in_viewport()
    await rec.note('电价管理原始 v13 隔离副本：10 条需求、15 份已保存资料。本段只查看，不修改答案、不提交，不调用模型。')
    await page.screenshot(path=str(output / '01-real-summary.png'))
    first = table.get_by_role('textbox', name=reqs[0]['title'] + ' 需求内容', exact=True)
    await first.scroll_into_view_if_needed()
    await rec.note('需求、业务说明和验收在同一表中。原资料未明确的空项原样保留；不为演示编造答案。')
    await page.screenshot(path=str(output / '02-real-requirement.png'))
    last = table.get_by_role('textbox').last
    await last.scroll_into_view_if_needed()
    await expect(table.locator('thead th').first).to_be_in_viewport()
    await expect(confirm).to_be_in_viewport()
    await page.screenshot(path=str(output / '03-real-questions.png'))
    assert store.get(pid) == initial and not [r for r in report['api_requests'] if r['method'] != 'GET']
    report['checks'].extend(['原始电价管理v13的10条需求与摘要均原样显示，没有更旧候选覆盖或吞掉人工文本',
                             '空项按原数据保留并记录，表内滚动时表头和确认入口可见，未提交或填造答案'])
    report['original_snapshot_unchanged'] = True
    write(output / 'project-after.json', store.get(pid))


async def run(args):
    output, build = args.output.resolve(), args.static_build.resolve()
    assert output.is_relative_to(ROOT / 'evidence') and output != ROOT / 'evidence'
    assert not output.exists() and (build / 'index.html').is_file()
    output.mkdir(parents=True)
    with tempfile.TemporaryDirectory(prefix='ra-sheet-import-') as tmp:
        os.environ['RA_DATA_DIR'] = str(Path(tmp) / 'data')
        import httpx
        from playwright.async_api import async_playwright, expect
        from app.main import create_app
        from app.requirement_confirmation import confirmation_current
        from tests.check_refine_human_flow_browser import Recorder, ANNOTATION
        from tests.check_refine_workspace_browser import NoModelProvider, wait_store

        provider = NoModelProvider(output)
        app = create_app(output / 'data', provider=provider)
        store = app.state.store
        seed = {'real-readonly': seed_readonly_tariff, 'synthetic': seed_sheet, 'group-scope': seed_group_scope}[args.scenario]
        pid, initial = seed(store)
        write(output / 'project-before.json', initial)
        report = dict(status='running', project_id=pid, fixture='FROZEN_TARIFF_V13_READONLY' if args.scenario == 'real-readonly' else 'SYNTHETIC_STORAGE_CLOUD',
            model_calls=0, daily_data_access=False, listener_started=False, api_requests=[],
            checks=[], page_errors=[], static_build=str(build), served_static={},
            model_quality='NOT_RUN', user_business_acceptance='NOT_RUN', formal_delivery='NOT_RUN')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=True)
                context = await browser.new_context(viewport=dict(width=1440, height=900),
                    record_video_dir=str(output / 'raw'), record_video_size=dict(width=1440, height=900))
                await context.add_init_script('(' + ANNOTATION + ')()')
                async def dispatch(route):
                    request, url = route.request, urlsplit(route.request.url)
                    assert url.netloc == '127.0.0.1:8765', 'External requests forbidden'
                    if url.path.startswith('/api/'):
                        assert request.method == 'GET' or args.scenario != 'real-readonly' and url.path.endswith(('/refinement-confirmation', '/actions/plan')), 'Unexpected write: ' + url.path
                        headers = {k: v for k, v in request.headers.items() if k.lower() not in ('host', 'content-length', 'connection', 'accept-encoding')}
                        headers['host'] = '127.0.0.1:8765'
                        response = await client.request(request.method, url.path + ('?' + url.query if url.query else ''),
                            headers=headers, content=request.post_data_buffer)
                        event = dict(method=request.method, path=url.path, status=response.status_code)
                        if request.post_data:
                            event.update(body=request.post_data_json, response=response.json())
                        report['api_requests'].append(event)
                        await route.fulfill(status=response.status_code, body=response.content,
                            content_type=response.headers.get('content-type', 'application/json'))
                    else:
                        path = (build / (url.path.lstrip('/') or 'index.html')).resolve()
                        assert path.is_relative_to(build) and path.is_file()
                        report['served_static'][url.path] = hashlib.sha256(path.read_bytes()).hexdigest()
                        await route.fulfill(status=200, body=path.read_bytes(), content_type=mimetypes.guess_type(path.name)[0] or 'application/octet-stream')
                await context.route('**/*', dispatch)
                page = await context.new_page()
                page.set_default_timeout(15000)
                page.on('pageerror', lambda e: report['page_errors'].append(str(e)))
                class SheetRecorder(Recorder):
                    async def note(self, text, seconds=3):
                        self.chapters.append(dict(seconds=round(time.monotonic()-self.started, 2), text=text))
                        provenance = '电价管理真实 v13 · 隔离只读副本' if args.scenario == 'real-readonly' else '合成储能云项目 · 人工操作验收'
                        await page.evaluate("text=>document.getElementById('recording-note').textContent=text", '录屏说明 · 非产品界面\n' + provenance + '\n0 模型调用\n非正式业务验收\n\n' + text)
                        await page.wait_for_timeout(seconds*1000)
                rec = SheetRecorder(page, output, True)
                try:
                    await page.goto(ORIGIN, wait_until='networkidle')
                    await rec.click(page.locator('aside.sidebar button[title="' + initial['name'] + '"]'))
                    work = page.locator('.connected-workspace:visible')
                    await rec.click(work.get_by_role('button', name='不使用 AI，直接完善', exact=True))
                    table = work.get_by_role('table', name='需求确认表', exact=True)
                    await expect(table).to_be_visible()
                    confirm = work.get_by_role('button', name='确认并进入交付', exact=True)
                    if args.scenario == 'real-readonly':
                        await inspect_real_sheet(page, work, table, confirm, rec, store, pid, initial, report, output, expect)
                    elif args.scenario == 'group-scope':
                        await inspect_group_scope(page, work, table, confirm, rec, store, pid, initial, report, output, expect)
                    else:
                        await expect(confirm).to_be_enabled()
                        assert await confirm.count() == 1
                        for name in ('选择待确认需求', '查看与确认', '查看并确认所选', '保存本期范围'):
                            assert not await work.get_by_role('button', name=name, exact=False).count(), 'Old multi-step action remains: ' + name
                        assert not await table.get_by_role('checkbox').count(), 'Selection/adoption checkbox should not be needed'
                        goal = table.get_by_role('textbox', name='希望达到的目标', exact=True)
                        answer = table.get_by_role('textbox', name=QUESTION + ' 我的回答', exact=True)
                        statement = table.get_by_role('textbox', name='模板查看 需求内容', exact=True)
                        original = next(i for i in initial['items'] if i['id'] == 'REQ-W1')['statement']
                        await expect(statement).to_have_value(original)
                        for name in ('希望达到的目标', '目标使用者', '本次改动', QUESTION + ' 我的回答'):
                            await expect(table.get_by_role('textbox', name=name, exact=True)).to_be_visible()
                        for title in ('模板查看', '历史查询'):
                            await expect(table.get_by_role('combobox', name=title + ' 本期范围', exact=True)).to_be_visible()
                            await expect(table.get_by_role('combobox', name=title + ' 本次改动', exact=True)).to_be_visible()
                            for label in (*BEHAVIOR, '验收建议 1'):
                                await expect(table.get_by_role('textbox', name=title + ' ' + label, exact=True)).to_be_visible()
                        report['checks'].append('摘要、范围、两条需求业务说明、验收和关键问题均直接在同一表；已确认原文优先于旧候选')
                        await rec.note('1. 全部待确认内容在同一表中直接查看和修改。这里是冻结合成资料，不是模型质量演示。')
                        await page.screenshot(path=str(output / '01-sheet.png'))
                        scroller = work.locator('.refinement-sheet-scroll')
                        head = table.locator('thead th').first
                        await expect(head).to_be_in_viewport()
                        await expect(confirm).to_be_in_viewport()
                        before_head = await head.bounding_box()
                        await scroller.hover()
                        await page.mouse.wheel(0, 1400)
                        await page.wait_for_timeout(350)
                        assert await scroller.evaluate('(e)=>e.scrollTop') > 0, 'The table should scroll internally'
                        await expect(head).to_be_in_viewport()
                        await expect(confirm).to_be_in_viewport()
                        after_head = await head.bounding_box()
                        assert abs(after_head['y'] - before_head['y']) < 3, 'Table heading moved away during internal scrolling'
                        report['checks'].append('表内上下滚动时表头与唯一确认按钮始终可见')
                        await page.screenshot(path=str(output / '02-inner-scroll.png'))
                        await rec.click(confirm)
                        await expect(goal).to_be_focused()
                        await expect(goal.locator('xpath=ancestor::tr')).to_have_class(re.compile(r'sheet-invalid'))
                        await expect(goal.locator('xpath=ancestor::tr').get_by_role('alert')).to_be_visible()
                        assert store.get(pid) == initial, 'Invalid submission changed the project'
                        await rec.note('2. 目标为空时，确认不会写入；直接定位到表内缺失单元格。')
                        await page.screenshot(path=str(output / '03-required-cell.png'))
                        await rec.fill(goal, GOAL)
                        await rec.click(confirm)
                        await expect(answer).to_be_focused()
                        await expect(answer.locator('xpath=ancestor::tr')).to_have_class(re.compile(r'sheet-invalid'))
                        await expect(answer.locator('xpath=ancestor::tr').get_by_role('alert')).to_be_visible()
                        assert store.get(pid) == initial, 'Missing critical answer caused partial writes'
                        report['checks'].append('目标与关键答案空缺均就地标记并聚焦，失败不发生部分写入')
                        edited = original + '查询为空显示暂无数据。'
                        await rec.fill(statement, edited)
                        await rec.click(work.get_by_role('navigation', name='需求工作流程').get_by_role('button', name=re.compile(r'^1\s*提供信息$')))
                        pending = page.get_by_role('dialog', name='未保存的修改', exact=True)
                        if await pending.is_visible():
                            await rec.click(pending.get_by_role('button', name='保存后离开', exact=True))
                        await expect(table).not_to_be_visible()
                        assert store.get(pid) == initial, 'Leaving prematurely confirmed the sheet'
                        await rec.click(work.get_by_role('button', name='不使用 AI，直接完善', exact=True))
                        await expect(goal).to_have_value(GOAL)
                        await expect(statement).to_have_value(edited)
                        report['checks'].append('修改后切换阶段再返回，摘要与需求草稿仍保留，未提前确认')
                        await rec.fill(answer, ANSWER)
                        await rec.click(work.get_by_role('button', name='AI 补充建议', exact=True))
                        plan_dialog = page.get_by_role('dialog', name='核对本次任务', exact=True)
                        await expect(plan_dialog).to_be_visible()
                        plan_request = next(r for r in reversed(report['api_requests']) if r['path'].endswith('/actions/plan'))
                        assert '本表未提交的人工修改' in plan_request['body']['message']
                        assert '补充本表中尚未明确' not in plan_request['body']['message'], 'Fixed drafting rules belong to the server contract, not a new user source'
                        assert edited in plan_request['body']['message'] and ANSWER in plan_request['body']['message']
                        assert store.get(pid) == initial and not provider.calls
                        await rec.note('AI 补充建议会先显示本次任务，尚未调用模型。本段返回保留输入，确认取消不会丢草稿。')
                        await page.screenshot(path=str(output / '04-ai-plan.png'))
                        await rec.click(plan_dialog.get_by_role('button', name='返回保留输入', exact=True))
                        await expect(statement).to_have_value(edited)
                        await expect(answer).to_have_value(ANSWER)
                        assert not provider.calls and store.get(pid) == initial
                        report['checks'].append('AI补充建议先核对任务并包含当前人工草稿；取消后不运行模型、不写项目且保留输入')
                        await rec.note('3. 按合成资料补齐关键答案，修改仍在表内。一处确认即可提交整张表并进入交付。')
                        await page.screenshot(path=str(output / '04-ready.png'))
                        before_submit = len([r for r in report['api_requests'] if r['method'] != 'GET' and r['status'] < 400])
                        await rec.click(confirm)
                        await expect(work.get_by_role('heading', name='确认交付版本', exact=True)).to_be_visible(timeout=15000)
                        saved = await wait_store(store, pid, lambda p: all(confirmation_current(p, i) for i in p['items'] if i['id'] in ('REQ-W1', 'REQ-W2')), 'Requirements were not confirmed together')
                        writes = [r for r in report['api_requests'] if r['method'] != 'GET' and r['status'] < 400]
                        assert len(writes) - before_submit == 1 and writes[-1]['path'].endswith('/refinement-confirmation')
                        assert saved['product_context']['intent'] == GOAL
                        assert next(i for i in saved['items'] if i['id'] == 'REQ-W1')['statement'] == edited
                        assert next(q for q in saved['questions'] if q['id'] == 'Q-SHEET-PERMISSIONS')['answer'] == ANSWER
                        assert all(s in saved['sources'] for s in initial['sources'])
                        assert not provider.calls and not report['page_errors']
                        report['checks'].append('填写完成后一次原子确认保存整表并自动进入交付，未确认正式交付')
                        await rec.note('4. 一个确认按钮完成本页保存，并自动进入交付。正式文档与交付仍另行处理。')
                        await page.screenshot(path=str(output / '05-delivery.png'))
                        write(output / 'project-after.json', saved)
                    assert not provider.calls and not report['page_errors']
                    report['status'] = 'passed'
                except Exception as exc:
                    report.update(status='failed', failure=str(exc), traceback=traceback.format_exc())
                    await page.screenshot(path=str(output / 'failure.png'), full_page=True)
                finally:
                    report['model_calls'] = provider.calls
                    rec.save()
                    write(output / 'result.json', report)
                    video = page.video
                    await context.close()
                    await video.save_as(str(output / '单表确认实际操作.webm'))
                    await browser.close()
                    provenance = '电价管理原始 v13，只读查看' if args.scenario == 'real-readonly' else '合成储能云资料，人工操作验收'
                    (output / '操作录像.html').write_text('<!doctype html><meta charset="utf-8"><title>单表确认验收录像</title><h1>单表确认实际操作</h1><p>' + html.escape(provenance + '，隔离副本，0 模型调用，非正式业务验收。' + report['status']) + '</p><video controls style="width:100%;max-height:85vh" src="单表确认实际操作.webm"></video>', encoding='utf-8')
        print(json.dumps({k: report.get(k) for k in ('status', 'checks', 'failure', 'model_calls')}, ensure_ascii=False))
        if report['status'] != 'passed':
            raise SystemExit(1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scenario', choices=('synthetic', 'real-readonly', 'group-scope'), default='synthetic')
    parser.add_argument('--static-build', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    asyncio.run(run(parser.parse_args()))
