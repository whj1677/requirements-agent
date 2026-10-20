"""Offline fault injection for partial business-draft review in the real UI.

The accepted/rejected report is an explicit synthetic fixture, not a model or
adapter accomplishment. Actual isolated ASGI preview/plan routes are exercised;
Provider.request is forbidden. No daily project is opened or modified.
"""
import argparse
import asyncio
import copy
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from playwright.async_api import async_playwright, expect

from tests.check_requirement_entry_browser import (
    Journey, ORIGIN, RID, OTHER, TITLE, NAME, BEFORE, AFTER, AC, ANNOTATION,
    DEFAULT, origin, create_app, now,
)
from tests.check_refine_human_flow_browser import Recorder
from tests.check_refine_workspace_browser import NoModelProvider

OTHER_TITLE = '电价历史版本查询'
CANDIDATE = 'REV-BUSINESS-DRAFT-ACCEPTED'
NEW_AC = 'AC-BUSINESS-DRAFT-ACCEPTED'
BAD = '坏稿标记：所有角色可直接删除任意园区的全部模板。'
MANUAL = AFTER + ' 人工暂存：先核对本期模板列表。'
REASON = '引用资料 SRC-NOT-IN-PROJECT 不存在，本条新建议未载入。'


class FaultRecorder(Recorder):
    async def note(self, text, seconds=2.8):
        import time
        self.chapters.append(dict(seconds=round(time.monotonic()-self.started, 2), text=text))
        if self.enabled:
            await self.page.evaluate("text=>{document.getElementById('recording-note').textContent=text}",
                '离线故障注入\n合成储能云电价资料\n预置成功/失败报告\n0 真实模型请求\n非业务验收\n\n' + text)
            await self.page.wait_for_timeout(seconds * 1000)


class FaultJourney(Journey):
    def __init__(self, *args):
        super().__init__(*args)
        p = self.project()
        refs = copy.deepcopy(p['items'][0]['source_refs'])
        def field(text, basis='fact', cited=True):
            return dict(text=text, basis=basis, source_refs=refs if cited else [])
        with self.store.edit(self.pid, p['revision'], '离线故障注入：一条可核对，一条来源错误') as (p, _):
            candidate = copy.deepcopy(next(i for i in p['items'] if i['id'] == RID))
            candidate.update(id=CANDIDATE, action='revise', target_item_id=RID,
                statement=AFTER, selection_status='candidate', change_type='new',
                behavior=dict(actor='电价管理员', entry='从云平台电价管理进入电价模板列表',
                    flow='查看电价模板列表', data='模板名称和所属地区', permissions='',
                    exceptions='', result='显示已保存模板的名称和所属地区'))
            p['items'].append(candidate)
            p['items'].append(dict(id=NEW_AC, kind='acceptance', title='模板列表显示验收',
                statement=AC, applies_to='to_be', epistemic_status='inferred', source_refs=refs,
                related_refs=[RID], selection_status='candidate', content_version=1,
                revision=p['revision'], change_type='unspecified'))
            p['business_draft_reviews']=[dict(run_id='RUN-OFFLINE-OLD-UNBOUND',
                accepted=[], rejected=[dict(reason='历史新需求缺少有效引用，未绑定任何原需求。',
                    raw=dict(title='历史未归属的合成草稿', statement='仅用于入口保护测试。'))]),
                dict(run_id='RUN-OFFLINE-FAULT-INJECTION',
                accepted=[dict(requirement_id=RID, candidate_temp_id=CANDIDATE,
                    field_evidence=dict(statement=field(AFTER),
                        behavior=dict(actor=field('电价管理员'), permissions=field('', 'unknown', False)),
                        acceptance=[field(AC, 'recommendation')]))],
                rejected=[dict(requirement_id=OTHER, reason=REASON,
                    raw=dict(title={'malformed': OTHER_TITLE}, statement=BAD,
                        source_refs=[dict(source_id='SRC-NOT-IN-PROJECT', excerpt_id='EX-MISSING')]))],
                summary_rejected=[])]

    def other_editor(self):
        return self.work().get_by_role('region', name=OTHER_TITLE+' 需求确认', exact=True)


async def journey(j):
    initial=copy.deepcopy(j.project())
    table=j.work().get_by_role('table',name='需求确认表',exact=True)
    await table.locator('.sheet-draft-review > summary').click()
    package=table.get_by_role('region',name='AI 草稿整理结果',exact=True)
    await expect(package).to_contain_text('1 条草稿可核对，1 条未载入')
    await package.scroll_into_view_if_needed()
    await j.page.screenshot(path=str(j.output/'01-partial-status.png'))
    j.report['checks'].append('当前单表保留部分整理结果提示，明确1条可核对和1条未载入，默认折叠不挤占笔记本高度')
    content=table.get_by_role('textbox',name=TITLE+' 需求内容',exact=True)
    await expect(content).to_have_value(AFTER)
    row=table.locator('tr[data-cell-key="requirement-'+RID+'-statement"]')
    await row.get_by_text('核对本版 AI 草稿的依据与待定内容',exact=True).click()
    evidence=row.locator('.business-draft-row-review')
    for label in ('引用资料整理 · 待核对','AI 推荐 · 待确认','资料未明确 · 待决定'):
        await expect(evidence.get_by_text(label,exact=True).first).to_be_visible()
    await evidence.get_by_role('button',name='查看这项来源',exact=True).first.click()
    sources=j.page.get_by_role('dialog',name='项目资料',exact=True)
    await expect(sources).to_contain_text('合成电价入口验收材料')
    await expect(sources).to_contain_text('电价管理员从云平台电价管理进入电价模板列表')
    attachment=j.page.get_by_role('dialog',name='附件内容',exact=True)
    if await attachment.is_visible(): await attachment.get_by_role('button',name='关闭附件',exact=True).click()
    await sources.get_by_role('button',name='关闭项目资料',exact=True).click()
    j.report['checks'].append('当前候选版本依据区分事实/推荐/未知，来源能打开真实合成原文，查看未采纳条款')
    await content.fill(MANUAL)
    await package.get_by_text('查看未载入条目与处理入口',exact=True).click()
    await expect(package.get_by_text('未能识别的需求条目',exact=True)).to_be_visible()
    await package.get_by_role('button',name='到这条需求处理',exact=True).click()
    failed=table.locator('tr[data-cell-key="requirement-'+OTHER+'-statement"]')
    old=next(i for i in initial['items'] if i['id']==OTHER)['statement']
    field=failed.get_by_role('textbox',name=OTHER_TITLE+' 需求内容',exact=True)
    await expect(field).to_be_in_viewport(ratio=.8)
    await expect(field).to_be_focused()
    await expect(field).to_have_value(old)
    await expect(failed).to_contain_text('本条新建议未载入，已有内容保留')
    assert BAD not in await failed.inner_text()
    await expect(content).to_have_value(MANUAL)
    assert j.project()==initial
    j.report['checks'].append('失败入口在同表直接定位并聚焦正确原条款，坏稿不混入；表内跳转保留人工输入且未写项目')
    await failed.get_by_role('button',name='只重新整理这条需求',exact=True).click()
    dialog=j.page.get_by_role('dialog',name='核对本次任务',exact=True)
    await expect(dialog).to_be_visible()
    plans=[r for r in j.report['api_requests'] if r['path'].endswith('/actions/plan')]
    assert len(plans)==1 and plans[0]['body']['target']==dict(kind='item',id=OTHER)
    assert plans[0]['body']['action']=='clarify' and j.provider.calls==0
    await dialog.get_by_role('button',name='返回保留输入',exact=True).click()
    await expect(content).to_have_value(MANUAL)
    await j.page.reload(wait_until='networkidle')
    await expect(j.work().get_by_role('button',name='和助手聊需求',exact=True)).to_have_attribute('aria-pressed','true')
    await j.work().get_by_role('button',name='不使用 AI，直接完善',exact=True).click()
    await expect(content).to_have_value(MANUAL)
    await expect(field).to_have_value(old)
    assert j.project()==initial and j.provider.calls==0
    j.report['checks'].append('重试只计划失败行，先保存当前浏览器草稿；取消和刷新后原人工文字保留，0真实模型、0业务写入')
    await table.locator('.sheet-draft-review > summary').click()
    await package.get_by_text('此前未载入的新需求记录 · 1 条',exact=True).click()
    history=package.locator('.business-draft-rejected').filter(has=j.page.get_by_text('历史未归属的合成草稿',exact=True))
    await history.get_by_role('button',name='补充需求',exact=True).click()
    create=j.page.get_by_role('dialog',name='补充候选需求',exact=True)
    await expect(create).to_be_visible()
    await expect(create.get_by_role('textbox',name='需求名称',exact=True)).to_have_value('')
    await create.get_by_role('button',name='取消',exact=True).click()
    assert j.project()==initial and j.provider.calls==0
    assert not [r for r in j.report['api_requests'] if r['method']=='POST' and r['path'].endswith('/actions')]
    j.report['checks'].append('历史无归属坏稿可安全补充，表单不带入坏稿，取消无写入；对象型错误标题未使页面崩溃')
    # A complete current-table submission must carry only visible original or
    # human text, never the rejected raw payload.
    await table.get_by_role('textbox',name=TITLE+' 验收建议 1',exact=True).fill(AC)
    await j.work().locator('.refinement-sheet-footer .primary').click()
    await expect(j.work().get_by_role('heading',name='确认交付版本',exact=True)).to_be_visible()
    writes=[r for r in j.report['api_requests'] if r['path'].endswith('/refinement-confirmation')]
    assert len(writes)==1 and writes[0]['status']==200
    payload=writes[0]['body']
    assert BAD not in json.dumps(payload,ensure_ascii=False)
    submitted=next(r for r in payload['rows'] if r['requirement_id']==OTHER)
    assert submitted['statement']==old and not submitted.get('candidate_id')
    assert next(i for i in j.project()['items'] if i['id']==OTHER)['statement']==old
    j.report['checks'].append('实际整表确认请求及保存结果只含失败行原文，无坏稿或伪造候选；人工确认前项目始终未改变')


async def run(args):
    output = args.output.resolve(); output.mkdir(parents=True, exist_ok=True)
    report = dict(status='running', checks=[], issues=[], page_errors=[], api_requests=[],
        real_model_calls=0, daily_data_access=False, product_acceptance='NOT_RUN',
        fixture='离线故障注入：预置 1 accepted + 1 rejected 报告，非真实模型生成',
        static_build=str(args.static_build.resolve()))
    with tempfile.TemporaryDirectory(prefix='ra-business-draft-browser-') as scratch:
        provider=NoModelProvider(Path(scratch))
        provider.keys[origin(DEFAULT)]='OFFLINE_BUSINESS_DRAFT_NO_TRANSPORT'
        app=create_app(Path(scratch)/'data', provider=provider)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN, timeout=30) as client:
            j=FaultJourney(args, 'entry', app, provider, client, output, report)
            j.save('project-before.json')
            async with async_playwright() as p:
                browser=await p.chromium.launch(headless=True)
                options=dict(viewport=dict(width=1440,height=900))
                if args.record: options.update(record_video_dir=str(output/'video'), record_video_size=options['viewport'])
                context=await browser.new_context(**options)
                if args.record: await context.add_init_script('('+ANNOTATION+')()')
                await context.route('**/*', j.dispatch)
                j.page=await context.new_page();j.rec=FaultRecorder(j.page, output, args.record)
                j.page.on('pageerror', lambda error: report['page_errors'].append(str(error)))
                try:
                    await j.open();await journey(j)
                    assert not report['page_errors']
                    assert not report['issues'], '; '.join(report['issues'])
                    report['status']='passed'
                except Exception as error:
                    report.update(status='failed', error=str(error))
                    await j.page.screenshot(path=str(output/'failure.png'))
                    (output/'failure-body.json').write_text(json.dumps(dict(text=await j.page.locator('body').inner_text()), ensure_ascii=False, indent=2), encoding='utf-8')
                finally:
                    j.save('project-after.json');j.rec.save()
                    report.update(provider_requests=provider.calls, tasks=j.store.records(j.pid,'user_task'))
                    video=j.page.video if args.record else None
                    await context.close()
                    if video: await video.save_as(str(output/'离线故障注入_部分草稿处理.webm'))
                    await browser.close()
    (output/'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k:report[k] for k in ('status','checks','real_model_calls','provider_requests','product_acceptance')},ensure_ascii=False,indent=2))
    if report['status']!='passed':
        print(report['error']);raise SystemExit(1)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--static-build', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--record', action='store_true')
    asyncio.run(run(parser.parse_args()))
