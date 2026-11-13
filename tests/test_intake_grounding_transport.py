import asyncio,json,copy,hashlib
import httpx,pytest
from app.provider import Provider,DEFAULT,origin,intake_grounding_fence
from app.model_evidence import ModelCallEvidence
from app.core import Problem
from tests.provider_transport import reply

@pytest.mark.parametrize('protocol',['intake-grounding-1','intake-grounding-patch-1','intake-grounding-patch-2','intake-grounding-challenge-1','intake-grounding-challenge-2','intake-grounding-coverage-1'])
@pytest.mark.parametrize('transport',['responses','chat'])
@pytest.mark.parametrize('case',['valid','duplicate','nan','tail','multiple','truncated','not_grounding','limited','raw_mismatch'])
def test_actual_provider_fence_is_bounded(tmp_path,monkeypatch,protocol,transport,case):
    inner='{"candidate_hash":"abc","items":[]}'
    content=' \n```json\n'+inner+'\n```\n'
    if case=='duplicate':content='```json\n{"x":1,"x":1}\n```'
    if case=='nan':content='```json\n{"x":NaN}\n```'
    if case=='tail':content+='tail'
    if case=='multiple':content+='```json\n{}\n```'
    schema={'type':'object'}
    header=dict(response_protocol='business-draft-v1',grounding_protocol=protocol,schema=schema)
    if case=='not_grounding':header.pop('grounding_protocol')
    messages=[dict(role='system',content='可信任务头：'+json.dumps(header)),dict(role='user',content='synthetic')]
    config=dict(DEFAULT,base_url='https://api.deepseek.com' if transport=='responses' else 'https://synthetic.invalid',model='deepseek-v4-pro')
    provider=Provider(env_path=tmp_path/'absent.env');provider.keys[origin(config)]='synthetic-key'
    evidence=ModelCallEvidence(tmp_path,'CALLsynthetic','RUNsynthetic','ingest',config['model'],origin(config),None,'synthetic-key')
    real_response=evidence.response
    def record(*args,**kwargs):
        real_response(*args,**kwargs)
        if case=='limited':evidence.limitations.append('final_output_truncated')
        if case=='raw_mismatch' and args:evidence.value['final_output']='different'
    monkeypatch.setattr(evidence,'response',record)
    seen=[];client=httpx.AsyncClient
    def respond(request):
        seen.append(request)
        return reply(request,content,finish='length' if case=='truncated' else 'stop')
    monkeypatch.setattr('app.provider.httpx.AsyncClient',lambda **kw:client(**kw,transport=httpx.MockTransport(respond)))
    if case=='valid':
        value,meta=asyncio.run(provider.request(config,messages,evidence))
        assert value==json.loads(inner)
        audit=evidence.value['transport_normalization']
        assert audit['removed_prefix']+audit['derived_content']+audit['removed_suffix']==content
        assert audit['raw_sha256']==hashlib.sha256(content.encode()).hexdigest()
        assert evidence.value['final_output']==content and meta['transport_normalization']==audit
    else:
        with pytest.raises(Problem):asyncio.run(provider.request(config,messages,evidence))
        assert 'transport_normalization' not in evidence.value
    assert len(seen)==1
