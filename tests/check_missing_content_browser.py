"""Click real continue/confirm buttons against isolated synthetic projects."""
import argparse
import asyncio
import re
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tests.check_answer_implementation_browser import enter,run,wait_saved,question
from tests.check_decision_document_replay import REQ,Q_PERMISSION,Q_TIME,PERMISSION,TIMING
from tests.check_refinement_laptop_browser import laptop_fixture
from tests.check_detail_prd_browser import TITLE


async def reachable(input):
    return await input.evaluate('''element=>{
        const r=element.getBoundingClientRect(),scroll=element.closest('.refinement-sheet-scroll'),s=scroll.getBoundingClientRect();
        const head=scroll.querySelector('thead').getBoundingClientRect();
        return r.top>=s.top+head.height&&r.bottom<=s.bottom&&document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)===element;
    }''')


async def exercise(page,store,initial,_chapter,output,report,expect):
    await page.set_viewport_size(dict(width=1093,height=614))
    detailed=laptop_fixture(store)
    with store.edit(detailed['id'],detailed['revision'],'合成必填目标缺项') as (p,_):
        p['product_context']['intent']=''
        p['context_field_states']['intent']={'status':'unknown'}
        p['questions'].append(dict(id='Q-MISSING-EXPORT',question='合成核对：本期是否允许导出告警？',
            why='合成必答缺项测试',blocking=True,status='open',answer=None,options=[],related_refs=['REQ-0001'],source_refs=[]))
    detailed=store.get(detailed['id'])
    work,table=await enter(page,detailed,expect,homepage=True)
    intent_row=table.locator('[data-cell-key="context-intent"]')
    await expect(intent_row).to_have_class('sheet-invalid')
    await expect(intent_row).to_have_attribute('aria-invalid','true')
    await expect(intent_row.get_by_text('待补充',exact=True)).to_be_visible()
    await expect(table.locator('[data-cell-key="context-value"]')).not_to_have_class('sheet-invalid')
    required_answer=table.locator('[data-cell-key="question-Q-MISSING-EXPORT"]')
    await expect(required_answer).to_have_class('sheet-invalid')
    await required_answer.locator('textarea').fill('不知道')
    await expect(required_answer).to_have_class('sheet-invalid')
    await required_answer.locator('textarea').fill('本期不提供告警导出。')
    await expect(required_answer).not_to_have_class('sheet-invalid')
    await required_answer.locator('textarea').fill('')
    await work.get_by_role('navigation',name='需求表定位').get_by_role('button',name=re.compile(r'^待补充 ·')).click()
    await expect(intent_row.locator('textarea')).to_be_focused()
    assert await reachable(intent_row.locator('textarea'))
    await page.screenshot(path=str(output/'00-gaps-highlighted-before-submit.png'))
    report['checks'].append('未点击下一步时必填缺项已高亮并显示待补充；选填空格不标错，可直接定位')
    await work.get_by_role('combobox',name='定位到需求',exact=True).select_option('REQ-LAPTOP-6')
    statement=table.locator('[data-cell-key="requirement-REQ-LAPTOP-6-statement"] textarea')
    await statement.fill('人工未提交输入：站点只读核对，必须保留。')
    await work.get_by_role('navigation',name='需求工作流程').get_by_role('button',name='3 确认交付',exact=True).click()
    intent=table.locator('[data-cell-key="context-intent"] textarea')
    await expect(intent).to_be_focused()
    assert await reachable(intent),'Top next-step navigation did not reveal the required field'
    await expect(statement).to_have_value('人工未提交输入：站点只读核对，必须保留。')
    assert store.get(detailed['id'])==detailed
    await page.screenshot(path=str(output/'01-next-focuses-missing-goal.png'))
    await intent.fill('合成核对站点告警与派单')
    await expect(intent_row).not_to_have_class('sheet-invalid')
    editor=table.locator('[data-detail-requirement="REQ-0001"]')
    group=editor.locator('[data-detail-section="fields"]')
    await group.locator(':scope > summary').click()
    field=editor.locator('[data-cell-key="requirement-REQ-0001-detail_contract.fields.F1"]')
    await field.locator(':scope > summary').click()
    enum=field.get_by_role('textbox',name=TITLE+' 告警级别 可选值',exact=True)
    await enum.fill('')
    detail_row=table.locator('tr[data-cell-key="requirement-REQ-0001-detail_contract"]')
    await expect(detail_row).to_have_class('sheet-invalid')
    await expect(detail_row.get_by_text(re.compile(r'^待补充 ·'))).to_be_visible()
    await field.locator(':scope > summary').click()
    await group.locator(':scope > summary').click()
    await work.get_by_role('navigation',name='需求工作流程').get_by_role('button',name='3 确认交付',exact=True).click()
    await expect(enum).to_be_focused()
    assert await reachable(enum)
    await expect(field).to_have_attribute('open','')
    await expect(group).to_have_attribute('open','')
    await enum.fill('一般\n严重')
    await expect(field.locator('.detail-property-invalid')).to_have_count(0)
    await expect(detail_row).not_to_have_class('sheet-invalid')
    await page.screenshot(path=str(output/'02-next-expands-and-focuses-enum.png'))
    assert store.get(detailed['id'])==detailed
    report['checks'].append('顶部下一步定位必填目标与折叠枚举，自动展开并聚焦；人工输入保留且不写后端')

    # Only these explicit saves write the isolated fixture; no missing field is auto-filled.
    work,table=await enter(page,initial,expect,homepage=True)
    qrow=table.locator('[data-cell-key="question-'+Q_PERMISSION+'"]')
    trow=table.locator('[data-cell-key="question-'+Q_TIME+'"]')
    # These two questions are optional in this fixture, so blank is valid.
    await expect(qrow).not_to_have_class('sheet-invalid')
    await expect(trow).not_to_have_class('sheet-invalid')
    await qrow.get_by_role('button',name=PERMISSION,exact=True).click()
    await trow.get_by_role('button',name=TIMING,exact=True).click()
    await expect(qrow).not_to_have_class('sheet-invalid')
    await expect(trow).not_to_have_class('sheet-invalid')
    await table.locator('[data-cell-key="requirement-'+REQ+'-statement"] textarea').click()
    await work.get_by_role('button',name='保存并继续核对',exact=True).click()
    await wait_saved(store,initial['id'],lambda p:question(p,Q_PERMISSION).get('status')=='answered','Explicit table save did not finish')
    target=qrow.locator('.answer-implementation select').first
    await expect(target).to_be_focused()
    assert await reachable(target),'Pending answer implementation was not brought into view'
    await page.screenshot(path=str(output/'03-save-locates-pending-answer.png'))
    report['checks'].append('保存并继续核对后直接定位待落实回答的原文选择控件；未自动勾选或采纳答案')

    # Formal confirmation is clickable even when gates are unmet, and routes without submitting.
    await page.reload(wait_until='networkidle')
    await page.locator('aside.sidebar button[title="'+initial['name']+'"]').click()
    work=page.locator('.connected-workspace:visible')
    await work.get_by_role('navigation',name='需求工作流程').get_by_role('button',name='3 确认交付',exact=True).click()
    await work.get_by_role('button',name='打开正式确认',exact=True).click()
    button=page.get_by_role('button',name='确认此版本的需求与文档',exact=True)
    await expect(button).to_be_enabled()
    before=store.get(initial['id'])
    count=len(report['api_requests'])
    await button.click()
    await expect(button).not_to_be_visible()
    target=page.locator('.connected-workspace:visible .answer-implementation select').first
    await expect(target).to_be_focused()
    assert await reachable(target),'Formal confirm did not focus the actual incomplete answer control'
    focused=await page.evaluate('document.activeElement?.outerHTML')
    report['formal_focus']=focused
    assert store.get(initial['id'])==before
    assert not [event for event in report['api_requests'][count:] if event['method']!='GET']
    await expect(page.get_by_role('dialog',name='确认内容复核',exact=True)).not_to_be_visible()
    await page.screenshot(path=str(output/'04-formal-confirmation-routes-to-gap.png'))
    report['checks'].append('正式确认有缺项时按钮可点击并跳转，未打开提交弹窗、未建立基线或发写请求')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--static-build',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    asyncio.run(run(parser.parse_args(),exercise))
