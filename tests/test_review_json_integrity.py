"""Offline transport fixtures; never count them as real model generation."""
import asyncio,copy,json

import httpx
import pytest

from app.contracts import validate_response
from app.core import Problem
from app.ingest import complete_object,diagnostic_object
from app.provider import DEFAULT,Provider,normalize_review_result_tail,review_structure_example
from tests.test_current_workflow_evidence import case,CURRENT,NEW
from tests.test_semantic_quality_boundaries import compile_explanation
from app.workflow import apply_response

def request(monkeypatch,tmp_path,raw):
    provider=Provider(env_path=tmp_path/'absent.env');provider.key=lambda c:'offline-synthetic-key'
    class Proof:
        value=dict(stage='review')
        def response(self,content=None,**meta):self.value.update(final_output=content,**meta)
        def save(self):pass
    proof=Proof();client=httpx.AsyncClient
    transport=httpx.MockTransport(lambda req:httpx.Response(200,json=dict(model='synthetic',usage={},choices=[dict(finish_reason='stop',message=dict(content=raw))])))
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:client(transport=transport,**kw))
    return asyncio.run(provider.request(dict(DEFAULT,base_url='http://synthetic.invalid',local_allowed=True),[],evidence=proof))

@pytest.mark.parametrize('duplicate',['summary','assessment','role'])
def test_duplicate_response_members_cannot_be_silently_collapsed(monkeypatch,tmp_path,duplicate):
    raw=json.dumps(review_structure_example(),ensure_ascii=False)
    raw=raw.replace('"'+duplicate+'":','"'+duplicate+'":"duplicate", "'+duplicate+'":',1)
    assert complete_object(raw) is None
    assert diagnostic_object(raw) is None
    with pytest.raises(Problem) as e:request(monkeypatch,tmp_path,raw)
    assert e.value.code=='SCHEMA_INVALID'

@pytest.mark.parametrize('bad',['stray_quote','truncated','missing_field','semantic_change'])
def test_invalid_or_changed_reviews_do_not_pass_format_repair(monkeypatch,tmp_path,bad):
    value=review_structure_example();value['result']['perspectives']=[dict(role='测试',considerations=['原义保留'])]
    raw=json.dumps(value,ensure_ascii=False)
    p=case(tmp_path)
    if bad=='stray_quote':raw=raw.replace('["原义保留"]}', '["原义保留"],"}')
    elif bad=='truncated':raw=raw[:-1]
    elif bad=='missing_field':value.pop('limitations');raw=json.dumps(value,ensure_ascii=False)
    else:
        apply_response(p,compile_explanation(p,NEW,'prd',['current_state']))
        b=next(b for s in p['documents']['prd']['content']['sections'] for b in s['blocks'] if b['kind']=='explanation');b['text']=CURRENT
        value['result'].update(assessment='ready_for_human_review',reviewed_refs=['REQ-0001'],required_decisions=[])
        raw=json.dumps(value,ensure_ascii=False)
    assert normalize_review_result_tail(raw) is None
    with pytest.raises(Problem):
        parsed,_=request(monkeypatch,tmp_path,raw)
        validate_response(parsed,'review',p,p['sources'][0]['excerpts'])

def test_valid_json_remains_valid_and_example_covers_object_separator(monkeypatch,tmp_path):
    value=review_structure_example()
    assert len(value['result']['perspectives'])>=2
    parsed,_=request(monkeypatch,tmp_path,json.dumps(value,ensure_ascii=False))
    assert parsed==value and complete_object(json.dumps(value))==value
