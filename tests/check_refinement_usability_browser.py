"""Desktop-only usability regression; all 8765 traffic intercepted, no listener."""
import argparse
import asyncio
import copy
from pathlib import Path
import re

from check_answer_implementation_browser import (run, enter, table_case, chapter_case,
                                                REQ, Q_PERMISSION, Q_TIME, question)
from tests.check_decision_document_replay import seed_storage_case, PERMISSION


async def long_table(page,store,output,report,expect):
    p=seed_storage_case(store,suffix='-LONG')
    with store.edit(p['id'],p['revision'],'合成长表，仅测试定位和展开') as (p,_):
        p['name']='合成储能云：12 条需求长表定位'
        original=next(i for i in p['items'] if i['kind']=='requirement')
        for n in range(1,12):
            item=copy.deepcopy(original);item.update(id=original['id']+'-'+str(n),title='合成站点告警查看 '+str(n))
            p['items'].append(item)
        ids=[i['id'] for i in p['items'] if i['kind']=='requirement']
        p['product_context']['scope_ids']=ids
        template=p['questions'][0]
        p['questions']=[dict(copy.deepcopy(template),id='Q-LONG-'+str(n),question='合成告警查看范围 '+str(n)+'？',
                             status='answered',answer=PERMISSION,related_refs=[iid],understanding_status='pending') for n,iid in enumerate(ids)]
    p=store.get(p['id']);before=copy.deepcopy(p)
    work,table=await enter(page,p,expect)
    nav=work.get_by_role('navigation',name='需求表定位',exact=True)
    await expect(table.get_by_role('button',name=re.compile('^展开业务说明：'))).to_have_count(12)
    await nav.get_by_role('button',name=re.compile('^待落实回答')).click()
    await expect(table.get_by_role('textbox',name='合成告警查看范围 0？ 我的回答',exact=True)).to_be_focused()
    await nav.get_by_role('button',name='上一项待落实',exact=True).click()
    await expect(table.get_by_role('textbox',name='合成告警查看范围 11？ 我的回答',exact=True)).to_be_focused()
    await nav.get_by_role('button',name='需求与验收',exact=True).click()
    await expect(table.locator('[data-section="requirements"]')).to_be_focused()
    assert store.get(p['id'])==before
    report['checks'].append('12 条合成储能需求默认折叠次要说明；待落实首末循环和分区跳转均可达，无业务写入')
    await page.screenshot(path=str(output/'07-long-table-section-navigation.png'))


async def exercise(page, store, initial, chapter, output, report, expect):
    await page.set_viewport_size(dict(width=1280,height=720))
    report['tested_viewport']=dict(width=1280,height=720)
    work, table = await enter(page, initial, expect, homepage=True)
    before = store.get(initial['id'])
    flow = table.locator('tr[data-cell-key="requirement-'+REQ+'-flow"]')
    await expect(flow).to_have_count(0)
    toggle = table.get_by_role('button',name='展开业务说明：站点告警查看',exact=True)
    await toggle.click()
    await expect(flow.get_by_role('textbox')).to_have_value(next(i for i in initial['items'] if i['id']==REQ)['behavior']['flow'])
    await table.get_by_role('button',name='收起业务说明：站点告警查看',exact=True).click()
    await expect(flow).to_have_count(0)
    assert store.get(initial['id']) == before
    report['checks'].append('业务说明可按需展开收起；既有值与后端均不变')

    # Run the complete existing answer/draft/implementation contract against this UI.
    await table_case(page,store,initial,output,report,expect)
    work,table=await enter(page,initial,expect)
    nav=work.get_by_role('navigation',name='需求表定位',exact=True)
    await nav.get_by_role('button',name=re.compile('^待落实回答')).click()
    permission=table.locator('tr[data-cell-key="question-'+Q_PERMISSION+'"]')
    timing=table.locator('tr[data-cell-key="question-'+Q_TIME+'"]')
    p_input=permission.get_by_role('textbox',name=question(initial,Q_PERMISSION)['question']+' 我的回答',exact=True)
    t_input=timing.get_by_role('textbox',name=question(initial,Q_TIME)['question']+' 我的回答',exact=True)
    await expect(p_input).to_be_focused()
    await nav.get_by_role('button',name='下一项待落实',exact=True).click()
    await expect(t_input).to_be_focused()
    await nav.get_by_role('button',name='上一项待落实',exact=True).click()
    await expect(p_input).to_be_focused()
    report['checks'].append('待落实入口与上一项下一项真实滚动定位并聚焦对应回答')
    before=store.get(initial['id'])
    target=permission.locator('.answer-implementation-target').filter(has=page.get_by_role('combobox',name='站点告警查看 答案落文位置',exact=True))
    await target.locator('summary').click()
    await expect(target.locator('dl')).to_contain_text(next(i for i in before['items'] if i['id']==REQ)['statement'])
    await target.get_by_role('button',name='选择此位置：条款正文',exact=True).click()
    await expect(target.get_by_role('combobox')).to_have_value('statement')
    await expect(target.get_by_role('checkbox',name='我已核对此原文表达了答案',exact=True)).not_to_be_checked()
    assert store.get(initial['id'])==before
    report['checks'].append('选位置前可预览原句摘要；选择位置不会自动勾选或应用答案')
    await target.locator('summary').click()
    await page.screenshot(path=str(output/'06-answer-navigation-and-quotes.png'))

    await work.get_by_role('navigation',name='需求工作流程').get_by_role('button',name=re.compile(r'^3\s*确认交付$')).click()
    alert=work.get_by_role('alert').filter(has=page.get_by_role('button',name='返回表格核对',exact=True))
    await expect(alert).to_contain_text('保存并继续核对')
    await expect(alert).not_to_contain_text('确认并进入交付')
    await alert.get_by_role('button',name='返回表格核对',exact=True).click()
    assert store.get(initial['id'])==before
    report['checks'].append('交付拦截提示与当前保存按钮同名；返回核对仅定位，不保存或刷新')
    await chapter_case(page,store,chapter,output,report,expect)
    report['checks'].append('原文复用的预览和确认文案明确本次落实核对，不暗示正文改动')
    await long_table(page,store,output,report,expect)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--static-build',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    asyncio.run(run(parser.parse_args(),exercise=exercise))
