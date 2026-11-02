"""Surface an existing adapter rejection in the already bounded correction slot."""
import asyncio
import copy
import json

import pytest

from app import intake_grounding as g
from app.business_draft import SUMMARY_FIELDS, adapt
from app.core import dumps
from app.workflow import Workflow
from tests.test_business_draft import business_value, draft_row, packet, setup
from tests.test_intake_coverage import PairProvider
from tests.test_requirement_recommendation import project


@pytest.mark.parametrize('key,basis,sources,expected', [
    ('new-note', 'recommendation', [], True),
    ('new-note', 'recommendation', [dict(source='S1', locator='L1')], False),
    ('new-note', 'unknown', [], False),
    ('R1', 'recommendation', [], False),
])
def test_only_new_acceptance_without_inheritable_source_is_located(key, basis, sources, expected):
    row = draft_row(key)
    row['acceptance'].append(business_value('建议保留交接备注。', basis, sources))
    row['behavior']['result'] = business_value('建议展示结果。', 'recommendation')
    raw = packet(row)
    before = copy.deepcopy(raw)
    plan = g.prepare(raw, dict(current_rows=[dict(row_key='R1')]))
    findings = g.local_findings(plan)
    assert bool(findings) == expected and raw == before
    if expected:
        assert len(findings) == 1
        finding = findings[0]
        assert finding['path'] == ['requirements', 0, 'acceptance', 1]
        assert next(f for f in plan['fields'] if f['id'] == finding['id'])['text'] == finding['quote']
        assert finding['sources'] == []


def test_existing_row_acceptance_keeps_adapter_context_source_inheritance(tmp_path):
    p, scope, _, _, excerpts, _ = setup(tmp_path)
    row = draft_row('R1')
    row['acceptance'] = [business_value('建议记录保存结果。', 'recommendation')]
    raw = packet(row)
    assert g.local_findings(g.prepare(raw, dict(current_rows=[dict(row_key='R1')]))) == []
    _, report = adapt(raw, p, excerpts, 'clarify', scope)
    assert report['complete'] and not report['rejected']


def test_local_and_both_model_findings_share_patch_then_full_pair_recheck(tmp_path):
    async def scenario():
        store, p = project(tmp_path)
        raw = packet(draft_row('R1'), draft_row('R2'), draft_row('new-note'))
        raw['summary_fields'] = {key: business_value() for key in SUMMARY_FIELDS}
        raw['requirements'][2]['acceptance'].append(business_value('交接备注展示方式尚未确定。', 'recommendation'))

        class RepairProvider(PairProvider):
            async def request(self, config, messages, evidence=None):
                header = json.loads(messages[0]['content'].split('可信任务头：', 1)[1])
                if header.get('grounding_protocol') != g.PATCH_VERSION:
                    return await super().request(config, messages, evidence)
                if evidence:
                    evidence.before_send(config)
                self.received.append(copy.deepcopy(messages))
                payload = json.loads(messages[1]['content'][0]['text'])
                self.patch_findings.append(copy.deepcopy(payload['findings']))
                slot = next(s for s in payload['slots'] if s['path'] == ['requirements', 2, 'acceptance'])
                acceptance = copy.deepcopy(payload['candidate']['requirements'][2]['acceptance'])
                acceptance[-1]['basis'] = 'unknown'
                result = dict(candidate_hash=payload['candidate_hash'], patches=[
                    dict(slot=slot['id'], value=acceptance, reason='保留未知，不补造引用。')])
                meta = dict(usage=dict(prompt_tokens=1, completion_tokens=1), finish_reason='stop')
                if evidence:
                    evidence.response(dumps(result), http_status=200, finish_reason='stop', usage=meta['usage'])
                return result, meta

        provider = RepairProvider(raw, 'repair')
        workflow = Workflow(store, provider)
        run = workflow.start(p['id'], p['revision'], 'ingest', '请整理初稿')
        await workflow.tasks[run['id']]
        saved = store.get_record(p['id'], run['id'], 'run')
        assert saved['intake_grounding']['status'] == 'repaired', saved
        assert saved['calls'] == len(provider.received) == 6
        assert provider.coverages == provider.challenges == 2
        assert {f['kind'] for f in provider.patch_findings[0]} == {
            'coverage_input', 'source_challenge', 'local_acceptance_source'}
        assert saved['result_applied'] and saved['intake_grounding']['local_findings'] == []
        assert store.get(p['id'])['items'][:len(p['items'])] == p['items']
        assert store.get(p['id'])['sources'] == p['sources']
    asyncio.run(scenario())
