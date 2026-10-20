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
    workspace=page.locator('.connected-workspace:visible')
    await recorder.click(workspace.get_by_role('button',name='不使用 AI，直接完善',exact=True))
    table=workspace.get_by_role('table',name='需求确认表',exact=True)
    await expect(table).to_be_visible()
    field=table.locator('tr[data-cell-key="context-intent"]')
    await field.get_by_role('textbox',name='希望达到的目标',exact=True).scroll_into_view_if_needed()
    return field


async def add_manual_requirement(page, workspace, store, pid):
    # Real UI creation supplies the minimum content needed for an atomic review;
    # it is disclosed synthetic input, never a pre-seeded model accomplishment.
    await workspace.get_by_role('button',name='补充需求',exact=True).first.click()
    dialog=page.get_by_role('dialog',name='补充候选需求',exact=True)
    await dialog.get_by_role('textbox',name='需求名称',exact=True).fill('合成告警处理记录')
    await dialog.get_by_role('textbox',name='需求原文',exact=True).fill('场站值班人员在云平台填写并保存告警处理记录，保存后显示本次处理内容。')
    await dialog.get_by_role('button',name='保存候选',exact=True).click()
    await expect(dialog).not_to_be_visible()
    await until(lambda:bool(store.get(pid)['items']))
    table=workspace.get_by_role('table',name='需求确认表',exact=True)
    await table.get_by_role('combobox',name='需求类型',exact=True).select_option('new_module')
    await table.get_by_role('combobox',name='模块关联',exact=True).select_option('independent')
    await table.get_by_role('combobox',name='合成告警处理记录 本期范围',exact=True).select_option('included')
    await table.get_by_role('combobox',name='合成告警处理记录 本次改动',exact=True).select_option('new')
    await table.get_by_role('textbox',name='合成告警处理记录 验收建议 1',exact=True).fill('场站值班人员填写处理内容并保存后，页面显示刚保存的内容。')
    return table


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
                    await expect(workspace.get_by_role('button',name='和助手聊需求',exact=True)).to_have_attribute('aria-pressed','true')
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
                    progress=page.get_by_role('complementary',name='AI 任务进度',exact=True)
                    if await progress.is_visible():
                        compact=progress.locator('.ai-progress-compact')
                        await expect(compact).to_be_visible(timeout=15000)
                        await compact.click()
                        await progress.get_by_role('button',name='关闭进度提示',exact=True).click()
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
                    table=workspace.get_by_role('table',name='需求确认表',exact=True)
                    goal=field.get_by_role('textbox',name='希望达到的目标',exact=True)
                    if variant=='legacy':
                        await expect(goal).to_have_value('')
                        await expect(field).to_contain_text('暂无内容')
                        await workspace.get_by_role('button',name='AI 补充建议',exact=True).click()
                        dialog=page.get_by_role('dialog',name='核对本次任务',exact=True)
                        await expect(dialog).to_be_visible()
                        assert provider.calls==1
                        await dialog.get_by_role('button',name='返回保留输入',exact=True).click()
                        assert store.get(pid)==after_chat
                        report['checks'].append('历史聊天无摘要时保留空内容；主动起草先核对任务，取消不调用也不写入')
                    elif variant=='manual':
                        await expect(goal).to_have_value(MANUAL)
                        await expect(table.get_by_role('textbox',name='保持不变',exact=True)).to_have_value('')
                        await expect(table.get_by_role('combobox',name='保持不变 状态',exact=True)).to_have_value('none')
                        edited='人工编辑的未保存目标，不能被刷新冲掉。'
                        await goal.fill(edited)
                        await workspace.get_by_role('button',name='保存草稿',exact=True).click()
                        await page.reload(wait_until='networkidle')
                        await expect(workspace.get_by_role('button',name='和助手聊需求',exact=True)).to_have_attribute('aria-pressed','true')
                        await open_goal(page,recorder,saved=True)
                        await expect(goal).to_have_value(edited)
                        await table.get_by_role('combobox',name='需求类型',exact=True).select_option('new_product')
                        # A populated AI suggestion is still a proposal; the
                        # human can explicitly mark this optional field inapplicable.
                        await table.get_by_role('textbox',name='当前现状',exact=True).fill('')
                        await table.get_by_role('combobox',name='当前现状 状态',exact=True).select_option('not_applicable')
                        await expect(table.get_by_role('combobox',name='当前现状 状态',exact=True)).to_have_value('not_applicable')
                        assert store.get(pid)==after_chat
                        assert store.get(pid)['product_context']==before['product_context']
                        assert store.get(pid)['context_field_states']==before['context_field_states']
                        report['checks'].append('人工目标和显式none优先，AI不覆盖；本地编辑、保存刷新保持精确文字，新产品空现状为不适用且未写业务数据')
                    elif variant=='uncertain':
                        await expect(goal).to_have_value(UNKNOWN)
                        await expect(table.get_by_role('combobox',name='希望达到的目标 状态',exact=True)).to_have_value('unknown')
                        user=table.get_by_role('textbox',name='目标使用者',exact=True)
                        await expect(user).to_have_value('目标用户待确认')
                        await expect(table.get_by_role('combobox',name='目标使用者 状态',exact=True)).to_have_value('unknown')
                        await workspace.get_by_role('button',name='保存草稿',exact=True).click()
                        await page.reload(wait_until='networkidle');await open_goal(page,recorder)
                        await expect(goal).to_have_value(UNKNOWN)
                        await expect(table.get_by_role('combobox',name='目标使用者 状态',exact=True)).to_have_value('unknown')
                        assert store.get(pid)==after_chat
                        report['checks'].append('不确定的目标及使用者可保存为本地草稿，刷新仍unknown，未被保存动作升级为人工确认')
                    else:
                        await expect(goal).to_have_value(SUGGESTIONS['intent'])
                        await expect(goal).to_be_in_viewport(ratio=.8)
                        await expect(field).to_contain_text('AI 整理 · 待你确认')
                        assert after_chat['product_context_proposal_evidence']['intent']['uncertain'] is False
                        await field.get_by_role('button',name='查看来源',exact=True).click()
                        sources=page.get_by_role('dialog',name='项目资料',exact=True)
                        await expect(sources).to_contain_text('用于界面验证的合成需求')
                        attachment=page.get_by_role('dialog',name='附件内容',exact=True)
                        if await attachment.is_visible():await attachment.get_by_role('button',name='关闭附件',exact=True).click()
                        await sources.get_by_role('button',name='关闭项目资料',exact=True).click()
                        assert store.get(pid)==after_chat
                        await page.screenshot(path=str(output/'03-pending-summary.png'))
                        table=await add_manual_requirement(page,workspace,store,pid)
                        pending=copy.deepcopy(store.get(pid))
                        assert pending.get('product_context')==before.get('product_context')
                        assert pending.get('context_field_states')==before.get('context_field_states')
                        if variant=='guards':
                            session=(await client.get('/api/session')).json()
                            client.headers.update({'Origin':ORIGIN,'X-CSRF-Token':session['csrf']})
                            source_id=pending['product_context_proposal_evidence']['intent']['source_refs'][0]['source_id']
                            response=await client.post(f'/api/projects/{pid}/sources/{source_id}/exclude',json={'expected_revision':pending['revision']})
                            assert response.status_code==200,response.text
                            excluded=copy.deepcopy(store.get(pid))
                            await workspace.locator('.refinement-sheet-footer .primary').click()
                            await expect(workspace.locator('.sheet-banner-error[role="alert"]')).to_be_visible()
                            assert any(r['status']==409 and r['path'].endswith('/refinement-confirmation') for r in report['api_requests'])
                            assert store.get(pid)==excluded
                            report['checks'].append('另一客户端排除候选来源并改变版本后，旧表确认被服务端拒绝；摘要、字段状态和范围均未被旧页确认')
                        else:
                            await workspace.locator('.refinement-sheet-footer .primary').click()
                            await expect(workspace.get_by_role('heading',name='确认交付版本',exact=True)).to_be_visible()
                            updated=store.get(pid)
                            for key,text in SUGGESTIONS.items():
                                assert updated['product_context'][key]==text
                                assert updated['context_field_states'][key]['status']=='known'
                            assert len(updated['product_context']['scope_ids'])==1
                            assert len([r for r in report['api_requests'] if r['path'].endswith('/refinement-confirmation')])==1
                            report['checks'].append('候选先同表展示来源且不写人工摘要；明确补充合成需求并整表确认后，摘要和范围一次原子保存，不再使用旧逐字段确认')
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
