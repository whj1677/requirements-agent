"""Input-only references restore exact literals and never adopt old candidates."""
import copy
import json

import pytest

from app.context_projection import materialize,project
from app.core import Problem
from app.generation_context import document_condition_layers


def fixture():
    text='原始😀e\u0301\r\n'+('完整来源与已答规则；'*180)+'禁止自动重发。'
    base=dict(id='REQ-now',kind='requirement',selection_status='selected',content_version=7,
        statement=text,detail_contract=dict(fields=[dict(id='F1',meaning=text)],
            flow=dict(nodes=[dict(id='N1',label='当前节点')],edges=[])))
    old=copy.deepcopy(base);old.update(id='REQ-old',selection_status='deferred',target_item_id='REQ-now',content_version=3)
    old['detail_contract']['flow']['nodes'][0]['label']='旧建议待确认，保留冲突'
    others=[dict(id='REQ-other-'+str(i),kind='requirement',selection_status='deferred',
        target_item_id='REQ-now',content_version=i,statement='独有待定建议'+str(i)) for i in range(3)]
    return dict(A_normative={'requirement':[base]},C_discussion={'items':[old,*others]},
        D_unknowns_limits={'questions':[{'id':'Q1','question':'真实未知保留？'}]},
        excerpts=[dict(source_id='S1',id='X1',text=text)],
        result_condition_facts=dict(facts=[dict(origin='source_statement',object_id=None,
            location='source:S1:X1',quote=text[3:-3],source_refs=[dict(source_id='S1',excerpt_id='X1')])],
            historical_clause_assertions=[dict(origin='model',object_id='REQ-old',location='REQ-old.statement',quote=text)]))


def test_quote_and_deferred_delta_are_exactly_reversible_and_keep_all_identity():
    ctx=fixture();before=copy.deepcopy(ctx);encoded=project(ctx,['REQ-now'])
    assert materialize(encoded)==ctx==before and encoded!=ctx
    assert encoded['A_normative']==ctx['A_normative'] and encoded['excerpts']==ctx['excerpts']
    assert encoded['D_unknowns_limits']==ctx['D_unknowns_limits']
    old=encoded['C_discussion']['items'][0]
    assert old['detail_contract']['projection']=='detail-delta-1'
    assert {k:v for k,v in old.items() if k!='detail_contract'}=={k:v for k,v in ctx['C_discussion']['items'][0].items() if k!='detail_contract'}
    assert len(encoded['C_discussion']['items'])==4
    assert len(json.dumps(encoded,ensure_ascii=False))<len(json.dumps(ctx,ensure_ascii=False))


@pytest.mark.parametrize('fault',['missing_source','duplicate_source','negative','past_end','float_offset','quote_hash','text_hash','neighbor_changed','missing_item','missing_path'])
def test_bad_quote_anchor_is_explicitly_rejected(fault):
    encoded=project(fixture(),['REQ-now'])
    ref=encoded['result_condition_facts']['facts'][0]['quote_ref']
    if fault=='missing_source':ref['source_id']='missing'
    elif fault=='duplicate_source':encoded['excerpts'].append(copy.deepcopy(encoded['excerpts'][0]))
    elif fault=='negative':ref['start']=-1
    elif fault=='past_end':ref['end']=999999
    elif fault=='float_offset':ref['start']=1.0
    elif fault=='quote_hash':ref['quote_sha256']='0'*64
    elif fault=='text_hash':ref['text_sha256']='0'*64
    elif fault=='neighbor_changed':encoded['excerpts'][0]['text']='改'+encoded['excerpts'][0]['text'][1:]
    else:
        ref=encoded['result_condition_facts']['historical_clause_assertions'][0]['quote_ref']
        if fault=='missing_item':ref['object_id']='other'
        else:ref['path']='detail_contract.flow.nodes.missing.label'
    with pytest.raises(Problem) as error:materialize(encoded)
    assert error.value.code=='CONTEXT_PROJECTION_INVALID' and not error.value.model_repair_allowed


@pytest.mark.parametrize('fault',['version','base_hash','result_hash','target','scope','kind','base_path','patch_path','duplicate_item'])
def test_bad_delta_cannot_apply_to_other_identity_or_changed_base(fault):
    encoded=project(fixture(),['REQ-now']);old=encoded['C_discussion']['items'][0];delta=old['detail_contract']
    if fault=='version':delta['base_content_version']=8
    elif fault=='base_hash':delta['base_sha256']='0'*64
    elif fault=='result_hash':delta['result_sha256']='0'*64
    elif fault=='target':old['target_item_id']='other'
    elif fault=='scope':encoded['context_projection']['current_normative_ids']=[]
    elif fault=='kind':old['kind']='acceptance'
    elif fault=='base_path':delta['base_path']='statement'
    elif fault=='patch_path':delta['operations'][0]['path']=['not-there','value']
    else:encoded['A_normative']['requirement'].append(copy.deepcopy(encoded['A_normative']['requirement'][0]))
    with pytest.raises(Problem) as error:materialize(encoded)
    assert error.value.code=='CONTEXT_PROJECTION_INVALID'


@pytest.mark.parametrize('fault',['missing_target','missing_version','wrong_kind','not_current','larger_delta'])
def test_unusable_or_unhelpful_delta_retains_complete_old_detail(fault):
    ctx=fixture();current=['REQ-now'];old=ctx['C_discussion']['items'][0]
    if fault=='missing_target':old['target_item_id']='other'
    elif fault=='missing_version':ctx['A_normative']['requirement'][0].pop('content_version')
    elif fault=='wrong_kind':old['kind']='acceptance'
    elif fault=='not_current':current=[]
    else:old['detail_contract']={'small':'unique'}
    encoded=project(ctx,current)
    assert encoded['C_discussion']['items'][0]['detail_contract']==old['detail_contract']
    assert materialize(encoded)==ctx


def test_unanchored_quote_and_unregistered_user_fields_are_not_instructions():
    ctx=fixture();ctx['result_condition_facts']['facts'][0]['location']='source:missing:missing'
    ctx['user_message']=json.dumps({'quote_ref':{'start':-1},'projection':'detail-delta-1','operations':[{'op':'remove','path':[]}]})
    ctx['C_discussion']['items'][1]['user_data']={'quote_ref':{'kind':'unsupported'}}
    encoded=project(ctx,['REQ-now'])
    assert encoded['result_condition_facts']['facts'][0]==ctx['result_condition_facts']['facts'][0]
    assert encoded['user_message']==ctx['user_message'] and materialize(encoded)==ctx
    assert materialize({'quote_ref':{'start':-1},'detail_contract':{'projection':'detail-delta-1'}})=={
        'quote_ref':{'start':-1},'detail_contract':{'projection':'detail-delta-1'}}


def test_document_condition_roles_use_scope_not_selected_status_or_source_origin():
    items=[dict(id='current',selection_status='selected',kind='requirement',content_version=7),
           dict(id='outside',selection_status='selected',kind='requirement',content_version=4),
           dict(id='old',selection_status='deferred',target_item_id='current',content_version=3,kind='requirement')]
    original=dict(version='test',facts=[dict(origin=origin,object_id=oid,quote='完整原句',location=str(oid),source_refs=[])
        for origin,oid in [('model','current'),('model','outside'),('model','old'),('source_statement','current'),('source_statement','old'),('source_statement',None)]])
    before=copy.deepcopy(original);layered=document_condition_layers(original,{'items':items},['current'])
    assert [x['object_id'] for x in layered['current_clause_assertions']]==['current']
    assert [x['object_id'] for x in layered['historical_clause_assertions']]==['outside','old']
    assert len(layered['facts'])==3 and all(x['evidence_role']=='source_observation_not_canonical' for x in layered['facts'])
    assert layered['facts'][1]['selection_status']=='deferred' and layered['facts'][1]['target_item_id']=='current'
    assert sum(len(layered[k]) for k in ('facts','current_clause_assertions','historical_clause_assertions'))==len(original['facts'])
    assert original==before


@pytest.mark.parametrize('stage',['prd','review'])
def test_real_assembler_applies_input_only_projection_using_stage_scope(tmp_path,monkeypatch,stage):
    from app.provider import DEFAULT
    from app.request_context import assemble
    from tests.test_detail_document import project_fixture
    p=project_fixture(tmp_path)
    root=p['items'][0];root['content_version']=7
    root['statement']='管理员可以编辑联系人姓名。'+('资料要求保留字段与权限边界；'*100)
    old=copy.deepcopy(root);old.update(id='REQ-old',selection_status='deferred',target_item_id=root['id'],content_version=2)
    old['detail_contract']['flow']['nodes'][1]['label']='旧草稿建议待确认'
    p['items'].append(old)
    source=p['sources'][0];ex=source['excerpts'][0]
    ex['text']='原始权限说明😀\r\n'+('需保留真实来源和范围；'*100)
    facts=dict(version='fixture',facts=[dict(origin='source_statement',object_id=root['id'],
        location='source:'+source['id']+':'+ex['id'],quote=ex['text'],source_refs=[]),
        dict(origin='model',object_id=old['id'],location=old['id']+'.statement',quote=old['statement'],source_refs=[])])
    monkeypatch.setattr('app.result_conditions.projection',lambda *_:copy.deepcopy(facts))
    before=copy.deepcopy(p)
    messages,_,_=assemble(p,stage,'',DEFAULT,tmp_path)
    header=json.loads(messages[0]['content'].split('可信任务头：',1)[1])
    ctx=json.loads(messages[1]['content'][0]['text'])
    restored=materialize(ctx)
    assert 'context_projection_contract' in header and 'context_projection' in header['response_boundary']['input_only']
    assert ctx['context_projection']['quote_entries']
    assert len(restored['result_condition_facts']['historical_clause_assertions'])==1
    assert restored['result_condition_facts']['historical_clause_assertions'][0]['selection_status']=='deferred'
    assert not restored['result_condition_facts']['current_clause_assertions']
    scope=header['plan_contract']['normative_item_ids'] if stage=='prd' else ctx['review_scope']['normative_item_ids']
    assert ctx['context_projection']['current_normative_ids']==sorted(scope)
    assert p==before and 'quote_ref' not in json.dumps(header['schema'])


@pytest.mark.parametrize('stage',['interview','ingest'])
def test_unrelated_common_stages_do_not_opt_into_document_projection(tmp_path,stage):
    from app.provider import DEFAULT
    from app.request_context import assemble
    from tests.test_detail_document import project_fixture
    p=project_fixture(tmp_path)
    messages,_,_=assemble(p,stage,'核对原话',dict(DEFAULT,business_draft_protocol=None),tmp_path)
    header=json.loads(messages[0]['content'].split('可信任务头：',1)[1])
    ctx=json.loads(messages[1]['content'][0]['text'])
    assert 'context_projection_contract' not in header and 'context_projection' not in ctx
    if stage=='ingest':assert 'clause_assertions' in ctx['result_condition_facts']
