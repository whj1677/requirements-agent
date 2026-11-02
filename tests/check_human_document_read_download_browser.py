"""Read and download synthetic documents using intercepted sole-origin requests.

No listening service, daily data, or model calls. Review-mode downloads are
actual backend exports and write only export records to the temporary store.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
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
IMPORT_DIR = tempfile.TemporaryDirectory(prefix='ra-human-doc-import-')
os.environ['RA_DATA_DIR'] = str(Path(IMPORT_DIR.name) / 'data')
os.environ['RA_ACCESS_TOKEN'] = 'off'

import httpx
from docx import Document
from playwright.async_api import async_playwright, expect
from app.main import create_app
from app.core import brief_hash
from app.document_reader import export_readiness
from app.product_flow import status
from tests.browser_navigation import select_project
from tests.check_document_scroll_browser import seed_documents, wheel_to
from tests.check_refine_workspace_browser import NoModelProvider
from tests.test_detail_business_draft import sample_detail

ORIGIN = 'http://127.0.0.1:8765'
VIEWPORTS = ((846, 620), (1366, 768))
QUESTION = '相接边界如何处理？'


async def open_documents(page, pid):
    await page.goto(ORIGIN, wait_until='networkidle')
    await select_project(page, pid)
    workspace = page.locator('.project-workspace:not([hidden])')
    connected = workspace.locator('.connected-workspace')
    await connected.get_by_role('navigation', name='需求工作流程').get_by_role('button', name=re.compile(r'^3\s*确认交付$')).click()
    await connected.get_by_role('button', name='查看与核对正式文档', exact=True).click()
    details = workspace.locator('details.connected-details')
    await expect(details).to_have_attribute('open', '')
    return details


async def download_and_check(page, reader, kind, output, label):
    for fmt in ('md', 'docx'):
        async with page.expect_download(timeout=60000) as event:
            await reader.get_by_role('button', name='下载评审稿 ' + fmt.upper(), exact=True).click()
        download = await event.value
        destination = output / f'{label}-{kind}-review.{fmt}'
        await download.save_as(str(destination))
        assert destination.stat().st_size > 0
        if fmt == 'md':
            text = destination.read_text('utf-8')
        else:
            doc = Document(destination)
            text = '\n'.join([*(p.text for p in doc.paragraphs),
                *(cell.text for table in doc.tables for row in table.rows for cell in row.cells)])
        assert '非正式交付' in text and '待核对：' + QUESTION in text, (kind, fmt, text[-2500:])
        assert '待确认' in text or '未决' in text, (kind, fmt)


async def ready_case(page, pid, output, report, label):
    details = await open_documents(page, pid)
    for kind in ('prd', 'mrd'):
        await details.get_by_role('tab', name=kind.upper() + ' 评审稿', exact=True).click()
        reader = details.get_by_role('article', name=kind.upper() + ' 正文', exact=True)
        await expect(reader).to_be_visible()
        await expect(reader.get_by_label('下载前待处理事项')).to_contain_text(QUESTION)
        await download_and_check(page, reader, kind, output, label)
        await page.screenshot(path=str(output / f'{label}-{kind}-download.png'))
        end = reader.get_by_role('heading', name=kind.upper() + ' 云平台业务说明末尾', exact=False)
        await wheel_to(page, end, 1, label + ' ' + kind + ' final section', report)
        await expect(end).to_be_in_viewport()
        await page.screenshot(path=str(output / f'{label}-{kind}-end.png'))
        toc = details.get_by_role('navigation', name='文档目录')
        await wheel_to(page, toc.locator(':scope > details > summary'), -1, label + ' ' + kind + ' directory', report)
        if await toc.locator('details').get_attribute('open') is None:
            await toc.locator(':scope > details > summary').click()
        await toc.get_by_role('button', name=re.compile(kind.upper() + ' 云平台业务说明末尾')).click()
        await expect(end).to_be_in_viewport()
        report['checks'].append(f'{label} {kind}: review MD/DOCX download contains unresolved question; mouse scroll and directory reach end')

        # Real artifact history, not a spoofed project projection.
        options = details.locator('details.document-options')
        if await options.get_attribute('open') is None:
            await options.locator(':scope > summary').click()
        await options.get_by_label('文档版本', exact=True).select_option('DOC-HISTORY-' + kind)
        await expect(reader).to_contain_text('历史版本仅支持阅读')
        await expect(reader.get_by_role('button', name=re.compile('下载评审稿'))).to_have_count(0)
        await expect(reader.get_by_role('button', name=re.compile(r'^(DOCX|MD|ZIP) 下载$'))).to_have_count(0)
        await page.screenshot(path=str(output / f'{label}-{kind}-history.png'))
        report['checks'].append(f'{label} {kind}: historical document readable; explanation visible and no current download controls')
        await options.get_by_label('文档版本', exact=True).select_option('')


async def blocked_case(page, pid, output, report, label, reason):
    details = await open_documents(page, pid)
    await details.get_by_role('tab', name='PRD 评审稿', exact=True).click()
    reader = details.get_by_role('article', name='PRD 正文', exact=True)
    await expect(reader).to_be_visible()
    await expect(reader.get_by_label('评审稿下载状态')).to_contain_text(reason)
    await expect(reader.get_by_role('button', name=re.compile('下载评审稿'))).to_have_count(0)
    await expect(reader.get_by_role('button', name=re.compile(r'^(DOCX|MD|ZIP) 下载$'))).to_have_count(0)
    await page.screenshot(path=str(output / f'{label}-blocked.png'))
    report['checks'].append(f'{label}: blocked PRD remains readable and shows exact backend issue with no download button')


async def gate_case(page, pid, output, report, label):
    details = await open_documents(page, pid)
    for kind in ('prd', 'mrd'):
        await details.get_by_role('tab', name=kind.upper() + ' 评审稿', exact=True).click()
        reader = details.get_by_role('article', name=kind.upper() + ' 正文', exact=True)
        await expect(reader).to_be_visible()
        await expect(reader.get_by_role('button', name='下载评审稿 MD', exact=True)).to_be_visible()
        await download_and_check(page, reader, kind, output, label + '-incomplete-gate')
        await expect(details.get_by_role('button', name=re.compile('核对后重试|重新核对后发起任务|恢复草稿'))).to_have_count(0)
        await expect(details.get_by_role('button', name=re.compile('核对并更新|核对并生成|我已核对这份'))).to_have_count(0)
        pending = reader.get_by_label('下载前待处理事项')
        await pending.locator('summary').first.click()
        await expect(pending.get_by_role('button', name=re.compile('去回答|核对纳入'))).to_have_count(0)
        await page.screenshot(path=str(output / f'{label}-{kind}-incomplete-gate.png'))
        report['checks'].append(f'{label} {kind}: actual review MD/DOCX downloads contain unresolved question with incomplete prior gate')
        options = details.locator('details.document-options')
        if await options.get_attribute('open') is None:
            await options.locator(':scope > summary').click()
        await options.get_by_label('文档版本', exact=True).select_option('DOC-HISTORY-' + kind)
        await expect(reader).to_contain_text('历史版本仅支持阅读')
        await expect(reader.get_by_role('button', name=re.compile('下载评审稿'))).to_have_count(0)
        await expect(reader.get_by_role('button', name=re.compile(r'^(DOCX|MD|ZIP) 下载$'))).to_have_count(0)
        await page.screenshot(path=str(output / f'{label}-{kind}-incomplete-gate-history.png'))
        await options.get_by_label('文档版本', exact=True).select_option('')
        report['checks'].append(f'{label} {kind}: historical version remains read-only while prior gate is incomplete')


async def run(args):
    build, output = args.static_build.resolve(), args.output.resolve()
    assert (build / 'index.html').is_file()
    assert output.is_relative_to(ROOT / 'work') and not output.exists()
    output.mkdir(parents=True)
    report = dict(status='running', fixture='SYNTHETIC_ONLY', static_build=str(build),
        listener_started=False, daily_data_access=False, model_calls=0, checks=[], failures=[],
        page_errors=[], routing_errors=[], requests=[])
    with tempfile.TemporaryDirectory(prefix='ra-human-doc-browser-') as scratch:
        folder = Path(scratch)
        provider = NoModelProvider(folder)
        app = create_app(folder / 'data', provider=provider, env_path=folder / 'absent.env')
        store = app.state.store
        pid, initial = seed_documents(store)
        with store.edit(pid, initial['revision'], '合成文档不附带原型截图', bump=False) as (project, _):
            for artifact in project['documents'].values():
                artifact['sketch_policy'] = 'excluded'
        initial = store.get(pid)
        for kind in ('prd', 'mrd'):
            artifact = copy.deepcopy(initial['documents'][kind])
            artifact['id'] = 'DOC-HISTORY-' + kind
            store.record(pid, 'document_artifact', artifact, rid=artifact['id'])
            review = export_readiness(initial, kind, mode='review')
            formal = export_readiness(initial, kind, mode='formal')
            assert review['ready'] and not formal['ready'] and QUESTION in str(formal['problems']), (kind, review, formal)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN, timeout=90) as client:
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=True)

                async def dispatch(route):
                    request, url = route.request, urlsplit(route.request.url)
                    try:
                        if url.scheme != 'http' or url.netloc != '127.0.0.1:8765':
                            raise AssertionError('External URL blocked: ' + request.url)
                        if url.path.startswith('/api/'):
                            if request.method != 'GET':
                                raise AssertionError('Unexpected write: ' + request.method + ' ' + url.path)
                            headers = {k:v for k,v in request.headers.items() if k.lower() not in
                                ('host', 'content-length', 'connection', 'accept-encoding')}
                            headers['host'] = '127.0.0.1:8765'
                            target = url.path + ('?' + url.query if url.query else '')
                            response = await client.get(target, headers=headers)
                            report['requests'].append(dict(path=url.path, query=url.query, status=response.status_code))
                            await route.fulfill(status=response.status_code, body=response.content,
                                headers={k:v for k,v in response.headers.items() if k.lower() not in
                                    ('content-length', 'content-encoding', 'transfer-encoding')})
                        elif url.path == '/' or url.path.startswith('/assets/'):
                            target = (build / (url.path.lstrip('/') or 'index.html')).resolve()
                            if not target.is_relative_to(build) or not target.is_file():
                                raise AssertionError('Missing static asset: ' + url.path)
                            await route.fulfill(status=200, body=target.read_bytes(),
                                content_type=mimetypes.guess_type(target.name)[0] or 'application/octet-stream')
                        else:
                            raise AssertionError('Unknown URL blocked: ' + request.url)
                    except Exception as exc:
                        report['routing_errors'].append(str(exc))
                        await route.abort()

                for width, height in VIEWPORTS:
                    label = f'{width}x{height}'
                    context = await browser.new_context(viewport=dict(width=width, height=height), accept_downloads=True)
                    await context.route('**/*', dispatch)
                    page = await context.new_page()
                    page.set_default_timeout(12000)
                    page.on('pageerror', lambda error: report['page_errors'].append(str(error)))
                    try:
                        await ready_case(page, pid, output, report, label)
                    except Exception as exc:
                        report['failures'].append(dict(case='ready-' + label, error=str(exc), traceback=traceback.format_exc()))
                        await page.screenshot(path=str(output / f'{label}-ready-failure.png'), full_page=True)
                    finally:
                        await context.close()

                # The review-ready artifacts remain unchanged, but prior stages are unconfirmed.
                store.record(pid, 'user_task', dict(action='document', label='合成 PRD 生成失败',
                    status='failed', message='合成失败，不调用模型。', error='SCHEMA_INVALID',
                    calls=0, max_calls=None, cost=None, run_ids=[], source_ids=[], stages=['prd'],
                    completed_steps=0, step_states=[dict(stage='prd', status='failed')],
                    retry_input=dict(action='document', document_type='prd')), rid='TASK-READONLY-FAILED')
                current = store.get(pid)
                with store.edit(pid, current['revision'], '合成前置阶段尚未确认', bump=False) as (project, _):
                    project['stage_checks'] = {}
                incomplete = store.get(pid)
                assert not status(incomplete)[4]['available']
                assert all(export_readiness(incomplete, kind, mode='review')['ready'] for kind in ('prd', 'mrd'))
                for width, height in VIEWPORTS:
                    label = f'{width}x{height}'
                    context = await browser.new_context(viewport=dict(width=width, height=height))
                    await context.route('**/*', dispatch)
                    page = await context.new_page()
                    page.set_default_timeout(12000)
                    page.on('pageerror', lambda error: report['page_errors'].append(str(error)))
                    try:
                        await gate_case(page, pid, output, report, label)
                    except Exception as exc:
                        report['failures'].append(dict(case='incomplete-gate-' + label, error=str(exc), traceback=traceback.format_exc()))
                        await page.screenshot(path=str(output / f'{label}-incomplete-gate-failure.png'), full_page=True)
                    finally:
                        await context.close()

                # Prepare an actual backend hard error without marking the document stale.
                current = store.get(pid)
                with store.edit(pid, current['revision'], '合成不完整详细定义', bump=False) as (project, _):
                    next(item for item in project['items'] if item['id'] == 'REQ-0001').update(
                        detail_contract=sample_detail(), detail_required=True)
                current = store.get(pid)
                with store.edit(pid, current['revision'], '合成绑定不完整的当前文档', bump=False) as (project, _):
                    for artifact in project['documents'].values():
                        artifact.update(brief_hash=brief_hash(project), item_snapshot=copy.deepcopy(project['items']))
                blocked = store.get(pid)
                readiness = export_readiness(blocked, 'prd', mode='review')
                assert not readiness['ready'] and readiness['issues'][0]['code'] == 'DETAIL_VERSION_INVALID', readiness
                reason = readiness['issues'][0]['message']
                report['blocked_readiness'] = readiness
                for width, height in VIEWPORTS:
                    label = f'{width}x{height}'
                    context = await browser.new_context(viewport=dict(width=width, height=height))
                    await context.route('**/*', dispatch)
                    page = await context.new_page()
                    page.set_default_timeout(12000)
                    page.on('pageerror', lambda error: report['page_errors'].append(str(error)))
                    try:
                        await blocked_case(page, pid, output, report, label, reason)
                    except Exception as exc:
                        report['failures'].append(dict(case='blocked-' + label, error=str(exc), traceback=traceback.format_exc()))
                        await page.screenshot(path=str(output / f'{label}-blocked-failure.png'), full_page=True)
                    finally:
                        await context.close()
                await browser.close()
        report['model_calls'] = provider.calls
    report['status'] = 'passed' if not (report['failures'] or report['page_errors'] or report['routing_errors'] or report['model_calls']) else 'failed'
    (output / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(dict(status=report['status'], checks=len(report['checks']),
        failures=[dict(case=f['case'], error=f['error'].split('\n')[0]) for f in report['failures']],
        page_errors=report['page_errors'], routing_errors=report['routing_errors'], model_calls=report['model_calls']), ensure_ascii=False))
    if report['status'] != 'passed':
        raise SystemExit(1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--static-build', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    asyncio.run(run(parser.parse_args()))
