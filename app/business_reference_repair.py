"""Repair only invalid business source aliases, using the already sent excerpts."""
import copy
import jsonschema
from .core import dumps,require
from .business_draft import _aliases

SCHEMA=dict(type='object',additionalProperties=False,required=['references'],properties={
    'references':dict(type='array',items=dict(type='object',additionalProperties=False,
        required=['slot','source','locator'],properties={'slot':dict(type='integer',minimum=0),
        'source':dict(type='string'),'locator':dict(type='string')}))})


def prepare(value,excerpts):
    pairs,_,_=_aliases(excerpts)
    allowed=[dict(**pairs[(e['source_id'],e['id'])],text=e['text']) for e in excerpts]
    valid={(r['source'],r['locator']) for r in allowed};slots=[]
    def visit(node,path=()):
        if isinstance(node,dict):
            if set(node)=={'source','locator'} and all(isinstance(x,str) for x in node.values()):
                if (node['source'],node['locator']) not in valid:slots.append(dict(path=list(path),original=copy.deepcopy(node)))
            else:
                for key,child in node.items():visit(child,(*path,key))
        elif isinstance(node,list):
            for index,child in enumerate(node):visit(child,(*path,index))
    visit(value)
    return dict(original=copy.deepcopy(value),slots=slots,allowed=allowed) if slots else None


def messages(plan):
    return [dict(role='system',content=(
        '仅修复业务草稿中无效的资料引用编号。所有业务文字及有效引用由程序保留，你只返回references补丁。'
        '对slots逐项核对original对应内容，选择allowed中实际支持该内容的source与locator原样填写；'
        '不能猜来源、不能改业务内容，不能增加或删除问题。无法找到支持原文时不填写该项，程序会保留待核对。'
        'slot是slots数组的0开始下标。只返回符合Schema的JSON。\n可信任务头：'+dumps(dict(stage='ingest',schema=SCHEMA)))),
        dict(role='user',content=[dict(type='text',text=dumps(plan))])]


def apply(plan,patch):
    require(not list(jsonschema.Draft202012Validator(SCHEMA).iter_errors(patch)),
        'BUSINESS_REFERENCE_REPAIR_INVALID','引用修复补丁格式不正确。')
    rows=patch['references'];slots=plan['slots'];valid={(r['source'],r['locator']) for r in plan['allowed']}
    require(len(rows)==len(slots) and {r['slot'] for r in rows}==set(range(len(slots))),
        'BUSINESS_REFERENCE_REPAIR_INVALID','引用修复未完整覆盖错误位置，原稿保留。')
    value=copy.deepcopy(plan['original'])
    for row in rows:
        require((row['source'],row['locator']) in valid,'BUSINESS_REFERENCE_REPAIR_INVALID','修复来源不在已授权材料中。')
        path=slots[row['slot']]['path'];node=value
        for key in path[:-1]:node=node[key]
        node[path[-1]]=dict(source=row['source'],locator=row['locator'])
    return value
