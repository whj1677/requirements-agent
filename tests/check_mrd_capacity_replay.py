"""Replay saved tariff MRD responses locally; isolated ASGI rendering, no model or daily writes."""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
import copy
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
import traceback
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
ORIGIN = 'http://127.0.0.1:8765'
PID = 'P-d4b61a83e6f14612'
RID = 'RUN-461acc36fc3444b0'
CALLS = ('CALL-7319e24910e74e74', 'CALL-7e9ed10ba60547ce', 'CALL-bbdde21b52c349a1')


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def daily_snapshot():
    with sqlite3.connect((ROOT / 'data/requirements.sqlite3').as_uri() + '?mode=ro', uri=True) as db:
        projects = db.execute('SELECT id,revision,payload FROM projects ORDER BY id').fetchall()
        record = db.execute('SELECT payload FROM records WHERE id=?', (RID,)).fetchone()[0]
        project = next(row for row in projects if row[0] == PID)
        assert project[1] == 26, 'This replay is bound to revision 26; freeze a new case if daily project changed'
        state = [(pid, rev, hashlib.sha256(payload.encode()).hexdigest()) for pid, rev, payload in projects]
        return state, json.loads(project[2]), json.loads(record)


def original_business(project):
    return {k: v for k, v in project.items() if k not in ('documents', 'messages')}


async def browser_check(app, build, output, report, initial):
    import httpx
    from playwright.async_api import async_playwright, expect
    from tests.check_document_scroll_browser import wheel_to
    from app.prd import MRD_NORMATIVE_APPENDIX_TITLE
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            context = await browser.new_context(viewport=dict(width=1440, height=900))
            async def dispatch(route):
                request, url = route.request, urlsplit(route.request.url)
                assert url.netloc == '127.0.0.1:8765', 'External requests forbidden'
                assert request.method == 'GET', 'All browser writes forbidden'
                if url.path.startswith('/api/'):
                    response = await client.get(url.path + ('?' + url.query if url.query else ''))
                    report['api_requests'].append(dict(path=url.path, status=response.status_code))
                    await route.fulfill(status=response.status_code, body=response.content,
                                        content_type=response.headers.get('content-type', 'application/json'))
                else:
                    path = (build / (url.path.lstrip('/') or 'index.html')).resolve()
                    assert path.is_relative_to(build) and path.is_file(), str(path)
                    report['served_static'][url.path] = hashlib.sha256(path.read_bytes()).hexdigest()
                    await route.fulfill(status=200, body=path.read_bytes(),
                                        content_type=mimetypes.guess_type(path.name)[0] or 'application/octet-stream')
            await context.route('**/*', dispatch)
            page = await context.new_page()
            page.set_default_timeout(15000)
            page.on('pageerror', lambda error: report['page_errors'].append(str(error)))
            page.on('console', lambda msg: report['console_errors'].append(msg.text) if msg.type == 'error' else None)
            try:
                await page.goto(ORIGIN, wait_until='networkidle')
                await page.locator('aside.sidebar button[title="电价管理"]').click()
                workspace = page.locator('.project-workspace:not([hidden])')
                connected = workspace.locator('.connected-workspace')
                await connected.get_by_role('navigation', name='需求工作流程').get_by_role('button', name=re.compile(r'^3\s*确认交付$')).click()
                await expect(connected.get_by_role('heading', name='确认交付版本')).to_be_visible()
                await connected.get_by_role('button', name='查看与核对正式文档', exact=True).click()
                details = workspace.locator('details.connected-details')
                await expect(details).to_have_attribute('open', '')
                await details.get_by_role('tab', name='MRD 评审稿', exact=True).click()
                reader = details.get_by_role('article', name='MRD 正文', exact=True)
                await expect(reader).to_be_visible()
                doc = initial['documents']['mrd']['content']
                appendix = next(s for s in doc['sections'] if s['title'] == MRD_NORMATIVE_APPENDIX_TITLE + '（续 2）')
                toc = details.get_by_role('navigation', name='文档目录')
                await wheel_to(page, toc.locator(':scope > details > summary'), -1, 'MRD directory reachable', report)
                if await toc.locator('details').get_attribute('open') is None:
                    await toc.locator(':scope > details > summary').click()
                await toc.get_by_role('button', name=re.compile(re.escape(appendix['title']) + '$')).click()
                heading = reader.get_by_role('heading', name=re.compile(re.escape(appendix['title']) + '$'))
                await expect(heading).to_be_in_viewport()
                report['checks'].append('MRD continued annex is reachable through its visible directory control')
                last_id = [b['ref_ids'][0] for b in appendix['blocks'] if b['kind'] in ('requirement', 'rule', 'acceptance')][-1]
                item = next(i for i in initial['items'] if i['id'] == last_id)
                # The actual canonical acceptance text must be present in the reader, not just the ID or TOC.
                assert item['kind'] == 'acceptance'
                text = reader.get_by_text(item['statement'], exact=True)
                assert await text.count() >= 1, 'Final acceptance statement missing from rendered MRD'
                await wheel_to(page, text.first, 1, 'Final canonical acceptance statement', report)
                await expect(text.first).to_be_in_viewport()
                report['last_acceptance'] = dict(item_id=last_id, text_sha256=hashlib.sha256(item['statement'].encode()).hexdigest(), rendered=True)
                await page.screenshot(path=str(output / 'mrd-final-acceptance.png'))
                report['checks'].append('MRD final acceptance statement is rendered and reachable by mouse wheel')
                assert not report['page_errors'] and not report['console_errors']
                assert app.state.store.get(PID) == initial, 'Read-only browser unexpectedly changed isolated project'
                report['browser_read_only'] = True
            except Exception:
                await page.screenshot(path=str(output / 'failure.png'), full_page=True)
                raise
            finally:
                await context.close()
                await browser.close()


async def run(args):
    output, build = args.output.resolve(), args.static_build.resolve()
    evidence_root = ROOT / 'evidence/mrd-capacity-20261011'
    assert output.is_relative_to(evidence_root) and not output.exists()
    assert (build / 'index.html').is_file()
    output.mkdir(parents=True)
    report = dict(status='running', project_id=PID, revision=26, source_run=RID,
        model_calls=0, daily_data_access='read_only', listener_started=False, video_recorded=False,
        checks=[], replays=[], api_requests=[], served_static={}, page_errors=[], console_errors=[],
        formal_confirmation='NOT_RUN', business_acceptance='NOT_RUN', fresh_model_quality='NOT_RUN')
    before, project, run_record = daily_snapshot()
    write(output / 'project-r26.json', project)
    write(output / 'failed-run.json', run_record)
    write(output / 'daily-before.json', before)
    file_hashes = {}
    try:
        with tempfile.TemporaryDirectory(prefix='ra-tariff-mrd-replay-') as scratch:
            os.environ['RA_DATA_DIR'] = str(Path(scratch) / 'import-data')
            from app.main import create_app
            from app.core import Problem, brief_hash, dumps, now
            from app.contracts import validate_response, VALIDATOR
            from app.prd import allowed, compile_plan, MRD_NORMATIVE_APPENDIX_TITLE
            from app.workflow import apply_response
            from tests.check_refine_workspace_browser import NoModelProvider
            provider = NoModelProvider(Path(scratch))
            app = create_app(Path(scratch) / 'fixture-data', provider=provider)
            valid = []
            for index, call_id in enumerate(CALLS):
                path = ROOT / 'data/evidence/model-calls' / (call_id + '.json')
                blob = path.read_bytes()
                file_hashes[str(path)] = hashlib.sha256(blob).hexdigest()
                (output / path.name).write_bytes(blob)
                evidence = json.loads(blob)
                messages = json.loads(evidence['request_input'])
                request = json.loads(messages[1]['content'][0]['text'])
                plan = json.loads(evidence['final_output'])
                if index == 0:
                    try:
                        compile_plan(plan, project, 'mrd', request['omitted_excerpt_ids'], include_sketch=False)
                    except Problem as exc:
                        assert exc.code == 'REFERENCE_INVALID' and 'AC-999305e063474c04' in exc.message
                        report['replays'].append(dict(call_id=call_id, status='rejected_as_expected', error=exc.code, missing_ref='AC-999305e063474c04'))
                    else:
                        raise AssertionError('Actual invalid first response was not rejected')
                    continue
                frozen_hash = hashlib.sha256(dumps(project).encode()).hexdigest()
                value = compile_plan(plan, project, 'mrd', request['omitted_excerpt_ids'], include_sketch=False)
                validate_response(value, 'prd', project, request['excerpts'], 'mrd')
                assert frozen_hash == hashlib.sha256(dumps(project).encode()).hexdigest()
                expected = [i['id'] for i in project['items'] if allowed(i, project)]
                assert len(expected) == 59
                doc = value['result']
                annexes = [s for s in doc['sections'] if s['title'].startswith(MRD_NORMATIVE_APPENDIX_TITLE)]
                assert [len(s['blocks']) for s in annexes] == [100, 18]
                actual = [r for s in doc['sections'] for b in s['blocks'] if b['kind'] in ('requirement','rule','acceptance') for r in b['ref_ids']]
                assert Counter(actual) == Counter(expected), 'Canonical clause missing or duplicated'
                by_section = {s['section_id']:s for s in doc['sections']}
                assert {c['item_id'] for c in doc['coverage']} == set(expected)
                for coverage in doc['coverage']:
                    assert len(coverage['section_ids']) == 1
                    section = by_section[coverage['section_ids'][0]]
                    assert any(coverage['item_id'] in b['ref_ids'] and b['kind'] in ('requirement','rule','acceptance') for b in section['blocks'])
                # Prove the original compiler layout exceeds the unchanged contract.
                legacy = copy.deepcopy(value)
                legacy_annex = next(s for s in legacy['result']['sections'] if s['title'] == MRD_NORMATIVE_APPENDIX_TITLE)
                legacy_annex['blocks'] = [b for s in annexes for b in s['blocks']]
                errors = list(VALIDATOR.iter_errors(legacy))
                flat = []
                while errors:
                    error = errors.pop()
                    flat.append(error)
                    errors.extend(error.context)
                assert any(error.validator == 'maxItems' and error.validator_value == 100
                           and list(error.absolute_path)[-1:] == ['blocks'] and len(error.instance) == 118
                           for error in flat), 'Original 118-block section did not fail its precise maxItems limit'
                write(output / (call_id + '-compiled.json'), value)
                report['replays'].append(dict(call_id=call_id, status='passed', normative_count=len(actual), annex_blocks=[100,18], coverage_valid=True, full_schema_and_reference_validation=True, original_118_block_layout_rejected=True))
                valid.append((plan, value))
            assert len(valid) == 2
            prepared = copy.deepcopy(project)
            original = copy.deepcopy(original_business(prepared))
            mapping = apply_response(prepared, valid[-1][1])
            assert mapping == {}
            artifact = prepared['documents']['mrd']
            artifact['assembly_version'] = 'prd-plan-' + valid[-1][0]['plan_version']
            artifact['sketch_policy'] = 'excluded'
            assert artifact['assembly_version'] == 'prd-plan-4'
            assert original_business(prepared) == original
            assert prepared['documents'].get('prd') == project['documents'].get('prd')
            assert prepared.get('stage_checks') == project.get('stage_checks')
            assert brief_hash(prepared) == brief_hash(project)
            with app.state.store.connect() as db:
                db.execute('INSERT INTO projects VALUES(?,?,?)', (PID, 26, dumps(prepared)))
                db.execute('INSERT INTO revisions VALUES(?,?,?,?,?)', (PID, 26, dumps(prepared), 'isolated offline replay', now()))
            write(output / 'isolated-mrd-artifact.json', artifact)
            report['checks'].append('apply_response created only an isolated MRD; business content, stage checks, brief hash and existing PRD preserved')
            await browser_check(app, build, output, report, prepared)
            report['model_calls'] = provider.calls
            assert provider.calls == 0
            report['status'] = 'passed'
    except Exception as exc:
        report.update(status='failed', failure=str(exc), traceback=traceback.format_exc())
    finally:
        after, _, _ = daily_snapshot()
        report['daily_projects_unchanged'] = before == after
        report['source_call_files_unchanged'] = all(hashlib.sha256(Path(path).read_bytes()).hexdigest() == checksum for path, checksum in file_hashes.items())
        if not report['daily_projects_unchanged'] or not report['source_call_files_unchanged']:
            report.update(status='failed', invariance_failure=True)
        write(output / 'result.json', report)
    print(json.dumps({k: report.get(k) for k in ('status','replays','checks','failure','daily_projects_unchanged','model_calls')}, ensure_ascii=False))
    if report['status'] != 'passed':
        raise SystemExit(1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--static-build', type=Path, default=ROOT / 'web/dist')
    parser.add_argument('--output', type=Path, default=ROOT / 'evidence/mrd-capacity-20261011/replay')
    asyncio.run(run(parser.parse_args()))
