"""Offline finite property encoding; never repairs semantic evidence or traces."""
import copy
import json
import pytest
from jsonschema import Draft202012Validator
from app import detail_semantics as s,detail_review_repair as repair
from app.core import Problem,digest
from tests.test_detail_review_repair import sample,broken,patch_for


def encoded_sample(tmp_path):
    plan,good=sample(tmp_path)
    plan['reference_encoding']=s.property_encoding(plan)
    plan['reference_encoding_hash']=digest(plan['reference_encoding'])
    return plan,good


def aliases(value):
    result=copy.deepcopy(value)
    def walk(node):
        if isinstance(node,dict):
            for key,value in node.items():
                if key in ('candidate_keys','affected_keys'):node[key]=[v[1:] for v in value]
                else:walk(value)
        elif isinstance(node,list):
            for child in node:walk(child)
    walk(result);return result


def test_full_review_exact_declared_aliases_preserve_original_and_canonical_guard(tmp_path):
    plan,good=encoded_sample(tmp_path);raw=aliases(good);before=copy.deepcopy((plan,raw))
    assert list(Draft202012Validator(s.review_schema(plan)).iter_errors(raw))
    assert not list(Draft202012Validator(s.wire_schema(plan)).iter_errors(raw))
    canonical,audit=s.decode_review(plan,raw)
    assert canonical==good and audit['changes'] and audit['canonical_hash']==digest(good)
    assert s.validate(plan,canonical)['R1']['status']=='passed'
    assert (plan,raw)==before
    header=json.loads(s.messages(plan)[0]['content'].split('可信任务头：',1)[1])
    assert header['schema']==s.wire_schema(plan)


@pytest.mark.parametrize('fault',['unknown','numeric','leading_zero','row','candidate','table','table_hash','property_refs','quote_alias','identity_alias','duplicate'])
def test_tamper_unannounced_encoding_and_duplicate_fail_closed(tmp_path,fault):
    plan,good=encoded_sample(tmp_path);raw=aliases(good);check=raw['rows'][0]['checks'][0]
    if fault=='unknown':check['candidate_keys']=['99999999']
    elif fault=='numeric':check['candidate_keys']=[1]
    elif fault=='leading_zero':check['candidate_keys']=['0'+check['candidate_keys'][0]]
    elif fault=='row':raw['rows'][0]['row_key']='other'
    elif fault=='candidate':raw['rows'][0]['candidate_hash']='wrong'
    elif fault=='table':
        plan['reference_encoding']['rows'][0]['aliases']['99999999']='P001'
        plan['reference_encoding_hash']=digest(plan['reference_encoding'])
    elif fault=='table_hash':plan['reference_encoding_hash']='wrong'
    elif fault=='property_refs':plan['rows'][0]['property_refs']['P001']['value']='changed'
    elif fault=='quote_alias':check['source_quote_keys']=['0012']
    elif fault=='identity_alias':raw['rows'][0]['source_checks'][0]['key']=raw['rows'][0]['source_checks'][0]['key'][1:]
    elif fault=='duplicate':check['candidate_keys']=[good['rows'][0]['checks'][0]['candidate_keys'][0],check['candidate_keys'][0]]
    with pytest.raises(Problem):
        canonical,_=s.decode_review(plan,raw);s.validate(plan,canonical)


def test_legacy_wire_and_decode_are_unchanged(tmp_path):
    plan,good=sample(tmp_path);raw=aliases(good)
    assert s.wire_schema(plan)==s.review_schema(plan)
    assert s.decode_review(plan,raw)==(raw,None)
    with pytest.raises(Problem):s.validate(plan,raw)


def test_patch_uses_same_declared_encoding_and_preserves_unmodified_slots(tmp_path):
    plan,good=encoded_sample(tmp_path);packet=repair.prepare(plan,broken(good));raw=aliases(patch_for(packet,good));before=copy.deepcopy(raw)
    assert not list(Draft202012Validator(repair.schema(plan,packet,wire=True)).iter_errors(raw))
    assembly={};merged,result,audit=repair.apply(plan,packet,raw,assembly=assembly)
    assert merged==good and result['R1']['status']=='passed' and raw==before
    assert audit['preserved_slots'] and assembly['property_reference_decoding']['changes']
    assert assembly['property_reference_decoding']['canonical_hash']==digest(assembly['canonical_detail_review_patch'])


@pytest.mark.parametrize('fault',['review_hash','slot','identity','duplicate'])
def test_patch_cannot_decode_around_frozen_identity_or_uniqueness(tmp_path,fault):
    plan,good=encoded_sample(tmp_path);packet=repair.prepare(plan,broken(good));raw=aliases(patch_for(packet,good))
    if fault=='review_hash':raw['review_hash']='wrong'
    elif fault=='slot':raw['replacements'][0]['slot_id']='other'
    elif fault=='identity':raw['replacements'][0]['replacement'].update(scope='other',key='1')
    else:
        entry=next(r['replacement'] for r in raw['replacements'] if r['replacement'].get('candidate_keys'))
        key=entry['candidate_keys'][0];entry['candidate_keys']=[key,'P'+key]
    with pytest.raises(Problem):repair.apply(plan,packet,raw)


@pytest.mark.parametrize('group,verdict,needs',[
 ('checks','supported',True),('checks','contradicted',True),('checks','unsupported',False),
 ('source_checks','supported',True),('source_checks','contradicted',True),('source_checks','unclear',False),
 ('state_checks',['supported','supported','supported'],True),
 ('state_checks',['supported','unsupported','supported'],False),
 ('state_checks',['contradicted','unsupported','unclear'],True),
 ('state_checks',['supported','unclear','supported'],False)])
def test_wire_only_citation_condition_matches_effective_verdict(tmp_path,group,verdict,needs):
    plan,good=encoded_sample(tmp_path);props=s.review_schema(plan)['properties']['rows']['items']['properties']
    schema=s.wire_item(plan,plan['rows'][0],group,props[group]['items'])
    condition=schema['allOf'][-1]
    entry=dict(source_quote_keys=[],human_quote_keys=[])
    if group=='state_checks':entry['verdicts']=dict(zip(('object_identity','success_timing','actor_perspective'),verdict))
    else:entry['verdict']=verdict
    assert bool(list(Draft202012Validator(condition).iter_errors(entry)))==needs
    if needs:
        field='source_quote_keys' if group=='source_checks' else 'human_quote_keys'
        entry[field]=['declared-test-key']
        assert not list(Draft202012Validator(condition).iter_errors(entry))
    assert not list(Draft202012Validator(condition).iter_errors({}))


def test_actual_provider_serialization_and_both_decode_audits(tmp_path,monkeypatch):
    import asyncio,httpx
    from app.provider import Provider,DEFAULT,origin
    from app.workflow import Workflow
    from tests.provider_transport import reply
    from tests.test_detail_review_patch_workflow import PatchProvider
    from tests.test_requirement_recommendation import project,target
    real_client=httpx.AsyncClient;fake=PatchProvider('pass');bodies=[]
    class NoEvidence:
        def before_send(self,*a):pass
        def response(self,*a,**kw):pass
    async def run():
        store,p=project(tmp_path);provider=Provider(env_path=tmp_path/'absent.env');provider.keys[origin(DEFAULT)]='synthetic-key'
        workflow=Workflow(store,provider);config=dict(workflow.config('clarify'),max_calls=7)
        assert config['detail_property_reference_encoding']==s.REFERENCE_ENCODING
        async def respond(request):
            body=json.loads(request.content);bodies.append(body);messages=body.get('messages',body.get('input'))
            result,_=await fake.request({},messages,NoEvidence())
            if len(bodies)>2:
                header=json.loads(messages[0]['content'].split('可信任务头：',1)[1])
                assert body['text']['format']['schema']==header['schema']
                result=aliases(result)
            return reply(request,json.dumps(result,ensure_ascii=False))
        monkeypatch.setattr('app.provider.httpx.AsyncClient',lambda **kw:real_client(**kw,transport=httpx.MockTransport(respond)))
        task=workflow.start(p['id'],p['revision'],'clarify','',discussion_target=target(),config_override=config)
        await workflow.tasks[task['id']]
        saved=store.get_record(p['id'],task['id'],'run')
        assert saved['calls']==saved['request_budget']['used']==len(bodies)==4
        assert saved['business_draft_review']['detail_semantic_reviews']['R1']['status']=='passed'
        records=[json.loads((store.folder/'evidence/model-calls'/(a['call_id']+'.json')).read_text('utf-8')) for a in saved['attempts']]
        for record in records[2:]:
            audit=record['property_reference_decoding']
            assert audit['changes'] and audit['input_hash']==digest(json.loads(record['final_output']))
            assert all(c['path'][-2] in ('candidate_keys','affected_keys') for c in audit['changes'])
        assert records[3]['review_entry_repair']['preserved_slots']
    asyncio.run(run())


def test_both_quote_kinds_are_allowed_and_unknown_does_not_require_evidence(tmp_path):
    plan,_=encoded_sample(tmp_path)
    for group in ('checks','state_checks'):
        base=s.review_schema(plan)['properties']['rows']['items']['properties'][group]['items']
        condition=s.wire_item(plan,plan['rows'][0],group,base)['allOf'][-1]
        entry=dict(source_quote_keys=['S0001'],human_quote_keys=['H0001'])
        if group=='checks':entry['verdict']='supported'
        else:entry['verdicts']={k:'supported' for k in ('object_identity','success_timing','actor_perspective')}
        assert not list(Draft202012Validator(condition).iter_errors(entry))


def test_existing_supported_empty_normalization_precedes_property_decode(tmp_path):
    plan,good=encoded_sample(tmp_path);raw=aliases(good)
    raw['rows'][0]['checks'][0].pop('affected_keys')
    normalized,changes=s.normalize_review(raw)
    canonical,audit=s.decode_review(plan,normalized)
    assert changes and canonical==good and audit['input_hash']==digest(normalized)
    assert audit['input_hash']!=digest(raw)
    assert s.validate(plan,canonical)['R1']['status']=='passed'


def test_two_rows_share_suffix_only_under_their_own_candidate_hash(tmp_path):
    plan,good=encoded_sample(tmp_path);row=copy.deepcopy(plan['rows'][0]);row['row_key']='R2'
    row['detail_contract']['fields'][0]['required']='另一候选的具体值'
    row['candidate_hash']=digest(row['detail_contract'])
    row['property_refs']={f'P{index:03d}':dict(path=path,value=value) for index,(path,value) in enumerate(sorted(s.properties(row['detail_contract']).items()),1)}
    plan['rows'].append(row);plan['reference_encoding']=s.property_encoding(plan);plan['reference_encoding_hash']=digest(plan['reference_encoding'])
    second=copy.deepcopy(good['rows'][0]);second.update(row_key='R2',candidate_hash=row['candidate_hash']);good['rows'].append(second)
    raw=aliases(good);canonical,audit=s.decode_review(plan,raw)
    assert canonical==good
    assert {c['row_key'] for c in audit['changes']}=={'R1','R2'}
    raw['rows'][0]['candidate_hash']=row['candidate_hash']
    with pytest.raises(Problem):s.decode_review(plan,raw)
