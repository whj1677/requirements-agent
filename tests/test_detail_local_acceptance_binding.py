"""One finite reference-only preflight, with original business values retained."""
import asyncio
import copy
import json
import pytest
from app import detail_preflight,detail_semantics
from app.business_draft import adapt,_input,PROTOCOL
from app.core import Problem,dumps
from tests.test_detail_semantics import fixture


def setup(tmp_path):
    p,scope,excerpts,raw,*_=fixture(tmp_path)
    visible=_input(p,excerpts,'',scope)['current_rows'][0]['related_clauses']
    known=[x['item_ref'] for x in visible if x['kind']=='acceptance']
    refs=known[:1]+['A1','A2']
    raw['requirements'][0]['detail_contract']['interactions'][0]['acceptance_refs']=refs
    _,report=adapt(raw,p,excerpts,'clarify',scope)
    outcomes=detail_preflight.prepare(raw,report,p,excerpts,'clarify',scope)
    return p,scope,excerpts,raw,report,outcomes,refs[:-1]


def test_invalid_local_alias_gets_reference_only_scope_and_preserves_original(tmp_path):
    p,scope,excerpts,raw,_,outcomes,kept=setup(tmp_path);before=copy.deepcopy(raw)
    assert outcomes
    schema,messages=detail_semantics.correction_messages(raw,dict(correction_protocol=2),outcomes,excerpts)
    packet=json.loads(messages[1]['content'][0]['text'])
    path='detail_contract.interactions.I1.acceptance_refs'
    assert packet['rows']==raw['requirements'] and packet['write_scopes']['R1']['allowed_write_paths']==[path]
    assert packet['write_scopes']['R1']['new_object_kinds']==[]
    finding=outcomes['R1']['findings'][0]
    assert finding['invalid_refs']==['A2'] and finding['preserved_refs']==kept and 'A2' not in finding['allowed_refs']
    patch=dict(rows=[dict(row_key='R1',updates=[dict(path=path,value=kept)],new_objects=[])])
    corrected=detail_semantics.apply_correction(raw,patch,schema,outcomes,protocol_version=2)
    canonical,report=adapt(corrected,p,excerpts,'clarify',scope)
    assert detail_preflight.accepted(report,canonical,outcomes)
    assert detail_semantics.prepare(corrected,canonical,report,excerpts)
    expected=copy.deepcopy(raw);expected['requirements'][0]['detail_contract']['interactions'][0]['acceptance_refs']=kept
    assert corrected==expected and raw==before


@pytest.mark.parametrize('fault',['remove_valid','new_alias','evidence','other_attribute','new_object','new_acceptance'])
def test_reference_patch_cannot_delete_valid_refs_or_modify_business(tmp_path,fault):
    _,_,excerpts,raw,_,outcomes,kept=setup(tmp_path)
    schema,_=detail_semantics.correction_messages(raw,dict(correction_protocol=2),outcomes,excerpts)
    patch=dict(rows=[dict(row_key='R1',updates=[dict(path='detail_contract.interactions.I1.acceptance_refs',value=kept)],new_objects=[])])
    row=patch['rows'][0]
    if fault=='remove_valid':row['updates'][0]['value']=[]
    elif fault=='new_alias':row['updates'][0]['value']=kept+['A404']
    elif fault=='evidence':row['updates'].append(dict(path='detail_contract.interactions.I1.evidence.acceptance_refs',value=dict(status='unknown')))
    elif fault=='other_attribute':row['updates'].append(dict(path='detail_contract.interactions.I1.actor',value='管理员'))
    elif fault=='new_object':row['new_objects']=[dict(section='flow.nodes',value=dict(id='N-new'))]
    else:row['acceptance']=[dict(text='新验收')]
    with pytest.raises(Problem):detail_semantics.apply_correction(raw,patch,schema,outcomes,protocol_version=2)


@pytest.mark.parametrize('fault',['other_ref','wrong_slot','missing_source','field_ref','topology','shape','duplicate_id','duplicate_row'])
def test_additional_defect_cannot_enter_reference_preflight(tmp_path,fault):
    p,scope,excerpts,raw,*_=setup(tmp_path);detail=raw['requirements'][0]['detail_contract']
    if fault=='other_ref':detail['interactions'][0]['acceptance_refs'].append('C404')
    elif fault=='wrong_slot':detail['interactions'][0]['rule_refs']=['A2']
    elif fault=='missing_source':detail['fields'][0].setdefault('evidence',{})['required']=dict(status='source')
    elif fault=='field_ref':detail['interactions'][0]['field_refs']=['F404']
    elif fault=='topology':detail['flow']['edges'][0]['to']='N404'
    elif fault=='shape':detail['extra']='unknown'
    elif fault=='duplicate_id':detail['fields'].append(copy.deepcopy(detail['fields'][0]))
    else:raw['requirements'].append(copy.deepcopy(raw['requirements'][0]))
    _,report=adapt(raw,p,excerpts,'clarify',scope)
    assert detail_preflight.prepare(raw,report,p,excerpts,'clarify',scope) is None


def test_hidden_cross_row_acceptance_in_another_property_blocks_entire_preflight(tmp_path):
    from app.business_draft import _item_aliases
    p,scope,excerpts,raw,*_=setup(tmp_path)
    foreign=copy.deepcopy(next(item for item in p['items'] if item['kind']=='acceptance'))
    foreign.update(id='AC-foreign-only',related_refs=['REQ-other'],target_item_id=None)
    p['items'].append(foreign)
    alias=_item_aliases(p)[foreign['id']]
    raw['requirements'][0]['detail_contract']['fields'][0]['acceptance_refs']=[alias]
    before=copy.deepcopy((p,raw))
    _,report=adapt(raw,p,excerpts,'clarify',scope)
    assert report['rejected'][0]['code']=='BUSINESS_DRAFT_DETAIL_REFERENCE'
    assert detail_preflight.prepare(raw,report,p,excerpts,'clarify',scope) is None
    assert (p,raw)==before


def test_post_patch_acceptance_rechecks_all_reference_properties(tmp_path):
    from app.business_draft import _item_aliases
    p,scope,excerpts,raw,*_=setup(tmp_path)
    foreign=copy.deepcopy(next(item for item in p['items'] if item['kind']=='acceptance'))
    foreign.update(id='AC-hidden',related_refs=['REQ-other'],target_item_id=None);p['items'].append(foreign)
    _,report=adapt(raw,p,excerpts,'clarify',scope)
    outcomes=detail_preflight.prepare(raw,report,p,excerpts,'clarify',scope)
    assert outcomes
    bad=copy.deepcopy(raw);detail=bad['requirements'][0]['detail_contract']
    detail['interactions'][0]['acceptance_refs'].remove('A2')
    detail['fields'][0]['acceptance_refs']=[_item_aliases(p)[foreign['id']]]
    canonical,checked=adapt(bad,p,excerpts,'clarify',scope)
    assert checked['accepted'] and not checked['rejected']
    assert not detail_preflight.accepted(checked,canonical,outcomes)


@pytest.mark.parametrize('request_scope',['single_shared','whole_table_visible'])
def test_actual_whitelist_shared_references_and_other_valid_rows_remain_allowed(tmp_path,request_scope):
    from app.business_draft import _item_aliases,_bind_detail_references,SCHEMA
    from tests.test_business_draft import draft_row
    p,scope,excerpts,raw,*_=setup(tmp_path)
    shared=copy.deepcopy(next(item for item in p['items'] if item['kind']=='acceptance'))
    shared.update(id='AC-visible-shared',related_refs=['REQ-other'],target_item_id=None)
    if request_scope=='single_shared':shared['related_refs'].append(scope['requirement_id'])
    else:
        scope=None
        raw['requirements'].append(draft_row('R2'))
    p['items'].append(shared);alias=_item_aliases(p)[shared['id']]
    raw['requirements'][0]['detail_contract']['fields'][0]['acceptance_refs']=[alias]
    context=_input(p,excerpts,'',scope);_bind_detail_references({'schema':copy.deepcopy(SCHEMA)},context,p)
    assert alias in context['detail_reference_options']['acceptance_refs']
    _,report=adapt(raw,p,excerpts,'clarify' if scope else 'ingest',scope)
    outcomes=detail_preflight.prepare(raw,report,p,excerpts,'clarify' if scope else 'ingest',scope)
    assert outcomes and set(outcomes)=={'R1'}
    assert alias in outcomes['R1']['reference_scope']['allowed_refs']
    fixed=copy.deepcopy(raw);fixed['requirements'][0]['detail_contract']['interactions'][0]['acceptance_refs'].remove('A2')
    canonical,checked=adapt(fixed,p,excerpts,'clarify' if scope else 'ingest',scope)
    assert detail_preflight.accepted(checked,canonical,outcomes)
    if not scope:
        assert {r['row_key'] for r in checked['accepted']}=={'R1','R2'}
        assert fixed['requirements'][1]==raw['requirements'][1]


@pytest.mark.parametrize('mode',['pass','still_bad','invalid_patch'])
def test_reference_fix_still_requires_complete_review_and_shared_budget(tmp_path,mode):
    from app.workflow import Workflow
    from tests.test_requirement_recommendation import project,target
    from tests.test_detail_semantic_workflow import SemanticProvider
    class Provider(SemanticProvider):
        def __init__(self):super().__init__('still_bad' if mode=='still_bad' else 'pass');self.binding_calls=0
        async def request(self,config,messages,evidence=None):
            payload=json.loads(messages[1]['content'][0]['text'])
            if 'local_acceptance_reference_missing' in json.dumps(payload.get('findings',{})):
                evidence.before_send(config);self.received.append(copy.deepcopy(messages));self.binding_calls+=1
                value=['A1','A99'] if mode=='invalid_patch' else ['A1']
                result=dict(rows=[dict(row_key='R1',updates=[dict(path='detail_contract.interactions.I1.acceptance_refs',value=value)],new_objects=[])])
                evidence.response(dumps(result),http_status=200,finish_reason='stop',usage={'total_tokens':100})
                return result,dict(finish_reason='stop',usage={'total_tokens':100})
            result,meta=await super().request(config,messages,evidence)
            header=json.loads(messages[0]['content'].split('可信任务头：',1)[1])
            if header.get('response_protocol')==PROTOCOL and not header.get('grounding_protocol'):
                result['requirements'][0]['detail_contract']['interactions'][0]['acceptance_refs']=['A1','A2']
                evidence.response(dumps(result),http_status=200,finish_reason='stop',usage={'total_tokens':100})
            return result,meta
    async def run():
        store,p=project(tmp_path);provider=Provider();workflow=Workflow(store,provider)
        task=workflow.start(p['id'],p['revision'],'clarify','',discussion_target=target(),config_override=dict(workflow.config('clarify'),max_calls=6))
        await workflow.tasks[task['id']]
        saved=store.get_record(p['id'],task['id'],'run');report=saved['business_draft_review']
        assert provider.binding_calls==1
        if mode=='invalid_patch':assert saved['calls']==3 and provider.review_count==0 and report['detail_preflight']['status']=='failed'
        else:
            assert saved['calls']==(6 if mode=='still_bad' else 4)
            assert report['detail_semantic_reviews']['R1']['status']==('failed' if mode=='still_bad' else 'passed')
    asyncio.run(run())
