"""Engineering claim aliases may be repaired only to one excerpt sent in this call."""
import copy

import pytest

from app.contracts import validate_response
from app.core import Problem
from app.ingest import preserve_response, response_anchor
from app.store import Store
from tests.helpers import EXAMPLES, prepared


def engineering_excerpt():
    return dict(id='SRC-0001:c-flow', source_id='SRC-0001',
                business_claim_id='c-flow', text='代码中的数据流',
                source_hash='fixture', locator='repo:app/flow.py:1-2',
                method='business-context')


def check(project, excerpts, original, fixed, stage):
    anchor = response_anchor(original, excerpts, stage, project=project)
    preserve_response(anchor, fixed, excerpts, stage, original=original, project=project)


def blocked(project, excerpts, original, fixed, stage):
    with pytest.raises(Problem) as caught:
        check(project, excerpts, original, fixed, stage)
    assert caught.value.code == 'SEMANTIC_BLOCKED'


@pytest.mark.parametrize('field', ['used_source_refs', 'source_refs'])
def test_unique_claim_alias_in_review_ref_list_can_be_corrected(tmp_path, field):
    project = prepared(Store(tmp_path))
    excerpts = [engineering_excerpt()]
    original = copy.deepcopy(EXAMPLES['review'])
    original['result']['assessment'] = 'ready_for_human_review'
    original['result']['required_decisions'] = []
    old = {'source_id': 'SRC-0001', 'excerpt_id': 'c-flow'}
    fixed_ref = {'source_id': 'SRC-0001', 'excerpt_id': excerpts[0]['id']}
    if field == 'used_source_refs':
        original['findings'] = []
        original[field] = [old]
        fixed = copy.deepcopy(original)
        fixed[field] = [fixed_ref]
    else:
        original['used_source_refs'] = []
        original['findings'][0][field] = [old]
        fixed = copy.deepcopy(original)
        fixed['findings'][0][field] = [fixed_ref]
    snapshot = copy.deepcopy(original)
    assert validate_response(fixed, 'review', project, excerpts) is fixed
    check(project, excerpts, original, fixed, 'review')
    assert original == snapshot


def test_unique_claim_alias_in_singular_scope_ref_can_be_corrected(tmp_path):
    project = prepared(Store(tmp_path))
    excerpts = [engineering_excerpt()]
    original = copy.deepcopy(EXAMPLES['clarify'])
    original['questions'] = []
    original['used_source_refs'] = []
    original['proposals'] = [dict(
        temp_id='TMP-scope-evidence', action='add', target_item_id=None,
        kind='requirement', title='现有数据流', statement='该数据流已存在。',
        applies_to='as_is', epistemic_status='reported', source_refs=[],
        related_refs=['REQ-0001'], change_type='existing',
        scope_evidence=[dict(state='current', quote=excerpts[0]['text'],
                             source_ref=dict(source_id='SRC-0001', excerpt_id='c-flow'))],
        classification_reason='引用代码快照。')]
    fixed = copy.deepcopy(original)
    fixed['proposals'][0]['scope_evidence'][0]['source_ref']['excerpt_id'] = excerpts[0]['id']
    assert validate_response(fixed, 'clarify', project, excerpts) is fixed
    check(project, excerpts, original, fixed, 'clarify')


@pytest.mark.parametrize('mutation', ['wrong_source', 'not_sent', 'non_business', 'ambiguous', 'duplicate_actual', 'change_valid'])
def test_claim_alias_repair_rejects_other_identity_changes(tmp_path, mutation):
    project = prepared(Store(tmp_path))
    engineering = engineering_excerpt()
    excerpts = [engineering]
    original = copy.deepcopy(EXAMPLES['review'])
    original['findings'] = []
    original['result']['assessment'] = 'ready_for_human_review'
    original['result']['required_decisions'] = []
    old = dict(source_id='SRC-0001', excerpt_id='c-flow')
    fixed = copy.deepcopy(original)
    fixed['used_source_refs'] = [dict(source_id='SRC-0001', excerpt_id=engineering['id'])]
    if mutation == 'wrong_source':
        old['source_id'] = 'SRC-wrong'
    elif mutation == 'not_sent':
        hidden = copy.deepcopy(engineering)
        hidden['business_claim_id'] = 'c-hidden'
        old['excerpt_id'] = 'c-hidden'
    elif mutation == 'non_business':
        excerpts = [{key: val for key, val in engineering.items() if key != 'business_claim_id'}]
    elif mutation == 'ambiguous':
        duplicate = copy.deepcopy(engineering)
        duplicate['id'] = 'SRC-0001:c-flow-2'
        excerpts.append(duplicate)
    elif mutation == 'duplicate_actual':
        excerpts.append(copy.deepcopy(engineering))
    else:
        old['excerpt_id'] = engineering['id']
        second = copy.deepcopy(engineering)
        second['id'] = 'SRC-0001:c-other'
        second['business_claim_id'] = 'c-other'
        excerpts.append(second)
        fixed['used_source_refs'] = [dict(source_id='SRC-0001', excerpt_id=second['id'])]
    original['used_source_refs'] = [old]
    assert validate_response(fixed, 'review', project, excerpts) is fixed
    blocked(project, excerpts, original, fixed, 'review')
