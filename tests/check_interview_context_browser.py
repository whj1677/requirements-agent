"""Offline same-call interview -> summary review, with no pre-seeded proposals.

Uses the real UI/action/Provider/evidence paths through an isolated ASGI app.
All browser traffic is intercepted on the sole workbench origin. No daily data,
external model or extra listening service is used. A recording is synthetic.
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
IMPORT_SANDBOX = tempfile.TemporaryDirectory(prefix='ra-summary-import-')
os.environ['RA_DATA_DIR'] = str((Path(IMPORT_SANDBOX.name) / 'data').resolve())
from app.main import create_app
from app.provider import DEFAULT, Provider, origin
from tests.check_refine_human_flow_browser import ANNOTATION, Recorder

ORIGIN = 'http://127.0.0.1:8765'
MESSAGE = ('这是用于界面验证的合成需求。我们要在大型储能云平台新增独立的告警处理记录模块。'
           '目标是让两名场站值班人员在云端记录和查询告警处理过程。目标使用者为两名场站值班人员。'
           '本期改动为新增处理记录填写、保存和按场站查询；不做设备控制。异常和权限规则尚未确定。')
SUGGESTIONS = dict(intent='让两名场站值班人员在云端记录和查询告警处理过程。',
                   users='两名场站值班人员。', change_scope='新增处理记录填写、保存和按场站查询。')
MANUAL = '人工确认目标：只记录告警处理结果，不新增查询能力。'
UNKNOWN = '目标尚未确定，需要进一步讨论。'


class FrozenInterview(Provider):
    def __init__(self, folder, variant):
        super().__init__(env_path=folder/'absent.env', credential_dir=folder/'credentials')
        self.keys[origin(DEFAULT)] = 'OFFLINE_CONTEXT_BROWSER_ONLY'
        self.calls, self.stages, self.variant = 0, [], variant

    async def request(self, config, messages, evidence=None):
        self.calls += 1
        stage = json.loads(messages[0]['content'].split('可信任务头：')[-1])['stage']
        self.stages.append(stage)
        assert stage == 'interview', 'No organize/repair/extra request authorized in this test'
        raw = messages[1]['content']
        context = json.loads(raw if isinstance(raw, str) else raw[0]['text'])
        excerpt = next(ex for ex in reversed(context['excerpts']) if '用于界面验证的合成需求' in ex['text'])
        ref = dict(source_id=excerpt['source_id'], excerpt_id=excerpt['id'])
        value = dict(schema_version='1.1', stage=stage,
            summary='已按你本次提供的原话整理目标、使用者和改动建议，建议仍需你核对；未知权限规则保持待确认。',
            proposals=[], questions=[], findings=[], used_source_refs=[ref], limitations=[], result={'can_organize': True})
        if self.variant != 'legacy':
            draft = dict(SUGGESTIONS)
            if self.variant == 'uncertain':
                draft['intent'] = UNKNOWN
                draft['users'] = '目标用户待确认'
            if self.variant == 'manual':
                draft['preserve_scope'] = '建议保留当前告警列表。'
                draft['current_state'] = '当前告警列表已存在。'
            value['context_draft'] = {field: dict(text=text, source_refs=[ref]) for field, text in draft.items()}
        assert evidence is not None
        evidence.before_send(config)
        usage = dict(prompt_tokens=20, completion_tokens=20, total_tokens=40)
        evidence.response(content=json.dumps(value, ensure_ascii=False), http_status=200,
            finish_reason='stop', usage=usage, response_model='OFFLINE_FROZEN_CONTEXT')
        return value, dict(request_model=config['model'], response_model='OFFLINE_FROZEN_CONTEXT',
            usage=usage, origin=origin(config), finish_reason='stop')


class SummaryRecorder(Recorder):
    async def note(self, text, seconds=2.5):
        self.chapters.append(dict(seconds=round(time.monotonic()-self.started, 2), text=text))
        if self.enabled:
            await self.page.evaluate("text=>{document.getElementById('recording-note').textContent=text}",
                '录屏说明 · 非产品界面\n合成储能云项目\n离线冻结模型响应\n非真实 AI 质量验证\n\n'+text)
            await self.page.wait_for_timeout(seconds*1000)


async def until(predicate, timeout=15):
    end = time.monotonic()+timeout
    while time.monotonic() < end:
        if predicate():
            return
        await asyncio.sleep(.1)
    raise AssertionError('Expected isolated store state was not reached')


async def open_goal(page, recorder, *, saved=False):
    workspace = page.locator('.connected-workspace:visible')
    await recorder.click(workspace.get_by_role('button', name='进入完善需求', exact=True))
    readiness = page.get_by_role('region', name='需求核对准备').filter(visible=True)
    # Actual visible blocker navigation; never expand a hidden node with JavaScript.
    await recorder.click(readiness.get_by_role('button', name='查看核对清单', exact=True))
    action = readiness.get_by_role('button', name=re.compile('^核对(?:本次改动意图|希望达到的目标) →$'))
    if not saved:
        await recorder.click(action.first)
    else:
        support = page.locator('details.requirement-support')
        await recorder.click(support.locator('> summary'))
        await recorder.click(support.get_by_text('查看或修改其他内容', exact=True))
        await recorder.click(support.get_by_role('button', name='编辑业务摘要', exact=True))
        await recorder.click(support.get_by_text('适用场景与全部字段（按需）', exact=True))
        await recorder.click(support.locator('.refine-context-field-list').get_by_role('button', name=re.compile('^希望达到的目标')))
    field = page.locator('[id^="refine-context-field-"][id$="-intent"]:visible')
    await field.wait_for(state='visible')
    return field


async def scenario(args, variant):
    output = args.output.resolve()/variant
    output.mkdir(parents=True, exist_ok=True)
    recording = bool(args.record and variant == 'empty')
    report = dict(scenario=variant, status='running', checks=[], page_errors=[], api_requests=[],
        data_scope='isolated temporary app', daily_data_access=False, real_external_model_calls=0,
        initial_context_seeded=(variant=='manual'), proposal_seeded=False,
        static_build=str(args.static_build.resolve()), viewport={'width':1440,'height':900})
    page, context, browser, recorder, video = None, None, None, None, None
    with tempfile.TemporaryDirectory(prefix='ra-interview-context-') as scratch:
        folder = Path(scratch)
        provider = FrozenInterview(folder, variant)
        app = create_app(folder/'data', provider=provider)
        store = app.state.store
        name = '合成储能云：'+dict(empty='聊天摘要待确认', manual='人工摘要保护', uncertain='未知目标保持待确认', legacy='历史聊天补整理', guards='来源过期保护', loading='载入不覆盖聊天')[variant]
        p = store.create(name)
        pid = p['id']
        if variant == 'manual':
            with store.edit(pid, p['revision'], '明确披露的人工内容对照前置条件') as (p, _):
                p['product_context'] = dict(intent=MANUAL, preserve_scope='')
                p['context_field_states'] = dict(intent={'status':'known'}, preserve_scope={'status':'none','note':'人工确认无额外保持项'})
        before = copy.deepcopy(store.get(pid))
        (output/'project-before.json').write_text(json.dumps(before, ensure_ascii=False, indent=2), encoding='utf-8')
        assert not before.get('product_context_proposal')
        if variant != 'manual':
            assert not before.get('product_context') and not before.get('context_field_states')
            report['checks'].append('初始摘要、字段状态、本期范围均为空，无预置建议')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN, timeout=30) as client:
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(headless=True)
                options = dict(viewport=report['viewport'])
                if recording:
                    options.update(record_video_dir=str(output/'video'), record_video_size=report['viewport'])
                context = await browser.new_context(**options)
                if recording:
                    await context.add_init_script('('+ANNOTATION+')()')

                async def dispatch(route):
                    request = route.request
                    url = urlsplit(request.url)
                    assert url.netloc == '127.0.0.1:8765', 'External browser request forbidden: '+url.netloc
                    path = url.path
                    if path.startswith('/api/'):
                        if variant=='loading' and path.endswith('/workspace-draft') and request.method=='GET':
                            await asyncio.sleep(1.5)
                        headers = {k:v for k,v in request.headers.items() if k.lower() not in ('host','content-length','connection','accept-encoding')}
                        headers['host'] = '127.0.0.1:8765'
                        response = await client.request(request.method, path+('?' + url.query if url.query else ''), content=request.post_data_buffer, headers=headers)
                        report['api_requests'].append(dict(method=request.method,path=path,status=response.status_code))
                        await route.fulfill(status=response.status_code, content_type=response.headers.get('content-type','application/json'), body=response.content)
                    else:
                        build = args.static_build.resolve()
                        target = (build/(path.lstrip('/') or 'index.html')).resolve()
                        if target.is_relative_to(build) and target.is_file():
                            await route.fulfill(status=200, content_type=mimetypes.guess_type(target.name)[0] or 'application/octet-stream', body=target.read_bytes())
                        else:
                            await route.fulfill(status=404, body='not found')
                await context.route('**/*', dispatch)
                page = await context.new_page()
                page.on('pageerror', lambda e:report['page_errors'].append(str(e)))
                recorder = SummaryRecorder(page, output, recording)
                try:
                    await page.goto(ORIGIN+'/', wait_until='networkidle')
                    await recorder.click(page.get_by_role('button', name=name))
                    workspace = page.locator('.connected-workspace:visible')
                    if variant!='loading':
                        await expect(workspace.get_by_role('textbox', name='填写当前需求的原文底稿', exact=True)).to_be_enabled()
                    await recorder.click(workspace.get_by_role('button', name='和助手聊需求', exact=True))
                    await recorder.note('1. 新项目起点\n目标、使用者和范围均为空。先在聊天中提供已知信息。')
                    message = MESSAGE
                    if variant=='uncertain':
                        message = MESSAGE.replace('目标是让两名场站值班人员在云端记录和查询告警处理过程。','目标尚未确定，需要进一步讨论。')
                        message = message.replace('目标使用者为两名场站值班人员。','目标使用者尚未确定。')
                    if variant=='manual':
                        message += '当前告警列表已存在。新讨论建议保留当前告警列表。'
                    await recorder.fill(workspace.get_by_role('textbox', name='你的想法', exact=True), message)
                    if variant=='loading':
                        await page.wait_for_timeout(1800)
                        await expect(workspace.get_by_role('textbox',name='你的想法',exact=True)).to_have_value(message)
                        await expect(workspace.get_by_role('textbox',name='你的想法',exact=True)).to_be_visible()
                        report['checks'].append('workspace-draft 响应延迟时用户先选聊天并输入，载入完成仍保留模式与原话')
                    await recorder.click(workspace.locator('.chat-composer-actions').get_by_role('button', name='发送', exact=True))
                    await until(lambda:len([m for m in store.get(pid)['messages'] if m['role']=='assistant'])==1)
                    await page.wait_for_timeout(300)
                    after_chat = copy.deepcopy(store.get(pid))
                    (output/'project-after-chat.json').write_text(json.dumps(after_chat, ensure_ascii=False, indent=2), encoding='utf-8')
                    assert provider.calls==1 and provider.stages==['interview']
                    for key in ('product_context','context_field_states'):
                        assert after_chat.get(key)==before.get(key), key+' was silently changed by interview'
                    assert not (after_chat.get('product_context') or {}).get('scope_ids')
                    report['checks'].append('界面发送真实 interview action，冻结 Provider 仅一次；未写人工摘要、状态或范围')
                    if variant!='legacy':
                        for field,text in (dict(SUGGESTIONS,intent=UNKNOWN,users='目标用户待确认') if variant=='uncertain' else SUGGESTIONS).items():
                            assert after_chat['product_context_proposal'][field]==text
                            meta=after_chat['product_context_proposal_evidence'][field]
                            assert meta['evidence_status']=='unreviewed' and meta['source_refs']
                            for ref in meta['source_refs']:
                                source=next(s for s in after_chat['sources'] if s['id']==ref['source_id'])
                                assert any(ex['id']==ref['excerpt_id'] and '用于界面验证的合成需求' in ex['text'] for ex in source['excerpts'])
                        report['checks'].append('目标、使用者、改动同次整理为候选，并绑定本轮真实输入来源')
                    await recorder.note('2. 同一轮聊天已整理摘要\n尚未保存为人工确认。进入完善需求查看核对入口。')
                    field = await open_goal(page, recorder, saved=variant in ('manual','legacy'))
                    if variant=='legacy':
                        await expect(field.get_by_text('已有聊天或资料，尚未形成这项摘要',exact=True)).to_be_visible()
                        await recorder.click(field.get_by_role('button',name='从已有聊天和资料整理草稿',exact=True))
                        dialog=page.get_by_role('dialog',name='核对本次任务')
                        await expect(dialog).to_be_visible()
                        assert provider.calls==1
                        report['checks'].append('历史聊天无摘要时只打开整理任务核对，不自动调用模型')
                    elif variant=='manual':
                        card=field.get_by_label('希望达到的目标待核对内容')
                        await expect(card.get_by_text(MANUAL,exact=True)).to_be_visible()
                        await recorder.click(field.get_by_text('另有一份 AI 建议，尚未采用',exact=True))
                        await recorder.click(field.get_by_role('button',name='用这份建议修改',exact=True))
                        editor=field.get_by_role('textbox',name='希望达到的目标内容',exact=True)
                        await expect(editor).to_have_value(SUGGESTIONS['intent'])
                        await recorder.fill(editor,'人工编辑的未保存目标，不能被刷新冲掉。')
                        support=page.locator('details.requirement-support')
                        await recorder.click(support.locator('> summary'))
                        await expect(editor).not_to_be_visible()
                        await recorder.click(support.locator('> summary'))
                        await expect(editor).to_have_value('人工编辑的未保存目标，不能被刷新冲掉。')
                        await recorder.click(workspace.get_by_role('button',name='刷新',exact=True))
                        await expect(editor).to_have_value('人工编辑的未保存目标，不能被刷新冲掉。')
                        assert store.get(pid)['product_context']==before['product_context']
                        await recorder.click(field.get_by_role('button',name='取消本字段修改',exact=True))
                        await expect(field.get_by_text(MANUAL,exact=True)).to_be_visible()
                        assert store.get(pid)['product_context']==before['product_context']
                        assert store.get(pid)['context_field_states']==before['context_field_states']
                        report['checks'].append('当前人工内容优先；新建议只能主动选择；收起再展开与刷新均保留未保存修改，取消恢复人工内容')
                        await recorder.click(support.locator('.refine-context-field-list').get_by_role('button',name=re.compile('^保持不变')))
                        preserve=support.locator('.refine-context-field:visible')
                        await expect(preserve.locator('.refine-context-empty').get_by_text('已确认没有额外约束',exact=True)).to_be_visible()
                        assert await preserve.get_by_label('保持不变待核对内容').count()==0
                        await expect(preserve.get_by_text('另有一份 AI 建议，尚未采用',exact=True)).to_be_visible()
                        await support.locator('.refine-context-profile').get_by_role('combobox').first.select_option('new_product')
                        await recorder.click(support.get_by_role('button',name='保存适用场景',exact=True))
                        await until(lambda:store.get(pid).get('scope_profile',{}).get('scenario')=='new_product')
                        await expect(support.get_by_role('button',name='保存适用场景',exact=True)).not_to_be_visible()
                        await recorder.click(support.locator('.refine-context-field-list').get_by_role('button',name=re.compile('^当前现状')))
                        current=support.locator('.refine-context-field:visible')
                        await expect(current.locator('.refine-context-empty').get_by_text('不适用',exact=True)).to_be_visible()
                        assert await current.get_by_label('当前现状待核对内容').count()==0
                        await expect(current.get_by_text('另有一份 AI 建议，尚未采用',exact=True)).to_be_visible()
                        assert not store.get(pid)['product_context'].get('current_state')
                        assert store.get(pid)['context_field_states']['preserve_scope']['status']=='none'
                        report['checks'].append('显式 none 与新产品隐式不适用空内容仍保留；AI 建议只能另行查看，未显示为已确认正文')
                    else:
                        card=field.get_by_label('希望达到的目标待核对内容')
                        await expect(card.get_by_text('AI 已整理 · 待你确认',exact=True)).to_be_visible()
                        await expect(card).to_be_in_viewport(ratio=.8)
                        await expect(field.get_by_role('button',name=re.compile('^(确认并继续|保存建议，仍待补充)$'))).to_be_in_viewport(ratio=.9)
                        await expect(card.get_by_text(UNKNOWN if variant=='uncertain' else SUGGESTIONS['intent'],exact=True)).to_be_visible()
                        assert await field.get_by_role('textbox').count()==0, 'Editing controls appeared before opting in'
                        await expect(card.get_by_role('button',name='查看整理依据',exact=True)).to_be_visible()
                        await recorder.note('3. 先读 AI 已整理的目标\n无需重新写一遍。内容不对才点“我来修改”；来源可查看。')
                        await page.screenshot(path=str(output/'03-pending-summary.png'), full_page=False)
                        if variant=='guards':
                            session=(await client.get('/api/session')).json()
                            client.headers.update({'Origin':ORIGIN,'X-CSRF-Token':session['csrf']})
                            latest=store.get(pid)
                            source_id=latest['product_context_proposal_evidence']['intent']['source_refs'][0]['source_id']
                            response=await client.post(f'/api/projects/{pid}/sources/{source_id}/exclude',json={'expected_revision':latest['revision']})
                            assert response.status_code==200,response.text
                            await recorder.click(field.get_by_role('button',name='确认并继续',exact=True))
                            await expect(field.get_by_role('alert')).to_contain_text('来源已不可用')
                            assert not store.get(pid).get('product_context')
                            assert not store.get(pid).get('context_field_states')
                            report['checks'].append('页面停留旧建议时来源被另一客户端排除，确认前拉取最新来源并阻止写入')
                        elif variant in ('empty','loading'):
                            assert after_chat['product_context_proposal_evidence']['intent']['uncertain'] is False, 'Unrelated unknown permissions incorrectly marked goal unknown'
                            await recorder.click(card.get_by_role('button',name='我来修改',exact=True))
                            await expect(field.get_by_role('textbox',name='希望达到的目标内容',exact=True)).to_have_value(SUGGESTIONS['intent'])
                            await recorder.click(field.get_by_role('button',name='返回查看',exact=True))
                            assert not store.get(pid).get('product_context')
                            await recorder.note('4. 这里演示查看编辑，再返回\n未改内容不写入。现在明确确认这项目标。')
                            await recorder.click(field.get_by_role('button',name='确认并继续',exact=True))
                            await until(lambda:(store.get(pid).get('context_field_states') or {}).get('intent',{}).get('status')=='known')
                            updated=store.get(pid)
                            assert updated['product_context']=={'intent':SUGGESTIONS['intent']}
                            assert set(updated['context_field_states'])=={'intent'}
                            assert not updated['product_context'].get('scope_ids')
                            await expect(page.locator('.refine-current-heading h3:visible')).not_to_have_text('希望达到的目标')
                            await expect(page.locator('.refine-current-heading:visible')).to_be_in_viewport(ratio=.8)
                            report['checks'].append('只读建议可进入编辑并返回；明确确认仅保存目标，其他摘要及本期范围仍未确认')
                            await recorder.note('5. 目标已确认\n界面继续处理下一项。使用者、范围等仍须各自核对，未被一并确认。',4)
                        else:
                            assert after_chat['product_context_proposal_evidence']['intent']['uncertain'] is True
                            await recorder.click(field.get_by_role('button',name='保存建议，仍待补充',exact=True))
                            await until(lambda:(store.get(pid).get('product_context') or {}).get('intent')==UNKNOWN)
                            assert store.get(pid)['context_field_states']['intent']['status']=='unknown'
                            await expect(field.get_by_text(re.compile('文字已记录，仍待确认'))).to_be_visible()
                            report['checks'].append('未知目标可以保存文字但保持 unknown，仍留当前项等待补充')
                            await recorder.click(page.get_by_role('navigation',name='完善需求待办').get_by_role('button',name=re.compile('目标使用者')))
                            user_field=page.locator('[id^="refine-context-field-"][id$="-users"]:visible')
                            await expect(user_field.get_by_label('目标使用者待核对内容')).to_contain_text('目标用户待确认')
                            await recorder.click(user_field.get_by_role('button',name='我来修改',exact=True))
                            await recorder.fill(user_field.get_by_role('textbox',name='目标使用者内容',exact=True),'目标用户待确认')
                            await recorder.click(user_field.get_by_role('button',name=re.compile('^(确认并继续|保存建议，仍待补充|保存为待确认)$')))
                            await until(lambda:(store.get(pid).get('product_context') or {}).get('users')=='目标用户待确认')
                            assert store.get(pid)['context_field_states']['users']['status']=='unknown'
                            report['checks'].append('目标用户待确认的候选即使进入编辑保存也不升级为 known')
                    await page.screenshot(path=str(output/'04-final.png'),full_page=False)
                    assert provider.calls==1 and not report['page_errors']
                    report.update(status='passed',frozen_provider_calls=provider.calls,stages=provider.stages)
                except Exception as error:
                    report.update(status='failed',error=str(error),frozen_provider_calls=provider.calls,stages=provider.stages)
                    await page.screenshot(path=str(output/'failure.png'),full_page=False)
                    (output/'failure-body.txt').write_text(await page.locator('body').inner_text(),encoding='utf-8')
                finally:
                    (output/'project-after.json').write_text(json.dumps(store.get(pid),ensure_ascii=False,indent=2),encoding='utf-8')
                    recorder.save()
                    if recording:
                        video=page.video
                    await context.close()
                    if video:
                        await video.save_as(str(output/'聊天摘要确认操作.webm'))
                    await browser.close()
    (output/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report


async def main(args):
    variants=args.scenario or ['empty','manual','uncertain','legacy','guards','loading']
    results=await asyncio.gather(*(scenario(args,v) for v in variants))
    summary=dict(status='passed' if all(r['status']=='passed' for r in results) else 'failed',
        tests=[dict(scenario=r['scenario'],status=r['status'],checks=r['checks'],error=r.get('error')) for r in results],
        real_external_model_calls=0,frozen_provider_calls=sum(r.get('frozen_provider_calls',0) for r in results),daily_data_access=False)
    (args.output/'result.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False,indent=2))
    if summary['status']!='passed':
        raise SystemExit(1)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--static-build',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--record',action='store_true')
    parser.add_argument('--scenario',action='append',choices=['empty','manual','uncertain','legacy','guards','loading'])
    asyncio.run(main(parser.parse_args()))
