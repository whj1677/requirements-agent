"""Laptop refinement regression over isolated ASGI; no listener or model calls.

The six-requirement storage-cloud fixture is synthetic UI evidence only.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tests.check_answer_implementation_browser import enter, run
from tests.check_detail_prd_browser import seed, TITLE

VIEWPORTS = ((1920, 1080), (1536, 864), (1280, 720), (1280, 640))


def laptop_fixture(store):
    pid = seed(store)
    with store.edit(pid, store.get(pid)['revision'], '合成笔记本六需求核对前置') as (project, _):
        project['name'] = '合成储能云：笔记本六需求核对'
        req = next(item for item in project['items'] if item['id'] == 'REQ-0001')
        field = copy.deepcopy(req['detail_contract']['fields'][0])
        req['detail_contract']['fields'] = [dict(copy.deepcopy(field), id='F'+str(n),
            name='告警级别' if n == 1 else '合成告警字段 '+str(n)) for n in range(1, 10)]
        candidate = next(item for item in project['items'] if item['id'] == 'REQ-DETAIL-CANDIDATE')
        candidate['detail_contract'] = copy.deepcopy(req['detail_contract'])
        acceptance = next(item for item in project['items'] if item['id'] == 'AC-0001')
        for number in range(2, 7):
            item, criterion = copy.deepcopy(req), copy.deepcopy(acceptance)
            item.update(id='REQ-LAPTOP-'+str(number), title='合成储能需求 '+str(number),
                        related_refs=['AC-LAPTOP-'+str(number)])
            criterion.update(id='AC-LAPTOP-'+str(number), title='合成需求验收 '+str(number),
                             related_refs=[item['id']])
            project['items'].extend((item, criterion))
        project['product_context']['scope_ids'] = ['REQ-0001']+['REQ-LAPTOP-'+str(n) for n in range(2, 7)]
    return store.get(pid)


async def layout_metrics(page, work, label, report):
    metrics = await work.evaluate('''(work) => {
        const rect = element => { const r = element.getBoundingClientRect();
            return {x:r.x,y:r.y,width:r.width,height:r.height,bottom:r.bottom,right:r.right}; };
        const scroll = work.querySelector('.refinement-sheet-scroll');
        const footer = work.querySelector('.refinement-sheet-footer');
        return {viewport:{width:innerWidth,height:innerHeight},
            table:{...rect(scroll),visibleHeight:scroll.clientHeight,scrollHeight:scroll.scrollHeight,
                scrollWidth:scroll.scrollWidth,clientWidth:scroll.clientWidth},
            footer:rect(footer),documentWidth:document.documentElement.scrollWidth,
            buttons:[...footer.querySelectorAll('button')].map(button=>{
                const r=button.getBoundingClientRect();
                const hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);
                return {...rect(button),text:button.textContent,
                    hit:!!hit&&(hit===button||button.contains(hit))}; })};
    }''')
    metrics['label'] = label
    report.setdefault('layouts', []).append(metrics)
    width, height = metrics['viewport']['width'], metrics['viewport']['height']
    assert metrics['documentWidth'] <= width+1, (label, 'document horizontal overflow', metrics)
    assert metrics['table']['scrollWidth'] <= metrics['table']['clientWidth']+1, (label, 'table horizontal overflow', metrics)
    assert metrics['table']['visibleHeight'] > 0, (label, 'editing area is hidden', metrics)
    for button in metrics['buttons']:
        assert button['y'] >= 0 and button['bottom'] <= height+1 and button['x'] >= 0 and button['right'] <= width+1 and button['hit'], (label, 'footer inaccessible', button)
    return metrics


async def exercise(page, store, _initial, _chapter, output, report, expect):
    initial = laptop_fixture(store)
    pid = initial['id']
    report.update(fixture='SYNTHETIC_ONLY', project_id=pid)
    last_note = ''
    for width, height in VIEWPORTS:
        await page.set_viewport_size(dict(width=width, height=height))
        work, table = await enter(page, initial, expect, homepage=True)
        await expect(table.locator('.detail-definition-section')).to_have_count(24)
        await expect(table.locator('.detail-definition-section[open]')).to_have_count(0)
        jump = work.get_by_role('combobox', name='定位到需求', exact=True)
        await expect(jump.locator('option')).to_have_count(7)
        await jump.select_option('REQ-LAPTOP-6')
        statement = table.locator('[data-cell-key="requirement-REQ-LAPTOP-6-statement"] textarea')
        await expect(statement).to_be_focused()
        assert await statement.evaluate('''input => {
            const r=input.getBoundingClientRect(), s=input.closest('.refinement-sheet-scroll').getBoundingClientRect();
            return r.top>=s.top && r.bottom<=s.bottom;
        }'''), 'Requirement jump did not bring the sixth requirement input into the editing viewport'
        last_note = f'本地人工核对草稿：{width}×{height}，未经提交。'
        await statement.fill(last_note)
        input_handle = await statement.element_handle()
        normal = await layout_metrics(page, work, f'{width}x{height}-normal', report)
        await page.screenshot(path=str(output/f'{width}x{height}-normal.png'))
        await work.get_by_role('button', name='专注核对', exact=True).click()
        await expect(work.get_by_role('button', name='退出专注核对', exact=True)).to_have_attribute('aria-pressed', 'true')
        assert await input_handle.evaluate('(input)=>input.isConnected'), 'Focus mode unmounted an edited input'
        await expect(statement).to_have_value(last_note)
        focused = await layout_metrics(page, work, f'{width}x{height}-focus', report)
        assert focused['table']['visibleHeight'] > normal['table']['visibleHeight'], 'Focus mode did not reclaim vertical space'
        await page.screenshot(path=str(output/f'{width}x{height}-focus.png'))
        await work.get_by_role('button', name='退出专注核对', exact=True).click()
        assert await input_handle.evaluate('(input)=>input.isConnected'), 'Leaving focus mode unmounted an edited input'
        await expect(statement).to_have_value(last_note)
        await layout_metrics(page, work, f'{width}x{height}-restored', report)
        await work.get_by_role('button', name='保存草稿', exact=True).click()
        assert store.get(pid) == initial, 'View controls or browser draft save changed server data'
    report['checks'].append('四种有效视口直达第六条需求；普通/专注/退出三态无横向溢出，底部按钮完整可见且命中，记录实际正文高度')
    report['checks'].append('专注模式进入和退出均保持同一个输入DOM及精确人工文字；保存草稿不写后端')

    await page.reload(wait_until='networkidle')
    work, table = await enter(page, initial, expect, homepage=True)
    statement = table.locator('[data-cell-key="requirement-REQ-LAPTOP-6-statement"] textarea')
    await expect(statement).to_have_value(last_note)
    assert store.get(pid) == initial
    report['checks'].append('刷新后仍先进入需求助手，手动打开完善需求恢复已保存的精确本地草稿')

    await work.get_by_role('combobox', name='定位到需求', exact=True).select_option('REQ-0001')
    editor = table.locator('[data-detail-requirement="REQ-0001"]')
    group = editor.locator('[data-detail-section="fields"]')
    await expect(group).not_to_have_attribute('open', '')
    await expect(group.locator(':scope > summary')).to_contain_text('9 项')
    await expect(group.locator(':scope > summary')).to_contain_text('0 项待补充')
    await group.locator(':scope > summary').click()
    field = editor.locator('[data-cell-key="requirement-REQ-0001-detail_contract.fields.F1"]')
    await field.locator(':scope > summary').click()
    meaning = field.get_by_role('textbox', name=TITLE+' 告警级别 业务含义', exact=True)
    await meaning.fill('人工核对：枚举等级只用于展示，未改变其他规则。')
    await expect(group).to_have_attribute('open', '')
    await expect(field).to_have_attribute('open', '')
    await expect(meaning).to_have_value('人工核对：枚举等级只用于展示，未改变其他规则。')
    await work.get_by_role('button', name='保存草稿', exact=True).click()
    await page.reload(wait_until='networkidle')
    work, table = await enter(page, initial, expect)
    editor = table.locator('[data-detail-requirement="REQ-0001"]')
    group = editor.locator('[data-detail-section="fields"]')
    await group.locator(':scope > summary').click()
    field = editor.locator('[data-cell-key="requirement-REQ-0001-detail_contract.fields.F1"]')
    await field.locator(':scope > summary').click()
    await expect(field.get_by_role('textbox', name=TITLE+' 告警级别 业务含义', exact=True)).to_have_value('人工核对：枚举等级只用于展示，未改变其他规则。')
    report['checks'].append('四个详细分组默认折叠并显示数量；展开字段后输入触发渲染仍保持组/对象展开，刷新恢复字段草稿')

    enum = field.get_by_role('textbox', name=TITLE+' 告警级别 可选值', exact=True)
    await enum.fill('')
    await expect(group.locator(':scope > summary')).to_contain_text('1 项待补充')
    await field.locator(':scope > summary').click()
    await group.locator(':scope > summary').click()
    await expect(group).not_to_have_attribute('open', '')
    await expect(field).not_to_have_attribute('open', '')
    request_count = len(report['api_requests'])
    await work.locator('.refinement-sheet-footer .primary').click()
    await expect(group).to_have_attribute('open', '')
    await expect(field).to_have_attribute('open', '')
    await expect(enum).to_be_focused()
    assert await enum.evaluate('''input => {
        const r=input.getBoundingClientRect(), scroll=input.closest('.refinement-sheet-scroll');
        const s=scroll.getBoundingClientRect(), heading=scroll.querySelector('thead').getBoundingClientRect();
        const hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);
        return r.top>=s.top+heading.height && r.bottom<=s.bottom && hit===input;
    }'''), 'Focused missing-enum input is clipped or covered'
    assert not [event for event in report['api_requests'][request_count:] if event['method'] != 'GET'], 'Invalid enum sent a write request'
    assert store.get(pid) == initial
    await layout_metrics(page, work, '1280x640-missing-enum', report)
    await page.screenshot(path=str(output/'1280x640-missing-enum.png'))
    await enum.fill('一级\n二级')
    await expect(group).to_have_attribute('open', '')
    await expect(field).to_have_attribute('open', '')
    await expect(enum).to_have_value('一级\n二级')
    assert store.get(pid) == initial
    report['checks'].append('真实确认点击遇到缺枚举时自动展开折叠组/对象并聚焦；输入修复后仍展开；全程不发写请求且后端数据未改')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--static-build', type=Path, default=ROOT/'.verify-laptop-build')
    parser.add_argument('--output', type=Path, default=ROOT/'evidence/refinement-laptop-20261013/laptop-regression')
    asyncio.run(run(parser.parse_args(), exercise=exercise))
