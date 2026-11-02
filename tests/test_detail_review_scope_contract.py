"""Exact review identity on the wire, without coercing model output."""
import copy
import json
import jsonschema
import pytest
from app import detail_semantics as review
from app.core import Problem
from tests.test_detail_semantics import fixture,model_response


@pytest.mark.parametrize('version',[2,3])
def test_exact_scopes_are_accepted_and_descriptive_scopes_rejected_by_sent_schema(tmp_path,version):
    p,_,excerpts,raw,canonical,report,_=fixture(tmp_path)
    plan=review.prepare(raw,canonical,report,excerpts,protocol_version=version)
    value=model_response(plan);before=copy.deepcopy(value)
    messages=review.messages(plan);schema=json.loads(messages[0]['content'].split('可信任务头：',1)[1])['schema']
    jsonschema.validate(value,schema)
    expected=list(dict.fromkeys(c['scope'] for c in plan['rows'][0]['required_checks']))
    assert schema['properties']['rows']['items']['properties']['checks']['items']['properties']['scope']=={'enum':expected}
    for suffix in (' 中文操作说明',' ','-描述'):
        bad=copy.deepcopy(value);bad['rows'][0]['checks'][0]['scope']+=suffix
        errors=list(jsonschema.Draft202012Validator(schema).iter_errors(bad))
        assert any(list(e.path)[-1]=='scope' for e in errors)
        normalized,_=review.normalize_review(bad)
        assert normalized['rows'][0]['checks'][0]['scope']==bad['rows'][0]['checks'][0]['scope']
        with pytest.raises(Problem):review.validate(plan,bad)
    assert value==before and '中文解释写入已有explanation字段' in messages[0]['content']


def test_scope_enum_is_per_row_and_does_not_mutate_shared_schema(tmp_path):
    *_,plan=fixture(tmp_path);before=copy.deepcopy(review.SCHEMA)
    other=copy.deepcopy(plan['rows'][0]);other['row_key']='R-other'
    other['required_checks']=[dict(scope='I-other',dimension='operation_results')]
    plan['rows'].append(other)
    variants=review.review_schema(plan)['properties']['rows']['items']['oneOf']
    first=variants[0]['properties']['checks']['items']['properties']['scope']['enum']
    second=variants[1]['properties']['checks']['items']['properties']['scope']['enum']
    assert 'I-other' not in first and second==['I-other'] and review.SCHEMA==before
