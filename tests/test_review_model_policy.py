"""Fixed mode never escalates; function-first mode uses a preapproved ceiling."""
from app.provider import DEFAULT, Provider
from app.store import Store
from app.workflow import Workflow


def test_review_normalizes_legacy_quotas_without_mutating_saved_settings(tmp_path):
    store=Store(tmp_path)
    settings=dict(DEFAULT,budget_mode='fixed',thinking_disabled=True,max_tokens=1234,max_calls=2,timeout=17)
    store.setting('model',settings)
    workflow=Workflow(store,Provider())
    review=workflow.config('review')
    assert review['thinking_disabled'] is False
    assert review['budget_mode']=='unlimited' and review['max_calls'] is None
    assert review['max_tokens']==65536 and review['retry_max_tokens']==393216
    assert review['timeout']==300 and review['action_seconds']==900
    assert review['base_url']==settings['base_url'] and review['model']==settings['model']
    assert workflow.config('prd')['thinking_disabled'] is True
    assert workflow.config('prd')['max_calls'] is None
    assert workflow.config('clarify')['thinking_disabled'] is True
    assert store.setting('model')==settings


def test_review_thinking_optout_and_other_provider_keep_unlimited_execution(tmp_path):
    store=Store(tmp_path)
    workflow=Workflow(store,Provider())
    for config in (dict(DEFAULT,budget_mode='fixed',max_calls=2,review_thinking=False),
                   dict(DEFAULT,base_url='https://synthetic-provider.example',budget_mode='fixed',max_calls=2,review_thinking=True)):
        store.setting('model',config)
        review=workflow.config('review')
        assert review['budget_mode']=='unlimited' and review['max_calls'] is None
        if config['base_url']=='https://api.deepseek.com':
            assert review['thinking_disabled'] is (not config['review_thinking'])
        else:
            assert review['thinking_disabled']==config['thinking_disabled']
        assert review['base_url']==config['base_url'] and review['model']==config['model']
        assert workflow.config('clarify')['max_calls'] is None

    opted_out=dict(DEFAULT,thinking_disabled=False,review_thinking=False)
    store.setting('model',opted_out)
    assert workflow.config('review')['thinking_disabled'] is True
    assert workflow.config('prd')['thinking_disabled'] is False
    assert store.setting('model')==opted_out


def test_repeated_review_truncation_fails_after_one_physical_capacity_escalation(tmp_path):
    import asyncio
    import copy
    from app.core import Problem
    from tests.helpers import prepared
    from tests.product_flow_helpers import check_prepared
    store=Store(tmp_path)
    store.setting('model',dict(DEFAULT,budget_mode='fixed'))
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
    assert result['status']=='failed' and result['error']=='OUTPUT_TRUNCATED'
    assert provider.calls==2 and result['calls']==2
    assert [attempt['max_tokens'] for attempt in result['attempts']]==[65536,393216]
    assert store.get(p['id'])['review']==old
