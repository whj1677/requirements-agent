"""Quality mode is authorized before execution and never raises user budgets."""
from app.provider import DEFAULT, Provider
from app.store import Store
from app.workflow import Workflow


def test_review_uses_thinking_without_mutating_saved_settings_or_budgets(tmp_path):
    store=Store(tmp_path)
    settings=dict(DEFAULT,thinking_disabled=True,max_tokens=1234,max_calls=2,timeout=17)
    store.setting('model',settings)
    workflow=Workflow(store,Provider())
    review=workflow.config('review')
    assert review['thinking_disabled'] is False
    for key in ('max_tokens','max_calls','timeout','base_url','model'):
        assert review[key]==settings[key]
    assert workflow.config('prd')['thinking_disabled'] is True
    assert workflow.config('clarify')['thinking_disabled'] is True
    assert store.setting('model')==settings


def test_explicit_optout_and_other_providers_keep_their_config(tmp_path):
    store=Store(tmp_path)
    workflow=Workflow(store,Provider())
    for config in (dict(DEFAULT,review_thinking=False),
                   dict(DEFAULT,base_url='https://synthetic-provider.example',review_thinking=True)):
        store.setting('model',config)
        assert workflow.config('review')==config

    opted_out=dict(DEFAULT,thinking_disabled=False,review_thinking=False)
    store.setting('model',opted_out)
    assert workflow.config('review')['thinking_disabled'] is True
    assert workflow.config('prd')['thinking_disabled'] is False
    assert store.setting('model')==opted_out


def test_truncated_review_pauses_once_and_keeps_previous_result(tmp_path):
    import asyncio
    import copy
    from app.core import Problem
    from tests.helpers import prepared
    from tests.product_flow_helpers import check_prepared
    store=Store(tmp_path)
    p=prepared(store)
    p=check_prepared(store,p['id'],through=3)
    with store.edit(p['id'],p['revision'],'Synthetic review authorization',bump=False) as (draft,_):
        draft['grants']['https://api.deepseek.com']={'source_ids':['SRC-0001']}
    p=store.get(p['id'])
    old=copy.deepcopy(p['review'])
    class Truncated:
        calls=0
        def key(self,config):return 'synthetic-test-key'
        async def request(self,config,messages,evidence=None):
            self.calls+=1
            evidence.response('{"stage":"review",',http_status=200,finish_reason='length')
            raise Problem('OUTPUT_TRUNCATED','Synthetic approved token limit')
    provider=Truncated()
    workflow=Workflow(store,provider)
    async def run():
        started=workflow.start(p['id'],p['revision'],'review','')
        await workflow.tasks[started['id']]
        return store.get_record(p['id'],started['id'],'run')
    result=asyncio.run(run())
    assert result['status']=='paused_budget' and result['error']=='BUDGET_EXHAUSTED'
    assert provider.calls==1 and result['calls']==1
    assert store.get(p['id'])['review']==old
