"""Read-only correction context and retained deterministic rejection evidence."""
import asyncio
import copy
import json
import pytest
from app import detail_semantics as semantics
from app.business_draft import PROTOCOL,adapt
from app.core import dumps,digest
from app.workflow import Workflow
from tests.test_detail_semantics import fixture,response,model_response
from tests.test_detail_semantic_workflow import generated_detail
from tests.test_business_draft import draft_row,packet
from tests.test_runtime import FakeProvider
from tests.test_requirement_recommendation import project,target


def correction_context(tmp_path):
    *_,excerpts,raw,canonical,report,plan=fixture(tmp_path)
    outcomes={'R1':dict(status='failed',findings=[dict(dimension='actor_paths',
        affected_paths=['detail_contract.flow.edges.E4.from'],traces=[dict(edge_ids=['E1','E2','E4'])])])}
    return raw,plan,outcomes,excerpts


def test_graph_facts_are_exact_frozen_context_and_do_not_expand_write_scope(tmp_path):
    raw,plan,outcomes,excerpts=correction_context(tmp_path)
    other=copy.deepcopy(raw['requirements'][0]);other['row_key']='R-other';raw['requirements'].append(other)
    before=copy.deepcopy((raw,plan,outcomes,excerpts));schema,messages=semantics.correction_messages(raw,dict(plan,correction_protocol=2),outcomes,excerpts)
    payload=json.loads(messages[1]['content'][0]['text']);facts=payload['graph_structure_facts']
    assert set(facts)=={'R1'} and [r['row_key'] for r in payload['rows']]==['R1']
    assert facts['R1']['candidate_hash']==digest(raw['requirements'][0]['detail_contract'])
    node=next(n for n in facts['R1']['nodes'] if n['id']=='N3')
    assert node['kind']=='decision' and node['incoming_edge_ids']==['E2'] and node['outgoing_edge_ids']==['E3','E4']
    assert 'detail_contract.flow.nodes.N3.kind' in node['writable_paths']
    writable=set(payload['write_scopes']['R1']['allowed_write_paths'])
    assert all(set(o['writable_paths'])<=writable for o in facts['R1']['nodes']+facts['R1']['edges'])
    from app.detail_correction import contract
    assert (schema,payload['write_scopes'])==contract(raw,outcomes)
    assert (raw,plan,outcomes,excerpts)==before
    prompt=messages[0]['content']
    assert 'decision至少两个' in prompt and '不得仅为消除结构错误改kind' in prompt
    assert '原请求确定拒绝结果仍须保留' in prompt and '不是业务来源' in facts['R1']['notice']
    assert '若当前来源和失败反例涉及非本人绕过页面直接提交' in prompt


@pytest.mark.parametrize('mode',['missing_kind','not_applicable'])
def test_graph_context_handles_incomplete_kind_and_not_applicable_without_invention(tmp_path,mode):
    raw,plan,outcomes,excerpts=correction_context(tmp_path);detail=raw['requirements'][0]['detail_contract']
    if mode=='missing_kind':detail['flow']['nodes'][0].pop('kind')
    else:
        detail['applicability']['flow']['applicable']=False;detail['flow']=dict(nodes=[],edges=[])
    before=copy.deepcopy(detail)
    _,messages=semantics.correction_messages(raw,dict(plan,correction_protocol=2),outcomes,excerpts)
    facts=json.loads(messages[1]['content'][0]['text'])['graph_structure_facts']['R1']
    if mode=='missing_kind':assert facts['nodes'][0]['kind'] is None
    else:assert facts['flow_applicable'] is False and facts['nodes']==facts['edges']==[]
    assert detail==before


def test_adapt_retains_actual_topology_issue_path_without_changing_rejection(tmp_path):
    p,local,excerpts,raw,*_=fixture(tmp_path)
    next(e for e in raw['requirements'][0]['detail_contract']['flow']['edges'] if e['id']=='E4')['from']='N2'
    before=copy.deepcopy(raw);canonical,report=adapt(raw,p,excerpts,'clarify',local)
    rejection=report['rejected'][0]
    assert rejection['code']=='BUSINESS_DRAFT_DETAIL_FLOW' and not canonical['proposals']
    assert rejection['issues'][0]['code']=='DETAIL_FLOW_BRANCH'
    assert rejection['issues'][0]['path']=='detail_contract.flow.nodes.N3'
    assert set(rejection['issues'][0])=={'code','path','message'}
    assert raw==before and '至少需要两个条件分支' in rejection['reason']


class TopologyFailureProvider(FakeProvider):
    business_draft_protocol=PROTOCOL
    def __init__(self):super().__init__([]);self.patch=None
    async def request(self,config,messages,evidence=None):
        evidence.before_send(config);self.received.append(copy.deepcopy(messages))
        header=json.loads(messages[0]['content'].split('可信任务头：',1)[1]);payload=json.loads(messages[1]['content'][0]['text'])
        if header.get('grounding_protocol'):
            result=dict(candidate_hash=payload['candidate_hash'],input_hash=payload['input_hash'],
                checked_field_ids=[f['id'] for f in payload['fields']],findings=[])
        elif header.get('response_protocol')==PROTOCOL:
            row=draft_row();row['detail_contract']=generated_detail();result=packet(row)
        elif payload.get('rows') and 'required_checks' in payload['rows'][0]:
            expanded=response(payload);check=next(c for c in expanded['rows'][0]['checks'] if c['dimension']=='operation_results')
            prop=next(p for p in payload['rows'][0]['property_refs'].values() if p['path']=='detail_contract.flow.edges.E4.from')
            check['candidate_quotes'].append(dict(path=prop['path'],quote=prop['value']))
            check.update(verdict='contradicted',affected_paths=[prop['path']],correction='离线拓扑失败反例，不是业务修复答案。')
            result=model_response(payload,expanded)
        else:
            assert payload['graph_structure_facts']['R1']['nodes']
            result=dict(rows=[dict(row_key='R1',updates=[dict(path='detail_contract.flow.edges.E4.from',value='N2')],new_objects=[])])
            self.patch=copy.deepcopy(result)
        evidence.response(dumps(result),http_status=200,finish_reason='stop',usage={'total_tokens':100})
        return result,dict(finish_reason='stop',usage={'total_tokens':100},response_model='OFFLINE_PROTOCOL_FIXTURE')


def test_failed_correction_keeps_raw_patch_report_exact_issue_and_pending_reason(tmp_path):
    async def run():
        store,p=project(tmp_path);provider=TopologyFailureProvider();workflow=Workflow(store,provider)
        task=workflow.start(p['id'],p['revision'],'clarify','',discussion_target=dict(target(),detail_required=True),config_override=dict(workflow.config('clarify'),max_calls=7))
        await workflow.tasks[task['id']]
        saved=store.get_record(p['id'],task['id'],'run')
        assert saved['calls']==len(provider.received)==4
        assert saved['request_budget']['used']==4
        headers=[json.loads(m[0]['content'].split('可信任务头：',1)[1]) for m in provider.received]
        assert headers[0]['response_protocol']==PROTOCOL and headers[1]['grounding_protocol']
        assert 'required_checks' in str(json.loads(provider.received[2][1]['content'][0]['text'])['rows'])
        assert 'graph_structure_facts' in json.loads(provider.received[3][1]['content'][0]['text'])
        review=saved['business_draft_review']['detail_semantic_reviews']['R1']
        assert review['status']=='failed' and review['code']=='DETAIL_CORRECTION_INVALID'
        assert 'DETAIL_FLOW_BRANCH detail_contract.flow.nodes.N3' in review['reason']
        record=json.loads((store.folder/'evidence/model-calls'/(saved['attempts'][-1]['call_id']+'.json')).read_text('utf-8'))
        assert json.loads(record['final_output'])==provider.patch
        audit=record['detail_correction_validation']
        assert audit['status']=='rejected' and audit['adapt_report']['rejected'][0]['code']=='BUSINESS_DRAFT_DETAIL_FLOW'
        assert audit['rejection_details'][0]['code']=='DETAIL_FLOW_BRANCH' and audit['rejection_details'][0]['path']=='detail_contract.flow.nodes.N3'
        assert store.get(p['id'])['items'][:len(p['items'])]==p['items']
        assert not any(x.get('detail_contract') for x in saved['response']['proposals'])
    asyncio.run(run())
