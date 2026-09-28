"""Offline behavior tests for approved escalation, context and cancellation."""
import asyncio
import copy
import json

import pytest

from app.budgets import check_context, stage_config
from app.core import Problem, digest, dumps
from app.model_evidence import ModelCallEvidence
from app.provider import DEFAULT, assemble
from app.store import Store
from app.workflow import Workflow
from tests.helpers import EXAMPLES, prepared
from tests.product_flow_helpers import check_prepared


def review_setup(tmp_path, provider, **settings):
    store=Store(tmp_path)
    p=prepared(store)
    p=check_prepared(store,p['id'],through=3)
    with store.edit(p['id'],p['revision'],'Synthetic permission',bump=False) as (p,_):
        p['grants']['https://api.deepseek.com']={'source_ids':['SRC-0001']}
    store.setting('model',dict(DEFAULT,**settings))
    return store,store.get(p['id']),Workflow(store,provider)


class Responses:
    def __init__(self, values):
        self.values=iter(values);self.requests=[]
    def key(self,config):return 'synthetic-key'
    async def request(self,config,messages,evidence=None):
        self.requests.append((copy.deepcopy(config),copy.deepcopy(messages)))
        result=next(self.values)
        if isinstance(result,Problem):
            evidence.response('{"stage":"review",',http_status=200,finish_reason='length',
                usage={'completion_tokens':config['max_tokens'],'completion_tokens_details':{'reasoning_tokens':config['max_tokens']-1}})
            raise result
        evidence.response(json.dumps(result),http_status=200,finish_reason='stop')
        return copy.deepcopy(result),{'finish_reason':'stop','usage':{'completion_tokens':100,'prompt_tokens':100}}


def execute(store,p,workflow):
    async def run():
        r=workflow.start(p['id'],p['revision'],'review','')
        await workflow.tasks[r['id']]
        return store.get_record(p['id'],r['id'],'run')
    return asyncio.run(run())


def test_runtime_policy_is_unlimited_and_does_not_rewrite_settings(tmp_path):
    store,p,w=review_setup(tmp_path,Responses([]),max_tokens=16000,context_chars=180000)
    before=store.setting('model')
    config=w.config('review')
    assert config['budget_mode']=='unlimited' and config['max_calls'] is None
    assert (config['max_tokens'],config['retry_max_tokens'])==(65536,393216)
    assert config['timeout']==300 and config['action_seconds']==900
    assert w.config('prd')['max_tokens']==16000
    assert w.config('prd')['max_calls'] is None and w.config('prd')['budget_mode']=='unlimited'
    assert store.setting('model')==before
    messages,_,omitted=assemble(p,'review','',config,tmp_path)
    check_context(messages,config)
    assert not omitted


def test_fixed_or_unknown_provider_never_raises_output_budget():
    for config in (dict(DEFAULT,budget_mode='fixed',max_tokens=1000),
                   dict(DEFAULT,base_url='https://other.example',max_tokens=1000)):
        actual=stage_config(config,'review')
        assert actual['max_tokens']==1000 and 'retry_max_tokens' not in actual


def test_fixed_mode_keeps_a_source_that_fits_the_existing_assembly_budget(tmp_path):
    p=prepared(Store(tmp_path))
    p['sources'][0]['excerpts'][0]['text']='来源原文'*5000
    config=dict(DEFAULT,budget_mode='fixed',max_tokens=1000,context_chars=2000000)
    messages,excerpts,_=assemble(p,'clarify','',config,tmp_path)
    context=json.loads(messages[1]['content'][0]['text'])
    context.pop('excerpts');context.pop('omitted_excerpt_ids')
    config['context_chars']=max(len(messages[0]['content'])+len(dumps(context))+4000+4000+sum(len(dumps(ex)) for ex in excerpts),
                                len(dumps(messages))+config['max_tokens']*4)+1
    messages,selected,omitted=assemble(p,'clarify','',config,tmp_path)
    assert selected==excerpts and not omitted
    check_context(messages,config)


def test_truncated_review_retries_same_input_once_with_frozen_ceiling(tmp_path):
    provider=Responses([Problem('OUTPUT_TRUNCATED','synthetic'),EXAMPLES['review']])
    store,p,w=review_setup(tmp_path,provider)
    result=execute(store,p,w)
    assert result['status'] in ('succeeded','partial') and result['result_applied']
    assert [c['max_tokens'] for c,_ in provider.requests]==[65536,393216]
    assert provider.requests[0][1]==provider.requests[1][1]
    assert result['authorization']['approved_max_tokens']==393216
    assert result['authorization']['initial_max_tokens']==65536
    assert result['attempts'][1]['repair_of']==result['attempts'][0]['call_id']
    entries=[json.loads((tmp_path/'evidence/model-calls'/(a['call_id']+'.json')).read_text('utf8')) for a in result['attempts']]
    assert [e['max_tokens'] for e in entries]==[65536,393216]
    assert entries[0]['usage']['completion_tokens_details']['reasoning_tokens']==65535
    assert entries[0]['actual_input_hash']==entries[1]['actual_input_hash']


@pytest.mark.parametrize('legacy_max_calls',[8,1])
def test_repeated_truncation_is_bounded_and_never_replaces_previous_review(tmp_path,legacy_max_calls):
    provider=Responses([Problem('OUTPUT_TRUNCATED','synthetic')]*3)
    store,p,w=review_setup(tmp_path,provider,max_calls=legacy_max_calls)
    old=copy.deepcopy(p['review'])
    result=execute(store,p,w)
    assert result['status']=='failed' and result['error']=='OUTPUT_TRUNCATED'
    assert len(provider.requests)==result['calls']==2
    assert [c['max_tokens'] for c,_ in provider.requests]==[65536,393216]
    assert store.get(p['id'])['review']==old


def test_no_escalation_for_nontruncation_error(tmp_path):
    provider=Responses([Problem('AUTH_FAILED','synthetic')])
    store,p,w=review_setup(tmp_path,provider)
    result=execute(store,p,w)
    assert result['error']=='AUTH_FAILED' and len(provider.requests)==1


def test_legacy_character_limit_does_not_omit_large_source(tmp_path):
    provider=Responses([])
    store=Store(tmp_path)
    p=prepared(store)
    p['sources'][0]['excerpts'][0]['text']='完整来源原文必须保留。'*50000
    store.setting('model',dict(DEFAULT,context_chars=10000))
    w=Workflow(store,provider)
    config=w.config('clarify')
    messages,selected,omitted=assemble(p,'clarify','',config,tmp_path)
    assert config['context_chars']==10000 and config['budget_mode']=='unlimited'
    assert selected==p['sources'][0]['excerpts'] and not omitted
    check_context(messages,config)
    assert not provider.requests


def test_cancel_stops_waiting_and_records_inflight_attempt(tmp_path):
    class Waiting:
        def key(self,config):return 'synthetic-key'
        async def request(self,config,messages,evidence=None):
            await asyncio.sleep(100)
    store,p,w=review_setup(tmp_path,Waiting())
    async def run():
        started=w.start(p['id'],p['revision'],'review','')
        task=w.tasks[started['id']]
        await asyncio.sleep(.04)
        w.cancel(p['id'],started['id'])
        await asyncio.wait_for(task,1)
        return store.get_record(p['id'],started['id'],'run')
    result=asyncio.run(run())
    assert result['status']=='cancelled' and result['calls']==1
    assert result['attempts'][0]['validation_result']=='CANCELLED'
    assert store.get(p['id'])['review']==p['review']


def test_larger_final_output_and_reasoning_counts_are_preserved_without_hidden_reasoning(tmp_path):
    record=ModelCallEvidence(tmp_path,'CALL-test','RUN-test','review','synthetic','https://example.com',None,'synthetic-key')
    record.request_context(dict(DEFAULT,max_tokens=131072),[],{}, {})
    content='业务条款。'*25000
    record.response(content,usage={'completion_tokens':40132,'completion_tokens_details':{'reasoning_tokens':36594,'private':'omit'},'reasoning_content':'never persist'})
    record.finish('accepted')
    saved=json.loads(record.path.read_text('utf8'))
    assert saved['final_output']==content and 'final_output_truncated' not in saved['evidence_limitations']
    assert saved['usage']['completion_tokens_details']=={'reasoning_tokens':36594}
    assert 'never persist' not in record.path.read_text('utf8') and 'private' not in record.path.read_text('utf8')
