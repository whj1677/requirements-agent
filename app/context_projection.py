"""Lossless model-input representation; stored facts and validators stay literal."""
import copy
import hashlib
import json

from .core import Problem

FACT_GROUPS=('facts','current_clause_assertions','historical_clause_assertions')
VERSION='context-projection-1'


def contract():
    return dict(version=VERSION,
        quote_ref='仅context_projection登记的result_condition_facts长quote可用quote_ref代替：source_excerpt指向本次excerpts的source_id/excerpt_id；item_field指向本次条目object_id的稳定字段path。start/end为零起点Unicode字符位置，end不含；text_sha256核对整个锚点，quote_sha256校验该精确原句。不做Unicode/换行归一化。原文仍在本次输入中，必须依该引用读全句，不用引用元数据代替业务依据。',
        detail_delta='仅历史deferred条目的detail_contract可为detail-delta-1：按base_object_id当前条目的detail_contract及base_content_version/base_sha256取基底，依次应用operations恢复该历史定义，result_sha256核对恢复结果。path数组字符串为字典键、整数为数组位置；add/remove/replace只改变该历史副本。未变部分继承基底，差异及该条目的身份/未知/建议/来源全部保留，不表示历史条目已采纳。',
        identity='facts是来源观察辅助谓词，仍核原文，不是当前规范；current_clause_assertions仅为本次规范白名单条目断言，historical_clause_assertions是历史/未采纳/范围外断言。身份不证明语义正确，source_statement或projection不升级canonical。',
        output='quote_ref、detail-delta-1、operations及分层辅助信息仅为输入表示；响应仍严格使用现行业务JSON schema，不回传这些内部字段，不把它们当新业务条款。')


def _json(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def _hash(value):return hashlib.sha256(value.encode('utf-8')).hexdigest()
def _fail(message):raise Problem('CONTEXT_PROJECTION_INVALID',message,model_repair_allowed=False)
def _check(condition,message):
    if not condition:_fail(message)


def _rows(context):
    result=list(context.get('items',[]))
    for group in ('A_normative','B_statements_answers','C_discussion'):
        for value in context.get(group,{}).values():
            if isinstance(value,list):result.extend(value)
    return [row for row in result if isinstance(row,dict) and 'id' in row and 'kind' in row]


def _one(rows,identity):
    matches=[row for row in rows if identity(row)]
    _check(len(matches)==1,'引用不存在或身份不唯一')
    return matches[0]


def _field(item,path):
    _check(isinstance(path,str) and bool(path),'条目字段路径无效')
    value=item
    for part in path.split('.'):
        if isinstance(value,list):value=_one(value,lambda row:isinstance(row,dict) and row.get('id')==part)
        else:
            _check(isinstance(value,dict) and part in value,'条目字段路径不存在')
            value=value[part]
    return value


def _facts(context):
    projection=context.get('result_condition_facts',{})
    for key in FACT_GROUPS:
        for fact in projection.get(key,[]):yield fact


def _quote(ref,context):
    _check(isinstance(ref,dict),'原句引用必须为对象')
    kind=ref.get('kind')
    if kind=='source_excerpt':
        row=_one(context.get('excerpts',[]),lambda e:e.get('source_id')==ref.get('source_id') and e.get('id')==ref.get('excerpt_id'))
        text=row.get('text')
    elif kind=='item_field':
        item=_one(_rows(context),lambda i:i['id']==ref.get('object_id'))
        text=_field(item,ref.get('path'))
    else:_fail('未知原句引用类型')
    start,end=ref.get('start'),ref.get('end')
    _check(isinstance(text,str) and type(start) is int and type(end) is int and 0<=start<end<=len(text),'原句字符范围无效')
    _check(_hash(text)==ref.get('text_sha256'),'原句锚点全文校验不一致')
    quote=text[start:end]
    _check(_hash(quote)==ref.get('quote_sha256'),'原句引用校验不一致')
    return quote


def _anchor(fact,context):
    quote=fact.get('quote')
    if not isinstance(quote,str) or not quote:return None
    location=fact.get('location','')
    try:
        if fact.get('origin')=='source_statement' and location.startswith('source:'):
            parts=location.split(':',2)
            if len(parts)!=3:return None
            row=_one(context.get('excerpts',[]),lambda e:e.get('source_id')==parts[1] and e.get('id')==parts[2])
            text=row.get('text')
            ref=dict(kind='source_excerpt',source_id=parts[1],excerpt_id=parts[2])
        elif fact.get('object_id') and location.startswith(fact['object_id']+'.'):
            path=location[len(fact['object_id'])+1:]
            item=_one(_rows(context),lambda i:i['id']==fact['object_id'])
            text=_field(item,path)
            ref=dict(kind='item_field',object_id=fact['object_id'],path=path)
        else:return None
        if not isinstance(text,str) or quote not in text:return None
        start=text.index(quote)
        ref.update(start=start,end=start+len(quote),text_sha256=_hash(text),quote_sha256=_hash(quote))
        return ref
    except Problem:return None  # No exact/unique anchor: retain the original quote.


def _patch(base,target,path=()):
    if base==target:return []
    if isinstance(base,dict) and isinstance(target,dict):
        ops=[dict(op='remove',path=list(path+(key,))) for key in sorted(base.keys()-target.keys())]
        for key in sorted(target):
            ops.extend([dict(op='add',path=list(path+(key,)),value=copy.deepcopy(target[key]))] if key not in base
                       else _patch(base[key],target[key],path+(key,)))
        return ops
    if isinstance(base,list) and isinstance(target,list) and len(base)==len(target):
        return [op for index,(left,right) in enumerate(zip(base,target)) for op in _patch(left,right,path+(index,))]
    return [dict(op='replace',path=list(path),value=copy.deepcopy(target))]


def _apply(base,operations):
    _check(isinstance(operations,list),'历史差异操作必须为数组')
    result=copy.deepcopy(base)
    for operation in operations:
        _check(isinstance(operation,dict) and operation.get('op') in ('add','remove','replace'),'历史差异操作无效')
        path=operation.get('path');kind=operation['op']
        _check(isinstance(path,list) and all(type(part) in (int,str) for part in path),'历史差异路径无效')
        _check(set(operation)==({'op','path'} if kind=='remove' else {'op','path','value'}),'历史差异字段无效')
        if not path:
            _check(kind=='replace','仅replace可替换整个历史定义')
            result=copy.deepcopy(operation['value']);continue
        parent=result
        for part in path[:-1]:
            _check(isinstance(parent,dict) and part in parent or isinstance(parent,list) and type(part) is int and 0<=part<len(parent),'历史差异父路径不存在')
            parent=parent[part]
        key=path[-1]
        exists=(isinstance(parent,dict) and key in parent or isinstance(parent,list) and type(key) is int and 0<=key<len(parent))
        _check(isinstance(parent,(dict,list)),'历史差异目标不是容器')
        _check((kind=='add' and isinstance(parent,dict) and isinstance(key,str) and not exists) or (kind in ('remove','replace') and exists),'历史差异目标不存在或操作不匹配')
        if kind=='remove':del parent[key]
        else:parent[key]=copy.deepcopy(operation['value'])
    return result


def materialize(context):
    """Restore the exact pre-projection value or fail explicitly on bad input."""
    restored=copy.deepcopy(context)
    registry=restored.pop('context_projection',None)
    if registry is None:return restored
    _check(isinstance(registry,dict) and registry.get('version')==VERSION,'输入投影登记版本无效')
    for oid in registry.get('detail_item_ids',[]):
        item=_one(_rows(restored),lambda row:row['id']==oid)
        encoded=item.get('detail_contract')
        _check(isinstance(encoded,dict) and encoded.get('projection')=='detail-delta-1','登记的历史差异不存在')
        _check(item.get('selection_status')=='deferred' and item.get('target_item_id')==encoded.get('base_object_id'),'历史差异身份不一致')
        base=_one(_rows(restored),lambda i:i['id']==encoded.get('base_object_id'))
        _check(base['id'] in registry.get('current_normative_ids',[]) and item.get('kind')==base.get('kind') and encoded.get('base_path')=='detail_contract','历史差异基底不属于本次同类规范范围')
        _check(base.get('selection_status')=='selected' and base.get('content_version') is not None and base['content_version']==encoded.get('base_content_version'),'历史差异基底版本不一致')
        detail=base.get('detail_contract')
        _check(isinstance(detail,dict) and _hash(_json(detail))==encoded.get('base_sha256'),'历史差异基底内容不一致')
        value=_apply(detail,encoded.get('operations'))
        _check(_hash(_json(value))==encoded.get('result_sha256'),'恢复后的历史定义校验不一致')
        item['detail_contract']=value
    for entry in registry.get('quote_entries',[]):
        _check(isinstance(entry,dict) and entry.get('group') in FACT_GROUPS and type(entry.get('index')) is int,'原句投影登记无效')
        rows=restored.get('result_condition_facts',{}).get(entry['group'],[])
        _check(0<=entry['index']<len(rows),'原句投影登记位置不存在')
        fact=rows[entry['index']]
        _check('quote' not in fact and 'quote_ref' in fact,'登记的原句引用不存在或与原文重复')
        fact['quote']=_quote(fact.pop('quote_ref'),restored)
    _check(_hash(_json(restored))==registry.get('original_sha256'),'还原后的上下文校验不一致')
    return restored


def project(context,current_normative_ids):
    """Only replace beneficial representations; prove full restoration locally."""
    projected=copy.deepcopy(context)
    _check('context_projection' not in context,'输入已经含有投影登记，不能再次投影')
    registry=dict(version=VERSION,quote_entries=[],detail_item_ids=[],
        current_normative_ids=sorted(set(current_normative_ids)),original_sha256=_hash(_json(context)))
    for group in FACT_GROUPS:
        for index,fact in enumerate(projected.get('result_condition_facts',{}).get(group,[])):
            ref=_anchor(fact,context)
            if ref and len(_json({'quote_ref':ref}))<len(_json({'quote':fact['quote']})):
                fact.pop('quote');fact['quote_ref']=ref
                registry['quote_entries'].append(dict(group=group,index=index))
    current=set(registry['current_normative_ids'])
    for item in _rows(projected):
        if item.get('selection_status')!='deferred' or item.get('target_item_id') not in current:continue
        old=item.get('detail_contract')
        if not isinstance(old,dict) or 'projection' in old:continue
        try:base=_one(_rows(context),lambda i:i['id']==item['target_item_id'])
        except Problem:continue
        detail=base.get('detail_contract')
        if not isinstance(detail,dict) or base.get('selection_status')!='selected' or base.get('content_version') is None or item.get('kind')!=base.get('kind'):continue
        delta=dict(projection='detail-delta-1',base_object_id=base['id'],base_path='detail_contract',base_content_version=base['content_version'],
            base_sha256=_hash(_json(detail)),result_sha256=_hash(_json(old)),operations=_patch(detail,old))
        if len(_json(delta))<len(_json(old)):
            item['detail_contract']=delta;registry['detail_item_ids'].append(item['id'])
    if registry['quote_entries'] or registry['detail_item_ids']:
        projected['context_projection']=registry
        if len(_json(projected))>=len(_json(context)):return copy.deepcopy(context)
    _check(materialize(projected)==context,'输入投影无法逐值还原')
    return projected
