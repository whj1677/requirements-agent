"""Real UI acceptance-revision confirmation over isolated ASGI; no video/model/data access."""
from __future__ import annotations
import argparse, asyncio, copy, hashlib, json, mimetypes, os, sys, tempfile, traceback
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
ORIGIN = 'http://127.0.0.1:8765'
REVISION_TEXT = '管理员从模板库打开列表，页面显示已保存的模板名称；查询为空时显示暂无数据。'
EDITED_TEXT = REVISION_TEXT + '核对显示的模板名称与已保存值一致。'
ADD_TEXT = '管理员打开模板列表后，核对模板名称可见。'
NEXT_TEXT = '列表查询结果为空时，核对页面显示暂无数据。'


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def item(project, iid):
    return next(row for row in project['items'] if row['id'] == iid)


def seed(store):
    from app.core import now
    from app.requirement_confirmation import row_fingerprint
    from app.sources import save_source
    from tests.check_draft_workload_browser import seed_navigation
    pid = seed_navigation(store)
    with store.edit(pid, store.get(pid)['revision'], '冻结合成储能云验收修订fixture') as (project, _):
        project['name'] = '合成储能云：验收候选确认'
        ac = item(project, 'AC-W1'); ac['selection_status'] = 'selected'
        source = save_source(store, '合成验收修订依据.txt', REVISION_TEXT.encode('utf-8'), 'goal')
        project['sources'].append(source)
        candidate = copy.deepcopy(ac)
        candidate.update(id='AC-REVISION', target_item_id=ac['id'], action='revise', statement=REVISION_TEXT,
            selection_status='candidate', recommendation_requirement_id='REQ-W1',
            source_refs=[dict(source_id=source['id'], excerpt_id=ex['id']) for ex in source['excerpts']])
        project['items'].append(candidate)
        req = item(project, 'REQ-W1')
        req['requirement_confirmation'] = dict(schema='requirement-confirmation-1',
            actor='authenticated_local_user', content_hash=row_fingerprint(project, req), reviewed_at=now())
    store.record(pid, 'user_task', dict(action='clarify', label='合成验收候选准备', status='succeeded',
        created=now(), source_ids=[], run_ids=[], max_calls=0, calls=0, completed_steps=1,
        stages=['clarify'], step_states=[dict(stage='clarify', status='succeeded')],
        message='冻结合成候选，仅验证交互及确认链路。'), 'TASK-AC-REVISION')
    return pid, store.get(pid)


def append_fixture_candidate(store, pid, iid, text):
    with store.edit(pid, store.get(pid)['revision'], '合成异步追加验收候选fixture') as (project, _):
        ac = copy.deepcopy(item(project, 'AC-W1'))
        ac.update(id=iid, action='add', statement=text, selection_status='candidate',
                  related_refs=['REQ-W1'], recommendation_requirement_id='REQ-W1')
        project['items'].append(ac)


async def run(args):
    build, output = args.static_build.resolve(), args.output.resolve()
    assert output.is_relative_to(ROOT / 'evidence') and not output.exists()
    assert (build / 'index.html').is_file()
    output.mkdir(parents=True)
    report = dict(status='running', fixture='FROZEN_SYNTHETIC_STORAGE_CLOUD', model_calls=0,
        daily_data_access=False, listener_started=False, video_recorded=False,
        checks=[], page_errors=[], api_requests=[], served_static={}, user_business_acceptance='NOT_RUN')
    with tempfile.TemporaryDirectory(prefix='ra-ac-revision-') as temporary:
        folder = Path(temporary); os.environ['RA_DATA_DIR'] = str(folder / 'import')
        import httpx
        from playwright.async_api import async_playwright, expect
        from app.main import create_app
        from app.requirement_confirmation import confirmation_current
        from tests.check_refine_workspace_browser import NoModelProvider
        provider = NoModelProvider(folder); app = create_app(folder / 'data', provider=provider)
        store = app.state.store; pid, initial = seed(store)
        write(output / 'project-before.json', initial)
        report['project_id'] = pid
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=True)
                context = await browser.new_context(viewport=dict(width=1440, height=1000))
                async def dispatch(route):
                    request = route.request; url = urlsplit(request.url)
                    assert url.netloc == '127.0.0.1:8765', 'External requests forbidden'
                    if url.path.startswith('/api/'):
                        assert request.method == 'GET' or url.path.endswith('/refinement-confirmation'), 'Unexpected write: ' + url.path
                        headers = {k:v for k,v in request.headers.items() if k.lower() not in ('host', 'content-length', 'connection', 'accept-encoding')}
                        headers['host'] = '127.0.0.1:8765'
                        response = await client.request(request.method, url.path + ('?' + url.query if url.query else ''),
                            headers=headers, content=request.post_data_buffer)
                        event = dict(method=request.method, path=url.path, status=response.status_code)
                        if request.post_data: event.update(body=request.post_data_json, response=response.json())
                        report['api_requests'].append(event)
                        await route.fulfill(status=response.status_code, body=response.content,
                            content_type=response.headers.get('content-type', 'application/json'))
                    else:
                        path = (build / (url.path.lstrip('/') or 'index.html')).resolve()
                        assert path.is_relative_to(build) and path.is_file()
                        report['served_static'][url.path] = hashlib.sha256(path.read_bytes()).hexdigest()
                        await route.fulfill(status=200, body=path.read_bytes(), content_type=mimetypes.guess_type(path.name)[0] or 'application/octet-stream')
                await context.route('**/*', dispatch)
                page = await context.new_page(); page.set_default_timeout(15000)
                page.on('pageerror', lambda error: report['page_errors'].append(str(error)))
                try:
                    await page.goto(ORIGIN, wait_until='networkidle')
                    await page.locator('aside.sidebar button[title="' + initial['name'] + '"]').click()
                    work = page.locator('.connected-workspace:visible')
                    await work.get_by_role('button', name='2 完善需求', exact=True).click()
                    table = work.get_by_role('table', name='需求确认表', exact=True)
                    await expect(table).to_be_visible()
                    ac = table.get_by_role('textbox', name='模板查看 验收建议 1', exact=True)
                    await expect(ac).to_have_value(item(initial, 'AC-W1')['statement'])
                    await expect(table.get_by_text(REVISION_TEXT, exact=True)).to_be_visible()
                    await ac.scroll_into_view_if_needed()
                    await page.screenshot(path=str(output / '01-original-and-revision.png'))
                    assert store.get(pid) == initial
                    await table.get_by_role('button', name='使用此验收建议', exact=True).click()
                    await expect(ac).to_have_value(REVISION_TEXT)
                    await page.wait_for_function("key=>JSON.parse(sessionStorage.getItem(key)||'null')?.rows?.['REQ-W1']?.acceptance_items?.[0]?.candidate_id==='AC-REVISION'",arg=pid+'-refinement-sheet')
                    draft = await page.evaluate('key=>JSON.parse(sessionStorage.getItem(key))', pid + '-refinement-sheet')
                    assert draft['rows']['REQ-W1']['acceptance_items'][0]['candidate_id'] == 'AC-REVISION'
                    assert store.get(pid) == initial, 'Using the suggestion must not save or confirm it'
                    await ac.fill(EDITED_TEXT)
                    await work.get_by_role('button', name='保存草稿', exact=True).click()
                    report['checks'].append('已确认原验收保持原文，修订建议并列显示；点击使用后仅写本地candidate_id，未采纳或写业务数据')

                    append_fixture_candidate(store, pid, 'AC-APPEND', ADD_TEXT)
                    await work.get_by_role('button', name='刷新', exact=True).click()
                    await expect(ac).to_have_value(EDITED_TEXT)
                    second = table.get_by_role('textbox', name='模板查看 验收建议 2', exact=True)
                    await expect(second).to_have_value(ADD_TEXT)
                    await table.locator('[data-cell-key="requirement-REQ-W1-acceptance"]').get_by_role('button', name='移除此条验收', exact=True).click()
                    await work.get_by_role('button', name='保存草稿', exact=True).click()
                    append_fixture_candidate(store, pid, 'AC-APPEND-NEXT', NEXT_TEXT)
                    await work.get_by_role('button', name='刷新', exact=True).click()
                    await expect(ac).to_have_value(EDITED_TEXT)
                    await expect(second).to_have_value(NEXT_TEXT)
                    assert await table.get_by_role('textbox', name='模板查看 验收建议', exact=False).count() == 2
                    await ac.scroll_into_view_if_needed()
                    await page.screenshot(path=str(output / '02-edited-revision-and-new-candidate.png'))
                    before = store.get(pid)
                    assert item(before, 'AC-W1')['statement'] == item(initial, 'AC-W1')['statement']
                    assert item(before, 'AC-REVISION')['selection_status'] == 'candidate'
                    assert item(before, 'AC-APPEND-NEXT')['selection_status'] == 'candidate'
                    assert not [r for r in report['api_requests'] if r['method'] != 'GET']
                    report['checks'].append('人工修改验收时，新add建议仍追加；删除的候选不会在下一次更新中复活，未提交前原验收与候选状态不变')

                    await work.get_by_role('button', name='确认并进入交付', exact=True).click()
                    await expect(work).to_have_attribute('data-step', 'delivery')
                    final = store.get(pid)
                    writes = [r for r in report['api_requests'] if r['method'] != 'GET']
                    assert len(writes) == 1 and writes[0]['response']['confirmed'], writes
                    row = next(r for r in writes[0]['body']['rows'] if r['requirement_id'] == 'REQ-W1')
                    assert row['acceptance_items'] == [dict(id='AC-W1', statement=EDITED_TEXT, candidate_id='AC-REVISION'),
                                                       dict(id='AC-APPEND-NEXT', statement=NEXT_TEXT)]
                    saved = item(final, 'AC-W1'); candidate = item(initial, 'AC-REVISION')
                    assert saved['id'] == 'AC-W1' and saved['statement'] == EDITED_TEXT
                    assert item(final, 'AC-REVISION')['selection_status'] == 'deferred'
                    assert item(final, 'AC-APPEND')['selection_status'] == 'candidate'
                    assert item(final, 'AC-APPEND-NEXT')['selection_status'] == 'selected'
                    assert saved['confirmed_revisions'][-1]['candidate_id'] == 'AC-REVISION'
                    assert saved['confirmed_revisions'][-1]['source_refs'] == candidate['source_refs']
                    assert all(ref in saved['source_refs'] for ref in item(initial, 'AC-W1')['source_refs'] + candidate['source_refs'])
                    assert len(final['sources']) == len(before['sources']) + 1
                    manual = final['sources'][-1]
                    assert (store.folder / 'sources' / manual['id']).read_text('utf-8') == EDITED_TEXT
                    assert all(confirmation_current(final, item(final, iid)) for iid in ('REQ-W1', 'REQ-W2'))
                    assert provider.calls == 0 and not report['page_errors']
                    await page.screenshot(path=str(output / '03-delivery.png'))
                    write(output / 'project-after.json', final)
                    report['checks'].append('真实UI一次确认调用真实后端，原AC稳定ID与候选历史/来源保留，人工修改独立留源，自动进入delivery')
                    report.update(status='passed', model_calls=provider.calls, final_revision=final['revision'])
                except BaseException:
                    report.update(status='failed', error=traceback.format_exc())
                    await page.screenshot(path=str(output / 'failure.png'))
                    raise
                finally:
                    write(output / 'result.json', report)
                    await context.close(); await browser.close()
    print(json.dumps(dict(status=report['status'], checks=report['checks'], output=str(output)), ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--static-build', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    asyncio.run(run(parser.parse_args()))
