"""Request identity exclusions mirror the existing per-row merge guard."""
import asyncio
import copy
import json
import jsonschema
import pytest
from app import detail_correction as correction
from app.detail_draft import model_schema
from app.detail_semantics import correction_messages
from app.core import Problem
from tests.test_detail_correction_graph_context import correction_context


def addition(raw, identity='N999'):
    node=copy.deepcopy(raw['requirements'][0]['detail_contract']['flow']['nodes'][0])
    node['id']=identity
    return dict(section='flow.nodes',value=node)


def patch(*objects,row_key='R1'):
    return dict(rows=[dict(row_key=row_key,updates=[],new_objects=list(objects))])


@pytest.mark.parametrize('identity',['N1','E1','F1','I1','N999'])
def test_request_rejects_existing_same_and_cross_kind_ids_only(tmp_path,identity):
    raw,_,outcomes,_=correction_context(tmp_path)
    schema,scopes=correction.contract(raw,outcomes)
    errors=list(jsonschema.Draft202012Validator(schema).iter_errors(patch(addition(raw,identity))))
    assert bool(errors)==(identity!='N999')
    assert {'N1','E1','F1','I1'}<=set(scopes['R1']['existing_object_ids'])


def test_contract_is_read_only_and_retains_original_id_shape(tmp_path):
    raw,plan,outcomes,excerpts=correction_context(tmp_path)
    before=copy.deepcopy((raw,outcomes));shape=correction.shapes();sparse=model_schema(generation=False)
    schema,messages=correction_messages(raw,dict(plan,correction_protocol=2),outcomes,excerpts)
    variants=schema['properties']['rows']['items']['properties']['new_objects']['items']['oneOf']
    for variant in variants:
        section=variant['properties']['section']['const']
        actual=copy.deepcopy(variant['properties']['value']);exclusion=actual['properties']['id'].pop('not')
        assert actual==shape[section] and exclusion['enum']
    assert (raw,outcomes)==before and correction.shapes()==shape and model_schema(generation=False)==sparse
    assert '既有对象只能通过获准updates修改' in messages[0]['content']
    legacy,legacy_messages=correction_messages(raw,dict(plan,correction_protocol=1),outcomes,excerpts)
    assert '既有对象只能通过获准updates修改' not in legacy_messages[0]['content']
    assert '返回每个失败row的完整detail_contract' in legacy_messages[0]['content']
    assert 'detail_contract' in legacy['properties']['rows']['items']['properties']


def test_cross_row_reuse_remains_legal_and_batch_duplicate_still_fails_merge(tmp_path):
    raw,_,outcomes,_=correction_context(tmp_path)
    second=copy.deepcopy(raw['requirements'][0]);second['row_key']='R2';raw['requirements'].append(second)
    outcomes['R2']=copy.deepcopy(outcomes['R1'])
    response=patch(addition(raw));response['rows'].append(dict(row_key='R2',updates=[],new_objects=[addition(raw)]))
    schema,_=correction.contract(raw,outcomes);jsonschema.validate(response,schema)
    assert len(correction.merge(raw,response,outcomes)['rows'])==2
    response['rows'][0]['new_objects'].append(addition(raw))
    jsonschema.validate(response,schema)
    with pytest.raises(Problem) as caught:correction.merge(raw,response,outcomes)
    assert caught.value.code=='DETAIL_CORRECTION_SCOPE' and 'ID冲突' in str(caught.value)


def test_actual_provider_serializes_identity_exclusion_to_synthetic_http(tmp_path,monkeypatch):
    import httpx
    from app.provider import Provider,DEFAULT,origin
    from tests.provider_transport import reply
    raw,plan,outcomes,excerpts=correction_context(tmp_path)
    schema,messages=correction_messages(raw,dict(plan,correction_protocol=2),outcomes,excerpts)
    result=patch(addition(raw));bodies=[];real_client=httpx.AsyncClient
    async def respond(request):
        body=json.loads(request.content);bodies.append(body)
        # Correction currently uses Chat Completions with schema in trusted
        # task text, not server-enforced Responses structured output.
        assert request.url.path.endswith('/chat/completions')
        header=json.loads(body['messages'][0]['content'].split('可信任务头：',1)[1])
        assert header['schema']==schema
        return reply(request,json.dumps(result))
    monkeypatch.setattr('app.provider.httpx.AsyncClient',lambda **kw:real_client(**kw,transport=httpx.MockTransport(respond)))
    provider=Provider(env_path=tmp_path/'absent.env');provider.keys[origin(DEFAULT)]='synthetic-key'
    received,_=asyncio.run(provider.request(DEFAULT,messages))
    assert received==result and len(bodies)==1
