"""A compound question can be split during repair only while its parent stays intact."""
import asyncio
import copy

import pytest

from app import config as app_config
from app.core import Problem
from app.provider import DEFAULT, origin
from app.store import Store
from app.workflow import Workflow
from tests.helpers import EXAMPLES, prepared
from tests.product_flow_helpers import check_prepared
from tests.test_runtime import FakeProvider, empty_ingest


def response_with_compound_question(stage, source_ref):
    response = copy.deepcopy(empty_ingest() if stage == 'ingest' else EXAMPLES['clarify'])
    parent = copy.deepcopy(response['questions'][0]) if response['questions'] else dict(
        options=[], blocking=False, blocking_stage=4)
    parent.update(
        temp_id='QTMP-contact-notes-display',
        topic_key='contacts.notes.display',
        question='可编辑角色的备注在列表中如何展示？只读角色是否采用相同展示？',
        why='展示方式影响页面方案与验收；只读角色能否查看需要与只读约束协同。',
        options=['列表内完整展示', '列表中截断并可查看全文', '只读角色看不到备注'],
        blocking=False,
        blocking_stage=4,
        related_refs=['REQ-0001'],
        source_refs=[copy.deepcopy(source_ref)],
    )
    response['questions'] = [parent]
    if stage == 'ingest':
        response['used_source_refs'] = [copy.deepcopy(source_ref)]
    return response


def repaired_with_decision_points(original, source_ref):
    repaired = copy.deepcopy(original)
    parent = repaired['questions'][0]

    def child(temp_id, topic_key, question, options):
        return dict(
            temp_id=temp_id, topic_key=topic_key, question=question,
            why='需要独立确认的展示决定。', options=options,
            blocking=False, blocking_stage=4,
            related_refs=['REQ-0001'], source_refs=[copy.deepcopy(source_ref)],
        )

    parent['decision_points'] = [
        child('QTMP-contact-notes-edit-display', 'contacts.notes.editable_display',
              '可编辑角色的备注在列表中如何展示？',
              ['列表内完整展示', '列表中截断并可查看全文']),
        child('QTMP-contact-notes-readonly-display', 'contacts.notes.readonly_display',
              '只读角色是否采用与可编辑角色相同的备注展示？',
              ['相同展示但不可编辑', '只读角色看不到备注']),
    ]
    return repaired


@pytest.mark.parametrize('stage', ['ingest', 'clarify'])
@pytest.mark.parametrize('rewrite_parent', [False, True], ids=['keep-parent', 'rewrite-parent'])
def test_compound_question_repair_preserves_parent_and_splits_children(
        tmp_path, monkeypatch, stage, rewrite_parent):
    data_dir = tmp_path / 'isolated-data'
    monkeypatch.setenv('RA_DATA_DIR', str(data_dir))
    monkeypatch.setattr(app_config, 'ROOT', tmp_path)

    async def scenario():
        store = Store(data_dir)
        project = prepared(store)
        if stage == 'clarify':
            project = check_prepared(store, project['id'], through=2)
        project = store.get(project['id'])
        source_ref = dict(source_id='SRC-0001', excerpt_id='EX-0001')
        with store.edit(project['id'], project['revision'], '合成授权', bump=False) as (draft, _):
            draft['grants'][origin(DEFAULT)] = {
                'source_ids': [source['id'] for source in draft['sources']]
            }

        original = response_with_compound_question(stage, source_ref)
        repaired = repaired_with_decision_points(original, source_ref)
        if rewrite_parent:
            repaired['questions'][0]['question'] = '备注字段如何展示？'
            repaired['questions'][0]['options'] = []
        workflow = Workflow(store, FakeProvider([original, repaired]))
        run = workflow.start(project['id'], project['revision'], stage, '合成复合问题修复')
        await workflow.tasks[run['id']]
        result = store.get_record(project['id'], run['id'], 'run')
        return store, project, result

    store, project, result = asyncio.run(scenario())
    if rewrite_parent:
        assert result['status'] == 'failed'
        assert result['error'] == 'SEMANTIC_BLOCKED'
        assert 'questions' in result['message'] and 'question' in result['message']
        assert result['calls'] == 2 and result['repair_count'] == 1
        assert not result.get('result_applied')
        assert store.get(project['id'])['questions'] == project['questions']
        return

    assert result['result_applied'] is True
    assert result['calls'] == 2 and result['repair_count'] == 1
    accepted = result['response']['questions'][0]
    original_parent = response_with_compound_question(stage, dict(
        source_id='SRC-0001', excerpt_id='EX-0001'))['questions'][0]
    for field in ('temp_id', 'topic_key', 'question', 'why', 'options', 'blocking',
                  'blocking_stage', 'related_refs', 'source_refs'):
        assert accepted[field] == original_parent[field]
    assert len(accepted['decision_points']) == 2

    persisted = store.get(project['id'])['questions']
    persisted_questions = {question['question'] for question in persisted}
    assert {child['question'] for child in accepted['decision_points']} <= persisted_questions
