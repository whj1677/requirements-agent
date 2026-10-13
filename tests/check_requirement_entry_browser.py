"""Isolated entry-path regression for the requirement table.

Starts from the support task list and summary continuation, never silently
substitutes a direct table entry. Synthetic tariff requirements reproduce a
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
        assert header['stage']=='clarify' and header['requirement_recommendation_contract']['requirement_id']==RID
        ex=next(ex for ex in ctx['excerpts'] if '合成入口验收资料' in ex['text'])
        refs=[dict(source_id=ex['source_id'],excerpt_id=ex['id'])]
        original=next(i for i in ctx['requirement_recommendation']['clauses'] if i['id']==RID)
        self.requests.append(dict(stage=header['stage'],requirement_id=RID,source_refs=refs))
        evidence.before_send(config)
        value=dict(schema_version='1.1',stage='clarify',summary='已按本条真实合成来源整理业务与验收候选，待人工确认。',
            proposals=[dict(temp_id='TMP-TARIFF',action='revise',target_item_id=RID,kind='requirement',title=TITLE,
                statement=AFTER,applies_to='to_be',epistemic_status='reported',source_refs=refs,
                related_refs=original.get('related_refs',[]),change_type='new',
                scope_evidence=[dict(state='change',quote='大型储能云平台本期新增独立的电价模板列表及历史版本查询。',source_ref=refs[0])],
                classification_reason='资料明确说明本期新增，原系统没有对应模块。',
                behavior=dict(actor='电价管理员',entry='从云平台电价管理进入电价模板列表',
                    flow='查看电价模板列表',data='模板名称和所属地区',permissions='',exceptions='',result='显示已保存模板的名称和所属地区')),
                dict(temp_id='TMP-TARIFF-AC',action='add',target_item_id=None,kind='acceptance',title=ACTITLE,
                    statement=AC,applies_to='to_be',epistemic_status='reported',source_refs=refs,related_refs=[RID])],
            questions=[],findings=[],used_source_refs=refs,limitations=['权限及边界异常尚无明确来源，保持未说明。'],
            result=dict(scope_summary='只完善电价模板列表',outstanding_decisions=[],next_focus='人工核对本条'))
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
        await expect(self.work().get_by_role('textbox',name='填写当前需求的原文底稿',exact=True)).to_have_value(SOURCE)
        # Explicit no-AI entry intentionally preserves this legacy fixture;
        # automatic preparation has its own actual-action browser suite.
        await self.rec.click(self.work().get_by_role('button',name='不使用 AI，直接完善',exact=True))
        await expect(self.work().get_by_role('table',name='需求确认表')).to_be_visible()
    async def open_support(self):
        if await self.support().get_attribute('open') is None:
            await self.rec.click(self.support().locator(':scope > summary'))
    async def select_task(self,title=TITLE):
        await self.open_support()
        await self.rec.click(self.tasks().get_by_role('button',name=re.compile(r'^\d+\s+'+re.escape(title)+r'$')))
    async def table_only(self):
        editor=self.editor()
        await expect(editor).to_be_visible()
        await expect(editor.get_by_role('heading',name=TITLE,exact=True)).to_be_in_viewport(ratio=.8)
        assert await self.work().locator('.requirement-task-field:visible, .requirement-task-review:visible, [id^="behavior-review-"]:visible').count()==0
        assert await self.work().get_by_role('dialog',name='补充关联验收条件',exact=True).count()==0
        return editor
    def save(self,name):
        (self.output/name).write_text(json.dumps(self.project(),ensure_ascii=False,indent=2),encoding='utf-8')


async def entry_flow(j):
    original=copy.deepcopy(j.project()); other=copy.deepcopy(j.item(OTHER))
    await j.rec.note('1. 从原来的待办入口开始\n这条电价模板需求已纳入本期，但缺少业务说明、验收和改动性质。')
    await j.select_task(); editor=await j.table_only()
    ai=editor.get_by_role('button',name='让 AI 起草业务说明与验收',exact=True)
    await expect(ai).to_be_in_viewport()
    assert not j.provider.requests and j.item()['change_type']=='unspecified'
    await j.page.screenshot(path=str(j.output/'01-task-to-table.png'))
    j.report['checks'].append('旧support单需求待办打开同一表格详情；未知改动性质和缺验收没有退回逐字段旧编辑器')
    await j.rec.note('2. 点击 AI 起草\n先核对本次资料和接收端，尚未调用。')
    await j.rec.click(ai)
    dialog=j.page.get_by_role('dialog',name='核对本次任务')
    await expect(dialog).to_be_visible()
    assert not j.provider.requests
    plan=next(r for r in reversed(j.report['api_requests']) if r['path'].endswith('/actions/plan'))
    assert plan['body']['target']==dict(kind='item',id=RID)
    await j.rec.click(dialog.get_by_role('button',name='返回保留输入',exact=True))
    assert not j.provider.requests
    await j.rec.click(ai)
    await j.rec.click(dialog.get_by_role('button',name='授权本次范围并运行',exact=True))
    await until(lambda:any(i.get('target_item_id')==RID for i in j.project()['items']),'Frozen recommendation did not arrive')
    await expect(editor.get_by_role('textbox',name=TITLE+' 需求内容',exact=True)).to_have_value(AFTER)
    await expect(editor.get_by_role('textbox',name=TITLE+' 验收建议 1',exact=True)).to_have_value(AC)
    assert len(j.provider.requests)==1 and j.item()['change_type']=='unspecified' and j.item()['statement']==BEFORE
    created=[i for i in j.project()['items'] if i['id'] not in {x['id'] for x in original['items']}]
    assert len(created)==2 and all(i['selection_status']=='candidate' for i in created)
    assert j.item(OTHER)==other
    j.report['checks'].append('AI入口先计划授权，取消0调用；一次冻结真实动作只生成当前REQ及验收候选，不自动采纳或改动原文/性质')
    await j.rec.note('3. AI 建议已显示\n需求正文与验收一起看；权限和异常没有依据，仍保留未说明。',4)
    await j.page.screenshot(path=str(j.output/'03-ai-candidate.png'))
    change=editor.get_by_role('combobox',name=TITLE+' 本次改动',exact=True)
    await expect(change).to_have_value('new')
    await j.rec.click(editor.get_by_role('button',name='查看确认内容',exact=True))
    preview=editor.get_by_role('region',name='本条确认预览',exact=True)
    await expect(preview).to_contain_text(AFTER)
    await expect(preview).to_contain_text(AC)
    await expect(preview).to_contain_text('新增')
    assert j.item()['change_type']=='unspecified' and j.item()['statement']==BEFORE
    preview_call=next(r for r in reversed(j.report['api_requests']) if r['path'].endswith('/requirement-confirmation/preview'))
    assert preview_call['body']['change_type']=='new'
    await j.rec.note('4. 人工核对本次确认内容\n新增性质、需求、业务说明与验收一起预览。此时原数据仍未改变。',4)
    await j.page.screenshot(path=str(j.output/'04-confirm-preview.png'))
    await j.rec.click(preview.get_by_role('button',name='确认这条需求',exact=True))
    await until(lambda:confirmation_current(j.project(),j.item()),'Final row confirmation did not become current')
    assert j.item()['change_type']=='new' and j.item()['statement']==AFTER and j.item(OTHER)==other
    assert j.item()['behavior']['permissions']=='' and j.item()['behavior']['exceptions']==''
    table_row=j.work().locator('tr[data-requirement-id="'+RID+'"]')
    await expect(table_row).to_contain_text('已确认')
    await expect(table_row.get_by_role('rowheader')).to_be_in_viewport(ratio=.8)
    await j.rec.note('5. 一次确认整条\n表格保留已确认状态；没有要求填满无依据的七个字段。',4)
    j.report['checks'].append('预览显示新增性质及正文验收但不写原条款，人工一次commit才原子确认；未知字段仍为空，其他行不变')


async def continuation_flow(j):
    assert 'change_scope' not in j.project()['context_field_states']
    await j.open_support()
    await j.rec.click(j.tasks().get_by_role('button',name=re.compile(r'^\d+\s+本次改动$')))
    field=j.work().locator('[id^="refine-context-field-"][id$="-change_scope"]:visible')
    await expect(field).to_contain_text(CHANGE)
    await expect(field).to_contain_text('AI 已整理 · 待你确认')
    await j.rec.click(field.get_by_role('button',name='确认并继续',exact=True))
    await until(lambda:j.project().get('context_field_states',{}).get('change_scope',{}).get('status')=='known','Summary did not confirm')
    await j.table_only()
    assert j.item()['change_type']=='unspecified' and not j.provider.requests
    j.report['checks'].append('最后一项摘要起始待确认；人工确认后真实自动续办进入需求表格，没有走回旧手填面板或调用AI')
    await j.open_support()
    await j.rec.click(j.tasks().get_by_role('button',name=re.compile(r'^\d+\s+电价历史版本查询$')))
    await expect(j.work().get_by_role('region',name='电价历史版本查询 需求确认',exact=True)).to_be_visible()
    assert await j.work().locator('.requirement-task-field:visible').count()==0
    await j.select_task(); await j.table_only()
    j.report['checks'].append('同一待办列表可切换两条需求并返回，始终定位对应表格行，不开旧字段流程')
    ready=j.work().get_by_role('region',name='需求核对准备').filter(visible=True).first
    await j.rec.click(ready.get_by_role('button',name='查看核对清单',exact=True))
    row=ready.locator('.connected-blocker-row').filter(has=j.page.get_by_text(TITLE,exact=True))
    await j.rec.click(row.get_by_role('button',name='查看与确认 →',exact=True))
    await j.table_only()
    assert not j.provider.requests
    j.report['checks'].append('完成清单的单条确认入口也回同一表格，不因入口不同换编辑器')


async def guards_flow(j):
    frozen=copy.deepcopy(j.project())
    await j.select_task(ACTITLE)
    editor=await j.table_only()
    await expect(editor.get_by_role('textbox',name=TITLE+' 验收建议 1',exact=True)).to_have_value(AC)
    j.report['checks'].append('单关联验收待办映射回所属需求表格，不打开单独验收弹窗或定位其他需求')
    text=editor.get_by_role('textbox',name=TITLE+' 需求内容',exact=True)
    draft=AFTER+'人工未保存草稿：模板名称保持来源原文。'
    await text.fill(draft)
    await j.open_support()
    await j.rec.click(j.tasks().get_by_role('button',name=re.compile(re.escape(QUESTION))))
    guard=j.page.get_by_role('dialog',name='未保存的修改',exact=True)
    await expect(guard).to_be_visible()
    await j.rec.click(guard.get_by_role('button',name='留在当前页',exact=True))
    await expect(text).to_have_value(draft)
    assert j.project()==frozen
    j.report['checks'].append('表格未保存草稿切换support问题受保护，取消导航保留原稿且不写任何业务数据')
    # Restore original visible candidate in the editor; no mutation or discard
    # shortcut is used. Cross-requirement discussion must stay in support.
    await text.fill(AFTER)
    await j.open_support()
    await j.rec.click(j.tasks().get_by_role('button',name=re.compile(re.escape(QUESTION))))
    await expect(j.support()).to_have_attribute('open','')
    await expect(j.support().locator('.refine-task-current')).to_contain_text(QUESTION)
    await expect(j.support().locator('.refine-current-heading')).to_be_in_viewport(ratio=.8)
    assert j.project()==frozen and not j.provider.requests
    j.report['checks'].append('跨需求共同问题留在support处理，不错误归入第一条；查看不写入也不调用模型')
    await j.rec.click(j.support().get_by_text('查看或修改其他内容',exact=True))
    await j.rec.click(j.support().get_by_role('button',name='调整本期范围',exact=True))
    scope=j.work().get_by_role('group',name='本期需求范围',exact=True)
    scope_panel=j.work().locator('details.requirement-scope')
    await expect(scope).to_be_visible()
    await expect(scope.get_by_role('checkbox',name='纳入本期 '+TITLE,exact=True)).to_be_checked()
    other=scope.get_by_role('checkbox',name='纳入本期 电价历史版本查询',exact=True)
    await expect(other).to_be_checked()
    await j.rec.click(other)
    await expect(scope_panel.get_by_role('button',name='保存本期范围',exact=True)).to_be_visible()
    assert j.project()==frozen
    await j.rec.click(scope_panel.get_by_role('button',name='放弃范围修改',exact=True))
    await expect(other).to_be_checked()
    assert await scope.locator('textarea:visible, [id^="behavior-review-"]:visible').count()==0
    assert j.project()==frozen and not j.provider.requests
    j.report['checks'].append('整体范围只显示纳入勾选，放弃范围修改恢复原选择；不出现旧逐条表单、不隐式采纳或确认')
    await j.rec.click(other)
    await j.rec.click(scope_panel.get_by_role('button',name='保存本期范围',exact=True))
    await until(lambda:j.project()['product_context']['scope_ids']==[RID],'Explicit scope save did not update only selection')
    assert j.project()['items']==frozen['items'] and j.project()['questions']==frozen['questions']
    assert not j.provider.requests
    write=next(r for r in reversed(j.report['api_requests']) if r['path'].endswith('/product-context') and r['method']=='POST')
    assert write['body']['scope_ids']==[RID] and write['body']['values']=={}
    j.report['checks'].append('明确保存范围只改变用户勾选的scope_ids，需求正文、条款状态与问题不变且无模型调用')


async def answer_guard_flow(j):
    frozen=copy.deepcopy(j.project())
    await j.select_task();editor=await j.table_only()
    answer=editor.get_by_role('textbox',name=QUESTION+' 我的回答',exact=True)
    text='分别筛选，不在两个模块之间联动筛选条件。'
    await answer.fill(text)
    guard=j.page.get_by_role('dialog',name='未保存的修改',exact=True)
    for button in ('查看确认内容','让 AI 调整本行'):
        await j.rec.click(editor.get_by_role('button',name=button,exact=True))
        await expect(guard).to_be_visible()
        await j.rec.click(guard.get_by_role('button',name='留在当前页',exact=True))
        await expect(answer).to_have_value(text)
        assert j.project()==frozen and not j.provider.requests
    j.report['checks'].append('未保存回答进入预览或AI均受保护，留当前保留完整回答，无计划执行或业务写入')
    await j.rec.click(editor.get_by_role('button',name='查看确认内容',exact=True))
    await j.rec.click(guard.get_by_role('button',name='保存后离开',exact=True))
    preview=editor.get_by_role('region',name='本条确认预览',exact=True)
    await expect(preview).to_be_visible()
    await expect(preview).to_contain_text(text)
    saved=copy.deepcopy(j.project())
    assert saved['revision']>frozen['revision'] and saved['items']==frozen['items']
    assert next(q for q in saved['questions'] if q['id']=='Q-SHARED')['answer']==text
    call=next(r for r in reversed(j.report['api_requests']) if r['path'].endswith('/requirement-confirmation/preview'))
    assert call['body']['expected_revision']==saved['revision'] and call['status']==200
    await j.rec.click(preview.get_by_role('button',name='返回修改',exact=True))
    if await editor.locator('details.table-questions').get_attribute('open') is None:
        await j.rec.click(editor.locator('details.table-questions > summary'))
    await expect(answer).to_have_value(text)
    await answer.fill(text+'这是本次明确回答。')
    await j.rec.click(editor.get_by_role('button',name='让 AI 调整本行',exact=True))
    await expect(guard).to_be_visible()
    await j.rec.click(guard.get_by_role('button',name='保存后离开',exact=True))
    dialog=j.page.get_by_role('dialog',name='核对本次任务')
    await expect(dialog).to_be_visible()
    assert next(q for q in j.project()['questions'] if q['id']=='Q-SHARED')['answer']==text+'这是本次明确回答。'
    assert not j.provider.requests
    await j.rec.click(dialog.get_by_role('button',name='返回保留输入',exact=True))
    await expect(answer).to_have_value(text+'这是本次明确回答。')
    j.report['checks'].append('保存回答后预览使用最新revision并展示原话；AI计划使用保存后的回答，取消计划仍0模型且不丢回答')


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
