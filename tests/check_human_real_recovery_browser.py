"""Verify empty-table recovery against an isolated copy of a synthetic failure.

The captured real-model response is fixture data only. Browser HTTP traffic is
intercepted into a temporary in-process ASGI app; task submission is blocked and
the provider rejects every request. No listener or daily store is used.
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
EVIDENCE_ROOT = ROOT / 'work' / 'human-real-20261031'
sys.path.insert(0, str(ROOT))
# app.main also constructs a module-level app: isolate before importing it.
IMPORT_SANDBOX = tempfile.TemporaryDirectory(prefix='ra-real-recovery-import-')
os.environ['RA_DATA_DIR'] = str(Path(IMPORT_SANDBOX.name) / 'data')
os.environ['RA_ACCESS_TOKEN'] = 'off'

import httpx
from playwright.async_api import async_playwright, expect

from app.core import digest
from app.main import create_app
from app.provider import DEFAULT, Provider, origin
from tests.browser_navigation import select_project

ORIGIN = 'http://127.0.0.1:8765'
VIEWPORTS = ((1366, 768), (846, 620))
LOCAL_EDIT = '人工本地修改：仅核对告警协作，不增加本期功能。'
PERSISTED_KEYS = (
    'schema_version', 'mode', 'items', 'questions', 'options', 'sources',
    'messages', 'documents', 'ui', 'review', 'active_baseline_id', 'grants',
    'reference_mode', 'intake', 'interview_context_review', 'interview_state',
    'product_context', 'product_context_proposal', 'product_context_proposal_evidence',
    'business_draft_reviews', 'stage_checks', 'workflow_mode', 'workspace_draft',
    'current_task_source_id', 'scope_profile', 'context_field_states', 'domain_context',
)


class RejectingProvider(Provider):
    def __init__(self, folder):
        super().__init__(env_path=folder / 'absent.env', credential_dir=folder / 'credentials')
        self.calls = 0
        # Metadata-only planning can show the usual recipient without a real key.
        self.keys[origin(DEFAULT)] = 'OFFLINE_RECOVERY_FIXTURE_ONLY'

    async def request(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError('Model requests are forbidden in recovery verification')


def seed_failure(store, fixture):
    assert fixture['id'] == 'P-8716b3947c514093', 'Unexpected fixture project'
    assert fixture['items'] == [], 'Recovery fixture must have no loaded items'
    review = fixture['business_draft_reviews'][-1]
    assert not review['accepted'] and len(review['rejected']) == 1
    assert review['rejected'][0]['code'] == 'BUSINESS_DRAFT_DETAIL_FLOW'
    assert '合成' in fixture['name'] or any('合成' in m.get('text', '') for m in fixture['messages'])
    created = store.create('隔离回归：真实合成失败恢复')
    with store.edit(created['id'], created['revision'], '复制明确授权的合成失败快照') as (project, _):
        project.update({key: copy.deepcopy(fixture[key]) for key in PERSISTED_KEYS if key in fixture})
    return created['id']


def seed_reviewed_empty_priority(store, fixture):
    """Canonical review proof only; response-only projection is never persisted."""
    pid = seed_failure(store, fixture)
    with store.edit(pid, store.get(pid)['revision'], '冻结人工核对空优先级与旧AI建议的合成前置') as (project, _):
        project['name'] = '隔离回归：人工空优先级保留'
        project.setdefault('product_context', {})['priority'] = ''
        state = dict(status='unknown', note='人工已核对：本期尚未决定优先级')
        project.setdefault('context_field_states', {})['priority'] = state
        project.setdefault('context_confirmations', {})['priority'] = dict(
            actor='authenticated_local_user', content_hash=digest(dict(text='', state=state)),
            confirmed_at='2026-10-31T00:00:00+00:00')
        project.setdefault('product_context_proposal', {})['priority'] = '建议：P1（旧建议，尚未选择）'
        project['refinement_completion'] = dict(mode='all')
    assert 'context_review_statuses' not in store.get(pid)
    return pid


def seed_unchanged_retry(store, failure):
    from tests.check_detail_prd_browser import seed
    pid = seed(store)
    with store.edit(pid, store.get(pid)['revision'], '冻结未修改六验收条目的重试计划回归') as (project, _):
        project['name'] = '隔离回归：未编辑条目重试不伪造人工修改'
        acceptance = next(item for item in project['items'] if item['id'] == 'AC-0001')
        for number in range(2, 7):
            item = copy.deepcopy(acceptance)
            item.update(id='AC-RETRY-'+str(number), statement=f'已保存的合成验收结果 {number}')
            project['items'].append(item)
        review = copy.deepcopy(failure['business_draft_reviews'][-1])
        review['rejected'][0]['requirement_id'] = 'REQ-0001'
        project['business_draft_reviews'] = [review]
    return pid


async def open_table(page, pid):
    await select_project(page, pid)
    work = page.locator('.connected-workspace:visible')
    await expect(work.get_by_role('button', name='和助手聊需求', exact=True)).to_have_attribute('aria-pressed', 'true')
    await work.get_by_role('navigation', name='需求工作流程').get_by_role('button', name=re.compile('完善需求')).click()
    await expect(work.get_by_role('table', name='需求确认表', exact=True)).to_be_visible()
    return work


async def control_geometry(locator):
    """Observe clipping and pointer ownership without scrolling or clicking."""
    return await locator.evaluate('''element => {
      const r=element.getBoundingClientRect();
      const points=[[r.left+r.width/2,r.top+r.height/2],
        [r.left+3,r.top+3],[r.right-3,r.top+3],
        [r.left+3,r.bottom-3],[r.right-3,r.bottom-3]];
      return {rect:{left:r.left,top:r.top,right:r.right,bottom:r.bottom,width:r.width,height:r.height},
        viewport:{width:innerWidth,height:innerHeight},
        fully_in_viewport:r.width>0&&r.height>0&&r.left>=0&&r.top>=0&&r.right<=innerWidth&&r.bottom<=innerHeight,
        hit_points:points.map(([x,y])=>{const hit=document.elementFromPoint(x,y);return {x,y,
          owned:!!hit&&(hit===element||element.contains(hit)),
          hit:hit?hit.tagName.toLowerCase()+'.'+String(hit.className):null};})};
    }''')


async def layout_geometry(work, *, failed):
    """Check failure-heading separation and full footer accessibility.

    The normal compact layout intentionally hides the heading copy at short
    heights; its visibility is not a requirement of this recovery check.
    """
    result = dict(failed_state=failed, footer={}, heading_overlap=[])
    footer = work.locator('.refinement-sheet-footer')
    for name, locator in (
        ('save', footer.get_by_role('button', name='保存草稿', exact=True)),
        ('confirm', footer.locator('button.primary')),
    ):
        await expect(locator).to_be_visible()
        result['footer'][name] = await control_geometry(locator)
    if failed:
        title = await work.get_by_text('本次 AI 需求草稿未载入', exact=True).bounding_box()
        assert title, 'Failure callout title is missing'
        result['callout_title'] = title
        actions = (
            ('ai_suggestions', work.locator('.sheet-view-actions').get_by_role('button', name='AI 补充建议', exact=True)),
            ('more_actions', work.locator('.sheet-view-actions .sheet-more-actions > summary')),
        )
        result['heading_actions'] = {}
        for name, locator in actions:
            await expect(locator).to_be_visible()
            rect = await locator.bounding_box()
            assert rect, 'Heading action is missing: ' + name
            result['heading_actions'][name] = rect
            overlap_width = max(0, min(title['x']+title['width'], rect['x']+rect['width'])-max(title['x'], rect['x']))
            overlap_height = max(0, min(title['y']+title['height'], rect['y']+rect['height'])-max(title['y'], rect['y']))
            if overlap_width > 0 and overlap_height > 0:
                result['heading_overlap'].append(dict(action=name, width=overlap_width, height=overlap_height))
    result['passed'] = not result['heading_overlap'] and all(
        row['fully_in_viewport'] and all(point['owned'] for point in row['hit_points'])
        for row in result['footer'].values())
    return result


async def run(args):
    build, output, snapshot = (path.resolve() for path in (args.static_build, args.output, args.snapshot))
    assert (build / 'index.html').is_file(), f'Missing static build: {build}'
    assert output.is_relative_to(EVIDENCE_ROOT) and output != EVIDENCE_ROOT
    assert snapshot.is_relative_to(EVIDENCE_ROOT), 'Only the explicitly captured synthetic snapshot is allowed'
    assert not output.exists(), f'Use a fresh output directory: {output}'
    output.mkdir(parents=True)
    snapshot_bytes = snapshot.read_bytes()
    fixture = json.loads(snapshot_bytes)
    report = dict(status='running', static_build=str(build), snapshot=str(snapshot),
                  snapshot_sha256=hashlib.sha256(snapshot_bytes).hexdigest(),
                  fixture='CAPTURED_SYNTHETIC_FAILURE_ONLY', listener_started=False,
                  daily_data_access=False, real_model_calls=0, provider_attempts=0,
                  checks=[], failures=[], requests=[], plans=[], task_submissions=[],
                  page_errors=[], routing_errors=[])
    with tempfile.TemporaryDirectory(prefix='ra-real-recovery-') as scratch:
        folder = Path(scratch)
        provider = RejectingProvider(folder)
        app = create_app(folder / 'data', provider=provider, env_path=folder / 'absent.env')
        store = app.state.store
        pid = seed_failure(store, fixture)
        reviewed_priority = seed_reviewed_empty_priority(store, fixture)
        unchanged_retry = seed_unchanged_retry(store, fixture)
        empty = store.create('隔离回归：正常新空项目')['id']
        before = store.get(pid)
        report['isolated_project_id'] = pid
        report['fixture_project_id'] = fixture['id']
        reason = fixture['business_draft_reviews'][-1]['rejected'][0]['reason']
        first_user = next(m['text'] for m in fixture['messages'] if m['role'] == 'user')
        first_reply = next(m['text'] for m in fixture['messages'] if m['role'] == 'assistant')
        summary = copy.deepcopy(fixture['product_context_proposal'])

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN, timeout=30) as client:
            projected = (await client.get(f'/api/projects/{reviewed_priority}')).json()
            assert projected['context_review_statuses']['priority'] is True
            assert len(projected['context_review_statuses']) == 10
            assert projected['product_context']['priority'] == ''
            assert projected['context_field_states']['priority']['status'] == 'unknown'
            assert 'context_review_statuses' not in store.get(reviewed_priority), 'API projection leaked into canonical data'
            report['reviewed_priority_projection'] = projected['context_review_statuses']
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=True)
                for width, height in VIEWPORTS:
                    label = f'{width}x{height}'
                    plans_at_start = len(report['plans'])
                    context = await browser.new_context(viewport=dict(width=width, height=height), service_workers='block')

                    async def dispatch(route):
                        request, url = route.request, urlsplit(route.request.url)
                        try:
                            if url.scheme != 'http' or url.netloc != '127.0.0.1:8765':
                                raise AssertionError('External request blocked: ' + request.url)
                            if url.path.startswith('/api/'):
                                plan_event = None
                                if request.method not in ('GET', 'HEAD') and (
                                    url.path.endswith('/actions') or '/runs' in url.path or '/tasks' in url.path
                                ):
                                    report['task_submissions'].append(dict(viewport=label, path=url.path))
                                    await route.fulfill(status=503, content_type='application/json',
                                        body=json.dumps({'detail': 'OFFLINE: task submission forbidden'}))
                                    return
                                if request.method == 'POST' and url.path.endswith('/actions/plan'):
                                    plan_event = dict(viewport=label, input=request.post_data_json)
                                    report['plans'].append(plan_event)
                                headers = {key: value for key, value in request.headers.items() if key.lower() not in
                                           ('host', 'content-length', 'connection', 'accept-encoding')}
                                headers['host'] = '127.0.0.1:8765'
                                target = url.path + ('?' + url.query if url.query else '')
                                response = await client.request(request.method, target,
                                    content=request.post_data_buffer, headers=headers)
                                if plan_event is not None:
                                    plan_event['response'] = response.json()
                                report['requests'].append(dict(viewport=label, method=request.method,
                                    path=url.path, status=response.status_code))
                                await route.fulfill(status=response.status_code, body=response.content,
                                    content_type=response.headers.get('content-type', 'application/json'))
                            elif url.path == '/' or url.path.startswith('/assets/'):
                                target = (build / (url.path.lstrip('/') or 'index.html')).resolve()
                                assert target.is_relative_to(build) and target.is_file(), 'Missing static asset: ' + url.path
                                await route.fulfill(status=200, body=target.read_bytes(),
                                    content_type=mimetypes.guess_type(target.name)[0] or 'application/octet-stream')
                            else:
                                raise AssertionError('Unexpected local path: ' + url.path)
                        except Exception as exc:
                            report['routing_errors'].append(f'{type(exc).__name__}: {exc}')
                            await route.abort()

                    await context.route('**/*', dispatch)
                    page = await context.new_page()
                    page.set_default_timeout(10000)
                    page.on('pageerror', lambda error: report['page_errors'].append(str(error)))
                    try:
                        await page.goto(ORIGIN + '/', wait_until='networkidle')
                        work = await open_table(page, pid)
                        # No details disclosure or scroll is permitted before these
                        # assertions: the failure must be discoverable on first view.
                        recovery = work.get_by_role('button', name='重新生成需求草稿', exact=True)
                        reason_text = work.get_by_text(reason, exact=True).filter(visible=True).first
                        await expect(recovery).to_be_visible()
                        await expect(reason_text).to_be_visible()
                        visibility = {}
                        for name, locator in (('recovery_button', recovery), ('rejection_reason', reason_text)):
                            try:
                                await expect(locator).to_be_in_viewport(ratio=.5, timeout=800)
                                visibility[name] = True
                            except AssertionError:
                                visibility[name] = False
                        if not all(visibility.values()):
                            report['failures'].append(dict(viewport=label, stage='first_view_visibility',
                                error='Failure diagnosis/recovery is outside the first table viewport', visibility=visibility))
                        assert len(report['plans']) == plans_at_start and not report['task_submissions'] and provider.calls == 0
                        await page.screenshot(path=str(output / (label + '-visible-failure.png')))
                        report['checks'].append(dict(viewport=label, check='failed empty table first-view diagnosis/recovery; no automatic plan/task', visibility=visibility))
                        geometry = await layout_geometry(work, failed=True)
                        report['checks'].append(dict(viewport=label, check='failure title avoids heading actions; footer buttons fully visible and pointer accessible', geometry=geometry))
                        if not geometry['passed']:
                            report['failures'].append(dict(viewport=label, stage='failed_state_geometry',
                                error='Failure title overlaps heading actions or footer controls are clipped/obstructed', geometry=geometry))

                        target = work.get_by_role('textbox', name='希望达到的目标', exact=True)
                        await target.fill(LOCAL_EDIT)
                        plans_before = len(report['plans'])
                        await recovery.click()
                        dialog = page.get_by_role('dialog', name='核对本次任务', exact=True)
                        await expect(dialog).to_be_visible()
                        assert len(report['plans']) == plans_before + 1
                        plan = report['plans'][-1]['input']
                        assert plan['action'] == 'organize' and plan['draft_request']['mode'] == 'all', plan
                        assert LOCAL_EDIT in plan['message'], 'Original requestAI flow lost the local human edit'
                        assert provider.calls == 0 and not report['task_submissions']
                        await page.screenshot(path=str(output / (label + '-recovery-plan.png')))
                        await dialog.get_by_role('button', name='返回保留输入', exact=True).click()
                        await expect(dialog).not_to_be_visible()
                        await expect(target).to_have_value(LOCAL_EDIT)
                        report['checks'].append(dict(viewport=label, check='explicit recovery uses original organize/all plan; cancellation preserves local human edit'))

                        # Reload reopens the assistant first; reopening the table is
                        # read-only navigation and restores the browser-local edit.
                        await page.reload(wait_until='networkidle')
                        await select_project(page, pid)
                        work = page.locator('.connected-workspace:visible')
                        await expect(work.get_by_role('button', name='和助手聊需求', exact=True)).to_have_attribute('aria-pressed', 'true')
                        await expect(work).to_contain_text(first_user)
                        await expect(work).to_contain_text(first_reply)
                        work = await open_table(page, pid)
                        await expect(work.get_by_role('textbox', name='希望达到的目标', exact=True)).to_have_value(LOCAL_EDIT)
                        current = store.get(pid)
                        assert current['messages'] == before['messages']
                        assert current['product_context_proposal'] == summary
                        assert current['product_context_proposal_evidence'] == before['product_context_proposal_evidence']
                        assert current['sources'] == before['sources'] and current['items'] == []
                        assert current.get('product_context') == before.get('product_context'), 'Local edit was silently confirmed'
                        report['checks'].append(dict(viewport=label, check='reload preserves original chat, summary, sources and unconfirmed browser-local edit'))

                        # A new project with no generation attempt must not inherit
                        # another project's failure explanation or recovery action.
                        await select_project(page, empty)
                        work = await open_table(page, empty)
                        await expect(work.get_by_role('button', name='重新生成需求草稿', exact=True)).to_have_count(0)
                        await expect(work.get_by_text(reason, exact=True)).to_have_count(0)
                        await expect(work).not_to_contain_text('最近一次 AI 整理：0 条草稿可核对，1 条未载入')
                        await expect(work).to_contain_text('尚无需求草稿')
                        await page.screenshot(path=str(output / (label + '-normal-empty.png')))
                        geometry = await layout_geometry(work, failed=False)
                        report['checks'].append(dict(viewport=label, check='normal empty table footer remains fully visible and pointer accessible', geometry=geometry))
                        if not geometry['passed']:
                            report['failures'].append(dict(viewport=label, stage='normal_state_geometry',
                                error='Normal table footer controls are clipped or obstructed', geometry=geometry))
                        assert len(report['plans']) == plans_before + 1 and not report['task_submissions'] and provider.calls == 0
                        report['checks'].append(dict(viewport=label, check='normal new empty project shows no failed-result diagnosis or recovery button'))

                        work = await open_table(page, reviewed_priority)
                        priority = work.get_by_role('textbox', name='优先级与依据', exact=True)
                        state = work.get_by_role('combobox', name='优先级与依据 状态', exact=True)
                        await priority.scroll_into_view_if_needed()
                        await expect(priority).to_have_value('')
                        await expect(state).to_have_value('unknown')
                        row = priority.locator('xpath=ancestor::tr')
                        ui = dict(value=await priority.input_value(), status=await state.input_value(),
                                  source_label=await row.locator('.sheet-origin').inner_text())
                        assert ui['source_label'] == '暂无内容', 'Reviewed empty priority must not be labelled AI pending adoption: ' + ui['source_label']
                        await page.screenshot(path=str(output / (label + '-reviewed-empty-priority.png')))
                        await page.reload(wait_until='networkidle')
                        work = await open_table(page, reviewed_priority)
                        await expect(work.get_by_role('textbox', name='优先级与依据', exact=True)).to_have_value('')
                        await expect(work.get_by_role('combobox', name='优先级与依据 状态', exact=True)).to_have_value('unknown')
                        assert 'context_review_statuses' not in store.get(reviewed_priority)
                        assert len(report['plans']) == plans_before + 1 and not report['task_submissions'] and provider.calls == 0
                        report['checks'].append(dict(viewport=label,
                            check='GET-derived human review preserves empty unknown priority in bulk mode and after reload', ui=ui))

                        # A normal row is actually initialized, saved without
                        # edits, reloaded, and restored before the single retry.
                        work = await open_table(page, unchanged_retry)
                        retry_before = copy.deepcopy(store.get(unchanged_retry))
                        await work.get_by_role('button', name='保存草稿', exact=True).click()
                        await page.reload(wait_until='networkidle')
                        work = await open_table(page, unchanged_retry)
                        old_texts = [item['statement'] for item in retry_before['items']
                                     if item['kind'] in ('requirement', 'acceptance')]
                        retry_plans = len(report['plans'])
                        await work.get_by_role('button', name='重新整理这条需求', exact=True).click()
                        dialog = page.get_by_role('dialog', name='核对本次任务', exact=True)
                        await expect(dialog).to_be_visible()
                        assert len(report['plans']) == retry_plans + 1
                        plan = report['plans'][-1]
                        pending = plan['response']['pending_text']
                        assert plan['input']['action'] == 'clarify'
                        assert plan['input']['target'] == dict(kind='item', id='REQ-0001')
                        assert pending == '请只重新整理这条需求，保留已有资料和人工内容，未知决定保持待确认。', pending
                        assert '本表未提交的人工修改' not in pending
                        assert all(text not in pending for text in old_texts)
                        await page.screenshot(path=str(output / (label + '-unchanged-row-retry-plan.png')))
                        await dialog.get_by_role('button', name='返回保留输入', exact=True).click()
                        await expect(dialog).not_to_be_visible()
                        assert store.get(unchanged_retry) == retry_before
                        assert provider.calls == 0 and not report['task_submissions']
                        report['checks'].append(dict(viewport=label,
                            check='untouched initial/save/reload/restore row retry plan has purpose only, no false human edits or old six-acceptance payload',
                            pending_text=pending))
                    except Exception as exc:
                        report['failures'].append(dict(viewport=label, error=str(exc), traceback=traceback.format_exc()))
                        await page.screenshot(path=str(output / (label + '-failure.png')), full_page=True)
                        (output / (label + '-failure.txt')).write_text(await page.locator('body').inner_text(), encoding='utf-8')
                    finally:
                        await context.close()
                await browser.close()
        report['provider_attempts'] = provider.calls
        assert store.records(pid, 'user_task') == [] and store.records(pid, 'run') == []
    report['status'] = 'passed' if not any(report[key] for key in
        ('failures', 'page_errors', 'routing_errors', 'task_submissions', 'provider_attempts')) else 'failed'
    (output / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(dict(status=report['status'], checks=len(report['checks']),
        failures=[dict(viewport=row['viewport'], error=row['error'].split('\n')[0]) for row in report['failures']],
        provider_attempts=report['provider_attempts'], plans=len(report['plans']),
        task_submissions=len(report['task_submissions']), page_errors=report['page_errors'],
        routing_errors=report['routing_errors']), ensure_ascii=False))
    if report['status'] != 'passed':
        raise SystemExit(1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--static-build', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--snapshot', type=Path, default=EVIDENCE_ROOT / 'project-organize-failed.json')
    asyncio.run(run(parser.parse_args()))
