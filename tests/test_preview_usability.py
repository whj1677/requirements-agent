"""Product interactions in the fixed interpreter, with isolated synthetic data."""
import asyncio
from playwright.async_api import async_playwright
from app.preview import prototype
from tests.test_ui01_preview import contacts_spec


async def exercise():
    spec=contacts_spec()
    components=spec['pages'][0]['regions'][0]['components']
    components[4]['type']='dialog'
    # required_state is itself a supported constraint even if rules omits it.
    components[4]['simulation']['rules']=[]
    components[4]['fields'][1]['required_state']='optional'
    for c in components:
        if c.get('simulation'):c['simulation']['editable_roles']=['管理员']
    # A phone matching the name filter must not count as a matching contact.
    components[2]['rows'].append(['其它人','小周'])
    async with async_playwright() as p:
        browser=await p.chromium.launch()
        page=await browser.new_page()
        requests=[];page.on('request',lambda r:requests.append(r.url))
        try:
            await page.set_content(prototype(spec))
            assert await page.get_by_label('模拟角色').input_value()=='管理员'
            await page.get_by_label('查找姓名').fill('小周')
            await page.get_by_role('button',name='筛选',exact=True).click()
            assert await page.locator('tbody tr').count()==1
            await page.get_by_label('查找姓名').fill('')
            await page.get_by_role('button',name='筛选',exact=True).click()
            await page.get_by_role('button',name='新增',exact=True).click()
            dialog=page.get_by_role('dialog',name='联系人表单')
            assert await dialog.is_visible()
            assert await dialog.get_by_role('button',name='模拟保存').is_visible()
            await dialog.get_by_role('button',name='模拟保存').click()
            assert '必填' in await page.get_by_role('status').inner_text()
            await page.get_by_label('姓名',exact=True).fill('测试甲')
            await page.get_by_label('模拟保存失败').check()
            await dialog.get_by_role('button',name='模拟保存').click()
            assert await page.get_by_label('姓名',exact=True).input_value()=='测试甲'
            assert await page.locator('tbody tr').count()==3
            await dialog.get_by_role('button',name='取消',exact=True).click()
            assert await dialog.get_by_role('button',name='继续编辑').is_visible()
            await dialog.get_by_role('button',name='继续编辑').click()
            await page.get_by_label('模拟保存失败').uncheck()
            await dialog.get_by_role('button',name='模拟保存').click()
            assert await page.locator('tbody tr').count()==4
            assert await page.get_by_role('button',name='新增',exact=True).evaluate('(e)=>e===document.activeElement')
            row=page.get_by_role('row').filter(has_text='测试甲')
            await row.get_by_role('button',name='编辑').click()
            assert await page.get_by_label('姓名',exact=True).input_value()=='测试甲'
            await page.get_by_label('姓名',exact=True).fill('不应保存')
            await dialog.press('Escape')
            await dialog.get_by_role('button',name='放弃修改').click()
            assert await page.get_by_role('cell',name='测试甲',exact=True).count()==1
            assert await page.get_by_role('cell',name='不应保存',exact=True).count()==0
            await page.get_by_label('模拟角色').select_option('只读')
            assert await page.get_by_role('button',name='新增',exact=True).is_disabled()
            assert all(await page.get_by_role('button',name='编辑',exact=True).evaluate_all('(els)=>els.map(e=>e.disabled)'))
            assert requests==[]
        finally:await browser.close()


def test_contact_modal_failure_cancel_focus_filter_and_role():
    asyncio.run(exercise())
