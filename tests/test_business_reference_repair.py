import asyncio
import copy
import json

import pytest

from app.business_reference_repair import prepare, apply
from app.core import Problem


def case():
    value = {'statement': {'text': '仅只读查看，不写回。', 'sources': [
        {'source': 'L1', 'locator': 'L1'}, {'source': 'S1', 'locator': 'L1'}]}}
    excerpts = [{'source_id': 'SRC-real', 'id': 'EX-real', 'text': '仅只读查看，不写回。'}]
    return value, prepare(value, excerpts)


def test_patch_preserves_business_text_and_valid_references():
    value, plan = case(); before = copy.deepcopy(value)
    fixed = apply(plan, {'references': [{'slot': 0, 'source': 'S1', 'locator': 'L1'}]})
    assert value == before
    assert fixed['statement']['text'] == before['statement']['text']
    assert fixed['statement']['sources'][1] == before['statement']['sources'][1]
    assert fixed['statement']['sources'][0] == fixed['statement']['sources'][1]
    assert prepare(fixed, [{'source_id': 'SRC-real', 'id': 'EX-real', 'text': '只读'}]) is None


@pytest.mark.parametrize('fault', ['unknown', 'missing', 'duplicate', 'new_slot', 'business_edit'])
def test_patch_cannot_guess_or_rewrite(fault):
    _, plan = case(); patch = {'references': [{'slot': 0, 'source': 'S1', 'locator': 'L1'}]}
    if fault == 'unknown': patch['references'][0]['source'] = 'S99'
    if fault == 'missing': patch['references'] = []
    if fault == 'duplicate': patch['references'] *= 2
    if fault == 'new_slot': patch['references'][0]['slot'] = 1
    if fault == 'business_edit': patch['statement'] = '可以写回'
    with pytest.raises(Problem): apply(plan, patch)


@pytest.mark.parametrize('invalid', [False, True])
def test_workflow_repairs_once_and_leaves_confirmation_to_user(tmp_path, invalid):
    from app.actions import Actions
    from app.store import Store
    from app.workflow import Workflow
    from tests.check_prewrite_batch_browser import BatchProvider, synthetic

    class BrokenSource(BatchProvider):
        async def request(self, config, messages, evidence=None):
            if not self.requests:
                value, meta = await super().request(config, messages, evidence)
                value['requirements'][0]['statement']['sources'][0]['source'] = 'L1'
                evidence.response(json.dumps(value), http_status=200, finish_reason='stop', usage={})
                return value, meta
            self.requests.append({'stage': 'repair'})
            plan = json.loads(messages[1]['content'][0]['text'])
            pair = plan['allowed'][0]
            patch = {'references': [{'slot': i, 'source': 'S99' if invalid else pair['source'],
                                     'locator': pair['locator']} for i in range(len(plan['slots']))]}
            evidence.before_send(config)
            evidence.response(json.dumps(patch), http_status=200, finish_reason='stop', usage={})
            return patch, {'finish_reason': 'stop'}

    async def run():
        store = Store(tmp_path); before = synthetic(store); provider = BrokenSource(tmp_path)
        provider.project = lambda: store.get(before['id'])
        actions = Actions(store, Workflow(store, provider))
        body = dict(expected_revision=before['revision'], action='organize', message='整批起草',
                    document_type='prd', draft_request={'mode': 'prewrite'})
        plan = actions.plan(before['id'], body)
        task = actions.start(before['id'], body, plan['plan_hash'], 'reference-repair-test', True)
        await actions.jobs[task['id']]
        task = store.get_record(before['id'], task['id'], 'user_task'); after = store.get(before['id'])
        assert len(provider.requests) == 2 and task['calls'] == 2
        assert task['status'] in (('failed',) if invalid else ('partial', 'succeeded')), task
        assert after['questions'] == before['questions'] and after['documents'] == before['documents']
        assert after['items'][:len(before['items'])] == before['items']
        if not invalid: assert after['refinement_completion']['complete']
    asyncio.run(run())
