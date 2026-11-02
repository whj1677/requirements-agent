"""Synthetic answer-location and scope UX over intercepted 8765 / isolated ASGI.

No listener, daily data, model call, or real business acceptance is involved.
"""
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tests.check_answer_implementation_browser import enter, run
from tests.check_decision_document_replay import REQ, Q_PERMISSION, PERMISSION


async def exercise(page, store, initial, chapter, output, report, expect):
    pid = initial['id']
    with store.edit(pid, initial['revision'], '合成范围与答案位置测试前置') as (p, _):
        req = next(i for i in p['items'] if i['id'] == REQ)
        req['selection_status'] = 'candidate'
        req['behavior'] = {}
        req['detail_contract'] = dict(schema_version=1, applicability={
            key: dict(applicable=key == 'fields', reason='合成目录测试')
            for key in ('fields', 'interactions', 'flow', 'state_transitions')},
            fields=[dict(id=f'F{i:03d}', name=f'字段{i:03d}') for i in range(1, 140)] +
                   [dict(id='F140', meaning='合成终端查询原句')],
            interactions=[], flow=dict(nodes=[], edges=[]), state_transitions=[])
        p['product_context']['scope_ids'] = []
        q = next(q for q in p['questions'] if q['id'] == Q_PERMISSION)
        q.update(answer=PERMISSION, status='answered', understanding_status='pending', related_refs=[REQ])
    initial = store.get(pid)
    from app.answer_implementation import location_suggestions
    suggestion = location_suggestions(initial, next(q for q in initial['questions'] if q['id'] == Q_PERMISSION))
    assert any(row['item_id'] == REQ and row['field'] == 'statement' and row['quote'] == PERMISSION
               for row in suggestion), 'the stale-suggestion test needs a real backend suggestion for the initial quote'
    work, table = await enter(page, initial, expect)
    scope = table.get_by_role('combobox', name='站点告警查看 本期范围', exact=True)
    await expect(scope).to_be_visible()
    selected = scope.locator('option:checked')
    assert '待' in (await selected.inner_text()), 'empty scope must be presented as an undecided choice'
    await scope.select_option(label='本期包含')
    await expect(scope.locator('option:checked')).to_have_text('本期包含')
    await scope.select_option(label='暂不纳入本期')
    await work.get_by_role('button', name='保存草稿', exact=True).click()
    assert store.get(pid) == initial, 'browser draft save must not mutate the server project'
    await page.reload(wait_until='networkidle')
    work, table = await enter(page, initial, expect)
    scope = table.get_by_role('combobox', name='站点告警查看 本期范围', exact=True)
    await expect(scope.locator('option:checked')).to_have_text('暂不纳入本期')
    await scope.select_option(label='本期包含')
    await work.get_by_role('button', name='保存草稿', exact=True).click()
    await page.reload(wait_until='networkidle')
    work, table = await enter(page, initial, expect)
    scope = table.get_by_role('combobox', name='站点告警查看 本期范围', exact=True)
    await expect(scope.locator('option:checked')).to_have_text('本期包含')
    assert store.get(pid) == initial
    report['checks'].append('空范围显示待决定；人工包含和排除可在本机草稿恢复，未写后端')

    qrow = table.locator('tr[data-cell-key="question-'+Q_PERMISSION+'"]')
    location = qrow.locator('.answer-implementation')
    await expect(location.get_by_role('heading', name='相关位置，需核对含义')).to_be_visible()
    target = location.locator('.answer-implementation-target')
    choice = target.get_by_role('combobox', name='站点告警查看 答案落文位置', exact=True)
    await expect(choice).to_have_value('')
    enabled_values = await choice.locator('option:not([disabled])').evaluate_all(
        "options => options.map(option => option.value).filter(Boolean)")
    assert len(enabled_values) == len(set(enabled_values)) == 141
    assert await target.get_by_role('checkbox').count() == 0
    related = location.locator('.answer-location-related article').filter(has_text=PERMISSION)
    await related.get_by_role('button', name='选择此位置').click()
    await expect(choice).to_have_value('statement')
    check = target.get_by_role('checkbox', name='我已核对此原文表达了答案', exact=True)
    await expect(check).not_to_be_checked()
    await check.click()
    await qrow.locator('.sheet-question-history > summary').click()
    await expect(check).to_be_checked()

    statement = table.locator('tr[data-cell-key="requirement-'+REQ+'-statement"]').get_by_role('textbox')
    await statement.fill('XYZ 已变化的本地正文')
    assert await target.get_by_role('checkbox').count() == 0 or not await target.get_by_role('checkbox').is_checked()
    await expect(location.locator('.answer-location-related article').filter(has_text=PERMISSION)).to_have_count(0)
    await expect(location.locator('.answer-location-related article').filter(has_text='XYZ 已变化的本地正文')).to_have_count(0)
    report['checks'].append('推荐仅导航；显式选择后仍需勾选，原文变化后旧见证和不匹配推荐均失效')

    catalog = location.locator('details.answer-location-catalog')
    await expect(catalog.locator('summary')).to_contain_text('141')
    await catalog.locator('summary').click()
    search = catalog.get_by_role('searchbox', name='搜索位置名称或原文')
    await search.fill('F140')
    result = catalog.locator('article').filter(has_text='合成终端查询原句')
    await expect(result).to_have_count(1)
    await search.fill('合成终端查询原句')
    await expect(result).to_have_count(1)
    await result.get_by_role('button', name='选择此位置').click()
    await expect(choice).to_have_value('detail_contract.fields.F140.meaning')
    await expect(target.get_by_role('blockquote')).to_have_text('合成终端查询原句')
    check = target.get_by_role('checkbox', name='我已核对此原文表达了答案', exact=True)
    await expect(check).not_to_be_checked()
    await check.click()
    await qrow.locator('.sheet-question-history > summary').click()
    await expect(check).to_be_checked()
    await work.get_by_role('button', name='保存草稿', exact=True).click()
    saved_draft = await page.evaluate('(key) => JSON.parse(sessionStorage.getItem(key))', pid + '-refinement-sheet')
    assert saved_draft['answer_implementations'][Q_PERMISSION] == [dict(
        item_id=REQ, field='detail_contract.fields.F140.meaning', quote='合成终端查询原句')]
    # The selected control must be reachable and not hidden under a sticky header.
    await check.scroll_into_view_if_needed()
    rect = await check.bounding_box()
    assert rect is not None
    topmost = await page.evaluate('''({x,y}) => { const e=document.elementFromPoint(x,y); return !!e && (e.matches('input[type=checkbox]') || !!e.closest('label.answer-implementation-check')); }''',
                                  dict(x=rect['x'] + rect['width']/2, y=rect['y'] + rect['height']/2))
    assert topmost, 'checkbox is obscured at its visible click position'
    assert store.get(pid) == initial
    report['checks'].append('141 处完整目录按位置名与正文均可查；末尾位置可选择、查看原句、手工勾选，控件未被遮挡')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--static-build', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    asyncio.run(run(parser.parse_args(), exercise=exercise))
