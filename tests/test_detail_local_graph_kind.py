"""One model-selected kind correction, never an automatic graph rewrite."""
import asyncio
import copy
import json
import pytest
from app import detail_preflight as preflight,detail_semantics as semantics
from app.business_draft import adapt,PROTOCOL
from app.core import Problem,digest,dumps
from app.workflow import Workflow
from tests.test_detail_semantics import fixture,model_response
from tests.test_detail_semantic_workflow import generated_detail
from tests.test_business_draft import draft_row,packet
from tests.test_runtime import FakeProvider
from tests.test_requirement_recommendation import project,target

MARKER='detail-local-graph-kind-1'


def setup(tmp_path):
    p,scope,excerpts,raw,*_=fixture(tmp_path)
    raw['requirements'][0]['detail_contract']=generated_detail()
    next(e for e in raw['requirements'][0]['detail_contract']['flow']['edges'] if e['id']=='E4')['from']='N2'
    _,report=adapt(raw,p,excerpts,'clarify',scope)
    return p,scope,excerpts,raw,report


def findings(p,scope,excerpts,raw,report,marker=MARKER):
    return preflight.prepare(raw,report,p,excerpts,'clarify',scope,graph_kind_protocol=marker)


def patch(kind='action'):
    return dict(rows=[dict(row_key='R1',updates=[dict(path='detail_contract.flow.nodes.N3.kind',value=kind)],new_objects=[])])


def test_qualification_probe_is_private_and_only_authorizes_kind_and_its_proof(tmp_path):
    p,scope,excerpts,raw,report=setup(tmp_path);before=copy.deepcopy((raw,report,p));out=findings(p,scope,excerpts,raw,report)
    assert out and not findings(p,scope,excerpts,raw,report,marker=None)
    binding=out['R1']['findings'][0]['graph_kind_binding']
    assert binding['candidate_hash']==digest(raw['requirements'][0]['detail_contract'])
    assert binding['node_id']=='N3' and binding['original_kind']=='decision'
    assert binding['incoming_edge_ids']==['E2'] and binding['outgoing_edge_ids']==['E3']
    schema,messages=semantics.correction_messages(raw,dict(correction_protocol=2),out,excerpts)
    payload=json.loads(messages[1]['content'][0]['text'])
    assert payload['rows']==raw['requirements']
    assert next(n for n in payload['rows'][0]['detail_contract']['flow']['nodes'] if n['id']=='N3')['kind']=='decision'
    assert payload['write_scopes']['R1']['allowed_write_paths']==['detail_contract.flow.nodes.N3.evidence.kind','detail_contract.flow.nodes.N3.kind']
    assert payload['write_scopes']['R1']['new_object_kinds']==[]
    corrected=semantics.apply_correction(raw,patch(),schema,out,protocol_version=2)
    canonical,checked=adapt(corrected,p,excerpts,'clarify',scope)
    assert preflight.accepted(checked,canonical,out)
    expected=copy.deepcopy(raw);next(n for n in expected['requirements'][0]['detail_contract']['flow']['nodes'] if n['id']=='N3')['kind']='action'
    assert corrected==expected and (raw,report,p)==before


@pytest.mark.parametrize('fault',['zero_outgoing','other_topology','invalid_source','empty_source','invalid_reference','orphan_proof','invalid_schema','other_empty_kind','two_decisions'])
def test_other_defects_cannot_be_hidden_by_kind_probe(tmp_path,fault):
    p,scope,excerpts,raw,_=setup(tmp_path);d=raw['requirements'][0]['detail_contract']
    if fault=='zero_outgoing':next(e for e in d['flow']['edges'] if e['id']=='E3')['from']='N2'
    elif fault=='other_topology':d['flow']['edges'][0]['to']='absent'
    elif fault in ('invalid_source','empty_source'):
        d['fields'][0]['evidence']['name']=dict(status='source',sources=[] if fault=='empty_source' else [dict(source='S404',locator='L1')])
    elif fault=='invalid_reference':d['interactions'][0]['acceptance_refs']=['C999']
    elif fault=='orphan_proof':d['fields'][0]['evidence']['not_a_property']=dict(status='proposal')
    elif fault=='invalid_schema':d['fields'][0]['name']=17
    elif fault=='other_empty_kind':d['flow']['nodes'][0]['kind']=''
    else:d['flow']['nodes'][0]['kind']='decision'
    _,report=adapt(raw,p,excerpts,'clarify',scope)
    assert findings(p,scope,excerpts,raw,report) is None


def test_only_existing_normalization_is_used_for_shape_and_raw_hash_is_preserved(tmp_path):
    p,scope,excerpts,raw,_=setup(tmp_path)
    raw['requirements'][0]['detail_contract']['fields'][0]['evidence']['name']['status']='recommendation'
    _,report=adapt(raw,p,excerpts,'clarify',scope);before=copy.deepcopy(raw)
    out=findings(p,scope,excerpts,raw,report)
    assert out['R1']['qualification_probe']['existing_shape_normalizations']
    assert out['R1']['findings'][0]['graph_kind_binding']['candidate_hash']==digest(raw['requirements'][0]['detail_contract'])
    assert raw==before
    raw['requirements'][0]['detail_contract']['fields'][0]['evidence']['name']['status']='fact'
    _,report=adapt(raw,p,excerpts,'clarify',scope)
    assert findings(p,scope,excerpts,raw,report) is None


@pytest.mark.parametrize('fault',['empty_kind','edge','label','actor','owner','proof','addition','hash','target','refs','original_changed','raw_changed'])
def test_local_kind_patch_and_frozen_scope_reject_tampering(tmp_path,fault):
    p,scope,excerpts,raw,report=setup(tmp_path);out=findings(p,scope,excerpts,raw,report)
    schema,_=semantics.correction_messages(raw,dict(correction_protocol=2),out,excerpts);value=patch()
    if fault=='empty_kind':value=patch('')
    elif fault in ('edge','label','actor','owner','proof'):
        path={'edge':'detail_contract.flow.edges.E3.condition','label':'detail_contract.flow.nodes.N3.label',
              'actor':'detail_contract.flow.nodes.N3.actor','owner':'detail_contract.flow.nodes.N3.interaction_id',
              'proof':'detail_contract.flow.nodes.N3.evidence.label'}[fault]
        value['rows'][0]['updates'].append(dict(path=path,value=dict(status='proposal') if fault=='proof' else 'changed'))
    elif fault=='addition':value['rows'][0]['new_objects']=[dict(section='flow.nodes',value=dict(id='Nnew',kind='end'))]
    elif fault=='hash':out['R1']['findings'][0]['graph_kind_binding']['candidate_hash']='tampered'
    elif fault=='target':out['R1']['findings'][0]['affected_paths']=['detail_contract.flow.nodes.N2.kind']
    elif fault=='refs':out['R1']['reference_scope']['allowed_refs'].append('C404')
    elif fault=='raw_changed':raw['summary']='tampered'
    else:raw['requirements'][0]['detail_contract']['flow']['edges'][0]['condition']='changed'
    with pytest.raises(Problem):semantics.apply_correction(raw,value,schema,out,protocol_version=2)


@pytest.mark.parametrize('kind',['decision','start','end'])
def test_model_kind_still_must_pass_original_full_topology(tmp_path,kind):
    p,scope,excerpts,raw,report=setup(tmp_path);out=findings(p,scope,excerpts,raw,report)
    schema,_=semantics.correction_messages(raw,dict(correction_protocol=2),out,excerpts)
    corrected=semantics.apply_correction(raw,patch(kind),schema,out,protocol_version=2)
    canonical,checked=adapt(corrected,p,excerpts,'clarify',scope)
    assert not preflight.accepted(checked,canonical,out)


class KindProvider(FakeProvider):
    business_draft_protocol=PROTOCOL
    def __init__(self,kind='action',mode='pass'):super().__init__([]);self.kind=kind;self.mode=mode;self.on_patch=None;self.configs=[];self.stages=[]
    async def request(self,config,messages,evidence=None):
        evidence.before_send(config);self.received.append(copy.deepcopy(messages));self.configs.append(copy.deepcopy(config))
        header=json.loads(messages[0]['content'].split('可信任务头：',1)[1]);payload=json.loads(messages[1]['content'][0]['text'])
        if header.get('grounding_protocol'):
            self.stages.append('body_source' if payload['fields'][0]['path'][0]=='requirements' else 'detail_challenge')
            result=dict(candidate_hash=payload['candidate_hash'],input_hash=payload['input_hash'],
                checked_field_ids=[f['id'] for f in payload['fields']],findings=[])
        elif header.get('response_protocol')==PROTOCOL:
            self.stages.append('main')
            row=draft_row();row['detail_contract']=generated_detail()
            next(e for e in row['detail_contract']['flow']['edges'] if e['id']=='E4')['from']='N2'
            result=packet(row)
        elif payload.get('rows') and 'required_checks' in payload['rows'][0]:
            self.stages.append('full_detail')
            from tests.test_detail_semantics import response
            expanded=response(payload)
            for check in expanded['rows'][0]['checks']:
                for trace in check['traces']:
                    if trace['edge_ids']==['E1','E2','E4']:trace['edge_ids']=['E1','E4']
            result=model_response(payload,expanded)
        else:
            self.stages.append('kind_preflight')
            if self.mode=='http_error':raise Problem('PROVIDER_HTTP_ERROR','Synthetic HTTP failure')
            if self.on_patch:self.on_patch()
            assert payload['findings']['R1'][0]['dimension']=='local_graph_kind_single_branch'
            assert next(n for n in payload['rows'][0]['detail_contract']['flow']['nodes'] if n['id']=='N3')['kind']=='decision'
            result=patch(self.kind)
        evidence.response(dumps(result),http_status=200,finish_reason='stop',usage={'total_tokens':100})
        return result,dict(finish_reason='stop',usage={'total_tokens':100},response_model='OFFLINE_PROTOCOL_FIXTURE')


@pytest.mark.parametrize('mode,limit,kind,calls,accepted',[
    ('pass',7,'action',5,True),('pass',1,'action',1,False),('pass',2,'action',2,False),
    ('pass',7,'decision',3,False),('legacy',7,'action',2,False),('http_error',7,'action',3,False),('cancel',7,'action',3,False)])
def test_workflow_reuses_one_preflight_then_requires_full_high_review(tmp_path,mode,limit,kind,calls,accepted):
    async def run():
        store,p=project(tmp_path);provider=KindProvider(kind,mode);workflow=Workflow(store,provider)
        config=dict(workflow.config('clarify'),max_calls=limit)
        if mode=='legacy':config.pop('detail_local_graph_kind_protocol')
        task=workflow.start(p['id'],p['revision'],'clarify','',discussion_target=dict(target(),detail_required=True),config_override=config)
        if mode=='cancel':provider.on_patch=lambda:store.update_run(p['id'],task['id'],status='cancelled')
        await workflow.tasks[task['id']]
        saved=store.get_record(p['id'],task['id'],'run')
        assert saved['calls']==saved['request_budget']['used']==len(provider.received)==calls
        expected=['main']
        if limit>1:expected.append('body_source')
        if limit>2 and mode!='legacy':expected.append('kind_preflight')
        if accepted:expected.extend(['full_detail','detail_challenge'])
        assert provider.stages==expected
        if accepted:
            assert saved['business_draft_review']['detail_semantic_reviews']['R1']['status']=='passed'
            assert provider.configs[provider.stages.index('full_detail')]['reasoning_effort']=='high'
            assert saved['repair_count']==1
        elif mode=='cancel':assert saved['status']=='cancelled' and not saved.get('result_applied')
        else:assert not any(x.get('detail_contract') for x in saved.get('response',{}).get('proposals',[]))
        assert store.get(p['id'])['items'][:len(p['items'])]==p['items']
    asyncio.run(run())


def test_action_plan_declares_narrow_opt_in_without_new_call_budget(tmp_path):
    from app.actions import Actions
    store,p=project(tmp_path);workflow=Workflow(store,KindProvider());snapshots={}
    plan=Actions(store,workflow).plan(p['id'],dict(expected_revision=p['revision'],action='clarify',message='',document_type='prd',target=target(),max_calls=2),config_snapshot=snapshots)
    policy=plan['detail_local_graph_kind_preflight']['clarify']
    assert policy['protocol']==MARKER and policy['shares_existing_single_preflight'] and policy['extra_requests_max']==0
    assert not policy['new_objects'] and not policy['deletions']
    assert plan['detail_review_entry_repair']['clarify']['detail_chain_requests_max']==7
    assert snapshots['clarify']['max_calls']==2


@pytest.mark.parametrize('surface',['both','raw_report','canonical'])
def test_private_empty_kind_probe_can_never_be_accepted_directly(tmp_path,surface):
    p,scope,excerpts,raw,report=setup(tmp_path);out=findings(p,scope,excerpts,raw,report)
    model_candidate=copy.deepcopy(raw)
    next(n for n in model_candidate['requirements'][0]['detail_contract']['flow']['nodes'] if n['id']=='N3')['kind']='' if surface=='both' else 'action'
    canonical,checked=adapt(model_candidate,p,excerpts,'clarify',scope)
    assert 'R1' in preflight._accepted_keys(checked,canonical)
    if surface=='raw_report':
        next(n for n in checked['accepted'][0]['raw']['detail_contract']['flow']['nodes'] if n['id']=='N3')['kind']=''
    elif surface=='canonical':
        candidate=next(c for c in canonical['proposals'] if c.get('detail_contract'))
        next(n for n in candidate['detail_contract']['flow']['nodes'] if n['id']=='N3')['kind']=''
    assert not preflight.accepted(checked,canonical,out)
