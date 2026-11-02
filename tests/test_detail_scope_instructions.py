"""Scope contract routing and unchanged technical gates, not a model oracle."""
import copy
import pytest
from app import business_draft as b,detail_semantics as s
from app.core import Problem
from app.requirement_details import require_complete
from tests.test_business_draft import setup
from tests.test_detail_unknown_scope import sample


def test_generation_legacy_exact_and_new_marker_preserves_input_schema(tmp_path):
    p,local,cfg,_,_,_=setup(tmp_path)
    local=dict(local,detail_required=True)
    args=(p,'clarify','',cfg,tmp_path,local)
    legacy=b.assemble(*args)
    modern=b.assemble(p,'clarify','',dict(cfg,detail_unknown_scope_protocol=s.UNKNOWN_SCOPE),tmp_path,local)
    assert modern[1:]==legacy[1:] and modern[0][1:]==legacy[0][1:]
    assert modern[0][0]['content'].split('可信任务头：',1)[1]==legacy[0][0]['content'].split('可信任务头：',1)[1]
    conflicting='unknown 只在资料或用户原话明确该属性时补齐，否则沿用对应待决定问题。'
    assert conflicting in legacy[0][0]['content'] and conflicting not in modern[0][0]['content']


def test_review_scope_does_not_change_legacy_schema_or_confirmation_gate(tmp_path):
    plan,good,_=sample(tmp_path)
    legacy=copy.deepcopy(plan);legacy.pop('unknown_scope');legacy.pop('unknown_scope_hash')
    before=copy.deepcopy(plan);canonical_schema=copy.deepcopy(s.review_schema(plan))
    modern=s.messages(plan);prior=s.messages(legacy)
    assert plan==before and s.review_schema(plan)==canonical_schema
    conflicting='proposal/unknown只核对是否如实标注，不将合理建议强制升级事实或要求PM决定。'
    assert conflicting in prior[0]['content'] and conflicting not in modern[0]['content']
    detail=copy.deepcopy(plan['rows'][0]['detail_contract'])
    assert s.validate(plan,good)['R1']['status']=='passed'
    with pytest.raises(Problem) as caught:require_complete(dict(detail_contract=detail))
    assert any(i['code']=='DETAIL_PROPERTY_UNKNOWN' for i in caught.value.issues)
    assert detail==plan['rows'][0]['detail_contract']
