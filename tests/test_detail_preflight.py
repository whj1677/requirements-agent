"""Isolated protocol fixtures; no live model or business semantic acceptance."""
import asyncio
import copy
import json
import pytest
from app import detail_preflight,detail_semantics
from app.business_draft import adapt,PROTOCOL
from app.core import Problem,dumps
from tests.test_detail_semantics import fixture


def setup(tmp_path,empty=False):
    p,scope,excerpts,raw,*_=fixture(tmp_path)
    field=raw['requirements'][0]['detail_contract']['fields'][0]
    field['default_value']='无'
    field.setdefault('evidence',{})['default_value']=dict(status='source',**({'sources':[]} if empty else {}))
    canonical,report=adapt(raw,p,excerpts,'clarify',scope)
    return p,scope,excerpts,raw,report


@pytest.mark.parametrize('empty',[False,True])
def test_only_missing_binding_is_authorized_without_mutating_raw(tmp_path,empty):
    p,scope,excerpts,raw,report=setup(tmp_path,empty);before=copy.deepcopy(raw)
    findings=detail_preflight.prepare(raw,report,p,excerpts,'clarify',scope)
    schema,messages=detail_semantics.correction_messages(raw,dict(correction_protocol=2),findings,excerpts)
    packet=json.loads(messages[1]['content'][0]['text'])
    assert packet['rows']==raw['requirements'] and raw==before
    assert packet['write_scopes']['R1']['allowed_write_paths']==[
        'detail_contract.fields.F1.default_value','detail_contract.fields.F1.evidence.default_value']
    assert packet['write_scopes']['R1']['new_object_kinds']==[]
    patch=dict(rows=[dict(row_key='R1',updates=[dict(path='detail_contract.fields.F1.evidence.default_value',
        value=dict(status='unknown',reason='原资料未说明默认值。'))],new_objects=[])])
    corrected=detail_semantics.apply_correction(raw,patch,schema,findings,protocol_version=2)
    canonical,checked=adapt(corrected,p,excerpts,'clarify',scope)
    assert detail_preflight.accepted(checked,canonical,findings)
    plan=detail_semantics.prepare(corrected,canonical,checked,excerpts)
    assert plan and raw==before


@pytest.mark.parametrize('fault',['duplicate_row','duplicate_id','bad_alias','bad_reference','topology','schema','not_missing'])
def test_ambiguous_or_additionally_invalid_candidate_is_not_eligible(tmp_path,fault):
    p,scope,excerpts,raw,_=setup(tmp_path);detail=raw['requirements'][0]['detail_contract']
    if fault=='duplicate_row':raw['requirements'].append(copy.deepcopy(raw['requirements'][0]))
    elif fault=='duplicate_id':detail['fields'].append(copy.deepcopy(detail['fields'][0]))
    elif fault=='bad_alias':detail['fields'][0]['evidence']['required']=dict(status='source',sources=[dict(source='S999',locator='L1')])
    elif fault=='bad_reference':detail['interactions'][0]['rule_refs']=['missing-rule']
    elif fault=='topology':detail['flow']['edges'][0]['to']='absent'
    elif fault=='schema':detail['fields'][0]['extra']='invalid'
    else:detail['fields'][0]['evidence']['default_value']=dict(status='proposal')
    _,report=adapt(raw,p,excerpts,'clarify',scope);before=copy.deepcopy(raw)
    assert detail_preflight.prepare(raw,report,p,excerpts,'clarify',scope) is None
    assert raw==before


def test_multiple_missing_paths_are_precise_and_forged_binding_cannot_downgrade(tmp_path):
    p,scope,excerpts,raw,_=setup(tmp_path)
    detail=raw['requirements'][0]['detail_contract']
    detail['fields'][0]['evidence']['required']=dict(status='source',sources=[])
    _,report=adapt(raw,p,excerpts,'clarify',scope)
    outcomes=detail_preflight.prepare(raw,report,p,excerpts,'clarify',scope)
    assert len(outcomes['R1']['findings'])==2
    detail['fields'][0]['evidence']['default_value']['sources']=[dict(source='S1',locator='L1')]
    schema,_=detail_semantics.correction_messages(raw,dict(correction_protocol=2),outcomes,excerpts)
    patch=dict(rows=[dict(row_key='R1',updates=[dict(path='detail_contract.fields.F1.evidence.default_value',
        value=dict(status='unknown'))],new_objects=[])])
    with pytest.raises(Problem):detail_semantics.apply_correction(raw,patch,schema,outcomes,protocol_version=2)


@pytest.mark.parametrize('fault',['orphan_evidence','field_ref','interaction_ref','parent_ref','parent_cycle','applicability','empty_section'])
def test_local_object_integrity_is_required_in_both_gates(tmp_path,fault):
    p,scope,excerpts,raw,report=setup(tmp_path)
    outcomes=detail_preflight.prepare(raw,report,p,excerpts,'clarify',scope)
    assert outcomes
    detail=raw['requirements'][0]['detail_contract']
    if fault=='orphan_evidence':detail['fields'][0]['evidence']['not_a_property']=dict(status='unknown')
    elif fault=='field_ref':detail['interactions'][0]['field_refs']=['absent']
    elif fault=='interaction_ref':detail['flow']['nodes'][0]['interaction_id']='absent'
    elif fault=='parent_ref':detail['fields'][0]['parent_id']='absent'
    elif fault=='parent_cycle':
        extra=copy.deepcopy(detail['fields'][0]);extra['id']='F_cycle';extra['parent_id']='F1'
        detail['fields'][0]['parent_id']='F_cycle';detail['fields'].append(extra)
    elif fault=='applicability':detail['applicability']['fields']['applicable']=False
    else:detail['applicability']['state_transitions']['applicable']=True;detail['state_transitions']=[]
    _,report=adapt(raw,p,excerpts,'clarify',scope)
    assert detail_preflight.prepare(raw,report,p,excerpts,'clarify',scope) is None
    for field in detail['fields']:
        field['evidence']['default_value']=dict(status='unknown')
    canonical,report=adapt(raw,p,excerpts,'clarify',scope)
    assert not detail_preflight.accepted(report,canonical,outcomes)


def test_authorized_reference_patch_cannot_introduce_missing_local_object(tmp_path):
    p,scope,excerpts,raw,_=setup(tmp_path)
    detail=raw['requirements'][0]['detail_contract']
    detail['fields'][0]['evidence']['default_value']=dict(status='unknown')
    item=detail['interactions'][0];item['field_refs']=['F1']
    item.setdefault('evidence',{})['field_refs']=dict(status='source')
    _,report=adapt(raw,p,excerpts,'clarify',scope)
    outcomes=detail_preflight.prepare(raw,report,p,excerpts,'clarify',scope)
    assert outcomes
    schema,_=detail_semantics.correction_messages(raw,dict(correction_protocol=2),outcomes,excerpts)
    base='detail_contract.interactions.'+item['id']
    patch=dict(rows=[dict(row_key='R1',updates=[dict(path=base+'.field_refs',value=['FIELD_DOES_NOT_EXIST']),
        dict(path=base+'.evidence.field_refs',value=dict(status='unknown'))],new_objects=[])])
    corrected=detail_semantics.apply_correction(raw,patch,schema,outcomes,protocol_version=2)
    canonical,report=adapt(corrected,p,excerpts,'clarify',scope)
    assert not detail_preflight.accepted(report,canonical,outcomes)


@pytest.mark.parametrize('mode',['pass','correct','still_bad','bad_patch','unchanged','bad_source','budget'])
def test_workflow_preflight_keeps_full_review_and_bounded_failure(tmp_path,mode):
    from tests.test_detail_semantic_workflow import SemanticProvider
    from tests.test_requirement_recommendation import project,target
    from app.workflow import Workflow
    class Provider(SemanticProvider):
        def __init__(self):super().__init__(mode);self.preflight_count=0
        async def request(self,config,messages,evidence=None):
            payload=json.loads(messages[1]['content'][0]['text'])
            if 'source_binding_missing' in json.dumps(payload.get('findings',{})):
                evidence.before_send(config);self.received.append(copy.deepcopy(messages));self.preflight_count+=1
                path='detail_contract.fields.F1.evidence.default_value'
                proof=dict(status='unknown',reason='合成资料没有默认值依据。')
                if mode=='bad_patch':path='detail_contract.fields.F1.evidence.required'
                if mode=='bad_source':proof=dict(status='source',sources=[dict(source='S404',locator='L1')])
                updates=[] if mode=='unchanged' else [dict(path=path,value=proof)]
                result=dict(rows=[dict(row_key='R1',updates=updates,new_objects=[])])
                evidence.response(dumps(result),http_status=200,finish_reason='stop',usage={'total_tokens':100})
                return result,dict(finish_reason='stop',usage={'total_tokens':100})
            is_review=bool(payload.get('rows') and 'required_checks' in payload['rows'][0])
            if is_review:
                # Final mock evidence must match the actual returned review.
                class DeferredReviewEvidence:
                    def before_send(self,*args):evidence.before_send(*args)
                    def response(self,*args,**kwargs):pass
            result,meta=await super().request(config,messages,DeferredReviewEvidence() if is_review else evidence)
            if is_review:
                by_key={row['row_key']:row for row in result['rows']}
                for scope_row in payload.get('unknown_scope',{}).get('rows',[]):
                    check=next(c for c in by_key[scope_row['row_key']]['checks'] if c['scope']=='detail' and c['dimension']=='field_conditions')
                    for prop in scope_row['properties']:
                        if prop['key'] not in check['candidate_keys']:check['candidate_keys'].append(prop['key'])
                evidence.response(dumps(result),http_status=200,finish_reason='stop',usage={'total_tokens':100})
            header=json.loads(messages[0]['content'].split('可信任务头：',1)[1])
            if header.get('response_protocol')==PROTOCOL and not header.get('grounding_protocol'):
                field=result['requirements'][0]['detail_contract']['fields'][0]
                field['default_value']='无';field['evidence']['default_value']=dict(status='source')
                evidence.response(dumps(result),http_status=200,finish_reason='stop',usage={'total_tokens':100})
            return result,meta
    async def run():
        store,p=project(tmp_path);provider=Provider();workflow=Workflow(store,provider)
        config=dict(workflow.config('clarify'),max_calls=1 if mode=='budget' else 6)
        task=workflow.start(p['id'],p['revision'],'clarify','',discussion_target=target(),config_override=config)
        await workflow.tasks[task['id']]
        saved=store.get_record(p['id'],task['id'],'run')
        if mode=='budget':
            assert saved['calls']==1 and provider.preflight_count==provider.review_count==0
            return
        assert provider.preflight_count==1
        audit=saved['business_draft_review']['detail_preflight']
        if mode in ('bad_patch','unchanged','bad_source'):
            assert audit['status']=='failed' and provider.review_count==0 and saved['calls']==3
        else:
            result=saved['business_draft_review']['detail_semantic_reviews']['R1']
            assert result['status']==('failed' if mode=='still_bad' else 'passed')
            assert provider.review_count==(2 if mode in ('correct','still_bad') else 1)
            assert saved['calls']==(6 if mode in ('correct','still_bad') else 4)
    asyncio.run(run())


def test_cancel_during_preflight_preserves_project_and_closes_evidence(tmp_path):
    from tests.test_detail_semantic_workflow import SemanticProvider
    from tests.test_requirement_recommendation import project,target
    from app.workflow import Workflow
    async def run():
        store,p=project(tmp_path);started=asyncio.Event()
        class Blocking(SemanticProvider):
            async def request(self,config,messages,evidence=None):
                if 'source_binding_missing' in messages[1]['content'][0]['text']:
                    evidence.before_send(config);self.received.append(copy.deepcopy(messages))
                    started.set();await asyncio.Event().wait()
                result,meta=await super().request(config,messages,evidence)
                if 'requirements' not in result:return result,meta
                field=result['requirements'][0]['detail_contract']['fields'][0]
                field['default_value']='无';field['evidence']['default_value']=dict(status='source')
                evidence.response(dumps(result),http_status=200,finish_reason='stop',usage={'total_tokens':100})
                return result,meta
        provider=Blocking();workflow=Workflow(store,provider)
        task=workflow.start(p['id'],p['revision'],'clarify','',discussion_target=target(),
            config_override=dict(workflow.config('clarify'),max_calls=5))
        await asyncio.wait_for(started.wait(),5)
        running=workflow.tasks[task['id']];workflow.cancel(p['id'],task['id']);await running
        saved=store.get_record(p['id'],task['id'],'run')
        assert saved['status']=='cancelled' and saved['calls']==3 and provider.review_count==0
        assert store.get(p['id'])==p
        records=[json.loads((store.folder/'evidence/model-calls'/(a['call_id']+'.json')).read_text('utf-8')) for a in saved['attempts']]
        assert records[0]['detail_preflight']['status']=='cancelled'
        assert records[2]['validation_result']=='CANCELLED' and records[2]['ended_at']
    asyncio.run(run())
