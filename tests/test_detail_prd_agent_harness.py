"""Offline coverage for the opt-in real-Agent harness; no credentials/network."""
import asyncio
import copy
import hashlib
import json

import pytest

from app.actions import Actions
from app.core import Problem, digest
from app.provider import DEFAULT, Provider, origin
from app.store import Store
from app.workflow import Workflow
from scripts.run_detail_prd_agent_validation import (
    BRIEF, evidence_summary, export_review, prepare_project, review_template,
    run_action, select_reviewed_draft, select_reviewed_with_revisions,
)
from scripts.run_detail_contract_validation import CASES


def detail_fixture():
    return dict(schema_version=1,
        applicability={key: dict(applicable=key != 'state_transitions',
            reason='本例不新增独立生命周期' if key == 'state_transitions' else '')
            for key in ('fields', 'interactions', 'flow', 'state_transitions')},
        fields=[dict(id='F1', name='姓名', meaning='联系人姓名', data_type='text', source='用户输入',
            required='必填', default_value='原姓名', constraints='1至30个字符', visibility='查看者可见',
            editability='管理员可改')],
        interactions=[dict(id='I1', name='保存', actor='管理员', entry='联系人详情', preconditions='有编辑权限',
            trigger='点击保存', inputs='姓名', validation='1至30个字符', pending='禁重复点击',
            repeat='同一提交只执行一次', cancel='提交前可取消', success='显示新姓名',
            failure='保留输入提示失败原因', effects='修改姓名', field_refs=['F1'])],
        flow=dict(nodes=[dict(id='N1', label='开始', kind='start'),
                         dict(id='N2', label='保存姓名', kind='action', actor='管理员', interaction_id='I1'),
                         dict(id='N3', label='完成', kind='end')],
                  edges=[dict(id='E1', **{'from': 'N1', 'to': 'N2'}),
                         dict(id='E2', **{'from': 'N2', 'to': 'N3'})]),
        state_transitions=[])


def candidates(tmp_path):
    store = Store(tmp_path / 'isolated')
    project = prepare_project(store, '合成说明：管理员可以编辑联系人姓名。姓名为1至30个字符。只读角色不得编辑。保存后显示新姓名。')
    source = project['sources'][0]
    refs = [dict(source_id=source['id'], excerpt_id=source['excerpts'][0]['id'])]
    with store.edit(project['id'], project['revision'], '合成测试候选夹具') as (project, _):
        project['items'] = [dict(id='REQ-TEST', kind='requirement', title='联系人姓名维护',
            statement='管理员可以编辑联系人姓名。', applies_to='to_be', epistemic_status='reported',
            selection_status='candidate', source_refs=refs, related_refs=[], change_type='new',
            behavior={}, behavior_review_required=True, detail_required=True, detail_contract=detail_fixture()),
            dict(id='AC-TEST', kind='acceptance', title='保存验收', statement='保存后显示新姓名。',
                 applies_to='to_be', epistemic_status='reported', selection_status='candidate',
                 source_refs=refs, related_refs=['REQ-TEST'])]
        project['questions'] = [dict(id='Q-TEST', question='单次最大数量是多少？', why='合成待决',
            blocking=True, blocking_stage=3, status='open', answer='', source_refs=refs,
            related_refs=['REQ-TEST'], options=[])]
    return store, store.get(project['id'])


def signed_review(project):
    review = review_template(project)
    for item in review['items']:
        item.update(decision='include_in_review_draft', reason='离线合成夹具已核对，仅验证评审投影。')
    return review


class OfflinePlanProvider(Provider):
    def key(self, config):
        return 'offline-fixture-not-a-secret'

    async def request(self, config, messages, evidence=None):
        value = dict(plan_version='4', sections=[dict(section_key='function', context_refs=[],
            normative_refs=['REQ-TEST', 'AC-TEST'], discussion_refs=[], explanations=[])])
        evidence.before_send(config)
        evidence.response(json.dumps(value, ensure_ascii=False), http_status=200,
                          finish_reason='stop', response_model='OFFLINE_FIXTURE',
                          usage=dict(prompt_tokens=10, completion_tokens=10, total_tokens=20))
        return value, dict(request_model=config['model'], response_model='OFFLINE_FIXTURE',
                           usage=dict(total_tokens=20), origin=origin(config), finish_reason='stop')


def test_input_keeps_original_complex_brief_and_unknowns():
    assert BRIEF == CASES['complex'][1]
    assert all(text in BRIEF for text in ('单次最大告警数', '查询间隔', '通知重试次数'))


def test_pending_or_stale_review_cannot_adopt(tmp_path):
    store, project = candidates(tmp_path)
    with pytest.raises(Problem, match='所有条目'):
        select_reviewed_draft(store, project['id'], review_template(project))
    assert store.get(project['id']) == project
    invalid = signed_review(project)
    invalid['items'][0]['content_hash'] = 'wrong'
    with pytest.raises(Problem, match='已变化'):
        select_reviewed_draft(store, project['id'], invalid)
    assert store.get(project['id']) == project


def test_synthetic_selection_preserves_unknowns_without_confirmation(tmp_path):
    store, project = candidates(tmp_path)
    after = select_reviewed_draft(store, project['id'], signed_review(project))
    assert after['questions'] == project['questions']
    assert all(item['selection_status'] == 'selected' for item in after['items'])
    assert all('requirement_confirmation' not in item for item in after['items'])
    assert all(item['test_review']['business_approval'] is False for item in after['items'])
    assert after['items'][0]['detail_contract'] == project['items'][0]['detail_contract']
    assert store.records(project['id'], 'synthetic_test_review')


def test_actions_workflow_document_to_review_export_records_real_pipeline(tmp_path):
    async def scenario():
        store, project = candidates(tmp_path)
        select_reviewed_draft(store, project['id'], signed_review(project))
        provider = OfflinePlanProvider()
        store.setting('model', dict(DEFAULT))
        actions = Actions(store, Workflow(store, provider))
        task = await run_action(store, actions, project['id'], 'document', '', 1, tmp_path)
        assert task['calls'] == 1
        manifest = await export_review(store, project['id'], tmp_path)
        summary = evidence_summary(store, project['id'])
        assert summary['model_call_count'] == 1
        assert len(summary['document']['generation_run_ids']) == 1
        assert summary['model_evidence'][0]['raw_output_sha256']
        assert summary['model_evidence'][0]['stage'] == 'prd'
        assert summary['review_readiness']['ready']
        assert not summary['formal_readiness']['ready']
        assert summary['row_confirmations'] == 0
        assert (tmp_path / 'deliverables/PRD.docx').exists()
        assert 'data:image/png;base64,' in (tmp_path / 'deliverables/PRD.md').read_text('utf-8')
        assert manifest['PRD.docx']['sha256'] == hashlib.sha256((tmp_path / 'deliverables/PRD.docx').read_bytes()).hexdigest()
        assert summary['user_business_acceptance'] == 'NOT_RUN'
    asyncio.run(scenario())


def test_reviewed_revision_uses_production_api_and_preserves_detail(tmp_path):
    async def scenario():
        store, project = candidates(tmp_path)
        with store.edit(project['id'], project['revision'], '离线模型修订夹具') as (current, _):
            revision = copy.deepcopy(current['items'][0])
            revision.update(id='REQ-REVISION', target_item_id='REQ-TEST', action='revise')
            revision['detail_contract']['flow']['nodes'][0]['label'] = '开始编辑姓名'
            current['items'].append(revision)
        project = store.get(project['id'])
        review = signed_review(project)
        next(row for row in review['items'] if row['id'] == 'REQ-TEST')['decision'] = 'exclude_from_review_draft'
        result = await select_reviewed_with_revisions(store, OfflinePlanProvider(), project['id'], review, tmp_path)
        root = next(i for i in result['items'] if i['id'] == 'REQ-TEST')
        assert root['detail_contract'] == revision['detail_contract']
        assert root['selection_status'] == 'selected'
        assert all('requirement_confirmation' not in item for item in result['items'])
        assert result['questions'] == project['questions']
        assert next(i for i in result['items'] if i['id'] == 'REQ-REVISION')['selection_status'] == 'deferred'
        evidence = json.loads((tmp_path / 'reviewed-revision-api-evidence.json').read_text('utf-8'))
        assert evidence['calls'][0]['reviewed_candidate_hash'] == next(r['content_hash'] for r in review['items'] if r['id'] == 'REQ-REVISION')
        assert not store.records(project['id'], 'run')
    asyncio.run(scenario())
