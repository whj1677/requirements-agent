"""Isolated entry-path regression for the requirement table.

Uses the current single-table navigation and assistant-first restoration.
Historical per-field confirmation is replaced by explicit atomic table review. Synthetic tariff requirements reproduce a
selected legacy row with unknown change type and missing business/acceptance
text. Only the Provider transport is frozen; plans, grants, revisions, preview
and commit run through the actual application. No daily data or model network.
"""
import argparse
import asyncio
import copy
import json
import mimetypes
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from playwright.async_api import async_playwright, expect

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
IMPORT_SANDBOX=tempfile.TemporaryDirectory(prefix='ra-entry-import-')
os.environ['RA_DATA_DIR']=str(Path(IMPORT_SANDBOX.name)/'data')
from app.core import now
from app.main import create_app
from app.product_flow import checkpoint, status
from app.provider import DEFAULT, Provider, origin
from app.requirement_confirmation import confirmation_current
from tests.check_refine_human_flow_browser import ANNOTATION, Recorder

ORIGIN='http://127.0.0.1:8765'
NAME='合成储能云：电价需求入口'
RID='REQ-TARIFF-LIST'
OTHER='REQ-TARIFF-HISTORY'
TITLE='电价模板列表'
ACID='AC-TARIFF-LIST'
ACTITLE='模板列表显示验收'
BEFORE='云平台支持查看电价模板列表。'
AFTER='电价管理员从云平台电价管理进入电价模板列表，查看模板名称和所属地区。'
AC='电价管理员打开电价模板列表后，页面显示已保存模板的名称和所属地区。'
CHANGE='本期新增独立的电价模板列表及历史版本查询。'
QUESTION='电价模板列表与历史版本查询是否使用同一个地区筛选条件？'
SOURCE=('合成入口验收资料，不是行业规则。大型储能云平台本期新增独立的电价模板列表及历史版本查询。'
        '原系统没有这两个模块。电价管理员从云平台电价管理进入电价模板列表，查看模板名称和所属地区。'
        '电价管理员打开电价模板列表后，页面显示已保存模板的名称和所属地区。'
        '历史版本查询显示所选模板已保存的版本名称。目标是方便电价管理员查找已保存的电价模板。'
        '权限、边界异常尚未提供，不可当作没有限制。')
FIELDS=('actor','entry','flow','data','permissions','result','exceptions')


async def until(predicate,message,seconds=20):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        if predicate():return
        await asyncio.sleep(.08)
    raise AssertionError(message)


class FrozenRecommendation(Provider):
    def __init__(self,folder):
        super().__init__(env_path=folder/'absent.env',credential_dir=folder/'credentials')
        self.keys[origin(DEFAULT)]='OFFLINE_ENTRY_ONLY'
        self.requests=[]

    async def request(self,config,messages,evidence=None):
        header=json.loads(messages[0]['content'].split('可信任务头：')[-1])
        raw=messages[1]['content']; ctx=json.loads(raw if isinstance(raw,str) else raw[0]['text'])
        assert header['stage']=='clarify' and header['response_protocol']=='business-draft-v1'
        assert len(ctx['current_rows'])==1 and ctx['current_rows'][0]['title']==TITLE
        ex=next(ex for ex in ctx['excerpts'] if '合成入口验收资料' in ex['text'])
        refs=[dict(source=ex['source'],locator=ex['locator'])]
        self.requests.append(dict(stage=header['stage'],requirement_id=RID,source_refs=refs))
        evidence.before_send(config)
        def field(text):
            return dict(text=text,basis='fact' if text else 'unknown',sources=refs if text else [])
        behavior=dict(actor='电价管理员',entry='从云平台电价管理进入电价模板列表',
            flow='查看电价模板列表',data='模板名称和所属地区',permissions='',exceptions='',result='显示已保存模板的名称和所属地区')
        detail=dict(schema_version=1,applicability={key:dict(applicable=key=='fields',reason='' if key=='fields' else '合成只读列表仅定义展示字段，不包含独立操作或状态流转') for key in ('fields','interactions','flow','state_transitions')},
            fields=[dict(id='F1',name='模板名称',meaning='已保存模板的名称',data_type='text',source='已保存模板',required='只读展示，不适用',default_value='无默认值',constraints='按已保存内容展示',visibility='本例页面显示',editability='只读')],
            interactions=[],flow=dict(nodes=[],edges=[]),state_transitions=[])
        value=dict(summary='本条业务与验收候选，待人工核对。',summary_fields={},
            requirements=[dict(row_key=ctx['current_rows'][0]['row_key'],title=TITLE,statement=field(AFTER),
                behavior={key:field(behavior[key]) for key in FIELDS},acceptance=[field(AC)],questions=[],detail_contract=detail)],
            limitations=['权限及边界异常尚无明确来源，保持未说明。'])
        usage=dict(prompt_tokens=20,completion_tokens=20,total_tokens=40)
        evidence.response(content=json.dumps(value,ensure_ascii=False),http_status=200,finish_reason='stop',usage=usage,response_model='OFFLINE_ENTRY')
        return value,dict(request_model=config['model'],response_model='OFFLINE_ENTRY',usage=usage,origin=origin(config),finish_reason='stop')


class EntryRecorder(Recorder):
    async def note(self,text,seconds=2.7):
        self.chapters.append(dict(seconds=round(time.monotonic()-self.started,2),text=text))
        if self.enabled:
            await self.page.evaluate("text=>{document.getElementById('recording-note').textContent=text}",
                '录屏说明 · 非产品界面\n合成储能云电价资料\n离线冻结模型响应\n非真实 AI 质量测试\n\n'+text)
            await self.page.wait_for_timeout(seconds*1000)


def seed(store,variant):
    p=store.create(NAME)
    with store.edit(p['id'],p['revision'],'合成旧项目单需求入口前置') as (p,_):
        p.update(workflow_mode='shared-draft',scope_profile=dict(scenario='new_module',dependencies='independent'),
            workspace_draft=dict(text=SOURCE,entry_mode='direct',saved_at=now()),intake=dict(mode='direct',text=SOURCE,saved_at=now()))
        refs=[dict(source_id='SRC-ENTRY',excerpt_id='EX-ENTRY')]
        p['sources']=[dict(id='SRC-ENTRY',title='合成电价入口验收材料',purpose='goal',uri=None,sha256='synthetic-entry-v1',created=now(),
            version=1,excluded=False,parse_status='read',failure_reason='',image_mime=None,
            excerpts=[dict(id='EX-ENTRY',source_id='SRC-ENTRY',source_hash='synthetic-entry-v1',locator='合成资料第1段',text=SOURCE,method='synthetic-fixture')])]
        p['product_context']=dict(product='大型储能云平台',module='电价管理',intent='方便电价管理员查找已保存的电价模板。',
            users='电价管理员',change_scope=CHANGE,scope_ids=[RID,OTHER])
        p['context_field_states']={k:dict(status='known') for k in ('intent','users','change_scope')}
        p['product_context_proposal']={}
        if variant=='continuation':
            p['product_context'].pop('change_scope')
            p['context_field_states'].pop('change_scope')
            p['product_context_proposal']={'change_scope':CHANGE}
            p['product_context_proposal_evidence']={'change_scope':dict(text=CHANGE,source_refs=refs,uncertain=False,evidence_status='unreviewed')}
        def row(iid,kind,title,statement,related,**kwargs):
            return dict(id=iid,kind=kind,title=title,statement=statement,applies_to='to_be',epistemic_status='reported',source_refs=refs,
                related_refs=related,selection_status='selected',revision=1,content_version=1,**kwargs)
        p['items']=[row(RID,'requirement',TITLE,BEFORE,[],change_type='unspecified',behavior={},behavior_review_required=True),
            row(OTHER,'requirement','电价历史版本查询','历史版本查询显示所选模板已保存的版本名称。',['AC-HISTORY'],
                change_type='new',behavior=dict(actor='电价管理员',flow='查询所选模板历史版本',result='显示已保存的版本名称'),behavior_review_required=True),
            row('AC-HISTORY','acceptance','历史版本验收','查询所选模板后显示已保存的版本名称。',[OTHER])]
        p['questions']=[]
        if variant in ('guards','answer_guard'):
            p['items'][0]['related_refs']=[ACID]
            p['items'].append(row(ACID,'acceptance',ACTITLE,AC,[RID],change_type='new'))
            p['items'][-1]['selection_status']='candidate'
            p['items'].append(row('REV-TARIFF','requirement',TITLE,AFTER,[],target_item_id=RID,change_type='new',behavior={}))
            p['items'][-1]['selection_status']='candidate'
            p['questions']=[dict(id='Q-SHARED',topic_key='shared-region-filter',question=QUESTION,why='两个模块共同使用的筛选决定',
                related_refs=[RID,OTHER],source_refs=refs,options=['建议：分别筛选（待人工确认）'],blocking=True,blocking_stage=3,
                status='open',answer=None,decision_coverage='pending')]
            if variant=='answer_guard':p['questions'][0]['related_refs']=[RID]
        p['stage_checks']={}
        checkpoint(p,1,status(p)[0]['content_hash'])
    return p['id']


class Journey:
    def __init__(self,args,variant,app,provider,client,output,report):
        self.args,self.variant,self.app,self.provider,self.client,self.output,self.report=args,variant,app,provider,client,output,report
        self.store=app.state.store; self.pid=seed(self.store,variant); self.page=None; self.rec=None
    def project(self):return self.store.get(self.pid)
    def item(self,iid=RID):return next(i for i in self.project()['items'] if i['id']==iid)
    def work(self):return self.page.locator('.connected-workspace:visible')
    def support(self):return self.work().locator('details.requirement-support')
    def tasks(self):return self.work().get_by_role('navigation',name='完善需求待办')
    def editor(self):return self.work().get_by_role('region',name=TITLE+' 需求确认',exact=True)
    async def dispatch(self,route):
        request=route.request; url=urlsplit(request.url)
        assert url.netloc=='127.0.0.1:8765','Forbidden external request '+request.url
        if url.path.startswith('/api/'):
            headers={k:v for k,v in request.headers.items() if k.lower() not in ('host','content-length','connection','accept-encoding')}
            headers['host']='127.0.0.1:8765'
            response=await self.client.request(request.method,url.path+('?' +url.query if url.query else ''),headers=headers,content=request.post_data_buffer)
            entry=dict(method=request.method,path=url.path,status=response.status_code)
            if request.post_data and request.headers.get('content-type','').startswith('application/json'):entry['body']=json.loads(request.post_data)
            self.report['api_requests'].append(entry)
            await route.fulfill(status=response.status_code,content_type=response.headers.get('content-type','application/json'),body=response.content)
        else:
            build=self.args.static_build.resolve(); target=(build/(url.path.lstrip('/') or 'index.html')).resolve()
            assert target.is_relative_to(build) and target.is_file(),url.path
            await route.fulfill(status=200,content_type=mimetypes.guess_type(target.name)[0] or 'application/octet-stream',body=target.read_bytes())
    async def open(self):
        await self.page.goto(ORIGIN,wait_until='networkidle')
        await self.rec.click(self.page.locator('aside.sidebar button[title="'+NAME+'"]'))
        await expect(self.work().get_by_role('button',name='和助手聊需求',exact=True)).to_have_attribute('aria-pressed','true')
        await self.rec.click(self.work().get_by_role('button',name='直接填写需求',exact=True))
        await expect(self.work().get_by_role('textbox',name='填写当前需求的原文底稿',exact=True)).to_have_value(SOURCE)
        # Explicit no-AI entry intentionally preserves this legacy fixture;
        # automatic preparation has its own actual-action browser suite.
        await self.rec.click(self.work().get_by_role('button',name='2 完善需求',exact=True))
        await expect(self.work().get_by_role('table',name='需求确认表')).to_be_visible()
    async def select_task(self,title=TITLE):
        # The single table replaces the old support task panel. Acceptance
        # entries belong to their requirement; cross-row questions stay shared.
        await self.work().get_by_role('combobox',name='定位到需求',exact=True).select_option(RID if title in (TITLE,ACTITLE) else OTHER)
    async def table_only(self):
        table=self.work().get_by_role('table',name='需求确认表',exact=True)
        await expect(table).to_be_visible()
        await expect(table.get_by_role('textbox',name=TITLE+' 需求内容',exact=True)).to_be_in_viewport(ratio=.8)
        assert await self.work().locator('.requirement-task-field:visible, .requirement-task-review:visible').count()==0
        return table
    def save(self,name):
        (self.output/name).write_text(json.dumps(self.project(),ensure_ascii=False,indent=2),encoding='utf-8')


async def entry_flow(j):
    original=copy.deepcopy(j.project()); other=copy.deepcopy(j.item(OTHER))
    await j.select_task(); table=await j.table_only()
    ai=table.get_by_role('button',name='请 AI 完善此需求',exact=True).first
    assert not j.provider.requests and j.item()['change_type']=='unspecified'
    await j.rec.click(ai)
    dialog=j.page.get_by_role('dialog',name='核对本次任务')
    await expect(dialog).to_be_visible()
    plan=next(r for r in reversed(j.report['api_requests']) if r['path'].endswith('/actions/plan'))
    assert plan['body']['target']==dict(kind='item',id=RID) and not j.provider.requests
    await dialog.get_by_role('button',name='返回保留输入',exact=True).click()
    assert j.project()==original
    await ai.click(); await dialog.get_by_role('button',name='授权本次范围并运行',exact=True).click()
    await until(lambda:any(i.get('target_item_id')==RID for i in j.project()['items']),'Frozen recommendation did not arrive')
    await expect(table.get_by_role('textbox',name=TITLE+' 需求内容',exact=True)).to_have_value(AFTER)
    await expect(table.get_by_role('textbox',name=TITLE+' 验收建议 1',exact=True)).to_have_value(AC)
    assert len(j.provider.requests)==1 and j.item()['statement']==BEFORE and j.item()['change_type']=='unspecified'
    created=[i for i in j.project()['items'] if i['id'] not in {x['id'] for x in original['items']}]
    assert len(created)==2 and all(i['selection_status']=='candidate' for i in created) and j.item(OTHER)==other
    j.report['checks'].append('定位需求进入同一表；单行AI先计划授权，取消0调用，一次冻结动作只新增当前需求与验收候选，未改原文或其他行')
    await expect(table.get_by_role('combobox',name=TITLE+' 本次改动',exact=True)).to_have_value('unspecified')
    await table.get_by_role('combobox',name=TITLE+' 本次改动',exact=True).select_option('new')
    await j.work().locator('.refinement-sheet-footer .primary').click()
    await expect(j.work().get_by_role('heading',name='确认交付版本',exact=True)).to_be_visible()
    await until(lambda:confirmation_current(j.project(),j.item()),'Table confirmation did not become current')
    assert j.item()['statement']==AFTER and j.item()['change_type']=='new'
    assert j.item()['behavior']['permissions']=='' and j.item()['behavior']['exceptions']==''
    assert j.item(OTHER)['statement']==other['statement']
    writes=[r for r in j.report['api_requests'] if r['path'].endswith('/refinement-confirmation')]
    assert len(writes)==1 and writes[0]['status']==200
    assert not [r for r in j.report['api_requests'] if r['path'].endswith('/requirement-confirmation/commit')]
    j.report['checks'].append('明确整表确认才原子保存摘要、需求和验收并进入交付；未知权限/异常仍为空，另一行正文保留')


async def continuation_flow(j):
    initial=copy.deepcopy(j.project())
    table=j.work().get_by_role('table',name='需求确认表',exact=True)
    await j.work().get_by_role('navigation',name='需求表定位').get_by_role('button',name='项目摘要',exact=True).click()
    field=table.get_by_role('textbox',name='本次改动',exact=True)
    await field.scroll_into_view_if_needed(); await expect(field).to_have_value(CHANGE)
    await expect(table.locator('tr[data-cell-key="context-change_scope"]')).to_contain_text('AI 整理 · 待你确认')
    await field.fill(CHANGE+' 人工补充：只核对本期资料。')
    await j.select_task(OTHER)
    await expect(table.get_by_role('textbox',name='电价历史版本查询 需求内容',exact=True)).to_be_in_viewport(ratio=.8)
    await j.select_task(); await j.table_only()
    await j.work().get_by_role('button',name='保存草稿',exact=True).click()
    assert j.project()==initial and not j.provider.requests
    await j.page.reload(wait_until='networkidle')
    await expect(j.work().get_by_role('button',name='和助手聊需求',exact=True)).to_have_attribute('aria-pressed','true')
    await j.work().get_by_role('button',name='2 完善需求',exact=True).click()
    await expect(field).to_have_value(CHANGE+' 人工补充：只核对本期资料。')
    assert j.project()==initial and not j.provider.requests
    j.report['checks'].append('摘要候选、需求定位和刷新恢复使用同一表；本地摘要编辑及保存不会单独确认字段或触发模型，刷新先回助手')


async def guards_flow(j):
    frozen=copy.deepcopy(j.project())
    await j.select_task(ACTITLE); table=await j.table_only()
    await expect(table.get_by_role('textbox',name=TITLE+' 验收建议 1',exact=True)).to_have_value(AC)
    text=table.get_by_role('textbox',name=TITLE+' 需求内容',exact=True)
    draft=AFTER+'人工未保存草稿：模板名称保持来源原文。'
    await text.fill(draft)
    await j.work().get_by_role('navigation',name='需求表定位').get_by_role('button',name='待落实回答',exact=True).click()
    question=table.get_by_role('textbox',name=QUESTION+' 我的回答',exact=True)
    await expect(question).to_be_in_viewport(ratio=.8)
    await j.select_task(); await expect(text).to_have_value(draft)
    assert j.project()==frozen and not j.provider.requests
    # Navigation within one persistent table needs no discard dialog. Leaving
    # the table must still protect unsaved edits.
    nav=j.page.get_by_role('navigation',name='需求工作流程')
    await nav.get_by_role('button',name=re.compile('提供信息')).click()
    guard=j.page.get_by_role('dialog',name='未保存的修改',exact=True)
    await expect(guard).to_be_visible()
    await guard.get_by_role('button',name='留在当前页',exact=True).click()
    await expect(text).to_have_value(draft)
    scope=table.get_by_role('combobox',name='电价历史版本查询 本期范围',exact=True)
    await scope.select_option('excluded')
    assert j.project()==frozen
    await j.work().get_by_role('button',name='保存草稿',exact=True).click()
    await j.page.reload(wait_until='networkidle')
    await j.work().get_by_role('button',name='2 完善需求',exact=True).click()
    await expect(scope).to_have_value('excluded'); await expect(text).to_have_value(draft)
    assert j.project()==frozen and not j.provider.requests
    j.report['checks'].append('关联验收与共享问题同表定位；表内跳转不丢草稿，离表取消保留输入；本期范围仅在浏览器保存，未隐式写入或采纳')


async def answer_guard_flow(j):
    frozen=copy.deepcopy(j.project());table=j.work().get_by_role('table',name='需求确认表',exact=True)
    answer=table.get_by_role('textbox',name=QUESTION+' 我的回答',exact=True)
    text='分别筛选，不在两个模块之间联动筛选条件。'
    await answer.fill(text)
    ai=j.work().get_by_role('button',name='AI 补充建议',exact=True)
    await ai.click()
    dialog=j.page.get_by_role('dialog',name='核对本次任务')
    await expect(dialog).to_be_visible()
    assert not j.provider.requests and j.project()==frozen
    await dialog.get_by_role('button',name='返回保留输入',exact=True).click()
    await expect(answer).to_have_value(text)
    await j.page.reload(wait_until='networkidle')
    await expect(j.work().get_by_role('button',name='和助手聊需求',exact=True)).to_have_attribute('aria-pressed','true')
    await j.work().get_by_role('button',name='2 完善需求',exact=True).click()
    await expect(answer).to_have_value(text)
    await expect(table.locator('.answer-implementation input[type="checkbox"]')).to_have_count(0)
    await expect(j.work().locator('.refinement-sheet-footer .primary')).to_have_text('保存并继续核对')
    assert j.project()==frozen and not j.provider.requests
    await j.work().locator('.refinement-sheet-footer .primary').click()
    await until(lambda:next(q for q in j.project()['questions'] if q['id']=='Q-SHARED')['answer']==text,'Answer was not saved')
    question=next(q for q in j.project()['questions'] if q['id']=='Q-SHARED')
    assert question.get('understanding_status')!='applied' and not question.get('applied_item_ids')
    await expect(table).to_be_visible()
    await expect(table.get_by_role('combobox',name=TITLE+' 答案落文位置',exact=True)).to_be_focused()
    assert not j.provider.requests
    j.report['checks'].append('AI核对取消与刷新保留本地回答且0模型；明确保存仅记录原话，无原文核对依据时仍待落实，留表聚焦选择位置，未跳交付')


SCENARIOS={'entry':entry_flow,'continuation':continuation_flow,'guards':guards_flow,'answer_guard':answer_guard_flow}


async def run_one(args,variant):
    output=args.output.resolve()/variant; output.mkdir(parents=True,exist_ok=True)
    report=dict(scenario=variant,status='running',checks=[],page_errors=[],api_requests=[],real_model_calls=0,daily_data_access=False,
        static_build=str(args.static_build.resolve()),fixture='synthetic selected legacy rows, saved scope; continuation summary is NOT preconfirmed')
    recording=args.record and variant=='entry'
    with tempfile.TemporaryDirectory(prefix='ra-entry-') as scratch:
        provider=FrozenRecommendation(Path(scratch)); app=create_app(Path(scratch)/'data',provider=provider)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=ORIGIN,timeout=30) as client:
            j=Journey(args,variant,app,provider,client,output,report);j.save('project-before.json')
            async with async_playwright() as p:
                browser=await p.chromium.launch(headless=True)
                options=dict(viewport=dict(width=1440,height=900))
                if recording:options.update(record_video_dir=str(output/'video'),record_video_size=options['viewport'])
                context=await browser.new_context(**options)
                if recording:await context.add_init_script('('+ANNOTATION+')()')
                await context.route('**/*',j.dispatch)
                j.page=await context.new_page();j.rec=EntryRecorder(j.page,output,recording)
                j.page.on('pageerror',lambda e:report['page_errors'].append(str(e)))
                try:
                    await j.open();await SCENARIOS[variant](j)
                    assert not report['page_errors']
                    report['status']='passed'
                    await j.page.screenshot(path=str(output/'final.png'))
                except Exception as error:
                    report.update(status='failed',error=str(error))
                    await j.page.screenshot(path=str(output/'failure.png'))
                    (output/'failure-body.txt').write_text(await j.page.locator('body').inner_text(),encoding='utf-8')
                finally:
                    j.save('project-after.json');j.rec.save();report['provider_requests']=provider.requests
                    report['tasks']=j.store.records(j.pid,'user_task')
                    video=j.page.video if recording else None
                    await context.close()
                    if video:await video.save_as(str(output/'电价需求统一入口与AI确认.webm'))
                    await browser.close()
    (output/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report


async def main(args):
    results=await asyncio.gather(*(run_one(args,v) for v in (args.scenario or list(SCENARIOS))))
    result=dict(status='passed' if all(r['status']=='passed' for r in results) else 'failed',
        scenarios=[{k:r.get(k) for k in ('scenario','status','checks','error')} for r in results],
        real_model_calls=0,daily_data_access=False,frozen_requests=sum(len(r['provider_requests']) for r in results))
    (args.output/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))
    if result['status']!='passed':raise SystemExit(1)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--static-build',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--scenario',action='append',choices=list(SCENARIOS))
    parser.add_argument('--record',action='store_true')
    asyncio.run(main(parser.parse_args()))
