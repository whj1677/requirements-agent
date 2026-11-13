"""Request schema matches frozen property requirements; NA remains source judged."""
import copy
import jsonschema
import pytest
from app import detail_semantics as review
from app.core import Problem,digest
from tests.test_detail_quote_pool import prepared
from tests.test_detail_semantics import model_response


@pytest.mark.parametrize('version',[2,3])
@pytest.mark.parametrize('keys',[[],['P999999'],['P001-P003']])
def test_property_diagnostic_identifies_row_dimension_and_empty_or_unknown(tmp_path,version,keys):
    plan=prepared(tmp_path)
    if version==2:plan['version']=review.VERSION
    value=model_response(plan);check=value['rows'][0]['checks'][0];check['candidate_keys']=keys
    with pytest.raises(Problem) as failure:review.validate(plan,value)
    message=failure.value.message
    assert 'row=R1' in message and 'scope='+check['scope'] in message
    assert 'dimension='+check['dimension'] in message and 'rows[0].checks[0].candidate_keys' in message
    assert ('empty=true' if not keys else 'unknown_keys') in message


def test_per_row_schema_arrays_are_independent_and_global_unchanged(tmp_path):
    plan=prepared(tmp_path);before=digest(review.SCHEMA);row=copy.deepcopy(plan['rows'][0]);row['row_key']='R2';row['property_refs']={'P999':{'path':'isolated','value':'x'}};plan['rows'].append(row)
    schema=review.review_schema(plan);variants=schema['properties']['rows']['items']['oneOf']
    for variant,origin in zip(variants,plan['rows']):
        fields=variant['properties']['checks']['items']['properties']
        assert fields['candidate_keys']['minItems']==1
        assert fields['candidate_keys']['items']['enum']==list(origin['property_refs'])
        jsonschema.validate([],fields['affected_keys'])
        jsonschema.validate(['E1'],fields['traces']['items']['properties']['edge_ids'])
        jsonschema.validate(['S0001'],fields['source_quote_keys'])
    assert digest(review.SCHEMA)==before
    next_schema=review.review_schema(prepared(tmp_path/'next'))
    assert 'P999' not in next_schema['properties']['rows']['items']['properties']['checks']['items']['properties']['candidate_keys']['items']['enum']


@pytest.mark.parametrize('secret',['SECRET SOURCE PROSE','PRIVATE_ASCII_TEXT'])
def test_unknown_affected_is_rejected_without_echoing_arbitrary_text(tmp_path,secret):
    plan=prepared(tmp_path);value=model_response(plan)
    value['rows'][0]['checks'][0]['affected_keys']=[secret]
    with pytest.raises(Problem) as failure:review.validate(plan,value)
    assert 'affected_keys' in failure.value.message and '<invalid-property-key>' in failure.value.message
    assert secret not in failure.value.message


def test_no_property_plan_has_no_invented_key_and_cannot_pass(tmp_path):
    plan=prepared(tmp_path);plan['rows'][0]['property_refs']={}
    schema=review.review_schema(plan)['properties']['rows']['items']['properties']['checks']['items']['properties']['candidate_keys']
    assert schema['items']=={'not':{}} and schema['minItems']==1
    for value in ([],['P001']):
        with pytest.raises(jsonschema.ValidationError):jsonschema.validate(value,schema)
