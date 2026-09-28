"""Reference diagnostics stay strict while identifying repairable source-pair mistakes."""
import copy

import pytest

from app.contracts import validate_response
from app.core import Problem
from tests.helpers import prepared
from tests.test_runtime import empty_ingest
from app.store import Store


def test_unique_excerpt_source_diagnostic_names_field_and_candidate_without_rewriting(tmp_path):
    store = Store(tmp_path)
    project = prepared(store)
    excerpts = project['sources'][0]['excerpts']
    actual = dict(source_id=excerpts[0]['source_id'], excerpt_id=excerpts[0]['id'])
    value = empty_ingest()
    value['used_source_refs'] = [
        dict(source_id='SRC-wrong-1', excerpt_id=actual['excerpt_id']),
        dict(source_id='SRC-wrong-2', excerpt_id=actual['excerpt_id']),
    ]

    with pytest.raises(Problem) as caught:
        validate_response(value, 'ingest', project, excerpts)

    assert caught.value.code == 'REFERENCE_INVALID'
    assert '$.used_source_refs[0]' in caught.value.message
    assert '$.used_source_refs[1]' in caught.value.message
    assert "source_id='SRC-wrong-1'" in caught.value.message
    assert "source_id='SRC-wrong-2'" in caught.value.message
    assert f"source_id={actual['source_id']!r}" in caught.value.message
    assert f"excerpt_id={actual['excerpt_id']!r}" in caught.value.message
    assert '不会自动替换引用' in caught.value.message
    assert [pair['source_id'] for pair in value['used_source_refs']] == ['SRC-wrong-1', 'SRC-wrong-2']


def test_ambiguous_excerpt_source_diagnostic_does_not_suggest_a_pair(tmp_path):
    store = Store(tmp_path)
    project = prepared(store)
    excerpts = copy.deepcopy(project['sources'][0]['excerpts'])
    duplicate = copy.deepcopy(excerpts[0])
    duplicate['source_id'] = 'SRC-another'
    excerpts.append(duplicate)
    value = empty_ingest()
    value['used_source_refs'] = [dict(source_id='SRC-wrong', excerpt_id=duplicate['id'])]

    with pytest.raises(Problem) as caught:
        validate_response(value, 'ingest', project, excerpts)

    assert caught.value.code == 'REFERENCE_INVALID'
    assert '$.used_source_refs[0]' in caught.value.message
    assert "source_id='SRC-wrong'" in caught.value.message
    assert '唯一对应' not in caught.value.message
    assert value['used_source_refs'][0]['source_id'] == 'SRC-wrong'


def test_reference_diagnostic_caps_a_batch_at_five_pairs(tmp_path):
    project = prepared(Store(tmp_path))
    excerpts = project['sources'][0]['excerpts']
    value = empty_ingest()
    value['used_source_refs'] = [
        dict(source_id=f'SRC-wrong-{index}', excerpt_id=excerpts[0]['id'])
        for index in range(7)
    ]

    with pytest.raises(Problem) as caught:
        validate_response(value, 'ingest', project, excerpts)

    assert caught.value.code == 'REFERENCE_INVALID'
    for index in range(5):
        assert f'$.used_source_refs[{index}]' in caught.value.message
    assert '$.used_source_refs[5]' not in caught.value.message
    assert '另有 2 项未展开' in caught.value.message


def test_actual_source_pair_remains_valid(tmp_path):
    project = prepared(Store(tmp_path))
    excerpts = project['sources'][0]['excerpts']
    value = empty_ingest()
    value['used_source_refs'] = [dict(source_id=excerpts[0]['source_id'],
                                      excerpt_id=excerpts[0]['id'])]

    assert validate_response(value, 'ingest', project, excerpts) is value
