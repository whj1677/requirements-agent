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
    initial = copy.deepcopy(j.project())
    package = j.work().get_by_role('region', name='AI 草稿整理结果', exact=True)
    await expect(package).to_contain_text('1 条草稿可核对，1 条未载入')
    await package.scroll_into_view_if_needed()
    await j.rec.note('1. 部分结果可用\n一条草稿可核对，另一条未载入。坏稿没有覆盖原需求。')
    await j.page.screenshot(path=str(j.output/'01-partial-status.png'))
    j.report['checks'].append('表格明确显示可核对 1 条、未载入 1 条')

    first = j.work().locator('tr[data-requirement-id="'+RID+'"]')
    if await first.get_by_role('button', name='查看与确认', exact=True).count():
        await j.rec.click(first.get_by_role('button', name='查看与确认', exact=True))
    editor = j.editor()
    await expect(editor.get_by_role('textbox', name=TITLE+' 需求内容', exact=True)).to_have_value(AFTER)
    await j.rec.click(editor.get_by_text('核对本版 AI 草稿的依据与待定内容', exact=True))
    evidence = editor.locator('.business-draft-row-review')
    for label in ('引用资料整理 · 待核对', 'AI 推荐 · 待确认', '资料未明确 · 待决定'):
        await expect(evidence.get_by_text(label, exact=True).first).to_be_visible()
    await j.rec.note('2. 区分资料事实、AI 推荐与未知\n这些标签都不表示已被人工采纳。')
    await j.page.screenshot(path=str(j.output/'02-evidence-basis.png'))
    await j.rec.click(evidence.get_by_role('button', name='查看这项来源', exact=True).first)
    sources = j.page.get_by_role('dialog', name='项目资料', exact=True)
    await expect(sources).to_be_visible()
    await expect(sources).to_contain_text('合成电价入口验收材料')
    await expect(sources).to_contain_text('电价管理员从云平台电价管理进入电价模板列表')
    await j.rec.note('3. 查看实际引用资料\n来源抽屉能核对文字；看过来源不会确认需求。')
    await j.page.screenshot(path=str(j.output/'03-source-drawer.png'))
    await j.rec.click(j.page.get_by_role('button', name='关闭附件', exact=True))
    await j.rec.click(sources.get_by_role('button', name='关闭项目资料', exact=True))
    j.report['checks'].append('成功行三种依据标签真实显示，来源按钮打开对应合成资料与原文')

    version = editor.get_by_role('combobox', name=TITLE+' 建议版本', exact=True)
    await version.select_option('')
    await expect(editor.get_by_role('textbox', name=TITLE+' 需求内容', exact=True)).to_have_value(BEFORE)
    assert await editor.get_by_text('核对本版 AI 草稿的依据与待定内容', exact=True).count() == 0
    await version.select_option(CANDIDATE)
    await expect(editor.get_by_text('核对本版 AI 草稿的依据与待定内容', exact=True)).to_be_visible()
    j.report['checks'].append('切到原条款不显示 AI 版本依据；切回准确 candidate_id 才显示对应依据')

    content = editor.get_by_role('textbox', name=TITLE+' 需求内容', exact=True)
    await j.rec.fill(content, MANUAL)
    await j.rec.click(package.get_by_text('查看未载入条目与处理入口', exact=True))
    await expect(package.get_by_text('未能识别的需求条目', exact=True)).to_be_visible()
    locate = package.get_by_role('button', name='到这条需求处理', exact=True)
    await j.rec.click(locate)
    guard = j.page.get_by_role('dialog', name='未保存的修改', exact=True)
    await expect(guard).to_be_visible()
    await j.rec.note('4. 有人工修改时跳转先保护草稿\n选择留在当前页，刚输入的文字仍完整保留。')
    await j.rec.click(guard.get_by_role('button', name='留在当前页', exact=True))
    await expect(content).to_have_value(MANUAL)
    await j.rec.click(locate)
    await j.rec.click(guard.get_by_role('button', name='保存后离开', exact=True))
    failed = j.other_editor()
    await expect(failed).to_be_visible()
    failed_heading = failed.get_by_role('heading', name=OTHER_TITLE, exact=True)
    try:
        await expect(failed_heading).to_be_in_viewport(ratio=.8)
    except AssertionError:
        # Continue independent checks, but never turn the navigation failure green.
        j.report['issues'].append('失败入口展开了对应行，但标题不在视口内，用户看不到处理位置')
        await j.page.screenshot(path=str(j.output/'location-not-visible.png'))
        await j.rec.note('诊断继续：定位没有滚动到目标\n已记为失败；本段手动滚动仅为检查后续操作。')
        await failed_heading.scroll_into_view_if_needed()
    old = next(i for i in initial['items'] if i['id'] == OTHER)['statement']
    await expect(failed.get_by_role('textbox', name=OTHER_TITLE+' 需求内容', exact=True)).to_have_value(old)
    await expect(failed).to_contain_text('本条新建议未载入，已有内容保留')
    assert BAD not in await failed.inner_text()
    assert j.project()['items'] == initial['items']
    await j.rec.note('5. 定位失败的原需求\n只显示原有内容和失败原因；未载入的坏稿不能混入确认。')
    await j.page.screenshot(path=str(j.output/'05-rejected-original.png'))
    j.report['checks'].append('失败入口受到未保存保护；保存后定位正确原行，已输入内容仅保存浏览器草稿，未改项目')

    await j.rec.click(failed.get_by_role('button', name='查看确认内容', exact=True))
    preview = failed.get_by_role('region', name='本条确认预览', exact=True)
    await expect(preview).to_be_visible()
    await expect(preview).to_contain_text(old)
    assert BAD not in await preview.inner_text()
    preview_call = next(r for r in reversed(j.report['api_requests']) if r['path'].endswith('/requirement-confirmation/preview'))
    assert preview_call['status'] == 200 and preview_call['body']['requirement_id'] == OTHER
    assert preview_call['body']['statement'] == old and not preview_call['body'].get('candidate_id')
    assert BAD not in json.dumps(preview_call['body'], ensure_ascii=False)
    await j.rec.note('6. 实际确认预览只包含原内容\n本段不点击确认，也不把坏稿转成有效候选。')
    await j.page.screenshot(path=str(j.output/'06-original-only-preview.png'))
    await j.rec.click(preview.get_by_role('button', name='返回修改', exact=True))
    j.report['checks'].append('真实 ASGI 预览请求及返回只含失败行旧正文/原验收，无坏稿、无 candidate_id、未提交确认')

    await j.rec.click(failed.get_by_role('button', name='只重新整理这条需求', exact=True))
    dialog = j.page.get_by_role('dialog', name='核对本次任务', exact=True)
    await expect(dialog).to_be_visible()
    plans = [r for r in j.report['api_requests'] if r['path'].endswith('/actions/plan')]
    assert len(plans) == 1 and plans[0]['body']['target'] == dict(kind='item', id=OTHER)
    assert plans[0]['body']['action'] == 'clarify'
    assert j.provider.calls == 0
    await j.rec.note('7. 重试只针对失败的这一行\n进入范围核对后取消；没有向模型发送请求。')
    await j.page.screenshot(path=str(j.output/'07-targeted-retry-plan.png'))
    await j.rec.click(dialog.get_by_role('button', name='返回保留输入', exact=True))
    assert not [r for r in j.report['api_requests'] if r['method']=='POST' and r['path'].endswith('/actions')]
    await j.rec.click(first.get_by_role('button', name='查看与确认', exact=True))
    await expect(content).to_have_value(MANUAL)
    await j.rec.note('8. 返回第一行，人工草稿仍在\n离线故障注入验证结束：0 模型请求，0 人工采纳。')
    await j.page.screenshot(path=str(j.output/'08-draft-restored.png'))
    assert j.project()['items'] == initial['items'] and j.project()['sources'] == initial['sources']
    assert j.provider.calls == 0
    j.report['checks'].append('单行重试仅生成失败行 clarify 计划，取消后 0 模型请求；返回成功行仍保留人工草稿')
    await j.rec.click(package.get_by_text('此前未载入的新需求记录 · 1 条', exact=True))
    history = package.locator('details').filter(has=j.page.get_by_text('历史未归属的合成草稿', exact=True))
    await expect(history.get_by_text('历史未归属的合成草稿', exact=True)).to_be_visible()
    await j.rec.click(history.get_by_role('button', name='补充需求', exact=True))
    create = j.page.get_by_role('dialog', name='补充候选需求', exact=True)
    await expect(create).to_be_visible()
    await expect(create.get_by_role('textbox', name='需求名称', exact=True)).to_have_value('')
    await j.rec.note('9. 历史无归属坏稿仍可查看与处理\n新建入口不自动带入坏稿，取消不写入。')
    await j.rec.click(create.get_by_role('button', name='取消', exact=True))
    assert j.project()['items'] == initial['items'] and j.provider.calls == 0
    j.report['checks'].append('对象型错误标题不会使 React 崩溃；历史无 requirement_id 记录仍有安全补充入口，取消无写入')


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
