"""Exact source/excerpt quote recovery without changing review decisions."""
import copy
import pytest
from app.core import Problem
from app.prewrite import validate_result
from tests.test_prewrite_consistency import isolated_store,review


def test_exact_source_quote_uses_the_existing_bound_excerpt(tmp_path):
    _,p=isolated_store(tmp_path);response=review(p,blocker=True)
    source=p['sources'][0];excerpt=source['excerpts'][0]
    finding=response['findings'][0]
    ref=dict(source_id=source['id'],excerpt_id=excerpt['id'])
    finding['source_refs']=[ref]
    finding['evidence_quotes']=[dict(object_id=source['id'],path=excerpt['id'],quote=excerpt['text'])]
    original=copy.deepcopy(response)
    validate_result(p,response)
    assert response==original
    for fault in ('text','missing_source_ref','excluded','wrong_excerpt'):
        project=copy.deepcopy(p);value=copy.deepcopy(response);f=value['findings'][0]
        if fault=='text':f['evidence_quotes'][0]['quote']='不存在于资料的业务决定'
        if fault=='missing_source_ref':f['source_refs']=[]
        if fault=='excluded':project['sources'][0]['excluded']=True
        if fault=='wrong_excerpt':f['evidence_quotes'][0]['path']='EX-NOT-SENT'
        with pytest.raises(Problem) as error:validate_result(project,value)
        assert error.value.code=='REFERENCE_INVALID'
