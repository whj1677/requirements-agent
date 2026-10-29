"""Isolated workload acceptance; actual AI candidates and synthetic navigation differ.

No server is started. Browser HTTP is intercepted into an ASGI application over
a copied database. The provider forbids model calls. Freeze the independent
content oracle before the parent generates a new global candidate; the oracle
must never enter a model prompt or its readable material directory.
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
import sqlite3
import sys
import tempfile
import time
import traceback
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PID = 'P-d4b61a83e6f14612'
RID = 'REQ-0d7398f85978463d'
BASE = ROOT / 'evidence/tariff-entry-20261010/business-draft-v1-whole/live/project-before.json'
SOURCE_ORACLE = ROOT / 'evidence/business-draft-20261010/acceptance-oracle.md'
FIELDS = ('actor', 'entry', 'flow', 'data', 'permissions', 'result', 'exceptions')
ORIGIN = 'http://127.0.0.1:8765'

# Frozen independent evaluation criteria, never sent to Provider or DSH.
CONTENT_CRITERIA = [
    ('R1-C1', '业主可见范围', ['EX-b19d35cb231947a7'],
     '业主只能看自己建立和中车运维建立的模板；不能扩成运维自身权限。'),
    ('R1-C2', '列表筛选', ['EX-4bf3f2dccd7f4deb'],
     '电价名称、归属地输入框及创建时间范围；不能混为 A0 省份下拉。'),
    ('R1-C3', '展示列与操作', ['EX-4bf3f2dccd7f4deb'],
     '保留名称、归属地、开始/结束时间、尖峰平谷摘要、创建人/时间和编辑/复制/删除。'),
    ('R1-C4', '新增编辑复制的结果', ['EX-4bf3f2dccd7f4deb'],
     '目标列表行应说明打开 A0；其他行出现不等于目标行已完整。'),
    ('R1-C5', '批量配置的结果', ['EX-4bf3f2dccd7f4deb'],
     '目标列表行应说明打开 B0 园区列表，不要求抄入弹窗内部全部规则。'),
    ('R1-C6', '页面入口与可见控件', ['EX-5172802d273b4b6d'],
     '类型维护→电价模板库、查询/重置/分页。截图按钮不能证明其动作结果。'),
    ('PACKAGE-KNOWN', '整包已知内容不再笼统追问', [],
     '检查 A0/B0 保存校验、24小时、至少一个园区、特殊时段优先级、效率公式、导出范围和详情入口。'),
    ('PACKAGE-DUPLICATE', '重复需求和问题', [],
     '对照原10条及已答问题；效率、日收入详情不重复新增；同一模式命名问题不反复确认。'),
    ('PACKAGE-FACT', '事实、推荐、未知分开', [],
     '不把样例数量/整点/价格写成固定规则；复制预填、删除效果、重置默认值等没有依据时保持建议或未知。'),
    ('PACKAGE-SOURCE', '来源语义正确', [],
     '引用存在之外还核对原文含义；人工修改、旧模型总结不能伪装成原材料事实。'),
]


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def evidence_path(path):
    path = path.resolve()
    assert path != ROOT / 'evidence' and path.is_relative_to(ROOT / 'evidence')
    return path


def freeze_oracle(output):
    project = json.loads(BASE.read_text('utf-8'))
    assert project['id'] == PID and project['revision'] == 13 and project['name'] == '电价管理'
    original = [i for i in project['items'] if i['kind'] == 'requirement'
                and i.get('applies_to') == 'to_be' and not i.get('target_item_id')]
    assert len(project['sources']) == 15 and len(original) == 10
    pairs = {ex['id'] for source in project['sources'] for ex in source.get('excerpts', [])}
    assert all(ex in pairs for _, _, excerpts, _ in CONTENT_CRITERIA for ex in excerpts)
    output.mkdir(parents=True, exist_ok=False)
    oracle = dict(frozen_at=time.time(), project_id=PID, revision=13,
        snapshot_sha256=sha(BASE), source_oracle_sha256=sha(SOURCE_ORACLE),
        source_identity=[dict(id=s['id'], sha256=s['sha256']) for s in project['sources']],
        original_requirement_ids=[i['id'] for i in original],
        criteria=[dict(id=key, name=name, excerpt_ids=refs, rule=rule,
                       result='NOT_REVIEWED') for key, name, refs, rule in CONTENT_CRITERIA],
        denominator_note='R1六组分别判断完整/部分/遗漏；整包抽查独立列出，不折算无依据总分。',
        workload_note='80%是减负方向，不把任意字段填充率当业务准确率；记录重填字段数、实际点击数、重复待办和剩余真实决定。',
        model_calls=0, must_not_be_model_input=True, user_business_acceptance='NOT_RUN')
    write(output / 'frozen-oracle.json', oracle)
    return oracle


def frozen_real_session(session, oracle_path):
    session = evidence_path(session)
    oracle = json.loads(oracle_path.read_text('utf-8'))
    assert oracle['snapshot_sha256'] == sha(BASE) and oracle['must_not_be_model_input']
    outcome = json.loads((session / 'outcome.json').read_text('utf-8'))
    if outcome.get('mode') == 'offline_workflow_replay':
        assert outcome['provider_requests'] == 0 and outcome['frozen_model_responses'] == 1
        original = evidence_path(Path(outcome['original_real_session']))
        original_outcome = json.loads((original / 'outcome.json').read_text('utf-8'))
        assert original_outcome['provider_requests'] > 0
        original_response = original / 'provider-response.json'
        assert sha(original_response) == outcome['original_response_sha256']
        original_value = json.loads(original_response.read_text('utf-8'))['value']
        calls = [json.loads(p.read_text('utf-8')) for p in (session / 'data/evidence/model-calls').glob('*.json')]
        assert len(calls) == 1 and calls[0]['offline_replay']
        assert calls[0]['original_response_sha256'] == outcome['original_response_sha256']
        assert json.loads(calls[0]['final_output']) == original_value, 'Replay altered the real response object'
        assert json.loads((original / 'project-before.json').read_text('utf-8')) == json.loads(BASE.read_text('utf-8'))
        outcome['browser_provenance'] = dict(mode='真实模型输出，修复后离线重放',
            original_provider_requests=original_outcome['provider_requests'],
            original_task_status=original_outcome['task_status'],
            original_response_sha256=outcome['original_response_sha256'],
            exact_response_object_preserved=True, new_model_requests=0)
    else:
        assert outcome.get('provider_requests', 0) > 0, 'A real model result is required'
    assert outcome.get('task_status') in ('completed', 'done', 'partial', 'succeeded')
    initial = json.loads((session / 'project-before.json').read_text('utf-8'))
    assert initial == json.loads(BASE.read_text('utf-8')), 'Use the same frozen v13 materials, not the corrected video copy'
    assert (session / 'project-after.json').is_file()
    project = json.loads((session / 'project-after.json').read_text('utf-8'))
    assert project['id'] == PID and project['sources'] == initial['sources']
    old_ids = {row['id'] for row in initial['items']}
    generated = [row for row in project['items'] if row['id'] not in old_ids]
    revised = {row['target_item_id'] for row in generated if row['kind'] == 'requirement' and row.get('target_item_id')}
    assert len(revised) >= 2, 'This recording requires a real global result, not one target-row call'
    assert all(row.get('selection_status') == 'candidate' for row in generated)
    with sqlite3.connect((session / 'data/requirements.sqlite3').as_uri() + '?mode=ro', uri=True) as db:
        assert json.loads(db.execute('SELECT payload FROM projects WHERE id=?', (PID,)).fetchone()[0]) == project
    return outcome, project, revised


def scoped_correction_evidence(session):
    """Audit a separately authorized real call, never execute or replay one."""
    session = evidence_path(session)
    before = json.loads((session / 'project-before.json').read_text('utf-8'))
    after = json.loads((session / 'project-after.json').read_text('utf-8'))
    outcome = json.loads((session / 'outcome.json').read_text('utf-8'))
    plan = json.loads((session / 'plan.json').read_text('utf-8'))
    rid = (plan.get('requirement_recommendation_scope') or {}).get('requirement_id')
    assert rid and before['id'] == after['id'] == PID
    assert outcome.get('provider_requests', 0) > 0 and outcome.get('task_status') in ('completed', 'done', 'partial', 'succeeded')
    assert before['sources'] == after['sources']
    previous = {i['id']: i for i in before['items']}
    generated = [i for i in after['items'] if i['id'] not in previous]
    assert generated, 'Real correction produced no reviewable candidate'
    assert all(i == previous[i['id']] for i in after['items'] if i['id'] in previous), 'Correction rewrote an original before review'
    assert all(i.get('selection_status') == 'candidate' for i in generated)
    assert all((i['kind'] == 'requirement' and i.get('target_item_id') == rid)
               or (i['kind'] == 'acceptance' and rid in i.get('related_refs', [])) for i in generated), 'Unrelated candidate generated'
    return dict(status='REAL_API_RESULT_VERIFIED_SEPARATELY', session=str(session),
        requirement_id=rid, provider_requests=outcome['provider_requests'], generated_ids=[i['id'] for i in generated],
        original_rows_unchanged=True, new_candidates_unadopted=True,
        browser_trigger_and_model_call_in_same_recording='NOT_RUN')


def seed_navigation(store):
    """Synthetic complete facts only, seeded before recording; no AI claim."""
    from app.core import now
    from app.product_flow import checkpoint, status
    p = store.create('合成储能云：批量确认后继续')
    with store.edit(p['id'], p['revision'], '合成导航验收的冻结前置') as (p, _):
        text = ('合成验收资料，不是真实电价管理规则。本期新增独立模板查看和历史查询模块。'
                '目标是供电价管理员只读查看模板名称和历史版本。管理员从模板库进入列表，选择模板查看历史；'
                '本例只有管理员且只能查看，查询为空显示暂无数据。')
        p.update(workflow_mode='shared-draft', scope_profile=dict(scenario='new_module', dependencies='independent'),
                 workspace_draft=dict(text=text, entry_mode='direct', saved_at=now()),
                 intake=dict(mode='direct', text=text, saved_at=now()))
        refs = [dict(source_id='SRC-WORKLOAD', excerpt_id='EX-WORKLOAD')]
        p['sources'] = [dict(id='SRC-WORKLOAD', title='合成批量导航资料', purpose='goal', uri=None,
            sha256=hashlib.sha256(text.encode()).hexdigest(), created=now(), version=1, excluded=False,
            parse_status='read', failure_reason='', image_mime=None,
            excerpts=[dict(id='EX-WORKLOAD', source_id='SRC-WORKLOAD', locator='合成资料第1段',
                           text=text, method='synthetic-fixture')])]
        p['product_context'] = dict(product='大型储能云平台', module='合成电价查看',
            intent='供电价管理员只读查看模板与历史。', users='电价管理员',
            change_scope='新增独立模板查看和历史查询模块。', scope_ids=['REQ-W1', 'REQ-W2'])
        p['context_field_states'] = {k: dict(status='known') for k in ('intent', 'users', 'change_scope')}
        def row(iid, kind, title, statement, related):
            return dict(id=iid, kind=kind, title=title, statement=statement, applies_to='to_be',
                epistemic_status='reported', source_refs=refs, related_refs=related,
                selection_status='selected' if kind == 'requirement' else 'candidate',
                revision=1, content_version=1, change_type='new')
        p['items'] = []
        for n, title, result in [(1, '模板查看', '显示已保存的模板名称'), (2, '历史查询', '显示所选模板的历史版本')]:
            req = row(f'REQ-W{n}', 'requirement', title, '电价管理员只读' + title + '，' + result + '。', [f'AC-W{n}'])
            req.update(behavior_review_required=True, behavior=dict(actor='电价管理员', entry='从模板库进入',
                flow=title, data='模板名称和历史版本', permissions='本例只有管理员且只能查看',
                result=result, exceptions='查询为空显示暂无数据'))
            p['items'] += [req, row(f'AC-W{n}', 'acceptance', title + '验收', '管理员执行' + title + '后，' + result + '。', [req['id']])]
        p['questions'] = []
        p['stage_checks'] = {}
        checkpoint(p, 1, status(p)[0]['content_hash'])
    return p['id']


async def run_browser(args):
    # Import-time application construction is isolated, even in helper modules.
    with tempfile.TemporaryDirectory(prefix='ra-workload-import-') as temporary:
        os.environ['RA_DATA_DIR'] = str(Path(temporary) / 'data')
        import httpx
        from playwright.async_api import async_playwright, expect
        from app.main import create_app
        from app.requirement_confirmation import confirmation_current
        from tests.check_tariff_entry_acceptance import copy_database
        from tests.check_refine_workspace_browser import NoModelProvider, wait_store
        from tests.check_refine_human_flow_browser import ANNOTATION, Recorder

        output, build = evidence_path(args.output), args.static_build.resolve()
        assert not output.exists() and (build / 'index.html').is_file()
        outcome = None
        if args.scenario == 'real':
            assert args.session and args.oracle
            outcome, initial, revised = frozen_real_session(args.session, args.oracle.resolve())
            source_db = args.session.resolve() / 'data/requirements.sqlite3'
            before_source_hash = sha(source_db)
            output.mkdir(parents=True)
            copy_database(args.session.resolve() / 'data', output / 'data')
        else:
            output.mkdir(parents=True)
        provider = NoModelProvider(output)
        app = create_app(output / 'data', provider=provider)
        store = app.state.store
        pid = PID if args.scenario == 'real' else seed_navigation(store)
        initial = store.get(pid)
        write(output / 'project-before.json', initial)
        report = dict(status='running', scenario=args.scenario, project_id=pid, checks=[],
            static_build=str(build), api_requests=[], page_errors=[], model_calls=0,
            actual_clicks=0, manually_filled_fields=0, daily_data_access=False,
            content_quality='INDEPENDENT_REVIEW_REQUIRED' if outcome else 'SYNTHETIC_NOT_MODEL_QUALITY',
            user_business_acceptance='NOT_RUN', formal_delivery='NOT_RUN',
            scoped_ai_correction='NOT_RUN', real_automatic_organize_transition='NOT_RUN')
        report['static_hashes'] = {str(p.relative_to(build)): sha(p) for p in build.rglob('*') if p.is_file()}
        if outcome:
            report['model_provenance'] = outcome.get('browser_provenance', dict(mode='真实模型调用已生成候选', provider_requests=outcome['provider_requests']))
        if args.correction_session:
            report['scoped_ai_correction'] = scoped_correction_evidence(args.correction_session)

        class WorkloadRecorder(Recorder):
            async def note(self, text, seconds=2.8):
                self.chapters.append(dict(seconds=round(time.monotonic() - self.started, 2), text=text))
                label = (('真实模型输出，修复后离线重放 · 电价管理隔离副本' if outcome.get('mode') == 'offline_workflow_replay' else '电价管理真实全局 AI 草稿 · 隔离副本') if outcome else '合成储能云导航场景 · 非真实 AI 结果')
                await self.page.evaluate("text=>{document.getElementById('recording-note').textContent=text}",
                    '录屏说明 · 非产品界面\n' + label + '\n本段 0 新增模型请求\n非用户正式业务验收\n\n' + text)
                await self.page.wait_for_timeout(seconds * 1000)
            async def click(self, locator):
                report['actual_clicks'] += 1
                await super().click(locator)

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=True)
                context = await browser.new_context(viewport=dict(width=1440, height=900),
                    record_video_dir=str(output / 'raw'), record_video_size=dict(width=1440, height=900))
                await context.add_init_script('(' + ANNOTATION + ')()')
                delayed_preview = dict(enabled=False, arrived=asyncio.Event(), release=asyncio.Event())
                async def dispatch(route):
                    request, url = route.request, urlsplit(route.request.url)
                    assert url.netloc == '127.0.0.1:8765', 'External browser request forbidden'
                    if url.path.startswith('/api/'):
                        permitted = ('/requirement-confirmation/batch/preview', '/requirement-confirmation/batch/commit', '/actions/plan')
                        assert request.method == 'GET' or url.path.endswith(permitted) or re.search(r'/stage-checks/[123]$', url.path), 'Unexpected mutation: ' + url.path
                        headers = {k: v for k, v in request.headers.items() if k.lower() not in ('host', 'content-length', 'connection', 'accept-encoding')}
                        headers['host'] = '127.0.0.1:8765'
                        response = await client.request(request.method, url.path + ('?' + url.query if url.query else ''),
                            headers=headers, content=request.post_data_buffer)
                        event = dict(method=request.method, path=url.path, status=response.status_code)
                        if request.post_data:
                            event.update(body=request.post_data_json, response=response.json())
                        report['api_requests'].append(event)
                        if url.path.endswith('/batch/preview') and delayed_preview['enabled']:
                            delayed_preview['arrived'].set()
                            await delayed_preview['release'].wait()
                        await route.fulfill(status=response.status_code, body=response.content,
                            content_type=response.headers.get('content-type', 'application/json'))
                    else:
                        path = (build / (url.path.lstrip('/') or 'index.html')).resolve()
                        assert path.is_relative_to(build) and path.is_file()
                        await route.fulfill(status=200, body=path.read_bytes(), content_type=mimetypes.guess_type(path.name)[0] or 'application/octet-stream')
                await context.route('**/*', dispatch)
                page = await context.new_page()
                page.set_default_timeout(15000)
                page.on('pageerror', lambda error: report['page_errors'].append(str(error)))
                rec = WorkloadRecorder(page, output, True)
                try:
                    await page.goto(ORIGIN, wait_until='networkidle')
                    await rec.click(page.locator('aside.sidebar button[title="' + initial['name'] + '"]'))
                    work = page.locator('.connected-workspace:visible')
                    await rec.click(work.get_by_role('button', name='2 完善需求', exact=True))
                    table = work.get_by_role('table', name='需求确认表', exact=True)
                    await expect(table).to_be_visible()
                    scope = initial['product_context']['scope_ids']
                    originals = [i for i in initial['items'] if i['kind'] == 'requirement' and not i.get('target_item_id') and i['id'] in scope]
                    eligible = [i for i in originals if args.scenario != 'real' or i['id'] in revised]
                    assert len(eligible) >= 2
                    selected = eligible[:2] if outcome else eligible[:1]
                    await rec.note('1. 表格已显示待确认草稿。先浏览内容；打开页面不代表已确认。' + ('本段草稿来自真实模型。' if outcome else '本段两条草稿是冻结的合成测试前置。'))
                    manual_text = None
                    if not outcome:
                        target = selected[0]
                        editor = work.get_by_role('region', name=target['title'] + ' 需求确认', exact=True)
                        if not await editor.is_visible():
                            await rec.click(table.locator('tr[data-requirement-id="' + target['id'] + '"]').get_by_role('button', name='查看与确认', exact=True))
                        statement = editor.get_by_role('textbox', name=target['title'] + ' 需求内容', exact=True)
                        manual_text = target['statement'] + '查询为空显示暂无数据。'
                        await rec.fill(statement, manual_text)
                        report['manually_filled_fields'] += 1
                    if args.scenario == 'real':
                        target = selected[0]
                        target_row = table.locator('tr[data-requirement-id="' + target['id'] + '"]')
                        editor = work.get_by_role('region', name=target['title'] + ' 需求确认', exact=True)
                        if not await editor.is_visible():
                            await rec.click(target_row.get_by_role('button', name='查看与确认', exact=True))
                        await expect(editor).to_be_visible()
                        instruction = editor.get_by_role('textbox', name=target['title'] + ' 给 AI 的修改意见', exact=True)
                        await rec.fill(instruction, '请重新核对当前这行引用的资料，只修订本行，保留其他需求和人工修改。')
                        report['manually_filled_fields'] += 1
                        await rec.click(editor.get_by_role('button', name='让 AI 调整本行', exact=True))
                        plan_dialog = page.get_by_role('dialog', name='核对本次任务', exact=True)
                        await expect(plan_dialog).to_be_visible()
                        plan_request = next(r for r in reversed(report['api_requests']) if r['path'].endswith('/actions/plan'))
                        assert plan_request['body']['target'] == dict(kind='item', id=target['id'])
                        assert store.get(pid) == initial and provider.calls == 0
                        input_preview = plan_dialog.get_by_role('region', name='本次修改内容', exact=True)
                        await expect(input_preview).to_be_visible()
                        assert not re.search(r'"(?:statement|behavior|acceptance_items|actor|permissions)"\s*:', await plan_dialog.inner_text()), 'Internal draft JSON is visible in the task dialog'
                        sent_draft = json.loads(plan_request['body']['message'].rsplit('\n当前待核对草稿：', 1)[1])
                        await expect(input_preview.locator('details')).not_to_have_attribute('open', '')
                        await input_preview.scroll_into_view_if_needed()
                        await page.screenshot(path=str(output / '00-task-input-collapsed.png'))
                        await rec.click(input_preview.locator('summary').filter(has_text='同时提供本行草稿'))
                        await expect(input_preview.get_by_text(sent_draft['statement'], exact=True)).to_be_visible()
                        assert await input_preview.locator('ol li').all_text_contents() == [ac['statement'] for ac in sent_draft['acceptance_items']]
                        for value in sent_draft['behavior'].values():
                            if isinstance(value, str) and value.strip():
                                await expect(input_preview).to_contain_text(value)
                        await page.screenshot(path=str(output / '00-task-input-expanded.png'))
                        report['checks'].append('本行 AI 任务以修改意见和可展开的人话草稿展示，正文与全部验收保持完整，无原始 JSON 直出')
                        await rec.note('局部修改先明确范围：计划只针对当前行。本段返回保留输入，不发新模型请求。')
                        await rec.click(plan_dialog.get_by_role('button', name='返回保留输入', exact=True))
                        await expect(instruction).to_have_value('请重新核对当前这行引用的资料，只修订本行，保留其他需求和人工修改。')
                        # Clear only this unsaved instruction through the UI, then
                        # save the row's local draft before selecting the batch.
                        await rec.fill(instruction, '')
                        await rec.click(editor.get_by_role('button', name='保存本行草稿', exact=True))
                        report['checks'].append('局部 AI 修改计划严格指向当前行；取消无调用、无项目写入并保留输入')
                    for row in selected:
                        await rec.click(table.get_by_role('checkbox', name='选择 ' + row['title'], exact=True))
                    assert store.get(pid) == initial
                    await rec.note('2. 勾选这次核对的需求。勾选是临时选择，尚未采纳。')
                    await page.screenshot(path=str(output / '01-selected.png'))
                    preview_button = work.get_by_role('button', name=f'查看并确认所选（{len(selected)}）', exact=True)
                    await rec.click(preview_button)
                    preview = work.get_by_role('region', name='批量确认预览', exact=True)
                    await expect(preview).to_be_visible()
                    for row in selected:
                        await expect(preview).to_contain_text(row['title'])
                    assert store.get(pid) == initial, 'Preview adopted content before confirmation'
                    report['checks'].append('选择与批量预览均不采纳、不改条款')
                    # A real preview must render every supplied value, not only
                    # a summary; this check does not judge its business truth.
                    preview_request = next(r for r in reversed(report['api_requests']) if r['path'].endswith('/batch/preview'))
                    for result_row in preview_request['response']['rows']:
                        card = preview.locator('article').filter(has=page.get_by_role('heading', name=result_row['after']['title'], exact=True))
                        await expect(card.locator('.table-batch-statement')).to_have_text(result_row['after']['statement'])
                        for value in result_row['after'].get('behavior', {}).values():
                            if isinstance(value, str) and value.strip():
                                await expect(card).to_contain_text(value)
                        for ac in result_row['acceptance_items']:
                            await expect(card).to_contain_text(ac['after']['statement'])
                    if not outcome:
                        await expect(preview.locator('.table-batch-statement')).to_have_text(manual_text)
                        await rec.note('预览包括刚修改的原句。返回修改只关闭预览，不应丢失人工输入。')
                        await rec.click(preview.get_by_role('button', name='返回表格修改', exact=True))
                        await expect(preview).not_to_be_visible()
                        await expect(statement).to_have_value(manual_text)
                        assert store.get(pid) == initial
                        report['checks'].append('人工修改后批量预览显示全文；取消保留输入且项目不变')
                        await rec.click(editor.get_by_role('button', name='保存本行草稿', exact=True))
                        delayed_preview['enabled'] = True
                        await rec.click(preview_button)
                        await asyncio.wait_for(delayed_preview['arrived'].wait(), timeout=10)
                        await expect(statement).to_be_disabled()
                        await rec.click(work.get_by_role('navigation', name='需求工作流程').get_by_role('button', name=re.compile(r'^1\s*提供信息$')))
                        await expect(table).to_be_visible()
                        await expect(page.get_by_text('当前操作仍在处理中，请等待结果后再切换页面。', exact=True)).to_be_visible()
                        delayed_preview['release'].set()
                        delayed_preview['enabled'] = False
                        await expect(preview.locator('.table-batch-statement')).to_have_text(manual_text)
                        await expect(statement).to_be_disabled()
                        await rec.click(preview.get_by_role('button', name='返回表格修改', exact=True))
                        await expect(statement).to_be_enabled()
                        await rec.click(work.get_by_role('navigation', name='需求工作流程').get_by_role('button', name=re.compile(r'^1\s*提供信息$')))
                        await expect(table).not_to_be_visible()
                        await rec.click(work.get_by_role('button', name='2 完善需求', exact=True))
                        await expect(table).to_be_visible()
                        await expect(preview).not_to_be_visible()
                        assert await table.get_by_role('checkbox', checked=True).count() == 0
                        if not await editor.is_visible():
                            await rec.click(table.locator('tr[data-requirement-id="' + target['id'] + '"]').get_by_role('button', name='查看与确认', exact=True))
                        await expect(statement).to_have_value(manual_text)
                        assert store.get(pid) == initial
                        report['checks'].append('延迟预览期间编辑与导航受保护；取消后可离开，返回时勾选清空、人工草稿恢复')
                        await rec.note('返回本页后，临时勾选已清空；人工草稿仍在。这次只确认第一条。')
                        await rec.click(table.get_by_role('checkbox', name='选择 ' + target['title'], exact=True))
                        await rec.click(preview_button)
                        await expect(preview.locator('.table-batch-statement')).to_have_text(manual_text)
                    await rec.note('3. 同屏查看所选需求、业务说明、验收与待定项，最后一次明确确认。')
                    await page.screenshot(path=str(output / '02-batch-preview.png'), full_page=True)
                    assert args.commit_approved, 'Concrete copied-project batch requires parent review before commit'
                    await rec.click(preview.get_by_role('button', name=f'确认以上 {len(selected)} 条需求', exact=True))
                    saved = await wait_store(store, pid, lambda p: all(confirmation_current(p, next(i for i in p['items'] if i['id'] == row['id'])) for row in selected), 'Batch confirmation did not persist')
                    await expect(preview).not_to_be_visible()
                    assert await table.get_by_role('checkbox', checked=True).count() == 0, 'Temporary selection persisted after commit'
                    assert saved['questions'] == initial['questions'], 'Batch silently answered unresolved questions'
                    selected_ids = {row['id'] for row in selected}
                    allowed = selected_ids | {i['id'] for i in initial['items'] if i.get('target_item_id') in selected_ids or i['kind'] == 'acceptance' and (set(i.get('related_refs', [])) & selected_ids or any(i['id'] in row.get('related_refs', []) for row in selected))}
                    assert all(next(i for i in saved['items'] if i['id'] == old['id']) == old for old in initial['items'] if old['id'] not in allowed), 'Unrelated row changed'
                    assert all(source in saved['sources'] for source in initial['sources']), 'An original material was changed'
                    new_sources = [source for source in saved['sources'] if source['id'] not in {s['id'] for s in initial['sources']}]
                    if manual_text:
                        assert len(new_sources) == 1 and new_sources[0]['title'] == '人工需求整行确认.txt', 'Unexpected new source'
                        assert ''.join(ex['text'] for ex in new_sources[0]['excerpts']) == manual_text, 'Human edit provenance differs from visible input'
                    else:
                        assert not new_sources, 'Unedited confirmation created unexpected material'
                    report['checks'].append('显式确认仅保存所选行及其验收，清空选择；其它行和未决问题不变')
                    await rec.note('4. 所选内容已确认，临时勾选清空。其他需求及尚待决定的问题保留。')
                    await page.screenshot(path=str(output / '03-confirmed.png'))
                    readiness = work.get_by_role('region', name='需求核对准备', exact=True)
                    if args.scenario == 'synthetic-navigation':
                        assert next(i for i in saved['items'] if i['id'] == selected[0]['id'])['statement'] == manual_text
                        await rec.click(table.locator('tr[data-requirement-id="' + target['id'] + '"]').get_by_role('button', name='查看与确认', exact=True))
                        await rec.fill(statement, manual_text + '本例只有管理员且只能查看。')
                        await rec.click(editor.get_by_role('button', name='保存本行草稿', exact=True))
                        await expect(table.get_by_role('checkbox', name='选择 ' + target['title'], exact=True)).to_be_enabled()
                        assert store.get(pid) == saved
                        report['checks'].append('已确认行再修改并保存草稿仍可批量选择，未擅自改正式内容')
                        # Restore the browser draft through the same visible
                        # editor; the assertion above tests eligibility only.
                        await rec.fill(statement, manual_text)
                        await rec.click(editor.get_by_role('button', name='保存本行草稿', exact=True))
                        remaining = eligible[1]
                        assert not confirmation_current(saved, next(i for i in saved['items'] if i['id'] == remaining['id']))
                        await rec.click(table.get_by_role('checkbox', name='选择 ' + remaining['title'], exact=True))
                        await rec.click(work.get_by_role('button', name='查看并确认所选（1）', exact=True))
                        await expect(preview).to_contain_text(remaining['statement'])
                        await rec.note('第二条尚未确认。本次单独查看其内容，再明确确认；第一条不重复处理。')
                        await rec.click(preview.get_by_role('button', name='确认以上 1 条需求', exact=True))
                        await wait_store(store, pid, lambda p: all(confirmation_current(p, next(i for i in p['items'] if i['id'] == row['id'])) for row in eligible), 'Remaining confirmation did not persist')
                        await rec.click(readiness.get_by_role('button', name='确认需求核对并进入交付', exact=True))
                        await expect(work.get_by_role('heading', name='确认交付版本', exact=True)).to_be_visible()
                        await rec.note('5. 合成场景没有剩余关键决定。确认继续后自动进入交付页面；本次未确认正式交付。')
                        report['checks'].append('合成无阻塞场景确认继续后自动导航到确认交付')
                    else:
                        await rec.click(readiness.get_by_role('button', name='查看核对清单', exact=True))
                        await expect(readiness.locator('.connected-blocker-row').first).to_be_visible()
                        assert not await readiness.get_by_role('button', name='确认需求核对并进入交付', exact=True).count()
                        await rec.note('5. 真实项目仍有必要事项。这里明确列出处理入口；本次不编造答案强行进入交付。')
                        await page.screenshot(path=str(output / '04-real-blockers.png'))
                        await rec.click(readiness.get_by_role('button', name='处理第一项', exact=True))
                        report['checks'].append('真实待定内容仍阻止进入交付，并提供处理入口；未填造未知答案')
                    write(output / 'project-after.json', store.get(pid))
                    assert not provider.calls and not report['page_errors']
                    assert not [r for r in report['api_requests'] if r['status'] >= 400]
                    report['status'] = 'passed'
                except Exception as error:
                    report.update(status='failed', failure=str(error))
                    report['traceback'] = traceback.format_exc()
                    await page.screenshot(path=str(output / 'failure.png'), full_page=True)
                finally:
                    delayed_preview['release'].set()
                    report['model_calls'] = provider.calls
                    if outcome:
                        report['real_source_database_unchanged'] = sha(source_db) == before_source_hash
                    rec.save()
                    write(output / 'result.json', report)
                    video = page.video
                    await context.close()
                    await video.save_as(str(output / '批量确认实际操作.webm'))
                    await browser.close()
                    provenance = report.get('model_provenance', {}).get('mode', '合成导航，不代表真实模型质量')
                    (output / '操作录像.html').write_text('<!doctype html><meta charset="utf-8"><title>需求减负验收录像</title><h1>需求减负验收录像</h1><p>' + html.escape(args.scenario + ' · ' + report['status'] + ' · ' + provenance) + '</p><p>实际界面点击，隔离副本，录像期间 0 模型调用，非正式业务验收。</p><video controls style="width:100%;max-height:85vh" src="批量确认实际操作.webm"></video>', encoding='utf-8')
        print(json.dumps({k: report.get(k) for k in ('status', 'scenario', 'checks', 'failure', 'model_calls')}, ensure_ascii=False))
        if report['status'] != 'passed':
            raise SystemExit(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--freeze-oracle', action='store_true')
    parser.add_argument('--scenario', choices=('real', 'synthetic-navigation'), default='real')
    parser.add_argument('--session', type=Path)
    parser.add_argument('--oracle', type=Path)
    parser.add_argument('--correction-session', type=Path, help='Optional separately authorized real scoped result; read-only audit, never invokes a model')
    parser.add_argument('--static-build', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--commit-approved', action='store_true')
    args = parser.parse_args()
    if args.freeze_oracle:
        oracle = freeze_oracle(evidence_path(args.output))
        print(json.dumps(dict(status='frozen', snapshot_sha256=oracle['snapshot_sha256'], model_calls=0)))
    else:
        assert args.static_build, '--static-build is required for browser verification'
        asyncio.run(run_browser(args))


if __name__ == '__main__':
    main()
