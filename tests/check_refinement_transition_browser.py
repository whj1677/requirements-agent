"""Automatic refinement preparation with isolated UI/API and frozen responses.

No proposal or readiness marker is pre-seeded. Every reusable result is produced
through the real interview/organize action chain. The browser's sole origin is
intercepted into a temporary ASGI application; no external model/daily data or
additional server is involved. A video, when requested, is explicitly offline.
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
IMPORT_SANDBOX=tempfile.TemporaryDirectory(prefix='ra-transition-import-')
os.environ['RA_DATA_DIR']=str((Path(IMPORT_SANDBOX.name)/'data').resolve())
from app.business_draft import BEHAVIOR, PROTOCOL, SUMMARY_FIELDS
from app.core import Problem
from app.main import create_app
from app.provider import DEFAULT, Provider, origin
from tests.check_refine_human_flow_browser import ANNOTATION, Recorder

ORIGIN='http://127.0.0.1:8765'
NAME='合成储能云：自动整理入口'
TITLE='场站告警处理记录'
TEXT=('这是自动转阶段界面验收的合成资料。大型储能云平台新增独立的告警处理记录模块。'
      '目标使用者为场站值班人员。目标是让值班人员在云端填写并保存告警处理记录。'
      '保存成功后在告警详情显示本次处理内容。不做设备控制；权限边界尚未确定。')
STATEMENT='场站值班人员在云端填写并保存告警处理记录。'
AC='值班人员填写处理记录并保存后，告警详情显示本次保存的处理内容。'
CONTEXT=dict(intent='让值班人员在云端填写并保存告警处理记录。',users='场站值班人员。',change_scope='新增告警处理记录填写、保存和详情显示。')


async def wait_until(predicate, message, timeout=20):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        if predicate(): return
        await asyncio.sleep(.08)
    raise AssertionError(message)


class FrozenProvider(Provider):
    def __init__(self, folder):
        super().__init__(env_path=folder/'absent.env',credential_dir=folder/'credentials')
        self.keys[origin(DEFAULT)]='OFFLINE_TRANSITION_ONLY'
        self.requests=[]
        self.release=asyncio.Event(); self.release.set()
        self.entered=asyncio.Event()
        self.fail_next=False
        self.legacy_draft_next=False
        self.return_after_cancel=False
        self.late_response_returned=False

    def hold(self, *, fail=False, late=False):
        self.release.clear(); self.entered.clear()
        self.fail_next=fail; self.return_after_cancel=late

    async def request(self, config, messages, evidence=None):
        header=json.loads(messages[0]['content'].split('可信任务头：')[-1])
        stage=header['stage']
        raw=messages[1]['content']
        ctx=json.loads(raw if isinstance(raw,str) else raw[0]['text'])
        ex=next(ex for ex in ctx['excerpts'] if '自动转阶段界面验收' in ex['text'])
        business=stage=='ingest' and header.get('response_protocol')==PROTOCOL
        refs=([dict(source=ex['source'],locator=ex['locator'])] if business else
              [dict(source_id=ex['source_id'],excerpt_id=ex['id'])])
        self.requests.append(dict(stage=stage,source_refs=refs,origin=origin(config),
                                  response_protocol=header.get('response_protocol'),
                                  draft_contract=header.get('draft_contract')))
        assert stage in ('interview','ingest'), 'Unexpected offline request stage: '+stage
        assert evidence is not None
        evidence.before_send(config)
        if stage=='ingest':
            self.entered.set()
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                if not self.return_after_cancel: raise
                await self.release.wait()
                self.late_response_returned=True
            if self.fail_next:
                self.fail_next=False
                raise Problem('OFFLINE_INJECTED_FAILURE','离线注入：本次整理失败，已有资料和草稿保留。',502,model_repair_allowed=False)
        value=dict(schema_version='1.1',stage=stage,summary='已根据本轮合成原话整理，内容与未知边界均待人工核对。',
            proposals=[],questions=[],findings=[],used_source_refs=refs,limitations=[],result={})
        if business:
            def field(text='',basis='unknown'):
                return dict(text=text,basis=basis,sources=copy.deepcopy(refs) if basis=='fact' else [])
            rows=ctx['current_rows'] or [dict(row_key='new-alarm-record',title=TITLE,statement=STATEMENT)]
            value=dict(summary='已根据合成原话整理整表草稿，权限仍未知，等待人工核对。',
                summary_fields={key:field() for key in SUMMARY_FIELDS},requirements=[],
                limitations=['权限边界尚未确定，未据此补充角色授权。'])
            value['summary_fields'].update({key:field(text,'fact') for key,text in
                {**CONTEXT,'product':'大型储能云平台','module':'告警处理记录','out_of_scope':'不做设备控制。'}.items()})
            for row in rows:
                value['requirements'].append(dict(row_key=row['row_key'],title=row['title'],
                    statement=field(row['statement'],'fact'),
                    behavior={key:(field('场站值班人员','fact') if key=='actor' else
                                   field('告警详情显示本次保存的处理内容。','fact') if key=='result' else
                                   field('权限边界尚未确定。') if key=='permissions' else field())
                              for key in BEHAVIOR},
                    acceptance=[] if self.legacy_draft_next else [field(AC,'fact')],questions=[]))
            self.legacy_draft_next=False
        elif stage=='interview':
            value['result']={'can_organize':True}
            value['context_draft']={k:dict(text=v,source_refs=refs) for k,v in CONTEXT.items()}
        else:
            value['product_context_proposal']={**dict.fromkeys(('product','module','intent','current_state','users','value','change_scope','preserve_scope','out_of_scope','priority'),''),
                **CONTEXT,'product':'大型储能云平台','module':'告警处理记录','out_of_scope':'不做设备控制。'}
            value['proposals']=[dict(temp_id='TMP-ALARM',action='add',target_item_id=None,kind='requirement',title=TITLE,
                statement=STATEMENT,applies_to='to_be',epistemic_status='reported',source_refs=refs,related_refs=['TMP-AC']),
                dict(temp_id='TMP-AC',action='add',target_item_id=None,kind='acceptance',title='保存后显示处理内容',
                statement=AC,applies_to='to_be',epistemic_status='reported',source_refs=refs,related_refs=['TMP-ALARM'])]
            value['result']=dict(understanding='新增独立的告警处理记录模块，权限边界仍未知。',material_limits=[])
        usage=dict(prompt_tokens=20,completion_tokens=20,total_tokens=40)
        evidence.response(content=json.dumps(value,ensure_ascii=False),http_status=200,finish_reason='stop',usage=usage,response_model='OFFLINE_TRANSITION')
        return value,dict(request_model=config['model'],response_model='OFFLINE_TRANSITION',usage=usage,origin=origin(config),finish_reason='stop')


class TransitionRecorder(Recorder):
    async def note(self,text,seconds=2.5):
        self.chapters.append(dict(seconds=round(time.monotonic()-self.started,2),text=text))
        if self.enabled:
            await self.page.evaluate("text=>{document.getElementById('recording-note').textContent=text}",
                '录屏说明 · 非产品界面\n合成储能云平台\n离线冻结模型响应\n非真实 AI 质量测试\n\n'+text)
            await self.page.wait_for_timeout(seconds*1000)


class Journey:
    def __init__(self,args,variant,app,provider,client,output,report):
        self.args,self.variant,self.app,self.provider,self.client,self.output,self.report=args,variant,app,provider,client,output,report
        self.store=app.state.store
        p=self.store.create(NAME)
        self.pid=p['id']
        self.other=self.store.create('另一隔离项目：不能被自动拉走')['id']
        self.page=None; self.rec=None
        self.drop_next_action_response=False
        self.dropped_response=False
        self.fail_next_draft_read=False
        self.draft_read_gate=None

    def project(self): return self.store.get(self.pid)
    def tasks(self): return [r for r in self.store.records(self.pid,'user_task') if r['action']=='organize']
    def calls(self,stage='ingest'): return sum(r['stage']==stage for r in self.provider.requests)
    def work(self): return self.page.locator('.connected-workspace:visible')
    def nav(self): return self.page.get_by_role('navigation',name='需求工作流程')

    async def api(self,path,method='GET',body=None):
        session=(await self.client.get('/api/session')).json()
        self.client.headers.update({'Origin':ORIGIN,'X-CSRF-Token':session['csrf']})
        response=await self.client.request(method,f'/api/projects/{self.pid}'+path,json=body)
        assert response.status_code==200,response.text
        return response.json()

    async def dispatch(self,route):
        request=route.request; url=urlsplit(request.url)
        assert url.netloc=='127.0.0.1:8765','External request forbidden: '+request.url
        if url.path.startswith('/api/'):
            if self.draft_read_gate is not None and request.method=='GET' and url.path.endswith('/workspace-draft'):
                await self.draft_read_gate.wait()
            if self.fail_next_draft_read and request.method=='GET' and url.path.endswith('/workspace-draft'):
                self.fail_next_draft_read=False
                self.report['api_requests'].append(dict(method='GET',path=url.path,status=503,injected_failure=True))
                await route.fulfill(status=503,content_type='application/json',body=json.dumps(dict(detail='离线注入：工作底稿读取失败')))
                return
            headers={k:v for k,v in request.headers.items() if k.lower() not in ('host','content-length','connection','accept-encoding')}
            headers['host']='127.0.0.1:8765'
            body=json.loads(request.post_data) if request.post_data and request.headers.get('content-type','').startswith('application/json') else None
            response=await self.client.request(request.method,url.path+('?' +url.query if url.query else ''),headers=headers,content=request.post_data_buffer)
            row=dict(method=request.method,path=url.path,status=response.status_code)
            if body and (url.path.endswith('/actions') or url.path.endswith('/actions/plan')):
                row['request']=body
            self.report['api_requests'].append(row)
            if self.drop_next_action_response and url.path.endswith('/actions') and request.method=='POST' and body and body.get('action')=='organize':
                self.drop_next_action_response=False; self.dropped_response=True; row['response_dropped_after_server_commit']=True
                await route.abort('connectionreset'); return
            await route.fulfill(status=response.status_code,content_type=response.headers.get('content-type','application/json'),body=response.content)
        else:
            build=self.args.static_build.resolve(); target=(build/(url.path.lstrip('/') or 'index.html')).resolve()
            assert target.is_relative_to(build)
            await route.fulfill(status=200 if target.is_file() else 404,body=target.read_bytes() if target.is_file() else b'',
                content_type=mimetypes.guess_type(target.name)[0] or 'application/octet-stream')

    async def open(self):
        await self.page.goto(ORIGIN+'/',wait_until='networkidle')
        await self.rec.click(self.page.locator('aside.sidebar button[title="'+NAME+'"]'))
        # Wait for the actual information draft load, not an arbitrary delay.
        # Concurrent browser scenarios can make a fixed 180 ms sleep too short.
        await expect(self.work().get_by_role('button',name='进入完善需求',exact=True).filter(visible=True)).to_be_enabled()

    async def chat(self,text=TEXT):
        await self.rec.click(self.work().get_by_role('button',name='和助手聊需求',exact=True))
        before=len([m for m in self.project()['messages'] if m['role']=='assistant' and m['stage']=='interview'])
        await self.rec.fill(self.work().get_by_role('textbox',name='你的想法',exact=True),text)
        await self.rec.click(self.work().locator('.chat-composer-actions').get_by_role('button',name='发送',exact=True))
        await wait_until(lambda:len([m for m in self.project()['messages'] if m['role']=='assistant' and m['stage']=='interview'])==before+1,'Interview did not finish')
        await expect(self.work().locator('.chat-composer-actions').get_by_role('button',name='发送',exact=True)).to_be_visible()
        await expect(self.work().get_by_role('textbox',name='你的想法',exact=True)).to_have_value('')
        await self.page.wait_for_timeout(200)

    def primary(self):
        return self.work().get_by_role('button',name='整理并进入完善需求',exact=True).filter(visible=True).first

    async def information(self):
        await self.rec.click(self.nav().get_by_role('button',name=re.compile('提供信息')))
        await expect(self.nav().get_by_role('button',name=re.compile('提供信息'))).to_have_attribute('aria-current','step')

    async def in_information(self):
        await expect(self.nav().get_by_role('button',name=re.compile('提供信息'))).to_have_attribute('aria-current','step')

    async def ready(self):
        await expect(self.nav().get_by_role('button',name=re.compile('完善需求'))).to_have_attribute('aria-current','step',timeout=20000)
        table=self.page.get_by_role('table',name='需求确认表')
        await expect(table).to_be_visible()
        await expect(table.get_by_text(TITLE,exact=True).first).to_be_in_viewport(ratio=.8)
        assert self.tasks()[-1]['status'] in ('succeeded','partial')
        p=self.project()
        assert all(i['selection_status']=='candidate' for i in p['items']), 'Automatic preparation adopted an item'
        assert not p.get('context_field_states') and not (p.get('product_context') or {}).get('scope_ids')
        assert p.get('product_context_proposal',{}).get('intent')==CONTEXT['intent']
        assert self.tasks()[-1].get('preparation_draft_schema')==2
        assert self.tasks()[-1].get('preparation_result',{}).get('draft_schema')==2
        assert any(item['kind']=='acceptance' and item['statement']==AC for item in p['items'])
        assert all(not (item.get('behavior') or {}).get('permissions') for item in p['items'] if item['kind']=='requirement')

    async def organize(self,*,gated=True):
        before=len(self.tasks())
        if gated:self.provider.hold()
        await self.rec.click(self.primary())
        await wait_until(lambda:len(self.tasks())==before+1,'Organize task was not created')
        await wait_until(self.provider.entered.is_set,'Frozen ingest did not start')
        await self.in_information()
        assert await self.page.get_by_role('dialog',name='核对本次任务').count()==0,'Existing grants unexpectedly prompted again'
        await expect(self.page.get_by_role('complementary',name='AI 任务进度')).to_be_visible()
        if gated:self.provider.release.set()
        await self.ready()

    def save(self,name):
        (self.output/name).write_text(json.dumps(self.project(),ensure_ascii=False,indent=2),encoding='utf-8')


async def main_flow(j):
    assert not j.project().get('product_context') and not j.project().get('product_context_proposal') and not j.project()['items']
    if not j.args.record:
        await j.rec.click(j.work().get_by_role('button',name='和助手聊需求',exact=True))
        draft=j.work().get_by_role('textbox',name='你的想法',exact=True)
        await draft.fill('还未发送的合成补充，不能被入口漏掉。')
        await j.rec.click(j.primary())
        await expect(j.page.get_by_role('alert').filter(has_text='聊天框还有未发送的文字或附件。')).to_contain_text('请先发送，或移除本轮输入，再进入完善需求。')
        await j.in_information(); await expect(draft).to_have_value('还未发送的合成补充，不能被入口漏掉。')
        assert not j.provider.requests
        await draft.fill('')
        await j.work().get_by_label('选择本轮聊天附件',exact=True).set_input_files({'name':'未发送验收.txt','mimeType':'text/plain','buffer':b'synthetic unsent attachment'})
        await j.rec.click(j.primary())
        await j.in_information()
        await expect(j.work().get_by_label('本轮待发送附件')).to_contain_text('未发送验收.txt')
        assert not j.provider.requests
        await j.rec.click(j.work().get_by_role('button',name='移除本轮附件 未发送验收.txt',exact=True))
        j.report['checks'].append('未发送文字或附件阻止整理跳页，保留原输入，不漏发材料也不暗中调用')
    await j.rec.note('1. 从空项目开始\n先把已知的储能云需求发给助手。')
    await j.chat()
    assert not j.project()['items']
    await j.rec.note('2. 一次点击“整理并进入完善需求”\n沿用刚才聊天已经确认的资料与接收端。')
    j.provider.hold()
    await j.rec.click(j.primary())
    await wait_until(j.provider.entered.is_set,'Organize not running')
    await j.in_information()
    await expect(j.page.get_by_role('complementary',name='AI 任务进度')).to_be_visible()
    assert len(j.tasks())==1 and j.calls()==1
    assert await j.page.get_by_role('dialog',name='核对本次任务').count()==0
    await j.rec.note('3. 整理进行中\n暂留当前页，可见 AI 任务进度。',3)
    await j.page.screenshot(path=str(j.output/'03-progress.png'))
    j.provider.release.set()
    await j.ready()
    await j.rec.note('4. 整理完成，自动进入完善需求\n已有需求和验收候选，等待人工确认。',4)
    await j.page.screenshot(path=str(j.output/'04-ready.png'))
    j.report['checks'].append('从空项目实际访谈，一次点击自动整理并显示进度；成功后自动到候选表，不自动采纳或确认范围')
    await j.information()
    before=j.calls()
    await j.rec.click(j.work().get_by_role('button',name='进入完善需求',exact=True).filter(visible=True))
    await j.ready()
    assert j.calls()==before and len(j.tasks())==1
    await j.rec.note('5. 返回后再次进入\n信息没有变化，直接使用已有草稿，不重复整理。',3)
    j.report['checks'].append('返回提供信息后再次进入直接复用已有草稿，零新增任务或模型请求')
    table=j.page.get_by_role('table',name='需求确认表')
    field=table.get_by_role('textbox',name='希望达到的目标',exact=True)
    await field.scroll_into_view_if_needed()
    await expect(field).to_have_value(CONTEXT['intent'])
    await expect(field).to_be_in_viewport(ratio=.8)
    summary_row=table.locator('tr').filter(has=j.page.get_by_role('textbox',name='希望达到的目标',exact=True))
    await expect(summary_row).to_contain_text('AI 整理 · 待你确认')
    await expect(j.page.get_by_role('button',name='确认并进入交付',exact=True)).to_be_visible()
    await table.get_by_role('combobox',name=TITLE+' 本期范围',exact=True).select_option('included')
    acceptance=table.get_by_role('textbox',name=TITLE+' 验收建议 1',exact=True)
    await acceptance.scroll_into_view_if_needed()
    await expect(acceptance).to_have_value(AC)
    await expect(acceptance).to_be_in_viewport(ratio=.8)
    assert not j.project().get('context_field_states') and j.calls()==before
    await j.rec.note('6. 表格直接展示目标摘要和验收候选\nAI 已根据聊天整理，无需重新填写；等待人工核对后统一确认。',4)
    await j.page.screenshot(path=str(j.output/'06-summary-and-acceptance-candidates.png'))
    j.report['checks'].append('单表显示AI整理的目标摘要，用户明确纳入本期后显示已有验收候选；表内可滚动核对，统一确认按钮明确可见，不需重填也未自动确认或追加调用')



async def reuse_flow(j):
    await j.chat(); await j.organize()
    state=await j.api('/refinement-preparation'); first_hash=state['input_hash']; assert state['status'] in ('ready','ready_with_limits')
    # A real separate client's human summary confirmation changes revision, not
    # raw input. This control never fakes preparation markers or candidates.
    current=j.project()
    await j.api('/product-context','POST',dict(expected_revision=current['revision'],values={'intent':CONTEXT['intent']},field_states={'intent':{'status':'known'}}))
    assert j.project()['revision']>current['revision']
    state=await j.api('/refinement-preparation'); assert state['input_hash']==first_hash and not state['needs_organize']
    await j.information(); before=j.calls()
    await j.rec.click(j.nav().get_by_role('button',name=re.compile('完善需求')))
    await expect(j.nav().get_by_role('button',name=re.compile('完善需求'))).to_have_attribute('aria-current','step')
    assert j.calls()==before and len(j.tasks())==1
    await j.information(); await j.chat(TEXT+'补充：记录需显示填写时间，具体格式待确认。')
    state=await j.api('/refinement-preparation'); assert state['input_hash']!=first_hash and state['needs_organize']
    j.provider.hold(); await j.rec.click(j.primary())
    await wait_until(lambda:len(j.tasks())==2,'New raw input did not trigger organize')
    j.provider.release.set()
    await expect(j.nav().get_by_role('button',name=re.compile('完善需求'))).to_have_attribute('aria-current','step',timeout=20000)
    assert j.calls()==before+1 and j.project()['context_field_states']['intent']['status']=='known'
    j.report['checks'].append('人工确认导致revision增加不重新整理；新增用户原话改变输入指纹才发起一次新整理')


async def duplicate_flow(j):
    await j.chat(); j.provider.hold(); j.drop_next_action_response=True
    button=j.primary(); await button.scroll_into_view_if_needed()
    box=await button.bounding_box()
    # Real pointer double-click; the disabled second click must not start again.
    await j.page.mouse.click(box['x']+box['width']/2,box['y']+box['height']/2,click_count=2,delay=35)
    await wait_until(lambda:len(j.tasks())==1 and j.dropped_response,'Committed response was not dropped')
    await j.in_information()
    await j.rec.click(j.nav().get_by_role('button',name=re.compile('完善需求')))
    await j.page.wait_for_timeout(350)
    assert len(j.tasks())==1 and j.calls()==1
    submitted=[r['request'] for r in j.report['api_requests'] if r['method']=='POST' and r['path'].endswith('/actions') and r.get('request',{}).get('action')=='organize']
    assert submitted and len({r['idempotency_key'] for r in submitted})==1
    j.provider.release.set()
    await j.ready()
    assert len(j.tasks())==1 and j.calls()==1
    j.report['checks'].append('双击入口且首次提交响应丢失后从顶部重进，仍复用同一请求键/任务，不重复调用或跳页')


async def failure_flow(j):
    await j.chat(); await j.organize(); await j.information()
    await j.chat(TEXT+'补充：处理记录保存时间需要保留。')
    before=copy.deepcopy(j.project()); old_tasks=len(j.tasks()); before_calls=j.calls()
    j.provider.hold(fail=True)
    await j.rec.click(j.primary()); await wait_until(j.provider.entered.is_set,'Failure task did not start')
    j.provider.release.set()
    await wait_until(lambda:len(j.tasks())==old_tasks+1 and j.tasks()[-1]['status']=='failed','Injected failure did not settle')
    await j.in_information()
    panel=j.page.get_by_label('进入完善需求的准备状态',exact=True)
    await expect(panel.get_by_role('button',name='重试整理',exact=True)).to_be_visible()
    assert j.project()['items']==before['items']
    assert j.project()['sources']==before['sources']
    assert [m for m in j.project()['messages'] if m['role']=='user']==[m for m in before['messages'] if m['role']=='user']
    await j.page.wait_for_timeout(700)
    assert j.calls()==before_calls+1 and len(j.tasks())==old_tasks+1
    keys=[t['idempotency_key'] for t in j.tasks()]
    j.provider.hold(); await j.rec.click(panel.get_by_role('button',name='重试整理',exact=True))
    await wait_until(lambda:len(j.tasks())==old_tasks+2,'Explicit retry not created')
    assert j.tasks()[-1]['idempotency_key'] not in keys
    j.provider.release.set()
    await j.ready()
    j.report['checks'].append('整理失败留当前页，原话/资料/旧候选不变且不自动重跑；主动重试才新任务，成功后自动进入')


async def manual_flow(j):
    await j.rec.click(j.work().get_by_role('button',name='直接填写需求',exact=True))
    await j.rec.fill(j.work().get_by_role('textbox',name='填写当前需求的原文底稿',exact=True),TEXT)
    await j.rec.click(j.work().get_by_role('button',name='不使用 AI，直接完善',exact=True).filter(visible=True).first)
    await expect(j.nav().get_by_role('button',name=re.compile('完善需求'))).to_have_attribute('aria-current','step')
    assert not j.provider.requests and not j.tasks() and j.project()['workspace_draft']['text']==TEXT
    assert not j.project()['items']
    j.report['checks'].append('显式手动路径先保存直接填写原文再进入，0 Provider请求，不伪造候选')
    await j.page.reload(wait_until='networkidle')
    await expect(j.page.get_by_role('table',name='需求确认表')).to_be_visible()
    await expect(j.work().get_by_text('当前为手动完善，刷新或再次进入不会自动调用 AI。',exact=False)).to_be_visible()
    assert not j.provider.requests and not j.tasks()
    await j.information()
    await j.rec.click(j.work().get_by_role('button',name='进入完善需求',exact=True).filter(visible=True))
    await expect(j.page.get_by_role('table',name='需求确认表')).to_be_visible()
    assert not j.provider.requests and not j.tasks()
    j.report['checks'].append('首次明确手动完善选择跨刷新和返回重入保留，0 自动 Provider 请求')
    await j.information()
    await j.rec.click(j.primary())
    dialog=j.page.get_by_role('dialog',name='核对本次任务')
    await expect(dialog).to_be_visible()
    await expect(dialog).to_contain_text('本次包含需你授权的内容')
    assert not j.provider.requests and not j.tasks()
    await j.rec.click(dialog.get_by_role('button',name='返回保留输入',exact=True))
    await j.in_information()
    await expect(j.work().get_by_role('textbox',name='填写当前需求的原文底稿',exact=True)).to_have_value(TEXT)
    await j.rec.click(j.primary()); await expect(dialog).to_be_visible()
    j.provider.hold()
    await j.rec.click(dialog.get_by_role('button',name='授权本次范围并运行',exact=True))
    await wait_until(j.provider.entered.is_set,'Authorized task did not start')
    j.provider.release.set(); await j.ready()
    assert j.calls()==1
    j.report['checks'].append('无已有接收端/资料授权时先显示原有核对弹窗；取消不调用，明确授权后才整理')


async def cancel_switch_flow(j):
    await j.chat(); j.provider.hold(late=True)
    await j.rec.click(j.primary()); await wait_until(j.provider.entered.is_set,'Cancel task not started')
    panel=j.page.get_by_label('进入完善需求的准备状态',exact=True)
    cancel=panel.get_by_role('button',name=re.compile('取消|停止'))
    if await cancel.count():
        await j.rec.click(cancel.first)
    else:
        progress=j.page.get_by_role('complementary',name='AI 任务进度')
        await j.rec.click(progress.get_by_role('button',name='停止后续步骤',exact=True))
    await wait_until(lambda:j.tasks()[-1]['status']=='cancelled','Task was not cancelled')
    j.provider.release.set()
    await wait_until(lambda:j.provider.late_response_returned,'Frozen late response did not return')
    await j.page.wait_for_timeout(500); await j.in_information()
    assert not j.project()['items'] and len(j.tasks())==1
    j.report['checks'].append('主动取消后冻结接收端仍返回迟到结果，页面不跳转且不写候选')
    # Retry is a separate explicit intention; switch projects while it runs.
    retry=panel.get_by_role('button',name='重试整理',exact=True)
    await expect(retry).to_be_visible()
    j.provider.hold(); await j.rec.click(retry)
    await wait_until(lambda:len(j.tasks())==2 and j.provider.entered.is_set(),'Switch task not started')
    await j.rec.click(j.page.get_by_role('button',name='另一隔离项目：不能被自动拉走'))
    await expect(j.page.get_by_role('heading',name='另一隔离项目：不能被自动拉走',exact=True)).to_be_visible()
    j.provider.release.set()
    await wait_until(lambda:j.tasks()[-1]['status'] in ('succeeded','partial'),'Background project task did not finish')
    await j.page.wait_for_timeout(700)
    await expect(j.page.get_by_role('heading',name='另一隔离项目：不能被自动拉走',exact=True)).to_be_visible()
    assert not j.store.get(j.other)['items']
    await j.rec.click(j.page.locator('aside.sidebar button[title="'+NAME+'"]'))
    before=j.calls()
    await j.rec.click(j.work().get_by_role('button',name='进入完善需求',exact=True).filter(visible=True))
    await j.ready(); assert j.calls()==before and len(j.tasks())==2
    j.report['checks'].append('切换项目后原项目完成不会拉走当前项目；返回后可用原草稿且不重复整理')


async def restore_refine_flow(j):
    await j.chat()
    # The real action chain produces a deliberately incomplete frozen response.
    # Only its version marker is downgraded to represent the previous contract.
    j.provider.legacy_draft_next=True
    await j.rec.click(j.primary())
    await wait_until(lambda:bool(j.tasks()) and j.tasks()[-1]['status'] in ('succeeded','partial'),'Legacy draft did not finish')
    table=j.page.get_by_role('table',name='需求确认表')
    await expect(table).to_be_visible()
    assert not any(i['kind']=='acceptance' for i in j.project()['items'])
    await table.get_by_role('combobox',name=TITLE+' 本期范围',exact=True).select_option('included')
    manual=STATEMENT+'（隔离浏览器保留的人工改动。）'
    await table.get_by_role('textbox',name=TITLE+' 需求内容',exact=True).fill(manual)
    await j.work().get_by_role('button',name='保存草稿',exact=True).click()
    task=j.tasks()[-1]
    legacy_result={**task.get('preparation_result',{}),'draft_schema':1}
    j.store.update_record(j.pid,task['id'],'user_task',preparation_draft_schema=1,preparation_result=legacy_result)
    j.report['legacy_marker_fixture']=dict(task_id=task['id'],preparation_draft_schema=1)
    state=await j.api('/refinement-preparation')
    assert state['status']=='needs_organize' and state['auto_allowed']
    before=j.calls()
    j.provider.hold()
    await j.page.reload(wait_until='networkidle')
    await wait_until(j.provider.entered.is_set,'Restored refine never entered preparation')
    await expect(j.page.get_by_label('进入完善需求的准备状态',exact=True)).to_be_visible()
    await expect(table).to_have_count(0)
    await j.rec.click(j.nav().get_by_role('button',name=re.compile('完善需求')))
    assert j.calls()==before+1 and len(j.tasks())==2
    j.provider.release.set()
    await expect(table).to_be_visible(timeout=20000)
    await expect(table.get_by_role('textbox',name=TITLE+' 需求内容',exact=True)).to_have_value(manual)
    await expect(table.get_by_role('textbox',name=TITLE+' 验收建议 1',exact=True)).to_have_value(AC)
    await j.page.reload(wait_until='networkidle')
    await expect(table).to_be_visible()
    await expect(table.get_by_role('textbox',name=TITLE+' 需求内容',exact=True)).to_have_value(manual)
    assert j.calls()==before+1 and len(j.tasks())==2
    await j.rec.click(j.nav().get_by_role('button',name=re.compile('完善需求')))
    await expect(table).to_be_visible()
    assert j.calls()==before+1 and len(j.tasks())==2
    j.report['checks'].append('旧契约缺验收且停在完善需求，刷新自动检查并单飞补充一次；人工表格草稿保留，新验收自动显示，再刷新或同阶段点击不重跑')


async def restore_authorization_flow(j):
    await j.api('/workspace-draft','POST',dict(expected_revision=j.project()['revision'],text=TEXT,entry_mode='direct'))
    await j.page.evaluate("pid=>sessionStorage.setItem('ra-connected-step-'+pid,'refine')",j.pid)
    await j.page.reload(wait_until='networkidle')
    dialog=j.page.get_by_role('dialog',name='核对本次任务')
    await expect(dialog).to_be_visible()
    assert not j.provider.requests and not j.tasks()
    await dialog.get_by_role('button',name='返回保留输入',exact=True).click()
    panel=j.page.get_by_label('进入完善需求的准备状态',exact=True)
    await expect(panel.get_by_role('button',name='继续检查并整理',exact=True)).to_be_visible()
    assert not j.provider.requests and not j.tasks()
    await j.page.reload(wait_until='networkidle')
    await expect(dialog).to_be_visible()
    assert not j.provider.requests and not j.tasks()
    await dialog.get_by_role('button',name='返回保留输入',exact=True).click()
    await panel.get_by_role('button',name='不使用 AI，直接完善',exact=True).click()
    await expect(j.page.get_by_role('table',name='需求确认表')).to_be_visible()
    await j.page.reload(wait_until='networkidle')
    await expect(j.page.get_by_role('table',name='需求确认表')).to_be_visible()
    assert not j.provider.requests and not j.tasks()
    j.report['checks'].append('恢复refine需要新增授权时先弹范围确认；取消与再刷新都0提交，明确改为手动后刷新不再触发AI')


async def restore_pending_input_flow(j):
    await j.chat()
    pending='尚未发出的合成补充，不能自动算成已经提供的资料。'
    await j.work().get_by_role('textbox',name='你的想法',exact=True).fill(pending)
    await j.page.evaluate("pid=>sessionStorage.setItem('ra-connected-step-'+pid,'refine')",j.pid)
    await j.page.reload(wait_until='networkidle')
    alert=j.page.get_by_role('alert').filter(has_text='聊天框还有未发送的文字或附件。')
    await expect(alert).to_be_visible()
    assert not j.tasks() and j.calls()==0
    await alert.get_by_role('button',name='返回聊天输入',exact=True).click()
    await j.in_information()
    await expect(j.work().get_by_role('textbox',name='你的想法',exact=True)).to_have_value(pending)
    assert not j.tasks() and j.calls()==0
    j.report['checks'].append('恢复refine时未发送聊天输入保留，明确提示并能返回聊天，不暗中发送或起草')


async def restore_cancel_flow(j):
    await j.chat()
    await j.page.evaluate("pid=>sessionStorage.setItem('ra-connected-step-'+pid,'refine')",j.pid)
    j.provider.hold()
    await j.page.reload(wait_until='networkidle')
    await wait_until(j.provider.entered.is_set,'Restored task did not start')
    await j.information()
    j.provider.release.set()
    await wait_until(lambda:j.tasks()[-1]['status'] in ('succeeded','partial'),'Restored task did not settle')
    await j.page.wait_for_timeout(600)
    await j.in_information()
    assert j.calls()==1 and len(j.tasks())==1
    await j.rec.click(j.work().get_by_role('button',name='进入完善需求',exact=True).filter(visible=True))
    await j.ready()
    assert j.calls()==1 and len(j.tasks())==1
    j.report['checks'].append('恢复期间用户返回提供信息后任务完成不拉回；再次进入复用结果，不新增调用')


async def restore_draft_read_failure_flow(j):
    await j.api('/workspace-draft','POST',dict(expected_revision=j.project()['revision'],text=TEXT,entry_mode='direct'))
    before=copy.deepcopy(j.project())
    await j.page.evaluate("pid=>sessionStorage.setItem('ra-connected-step-'+pid,'refine')",j.pid)
    j.fail_next_draft_read=True
    await j.page.reload(wait_until='networkidle')
    retry=j.work().get_by_role('button',name='重新读取工作底稿',exact=True)
    await expect(retry).to_be_visible()
    panel=j.page.get_by_label('进入完善需求的准备状态',exact=True)
    await expect(panel.get_by_role('button',name='继续检查并整理',exact=True)).to_be_disabled()
    await expect(panel.get_by_role('button',name='不使用 AI，直接完善',exact=True)).to_be_disabled()
    assert j.project()==before and not j.tasks() and not j.provider.requests
    assert not any(row['method']=='POST' and row['path'].endswith('/workspace-draft') for row in j.report['api_requests'])
    await retry.click()
    dialog=j.page.get_by_role('dialog',name='核对本次任务')
    await expect(dialog).to_be_visible()
    assert not j.tasks() and not j.provider.requests and j.project()==before
    await dialog.get_by_role('button',name='返回保留输入',exact=True).click()
    await panel.get_by_role('button',name='不使用 AI，直接完善',exact=True).click()
    await expect(j.page.get_by_role('table',name='需求确认表')).to_be_visible()
    assert j.project()==before
    assert not any(row['method']=='POST' and row['path'].endswith('/workspace-draft') for row in j.report['api_requests'])
    j.report['checks'].append('恢复refine的共享底稿读取失败时禁止继续保存或手动覆盖；重读成功才继续，原文不变且0 workspace-draft写入')


async def pending_draft_read_flow(j):
    await j.chat()
    before=j.calls()
    j.draft_read_gate=asyncio.Event()
    try:
        await j.page.reload(wait_until='domcontentloaded')
        await expect(j.primary()).to_be_visible()
        await j.primary().click()
        await expect(j.work().get_by_role('alert').filter(has_text='工作底稿仍在读取，请稍候再进入完善需求。')).to_be_visible()
        assert j.calls()==before and not j.tasks()
    finally:
        j.draft_read_gate.set()
    await expect(j.work().get_by_role('button',name='进入完善需求',exact=True).filter(visible=True)).to_be_enabled()
    await j.primary().click()
    await j.ready()
    assert j.calls()==before+1 and len(j.tasks())==1
    j.report['checks'].append('底稿GET未返回时点击聊天整理入口明确提示正在读取，非静默无反应；0新任务，读取完成后一次点击正常起草')


SCENARIOS={'main':main_flow,'reuse':reuse_flow,'duplicate':duplicate_flow,'failure':failure_flow,'manual':manual_flow,'cancel_switch':cancel_switch_flow,
           'restore_refine':restore_refine_flow,'restore_authorization':restore_authorization_flow,
           'restore_pending_input':restore_pending_input_flow,'restore_cancel':restore_cancel_flow,
           'restore_draft_read_failure':restore_draft_read_failure_flow,'pending_draft_read':pending_draft_read_flow}


async def run_one(args,variant):
    output=args.output.resolve()/variant; output.mkdir(parents=True,exist_ok=True)
    report=dict(scenario=variant,status='running',checks=[],page_errors=[],api_requests=[],real_model_calls=0,
        daily_data_access=False,static_build=str(args.static_build.resolve()),preseeded_candidates=False,preseeded_preparation=False)
    recording=args.record and variant=='main'
    with tempfile.TemporaryDirectory(prefix='ra-transition-') as scratch:
        provider=FrozenProvider(Path(scratch)); app=create_app(Path(scratch)/'data',provider=provider)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=ORIGIN,timeout=30) as client:
            j=Journey(args,variant,app,provider,client,output,report)
            j.save('project-before.json')
            async with async_playwright() as p:
                browser=await p.chromium.launch(headless=True)
                options=dict(viewport={'width':1440,'height':900})
                if recording:options.update(record_video_dir=str(output/'video'),record_video_size=options['viewport'])
                context=await browser.new_context(**options)
                if recording:await context.add_init_script('('+ANNOTATION+')()')
                await context.route('**/*',j.dispatch)
                j.page=await context.new_page(); j.rec=TransitionRecorder(j.page,output,recording)
                j.page.on('pageerror',lambda e:report['page_errors'].append(str(e)))
                try:
                    await j.open(); await SCENARIOS[variant](j)
                    assert not report['page_errors']
                    report['status']='passed'
                    await j.page.screenshot(path=str(output/'final.png'))
                except Exception as error:
                    report.update(status='failed',error=str(error))
                    await j.page.screenshot(path=str(output/'failure.png'))
                    (output/'failure-body.txt').write_text(await j.page.locator('body').inner_text(),encoding='utf-8')
                finally:
                    provider.release.set()
                    await asyncio.sleep(.15)
                    j.save('project-after.json'); j.rec.save()
                    report['provider_requests']=provider.requests; report['organize_tasks']=j.tasks()
                    video=j.page.video if recording else None
                    await context.close()
                    if video:await video.save_as(str(output/'自动整理并进入完善需求.webm'))
                    await browser.close()
    (output/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report


async def main(args):
    variants=args.scenario or list(SCENARIOS)
    results=await asyncio.gather(*(run_one(args,v) for v in variants))
    summary=dict(status='passed' if all(r['status']=='passed' for r in results) else 'failed',
        tests=[{k:r.get(k) for k in ('scenario','status','checks','error')} for r in results],
        real_model_calls=0,daily_data_access=False,frozen_requests=sum(len(r['provider_requests']) for r in results))
    (args.output/'result.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False,indent=2))
    if summary['status']!='passed':raise SystemExit(1)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--static-build',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--record',action='store_true')
    parser.add_argument('--scenario',action='append',choices=list(SCENARIOS))
    asyncio.run(main(parser.parse_args()))
