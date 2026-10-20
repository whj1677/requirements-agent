"""Visible linked-rule review regression; isolated ASGI, no daily data/models/video.

The tariff scenario uses a frozen v20 snapshot only. Synthetic browser choices
are explicitly fixtures, never business acceptance of the user's project.
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
ORIGIN = 'http://127.0.0.1:8765'
TARIFF = ROOT / 'evidence/tariff-live-recovery-20261011/actual-session/project-after.json'
RULE = 'RULE-LINKED-VISIBILITY'
QUESTION = '模板查看页面是否仅允许电价管理员查看？'
OPTION = '本例只允许电价管理员查看'
TITLE = '合成模板可见范围'
STATEMENT = '模板只允许电价管理员查看。'
EDITED = '模板只允许电价管理员查看；合成验收补充，不包含编辑功能。'
FIELDS = ('actor', 'entry', 'flow', 'data', 'permissions', 'result', 'exceptions')


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def seed(store, scenario):
    from app.core import dumps, now
    from app.product_flow import checkpoint, status
    if scenario == 'tariff':
        p = json.loads(TARIFF.read_text('utf-8'))
        assert p['id'] == 'P-d4b61a83e6f14612' and p['revision'] == 20
        with store.connect() as db:
            db.execute('INSERT INTO projects VALUES(?,?,?)', (p['id'], p['revision'], dumps(p)))
            db.execute('INSERT INTO revisions VALUES(?,?,?,?,?)', (p['id'], p['revision'], dumps(p), '电价管理v20冻结显示副本，禁止业务确认', now()))
        return p['id'], p
    from tests.check_draft_workload_browser import seed_navigation
    pid = seed_navigation(store)
    with store.edit(pid, store.get(pid)['revision'], '冻结关联规则交互合成前置') as (p, _):
        p['name'] = '合成储能云：关联规则-' + scenario
        rule = copy.deepcopy(p['items'][0])
        rule.update(id=RULE, kind='rule', title=TITLE, statement=STATEMENT,
                    selection_status='candidate', related_refs=['REQ-W1'],
                    behavior_review_required=False,
                    behavior={field: ('仅电价管理员可查看' if field == 'permissions' else '') for field in FIELDS})
        p['items'].append(rule)
        p['questions'] = [dict(id='Q-LINKED', question=QUESTION, topic_key='linked-visibility',
            why='合成可选问题，验证关联规则必须可见并随人工确认提交。',
            options=[OPTION, '其他（请说明）'], blocking=False,
            related_refs=['REQ-W1', RULE], source_refs=copy.deepcopy(rule['source_refs']),
            status='open', answer=None)]
        checkpoint(p, 1, status(p)[0]['content_hash'])
    return pid, store.get(pid)


async def enter(page, initial, expect):
    await page.goto(ORIGIN, wait_until='networkidle')
    await page.locator('aside.sidebar button[title="' + initial['name'] + '"]').click()
    work = page.locator('.connected-workspace:visible')
    table = work.get_by_role('table', name='需求确认表', exact=True)
    if not await table.is_visible():
        await work.get_by_role('button', name='不使用 AI，直接完善', exact=True).click()
    await expect(table).to_be_visible()
    return work, table


async def source_check(page, table, item, project, expect):
    await table.get_by_role('button', name='查看规则来源：' + item['title'], exact=True).click()
    dialog = page.get_by_role('dialog', name='项目资料', exact=True)
    await expect(dialog).to_be_visible()
    ref = item['source_refs'][0]
    source = next(s for s in project['sources'] if s['id'] == ref['source_id'])
    excerpt = next(e for e in source['excerpts'] if e['id'] == ref['excerpt_id'])
    await expect(dialog).to_contain_text(excerpt['text'])
    preview = page.get_by_role('dialog', name='附件内容', exact=True)
    if await preview.is_visible():
        await preview.get_by_role('button', name='关闭附件', exact=True).click()
    await dialog.get_by_role('button', name='关闭项目资料', exact=True).click()


async def synthetic(page, store, pid, initial, scenario, output, report, expect):
    work, table = await enter(page, initial, expect)
    rule = next(i for i in initial['items'] if i['id'] == RULE)
    row = table.locator('tr[data-cell-key="clause-' + RULE + '"]')
    await expect(row).to_have_count(0)
    confirm = work.locator('.refinement-sheet-footer .primary')
    if scenario != 'blank':
        qrow = table.locator('tr[data-cell-key="question-Q-LINKED"]')
        await qrow.get_by_role('button', name=OPTION, exact=True).click()
        await expect(row).to_be_visible()
        text = row.get_by_role('textbox', name=TITLE + ' 关联规则正文', exact=True)
        await expect(text).to_have_value(STATEMENT)
        await expect(row.get_by_role('textbox', name=TITLE + ' 关联规则 权限约束', exact=True)).to_have_value(rule['behavior']['permissions'])
        await expect(row).to_contain_text('待采纳规则')
        await expect(row.locator('details')).not_to_have_attribute('open', '')
        await qrow.get_by_role('button', name='核对关联规则：' + TITLE, exact=True).click()
        await expect(text).to_be_focused()
        assert store.get(pid) == initial
        report['checks'].append('点击推荐回答只填答案，关联原规则同表可見可定位，未被答案覆盖，也未提前采纳')
        await source_check(page, table, rule, initial, expect)
        await text.scroll_into_view_if_needed()
        await page.screenshot(path=str(output / '01-linked-rule.png'))
        if scenario == 'edited':
            await text.fill('')
            await confirm.click()
            await expect(text).to_be_focused()
            await expect(row).to_have_class(re.compile('sheet-invalid'))
            await expect(row.get_by_role('alert')).to_be_visible()
            assert store.get(pid) == initial
            assert not [r for r in report['api_requests'] if r['method'] != 'GET']
            report['checks'].append('规则正文清空时就地标错并聚焦，未发生后端写入')
            await text.fill(EDITED)
            await work.get_by_role('button', name='保存草稿', exact=True).click()
            assert store.get(pid) == initial
            await page.reload(wait_until='networkidle')
            work, table = await enter(page, initial, expect)
            row = table.locator('tr[data-cell-key="clause-' + RULE + '"]')
            text = row.get_by_role('textbox', name=TITLE + ' 关联规则正文', exact=True)
            await expect(text).to_have_value(EDITED)
            await expect(table.get_by_role('textbox', name=QUESTION + ' 我的回答', exact=True)).to_have_value(OPTION)
            report['checks'].append('保存草稿刷新后，人工修改与已填回答均保留，规则仍待采纳')
            confirm = work.locator('.refinement-sheet-footer .primary')
    if scenario != 'blank':
        # A filled answer alone does not establish implementation. The tester
        # explicitly adds its meaning to the directly related requirement and
        # checks both that clause and the independently visible linked rule.
        req = next(i for i in initial['items'] if i['id'] == 'REQ-W1')
        await table.get_by_role('button', name='展开业务说明：'+req['title'], exact=True).click()
        await table.get_by_role('textbox', name=req['title']+' 权限约束', exact=True).fill('模板只允许电价管理员查看。')
        qrow = table.locator('tr[data-cell-key="question-Q-LINKED"]')
        await expect(confirm).to_have_text('保存并继续核对')
        assert store.get(pid) == initial
        for title, field in [(req['title'], 'behavior.permissions'), (TITLE, 'statement')]:
            target = qrow.locator('.answer-implementation-target').filter(has=page.get_by_role('combobox', name=title+' 答案落文位置', exact=True))
            await target.get_by_role('combobox').select_option(field)
            await expect(target.get_by_role('blockquote')).to_contain_text('模板只允许电价管理员查看')
            await target.get_by_role('checkbox', name='我已核对此原文表达了答案', exact=True).check()
        await expect(confirm).to_have_text('确认并进入交付')
        report['checks'].append('明确把答案写入直接关联需求，并逐个选择需求/规则原文且人工勾选；未核对前保持待落实，未自动映射答案')
    await confirm.click()
    await expect(work.get_by_role('heading', name='确认交付版本', exact=True)).to_be_visible(timeout=15000)
    saved = store.get(pid)
    writes = [r for r in report['api_requests'] if r['method'] != 'GET']
    assert len(writes) == 1 and writes[0]['path'].endswith('/refinement-confirmation'), writes
    assert writes[0]['response']['confirmed'] is True, writes
    assert saved['revision'] == initial['revision'] + 1
    after = next(i for i in saved['items'] if i['id'] == RULE)
    question = next(q for q in saved['questions'] if q['id'] == 'Q-LINKED')
    payload = writes[0]['body']['linked_clauses']
    if scenario == 'blank':
        assert payload == [] and after == rule and question['answer'] is None
        report['checks'].append('可选答案留空时规则不强制采纳，整表确认仍可进入交付')
    else:
        assert payload == [dict(item_id=RULE, statement=EDITED if scenario == 'edited' else STATEMENT,
                                behavior=rule['behavior'])]
        assert after['selection_status'] == 'selected'
        assert after['statement'] == (EDITED if scenario == 'edited' else STATEMENT)
        assert question['answer'] == OPTION and RULE in question.get('applied_item_ids', [])
        assert all(ref in after['source_refs'] for ref in rule['source_refs'])
        if scenario == 'unchanged':
            assert after['source_refs'] == rule['source_refs'] and after['content_version'] == rule['content_version']
        else:
            assert len(after['source_refs']) > len(rule['source_refs'])
        report['checks'].append('一次原子确认包含可见规则并自动跳转交付；原来源保留，修改单独保留人工来源')
    await page.screenshot(path=str(output / '02-delivery.png'))
    write(output / 'project-after.json', saved)


async def tariff(page, store, pid, initial, output, report, expect):
    work, table = await enter(page, initial, expect)
    question = next(q for q in initial['questions'] if q['id'] == 'Q-8e4155764d3f447e')
    rule = next(i for i in initial['items'] if i['id'] == 'RULE-f8353ea2bdea4a72')
    row = table.locator('tr[data-cell-key="clause-' + rule['id'] + '"]')
    await expect(row).to_have_count(0)
    qrow = table.locator('tr[data-cell-key="question-' + question['id'] + '"]')
    option = '与该账号有权限的园区一致（本账号下园区）'
    await qrow.get_by_role('button', name=option, exact=True).click()
    await expect(row).to_be_visible()
    await qrow.get_by_role('button', name='核对关联规则：' + rule['title'], exact=True).click()
    text = row.get_by_role('textbox', name=rule['title'] + ' 关联规则正文', exact=True)
    await expect(text).to_be_focused()
    await expect(text).to_have_value(rule['statement'])
    await expect(row.get_by_role('textbox', name=rule['title'] + ' 关联规则 权限约束', exact=True)).to_have_value(rule['behavior']['permissions'])
    await expect(row).to_contain_text('待采纳规则')
    await expect(row).to_contain_text(option)
    assert rule['statement'] != option
    await page.screenshot(path=str(output / 'tariff-rule-visible.png'))
    await source_check(page, table, rule, initial, expect)
    await work.get_by_role('button', name='保存草稿', exact=True).click()
    await page.reload(wait_until='networkidle')
    work, table = await enter(page, initial, expect)
    await table.get_by_role('button', name='核对关联规则：' + rule['title'], exact=True).click()
    await expect(table.get_by_role('textbox', name=rule['title'] + ' 关联规则正文', exact=True)).to_have_value(rule['statement'])
    await expect(table.get_by_role('textbox', name=question['question'] + ' 我的回答', exact=True)).to_have_value(option)
    await page.screenshot(path=str(output / 'tariff-rule-after-refresh.png'))
    assert store.get(pid) == initial and not [r for r in report['api_requests'] if r['method'] != 'GET']
    report['checks'].extend(['电价管理v20冻结副本点击截图中相同推荐回答，规则正文及原权限可见，来源可读，回答不覆写规则',
                             '真实副本保存浏览器草稿并刷新后内容仍可見；未替用户确认任何业务决定，项目快照完全未变'])
    report['snapshot_sha256'] = hashlib.sha256(TARIFF.read_bytes()).hexdigest()


async def run_case(args, scenario):
    import httpx
    from playwright.async_api import async_playwright, expect
    from app.main import create_app
    from tests.check_refine_workspace_browser import NoModelProvider
    output = args.output.resolve() / scenario
    output.mkdir(parents=True)
    build = args.static_build.resolve()
    provider = NoModelProvider(output)
    app = create_app(output / 'isolated-data', provider=provider)
    store = app.state.store
    pid, initial = seed(store, scenario)
    write(output / 'project-before.json', initial)
    report = dict(status='running', scenario=scenario, project_id=pid, checks=[], api_requests=[],
        page_errors=[], routing_errors=[], model_calls=0, listener_started=False, daily_data_access=False,
        recorded_video=False, business_acceptance='NOT_RUN', static_build=str(build), served_static={})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            context = await browser.new_context(viewport=dict(width=1600, height=980))
            async def dispatch(route):
                request, url = route.request, urlsplit(route.request.url)
                try:
                    assert url.netloc == '127.0.0.1:8765', 'External request forbidden'
                    if url.path.startswith('/api/'):
                        assert request.method == 'GET' or scenario != 'tariff' and url.path.endswith('/refinement-confirmation'), 'Unexpected write ' + url.path
                        headers = {k: v for k, v in request.headers.items() if k.lower() not in ('host', 'content-length', 'connection', 'accept-encoding')}
                        headers['host'] = '127.0.0.1:8765'
                        response = await client.request(request.method, url.path + ('?' + url.query if url.query else ''), headers=headers, content=request.post_data_buffer)
                        event = dict(method=request.method, path=url.path, status=response.status_code)
                        if request.post_data:
                            event.update(body=request.post_data_json, response=response.json())
                        report['api_requests'].append(event)
                        await route.fulfill(status=response.status_code, body=response.content, content_type=response.headers.get('content-type', 'application/json'))
                    else:
                        path = (build / (url.path.lstrip('/') or 'index.html')).resolve()
                        assert path.is_relative_to(build) and path.is_file(), str(path)
                        report['served_static'][url.path] = hashlib.sha256(path.read_bytes()).hexdigest()
                        await route.fulfill(status=200, body=path.read_bytes(), content_type=mimetypes.guess_type(path.name)[0] or 'application/octet-stream')
                except Exception as exc:
                    report['routing_errors'].append(str(exc))
                    await route.abort()
            await context.route('**/*', dispatch)
            page = await context.new_page()
            page.set_default_timeout(15000)
            page.on('pageerror', lambda e: report['page_errors'].append(str(e)))
            try:
                if scenario == 'tariff':
                    await tariff(page, store, pid, initial, output, report, expect)
                else:
                    await synthetic(page, store, pid, initial, scenario, output, report, expect)
                assert provider.calls == 0 and not report['page_errors'] and not report['routing_errors']
                report['status'] = 'passed'
            except Exception as exc:
                report.update(status='failed', failure=str(exc), traceback=traceback.format_exc())
                await page.screenshot(path=str(output / 'failure.png'), full_page=True)
            finally:
                report['model_calls'] = provider.calls
                write(output / 'result.json', report)
                await context.close()
                await browser.close()
    return {k: report.get(k) for k in ('scenario', 'status', 'checks', 'failure', 'model_calls')}


async def run(args):
    assert args.output.resolve().is_relative_to(ROOT / 'evidence') and args.output.resolve() != ROOT / 'evidence'
    assert not args.output.exists() and (args.static_build / 'index.html').is_file()
    args.output.mkdir(parents=True)
    with tempfile.TemporaryDirectory(prefix='ra-linked-import-') as temporary:
        os.environ['RA_DATA_DIR'] = str(Path(temporary) / 'data')
        cases = ['unchanged', 'edited', 'blank', 'tariff'] if args.scenario == 'all' else [args.scenario]
        result = [await run_case(args, scenario) for scenario in cases]
    write(args.output / 'result.json', result)
    print(json.dumps(result, ensure_ascii=False))
    if any(case['status'] != 'passed' for case in result):
        raise SystemExit(1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--static-build', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--scenario', choices=('all', 'unchanged', 'edited', 'blank', 'tariff'), default='all')
    asyncio.run(run(parser.parse_args()))
