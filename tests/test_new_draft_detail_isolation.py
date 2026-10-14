"""Malformed new detail cannot erase valid clauses or authorize delivery."""
import copy

import pytest

from app.business_draft import adapt
from app.requirement_details import detail_issues
from app.workflow import apply_response
from tests.test_business_draft import draft_row, packet, setup
from tests.test_business_draft_partial_questions import question
from tests.test_detail_business_draft import sample_detail


@pytest.mark.parametrize('has_existing', [False, True])
def test_wire_row_keys_match_existing_aliases_without_inventing_identity(tmp_path, has_existing):
    import json,jsonschema
    from app.business_draft import assemble
    project,_,config,_,_,_=setup(tmp_path)
    if not has_existing:project['items']=[];project['questions']=[]
    before=copy.deepcopy(project)
    messages,_,_=assemble(project,'ingest','',config,tmp_path)
    header=json.loads(messages[0]['content'].split('可信任务头：',1)[1])
    key_schema=header['schema']['properties']['requirements']['items']['properties']['row_key']
    validator=jsonschema.Draft202012Validator(key_schema)
    validator.validate('new-batch-dispatch')
    if has_existing:validator.validate('R1')
    else:
        assert header['allowed_existing_row_keys']==[]
        with pytest.raises(jsonschema.ValidationError):validator.validate('R1')
    with pytest.raises(jsonschema.ValidationError):validator.validate('R404')
    assert project==before


@pytest.mark.parametrize('defect', ['flow_metadata', 'bad_status', 'non_object'])
def test_invalid_new_detail_retains_independent_body_acceptance_and_question(tmp_path, defect):
    project, _, _, _, excerpts, _ = setup(tmp_path)
    row=draft_row('new-cloud-function'); row['questions']=[question()]
    detail=sample_detail()
    if defect=='flow_metadata':detail['flow']['evidence']={'nodes':{'status':'source','sources':[{'source':'S1','locator':'L1'}]}}
    elif defect=='bad_status':detail['fields'][0]['evidence']={'constraints':{'status':'recommendation'}}
    else:detail='invalid detailed definition'
    row['detail_contract']=detail
    before=copy.deepcopy((project,row))
    canonical,report=adapt(packet(row),project,excerpts,'ingest')
    assert report['status']=='partial' and not report['complete'] and not report['rejected']
    assert len(report['accepted'])==1 and report['accepted'][0]['raw']==row
    assert report['field_rejected'][0]['path']==['requirements',0,'detail_contract']
    assert report['field_rejected'][0]['raw']==detail
    assert [i['kind'] for i in canonical['proposals']]==['requirement','acceptance']
    req=canonical['proposals'][0]
    assert req['detail_required'] is True and 'detail_contract' not in req
    assert any(i['code']=='DETAIL_REQUIRED' for i in detail_issues(req))
    assert canonical['questions'][0]['question']==row['questions'][0]['text']
    assert (project,row)==before
    saved=copy.deepcopy(project);apply_response(saved,canonical,identity_only=True)
    assert saved['items'][:len(project['items'])]==project['items']
    assert all(i['selection_status']=='candidate' for i in saved['items'][len(project['items']):])


@pytest.mark.parametrize('invalid_shape', [False, True])
def test_invalid_detail_source_still_rejects_entire_row(tmp_path, invalid_shape):
    project, _, _, _, excerpts, _ = setup(tmp_path)
    row=draft_row('new-cloud-function');detail=sample_detail()
    detail['fields'][0]['evidence']={'constraints':{'status':'source','sources':[{'source':'S404','locator':'L1'}]}}
    if invalid_shape:detail['flow']['evidence']={}
    row['detail_contract']=detail
    before=copy.deepcopy((project,row))
    canonical,report=adapt(packet(row),project,excerpts,'ingest')
    assert not canonical['proposals'] and not canonical['questions']
    assert report['rejected'][0]['code']=='BUSINESS_DRAFT_SOURCE'
    assert (project,row)==before


def test_existing_detail_is_not_silently_replaced_when_new_output_is_invalid(tmp_path):
    project,local,_,_,excerpts,_=setup(tmp_path)
    original=sample_detail();project['items'][0]['detail_contract']=copy.deepcopy(original)
    row=draft_row();row['detail_contract']=copy.deepcopy(original)
    row['detail_contract']['flow']['evidence']={}
    before=copy.deepcopy(project)
    canonical,report=adapt(packet(row),project,excerpts,'clarify',local)
    assert not canonical['proposals'] and report['rejected']
    assert project==before and project['items'][0]['detail_contract']==original
