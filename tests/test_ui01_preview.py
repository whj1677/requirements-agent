"""Independent synthetic preview scenarios; neither is a user requirement."""
import asyncio
import copy
import json

import pytest
from jsonschema import ValidationError
from playwright.async_api import async_playwright

from app.contracts import validate_ui
from app.preview import prototype


def component(cid, kind, label, refs, *, fields=None, columns=None, rows=None, action='none', target=None, simulation=None):
    value=dict(component_id=cid,type=kind,label=label,description='',ref_ids=refs,
        fields=fields or [],columns=columns or [],rows=rows or [],
        interaction=dict(action=action,target_id=target,target_state=None),provisional=False)
    if simulation is not None:value['simulation']=simulation
    return value


def field(name,label,kind='text'):
    return dict(name=name,label=label,type=kind,required_state='required',options=[],ref_ids=['REQ-1'])


def columns(*names):
    return [dict(key=name,label=name,ref_ids=['REQ-1']) for name in names]


def spec(title, components):
    return dict(schema_version='1.1',title=title,draft_revision=1,design_intent='合成测试交互',
        demo_data_label='模拟数据，仅用于原型演示',pages=[dict(page_id='PAGE-1',title=title,
        requirement_refs=['REQ-1'],regions=[dict(name='main',components=components)],
        states=['normal','error'],state_messages=dict(normal='模拟列表',loading=None,empty=None,error='模拟错误',forbidden=None),not_applicable_states=[])])


def price_spec():
    sim=dict(dataset_id='periods',editable_roles=['编辑者'],rules=[
        dict(kind='required',field_names=['start'],ref_ids=['REQ-1']),
        dict(kind='required',field_names=['end'],ref_ids=['REQ-1']),
        dict(kind='start_before_end',field_names=['start','end'],ref_ids=['RULE-1']),
        dict(kind='no_overlap',field_names=['start','end'],ref_ids=['RULE-1'])])
    return spec('电价时段配置',[
        component('LIST','table','时段列表',['REQ-1'],columns=columns('start','end'),rows=[['00:00','07:00'],['10:00','14:00']],action='edit',target='DRAWER',simulation=dict(dataset_id='periods',editable_roles=['编辑者'],rules=[])),
        component('NEW','button','新增',['REQ-1'],action='new',target='DRAWER'),
        component('DRAWER','drawer','编辑时段',['REQ-1'],fields=[field('start','开始','time'),field('end','结束','time')],simulation=sim),
        component('SAVE','button','模拟保存',['REQ-1'],action='save',target='DRAWER'),
        component('CANCEL','button','取消',['REQ-1'],action='cancel',target='DRAWER')])


def contacts_spec():
    return spec('联系人维护',[
        component('FILTER','filters','姓名筛选',['REQ-1'],fields=[field('name','查找姓名')]),
        component('FIND','button','筛选',['REQ-1'],action='filter',target='LIST'),
        component('LIST','table','联系人列表',['REQ-1'],columns=columns('name','phone'),rows=[['小李','1001'],['小周','1002']],action='edit',target='DRAWER',simulation=dict(dataset_id='contacts',editable_roles=['编辑者'],rules=[])),
        component('NEW','button','新增',['REQ-1'],action='new',target='DRAWER'),
        component('DRAWER','drawer','联系人表单',['REQ-1'],fields=[field('name','姓名'),field('phone','电话')],simulation=dict(dataset_id='contacts',editable_roles=['编辑者'],rules=[dict(kind='required',field_names=['name'],ref_ids=['REQ-1'])])),
        component('SAVE','button','模拟保存',['REQ-1'],action='save',target='DRAWER'),
        component('CANCEL','button','取消',['REQ-1'],action='cancel',target='DRAWER')])


def project():
    return dict(revision=1,items=[dict(id='REQ-1'),dict(id='RULE-1')])


def test_schema_accepts_old_and_new_and_rejects_invalid():
    from app.core import KIT, read_json
    old=read_json(KIT/'examples/wireframe.example.json')
    old['draft_revision']=1
    old['pages'][0]['requirement_refs']=['REQ-1']
    old['pages'][0]['regions'][0]['components'][0]['ref_ids']=['REQ-1']
    validate_ui(old,project())
    validate_ui(price_spec(),project())
    validate_ui(contacts_spec(),project())
    validate_ui(read_json(KIT/'examples/wireframe.v1_1.example.json'),project())
    invalid=price_spec();invalid['pages'][0]['regions'][0]['components'][1]['interaction']['target_id']='MISSING'
    with pytest.raises(Exception):validate_ui(invalid,project())
    invalid=price_spec();invalid['pages'][0]['regions'][0]['components'][1]['interaction']['action']='eval'
    with pytest.raises(ValidationError):validate_ui(invalid,project())
    invalid=price_spec();invalid['pages'][0]['regions'][0]['components'][2]['simulation']['rules'][0]['expression']='run()'
    with pytest.raises(ValidationError):validate_ui(invalid,project())
    invalid=contacts_spec();invalid['title']='<img src=x onerror="run()">'
    with pytest.raises(Exception):validate_ui(invalid,project())


async def exercise_browser():
    async with async_playwright() as playwright:
        browser=await playwright.chromium.launch(headless=True)
        try:
            page=await browser.new_page()
            requests=[]
            page.on('request',lambda request:requests.append(request.url))
            await page.set_content(prototype(price_spec()))
            await page.get_by_role('button',name='新增').click()
            await page.get_by_label('开始').fill('07:00')
            await page.get_by_label('结束').fill('10:00')
            await page.get_by_role('button',name='模拟保存').click()
            assert await page.locator('tbody tr').count()==3
            assert await page.get_by_role('row').filter(has_text='07:00').filter(has_text='10:00').count()==1
            await page.get_by_role('button',name='新增').click()
            await page.get_by_label('开始').fill('06:00')
            await page.get_by_label('结束').fill('08:00')
            await page.get_by_role('button',name='模拟保存').click()
            assert '重叠' in await page.get_by_role('status').inner_text()
            assert await page.get_by_label('开始').input_value()=='06:00'
            assert await page.get_by_role('cell',name='06:00').count()==0
            await page.get_by_role('button',name='取消').click()
            assert '未保存' in await page.get_by_role('status').inner_text()
            await page.get_by_label('模拟角色').select_option('只读')
            assert await page.get_by_role('button',name='新增').is_disabled()
            assert not requests

            contacts=contacts_spec()
            assert all(word not in json.dumps(contacts,ensure_ascii=False) for word in ('电价','时段','价格'))
            contacts['title']='<img src=x onerror="window.hacked=1">'
            await page.close()
            page=await browser.new_page()
            page.on('request',lambda request:requests.append(request.url))
            await page.set_content(prototype(contacts))
            assert await page.locator('img').count()==0
            assert await page.evaluate('window.hacked') is None
            await page.get_by_label('查找姓名').fill('小周')
            await page.get_by_role('button',name='筛选').click()
            assert await page.get_by_role('cell',name='小周').count()==1
            assert await page.get_by_role('cell',name='小李').count()==0
            await page.get_by_role('button',name='新增').click()
            await page.get_by_label('姓名',exact=True).fill('小陈')
            await page.get_by_label('电话').fill('1003')
            await page.get_by_role('button',name='模拟保存').click()
            await page.get_by_label('查找姓名').fill('小陈')
            await page.get_by_role('button',name='筛选').click()
            assert await page.get_by_role('cell',name='小陈').count()==1
            assert not requests
        finally:
            await browser.close()


def test_two_independent_synthetic_interactions():
    asyncio.run(exercise_browser())
