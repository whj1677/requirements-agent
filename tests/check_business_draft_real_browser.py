"""Record human review and source-backed correction of the actual whole business draft.

The input must already contain a valid real-model acceptance candidate. A
requirement revision is optional: otherwise the original text is retained.
Preview/commit affect only the copied project.
The film demonstrates UI confirmation, not the user's business acceptance or
completion of the whole project. No unknown answer or classification is filled.
"""
import argparse
import asyncio
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
IMPORT_DATA = tempfile.TemporaryDirectory(prefix='ra-tariff-candidate-import-')
os.environ['RA_DATA_DIR'] = str(Path(IMPORT_DATA.name) / 'data')

import httpx
from playwright.async_api import async_playwright, expect
from app.main import create_app
from app.requirement_confirmation import confirmation_current, confirmation_state
from tests.check_tariff_entry_acceptance import copy_database
from tests.check_refine_workspace_browser import NoModelProvider, wait_store
from tests.check_refine_human_flow_browser import ANNOTATION, Recorder

ORIGIN = 'http://127.0.0.1:8765'
FIELDS = ('actor', 'entry', 'flow', 'data', 'permissions', 'result', 'exceptions')


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def item(project, identity):
    return next(row for row in project['items'] if row['id'] == identity)


class CandidateRecorder(Recorder):
    async def note(self, text, seconds=4):
        self.chapters.append(dict(seconds=round(time.monotonic()-self.started, 2), text=text))
        await self.page.evaluate("text=>{document.getElementById('recording-note').textContent=text}",
            '录屏说明 · 非产品界面\n真实电价管理项目副本\n真实 AI 整包草稿\n本段 0 新增模型请求\n非用户正式业务验收\n'
            + (getattr(self, 'limitation', '') + '\n' if getattr(self, 'limitation', '') else '') + '\n' + text)
        await self.page.wait_for_timeout(seconds * 1000)


async def run(args):
    session, build, output = args.session.resolve(), args.static_build.resolve(), args.output.resolve()
    assert session.is_relative_to(ROOT/'evidence') and output.is_relative_to(ROOT/'evidence')
    assert (build/'index.html').is_file() and not output.exists(), 'Use a validated build and a fresh evidence directory'
    outcome = json.loads((session/'outcome.json').read_text('utf-8'))
    assert outcome.get('task_status') in ('completed', 'done', 'partial', 'succeeded'), 'No successful candidate result; do not fabricate one'
    assert outcome.get('acceptance_candidate_ids'), 'A real acceptance candidate is required'
    pid, rid = outcome['project_id'], outcome['requirement_id']
    source_db = session/'data/requirements.sqlite3'
    source_hash = hashlib.sha256(source_db.read_bytes()).hexdigest()
    # Validate candidate preconditions before creating any video or copy.
    with sqlite3.connect(source_db.as_uri()+'?mode=ro', uri=True) as db:
        frozen = json.loads(db.execute('SELECT payload FROM projects WHERE id=?', (pid,)).fetchone()[0])
    original = item(frozen, rid)
    revisions = [row for row in frozen['items'] if row.get('target_item_id') == rid
                 and row['kind'] == 'requirement' and row['selection_status'] == 'candidate']
    candidate = revisions[-1] if revisions else None
    offered = candidate or original
    acs = [row for row in frozen['items'] if row['kind'] == 'acceptance' and row.get('applies_to') == 'to_be'
           and not row.get('target_item_id') and row.get('selection_status') in ('candidate', 'selected')
           and (rid in row.get('related_refs', []) or row['id'] in original.get('related_refs', []))]
    assert (not candidate or candidate['id'] in outcome['requirement_candidate_ids']) and acs
    assert frozen['name'] == '电价管理' and rid in frozen['product_context']['scope_ids']
    title = original['title']
    behavior = {**dict.fromkeys(FIELDS, ''), **original.get('behavior', {}), **offered.get('behavior', {})}
    change_type = offered.get('change_type') or original.get('change_type') or 'unspecified'
    # These corrections are frozen by independent source review, not injected
    # into the model response/database. They must be typed through the UI.
    corrections = {
        'actor': '业主（业主账号）；中车运维账号也是模板创建来源，运维自身操作权限未说明。',
        'permissions': '查看电价模板时，业主只能看到自己建立的模板和中车运维账号建立的模板。其他角色可见范围及编辑、复制、删除权限未说明。',
        'flow': '进入电价模板库列表页，按筛选项查询和查看模板。点击新增模板、编辑或复制，打开新增/编辑框（A0）；点击批量配置，打开园区列表弹出框（B0）。本行只说明入口，不展开弹窗内部字段和保存规则。',
    }
    word = next(source for source in frozen['sources'] if source['id']=='SRC-833469838c874098')
    source_text = {excerpt['id']:excerpt['text'] for excerpt in word['excerpts']}
    assert '业主只能看到自己建立' in source_text['EX-b19d35cb231947a7']
    assert '展示A0弹出框' in source_text['EX-4bf3f2dccd7f4deb'] and '展示B0弹出框' in source_text['EX-4bf3f2dccd7f4deb']
    expected_behavior = {**behavior, **corrections}
    unknown_fields = [key for key in FIELDS if not str(expected_behavior.get(key, '')).strip()]
    unanswered = [q for q in frozen['questions'] if not str(q.get('answer') or '').strip()]
    output.mkdir(parents=True)
    copy_database(session/'data', output/'data')
    provider = NoModelProvider(output)
    app = create_app(output/'data', provider=provider)
    store = app.state.store
    assert store.get(pid) == frozen
    write(output/'project-before.json', frozen)
    trace = dict(status='running', scope='one_actual_tariff_candidate_confirmation_on_isolated_copy',
        source_session=str(session), static_build=str(build), project_id=pid, requirement_id=rid,
        candidate_id=candidate['id'] if candidate else None, acceptance_ids=[a['id'] for a in acs],
        requirement_content_origin='AI修订建议' if candidate else '正文沿用原条款，AI新增验收',
        known_limitations=args.limitation, human_corrections=corrections, original_ai_behavior=behavior,
        correction_sources=['EX-b19d35cb231947a7','EX-4bf3f2dccd7f4deb'], commit_authorized=args.commit_approved,
        initial_revision=frozen['revision'], prior_real_task_status=outcome['task_status'],
        prior_provider_requests=outcome['provider_requests'], prior_cost=outcome.get('cost'),
        unknown_fields_kept=unknown_fields, displayed_change_type=change_type,
        checks=[], api_requests=[], page_errors=[], real_model_calls=0, daily_data_access=False,
        user_business_acceptance='NOT_RUN', formal_delivery='NOT_RUN', entire_stage_completion='NOT_RUN')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN, timeout=30) as client:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            context = await browser.new_context(viewport=dict(width=1440, height=900),
                record_video_dir=str(output/'raw'), record_video_size=dict(width=1440, height=900))
            await context.add_init_script('('+ANNOTATION+')()')
            async def dispatch(route):
                request = route.request
                url = urlsplit(request.url)
                assert url.netloc == '127.0.0.1:8765', 'External browser requests are forbidden'
                if url.path.startswith('/api/'):
                    permitted = ('/requirement-confirmation/preview', '/requirement-confirmation/commit', '/decision-review')
                    assert request.method == 'GET' or url.path.endswith(permitted), 'Unexpected mutation or model start: '+url.path
                    headers = {k:v for k,v in request.headers.items() if k.lower() not in ('host','content-length','connection','accept-encoding')}
                    headers['host'] = '127.0.0.1:8765'
                    response = await client.request(request.method, url.path+('?' + url.query if url.query else ''),
                        headers=headers, content=request.post_data_buffer)
                    request_row = dict(method=request.method, path=url.path, status=response.status_code)
                    if request.post_data:
                        request_row.update(body=request.post_data_json, response=response.json())
                    trace['api_requests'].append(request_row)
                    await route.fulfill(status=response.status_code, body=response.content,
                        content_type=response.headers.get('content-type', 'application/json'))
                else:
                    path = (build/(url.path.lstrip('/') or 'index.html')).resolve()
                    assert path.is_relative_to(build) and path.is_file()
                    await route.fulfill(status=200, body=path.read_bytes(),
                        content_type=mimetypes.guess_type(path.name)[0] or 'application/octet-stream')
            await context.route('**/*', dispatch)
            page = await context.new_page()
            page.set_default_timeout(15000)
            page.on('pageerror', lambda error:trace['page_errors'].append(str(error)))
            rec = CandidateRecorder(page, output, True)
            rec.limitation = args.limitation
            try:
                await page.goto(ORIGIN, wait_until='networkidle')
                async with page.expect_response(lambda response:response.url.endswith('/workspace-draft') and response.request.method == 'GET'):
                    await rec.click(page.locator('aside.sidebar button[title="电价管理"]'))
                work = page.locator('.connected-workspace:visible')
                await rec.click(work.get_by_role('button', name='不使用 AI，直接完善', exact=True))
                table = work.get_by_role('table', name='需求确认表', exact=True)
                await expect(table).to_be_visible()
                row = table.locator('tr[data-requirement-id="'+rid+'"]')
                await expect(row).to_contain_text('AI 修订建议' if candidate else '当前草稿')
                await expect(row).to_contain_text(offered['statement'])
                for ac in acs:
                    await expect(row).to_contain_text(ac['statement'])
                assert store.get(pid) == frozen
                await rec.note(('1. 这是新一次真实模型产出的建议' if candidate else '1. 正文沿用原条款，AI 新增验收')+'\n原需求尚未被改写。这里只使用已有结果，不再次调用 AI。', 5)
                await page.screenshot(path=str(output/'01-real-candidates.png'))
                if await row.get_by_role('button', name='查看与确认', exact=True).count():
                    await rec.click(row.get_by_role('button', name='查看与确认', exact=True))
                editor = work.get_by_role('region', name=title+' 需求确认', exact=True)
                await expect(editor.get_by_role('textbox', name=title+' 需求内容', exact=True)).to_have_value(offered['statement'])
                await expect(editor.get_by_role('combobox', name=title+' 本次改动', exact=True)).to_have_value(change_type)
                for number, ac in enumerate(acs, 1):
                    await expect(editor.get_by_role('textbox', name=f'{title} 验收建议 {number}', exact=True)).to_have_value(ac['statement'])
                trace['checks'].append(('真实需求修订和验收候选原样呈现' if candidate else '正文沿用原条款，真实AI新增验收候选原样呈现')+'；打开与浏览不提前采纳')
                await rec.note('2. 这份 AI 草稿还有遗漏\n业主可见范围及 A0/B0 入口已有资料，当前建议没有整理到本行。先查原文再修正。', 5)
                await page.screenshot(path=str(output/'02-real-editor.png'))
                await rec.click(editor.get_by_role('button', name='查看依据', exact=True))
                attachment = page.get_by_role('dialog', name='附件内容', exact=True)
                await expect(attachment).to_be_visible()
                source_ids = {ref['source_id'] for ref in offered['source_refs']}
                source_titles = [s['title'] for s in frozen['sources'] if s['id'] in source_ids]
                attachment_text = await attachment.inner_text()
                assert any(source_title in attachment_text for source_title in source_titles), 'Source viewer does not match real candidate provenance'
                await expect(attachment.get_by_role('region', name='已读取内容')).to_be_visible()
                read_content = attachment.get_by_role('region', name='已读取内容')
                await expect(read_content).to_contain_text('业主只能看到自己建立')
                await expect(read_content).to_contain_text('展示A0弹出框')
                await expect(read_content).to_contain_text('展示B0弹出框')
                owner_excerpt = read_content.locator('#excerpt-EX-b19d35cb231947a7')
                await owner_excerpt.scroll_into_view_if_needed()
                await expect(owner_excerpt).to_be_in_viewport(ratio=.95)
                await rec.note('3. 对照 Word 业主可见范围原文\n这里已明确：业主仅看到自己和中车运维账号建立的模板。运维自身权限没有说明。', 5)
                await page.screenshot(path=str(output/'03a-owner-source-evidence.png'))
                entry_excerpt = read_content.locator('#excerpt-EX-4bf3f2dccd7f4deb')
                await entry_excerpt.scroll_into_view_if_needed()
                await expect(entry_excerpt).to_be_in_viewport(ratio=.95)
                await rec.note('3. 对照 Word 入口结果原文\nB/D：新增、编辑、复制打开 A0；E：批量配置打开 B0。本条不展开弹窗内部业务。', 5)
                await page.screenshot(path=str(output/'03-source-evidence.png'))
                await rec.click(attachment.get_by_role('button', name='关闭附件', exact=True))
                await rec.click(page.get_by_role('dialog', name='项目资料', exact=True).get_by_role('button', name='关闭项目资料', exact=True))
                await rec.click(editor.locator('details.table-behavior > summary'))
                labels = {'actor':'谁操作','permissions':'权限约束','flow':'主要流程'}
                for field, text in corrections.items():
                    box = editor.get_by_role('textbox', name=title+' '+labels[field], exact=True)
                    await box.scroll_into_view_if_needed()
                    await box.fill(text)
                    await expect(box).to_have_value(text)
                    await rec.note('4. 人工补齐原文已经说明的'+labels[field]+'\n这是本次人工纠正，原始 AI 建议没有被改写。未知权限/异常/改动性质继续待定。', 3)
                for field in unknown_fields:
                    assert expected_behavior[field] == behavior[field]
                await page.screenshot(path=str(output/'04-human-corrections.png'))
                assert store.get(pid) == frozen
                trace['checks'].append('人工经界面补入Word已明确业主范围和A0/B0，原候选及服务器条款未写入')
                await rec.click(editor.get_by_role('button', name='查看确认内容', exact=True))
                preview = editor.get_by_role('region', name='本条确认预览', exact=True)
                await expect(preview).to_be_visible()
                # A subpixel border intersection can report ~0.978 while the
                # complete title is visibly readable. Do not scroll for it.
                await expect(preview.get_by_role('heading', name='本次确认内容', exact=True)).to_be_in_viewport(ratio=.95)
                await expect(preview).to_contain_text(original['statement'])
                await expect(preview).to_contain_text(offered['statement'])
                for ac in acs:
                    await expect(preview).to_contain_text(ac['statement'])
                if unknown_fields:
                    await expect(preview).to_contain_text('未说明；不作为已确认的业务条件')
                if change_type == 'unspecified':
                    await expect(preview).to_contain_text('本次改动：尚未确定')
                preview_request = next(r for r in reversed(trace['api_requests']) if r['path'].endswith('/requirement-confirmation/preview'))
                assert preview_request['status'] == 200 and preview_request['body']['change_type'] == change_type
                assert preview_request['body']['behavior'] == expected_behavior
                assert store.get(pid) == frozen
                trace['checks'].append('预览完整正文、业务说明与验收且不写条款；未知分类/字段未被自动补齐')
                await rec.note('5. 预览真实 AI 建议与人工修改后的完整内容\n空白异常仍是未说明，改动性质仍待确定。确认只作用于这条内容。', 5)
                await page.screenshot(path=str(output/'04-whole-row-preview.png'))
                await preview.get_by_role('heading', name='同时确认的验收', exact=True).scroll_into_view_if_needed()
                await rec.note('6. 两条真实验收均保留\n这里只核对筛选和展示列，不把截图50条/10条当固定规则，不替用户回答未知问题。', 5)
                await page.screenshot(path=str(output/'05-acceptance-preview.png'))
                assert args.commit_approved, 'Preview recorded; explicit review authority is required for copied project commit'
                await rec.click(preview.get_by_role('button', name='确认这条需求', exact=True))
                saved = await wait_store(store, pid, lambda project:confirmation_current(project, item(project, rid)), 'Candidate package not confirmed')
                await expect(editor).not_to_be_visible()
                await expect(row.get_by_role('rowheader')).to_be_in_viewport(ratio=.8)
                await expect(row.locator('.table-state')).to_be_in_viewport(ratio=1)
                await expect(row).to_contain_text('已确认内容')
                assert item(saved, rid)['statement'] == offered['statement']
                assert item(saved, rid)['behavior'] == expected_behavior and item(saved, rid)['change_type'] == change_type
                if candidate:
                    assert item(saved, candidate['id'])['selection_status'] == 'deferred'
                for ac in acs:
                    assert item(saved, ac['id'])['statement'] == ac['statement'] and item(saved, ac['id'])['selection_status'] == 'selected'
                changed_ids = {rid, *[ac['id'] for ac in acs]}
                if candidate:
                    changed_ids.add(candidate['id'])
                assert all(item(saved, old['id']) == old for old in frozen['items'] if old['id'] not in changed_ids)
                assert all(next(q for q in saved['questions'] if q['id'] == old['id']) == old for old in unanswered)
                assert saved.get('stage_checks') == frozen.get('stage_checks'), 'Single row confirmation advanced the entire stage'
                current_state = next(s for s in confirmation_state(saved)['rows'] if s['requirement_id'] == rid)
                trace['unresolved_decisions_after_confirmation'] = current_state['missing_decisions']
                if change_type == 'unspecified':
                    await expect(row.locator('.table-state')).to_have_text('改动性质待确认')
                elif current_state['missing_decisions']:
                    await expect(row.locator('.table-state')).to_have_text('有待定决定')
                else:
                    await expect(row.locator('.table-state')).to_have_text('已确认')
                commits = [request for request in trace['api_requests'] if request['path'].endswith('/requirement-confirmation/commit')]
                assert len(commits) == 1 and commits[0]['status'] == 200
                assert provider.calls == 0 and not trace['page_errors'] and not any(r['status'] >= 400 for r in trace['api_requests'])
                trace['checks'].append('一次显式确认只采纳当前正文和验收并回到当前行；其他需求与未答问题保留，整阶段未推进')
                await rec.note('7. 人工核对的本条内容已记录，回到原行\n状态仍显示改动性质待确认；其他行与未答问题保留。整项目尚未完成，非用户正式验收。', 6)
                await page.screenshot(path=str(output/'06-back-to-table.png'))
                write(output/'project-after.json', saved)
                trace.update(status='passed', final_revision=saved['revision'])
            except Exception as error:
                trace.update(status='failed', failure=str(error))
                await page.screenshot(path=str(output/'failure.png'), full_page=True)
                write(output/'failure-body.json', {'text':await page.locator('body').inner_text()})
            finally:
                trace.update(source_database_sha256_unchanged=hashlib.sha256(source_db.read_bytes()).hexdigest() == source_hash,
                    real_model_calls=provider.calls)
                rec.save()
                write(output/'result.json', trace)
                video = page.video
                await context.close()
                await video.save_as(str(output/'真实电价AI建议与人工确认.webm'))
                await browser.close()
    print(json.dumps({key:trace.get(key) for key in ('status','checks','failure','source_database_sha256_unchanged','real_model_calls')}, ensure_ascii=False, indent=2))
    if trace['status'] != 'passed':
        raise SystemExit(1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session', type=Path, required=True)
    parser.add_argument('--static-build', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--commit-approved', action='store_true', help='Parent reviewed the exact copied-project manual corrections before recording')
    parser.add_argument('--limitation', default='', help='Visible limitation carried throughout the recording and result')
    asyncio.run(run(parser.parse_args()))
