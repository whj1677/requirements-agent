"""Human-order refinement acceptance and recording, isolated real application.

The primary fixture deliberately DOES NOT already contain either answer in its
adopted clauses. A successful journey must make real, explicitly reviewed clause
revisions. The initial material, seven baseline behavior fields and acceptance
clause are disclosed preconditions, not claimed browser accomplishments.
"""
import copy
import argparse
import asyncio
import json
import mimetypes
import os
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from playwright.async_api import async_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# app.main constructs a module-level application: isolate BEFORE importing it.
IMPORT_SANDBOX = tempfile.TemporaryDirectory(prefix='ra-human-flow-import-')
os.environ['RA_DATA_DIR'] = str((Path(IMPORT_SANDBOX.name) / 'data').resolve())

from app.core import now
from app.product_flow import checkpoint, status
from app.main import create_app
from tests.check_refine_workspace_browser import NoModelProvider, require, wait_store

ORIGIN = 'http://127.0.0.1:8765'
NAME = '合成储能云：告警协作需求核对'
QUESTION_PERMISSION = '两名值班人员能否修改对方的告警处理记录？'
ANSWER_PERMISSION = '不能相互修改，但都能查看。'
BEFORE_PERMISSION = '储能云平台支持两名值班人员协同处理告警。'
AFTER_PERMISSION = '储能云平台支持两名值班人员协同处理告警；不能相互修改对方的处理记录，但都能查看。'
QUESTION_GOAL = '这期交付桌面客户端，还是网页加后端？'
ANSWER_GOAL = '网页加后端，不交付桌面客户端。'
BEFORE_GOAL = '本期交付储能场站告警协作工具，具体交付形态待明确。'
AFTER_GOAL = '本期交付储能场站告警协作工具，采用网页加后端，不交付桌面客户端。'
QUESTION_OPTIONAL = '后续是否提供告警日报导出？'


def seed_human_flow(store, *, grouped=False):
    """Freeze synthetic prior evidence only; never call after browser starts."""
    project = store.create(NAME + ('（独立问题部分确认）' if grouped else ''))
    pid = project['id']
    with store.edit(pid, project['revision'], '冻结有意缺少新回答的合成前置证据') as (p, _):
        p['workflow_mode'] = 'shared-draft'
        p['workspace_draft'] = dict(text='新增独立的储能场站告警协作模块。', entry_mode='direct', saved_at=now())
        p['intake'] = dict(mode='direct', text='新增独立的储能场站告警协作模块。', saved_at=now())
        p['scope_profile'] = dict(scenario='new_module', dependencies='independent')
        p['product_context'] = dict(product='大型储能云平台', module='场站告警协作',
            intent='让两名值班人员在云平台协同查看和处理场站告警。', users='两名场站值班人员。',
            change_scope='新增告警协作模块；记录查看与修改边界、交付形态待本次明确。',
            scope_ids=['REQ-COLLAB'])
        p['context_field_states'] = {field: dict(status='known') for field in ('intent', 'users', 'change_scope')}
        source_text = (BEFORE_PERMISSION + BEFORE_GOAL +
            '值班人员在告警列表选择一条告警，填写处理记录并保存。记录包括处理人、处理时间和处理内容。'
            '保存成功后显示记录；提交失败时提示失败并保留输入。告警详情应显示已保存的处理内容。')
        p['sources'] = [dict(id='SRC-HUMAN', title='冻结合成材料：告警协作初稿', purpose='goal', uri=None,
            sha256='synthetic-human-flow-source-v1', created=now(), version=1, excluded=False,
            parse_status='read', failure_reason='', image_mime=None,
            excerpts=[dict(id='EX-HUMAN', source_id='SRC-HUMAN', source_hash='synthetic-human-flow-source-v1',
                           locator='合成初稿第 1 段', text=source_text, method='synthetic-fixture')])]

        def item(iid, kind, title, statement, related):
            return dict(id=iid, kind=kind, title=title, statement=statement, applies_to='to_be',
                epistemic_status='reported', source_refs=[dict(source_id='SRC-HUMAN', excerpt_id='EX-HUMAN')],
                related_refs=related, selection_status='selected', revision=1, content_version=1,
                change_type='new', behavior_review_required=False)

        req = item('REQ-COLLAB', 'requirement', '场站告警协作', BEFORE_PERMISSION, ['AC-COLLAB'])
        req['behavior'] = dict(actor='两名场站值班人员。', entry='从场站告警列表选择一条告警。',
            flow='打开告警详情，填写处理内容并保存。', data='处理人、处理时间和处理内容。',
            permissions='两名值班人员使用；跨人员查看与修改边界需澄清。',
            result='保存后告警详情显示已保存的处理内容。',
            exceptions='提交失败时显示失败提示并保留输入。')
        p['items'] = [req,
            item('GOAL-DELIVERY', 'goal', '本期交付形态', BEFORE_GOAL, ['REQ-COLLAB']),
            item('AC-COLLAB', 'acceptance', '告警记录保存验收',
                 '值班人员在告警详情填写处理内容并保存，成功后应看到原样保存的处理内容。', ['REQ-COLLAB'])]

        def question(qid, title, ref, blocking=True):
            return dict(id=qid, topic_key=qid.lower(), question=title, why='当前合成材料未明确，需要人工决定。',
                options=[], blocking=blocking, related_refs=[ref], source_refs=[], status='open', answer=None,
                decision_group_id='QG-COLLAB' if grouped and blocking else qid,
                decision_group_title='协作权限与交付形态' if grouped and blocking else title,
                decision_coverage='pending')

        p['questions'] = [question('Q-PERMISSION', QUESTION_PERMISSION, 'REQ-COLLAB'),
                          question('Q-DELIVERY', QUESTION_GOAL, 'GOAL-DELIVERY'),
                          question('Q-OPTIONAL', QUESTION_OPTIONAL, 'REQ-COLLAB', False)]
        p['stage_checks'] = {}
        checkpoint(p, 1, status(p)[0]['content_hash'])
    frozen = store.get(pid)
    assert ANSWER_PERMISSION not in str(frozen['items'])
    assert '网页加后端' not in str(frozen['items'])
    assert frozen['questions'][-1]['blocking'] is False
    assert not any(row['blockers'] for row in status(frozen)[:2])
    return pid, copy.deepcopy(frozen)


OBSERVABLE_ACCEPTANCE = [
    '打开问题同屏看回答与原条款；界面没有要求填写覆盖状态或复制原话依据。',
    '原条款没有新回答时，手动编辑真正改变条款；仅打开/保存原话不算完成。',
    '预览显示修改前后；取消预览或离开并取消不写条款，保持输入。',
    '明确确认后只提交当前选定业务修改；刷新后问题完成，顺序自动继续。',
    'goal 关联在同一条处理链里可见、可修订、可确认，不能要求使用者知道类型。',
    '多独立问题只确认一个时，其他仍待处理；不得凭整段答案自动全部确认。',
    '旧版本预览冲突时不写入、不前進，原输入仍可用，展示明确重新核对动作。',
    '模型失败后在当前项直接改内容可继续；无隐式模型调用，失败不吞原话。',
    '可选问题单独呈现，不计入进入交付的必需剩余事项。',
    '必要缺项真正消除后，显式阶段确认并自动进入交付，不把进交付算正式交付完成。',
]


ANNOTATION = """() => {
 const install=()=>{
  const note=document.createElement('aside');note.id='recording-note';
  note.style.cssText='position:fixed;left:10px;bottom:64px;width:176px;padding:12px;background:#133932;color:white;border:2px solid #60c8a7;border-radius:10px;z-index:2147483647;font:14px/1.65 Microsoft YaHei,sans-serif;pointer-events:none;box-shadow:0 4px 16px #0003;white-space:pre-line';
  document.body.appendChild(note);
  const point=document.createElement('div');point.id='recording-pointer';
  point.style.cssText='position:fixed;width:20px;height:20px;border:3px solid #ec7540;border-radius:50%;background:#ec754022;z-index:2147483646;pointer-events:none;left:-40px;top:-40px';document.body.appendChild(point);
  document.addEventListener('mousemove',e=>{point.style.left=(e.clientX-10)+'px';point.style.top=(e.clientY-10)+'px'});
  document.addEventListener('mousedown',()=>point.style.background='#ec7540aa');
  document.addEventListener('mouseup',()=>point.style.background='#ec754022');
 };if(document.body)install();else document.addEventListener('DOMContentLoaded',install,{once:true});
}"""


class Recorder:
    def __init__(self, page, output, enabled):
        self.page, self.output, self.enabled = page, output, enabled
        self.started = time.monotonic()
        self.chapters = []

    async def note(self, text, seconds=3):
        self.chapters.append(dict(seconds=round(time.monotonic()-self.started, 2), text=text))
        if self.enabled:
            await self.page.evaluate("text=>{document.getElementById('recording-note').textContent=text}",
                '录屏说明 · 非产品界面\n合成储能云项目\n人工路径 · 未调用模型\n\n' + text)
            await self.page.wait_for_timeout(seconds*1000)

    async def click(self, locator):
        if self.enabled:
            await locator.scroll_into_view_if_needed()
            await locator.hover()
            await self.page.wait_for_timeout(350)
        await locator.click()

    async def fill(self, locator, value):
        if self.enabled:
            await locator.scroll_into_view_if_needed()
            await locator.hover()
            await self.page.wait_for_timeout(250)
        await locator.fill(value)
        if self.enabled:
            await self.page.wait_for_timeout(600)

    def save(self):
        (self.output/'chapters.json').write_text(json.dumps(self.chapters, ensure_ascii=False, indent=2), encoding='utf-8')


async def open_isolated_browser(args, scenario):
    """Run supplied UI scenario with real API writes and an optional full video."""
    build, output = args.static_build.resolve(), args.output.resolve()
    require((build/'index.html').is_file(), 'Static build is missing')
    output.mkdir(parents=True, exist_ok=True)
    trace = dict(scope='synthetic_storage_cloud_actual_clause_revision',
        fixture_preconditions=['初稿来源、已采纳需求范围、七项基础行为与通用保存验收已准备',
                               '两个必需问题未答，原条款未表达答案', '一个可选问题未答'],
        checks=[], api_requests=[], page_errors=[], provider_calls=0,
        daily_data_access=False, post_browser_fixture_mutations=0,
        not_run=['真实模型质量', '正式文档审核、基线和交付验收'], static_build=str(build))
    with tempfile.TemporaryDirectory(prefix='ra-human-flow-browser-') as folder:
        provider = NoModelProvider(Path(folder))
        app = create_app(Path(folder)/'data', provider=provider)
        store = app.state.store
        pid, frozen = seed_human_flow(store, grouped=args.case=='grouped')
        trace['project_id'], trace['initial_revision'] = pid, frozen['revision']
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN, timeout=30) as client:
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=True)
                options = dict(viewport=dict(width=1440,height=900))
                if args.record:
                    options.update(record_video_dir=str(output/'raw'),record_video_size=dict(width=1440,height=900))
                context = await browser.new_context(**options)
                if args.record:
                    await context.add_init_script('('+ANNOTATION+')()')

                async def dispatch(route):
                    request = route.request
                    parsed = urlsplit(request.url)
                    require(parsed.netloc=='127.0.0.1:8765', 'External request is forbidden')
                    if parsed.path.startswith('/api/'):
                        require(not (request.method!='GET' and (parsed.path.endswith('/actions') or
                            '/confirm' in parsed.path or '/runs' in parsed.path)), 'Model or formal confirmation is forbidden')
                        headers = {k:v for k,v in request.headers.items() if k.lower() not in
                            ('host','content-length','connection','accept-encoding')}
                        headers['host']='127.0.0.1:8765'
                        response = await client.request(request.method,
                            parsed.path+('?' + parsed.query if parsed.query else ''),
                            content=request.post_data_buffer,headers=headers)
                        row=dict(method=request.method,path=parsed.path,status=response.status_code)
                        if request.method!='GET':
                            row['body']=request.post_data_json
                            row['response']=response.json()
                        trace['api_requests'].append(row)
                        await route.fulfill(status=response.status_code,body=response.content,
                            headers={'content-type':response.headers.get('content-type','application/json')})
                    else:
                        file=(build/(parsed.path.lstrip('/') or 'index.html')).resolve()
                        require(file.is_relative_to(build) and file.is_file(),'Unexpected static path')
                        await route.fulfill(status=200,body=file.read_bytes(),
                            content_type=mimetypes.guess_type(file.name)[0] or 'application/octet-stream')

                await context.route('**/*', dispatch)
                page=await context.new_page()
                page.set_default_timeout(12000)
                page.on('pageerror',lambda error:trace['page_errors'].append(str(error)))
                recording=Recorder(page,output,args.record)
                video=page.video
                try:
                    await page.goto(ORIGIN,wait_until='networkidle')
                    await scenario(page,recording,store,pid,frozen,trace,client)
                    require(not trace['page_errors'],'Browser runtime errors occurred')
                    require(provider.calls==0,'Unexpected model provider invocation')
                    trace['status']='passed'
                    trace['final_revision']=store.get(pid)['revision']
                except Exception as error:
                    trace.update(status='failed',failure=str(error))
                    await page.screenshot(path=str(output/'failure.png'),full_page=True)
                    (output/'failure.html').write_text(await page.content(),encoding='utf-8')
                    raise
                finally:
                    trace['provider_calls']=provider.calls
                    recording.save()
                    (output/'result.json').write_text(json.dumps(trace,ensure_ascii=False,indent=2),encoding='utf-8')
                    await context.close()
                    if video:
                        await video.save_as(str(output/'完善需求连续操作.webm'))
                    await browser.close()
    print(json.dumps(dict(status=trace['status'],checks=trace['checks'],output=str(output)),ensure_ascii=False))


def by_id(project, collection, identity):
    return next(row for row in project[collection] if row['id']==identity)


async def continuous_human_journey(page, rec, store, pid, frozen, trace, client):
    """New answers must revise the old clauses; optional unknown stays unknown."""
    await rec.note('这是隔离合成项目。已准备初稿、需求范围、基础业务说明和保存验收；两条关键答案还没填写，原条款也未表达答案。', 7)
    await rec.click(page.get_by_role('button',name=NAME).first)
    work=page.locator('.connected-workspace:visible')
    await rec.click(work.locator('.connected-steps').get_by_role('button',name='完善需求'))
    current=work.locator('.refine-task-current')
    answer=current.get_by_label('我的回答原文',exact=True)
    confirm=current.locator('.answer-confirmation')
    require(await work.locator('.refine-task-nav > button').count()==2,
            'Required queue must contain exactly two questions, excluding optional unknown')
    await expect(current).to_contain_text(QUESTION_PERMISSION)
    await expect(answer).to_have_value('')
    await rec.note('先处理权限问题。回答“不能相互修改，但都能查看”。页面应带着这条回答，直接核对相关需求内容。', 5)
    await rec.fill(answer,ANSWER_PERMISSION)
    await rec.click(confirm.get_by_role('button',name='直接修改需求',exact=True))
    clause=confirm.locator('.answer-clause-editor')
    await expect(clause).to_have_count(1)
    statement=clause.get_by_role('textbox',name='场站告警协作（修改后）',exact=True)
    await expect(statement).to_have_value(BEFORE_PERMISSION)
    require(await current.get_by_text('覆盖判断',exact=True).count()==0,
            'Default path exposes internal coverage bookkeeping')
    require(await current.get_by_text('回答原文中的依据',exact=True).count()==0,
            'Default path asks the user to copy an already known answer')
    await rec.note('原来只写“两人协作”，没有权限规则。这里直接修改正文；不能直接确认一个没表达答案的旧条款。', 5)
    await rec.fill(statement,AFTER_PERMISSION)
    details=clause.locator('details').filter(has=page.get_by_text('业务说明 · 核对或修改',exact=True))
    await rec.click(details.locator(':scope > summary'))
    await rec.click(details.get_by_role('button',name='修改权限约束',exact=True))
    await rec.fill(details.get_by_role('textbox',name='权限约束（修改后）',exact=True),
                   '两名值班人员不能相互修改对方的处理记录，但都能查看。')
    await rec.note('同时把原业务说明里待澄清的权限更新，避免正文已明确、下面的权限仍是旧内容。其他六项说明保持原样。', 5)
    await rec.click(confirm.get_by_role('button',name='查看修改并确认',exact=True))
    preview=confirm.locator('.answer-change-preview')
    await expect(preview).to_be_visible()
    await expect(preview.locator('.answer-before-after').first).to_be_in_viewport(ratio=.95)
    await expect(work.locator('.refine-task-nav > button[aria-current="step"]')).to_contain_text('回答已存')
    await expect(preview).to_contain_text(BEFORE_PERMISSION)
    await expect(preview).to_contain_text(AFTER_PERMISSION)
    await expect(preview).to_contain_text('两名值班人员不能相互修改对方的处理记录，但都能查看。')
    require(by_id(store.get(pid),'items','REQ-COLLAB')==by_id(frozen,'items','REQ-COLLAB'),
            'Preview wrote the clause before explicit confirmation')
    require(by_id(store.get(pid),'questions','Q-PERMISSION').get('understanding_status')!='applied',
            'Saving an answer was misrepresented as completing it')
    await rec.note('对照修改前后和业务说明，确认意思正确后，只点一次“确认并继续”。预览阶段尚未改动需求条款。', 7)
    await page.screenshot(path=str(rec.output/'01-permission-before-after.png'))
    if rec.enabled:
        changed_permission=preview.locator('.answer-behavior-row').filter(has_text='权限约束')
        await changed_permission.scroll_into_view_if_needed()
        await changed_permission.hover()
        await rec.note('这次同时更新的权限说明：原来仍待澄清；确认后是“不能相互修改，但都能查看”。其他业务说明也在同一次预览中可核对。', 7)
    await rec.click(preview.get_by_role('button',name='确认并继续',exact=True))
    after_permission=await wait_store(store,pid,
        lambda p:by_id(p,'questions','Q-PERMISSION').get('understanding_status')=='applied',
        'Permission answer was not explicitly applied')
    await expect(current).to_contain_text(QUESTION_GOAL)
    await expect(confirm.locator('.refine-question-title')).to_be_in_viewport(ratio=1)
    await expect(answer).to_have_value('')
    changed_req=by_id(after_permission,'items','REQ-COLLAB')
    require(changed_req['statement']==AFTER_PERMISSION,'Confirmation did not actually revise original requirement')
    require(changed_req['behavior']=={**by_id(frozen,'items','REQ-COLLAB')['behavior'],
        'permissions':'两名值班人员不能相互修改对方的处理记录，但都能查看。'},
        'Confirmation rewrote unrelated behavior or left stale permissions')
    require(by_id(after_permission,'items','AC-COLLAB')==by_id(frozen,'items','AC-COLLAB'),
        'Unrelated accepted acceptance was changed')
    require(by_id(after_permission,'items','GOAL-DELIVERY')==by_id(frozen,'items','GOAL-DELIVERY'),
        'Confirming the first answer changed the later goal')
    require(by_id(after_permission,'questions','Q-OPTIONAL')==by_id(frozen,'questions','Q-OPTIONAL'),
        'Required-answer confirmation answered the optional question')
    trace['checks'].append('real_answer_and_clause_permission_revision_single_confirmation_automatically_advances')

    await rec.note('已自动进入交付形态问题。无需返回原列表，也不用自己寻找“目标类条款”。', 5)
    await rec.fill(answer,ANSWER_GOAL)
    await rec.click(confirm.get_by_role('button',name='直接修改需求',exact=True))
    goal=confirm.get_by_role('textbox',name='本期交付形态（修改后）',exact=True)
    await expect(goal).to_have_value(BEFORE_GOAL)
    await rec.fill(goal,AFTER_GOAL)
    await rec.click(confirm.get_by_role('button',name='查看修改并确认',exact=True))
    await expect(preview).to_contain_text(BEFORE_GOAL)
    await expect(preview.locator('.answer-before-after').first).to_be_in_viewport(ratio=.95)
    await expect(preview).to_contain_text(AFTER_GOAL)
    require(by_id(store.get(pid),'items','GOAL-DELIVERY')==by_id(frozen,'items','GOAL-DELIVERY'),
        'Goal preview wrote business content before confirmation')
    await rec.note('确认目标中的交付形态：网页加后端，不交付桌面客户端。目标与普通需求使用相同的核对操作。', 6)
    await page.screenshot(path=str(rec.output/'02-goal-before-after.png'))
    await rec.click(preview.get_by_role('button',name='确认并继续',exact=True))
    ready=await wait_store(store,pid,
        lambda p:by_id(p,'questions','Q-DELIVERY').get('understanding_status')=='applied',
        'Goal answer was not explicitly applied')
    require(by_id(ready,'items','GOAL-DELIVERY')['statement']==AFTER_GOAL,'Goal clause was not really revised')
    require(by_id(ready,'items','REQ-COLLAB')==changed_req,'Goal confirmation rewrote the completed requirement')
    require(not any(row['blockers'] for row in status(ready)[:3]),'Required blockers remain after two actual revisions')
    require(by_id(ready,'questions','Q-OPTIONAL')==by_id(frozen,'questions','Q-OPTIONAL'),
        'Optional unknown was silently settled to allow proceeding')
    trace['checks'].append('goal_clause_uses_same_visible_path_and_actual_revision_without_answering_optional_unknown')

    stage=current.get_by_role('button',name='确认需求核对并进入交付',exact=True)
    await expect(stage).to_be_visible()
    await rec.note('两项必要问题已完成。可选的日报导出仍未回答，不阻止本阶段结束。核对完毕后进入交付准备。', 6)
    await page.screenshot(path=str(rec.output/'03-required-complete-optional-kept.png'))
    await rec.click(stage)
    await expect(work.get_by_role('heading',name='确认交付版本',exact=True)).to_be_visible()
    finished=await wait_store(store,pid,lambda p:all(row['complete'] for row in status(p)[:3]),
                              'Final actual stage confirmations were not persisted')
    require(finished['items']==ready['items'] and finished['questions']==ready['questions'],
        'Stage confirmation modified business decisions')
    require(finished['sources'][0]==frozen['sources'][0],'Original synthetic source was changed')
    require(not finished.get('active_baseline_id'),'Entering delivery created an unauthorized baseline')
    require(len([r for r in trace['api_requests'] if r['method']=='POST' and r['path'].endswith('/decision-review/commit')])==2,
        'Journey unexpectedly repeated or added answer confirmation writes')
    trace['checks'].append('necessary_work_completed_actual_stage_confirmation_auto_delivery_optional_unknown_preserved')
    await rec.note('已自动进入“确认交付”。这证明完善阶段的操作闭环；正式文档审核和正式交付还需另外完成。本次未调用模型。', 8)
    await page.screenshot(path=str(rec.output/'04-automatic-delivery.png'))
    await rec.click(work.locator('.connected-steps').get_by_role('button',name='完善需求'))
    more=work.locator('.refine-task-nav').get_by_text('查看或修改其他内容',exact=True)
    await rec.click(more)
    await rec.click(work.get_by_role('button',name='查看全部问题',exact=True))
    await current.get_by_role('combobox').select_option('Q-DELIVERY')
    await expect(current.get_by_label('我的回答原文',exact=True)).to_have_value(ANSWER_GOAL)
    require(await work.locator('.refine-task-nav > button').count()==0,
            'Reading a completed answer reopened the required queue')
    require(store.get(pid)==finished,'Read-only revisit changed completed business content')
    trace['checks'].append('completed_goal_answer_remains_reachable_from_all_questions_without_reopening_or_writing_it')
    await rec.note('已完成的问题也能回看：展开“查看或修改其他内容”，选择全部问题，再选择交付形态。这里没有修改答案或重新制造待办。', 5)
    await page.screenshot(path=str(rec.output/'05-completed-answer-revisit.png'))
    await rec.click(work.locator('.connected-steps').get_by_role('button',name='确认交付'))
    await expect(work.get_by_role('heading',name='确认交付版本',exact=True)).to_be_visible()
    await rec.note('完善需求阶段已完成。接下来在交付页处理正式文档与交付核对。', 4)


async def dirty_and_conflict_guards(page, rec, store, pid, frozen, trace, client):
    await page.get_by_role('button',name=NAME).first.click()
    work=page.locator('.connected-workspace:visible')
    await work.locator('.connected-steps').get_by_role('button',name='完善需求').click()
    current=work.locator('.refine-task-current')
    answer=current.get_by_label('我的回答原文',exact=True)
    confirm=current.locator('.answer-confirmation')
    await answer.fill(ANSWER_PERMISSION)
    await confirm.get_by_role('button',name='直接修改需求',exact=True).click()
    statement=confirm.get_by_role('textbox',name='场站告警协作（修改后）',exact=True)
    await statement.fill(AFTER_PERMISSION)
    await work.locator('.refine-task-nav > button').nth(1).click()
    dialog=page.get_by_role('dialog',name='未保存的修改',exact=True)
    await expect(dialog).to_be_visible()
    await dialog.get_by_role('button',name='留在当前页',exact=True).click()
    await expect(answer).to_have_value(ANSWER_PERMISSION)
    await expect(statement).to_have_value(AFTER_PERMISSION)
    require(store.get(pid)==frozen,'Attempting then cancelling task navigation wrote business state')
    trace['checks'].append('cancel_unsaved_navigation_preserves_answer_and_clause_edit_without_write')
    await confirm.get_by_role('button',name='让助手整理',exact=True).click()
    warning=confirm.locator('.answer-switch-warning')
    await expect(warning).to_be_visible()
    await warning.get_by_role('button',name='保留手工修改',exact=True).click()
    await expect(statement).to_have_value(AFTER_PERMISSION)
    require(store.get(pid)==frozen,'Cancelling AI mode switch changed business content')
    require(not any(row['path'].endswith('/actions') and row['method']=='POST' for row in trace['api_requests']),
        'Switching answer method silently executed an AI action')
    trace['checks'].append('switch_to_assistant_requires_explicit_draft_disposition_and_never_runs_model_on_mode_click')

    await confirm.get_by_role('button',name='查看修改并确认',exact=True).click()
    preview=confirm.locator('.answer-change-preview')
    await expect(preview).to_be_visible()
    saved=copy.deepcopy(store.get(pid))
    await answer.fill(ANSWER_PERMISSION+' 只针对本期告警记录。')
    await expect(preview).not_to_be_visible()
    require(store.get(pid)==saved,'Changing answer immediately wrote or confirmed old preview')
    await expect(statement).to_have_value(AFTER_PERMISSION)
    trace['checks'].append('editing_answer_invalidates_old_preview_and_preserves_authored_clause_draft')
    await confirm.get_by_role('button',name='查看修改并确认',exact=True).click()
    await expect(preview).to_be_visible()

    # A second actual client changes only an unrelated optional context field.
    # This is a real version conflict; it is not an invented 409 response.
    session=(await client.get('/api/session')).json()
    before_concurrent=store.get(pid)
    response=await client.post('/api/projects/'+pid+'/product-context',
        headers={'Origin':ORIGIN,'X-CSRF-Token':session['csrf']},
        json=dict(expected_revision=before_concurrent['revision'],values={'priority':'另一个编辑者补充的优先级说明'}))
    require(response.status_code==200,'Unable to create actual concurrent edit: '+response.text)
    concurrent=copy.deepcopy(store.get(pid))
    trace['concurrent_api']=dict(path='/api/projects/'+pid+'/product-context',status=response.status_code)
    await preview.get_by_role('button',name='确认并继续',exact=True).click()
    for _ in range(80):
        commits=[row for row in trace['api_requests'] if row['path'].endswith('/decision-review/commit')]
        if commits:
            break
        await asyncio.sleep(.1)
    require(commits and commits[-1]['status']==409,'Stale preview did not meet real version rejection')
    require(store.get(pid)==concurrent,'Conflicted confirmation wrote partial content or overwrote newer data')
    await expect(current).to_contain_text(QUESTION_PERMISSION)
    await expect(answer).to_have_value(ANSWER_PERMISSION+' 只针对本期告警记录。')
    require(by_id(concurrent,'items','REQ-COLLAB')['statement']==BEFORE_PERMISSION,
        'Concurrent race changed the unrelated requirement')
    trace['checks'].append('actual_second_client_revision_conflict_has_zero_confirmation_writes_and_stays_on_current_question')
    await page.screenshot(path=str(rec.output/'guard-real-version-conflict.png'))
    await confirm.get_by_role('button',name='重新核对最新内容',exact=True).click()
    await expect(statement).to_have_value(AFTER_PERMISSION)
    await expect(answer).to_have_value(ANSWER_PERMISSION+' 只针对本期告警记录。')
    details=confirm.locator('.answer-behavior-editor')
    if await details.get_attribute('open') is None:
        await details.locator(':scope > summary').click()
    await details.get_by_role('button',name='修改权限约束',exact=True).click()
    await details.get_by_role('textbox',name='权限约束（修改后）',exact=True).fill('两名值班人员不能相互修改对方的处理记录，但都能查看。')
    await confirm.get_by_role('button',name='查看修改并确认',exact=True).click()
    await expect(preview).to_be_visible()
    await preview.get_by_role('button',name='确认并继续',exact=True).click()
    recovered=await wait_store(store,pid,lambda p:by_id(p,'questions','Q-PERMISSION').get('understanding_status')=='applied',
                               'Retrying a freshly reviewed edit did not complete after conflict')
    require(recovered['product_context']['priority']=='另一个编辑者补充的优先级说明',
        'Recovered confirmation overwrote the concurrent context edit')
    require(by_id(recovered,'items','REQ-COLLAB')['statement']==AFTER_PERMISSION,
        'Recovered confirmation lost the user-authored draft')
    trace['checks'].append('conflict_recheck_keeps_authored_draft_then_new_preview_can_confirm_without_overwriting_concurrent_edit')


async def grouped_partial_confirmation(page, rec, store, pid, frozen, trace, client):
    await page.get_by_role('button',name=frozen['name']).first.click()
    work=page.locator('.connected-workspace:visible')
    await work.locator('.connected-steps').get_by_role('button',name='完善需求').click()
    current=work.locator('.refine-task-current')
    confirm=current.locator('.answer-confirmation')
    await current.get_by_label('我的回答原文',exact=True).fill(ANSWER_PERMISSION)
    await confirm.get_by_role('button',name='直接修改需求',exact=True).click()
    group=confirm.get_by_role('group',name='本次已经回答清楚哪些问题？',exact=True)
    await expect(group.get_by_role('checkbox',name=QUESTION_PERMISSION,exact=True)).not_to_be_checked()
    await expect(group.get_by_role('checkbox',name=QUESTION_GOAL,exact=True)).not_to_be_checked()
    await group.get_by_role('checkbox',name=QUESTION_PERMISSION,exact=True).check()
    await expect(confirm.get_by_role('textbox',name='本期交付形态（修改后）',exact=True)).not_to_be_visible()
    await confirm.get_by_role('textbox',name='场站告警协作（修改后）',exact=True).fill(AFTER_PERMISSION)
    details=confirm.locator('.answer-behavior-editor')
    await details.locator(':scope > summary').click()
    await details.get_by_role('button',name='修改权限约束',exact=True).click()
    await details.get_by_role('textbox',name='权限约束（修改后）',exact=True).fill('两名值班人员不能相互修改对方的处理记录，但都能查看。')
    await confirm.get_by_role('button',name='查看修改并确认',exact=True).click()
    preview=confirm.locator('.answer-change-preview')
    await expect(preview).to_contain_text(QUESTION_GOAL+' · 仍待明确')
    await preview.get_by_role('button',name='确认并继续',exact=True).click()
    partial=await wait_store(store,pid,lambda p:by_id(p,'questions','Q-PERMISSION').get('understanding_status')=='applied',
                            'Explicitly selected independent question was not completed')
    require(by_id(partial,'questions','Q-DELIVERY')['status']=='open' and
            by_id(partial,'questions','Q-DELIVERY')['decision_coverage']=='pending',
            'Answering one question falsely confirmed its unanswered sibling')
    require(by_id(partial,'items','GOAL-DELIVERY')==by_id(frozen,'items','GOAL-DELIVERY'),
            'Partial confirmation altered the unanswered goal')
    require(any(row['blockers'] for row in status(partial)[:3]),'Partial review incorrectly clears the stage gate')
    await expect(current).to_contain_text('协作权限与交付形态')
    await expect(work.get_by_role('heading',name='确认交付版本',exact=True)).not_to_be_visible()
    await expect(current.get_by_role('button',name='确认需求核对并进入交付',exact=True)).not_to_be_visible()
    trace['checks'].append('grouped_partial_confirmation_only_applies_selected_question_keeps_unknown_goal_and_required_stage_blocker')
    await page.screenshot(path=str(rec.output/'partial-keeps-required-question.png'))


async def concurrent_clause_merge(page, rec, store, pid, frozen, trace, client):
    await page.get_by_role('button',name=NAME).first.click()
    work=page.locator('.connected-workspace:visible')
    await work.locator('.connected-steps').get_by_role('button',name='完善需求').click()
    current=work.locator('.refine-task-current')
    confirm=current.locator('.answer-confirmation')
    await current.get_by_label('我的回答原文',exact=True).fill(ANSWER_PERMISSION)
    await confirm.get_by_role('button',name='直接修改需求',exact=True).click()
    statement=confirm.get_by_role('textbox',name='场站告警协作（修改后）',exact=True)
    await statement.fill(AFTER_PERMISSION)
    details=confirm.locator('.answer-behavior-editor')
    await details.locator(':scope > summary').click()
    await details.get_by_role('button',name='修改权限约束',exact=True).click()
    mine='两名值班人员不能相互修改对方的处理记录，但都能查看。'
    other='值班人员修改本人处理记录，其他人员可以查看。'
    new_flow='打开告警详情后填写处理记录，再点击保存。'
    await details.get_by_role('textbox',name='权限约束（修改后）',exact=True).fill(mine)
    await confirm.get_by_role('button',name='查看修改并确认',exact=True).click()
    preview=confirm.locator('.answer-change-preview')
    await expect(preview).to_be_visible()
    session=(await client.get('/api/session')).json()
    concurrent_response=await client.post('/api/projects/'+pid+'/items/REQ-COLLAB/behavior',
        headers={'Origin':ORIGIN,'X-CSRF-Token':session['csrf']},
        json=dict(expected_revision=store.get(pid)['revision'],values={'permissions':other,'flow':new_flow},
                  change_type='new',review_confirmed=False))
    require(concurrent_response.status_code==200,'Actual concurrent clause edit failed: '+concurrent_response.text)
    concurrent=copy.deepcopy(store.get(pid))
    trace['concurrent_api']=dict(path='/api/projects/'+pid+'/items/REQ-COLLAB/behavior',status=200)
    await preview.get_by_role('button',name='确认并继续',exact=True).click()
    await expect(confirm.get_by_role('button',name='重新核对最新内容',exact=True)).to_be_visible()
    require(store.get(pid)==concurrent,'Stale clause confirmation overwrote concurrent values')
    await confirm.get_by_role('button',name='重新核对最新内容',exact=True).click()
    conflict=confirm.locator('.answer-field-conflict')
    await expect(conflict).to_have_count(1)
    await expect(conflict).to_contain_text('权限约束存在不同修改')
    await expect(conflict).to_contain_text(mine)
    await expect(conflict).to_contain_text(other)
    await expect(confirm.get_by_role('button',name='查看修改并确认',exact=True)).to_be_disabled()
    await expect(statement).to_have_value(AFTER_PERMISSION)
    require(store.get(pid)==concurrent,'Reload merged unconfirmed edits into the server')
    await page.screenshot(path=str(rec.output/'same-field-conflict-requires-choice.png'))
    await conflict.get_by_role('button',name='保留我的修改',exact=True).click()
    await confirm.get_by_role('button',name='查看修改并确认',exact=True).click()
    await expect(preview).to_contain_text(new_flow)
    await expect(preview).to_contain_text(mine)
    await preview.get_by_role('button',name='确认并继续',exact=True).click()
    completed=await wait_store(store,pid,lambda p:by_id(p,'questions','Q-PERMISSION').get('understanding_status')=='applied',
        'Explicit conflict choice did not complete on fresh preview')
    actual=by_id(completed,'items','REQ-COLLAB')
    require(actual['behavior']['flow']==new_flow,'Untouched behavior field was overwritten with stale pre-edit value')
    require(actual['behavior']['permissions']==mine,'Explicitly retained human edit was lost')
    require(actual['statement']==AFTER_PERMISSION,'Independent human statement edit was lost')
    trace['checks'].append('actual_concurrent_clause_edit_merges_untouched_latest_field_and_requires_explicit_same_field_choice')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--static-build',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--record',action='store_true')
    parser.add_argument('--case',choices=['journey','guards','grouped','merge'],default='journey')
    args=parser.parse_args()
    scenarios=dict(journey=continuous_human_journey,guards=dirty_and_conflict_guards,
                   grouped=grouped_partial_confirmation,merge=concurrent_clause_merge)
    asyncio.run(open_isolated_browser(args,scenarios[args.case]))
