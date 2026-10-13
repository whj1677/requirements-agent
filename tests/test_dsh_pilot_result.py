"""Deterministic framing recovery must not hide ambiguous business output."""
import json

import pytest

from scripts.run_dsh_requirement_pilot import parse_business_final


def body():
    return json.dumps(dict(summary='候选', summary_fields={}, requirements=[], limitations=[]), ensure_ascii=False)


def test_short_progress_preface_is_retained_without_changing_business():
    raw = body()
    value, envelope = parse_business_final('The material has been read.\n\n' + raw)
    assert value == json.loads(raw)
    assert envelope['prefix'] == 'The material has been read.\n\n'
    assert envelope['mode'] == 'single_prose_preface'


@pytest.mark.parametrize('raw', [
    'First\nSecond\n' + body(),
    body() + '\n' + body(),
    '```json\n' + body() + '\n```',
    'Read.\n' + body() + '\nDone.',
    'Read.\n{"summary":"a","summary":"b","summary_fields":{},"requirements":[],"limitations":[]}',
    'Read.\n{"summary":NaN,"summary_fields":{},"requirements":[],"limitations":[]}',
    'Read.\n{"summary":',
])
def test_ambiguous_or_malformed_result_is_not_repaired(raw):
    with pytest.raises(ValueError):
        parse_business_final(raw)


def test_strict_json_stays_strict():
    value, envelope = parse_business_final(body())
    assert value['summary'] == '候选' and envelope['mode'] == 'strict_object'
