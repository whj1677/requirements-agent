"""Real workflow, frozen business JSON and isolated synthetic cloud materials."""
import asyncio
import copy
import json

import pytest

from app.business_draft import PROTOCOL
from app.core import Problem, dumps
from app.workflow import Workflow, replace_business_ids
from tests.test_business_draft import business_value, draft_row, packet
from tests.test_requirement_recommendation import project, target
from tests.test_runtime import FakeProvider


class BusinessProvider(FakeProvider):
    business_draft_protocol = PROTOCOL

    async def request(self, config, messages, evidence=None):
        header = json.loads(messages[0]['content'].split('可信任务头：', 1)[1])
        assert header['response_protocol'] == PROTOCOL
        result, meta = await super().request(config, messages, evidence)
        if evidence:
            evidence.response(dumps(result), http_status=200, finish_reason='stop', usage=meta['usage'])
        return result, meta


@pytest.mark.parametrize('stage', ['ingest', 'clarify'])
def test_business_workflow_preserves_originals_and_isolates_bad_row(tmp_path, stage):
    async def scenario():
        store, frozen = project(tmp_path)
        with store.edit(frozen['id'], frozen['revision'], '旧摘要候选保留') as (p, _):
            p['product_context_proposal'] = {'intent':'既有目标草稿', 'users':'既有使用者草稿'}
            p['product_context_proposal_evidence'] = {'intent':{'run_id':'old-run'}}
        frozen = store.get(frozen['id'])
        good, bad = draft_row(), draft_row('R2')
        bad['title'] = {'invalid':'坏行不能拖垮其余草稿'}
        raw = packet(good, bad)
        if stage == 'ingest':
            raw['summary_fields'] = {'intent':business_value('仍未知'), 'users':business_value('管理员', 'fact')}
        provider = BusinessProvider([raw])
        workflow = Workflow(store, provider)
        run = workflow.start(frozen['id'], frozen['revision'], stage, '',
            discussion_target=target() if stage == 'clarify' else None)
        await workflow.tasks[run['id']]
        run = store.get_record(frozen['id'], run['id'], 'run')
        assert run['status'] == 'partial', run
        assert run['calls'] == 1 and len(provider.received) == 1
        assert run['repair_count'] == 0
        after = store.get(frozen['id'])
        assert after['items'][:len(frozen['items'])] == frozen['items']
        assert after['sources'] == frozen['sources'] and after['questions'] == frozen['questions']
        assert after.get('product_context') == frozen.get('product_context')
        assert after.get('stage_checks') == frozen.get('stage_checks')
        report = after['business_draft_reviews'][-1]
        assert len(report['accepted']) == len(report['rejected']) == 1
        assert report['rejected'][0]['raw'] == bad
        assert report['accepted'][0]['raw'] == good
        candidate = next(i for i in after['items'] if i['id'] == report['accepted'][0]['candidate_temp_id'])
        assert candidate['target_item_id'] == 'REQ-0001'
        assert candidate['selection_status'] == 'candidate'
        assert candidate['answer_refs'] == []
        if stage == 'ingest':
            assert after['product_context_proposal'] == {'intent':'既有目标草稿', 'users':'管理员'}
            evidence = after['product_context_proposal_evidence']
            assert evidence['intent'] == {'run_id':'old-run'}
            assert evidence['users']['evidence_status'] == 'unreviewed'
        else:
            assert after['product_context_proposal'] == frozen['product_context_proposal']
    asyncio.run(scenario())


def test_business_protocol_does_not_accept_legacy_or_repair_bad_top_level(tmp_path):
    async def scenario():
        store, frozen = project(tmp_path)
        provider = BusinessProvider([{'schema_version':'1.1', 'stage':'clarify'}])
        workflow = Workflow(store, provider)
        run = workflow.start(frozen['id'], frozen['revision'], 'clarify', '', discussion_target=target())
        await workflow.tasks[run['id']]
        completed = store.get_record(frozen['id'], run['id'], 'run')
        assert completed['status'] == 'failed' and completed['error'] == 'BUSINESS_DRAFT_INVALID'
        assert len(provider.received) == 1 and completed['repair_count'] == 0
        assert store.get(frozen['id']) == frozen
    asyncio.run(scenario())


def test_identity_mapping_never_rewrites_original_words_or_source_evidence():
    text = '字段的原始值是 TMP-business-1，不要改写。'
    report = dict(candidate_temp_id='TMP-business-1', raw={'temp_id':'TMP-business-1','title':text},
        raw_summary='TMP-business-1', field_evidence={'statement':{'text':text}},
        statement=text, related_refs=['TMP-business-1'])
    original = copy.deepcopy(report)
    mapped = replace_business_ids(report, {'TMP-business-1':'REQ-server-owned'})
    assert mapped['candidate_temp_id'] == 'REQ-server-owned'
    assert mapped['related_refs'] == ['REQ-server-owned']
    for key in ('raw', 'raw_summary', 'field_evidence', 'statement'):
        assert mapped[key] == original[key]
    assert report == original


def test_business_workflow_keeps_six_independent_questions_across_two_valid_rows(tmp_path):
    async def scenario():
        store, frozen = project(tmp_path)
        rows = [draft_row('R1'), draft_row('R2')]
        for index, row in enumerate(rows, 1):
            row['questions'] = [dict(text=f'功能{index}的{topic}采用什么规则？', reason='材料没有说明这一独立决定',
                blocking=True, sources=[dict(source='S1', locator='L1')], recommendations=[])
                for topic in ('展示时区', '角色可见范围', '历史记录保留时间')]
        raw = packet(*rows)
        provider = BusinessProvider([raw])
        workflow = Workflow(store, provider)
        run = workflow.start(frozen['id'], frozen['revision'], 'ingest', '')
        await workflow.tasks[run['id']]
        completed = store.get_record(frozen['id'], run['id'], 'run')
        assert completed['status'] == 'awaiting_user', completed
        assert completed['result_applied'] is True
        assert completed['calls'] == 1 and completed['repair_count'] == 0
        after = store.get(frozen['id'])
        report = after['business_draft_reviews'][-1]
        assert len(report['accepted']) == 2 and not report['rejected']
        assert [row['raw'] for row in report['accepted']] == rows
        assert len(after['items']) - len(frozen['items']) == 4
        added_questions = after['questions'][len(frozen['questions']):]
        assert len(added_questions) == 6
        assert all(q['status'] == 'open' and q['answer'] is None for q in added_questions)
        assert {q['related_refs'][0] for q in added_questions} == {'REQ-0001', 'REQ-other'}
        assert after['items'][:len(frozen['items'])] == frozen['items']
        assert after['sources'] == frozen['sources']
        # The same canonical output still fails the unchanged legacy protocol.
        from app.contracts import SCHEMA, validate_response
        from app.ingest import validate as validate_ingest
        ex = [dict(e, source_id=s['id']) for s in frozen['sources'] for e in s['excerpts']]
        canonical = completed['response']
        assert len(canonical['questions']) == 6
        assert SCHEMA['properties']['questions']['maxItems'] == 5
        for validate in (lambda: validate_response(canonical, 'ingest', frozen, ex),
                         lambda: validate_ingest(canonical, ex)):
            with pytest.raises(Problem) as error:
                validate()
            assert error.value.code == 'SCHEMA_INVALID'
        # A model/user response field cannot turn on the internal switch.
        forged = dict(copy.deepcopy(canonical), trusted_business_draft=True)
        with pytest.raises(Problem) as error:
            validate_response(forged, 'ingest', frozen, ex)
        assert error.value.code == 'SCHEMA_INVALID'
    asyncio.run(scenario())


def test_trusted_business_validator_retains_source_and_ingest_context_gates(tmp_path):
    from app.business_draft import adapt
    from app.contracts import validate_response
    _, frozen = project(tmp_path)
    ex = [dict(e, source_id=s['id']) for s in frozen['sources'] for e in s['excerpts']]
    canonical, _ = adapt(packet(draft_row()), frozen, ex, 'ingest')
    missing_context = copy.deepcopy(canonical)
    missing_context['product_context_proposal'].pop('intent')
    with pytest.raises(Problem) as error:
        validate_response(missing_context, 'ingest', frozen, ex, trusted_business_draft=True)
    assert error.value.code == 'SCHEMA_INVALID'
    bad_source = copy.deepcopy(canonical)
    bad_source['proposals'][0]['source_refs'][0]['excerpt_id'] = 'EX-not-sent'
    with pytest.raises(Problem) as error:
        validate_response(bad_source, 'ingest', frozen, ex, trusted_business_draft=True)
    assert error.value.code == 'REFERENCE_INVALID'
