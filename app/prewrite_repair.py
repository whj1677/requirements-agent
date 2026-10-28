"""Bounded review metadata repair. The model selects slots, never rewrites a review.

The original response and patch remain separate evidence. Only invalid quotes
and unbound decision associations are editable; assertions still need PM review.
"""
import copy
import jsonschema

from .core import dumps, require
from .prewrite import _resolve, _clause_refs


def prepare(project, response):
    if not isinstance(response, dict) or not isinstance(response.get('findings'), list):return None
    slots=[]
    for fi, finding in enumerate(response['findings']):
        if not isinstance(finding, dict):return None
        for qi, quote in enumerate(finding.get('evidence_quotes', [])):
            if not isinstance(quote, dict):return None
            text=_resolve(project, quote.get('object_id'), quote.get('path',''))
            if isinstance(text,str) and quote.get('quote') and quote['quote'] in text:continue
            # Keep the location fixed. A paraphrase may be replaced by an exact
            # span of that field, but cannot jump to a more convenient source.
            if not isinstance(text,str) or not text:return None
            slots.append(dict(finding=fi,quote=qi,actual_text=text,original=copy.deepcopy(quote)))
    decisions=response.get('result',{}).get('required_decisions',[])
    blockers=[f for f in response['findings'] if f.get('severity')=='blocker']
    unbound=[]
    for di, decision in enumerate(decisions):
        if any(_clause_refs(project,f.get('related_refs',[])) and (
                f.get('decision_question')==decision or any(ref in decision for ref in f['related_refs'])
                or len(blockers)==1 and len(decisions)==1) for f in blockers):continue
        choices=[fi for fi,f in enumerate(response['findings'])
                 if f.get('decision_question') and _clause_refs(project,f.get('related_refs',[]))]
        if not choices:return None
        unbound.append(dict(decision=di,text=decision,allowed_findings=choices))
    if not slots and not unbound:return None
    return dict(original=copy.deepcopy(response),quote_slots=slots,decision_slots=unbound)


SCHEMA=dict(type='object',additionalProperties=False,required=['quotes','decisions'],properties={
    'quotes':dict(type='array',items=dict(type='object',additionalProperties=False,
        required=['finding','quote','text'],properties={
            'finding':dict(type='integer',minimum=0),'quote':dict(type='integer',minimum=0),
            'text':dict(type='string',minLength=1,maxLength=4000)})),
    'decisions':dict(type='array',items=dict(type='object',additionalProperties=False,
        required=['decision','finding'],properties={
            'decision':dict(type='integer',minimum=0),'finding':dict(type='integer',minimum=0)}))})


def messages(plan):
    return [dict(role='system',content=(
        '修复成文检查的元数据。只返回符合Schema的JSON补丁，不重写原检查，不回答业务问题。'
        'quotes逐项填写：从该位置actual_text逐字复制能支持原finding的连续引文；不得改object或path。'
        'decisions逐项填写：仅当待决定问题与某finding.decision_question询问同一业务决定时，选择该finding下标。'
        '无法保持原义则不要提交该项，程序会保留失败供核对。下标从0开始。'
        '\n可信任务头：'+dumps(dict(stage='review',schema=SCHEMA)))),
        dict(role='user',content=[dict(type='text',text=dumps(plan))])]


def apply(plan, patch):
    errors=list(jsonschema.Draft202012Validator(SCHEMA).iter_errors(patch))
    require(not errors,'PREWRITE_REPAIR_INVALID','检查修复补丁格式不正确，原内容保留。')
    value=copy.deepcopy(plan['original'])
    quotes={(x['finding'],x['quote']):x for x in plan['quote_slots']}
    require(len(patch['quotes'])==len(quotes) and
            {(x['finding'],x['quote']) for x in patch['quotes']}==set(quotes),
            'PREWRITE_REPAIR_INVALID','检查修复必须覆盖且仅覆盖错误引文。')
    for row in patch['quotes']:
        slot=quotes[row['finding'],row['quote']]
        require(row['text'] in slot['actual_text'],'PREWRITE_REPAIR_INVALID','修复引文仍不在原位置的真实内容中。')
        value['findings'][row['finding']]['evidence_quotes'][row['quote']]['quote']=row['text']
    decisions={x['decision']:x for x in plan['decision_slots']}
    require(len(patch['decisions'])==len(decisions) and
            {x['decision'] for x in patch['decisions']}==set(decisions) and
            len({x['finding'] for x in patch['decisions']})==len(decisions),
            'PREWRITE_REPAIR_INVALID','待确认问题必须逐一关联，不能合并或遗漏。')
    for row in patch['decisions']:
        slot=decisions[row['decision']]
        require(row['finding'] in slot['allowed_findings'],'PREWRITE_REPAIR_INVALID','修复指向没有真实条款关联的问题。')
        finding=value['findings'][row['finding']]
        finding.update(severity='blocker',decision_question=slot['text'])
    return value
