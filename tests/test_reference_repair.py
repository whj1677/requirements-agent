"""Narrow repair anchors for malformed review references."""
import copy

import pytest

from app.contracts import validate_response
from app.core import Problem
from app.ingest import preserve_response, response_anchor
from app.store import Store
from tests.helpers import EXAMPLES, prepared


def review_case(tmp_path):
    project = prepared(Store(tmp_path))
    excerpts = project['sources'][0]['excerpts']
    original = copy.deepcopy(EXAMPLES['review'])
    original['result']['assessment'] = 'ready_for_human_review'
    original['result']['required_decisions'] = []
    original['result']['reviewed_refs'] = ['REQ-0001', 'AC-83631ae82ea8460d']
    original['result']['perspectives'][0]['role_note'] = 'extra schema field'
    original['used_source_refs'] = [
        {'source_id': 'SRC-0001', 'excerpt_id': 'EX-0001'},
        {'source_id': 'SRC-wrong', 'excerpt_id': 'EX-0001'},
    ]
    fixed = copy.deepcopy(original)
    fixed['result']['reviewed_refs'] = ['REQ-0001']
    fixed['result']['perspectives'][0].pop('role_note')
    fixed['used_source_refs'] = [
        {'source_id': 'SRC-0001', 'excerpt_id': 'EX-0001'}
    ]
    return project, excerpts, original, fixed


def repair(project, excerpts, original, candidate):
    anchor = response_anchor(original, excerpts, 'review', project=project)
    preserve_response(anchor, candidate, excerpts, 'review',
                      original=original, project=project)


def blocked(project, excerpts, original, candidate):
    with pytest.raises(Problem) as caught:
        repair(project, excerpts, original, candidate)
    assert caught.value.code == 'SEMANTIC_BLOCKED'


def test_review_repair_drops_unknown_coverage_and_corrects_only_unique_source_pair(tmp_path):
    project, excerpts, original, fixed = review_case(tmp_path)
    snapshot = copy.deepcopy(original)

    assert validate_response(fixed, 'review', project, excerpts) is fixed
    repair(project, excerpts, original, fixed)

    assert original == snapshot
    assert fixed['result']['reviewed_refs'] == ['REQ-0001']
    assert fixed['used_source_refs'] == [
        {'source_id': 'SRC-0001', 'excerpt_id': 'EX-0001'}
    ]
    assert fixed['findings'] == original['findings']
    assert fixed['result']['assessment'] == original['result']['assessment']
    assert fixed['result']['required_decisions'] == original['result']['required_decisions']


def test_wrong_source_is_corrected_without_changing_excerpt_or_valid_neighbors(tmp_path):
    project, excerpts, original, fixed = review_case(tmp_path)
    original['used_source_refs'] = [{'source_id': 'SRC-wrong', 'excerpt_id': 'EX-0001'}]
    assert validate_response(fixed, 'review', project, excerpts) is fixed
    repair(project, excerpts, original, fixed)
    assert original['used_source_refs'][0]['source_id'] == 'SRC-wrong'


def test_deleting_wrong_pair_before_valid_pair_preserves_the_valid_pair(tmp_path):
    project, excerpts, original, fixed = review_case(tmp_path)
    original['used_source_refs'].reverse()
    assert validate_response(fixed, 'review', project, excerpts) is fixed
    repair(project, excerpts, original, fixed)


@pytest.mark.parametrize('replacement', ['REQ-0001', 'AC-0001'])
def test_unknown_reviewed_id_cannot_be_replaced_with_new_valid_coverage(tmp_path, replacement):
    project, excerpts, original, fixed = review_case(tmp_path)
    candidate = copy.deepcopy(fixed)
    candidate['result']['reviewed_refs'] = ['REQ-0001', replacement]
    assert validate_response(candidate, 'review', project, excerpts) is candidate
    blocked(project, excerpts, original, candidate)


def test_review_repair_rejects_new_reviewed_reference_slot(tmp_path):
    project, excerpts, original, fixed = review_case(tmp_path)
    candidate = copy.deepcopy(fixed)
    candidate['result']['reviewed_refs'].append('AC-0001')
    assert validate_response(candidate, 'review', project, excerpts) is candidate
    blocked(project, excerpts, original, candidate)


@pytest.mark.parametrize('mutation', [
    'delete_valid_source',
    'change_assessment',
    'change_findings',
    'change_required_decisions',
    'change_perspective',
])
def test_review_repair_rejects_changes_outside_invalid_reference_slots(tmp_path, mutation):
    project, excerpts, original, fixed = review_case(tmp_path)
    candidate = copy.deepcopy(fixed)
    if mutation == 'delete_valid_source':
        candidate['used_source_refs'] = []
    elif mutation == 'change_assessment':
        candidate['result']['assessment'] = 'needs_changes'
    elif mutation == 'change_findings':
        candidate['findings'][0]['message'] = 'rewritten finding'
    elif mutation == 'change_required_decisions':
        candidate['result']['required_decisions'] = ['new decision']
    else:
        candidate['result']['perspectives'][0]['considerations'] = ['rewritten review']
    blocked(project, excerpts, original, candidate)


def test_review_repair_rejects_replacing_source_pair_across_excerpts(tmp_path):
    project, excerpts, original, fixed = review_case(tmp_path)
    other = copy.deepcopy(excerpts[0])
    other['id'] = 'EX-0002'
    excerpts.append(other)
    candidate = copy.deepcopy(fixed)
    candidate['used_source_refs'] = [
        {'source_id': 'SRC-0001', 'excerpt_id': 'EX-0002'}
    ]
    assert validate_response(candidate, 'review', project, excerpts) is candidate
    blocked(project, excerpts, original, candidate)


def test_review_repair_rejects_ambiguous_source_for_same_excerpt(tmp_path):
    project, excerpts, original, fixed = review_case(tmp_path)
    duplicate = copy.deepcopy(excerpts[0])
    duplicate['source_id'] = 'SRC-OTHER'
    excerpts.append(duplicate)
    candidate = copy.deepcopy(fixed)
    candidate['used_source_refs'].append(
        {'source_id': 'SRC-OTHER', 'excerpt_id': 'EX-0001'})
    assert validate_response(candidate, 'review', project, excerpts) is candidate
    blocked(project, excerpts, original, candidate)


def test_review_repair_rejects_reordering_originally_valid_source_refs(tmp_path):
    project, excerpts, original, fixed = review_case(tmp_path)
    second = copy.deepcopy(excerpts[0])
    second['id'] = 'EX-0002'
    excerpts.append(second)
    valid_second = {'source_id': 'SRC-0001', 'excerpt_id': 'EX-0002'}
    original['used_source_refs'] = [
        valid_second,
        {'source_id': 'SRC-wrong', 'excerpt_id': 'EX-0001'},
        {'source_id': 'SRC-0001', 'excerpt_id': 'EX-0001'},
    ]
    fixed['used_source_refs'] = [
        valid_second,
        {'source_id': 'SRC-0001', 'excerpt_id': 'EX-0001'},
    ]
    candidate = copy.deepcopy(fixed)
    candidate['used_source_refs'].reverse()
    assert validate_response(candidate, 'review', project, excerpts) is candidate
    blocked(project, excerpts, original, candidate)


def scope_evidence_case(tmp_path):
    project = prepared(Store(tmp_path))
    excerpts = project['sources'][0]['excerpts']
    response = copy.deepcopy(EXAMPLES['clarify'])
    response['proposals'] = [{
        'temp_id': 'TMP-scope-evidence', 'action': 'add', 'target_item_id': None,
        'kind': 'requirement', 'title': '现有功能分类',
        'statement': '电价时段维护属于现有功能。', 'applies_to': 'as_is',
        'epistemic_status': 'reported', 'source_refs': [],
        'related_refs': ['REQ-0001'], 'change_type': 'existing',
        'scope_evidence': [{
            'state': 'current', 'quote': excerpts[0]['text'],
            'source_ref': {'source_id': 'SRC-wrong', 'excerpt_id': 'EX-0001'},
        }],
        'classification_reason': '引用材料说明该功能已存在。',
    }]
    repaired = copy.deepcopy(response)
    repaired['proposals'][0]['scope_evidence'][0]['source_ref'] = {
        'source_id': 'SRC-0001', 'excerpt_id': 'EX-0001'
    }
    return project, excerpts, response, repaired


def test_scope_evidence_single_pair_can_be_corrected_only_to_unique_same_excerpt(tmp_path):
    project, excerpts, original, repaired = scope_evidence_case(tmp_path)
    assert validate_response(repaired, 'clarify', project, excerpts) is repaired
    anchor = response_anchor(original, excerpts, 'clarify', project=project)
    preserve_response(anchor, repaired, excerpts, 'clarify',
                      original=original, project=project)


def test_valid_single_pair_cannot_be_replaced_with_another_real_source(tmp_path):
    project, excerpts, _, original = scope_evidence_case(tmp_path)
    other = copy.deepcopy(excerpts[0])
    other['source_id'] = 'SRC-OTHER'
    excerpts.append(other)
    candidate = copy.deepcopy(original)
    candidate['proposals'][0]['scope_evidence'][0]['source_ref']['source_id'] = 'SRC-OTHER'
    assert validate_response(candidate, 'clarify', project, excerpts) is candidate
    anchor = response_anchor(original, excerpts, 'clarify', project=project)
    with pytest.raises(Problem, match='格式修复改变'):
        preserve_response(anchor, candidate, excerpts, 'clarify', original=original, project=project)


@pytest.mark.parametrize('mutation', [
    'change_excerpt', 'ambiguous_source', 'change_state', 'change_quote',
    'drop_reference', 'drop_evidence',
])
def test_scope_evidence_pair_repair_cannot_change_evidence_or_classification(tmp_path, mutation):
    project, excerpts, original, repaired = scope_evidence_case(tmp_path)
    candidate = copy.deepcopy(repaired)
    evidence = candidate['proposals'][0]['scope_evidence']
    if mutation == 'change_excerpt':
        other = copy.deepcopy(excerpts[0])
        other['id'] = 'EX-0002'
        excerpts.append(other)
        evidence[0]['source_ref']['excerpt_id'] = 'EX-0002'
    elif mutation == 'ambiguous_source':
        duplicate = copy.deepcopy(excerpts[0])
        duplicate['source_id'] = 'SRC-OTHER'
        excerpts.append(duplicate)
        evidence[0]['source_ref']['source_id'] = 'SRC-OTHER'
    elif mutation == 'change_state':
        evidence[0]['state'] = 'preserve'
        candidate['proposals'][0]['change_type'] = 'preserved'
        candidate['proposals'][0]['applies_to'] = 'to_be'
    elif mutation == 'change_quote':
        evidence[0]['quote'] = '重叠拒绝'
    elif mutation == 'drop_reference':
        evidence[0].pop('source_ref')
    else:
        candidate['proposals'][0]['scope_evidence'] = []

    if mutation not in ('drop_reference', 'drop_evidence'):
        assert validate_response(candidate, 'clarify', project, excerpts) is candidate
    anchor = response_anchor(original, excerpts, 'clarify', project=project)
    with pytest.raises(Problem) as caught:
        preserve_response(anchor, candidate, excerpts, 'clarify',
                          original=original, project=project)
    assert caught.value.code == 'SEMANTIC_BLOCKED'
