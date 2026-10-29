"""Synthetic definitions and isolated stores; no model or daily-workbench calls."""
import copy

import pytest

from app.core import Problem, digest
from app.requirement_details import (DETAIL_SCHEMA, DetailProblem, detail_issues,
    detail_property_text, validate_detail)
from app.requirement_confirmation import confirmation_current, row_fingerprint
from app.requirements import CONTENT_FIELDS, behavior_fingerprint, item_content
from app.store import Store
from app.understanding import application_hash
from tests.test_requirement_confirmation import setup, api, preview, commit


def detail():
    return dict(schema_version=1,
        applicability={key: dict(applicable=key != 'state_transitions', reason='' if key != 'state_transitions' else '只读查询，不改变业务状态')
                       for key in ('fields', 'interactions', 'flow', 'state_transitions')},
        fields=[dict(id='FD-level', name='告警级别', meaning='设备上报的告警严重程度', data_type='enum',
            source='EMS上报', required='展示必需；来源缺失时显示未提供', default_value='无默认值',
            constraints='按来源原值展示', visibility='本电站有查看权限的用户可见', editability='用户不可修改',
            enum_values=['紧急', '一般'])],
        interactions=[dict(id='IT-view', name='查看告警', actor='值班人员', entry='电站告警列表',
            preconditions='已登录且有本站查看权限', trigger='打开列表', inputs='当前电站编号',
            validation='校验本站查看权限', pending='显示加载状态', repeat='覆盖旧查询，展示最新查询结果',
            cancel='离开页面停止等待且不更改业务数据', success='展示告警级别', failure='展示失败信息并允许重试',
            effects='不修改告警', field_refs=['FD-level'])],
        flow=dict(nodes=[dict(id='ND-start', label='打开电站', kind='start'),
            dict(id='ND-view', label='读取告警', kind='action', actor='云平台', interaction_id='IT-view'),
            dict(id='ND-end', label='展示结果', kind='end')],
            edges=[dict(id='ED-1', **{'from':'ND-start','to':'ND-view'}, condition=''),
                   dict(id='ED-2', **{'from':'ND-view','to':'ND-end'}, condition='')]),
        state_transitions=[], depends_on=[])


def item(contract=None):
    return dict(id='REQ-test', kind='requirement', detail_contract=detail() if contract is None else contract)


def test_complete_contract_and_legacy_absence():
    value = detail()
    assert validate_detail(value) == value
    assert detail_issues(item(value)) == []
    assert detail_issues({'id':'REQ-legacy'}) == []
    assert detail_issues({'id':'REQ-new','detail_required':True})[0]['code'] == 'DETAIL_REQUIRED'
    assert not detail_issues(item(value))


def test_named_pending_review_state_is_not_an_undecided_property():
    d=detail();d['applicability']['state_transitions']={'applicable':True,'reason':'交付物核对流程'}
    d['state_transitions']=[dict(id='T1',from_state='待提交',to_state='待核对',trigger='提交交付物',condition='提交物完整',prohibited_actions='核对前不得交付'),
        dict(id='T2',from_state='待核对',to_state='核对通过',trigger='完成核对',condition='全部核对项通过',prohibited_actions='不得跳过核对')]
    assert not [i for i in detail_issues(item(d)) if i['code']=='DETAIL_PROPERTY_UNKNOWN']
    d['state_transitions'][1]['evidence']={'from_state':{'status':'unknown','reason':'本例刻意未决定'}}
    assert any(i['path']=='detail_contract.state_transitions.T2.from_state' and i['code']=='DETAIL_PROPERTY_UNKNOWN' for i in detail_issues(item(d)))


def test_pending_review_word_is_not_enough_without_a_defined_transition():
    d=detail();d['applicability']['state_transitions']={'applicable':True,'reason':'尚未完整的流程'}
    d['state_transitions']=[dict(id='T1',from_state='待提交',to_state='待核对',trigger='提交交付物',condition='提交物完整',prohibited_actions='核对前不得交付')]
    assert any(i['path']=='detail_contract.state_transitions.T1.to_state' and i['code']=='DETAIL_PROPERTY_UNKNOWN' for i in detail_issues(item(d)))
@pytest.mark.parametrize('mutate,code,path', [
    (lambda d: d['fields'][0].update(enum_values=[]), 'DETAIL_ENUM_MISSING', 'detail_contract.fields.FD-level.enum_values'),
    (lambda d: d['fields'][0].update(required='待确认'), 'DETAIL_PROPERTY_UNKNOWN', 'detail_contract.fields.FD-level.required'),
    (lambda d: d['fields'][0].update(required='待确认：是否必须填写'), 'DETAIL_PROPERTY_UNKNOWN', 'detail_contract.fields.FD-level.required'),
    (lambda d: d['fields'][0].update(data_type='number'), 'DETAIL_PROPERTY_MISSING', 'detail_contract.fields.FD-level.unit'),
    (lambda d: d['fields'][0].update(data_type='datetime'), 'DETAIL_PROPERTY_MISSING', 'detail_contract.fields.FD-level.time_basis'),
    (lambda d: d['fields'][0].update(source='calculated'), 'DETAIL_PROPERTY_MISSING', 'detail_contract.fields.FD-level.formula'),
    (lambda d: d['interactions'][0].update(field_refs=['FD-missing']), 'DETAIL_FIELD_REFERENCE', 'detail_contract.interactions.IT-view.field_refs'),
    (lambda d: d['interactions'][0].update(repeat=''), 'DETAIL_PROPERTY_MISSING', 'detail_contract.interactions.IT-view.repeat'),
    (lambda d: d['fields'][0].update(evidence={'required':{'status':'unknown'}}), 'DETAIL_PROPERTY_UNKNOWN', 'detail_contract.fields.FD-level.required'),
    (lambda d: d['applicability']['state_transitions'].update(reason=''), 'DETAIL_NA_REASON', 'detail_contract.applicability.state_transitions.reason'),
    (lambda d: d['applicability']['fields'].update(applicable=False,reason='不展示数据'), 'DETAIL_APPLICABILITY_CONFLICT', 'detail_contract.fields'),
    (lambda d: d['flow']['edges'][0].update(to='ND-missing'), 'DETAIL_FLOW_REFERENCE', 'detail_contract.flow.edges.ED-1'),
    (lambda d: d['flow']['nodes'][1].update(kind='decision'), 'DETAIL_FLOW_BRANCH', 'detail_contract.flow.nodes.ND-view'),
])
def test_locatable_incomplete_business_definitions(mutate, code, path):
    value = detail(); mutate(value)
    assert any(row['code'] == code and row['path'] == path and row['item_id'] == 'REQ-test'
               for row in detail_issues(item(value)))


def test_duplicate_ids_and_disconnected_cycles_are_blocked():
    value = detail()
    value['flow']['nodes'] += [dict(id='ND-lost', label='孤立', kind='action', actor='平台')]
    value['flow']['edges'] += [dict(id='ED-lost', **{'from':'ND-lost','to':'ND-lost'})]
    value['interactions'][0]['id'] = 'FD-level'
    assert {'DETAIL_DUPLICATE_ID','DETAIL_FLOW_UNREACHABLE'} <= {row['code'] for row in detail_issues(item(value))}


def test_branch_conditions_and_wait_continuation_are_explicit():
    value = detail()
    value['flow']['nodes'][1]['kind'] = 'decision'
    value['flow']['edges'] += [dict(id='ED-3', **{'from':'ND-view','to':'ND-end'}, condition='')]
    assert 'DETAIL_FLOW_CONDITION' in {row['code'] for row in detail_issues(item(value))}
    value['flow']['nodes'][1]['kind'] = 'wait'
    assert 'DETAIL_FLOW_WAIT' in {row['code'] for row in detail_issues(item(value))}


def test_evidence_source_refs_validate_active_excerpts(tmp_path):
    store = Store(tmp_path); p = setup(store)
    value = detail()
    value['fields'][0]['evidence'] = {'name': {'status':'source','source_refs':[{'source_id':'missing','excerpt_id':'E-missing'}]}}
    assert 'DETAIL_SOURCE_INVALID' in {row['code'] for row in detail_issues(item(value), p)}
    ref = p['items'][0]['source_refs'][0]
    value['fields'][0]['evidence']['name']['source_refs'] = [ref]
    assert detail_issues(item(value), p) == []


def test_legacy_hashes_exactly_preserved_and_details_versioned(tmp_path):
    store = Store(tmp_path); p = setup(store); req = p['items'][0]
    assert item_content(req) == {key:copy.deepcopy(req.get(key)) for key in CONTENT_FIELDS}
    assert application_hash(req) == digest({key:req.get(key) for key in CONTENT_FIELDS})
    assert behavior_fingerprint(req) == digest({key:req.get(key) for key in ('kind','statement','change_type','behavior','source_refs')})
    old = application_hash(req)
    with store.edit(p['id'],p['revision'],'明确启用详细需求') as (working,_):
        working['items'][0]['detail_contract'] = detail()
    current = store.get(p['id'])['items'][0]
    assert current['content_version'] == req['content_version'] + 1
    assert application_hash(current) != old


def test_single_confirmation_checks_structure_and_keeps_unknown_unconfirmed(tmp_path):
    store = Store(tmp_path); p = setup(store)
    with api(store) as client:
        invalid = detail(); invalid['fields'][0]['enum_values'] = []
        from tests.test_requirement_confirmation import payload
        response = client.post(f'/api/projects/{p["id"]}/requirement-confirmation/preview', json=payload(p,detail_contract=invalid))
        assert response.status_code == 409 and response.json()['code'] == 'DETAIL_INCOMPLETE'
        assert store.get(p['id']) == p
        view = preview(client,p,detail_contract=detail())
        response = commit(client,p,view)
        assert response.status_code == 200, response.text
        saved = response.json()['project']; req=saved['items'][0]
        assert req['detail_required'] and confirmation_current(saved,req)
        assert detail_issues(req,saved,require_confirmed=True) == []
        changed=copy.deepcopy(saved)
        changed['items'][0]['detail_contract']['fields'][0]['enum_values'].append('重要')
        assert not confirmation_current(changed,changed['items'][0])


def test_revision_candidate_cannot_drop_required_or_unseen_structured_definition(tmp_path):
    from app.requirement_confirmation import build_preview
    from tests.test_requirement_confirmation import payload
    store=Store(tmp_path); p=setup(store); before=p['items'][0]
    candidate=copy.deepcopy(before)
    candidate.update(id='REQ-revision',target_item_id=before['id'],action='revise',detail_required=True)
    p['items'].append(candidate)
    with pytest.raises(Problem) as error:
        build_preview(p,payload(p,candidate_id=candidate['id']))
    assert error.value.code=='DETAIL_INCOMPLETE'
    candidate['detail_contract']=detail()
    with pytest.raises(Problem) as error:
        build_preview(p,payload(p,candidate_id=candidate['id']))
    assert error.value.code=='DETAIL_REVIEW_REQUIRED'
    shown=build_preview(p,payload(p,candidate_id=candidate['id'],detail_contract=detail()))
    assert shown['after']['detail_required']


def test_dependency_changes_invalidate_only_related_confirmations(tmp_path):
    store = Store(tmp_path); p = setup(store)
    original=p['items'][0]; original['selection_status']='selected'; original['detail_contract']=detail()
    dependent=copy.deepcopy(original); dependent.update(id='REQ-dep',title='依赖需求')
    independent=copy.deepcopy(original); independent.update(id='REQ-free',title='无关需求')
    dependent['detail_contract']['depends_on']=[original['id']]
    p['items'] += [dependent,independent]
    p['product_context']['scope_ids'] += ['REQ-dep','REQ-free']
    for req in (original,dependent,independent):
        req['requirement_confirmation']=dict(schema='requirement-confirmation-1',actor='authenticated_local_user',content_hash=row_fingerprint(p,req))
    assert all(confirmation_current(p,req) for req in (original,dependent,independent))
    original['detail_contract']['fields'][0]['name']='告警等级'
    assert not confirmation_current(p,original)
    assert not confirmation_current(p,dependent)
    assert confirmation_current(p,independent)
    original['detail_contract']['depends_on']=['REQ-dep']
    assert 'DETAIL_DEPENDENCY_CYCLE' in {row['code'] for row in detail_issues(original,p)}
    # Cycles are bounded even when only inspecting a confirmation hash.
    assert isinstance(row_fingerprint(p,original),str)


def test_dependency_acceptance_and_source_change_invalidate_dependent_review(tmp_path):
    store=Store(tmp_path); p=setup(store); original=p['items'][0]
    original.update(selection_status='selected',detail_contract=detail())
    p['items'][2]['selection_status']='selected'
    dependent=copy.deepcopy(original)
    dependent.update(id='REQ-dependent',related_refs=[])
    dependent['detail_contract']['depends_on']=[original['id']]
    p['items'].append(dependent)
    p['product_context']['scope_ids'].append(dependent['id'])
    original_hash=row_fingerprint(p,dependent)
    p['items'][2]['statement']+=' 仅展示本站记录。'
    assert row_fingerprint(p,dependent)!=original_hash


def test_stable_property_witness_survives_reordering_but_not_value_change(tmp_path):
    from app.answer_implementation import field_text, derive_changed_witnesses, validate_implementations
    store = Store(tmp_path); p = setup(store); req=p['items'][0]
    req['detail_contract']=detail()
    old=copy.deepcopy(req)
    req['detail_contract']['fields'][0]['enum_values']=['紧急','重要','一般']
    path='detail_contract.fields.FD-level.enum_values'
    assert field_text(req,path)=='紧急、重要、一般'
    witnesses=derive_changed_witnesses(old,req)
    assert dict(item_id=req['id'],field=path,quote='紧急、重要、一般') in witnesses
    q=p['questions'][0]
    assert validate_implementations(p,q,witnesses,targets=[req])
    extra=copy.deepcopy(req['detail_contract']['fields'][0]); extra['id']='FD-other'
    req['detail_contract']['fields'].insert(0,extra)
    assert field_text(req,path)=='紧急、重要、一般'
    req['detail_contract']['fields'][1]['enum_values']=['一般']
    with pytest.raises(Problem) as error:
        validate_implementations(p,q,witnesses,targets=[req])
    assert error.value.code=='ANSWER_IMPLEMENTATION_STALE'


def test_refinement_confirmation_reports_property_locations_and_rolls_back(tmp_path):
    from tests.test_refinement_confirmation import setup as table_setup, payload, api as table_api, post
    store=Store(tmp_path); p=table_setup(store); body=payload(p)
    body['rows'][0]['detail_contract']=detail()
    body['rows'][0]['detail_contract']['fields'][0]['enum_values']=[]
    with table_api(store) as client:
        result=post(client,p,body).json()
    assert not result['confirmed']
    assert any(row['path']=='detail_contract.fields.FD-level.enum_values' and row['kind']=='requirement' for row in result['issues'])
    assert store.get(p['id'])==p


def test_batch_cross_row_dependencies_bind_final_confirmed_contents(tmp_path):
    from tests.test_refinement_confirmation import setup as table_setup, payload, api as table_api, post
    store=Store(tmp_path); p=table_setup(store); body=payload(p)
    for row in body['rows']:
        row['detail_contract']=detail()
    body['rows'][0]['detail_contract']['depends_on']=[body['rows'][1]['requirement_id']]
    with table_api(store) as client:
        result=post(client,p,body).json()
    assert result['confirmed'], result
    saved=store.get(p['id'])
    for iid in saved['product_context']['scope_ids']:
        req=next(row for row in saved['items'] if row['id']==iid)
        assert confirmation_current(saved,req)
