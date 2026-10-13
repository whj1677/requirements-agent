"""Record the current table using a clone of preserved real model candidates.

No generation calls, fabricated candidate replacement, live daily database
writes, or edits to the original real-model evidence are permitted.
"""
import argparse
import asyncio
import copy
import json
import mimetypes
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import time
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
IMPORT_DATA = tempfile.TemporaryDirectory(prefix='ra-real-table-import-')
os.environ['RA_DATA_DIR'] = str((Path(IMPORT_DATA.name) / 'data').resolve())

import httpx
from playwright.async_api import async_playwright, expect
from app.main import create_app
from app.requirement_confirmation import confirmation_current
from tests.check_refine_workspace_browser import NoModelProvider, require, wait_store
from tests.check_refine_human_flow_browser import ANNOTATION, Recorder

ORIGIN = 'http://127.0.0.1:8765'
RID = 'REQ-ALARM-COLLAB'
OTHER_RID = 'REQ-ALARM-OVERVIEW'
TITLE = '告警处理记录协作'


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def item(project, identity):
    return next(row for row in project['items'] if row['id'] == identity)


class RealRecorder(Recorder):
    async def note(self, text, seconds=4):
        self.chapters.append(dict(seconds=round(time.monotonic()-self.started, 2), text=text))
        if self.enabled:
            await self.page.evaluate("text=>{document.getElementById('recording-note').textContent=text}",
                '录屏说明 · 非产品界面\n真实模型建议快照副本\n本段无新增请求\n合成储能云资料\n\n' + text)
            await self.page.wait_for_timeout(seconds*1000)


async def journey(page, rec, store, pid, frozen, trace):
    candidate = next(row for row in frozen['items'] if row.get('target_item_id') == RID
                     and row['selection_status'] == 'candidate')
    acceptance = next(row for row in frozen['items'] if row['kind'] == 'acceptance'
                      and RID in row.get('related_refs',[]) and row['selection_status'] == 'candidate')
    old_phrase = '打开对方创建的记录时，不提供修改本人非创建记录的编辑能力。'
    new_phrase = '打开对方创建的记录时，不能修改对方创建的记录。'
    require(old_phrase in acceptance['statement'], 'Recorded real candidate differs; inspect it before choosing a manual edit')
    revised_acceptance = acceptance['statement'].replace(old_phrase, new_phrase)
    await rec.click(page.get_by_role('button', name=frozen['name']).first)
    work = page.locator('.connected-workspace:visible')
    await rec.click(work.locator('.connected-steps').get_by_role('button', name='完善需求'))
    table = work.get_by_role('table', name='需求确认表', exact=True)
    await expect(table).to_be_visible()
    first = table.locator('tr[data-requirement-id="' + RID + '"]')
    other = table.locator('tr[data-requirement-id="' + OTHER_RID + '"]')
    await expect(first).to_contain_text('AI 修订建议')
    await expect(first).to_contain_text(candidate['statement'])
    await expect(first).to_contain_text(acceptance['statement'])
    await expect(other).to_contain_text('已确认')
    require(store.get(pid) == frozen, 'Opening the real snapshot wrote business data')
    await rec.note('这是前面真实模型生成的未采纳建议。另一个已确认需求作为对照；本段只核对和修改，不再调用模型。', 6)
    await page.screenshot(path=str(rec.output / '01-real-candidates-table.png'))
    trace['checks'].append('actual_model_requirement_and_acceptance_are_displayed_unadopted_with_other_row_confirmed')

    await rec.click(first.get_by_role('button', name='查看与确认', exact=True))
    editor = work.get_by_role('region', name=TITLE + ' 需求确认', exact=True)
    statement = editor.get_by_role('textbox', name=TITLE + ' 需求内容', exact=True)
    ac_input = editor.get_by_role('textbox', name=TITLE + ' 验收建议 1', exact=True)
    await expect(statement).to_have_value(candidate['statement'])
    await expect(ac_input).to_have_value(acceptance['statement'])
    await rec.note('AI 已起草正文和验收。这里的验收末句“本人非创建记录”不顺畅，需要人工改清楚。', 6)
    await page.screenshot(path=str(rec.output / '02-real-acceptance-before-edit.png'))
    await rec.fill(ac_input, revised_acceptance)
    await rec.note('人工修改为“不能修改对方创建的记录”，对应原来已保存的权限回答；没有规定隐藏或禁用哪种按钮。', 7)
    await expect(statement).to_have_value(candidate['statement'])
    require(store.get(pid) == frozen, 'Editing local text silently adopted AI content')
    trace['checks'].append('human_corrects_real_ai_acceptance_wording_in_place_without_automatic_adoption_or_regeneration')

    await rec.click(editor.get_by_role('button', name='查看确认内容', exact=True))
    preview = editor.get_by_role('region', name='本条确认预览', exact=True)
    await expect(preview).to_be_visible()
    await expect(preview.get_by_role('heading', name='本次确认内容', exact=True)).to_be_in_viewport(ratio=1)
    await expect(preview).to_contain_text(item(frozen, RID)['statement'])
    await expect(preview).to_contain_text(candidate['statement'])
    await rec.note('先核对原需求与确认后的完整正文。已知入口、流程、权限由 AI 根据资料起草；异常仍明确标为未知。', 7)
    await page.screenshot(path=str(rec.output / '03-requirement-full-preview.png'))
    await preview.get_by_role('heading', name='同时确认的验收', exact=True).scroll_into_view_if_needed()
    await expect(preview).to_contain_text(revised_acceptance)
    await expect(preview.locator('del')).to_contain_text(acceptance['statement'])
    await rec.note('再核对验收：划线部分是原 AI 文案，下方是人工修改后本次要确认的内容。改动没有被隐藏。', 7)
    await page.screenshot(path=str(rec.output / '04-acceptance-manual-change-preview.png'))
    question = preview.locator('.table-question-results')
    await question.scroll_into_view_if_needed()
    await expect(question).to_contain_text('只能修改本人创建的告警处理记录。')
    await expect(preview).to_contain_text('本次同时确认')
    require(store.get(pid) == frozen, 'Preview committed the requirement or acceptance')
    await rec.note('已保存的权限回答也列在本次确认中。看完本条正文、业务说明、验收和回答后，只需确认一次。', 6)
    await page.screenshot(path=str(rec.output / '05-answer-in-whole-row-preview.png'))
    trace['checks'].append('complete_requirement_acceptance_diff_and_saved_answer_visible_together_before_one_explicit_confirmation')

    await rec.click(preview.get_by_role('button', name='确认这条需求', exact=True))
    saved = await wait_store(store,pid,lambda p:confirmation_current(p,item(p,RID)), 'Real candidate row did not confirm')
    await expect(first).to_contain_text('已确认')
    await expect(editor).not_to_be_visible()
    await expect(other).to_contain_text('已确认')
    # Success must be visible without the test scrolling on the user's behalf.
    await expect(first.get_by_role('rowheader')).to_be_in_viewport(ratio=.8)
    await expect(first.locator('.table-state.confirmed')).to_be_in_viewport(ratio=1)
    require(item(saved,RID)['statement'] == candidate['statement'], 'Confirmed different requirement content')
    require(item(saved,acceptance['id'])['statement'] == revised_acceptance, 'Visible manual acceptance edit not persisted')
    require(item(saved,acceptance['id'])['selection_status'] == 'selected', 'Acceptance not confirmed with requirement')
    require(item(saved,candidate['id'])['selection_status'] == 'deferred', 'Applied revision remains pending')
    require(item(saved,OTHER_RID) == item(frozen,OTHER_RID) and item(saved,'AC-OVERVIEW') == item(frozen,'AC-OVERVIEW'), 'Unchanged control row mutated')
    require(confirmation_current(saved,item(saved,OTHER_RID)), 'Control row lost confirmation')
    answer = next(q for q in saved['questions'] if q['id'] == 'Q-MODIFY-OWNER')
    require(answer.get('understanding_status') == 'applied', 'Displayed answer was not confirmed with row')
    commits = [row for row in trace['api_requests'] if row['path'].endswith('/requirement-confirmation/commit')]
    require(len(commits) == 1 and commits[0]['status'] == 200, 'Whole row required duplicate confirmation')
    trace['checks'].append('single_ui_commit_confirms_real_candidate_and_human_edited_acceptance_while_preserving_other_confirmed_row')
    trace['human_acceptance_edit'] = dict(before=acceptance['statement'], after=revised_acceptance,
                                         reason='Clarified awkward model wording using the saved permission answer')
    await rec.note('当前行已确认，另一个已确认需求保持原状。原真实模型证据不变；本段只在副本中验证人工核对，不代表正式交付完成。', 7)
    await page.screenshot(path=str(rec.output / '06-real-row-confirmed-control-preserved.png'))
    write(rec.output / 'project-after.json', saved)


async def run(args):
    session, build, output = args.session.resolve(), args.static_build.resolve(), args.output.resolve()
    require(session.is_relative_to(ROOT/'evidence') and output.is_relative_to(ROOT/'evidence'), 'Evidence path outside repository')
    require((build/'index.html').is_file(), 'Validated static build missing')
    require(not output.exists(), 'Never overwrite earlier recording evidence')
    output.mkdir(parents=True)
    source, clone = session/'data', output/'data'
    clone.mkdir()
    with sqlite3.connect((source/'requirements.sqlite3').as_uri()+'?mode=ro',uri=True) as original:
        with sqlite3.connect(clone/'requirements.sqlite3') as target:
            original.backup(target)
    for path in source.iterdir():
        if path.name.startswith('requirements.sqlite3'):
            continue
        if path.is_dir():
            shutil.copytree(path,clone/path.name)
        else:
            shutil.copy2(path,clone/path.name)
    provider = NoModelProvider(output)
    app = create_app(clone,provider=provider)
    store = app.state.store
    pid = (session/'project-id.txt').read_text('utf-8').strip()
    frozen = copy.deepcopy(store.get(pid))
    write(output/'project-before.json',frozen)
    trace = dict(scope='actual_real_model_candidates_on_isolated_copy_with_visible_human_correction',
        source_session=str(session),project_id=pid,static_build=str(build),checks=[],api_requests=[],page_errors=[],
        provider_requests_during_recording=0,daily_data_access=False,post_browser_fixture_mutations=0,
        initial_revision=frozen['revision'],prior_real_task_status='partial',
        prior_provider_requests=json.loads((session/'outcome.json').read_text('utf-8'))['provider_requests'])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=ORIGIN,timeout=30) as client:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            context = await browser.new_context(viewport=dict(width=1440,height=900),
                record_video_dir=str(output/'raw'),record_video_size=dict(width=1440,height=900))
            await context.add_init_script('('+ANNOTATION+')()')

            async def dispatch(route):
                req=route.request
                parsed=urlsplit(req.url)
                require(parsed.netloc=='127.0.0.1:8765','External browser request forbidden')
                if parsed.path.startswith('/api/'):
                    require(not(req.method!='GET' and (parsed.path.endswith('/actions') or '/runs' in parsed.path
                        or '/confirm' in parsed.path or parsed.path.endswith('/recommend'))),'Model/formal confirmation forbidden')
                    headers={k:v for k,v in req.headers.items() if k.lower() not in ('host','content-length','connection','accept-encoding')}
                    headers['host']='127.0.0.1:8765'
                    response=await client.request(req.method,parsed.path+('?' + parsed.query if parsed.query else ''),
                                                  content=req.post_data_buffer,headers=headers)
                    row=dict(method=req.method,path=parsed.path,status=response.status_code)
                    if req.method!='GET':
                        row.update(body=req.post_data_json,response=response.json())
                    trace['api_requests'].append(row)
                    await route.fulfill(status=response.status_code,body=response.content,
                                        headers={'content-type':response.headers.get('content-type','application/json')})
                else:
                    path=(build/(parsed.path.lstrip('/') or 'index.html')).resolve()
                    require(path.is_relative_to(build) and path.is_file(),'Unexpected static asset request')
                    await route.fulfill(status=200,body=path.read_bytes(),content_type=mimetypes.guess_type(path.name)[0] or 'application/octet-stream')

            await context.route('**/*',dispatch)
            page=await context.new_page()
            page.set_default_timeout(15000)
            page.on('pageerror',lambda error:trace['page_errors'].append(str(error)))
            video=page.video
            rec=RealRecorder(page,output,True)
            try:
                await page.goto(ORIGIN,wait_until='networkidle')
                await journey(page,rec,store,pid,frozen,trace)
                require(provider.calls==0 and not trace['page_errors'],'Unexpected model invocation or browser error')
                trace.update(status='passed',final_revision=store.get(pid)['revision'])
            except Exception as error:
                trace.update(status='failed',failure=str(error))
                await page.screenshot(path=str(output/'failure.png'),full_page=True)
                (output/'failure.html').write_text(await page.content(),encoding='utf-8')
                raise
            finally:
                with sqlite3.connect((source/'requirements.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
                    original=json.loads(db.execute('SELECT payload FROM projects WHERE id=?',(pid,)).fetchone()[0])
                trace.update(source_snapshot_unchanged=original==frozen,provider_requests_during_recording=provider.calls)
                rec.save()
                write(output/'result.json',trace)
                await context.close()
                await video.save_as(str(output/'真实AI表格确认操作.webm'))
                await browser.close()
    require(trace['source_snapshot_unchanged'],'Original real model snapshot changed')
    print(json.dumps(dict(status=trace['status'],checks=trace['checks'],output=str(output)),ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session',required=True,type=Path)
    parser.add_argument('--static-build',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    asyncio.run(run(parser.parse_args()))
