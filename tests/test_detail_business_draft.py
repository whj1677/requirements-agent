"""The DeepSeek text contract carries reviewable definitions, never approval."""
import copy
import json

import pytest

from app.business_draft import adapt, assemble, ROW
from app.detail_draft import canonical_detail, input_detail, model_schema, normalize_redundant_metadata


def test_redundant_uniqueness_evidence_keeps_the_exact_constraint_and_sources():
    proof={'status':'source','sources':[{'source':'S1','locator':'L1'}]}
    field={'id':'F1','constraints':'唯一且非空','evidence':{'constraints':proof,'uniqueness':copy.deepcopy(proof)}}
    normalized,diagnostics=normalize_redundant_metadata({'fields':[field]})
    assert normalized['fields'][0]['constraints']==field['constraints']
    assert normalized['fields'][0]['evidence']=={'constraints':proof}
    assert field['evidence']['uniqueness']==proof
    assert diagnostics[0]['code']=='BUSINESS_DRAFT_DETAIL_REDUNDANT_EVIDENCE'
    for changed in ({'status':'unknown'},{'status':'source','sources':[{'source':'S404','locator':'L1'}]}):
        value=copy.deepcopy(field);value['evidence']['uniqueness']=changed
        preserved,notes=normalize_redundant_metadata({'fields':[value]})
        assert preserved['fields'][0]['evidence']['uniqueness']==changed and not notes
from app.core import Problem
from app.requirement_details import detail_issues, validate_detail
from app.workflow import apply_response, replace_business_ids
from tests.test_business_draft import draft_row, packet, setup


def sample_detail(complex_flow=False):
    field = dict(id='F1', name='联系人姓名', meaning='接收站点沟通的联系人', data_type='text',
        source='由有权人员填写', required='必填', default_value='无默认值', constraints='去首尾空格后1至50字',
        visibility='有本站查看权限的用户可见', editability='仅本站管理员可修改')
    action = dict(id='I1', name='保存联系人', actor='本站管理员', entry='联系人编辑窗口',
        preconditions='具有本站编辑权限', trigger='点击保存', inputs='联系人姓名', validation='姓名为1至50字',
        pending='显示保存中并禁用重复点击', repeat='同次保存重复提交只产生一次修改',
        cancel='尚未提交时关闭窗口，放弃未保存编辑', success='提示保存成功并显示最新姓名',
        failure='明确校验失败时保留输入并显示原因', effects='保存成功后更新本站联系人姓名',field_refs=['F1'])
    nodes = [dict(id='N1',label='打开联系人编辑',kind='start'),
             dict(id='N2',label='填写并保存',kind='action',actor='本站管理员',interaction_id='I1'),
             dict(id='N3',label='输入是否有效',kind='decision',actor='云平台'),
             dict(id='N4',label='更新联系人并提示成功',kind='end'),
             dict(id='N5',label='保留输入并提示校验原因',kind='end')]
    edges = [dict(id='E1',**{'from':'N1','to':'N2'},condition=''),
             dict(id='E2',**{'from':'N2','to':'N3'},condition=''),
             dict(id='E3',**{'from':'N3','to':'N4'},condition='校验通过'),
             dict(id='E4',**{'from':'N3','to':'N5'},condition='校验不通过')]
    if complex_flow:
        field.update(name='处理期限',meaning='告警工单约定完成的时间',data_type='datetime',
                     source='调度员填写',constraints='须晚于提交时刻',time_basis='Asia/Shanghai，按服务端时刻校验')
        action.update(name='按站点批量派单',actor='调度员',entry='告警列表批量派单',
            inputs='勾选告警、各站处理人和处理期限',preconditions='具有选中站点派单权限',
            validation='每站处理人有资格且期限有效；同站整组成功或失败',
            success='展示每站工单及批次最终结果',failure='展示各站失败原因；不补造成功',
            effects='每个成功站点产生一张关联全部选中告警的工单',
            cancel='尚未提交可退出；服务已接受后关闭页面不取消任务')
        nodes=[dict(id='N1',label='选择多站点告警',kind='start'),
            dict(id='N2',label='预览各站处理人和期限后提交',kind='action',actor='调度员',interaction_id='I1'),
            dict(id='N3',label='逐站校验并建单',kind='decision',actor='云平台'),
            dict(id='N4',label='展示该站成功工单',kind='action',actor='云平台'),
            dict(id='N5',label='展示该站失败原因',kind='action',actor='云平台'),
            dict(id='N6',label='核对原提交结果，禁止另建新请求',kind='wait',actor='云平台'),
            dict(id='N7',label='全部站点均已有确定结果',kind='decision',actor='云平台'),
            dict(id='N8',label='展示全部成功、部分成功或全部失败',kind='end')]
        edges=[dict(id='E'+str(i+1),**{'from':a,'to':b},condition=c) for i,(a,b,c) in enumerate([
            ('N1','N2',''),('N2','N3',''),('N3','N4','确认整站建单成功'),('N3','N5','确认整站未建单且失败'),
            ('N3','N6','提交结果未知'),('N6','N4','查询确认成功'),('N6','N5','查询确认未建单且原执行已终止'),
            ('N4','N7',''),('N5','N7',''),('N7','N3','仍有未执行站点'),('N7','N8','所有站点结果确定')])]
    return dict(schema_version=1,applicability={k:dict(applicable=k!='state_transitions',
        reason='本示例只定义派单与结果，不含后续工单状态' if k=='state_transitions' else '')
        for k in ('fields','interactions','flow','state_transitions')},
        fields=[field],interactions=[action],flow=dict(nodes=nodes,edges=edges),state_transitions=[])


def test_detail_model_roundtrip_uses_local_source_aliases():
    detail=sample_detail()
    detail['fields'][0]['evidence']={'constraints':dict(status='source',source_refs=[dict(source_id='SRC-x',excerpt_id='EX-x')])}
    raw=input_detail(detail,{('SRC-x','EX-x'):dict(source='S1',locator='L1')},{})
    assert 'SRC-x' not in json.dumps(raw)
    assert canonical_detail(raw,{('S1','L1'):dict(source_id='SRC-x',excerpt_id='EX-x')},{})==detail
    validate_detail(detail)
    assert not detail_issues(dict(id='REQ-x',detail_contract=sample_detail()))


def test_detail_bad_source_never_borrows_context():
    raw=sample_detail()
    raw['fields'][0]['evidence']={'constraints':dict(status='source',sources=[dict(source='S99',locator='L1')])}
    with pytest.raises(Problem,match='资料位置'):
        canonical_detail(raw,{}, {})


def test_model_cannot_submit_approval_in_detail():
    import jsonschema
    raw=sample_detail();raw['confirmed']=True
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(raw,model_schema())


def test_cloud_draft_requires_detail_without_migrating_saved_content(tmp_path):
    p,local,cfg,_,excerpts,_=setup(tmp_path)
    p.setdefault('product_context',{})['product']='储能云平台'
    before=copy.deepcopy(p)
    messages,_,_=assemble(p,'clarify','完善此行',cfg,tmp_path,local)
    header=json.loads(messages[0]['content'].split('可信任务头：')[1])
    assert header['detail_required'] is True
    assert 'detail_contract' in header['schema']['properties']['requirements']['items']['required']
    value,_=adapt(packet(draft_row()),p,excerpts,'clarify',local)
    req=next(i for i in value['proposals'] if i['kind']=='requirement')
    assert req['detail_required'] is True
    assert any(i['code']=='DETAIL_REQUIRED' for i in detail_issues(req))
    assert p==before


def test_detail_candidate_is_persisted_unconfirmed_and_keeps_new_acceptance_refs(tmp_path):
    p,local,_,_,excerpts,_=setup(tmp_path)
    row=draft_row();row['detail_contract']=sample_detail()
    row['detail_contract']['interactions'][0]['acceptance_refs']=['A1']
    value,report=adapt(packet(row),p,excerpts,'clarify',local)
    assert report['accepted'], report
    assert not report['rejected'], report
    apply_response(p,value,identity_only=True)
    candidate=next(i for i in reversed(p['items']) if i.get('detail_contract'))
    assert candidate['selection_status']=='candidate'
    assert 'requirement_confirmation' not in candidate
    ref=candidate['detail_contract']['interactions'][0]['acceptance_refs'][0]
    assert any(i['id']==ref and i['kind']=='acceptance' for i in p['items'])


def test_plain_prose_revision_cannot_silently_erase_details(tmp_path):
    p,local,_,_,excerpts,_=setup(tmp_path)
    p['items'][0]['detail_contract']=sample_detail()
    value,report=adapt(packet(draft_row()),p,excerpts,'clarify',local)
    assert not report['rejected'], report
    candidate=next(i for i in value['proposals'] if i['kind']=='requirement')
    assert candidate['detail_contract']==p['items'][0]['detail_contract']


@pytest.mark.parametrize('bad_source',[False,True])
def test_invalid_replacement_detail_preserves_existing_definition_and_independent_fields(tmp_path,bad_source):
    p,local,_,_,excerpts,_=setup(tmp_path)
    p['items'][0]['detail_contract']=sample_detail()
    before=copy.deepcopy(p)
    row=draft_row();row['detail_contract']=sample_detail()
    row['detail_contract']['flow']['field_refs']=['F1']
    if bad_source:
        row['detail_contract']['fields'][0]['evidence']={'meaning':dict(status='source',sources=[dict(source='S404',locator='L1')])}
    value,report=adapt(packet(row),p,excerpts,'clarify',local)
    assert p==before
    if bad_source:
        assert not value['proposals'] and report['rejected'][0]['code']=='BUSINESS_DRAFT_SOURCE'
    else:
        assert report['status']=='partial' and not report['rejected']
        assert report['field_rejected'][0]['path']==['requirements',0,'detail_contract']
        assert report['field_rejected'][0]['requirement_id']==p['items'][0]['id']
        candidate=next(i for i in value['proposals'] if i['kind']=='requirement')
        assert candidate['detail_contract']==p['items'][0]['detail_contract']
        assert any(i['kind']=='acceptance' for i in value['proposals'])


def test_detail_candidate_cannot_relabel_acceptance_as_rule(tmp_path):
    p,local,_,_,excerpts,_=setup(tmp_path)
    row=draft_row();row['detail_contract']=sample_detail()
    row['detail_contract']['interactions'][0]['rule_refs']=['A1']
    before=copy.deepcopy(p)
    value,report=adapt(packet(row),p,excerpts,'clarify',local)
    assert not value['proposals']
    assert report['rejected'][0]['code']=='DETAIL_ITEM_REFERENCE'
    assert p==before


def test_reference_evidence_is_not_mistaken_for_identity_array(tmp_path):
    p,local,_,_,excerpts,_=setup(tmp_path)
    row=draft_row();row['detail_contract']=sample_detail()
    action=row['detail_contract']['interactions'][0]
    action['acceptance_refs']=['A1']
    action['evidence']={'acceptance_refs':{'status':'proposal','reason':'建议关联本条验收'}}
    value,report=adapt(packet(row),p,excerpts,'clarify',local)
    assert not report['rejected'],report
    assert next(i for i in value['proposals'] if i['kind']=='requirement')['detail_contract']


@pytest.mark.parametrize('kind', ['depends_on', 'rule_refs', 'acceptance_refs'])
def test_unavailable_saved_reference_blocks_input_with_location_and_preserves_definition(kind):
    detail=sample_detail()
    container=detail if kind=='depends_on' else detail['interactions'][0]
    container[kind]=['SAVED-unavailable']
    before=copy.deepcopy(detail)
    with pytest.raises(Problem) as error:
        input_detail(detail,{}, {})
    assert error.value.code=='BUSINESS_DRAFT_DETAIL_REFERENCE'
    assert 'SAVED-unavailable' in error.value.message
    expected='detail_contract.'+kind if kind=='depends_on' else 'detail_contract.interactions.I1.'+kind
    assert expected in error.value.message
    assert detail==before


def test_detail_alias_roundtrip_keeps_all_available_saved_relationships():
    detail=sample_detail()
    detail['depends_on']=['REQ-parent']
    detail['interactions'][0].update(rule_refs=['RULE-1'],acceptance_refs=['AC-1'])
    aliases={'REQ-parent':'R2','RULE-1':'C1','AC-1':'C2'}
    model=input_detail(detail,{}, aliases)
    assert model['depends_on']==['R2']
    assert model['interactions'][0]['rule_refs']==['C1']
    output=canonical_detail(model,{}, {alias:iid for iid,alias in aliases.items()},existing_detail=detail)
    assert output==detail


def test_direct_canonical_adaptation_cannot_erase_unavailable_saved_reference():
    detail=sample_detail()
    detail['depends_on']=['REQ-unavailable']
    proposed=sample_detail()
    with pytest.raises(Problem) as error:
        canonical_detail(proposed,{}, {},existing_detail=detail)
    assert error.value.code=='BUSINESS_DRAFT_DETAIL_REFERENCE'
    assert detail['depends_on']==['REQ-unavailable']


def test_duplicate_new_acceptance_alias_maps_to_actual_deduplicated_candidate(tmp_path):
    p,local,_,_,excerpts,_=setup(tmp_path)
    row=draft_row();row['acceptance'].append(copy.deepcopy(row['acceptance'][0]))
    row['detail_contract']=sample_detail()
    row['detail_contract']['interactions'][0]['acceptance_refs']=['A2']
    value,report=adapt(packet(row),p,excerpts,'clarify',local)
    assert report['accepted'] and not report['rejected'],report
    clauses=[item for item in value['proposals'] if item['kind']=='acceptance']
    assert len(clauses)==1
    req=next(item for item in value['proposals'] if item.get('detail_contract'))
    assert req['detail_contract']['interactions'][0]['acceptance_refs']==[clauses[0]['temp_id']]
    apply_response(p,value,identity_only=True)
    candidate=next(item for item in reversed(p['items']) if item.get('detail_contract'))
    referenced=candidate['detail_contract']['interactions'][0]['acceptance_refs']
    assert all(any(item['id']==iid and item['kind']=='acceptance' for item in p['items']) for iid in referenced)


def test_adapt_cannot_hide_a_saved_unavailable_reference(tmp_path):
    p,local,_,_,excerpts,_=setup(tmp_path)
    p['items'][0]['detail_contract']=sample_detail()
    p['items'][0]['detail_contract']['depends_on']=['REQ-missing']
    before=copy.deepcopy(p)
    row=draft_row();row['detail_contract']=sample_detail()
    value,report=adapt(packet(row),p,excerpts,'clarify',local)
    assert not report['accepted']
    assert report['rejected'][0]['code']=='BUSINESS_DRAFT_DETAIL_REFERENCE'
    assert 'detail_contract.depends_on' in report['rejected'][0]['reason']
    assert value['proposals']==[] and p==before


def test_redundant_output_index_is_traceable_and_never_mutates_raw(tmp_path):
    p,local,_,_,excerpts,_=setup(tmp_path)
    row=draft_row();row['detail_contract']=sample_detail()
    row['detail_contract']['interactions'][0]['index']=1
    raw=packet(row);before=copy.deepcopy(raw)
    value,report=adapt(raw,p,excerpts,'clarify',local)
    assert report['accepted'] and not report['rejected'],report
    assert raw==before
    diagnostic=report['detail_normalized'][0]
    assert diagnostic['path']=='detail_contract.interactions.I1.index'
    assert diagnostic['raw']==1
    assert report['accepted'][0]['raw']['detail_contract']['interactions'][0]['index']==1
    assert report['accepted'][0]['notes']
    candidate=next(i for i in value['proposals'] if i.get('detail_contract'))
    assert 'index' not in candidate['detail_contract']['interactions'][0]
    assert 'requirement_confirmation' not in candidate


@pytest.mark.parametrize('extra', [dict(index=99),dict(index='1'),dict(index=True),dict(approved=True)])
def test_unknown_or_nonredundant_metadata_still_rejects_entire_row(tmp_path,extra):
    p,local,_,_,excerpts,_=setup(tmp_path)
    row=draft_row();row['detail_contract']=sample_detail()
    row['detail_contract']['interactions'][0].update(extra)
    value,report=adapt(packet(row),p,excerpts,'clarify',local)
    assert not value['proposals'] and report['rejected'][0]['code']=='BUSINESS_DRAFT_INVALID'


def test_index_with_business_evidence_is_not_silently_removed():
    detail=sample_detail()
    detail['interactions'][0].update(index=1,evidence={'index':{'status':'proposal'}})
    normalized,diagnostics=normalize_redundant_metadata(detail)
    assert normalized==detail and not diagnostics


def test_only_exact_duplicate_edge_relation_is_normalized():
    detail=sample_detail()
    detail['flow']['nodes'][0]['interaction_id']='I1'
    detail['flow']['edges'][0]['interaction_id']='I1'
    original=copy.deepcopy(detail)
    normalized,diagnostics=normalize_redundant_metadata(detail)
    assert detail==original
    assert 'interaction_id' not in normalized['flow']['edges'][0]
    assert diagnostics[0]['path']=='detail_contract.flow.edges.E1.interaction_id'
    assert diagnostics[0]['raw']=='I1'
    assert normalized['flow']['nodes']==original['flow']['nodes']
    detail['flow']['nodes'][0].pop('interaction_id')
    normalized,diagnostics=normalize_redundant_metadata(detail)
    assert normalized==detail and not diagnostics


def test_format_normalization_never_accepts_forged_sources(tmp_path):
    p,local,_,_,excerpts,_=setup(tmp_path)
    row=draft_row();row['detail_contract']=sample_detail()
    row['detail_contract']['interactions'][0].update(index=1,evidence={
        'actor':dict(status='source',sources=[dict(source='S99',locator='L1')])})
    value,report=adapt(packet(row),p,excerpts,'clarify',local)
    assert not value['proposals'] and report['rejected'][0]['code']=='BUSINESS_DRAFT_SOURCE'


def test_normalized_candidate_keeps_wait_without_exit_blocked(tmp_path):
    p,local,_,_,excerpts,_=setup(tmp_path)
    row=draft_row();row['detail_contract']=sample_detail()
    row['detail_contract']['interactions'][0]['index']=1
    row['detail_contract']['flow']['nodes'][-1].update(kind='wait',actor='云平台',label='等待原提交结果')
    value,report=adapt(packet(row),p,excerpts,'clarify',local)
    assert report['accepted'] and not report['rejected'],report
    candidate=next(i for i in value['proposals'] if i.get('detail_contract'))
    issues=detail_issues(candidate)
    assert any('N5' in issue['path'] and issue['code']=='DETAIL_FLOW_WAIT' for issue in issues),issues


def test_exact_text_envelope_preserves_proposal_and_references(tmp_path):
    p,local,_,_,excerpts,_=setup(tmp_path)
    row=draft_row();row['detail_contract']=sample_detail()
    field=row['detail_contract']['fields'][0]
    field['constraints']={'text':'保留这条建议原文。','basis':'recommendation','sources':[{'source':'S1','locator':'L1'}]}
    field.setdefault('evidence',{})['constraints']={'status':'proposal'}
    raw=packet(row);before=copy.deepcopy(raw)
    value,report=adapt(raw,p,excerpts,'clarify',local)
    assert report['accepted'] and not report['rejected'],report
    candidate=next(i for i in value['proposals'] if i.get('detail_contract'))
    result=candidate['detail_contract']['fields'][0]
    assert result['constraints']=='保留这条建议原文。'
    assert result['evidence']['constraints']['status']=='proposal'
    assert result['evidence']['constraints']['source_refs']
    assert report['detail_normalized'][0]['code']=='BUSINESS_DRAFT_DETAIL_VALUE_ENVELOPE'
    assert raw==before


@pytest.mark.parametrize('change',['conflict','extra','forged','wrong_type'])
def test_text_envelope_cannot_hide_conflicts_or_forged_sources(tmp_path,change):
    p,local,_,_,excerpts,_=setup(tmp_path)
    row=draft_row();row['detail_contract']=sample_detail();field=row['detail_contract']['fields'][0]
    field['constraints']={'text':'原文。','basis':'recommendation','sources':[]}
    field.setdefault('evidence',{})['constraints']={'status':'proposal'}
    if change=='conflict':field['evidence']['constraints']['status']='source'
    if change=='extra':field['constraints']['approved']=True
    if change=='forged':field['constraints']['sources']=[{'source':'S99','locator':'L1'}]
    if change=='wrong_type':field['constraints']['text']=['不能合并']
    _,report=adapt(packet(row),p,excerpts,'clarify',local)
    assert not report['accepted'] and report['rejected']


def test_generation_schema_requires_enum_values_and_plain_text_stays_supported():
    import jsonschema
    schema=model_schema(generation=True)['properties']['fields']['items']
    field=sample_detail()['fields'][0];field['evidence']={key:{'status':'proposal'} for key in ('name','meaning','data_type','source','required','default_value','constraints','visibility','editability')}
    jsonschema.validate(field,schema)
    field['data_type']='enum'
    with pytest.raises(jsonschema.ValidationError):jsonschema.validate(field,schema)
    field['enum_values']=[]
    with pytest.raises(jsonschema.ValidationError):jsonschema.validate(field,schema)
    field['enum_values']=['已保存','未保存'];jsonschema.validate(field,schema)
