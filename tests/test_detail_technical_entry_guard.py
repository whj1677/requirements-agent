"""Shared deterministic gate, not a new business completeness policy."""
import asyncio
import copy
import json
import jsonschema
import pytest
from app import detail_draft,detail_semantics,detail_preflight
from app.requirement_details import technical_issues,detail_issues,DetailProblem
from app.business_draft import adapt,PROTOCOL
from app.core import dumps
from app.workflow import Workflow
from tests.test_detail_semantics import fixture
from tests.test_detail_semantic_workflow import generated_detail
from tests.test_business_draft import draft_row,packet
from tests.test_requirement_recommendation import project,target
from tests.test_runtime import FakeProvider


def test_orphan_optional_proof_is_rejected_with_exact_path_without_moving_or_dropping_it(tmp_path):
    p,scope,excerpts,raw,*_=fixture(tmp_path)
    raw['requirements'][0]['detail_contract']=generated_detail()
    field=raw['requirements'][0]['detail_contract']['fields'][0]
    field['default_value']='空（未释放）';field['evidence']['empty_value']=dict(status='source',sources=[dict(source='S1',locator='L1')])
    before=copy.deepcopy(raw);canonical,report=adapt(raw,p,excerpts,'clarify',scope)
    assert not canonical['proposals']
    rejected=report['rejected'][0]
    assert rejected['code']=='BUSINESS_DRAFT_DETAIL_TECHNICAL'
    assert rejected['issues']==[dict(code='DETAIL_EVIDENCE_PATH',path='detail_contract.fields.F1.evidence.empty_value',message='属性依据指向不存在的业务属性：empty_value')]
    assert raw==before and rejected['raw']==raw['requirements'][0]


def test_semantic_prepare_independently_rejects_stale_technically_invalid_candidate(tmp_path):
    p,scope,excerpts,raw,canonical,report,_=fixture(tmp_path)
    candidate=next(c for c in canonical['proposals'] if c.get('detail_contract'))
    candidate['detail_contract']['fields'][0].setdefault('evidence',{})['empty_value']=dict(status='proposal')
    before=copy.deepcopy(canonical)
    with pytest.raises(DetailProblem) as caught:detail_semantics.prepare(raw,canonical,report,excerpts)
    assert caught.value.code=='DETAIL_REVIEW_TECHNICAL_INVALID'
    assert caught.value.issues[0]['path']=='detail_contract.fields.F1.evidence.empty_value'
    assert canonical==before and not detail_preflight._accepted_keys(report,canonical)


@pytest.mark.parametrize('section,name',[('fields','empty_value'),('fields','precision'),('interactions','acceptance_refs'),('flow.nodes','interaction_id'),('flow.edges','acceptance_refs')])
def test_generation_schema_requires_actual_optional_property_when_proof_present(section,name):
    schema=detail_draft.model_schema(generation=True)
    item=(schema['properties']['flow']['properties'][section.split('.')[1]] if section.startswith('flow.') else schema['properties'][section])['items']
    conditions=[c for c in item['allOf'] if c.get('then',{}).get('required')==[name]]
    assert len(conditions)==1
    condition=conditions[0]
    assert condition['if']['required']==['evidence']
    assert not list(jsonschema.Draft202012Validator(condition).iter_errors({}))
    assert list(jsonschema.Draft202012Validator(condition).iter_errors(dict(evidence={name:dict(status='proposal')})))
    # Presence is not truthiness. Existing per-property types still apply in
    # the full schema; this dependency does not invent a new emptiness policy.
    for value in ('',[],False,0):
        assert not list(jsonschema.Draft202012Validator(condition).iter_errors(dict(evidence={name:dict(status='proposal')},**{name:value})))


def test_wire_generation_schema_rejects_orphan_but_accepts_real_optional_property():
    value=generated_detail();field=value['fields'][0]
    schema=detail_draft.model_schema(generation=True);jsonschema.validate(value,schema)
    field['evidence']['empty_value']=dict(status='proposal')
    assert list(jsonschema.Draft202012Validator(schema).iter_errors(value))
    field['empty_value']='空'
    jsonschema.validate(value,schema)
    assert not technical_issues(dict(detail_contract=value))


def test_sparse_unknown_proposal_and_unadopted_clause_states_keep_existing_boundaries():
    value=generated_detail();field=value['fields'][0]
    field['default_value']='';field['evidence']['default_value']=dict(status='unknown',reason='未明确')
    field['empty_value']='';field['evidence']['empty_value']=dict(status='proposal')
    value['interactions'][0]['acceptance_refs']=['AC-pending']
    candidate=dict(detail_contract=value)
    assert technical_issues(candidate)==[]
    assert any(i['code']=='DETAIL_PROPERTY_MISSING' for i in detail_issues(candidate))
    assert any(i['code']=='DETAIL_ITEM_REFERENCE' for i in detail_issues(candidate,project=dict(items=[],sources=[])))
    legacy=detail_draft.model_schema(generation=False)
    assert 'allOf' not in legacy['properties']['fields']['items']
    sparse=dict(schema_version=1,applicability={s:dict(applicable=False,reason='本期无此模块') for s in ('fields','interactions','flow','state_transitions')},
        fields=[],interactions=[],state_transitions=[],flow=dict(nodes=[],edges=[]))
    jsonschema.validate(sparse,legacy)
    assert technical_issues(dict(detail_contract=sparse))==[]


def test_shared_technical_filter_preserves_old_exact_classification(tmp_path):
    *_,plan=fixture(tmp_path);detail=plan['rows'][0]['detail_contract']
    detail['fields'][0]['evidence']={'empty_value':dict(status='proposal')}
    detail['fields'][0]['default_value']=''
    original_structural={'DETAIL_STRUCTURE','DETAIL_DUPLICATE_ID','DETAIL_EVIDENCE_PATH','DETAIL_FIELD_REFERENCE','DETAIL_FIELD_CYCLE',
        'DETAIL_INTERACTION_REFERENCE','DETAIL_APPLICABILITY_CONFLICT','DETAIL_SECTION_EMPTY','DETAIL_SOURCE_MISSING'}
    all_issues=detail_issues(dict(detail_contract=detail))
    assert technical_issues(dict(detail_contract=detail))==[i for i in all_issues if i['code'] in original_structural or i['code'].startswith('DETAIL_FLOW_')]


class OrphanProvider(FakeProvider):
    business_draft_protocol=PROTOCOL
    def __init__(self):super().__init__([])
    async def request(self,config,messages,evidence=None):
        evidence.before_send(config);self.received.append(copy.deepcopy(messages))
        header=json.loads(messages[0]['content'].split('可信任务头：',1)[1])
        if header.get('grounding_protocol'):
            payload=json.loads(messages[1]['content'][0]['text'])
            assert all(f['path'][0]=='requirements' for f in payload['fields'])
            result=dict(candidate_hash=payload['candidate_hash'],input_hash=payload['input_hash'],
                checked_field_ids=[f['id'] for f in payload['fields']],findings=[])
            evidence.response(dumps(result),http_status=200,finish_reason='stop',usage={'total_tokens':100})
            return result,dict(finish_reason='stop',usage={'total_tokens':100})
        assert len(self.received)==1,'Invalid detail must not enter a semantic model call or gain another repair'
        row=draft_row();row['detail_contract']=generated_detail()
        row['detail_contract']['fields'][0]['evidence']['empty_value']=dict(status='source',sources=[dict(source='S1',locator='L1')])
        result=packet(row);evidence.response(dumps(result),http_status=200,finish_reason='stop',usage={'total_tokens':100})
        return result,dict(finish_reason='stop',usage={'total_tokens':100},response_model='OFFLINE_PROTOCOL_FIXTURE')


def test_workflow_cannot_mark_orphan_proof_semantically_passed_or_adopt_it(tmp_path):
    async def run():
        store,p=project(tmp_path);provider=OrphanProvider();workflow=Workflow(store,provider)
        task=workflow.start(p['id'],p['revision'],'clarify','',discussion_target=target(),config_override=dict(workflow.config('clarify'),max_calls=7))
        await workflow.tasks[task['id']]
        saved=store.get_record(p['id'],task['id'],'run')
        assert saved['calls']==len(provider.received)==2
        report=saved['business_draft_review']
        assert report['rejected'][0]['issues'][0]['code']=='DETAIL_EVIDENCE_PATH'
        assert not report.get('detail_semantic_reviews')
        assert not any(x.get('detail_contract') for x in saved.get('response',{}).get('proposals',[]))
        record=json.loads((store.folder/'evidence/model-calls'/(saved['attempts'][0]['call_id']+'.json')).read_text('utf-8'))
        assert record['business_draft_review']['rejected'][0]['issues'][0]['path']=='detail_contract.fields.F1.evidence.empty_value'
        assert 'empty_value' in json.loads(record['final_output'])['requirements'][0]['detail_contract']['fields'][0]['evidence']
        assert store.get(p['id'])['items']==p['items']
    asyncio.run(run())


def test_real_provider_preserves_presence_dependency_in_responses_request_schema(tmp_path,monkeypatch):
    import httpx
    from app.provider import Provider,DEFAULT,origin
    from tests.provider_transport import reply
    bodies=[];real_client=httpx.AsyncClient;fake=OrphanProvider()
    class NoEvidence:
        def before_send(self,*args):pass
        def response(self,*args,**kwargs):pass
    async def respond(request):
        body=json.loads(request.content);bodies.append(body)
        result,_=await fake.request({},body.get('messages',body.get('input')),NoEvidence())
        return reply(request,json.dumps(result,ensure_ascii=False))
    monkeypatch.setattr('app.provider.httpx.AsyncClient',lambda **kw:real_client(**kw,transport=httpx.MockTransport(respond)))
    async def run():
        store,p=project(tmp_path);provider=Provider(env_path=tmp_path/'absent.env');provider.keys[origin(DEFAULT)]='synthetic-key'
        with store.edit(p['id'],p['revision'],'enable isolated generation detail') as (draft,_):draft['items'][0]['detail_required']=True
        p=store.get(p['id'])
        workflow=Workflow(store,provider);task=workflow.start(p['id'],p['revision'],'clarify','',discussion_target=target(),config_override=dict(workflow.config('clarify'),max_calls=7))
        await workflow.tasks[task['id']]
        assert len(bodies)==2
        schema=bodies[0]['text']['format']['schema']
        detail=schema['properties']['requirements']['items']['properties']['detail_contract']
        item=detail['properties']['fields']['items']
        dependency=next(c for c in item['allOf'] if c.get('then',{}).get('required')==['empty_value'])
        assert dependency['if']==dict(required=['evidence'],properties=dict(evidence=dict(required=['empty_value'])))
        saved=store.get_record(p['id'],task['id'],'run')
        assert saved['business_draft_review']['rejected'][0]['code']=='BUSINESS_DRAFT_DETAIL_TECHNICAL'
    asyncio.run(run())


def test_role_prompt_separates_guard_and_scenario_without_prescribing_graph_edits(tmp_path):
    *_,plan=fixture(tmp_path)
    for prompt in (detail_draft.PROMPT,detail_semantics.messages(plan)[0]['content']):
        assert '无来源的角色、归属或时点变化' in prompt
        assert '通用鉴权守卫与实际场景路径' in prompt
        assert '原请求确定拒绝及恢复出口' in prompt
        assert '删除E12' not in prompt and 'N11' not in prompt
