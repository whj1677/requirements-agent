"""Answer-reference repair may remove only bindings invalid for the project."""
import asyncio
import copy

import pytest

from app.core import Problem
from app.ingest import preserve_response, response_anchor
from app.provider import DEFAULT, origin
from app.store import Store
from app.workflow import Workflow
from tests.helpers import EXAMPLES, prepared
from tests.product_flow_helpers import check_prepared
from tests.test_runtime import FakeProvider


def project_with_answer(tmp_path, *, acceptance_related):
    store = Store(tmp_path)
    project = prepared(store)
    project = check_prepared(store, project['id'], through=2)
    project = store.get(project['id'])
    with store.edit(project['id'], project['revision'], '合成已保存回答') as (draft, _):
        question = next(q for q in draft['questions'] if q['id'] == 'Q-0001')
        question.update(status='answered', answer='合成回答：只读角色不可执行该操作。',
                        understanding_status='pending')
        if acceptance_related:
            question['related_refs'].append('AC-0001')
    return store, store.get(project['id'])


def acceptance_revision(project, *, valid_binding):
    response = copy.deepcopy(EXAMPLES['clarify'])
    response['questions'] = []
    source_refs = [dict(source_id=project['sources'][0]['id'],
                        excerpt_id=project['sources'][0]['excerpts'][0]['id'])]
    acceptance = next(i for i in project['items'] if i['id'] == 'AC-0001')
    proposal = dict(
        temp_id='TMP-acceptance-revision', action='revise', target_item_id='AC-0001',
        kind='acceptance', title=acceptance['title'],
        statement='合成修订：只读角色执行操作时应看到拒绝提示。',
        applies_to='to_be', epistemic_status='reported',
        source_refs=source_refs, related_refs=['REQ-0001'],
    )
    if valid_binding:
        proposal['answer_refs'] = ['Q-0001']
    response['proposals'] = [proposal]
    response['used_source_refs'] = copy.deepcopy(source_refs)
    return response


def test_workflow_can_remove_invalid_answer_binding_without_changing_ac_revision(tmp_path):
    async def scenario():
        store, project = project_with_answer(tmp_path, acceptance_related=False)
        with store.edit(project['id'], project['revision'], '合成授权', bump=False) as (draft, _):
            draft['grants'][origin(DEFAULT)] = {
                'source_ids': [source['id'] for source in draft['sources']]
            }
        invalid = acceptance_revision(project, valid_binding=True)
        repaired = copy.deepcopy(invalid)
        repaired['proposals'][0].pop('answer_refs')
        workflow = Workflow(store, FakeProvider([invalid, repaired]))
        run = workflow.start(project['id'], project['revision'], 'clarify', '合成回答候选修复')
        await workflow.tasks[run['id']]
        return store, project, store.get_record(project['id'], run['id'], 'run')

    store, project, run = asyncio.run(scenario())
    assert run['result_applied'] is True
    assert run['calls'] == 2 and run['repair_count'] == 1
    accepted = run['response']['proposals'][0]
    assert accepted['action'] == 'revise'
    assert accepted['target_item_id'] == 'AC-0001'
    assert accepted['kind'] == 'acceptance'
    assert accepted['statement'] == '合成修订：只读角色执行操作时应看到拒绝提示。'
    assert accepted['source_refs'] == acceptance_revision(project, valid_binding=True)['proposals'][0]['source_refs']
    assert 'answer_refs' not in accepted
    stored = store.get(project['id'])
    candidate = next(i for i in stored['items'] if i.get('target_item_id') == 'AC-0001'
                     and i['selection_status'] == 'candidate')
    assert candidate['action'] == 'revise'
    assert candidate['target_item_id'] == 'AC-0001'
    assert 'answer_refs' not in candidate


@pytest.mark.parametrize('mutation', [
    'remove_valid_binding', 'change_target', 'revise_to_add', 'change_kind',
    'change_statement', 'add_valid_binding',
])
def test_project_aware_anchor_keeps_all_valid_bindings_and_revision_identity(tmp_path, mutation):
    store, project = project_with_answer(tmp_path, acceptance_related=True)
    original = acceptance_revision(project, valid_binding=mutation != 'add_valid_binding')
    corrected = copy.deepcopy(original)
    proposal = corrected['proposals'][0]
    if mutation == 'remove_valid_binding':
        proposal.pop('answer_refs')
    elif mutation == 'change_target':
        proposal['target_item_id'] = 'REQ-0001'
    elif mutation == 'revise_to_add':
        proposal['action'] = 'add'
        proposal['target_item_id'] = None
    elif mutation == 'change_kind':
        proposal['kind'] = 'rule'
    elif mutation == 'change_statement':
        proposal['statement'] = '合成改写：所有角色均可执行。'
    elif mutation == 'add_valid_binding':
        proposal['answer_refs'] = ['Q-0001']

    excerpts = project['sources'][0]['excerpts']
    anchor = response_anchor(original, excerpts, 'clarify', project=project)
    with pytest.raises(Problem) as caught:
        preserve_response(anchor, corrected, excerpts, 'clarify',
                          original=original, project=project)
    assert caught.value.code == 'SEMANTIC_BLOCKED'
