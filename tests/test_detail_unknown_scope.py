"""Bounded unknown coverage only; semantic classification remains model-owned."""
import copy
import json
import pytest
from jsonschema import Draft202012Validator
from app import detail_semantics as s,detail_review_repair as repair
from app.core import Problem,digest
from app.business_draft import adapt
from app.requirement_details import detail_issues
from tests.test_detail_semantics import fixture,model_response
from tests.test_detail_review_repair import patch_for


def sample(tmp_path,enabled=True,reason='具体业务规则尚未确定'):
    p,local,excerpts,raw,_,_,_=fixture(tmp_path)
    raw['requirements'][0]['detail_contract']['fields'][0].setdefault('evidence',{})['default_value']=dict(status='unknown',reason=reason)
    canonical,report=adapt(raw,p,excerpts,'clarify',local)
    plan=s.prepare(raw,canonical,report,excerpts,protocol_version=3,reference_encoding=s.REFERENCE_ENCODING,unknown_scope=s.UNKNOWN_SCOPE if enabled else None)
    good=model_response(plan)
    unknown_key=next(k for k,v in plan['rows'][0]['property_refs'].items() if v['path'].endswith('.F1.default_value'))
    check=next(c for c in good['rows'][0]['checks'] if c['scope']=='detail' and c['dimension']=='field_conditions')
    if unknown_key not in check['candidate_keys']:check['candidate_keys'].append(unknown_key)
    return plan,good,unknown_key


def omit(good,key):
    raw=copy.deepcopy(good)
    check=next(c for c in raw['rows'][0]['checks'] if c['scope']=='detail' and c['dimension']=='field_conditions')
    check['candidate_keys']=[k for k in check['candidate_keys'] if k!=key]
    assert check['candidate_keys']
    return raw


def test_omitted_unknown_is_exact_single_patch_slot_and_canonical_schema_unchanged(tmp_path):
    plan,good,key=sample(tmp_path);bad=omit(good,key);before=copy.deepcopy((plan,bad))
    assert not list(Draft202012Validator(s.review_schema(plan)).iter_errors(bad))
    assert list(Draft202012Validator(s.wire_schema(plan)).iter_errors(bad))
    with pytest.raises(Problem,match='field_conditions'):s.validate(plan,bad)
    packet=repair.prepare(plan,bad)
    assert len(packet['slots'])==1 and packet['slots'][0]['identity']==dict(scope='detail',dimension='field_conditions')
    assert [e['code'] for e in packet['slots'][0]['errors']]==['unknown_scope_coverage']
    merged,outcome,audit=repair.apply(plan,packet,patch_for(packet,good))
    assert merged==good and outcome['R1']['status']=='passed' and audit['preserved_slots']
    assert (plan,bad)==before


@pytest.mark.parametrize('reason',['具体业务规则尚未确定','资料未说明存储默认值或显示占位文案'])
def test_classification_is_not_host_word_matching_and_unknown_still_blocks_confirmation(tmp_path,reason):
    plan,good,key=sample(tmp_path,reason=reason)
    assert s.validate(plan,good)['R1']['status']=='passed'
    assert plan['unknown_scope']['rows'][0]['properties'][0]['reason']==reason
    issues=detail_issues(dict(id='REQ-test',detail_contract=plan['rows'][0]['detail_contract']))
    assert any(i['code']=='DETAIL_PROPERTY_UNKNOWN' and i['path'].endswith('.F1.default_value') for i in issues)


def test_model_counterexample_uses_existing_business_correction_scope(tmp_path):
    plan,good,key=sample(tmp_path)
    check=next(c for c in good['rows'][0]['checks'] if c['scope']=='detail' and c['dimension']=='field_conditions')
    check.update(verdict='unsupported',affected_keys=[key],correction='按原来源核对该属性的具体业务口径，保留真正未知部分。')
    result=s.validate(plan,good)['R1']
    assert result['status']=='failed'
    assert any('detail_contract.fields.F1.default_value' in f['affected_paths'] for f in result['findings'])


def test_wire_coverage_accepts_only_declared_P_alias_and_patch_enforces_full_validate(tmp_path):
    plan,good,key=sample(tmp_path);raw=copy.deepcopy(good)
    check=next(c for c in raw['rows'][0]['checks'] if c['scope']=='detail' and c['dimension']=='field_conditions')
    check['candidate_keys']=[k[1:] if k==key else k for k in check['candidate_keys']]
    assert not list(Draft202012Validator(s.wire_schema(plan)).iter_errors(raw))
    decoded,_=s.decode_review(plan,raw);assert decoded==good
    packet=repair.prepare(plan,omit(good,key));patch=patch_for(packet,good)
    patch['replacements'][0]['replacement']['candidate_keys']=[k[1:] if k==key else k for k in patch['replacements'][0]['replacement']['candidate_keys']]
    assert not list(Draft202012Validator(repair.schema(plan,packet,wire=True)).iter_errors(patch))
    assert repair.apply(plan,packet,patch)[1]['R1']['status']=='passed'
    missing=patch_for(packet,omit(good,key))
    with pytest.raises(Problem):repair.apply(plan,packet,missing)


@pytest.mark.parametrize('fault',['list','hash','candidate','row','property_refs','missing_marker'])
def test_frozen_unknown_scope_cannot_be_tampered(tmp_path,fault):
    plan,good,key=sample(tmp_path)
    if fault=='list':
        plan['unknown_scope']['rows'][0]['properties']=[];plan['unknown_scope_hash']=digest(plan['unknown_scope'])
    elif fault=='hash':plan['unknown_scope_hash']='wrong'
    elif fault=='candidate':plan['rows'][0]['detail_contract']['fields'][0]['evidence']['default_value']['reason']='changed'
    elif fault=='row':plan['unknown_scope']['rows'][0]['row_key']='other'
    elif fault=='property_refs':plan['rows'][0]['property_refs'][key]['value']='changed'
    else:plan.pop('unknown_scope')
    with pytest.raises(Problem):s.validate(plan,good)


def test_legacy_missing_marker_and_fresh_candidate_contract(tmp_path):
    plan,good,key=sample(tmp_path,enabled=False)
    assert s.validate(plan,omit(good,key))['R1']['status']=='passed'
    first=s.unknown_scope_contract(plan);plan['rows'][0]['detail_contract']['fields'][0]['evidence']['default_value']['reason']='新的业务未知原因'
    plan['rows'][0]['candidate_hash']=digest(plan['rows'][0]['detail_contract'])
    second=s.unknown_scope_contract(plan)
    assert first!=second and first['rows'][0]['candidate_hash']!=second['rows'][0]['candidate_hash']



def rebind(row):
    row['candidate_hash']=digest(row['detail_contract'])
    row['property_refs']={f'P{i:03d}':dict(path=p,value=v) for i,(p,v) in enumerate(sorted(s.properties(row['detail_contract']).items()),1)}


@pytest.mark.parametrize('count',[300,301])
def test_unknown_capacity_uses_existing_limit_without_truncation(tmp_path,count):
    plan,good,_=sample(tmp_path,enabled=False);row=plan['rows'][0]
    original=row['detail_contract']['fields'][0]
    for prop in ('meaning','required'):original['evidence'][prop]=dict(status='unknown',reason='具体业务决定待明确')
    row['detail_contract']['fields']=[dict(copy.deepcopy(original),id='F'+str(i)) for i in range(1,101)]
    if count==301:row['detail_contract']['fields'][0]['evidence']['visibility']=dict(status='unknown',reason='具体可见范围未定')
    rebind(row)
    if count==301:
        with pytest.raises(Problem,match='引用容量'):s.unknown_scope_contract(plan)
        return
    plan.pop('reference_encoding',None);plan.pop('reference_encoding_hash',None)
    plan['unknown_scope']=s.unknown_scope_contract(plan);plan['unknown_scope_hash']=digest(plan['unknown_scope'])
    assert len(plan['unknown_scope']['rows'][0]['properties'])==300
    check=next(c for c in good['rows'][0]['checks'] if c['scope']=='detail' and c['dimension']=='field_conditions')
    check['candidate_keys']=[p['key'] for p in plan['unknown_scope']['rows'][0]['properties']]
    item=s.wire_item(plan,row,'checks',s.CHECK)
    assert not list(Draft202012Validator(item).iter_errors(check))
    check.update(scope='I1',dimension='operation_results',candidate_keys=[next(iter(row['property_refs']))])
    assert not list(Draft202012Validator(item).iter_errors(check))


def test_literal_unknown_duplicate_and_named_wait_exception_follow_existing_diagnostics(tmp_path):
    plan,_,_=sample(tmp_path,enabled=False);row=plan['rows'][0]
    row['detail_contract']['fields'][0]['default_value']='TBD'
    row['detail_contract']['applicability']['state_transitions']=dict(applicable=True,reason='')
    row['detail_contract']['state_transitions']=[
        dict(id='T1',from_state='已提交',to_state='待核对',trigger='收到请求',condition='服务端处理结果尚未取得',prohibited_actions='禁止新建重复请求'),
        dict(id='T2',from_state='待核对',to_state='已完成',trigger='取得原请求结果',condition='已确认处理完成',prohibited_actions='禁止重复写入')]
    rebind(row)
    props=s.unknown_scope_contract(plan)['rows'][0]['properties']
    assert len(props)==1 and len(props[0]['diagnostics'])==2
    assert props[0]['reason']=='具体业务规则尚未确定'
    row['detail_contract']['fields'][0]['evidence']['default_value']['status']='proposal';rebind(row)
    props=s.unknown_scope_contract(plan)['rows'][0]['properties']
    assert len(props)==1 and props[0]['value']=='TBD' and len(props[0]['diagnostics'])==1


def test_unreferenceable_existing_unknown_diagnostic_rejects(tmp_path):
    plan,_,_=sample(tmp_path,enabled=False);row=plan['rows'][0]
    row['detail_contract']['fields'][0]['evidence']['enum_values']=dict(status='unknown',reason='具体枚举值未定')
    rebind(row)
    with pytest.raises(Problem,match='无法绑定'):s.unknown_scope_contract(plan)


@pytest.mark.parametrize('mode,limit,calls',[('pass',7,5),('budget',2,2),('repair_budget',3,3),('cancel',7,4),('capacity',7,2)])
def test_unknown_scope_actual_provider_full_and_entry_patch_with_budget_cancel(tmp_path,monkeypatch,mode,limit,calls):
    import asyncio,httpx
    from app.provider import Provider,DEFAULT,origin
    from app.workflow import Workflow
    from app.core import dumps
    from app.business_draft import PROTOCOL
    from tests.provider_transport import reply
    from tests.test_business_draft import draft_row,packet
    from tests.test_detail_semantic_workflow import generated_detail
    from tests.test_requirement_recommendation import project,target
    bodies=[];good_reviews=[];stages=[];real_client=httpx.AsyncClient
    async def run():
        store,p=project(tmp_path);provider=Provider(env_path=tmp_path/'absent.env');provider.keys[origin(DEFAULT)]='synthetic-key'
        workflow=Workflow(store,provider);config=dict(workflow.config('clarify'),max_calls=limit)
        assert config['detail_unknown_scope_protocol']==s.UNKNOWN_SCOPE
        async def respond(request):
            body=json.loads(request.content);bodies.append(body)
            messages=body.get('messages',body.get('input'));header=json.loads(messages[0]['content'].split('可信任务头：',1)[1])
            payload=json.loads(messages[1]['content'][0]['text'])
            if header.get('grounding_protocol'):
                stages.append('source' if header.get('source_only') else 'detail_challenge')
                result=dict(candidate_hash=payload['candidate_hash'],input_hash=payload['input_hash'],
                    checked_field_ids=[field['id'] for field in payload['fields']],findings=[])
                assert not list(Draft202012Validator(header['schema']).iter_errors(result))
            elif header.get('response_protocol')==PROTOCOL:
                stages.append('main')
                row=draft_row();row['detail_contract']=generated_detail()
                row['detail_contract']['fields'][0]['evidence']['default_value']=dict(status='unknown',reason='具体业务默认尚未确定')
                if mode=='capacity':
                    original=row['detail_contract']['fields'][0]
                    for prop in ('meaning','required'):original['evidence'][prop]=dict(status='unknown',reason='具体业务决定待明确')
                    row['detail_contract']['fields']=[dict(copy.deepcopy(original),id='F'+str(i)) for i in range(1,101)]
                    row['detail_contract']['fields'][0]['evidence']['visibility']=dict(status='unknown',reason='具体范围待定')
                result=packet(row)
            else:
                assert request.url.path.endswith('/responses') and body['text']['format']['schema']==header['schema']
                assert body['reasoning']['effort']=='high' and body['max_output_tokens']==65536
                assert header['strict_json_protocol']=='detail-review-strict-json-1'
                if header.get('response_protocol')==repair.PROTOCOL:
                    stages.append('entry_patch')
                    rp=payload['repair_packet'];assert len(rp['slots'])==1
                    assert rp['slots'][0]['identity']==dict(scope='detail',dimension='field_conditions')
                    result=patch_for(rp,good_reviews[-1])
                    assert not list(Draft202012Validator(header['schema']).iter_errors(result))
                    if mode=='cancel':store.update_run(p['id'],task['id'],status='cancelled')
                else:
                    stages.append('detail_review')
                    assert payload['unknown_scope']['protocol']==s.UNKNOWN_SCOPE
                    properties=payload['unknown_scope']['rows'][0]['properties'];assert len(properties)==1
                    result=model_response(payload)
                    check=next(c for c in result['rows'][0]['checks'] if c['scope']=='detail' and c['dimension']=='field_conditions')
                    key=properties[0]['key']
                    if key not in check['candidate_keys']:check['candidate_keys'].append(key)
                    assert not list(Draft202012Validator(header['schema']).iter_errors(result))
                    good_reviews.append(copy.deepcopy(result));check['candidate_keys'].remove(key)
                    assert list(Draft202012Validator(header['schema']).iter_errors(result))
            return reply(request,dumps(result))
        monkeypatch.setattr('app.provider.httpx.AsyncClient',lambda **kw:real_client(**kw,transport=httpx.MockTransport(respond)))
        task=workflow.start(p['id'],p['revision'],'clarify','',discussion_target=dict(target(),detail_required=True),config_override=config)
        await workflow.tasks[task['id']]
        saved=store.get_record(p['id'],task['id'],'run')
        assert len(bodies)==saved['calls']==saved['request_budget']['used']==calls
        records=[json.loads((store.folder/'evidence/model-calls'/(a['call_id']+'.json')).read_text('utf-8')) for a in saved['attempts']]
        expected_stages={'pass':['main','source','detail_review','entry_patch','detail_challenge'],
            'budget':['main','source'],'repair_budget':['main','source','detail_review'],
            'cancel':['main','source','detail_review','entry_patch'],'capacity':['main','source']}
        assert stages==expected_stages[mode]
        assert saved['business_source_review']['status']=='passed'
        if mode=='budget':
            review=saved['business_draft_review']['detail_semantic_reviews']['R1']
            assert saved['status']=='partial' and review['status']=='pending' and review['code']=='BUDGET_EXHAUSTED'
            assert store.get(p['id'])['items'][:len(p['items'])]==p['items']
            assert all(not item.get('detail_contract') for item in store.get(p['id'])['items'][len(p['items']):])
            return
        if mode=='capacity':
            assert records[0]['validation_result']=='DETAIL_REVIEW_INVALID'
            raw=json.loads(records[0]['final_output'])
            assert len(raw['requirements'][0]['detail_contract']['fields'])==100
            assert '引用容量' in dumps(saved)
            assert not saved.get('result_applied') and store.get(p['id'])['items']==p['items']
            return
        assert records[2]['validation_result']=='DETAIL_REVIEW_INVALID'
        assert records[2]['review_entry_diagnostics']['slots'][0]['errors'][0]['code']=='unknown_scope_coverage'
        if mode=='pass':
            assert saved['business_draft_review']['detail_semantic_reviews']['R1']['status']=='passed'
            assert records[3]['review_entry_repair']['full_strict_validation']=='passed'
            assert records[3]['review_entry_repair']['preserved_slots']
            original=json.loads(records[0]['final_output'])['requirements'][0]['detail_contract']['fields'][0]
            assert original['evidence']['default_value']['status']=='unknown'
            assert any(i['code']=='DETAIL_PROPERTY_UNKNOWN' for i in detail_issues(dict(detail_contract=payload_detail(records[2]))))
        elif mode=='cancel':assert saved['status']=='cancelled' and not saved.get('result_applied')
        else:assert saved['business_draft_review']['detail_semantic_reviews']['R1']['status']=='pending'
        assert store.get(p['id'])['items'][:len(p['items'])]==p['items']
    asyncio.run(run())


def payload_detail(record):
    messages=json.loads(record['request_input'])
    return json.loads(messages[1]['content'][0]['text'])['rows'][0]['detail_contract']


def test_covering_mixed_unknowns_does_not_expand_affected_authority(tmp_path):
    plan,_,_=sample(tmp_path,enabled=False);row=plan['rows'][0]
    row['detail_contract']['fields'][0]['evidence']['meaning']=dict(status='unknown',reason='具体业务含义尚未决定')
    rebind(row);plan['reference_encoding']=s.property_encoding(plan);plan['reference_encoding_hash']=digest(plan['reference_encoding'])
    plan['unknown_scope']=s.unknown_scope_contract(plan);plan['unknown_scope_hash']=digest(plan['unknown_scope'])
    good=model_response(plan)
    check=next(c for c in good['rows'][0]['checks'] if c['scope']=='detail' and c['dimension']=='field_conditions')
    keys={p['path']:p['key'] for p in plan['unknown_scope']['rows'][0]['properties']}
    for key in keys.values():
        if key not in check['candidate_keys']:check['candidate_keys'].append(key)
    affected='detail_contract.fields.F1.default_value'
    check.update(verdict='unsupported',affected_keys=[keys[affected]],correction='仅按已有资料核对该属性归因，另一真实业务未知保持。')
    outcome=s.validate(plan,good)['R1']
    assert outcome['status']=='failed' and [f['affected_paths'] for f in outcome['findings']]==[[affected]]
    assert row['detail_contract']['fields'][0]['evidence']['meaning']['status']=='unknown'
    assert any(i['path'].endswith('.F1.meaning') and i['code']=='DETAIL_PROPERTY_UNKNOWN' for i in detail_issues(dict(detail_contract=row['detail_contract'])))
