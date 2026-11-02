import copy
import jsonschema
import pytest
from app import detail_semantics as review, detail_review_repair as repair
from tests.test_detail_review_repair import sample,broken


def test_pool_keys_are_kind_specific_and_patch_inherits(tmp_path):
    plan,good=sample(tmp_path);before=copy.deepcopy(plan)
    schema=review.review_schema(plan)
    jsonschema.validate(good,schema)
    fields=schema['properties']['rows']['items']['properties']
    for group in ('checks','source_checks','state_checks'):
        for field,kind in (('source_quote_keys','source'),('human_quote_keys','human')):
            if field not in fields[group]['items']['properties']:continue
            rule=fields[group]['items']['properties'][field]
            jsonschema.validate([],rule)
            for value in ['0012','not-a-key']+[q['key'] for q in plan['quote_pool'] if q['kind']!=kind]:
                with pytest.raises(jsonschema.ValidationError):jsonschema.validate([value],rule)
            for q in plan['quote_pool']:
                if q['kind']==kind:jsonschema.validate([q['key']],rule)
    packet=repair.prepare(plan,broken(good));encoded=str(repair.schema(plan,packet))
    assert "'enum'" in encoded and plan==before


def test_legacy_quote_key_schema_is_unchanged(tmp_path):
    plan,_=sample(tmp_path)
    for version in ('detail-source-review-1',review.VERSION):
        plan['version']=version
        schema=review.review_schema(plan)
        for group in ('checks','source_checks','state_checks'):
            rule=schema['properties']['rows']['items']['properties'][group]['items']['properties']['source_quote_keys']
            jsonschema.validate(['0012'],rule)


def test_empty_material_kind_only_accepts_empty_list(tmp_path):
    plan,_=sample(tmp_path)
    plan['quote_pool']=[q for q in plan['quote_pool'] if q['kind']=='source']
    schema=review.review_schema(plan)
    for group in ('checks','state_checks'):
        rule=schema['properties']['rows']['items']['properties'][group]['items']['properties']['human_quote_keys']
        jsonschema.validate([],rule)
        with pytest.raises(jsonschema.ValidationError):jsonschema.validate(['H0001'],rule)
