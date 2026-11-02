import copy
import pytest
from app import detail_semantics as review
from app.core import Problem,digest
from tests.test_detail_semantics import fixture


def candidate(tmp_path):
    *_,raw,_,_,plan=fixture(tmp_path)
    detail=raw['requirements'][0]['detail_contract']
    detail['fields'][0]['evidence']={}
    detail['flow']['edges'][0]['evidence']={'condition':{'status':'recommendation','reason':'原有未确认建议'}}
    outcomes={'R1':dict(status='failed',findings=[dict(scope='detail',dimension='field_conditions',verdict='unclear',
        affected_paths=['detail_contract.fields.F1.required'],traces=[])])}
    schema,_=review.correction_messages(raw,plan,outcomes,[])
    fixed=copy.deepcopy(detail);fixed['fields'][0]['required']='填写时必填'
    return raw,dict(rows=[dict(row_key='R1',detail_contract=fixed)]),schema,outcomes


def test_existing_alias_and_missing_unaffected_proof_are_not_new_decisions(tmp_path):
    raw,patch,schema,outcomes=candidate(tmp_path);before=copy.deepcopy((raw,patch));audit=[]
    result=review.apply_correction(raw,patch,schema,outcomes,normalization_audit=audit)
    detail=result['requirements'][0]['detail_contract']
    assert detail['fields'][0]['evidence']=={}
    assert detail['flow']['edges'][0]['evidence']['condition']['status']=='proposal'
    assert detail['fields'][0]['required']=='填写时必填'
    assert (raw,patch)==before
    assert {a['phase'] for a in audit}=={'original_candidate','correction_output'}
    assert all(a['request_schema_hash']==digest(schema) and a['input_hash']!=a['output_hash'] for a in audit)


@pytest.mark.parametrize('fault',['unknown_status','wrong_wrapper','unrelated_property','invent_unrelated_evidence'])
def test_normalization_does_not_expand_acceptance_or_correction_authority(tmp_path,fault):
    raw,patch,schema,outcomes=candidate(tmp_path)
    detail=patch['rows'][0]['detail_contract']
    if fault=='unknown_status':detail['flow']['edges'][0]['evidence']['condition']['status']='approved'
    elif fault=='wrong_wrapper':patch={'requirements':patch['rows']}
    elif fault=='unrelated_property':detail['fields'][0]['name']='无授权的新字段名称'
    else:detail['fields'][0]['evidence']['name']={'status':'source','sources':[{'source':'S1','locator':'L1'}]}
    before=copy.deepcopy((raw,patch))
    with pytest.raises(Problem) as caught:review.apply_correction(raw,patch,schema,outcomes)
    assert caught.value.code==('DETAIL_CORRECTION_INVALID' if fault in ('unknown_status','wrong_wrapper') else 'DETAIL_CORRECTION_SCOPE')
    assert (raw,patch)==before
