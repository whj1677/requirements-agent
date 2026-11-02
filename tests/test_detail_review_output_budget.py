"""Frozen auxiliary output allocation; synthetic transport, no live model."""
import asyncio
import copy
import json
import pytest
from app.workflow import Workflow
from app.provider import DEFAULT
from app.core import Problem,digest
from tests.test_detail_semantic_workflow import SemanticProvider
from tests.test_requirement_recommendation import project,target


@pytest.mark.parametrize('review_limit',[8192,48000,65536])
@pytest.mark.parametrize('legacy',[False,True])
def test_review_budget_is_role_scoped_frozen_and_visible_in_evidence(tmp_path,review_limit,legacy):
    async def run():
        store,p=project(tmp_path);store.setting('model',dict(DEFAULT,review_max_tokens=review_limit))
        configs=[]
        class Capture(SemanticProvider):
            async def request(self,config,messages,evidence=None):
                configs.append(copy.deepcopy(config))
                if len(configs)==1:store.setting('model',dict(DEFAULT,review_max_tokens=131072))
                return await super().request(config,messages,evidence)
        provider=Capture('correct');workflow=Workflow(store,provider)
        config=dict(workflow.config('clarify'),max_calls=4)
        if legacy:config['detail_review_options'].pop('max_tokens')
        frozen=copy.deepcopy(config)
        task=workflow.start(p['id'],p['revision'],'clarify','',discussion_target=target(),config_override=config)
        await workflow.tasks[task['id']]
        saved=store.get_record(p['id'],task['id'],'run')
        expected=48000 if legacy else review_limit
        assert [c['max_tokens'] for c in configs]==[48000,expected,48000,expected]
        assert [a['max_tokens'] for a in saved['attempts']]==[48000,expected,48000,expected]
        assert saved['business_draft_review']['detail_semantic_reviews']['R1']['status']=='passed'
        assert config==frozen
        for index in (1,3):
            effective=configs[index]
            assert effective['reasoning_effort']=='high' and effective['timeout']==300
            record=json.loads((store.folder/'evidence/model-calls'/(saved['attempts'][index]['call_id']+'.json')).read_text('utf-8'))
            assert record['max_tokens']==expected and record['authorization']['effective_config']['max_tokens']==expected
            assert record['authorization']['effective_config_hash']==digest({k:v for k,v in effective.items() if k!='proxy'})
    asyncio.run(run())


def test_plan_freezes_role_output_and_changed_setting_invalidates_plan(tmp_path):
    from app.actions import Actions
    store,p=project(tmp_path);provider=SemanticProvider();workflow=Workflow(store,provider);actions=Actions(store,workflow)
    store.setting('model',dict(DEFAULT,review_max_tokens=8192))
    body=dict(expected_revision=p['revision'],action='clarify',message='',document_type='prd',target=target(),max_calls=4)
    snapshots={};plan=actions.plan(p['id'],body,config_snapshot=snapshots)
    assert plan['detail_review_options']['clarify']['max_tokens']==8192
    assert snapshots['clarify']['detail_review_options']['max_tokens']==8192
    store.setting('model',dict(DEFAULT,review_max_tokens=65536))
    with pytest.raises(Problem) as caught:actions.start(p['id'],body,plan['plan_hash'],'changed-review-output')
    assert caught.value.code=='STALE_PLAN' and not provider.received


def test_approved_ceiling_still_limits_auxiliary_output(tmp_path):
    async def run():
        store,p=project(tmp_path);seen=[]
        class Capture(SemanticProvider):
            async def request(self,config,messages,evidence=None):
                seen.append(config['max_tokens']);return await super().request(config,messages,evidence)
        provider=Capture();workflow=Workflow(store,provider)
        config=dict(workflow.config('clarify'),max_calls=2,retry_max_tokens=48000)
        task=workflow.start(p['id'],p['revision'],'clarify','',discussion_target=target(),config_override=config)
        await workflow.tasks[task['id']]
        saved=store.get_record(p['id'],task['id'],'run')
        assert saved['authorization']['approved_max_tokens']==48000 and seen==[48000,48000]
        assert saved['business_draft_review']['detail_semantic_reviews']['R1']['status']=='passed'
    asyncio.run(run())


@pytest.mark.parametrize('tamper',['actual_limit','role_limit'])
def test_changed_review_limit_is_rejected_before_outbound_reservation(tmp_path,tamper):
    async def run():
        store,p=project(tmp_path);store.setting('model',dict(DEFAULT,review_max_tokens=8192))
        class Tamper(SemanticProvider):
            async def request(self,config,messages,evidence=None):
                if self.received:
                    config=copy.deepcopy(config)
                    if tamper=='actual_limit':config['max_tokens']=65536
                    else:config['detail_review_options']['max_tokens']=65536
                return await super().request(config,messages,evidence)
        provider=Tamper();workflow=Workflow(store,provider)
        task=workflow.start(p['id'],p['revision'],'clarify','',discussion_target=target(),
            config_override=dict(workflow.config('clarify'),max_calls=4))
        await workflow.tasks[task['id']]
        saved=store.get_record(p['id'],task['id'],'run')
        assert saved['calls']==1 and len(provider.received)==1
        assert saved['attempts'][-1]['validation_result']=='BUDGET_AUTH_CHANGED'
    asyncio.run(run())
