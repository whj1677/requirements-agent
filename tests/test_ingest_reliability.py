"""Synthetic reproductions of live failure shapes; never load private responses."""
import copy
import json
import jsonschema
import pytest

from app.provider import DEFAULT, assemble, request_schema
from app.store import Store
from tests.test_runtime import empty_ingest, execute_run


def test_ingest_contract_is_stage_local_and_example_is_executable(tmp_path):
    messages, _, _ = assemble(Store(tmp_path).create('联系人'), 'ingest', '', DEFAULT, tmp_path)
    header = json.loads(messages[0]['content'].split('可信任务头：')[1])
    schema = header['schema']
    assert schema['properties']['stage'] == {'const': 'ingest'}
    assert 'prdResult' not in schema.get('$defs', {})
    assert 'uiResult' not in schema.get('$defs', {})
    jsonschema.Draft202012Validator(schema).validate(header['ingest_contract']['example'])
    assert header['ingest_contract']['example']['result'] == {'understanding': '', 'material_limits': []}


def test_all_extra_fields_and_required_result_reported_together(tmp_path):
    bad = empty_ingest()
    del bad['result']
    bad['product_context_proposal']['priority_note'] = 'synthetic note'
    bad['used_source_refs'] = [dict(source_id='SRC-absent', excerpt_id='EX-absent', excerpt_id_note='placeholder')]
    run, _, provider = execute_run(tmp_path, [bad, empty_ingest()], max_calls=2)
    repair = provider.received[1][-1]['content']
    assert all(s in repair for s in ['priority_note', 'excerpt_id_note', 'result', 'EX-absent'])
    assert run['status'] == 'succeeded'


def test_repair_keeps_full_output_beyond_previous_12000_limit(tmp_path):
    bad = empty_ingest()
    bad['summary'] = '甲' * 5900
    bad['product_context_proposal']['current_state'] = '合成现状' * 1400
    bad['result']['understanding'] = '乙' * 11000 + 'END-OF-UNDERSTANDING'
    bad['unexpected'] = 'synthetic error'
    valid = copy.deepcopy(bad)
    del valid['unexpected']
    from app.core import dumps
    assert dumps(bad).index('END-OF-UNDERSTANDING') > 12000
    run, _, provider = execute_run(tmp_path, [bad, valid], max_calls=2)
    assert 'END-OF-UNDERSTANDING' in provider.received[1][-1]['content']
    assert run['status'] == 'succeeded'


def question():
    return dict(temp_id='QTMP-one', topic_key='delete', question='谁可以删除联系人？',
                why='影响权限', options=[], blocking=True, blocking_stage=3,
                source_refs=[], related_refs=[])


@pytest.mark.parametrize('mutation', ['replace_question', 'drop_question', 'downgrade_blocking'])
def test_format_repair_cannot_change_business_decisions(tmp_path, mutation):
    bad = empty_ingest()
    bad['questions'] = [question()]
    bad['unexpected'] = True
    fixed = copy.deepcopy(bad)
    del fixed['unexpected']
    if mutation == 'replace_question':
        fixed['questions'][0]['question'] = '你想要哪种颜色？'
    elif mutation == 'drop_question':
        fixed['questions'] = []
    else:
        fixed['questions'][0]['blocking'] = False
    run, project, _ = execute_run(tmp_path, [bad, fixed], max_calls=2)
    assert run['error'] == 'SEMANTIC_BLOCKED'
    assert project['questions'] == [] and project['messages'] == []


def test_required_result_not_optional_or_auto_filled(tmp_path):
    bad = empty_ingest()
    del bad['result']
    run, project, _ = execute_run(tmp_path, [bad] * 3)
    assert run['error'] == 'SCHEMA_INVALID' and run['calls'] == 3
    assert project['documents'] == {} and project['items'] == []


def test_legacy_response_remains_readable():
    from app.contracts import validate_response
    value = empty_ingest()
    del value['product_context_proposal']
    validate_response(value, 'ingest', dict(items=[], questions=[], ui=None), [])
    assert list(jsonschema.Draft202012Validator(request_schema('ingest')).iter_errors(value))


@pytest.mark.parametrize('failure', ['extra_fields', 'extra_json', 'missing_result', 'invalid_reference'])
def test_real_http_response_shapes_repair_without_hiding_original(tmp_path, monkeypatch, failure):
    import asyncio
    import httpx
    from app.provider import Provider, origin
    from app.workflow import Workflow
    bad = empty_ingest()
    bad['questions'] = [question()]
    good = copy.deepcopy(bad)
    if failure == 'extra_fields':
        bad['product_context_proposal']['value_note'] = 'not allowed'
    if failure == 'missing_result':
        del bad['result']
    if failure == 'invalid_reference':
        bad['used_source_refs'] = [dict(source_id='SRC-absent', excerpt_id='EX-absent')]
    original = json.dumps(bad, ensure_ascii=False) + ('\n{"extra":true}' if failure == 'extra_json' else '')
    outputs = iter([original, json.dumps(good, ensure_ascii=False)])
    requests = []

    class HTTPFixture:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, *args, **kwargs):
            requests.append(copy.deepcopy(kwargs['json']))
            return httpx.Response(200, json={'choices':[{'message':{'content':next(outputs)},'finish_reason':'stop'}],
                                            'usage':{'prompt_tokens':1,'completion_tokens':2}})

    monkeypatch.setattr(httpx, 'AsyncClient', HTTPFixture)

    async def scenario():
        store = Store(tmp_path)
        p = store.create('合成联系人材料')
        with store.edit(p['id'], 0, 'synthetic grant', bump=False) as (draft, _):
            draft['grants'][origin(DEFAULT)] = {'source_ids':[]}
        provider = Provider(tmp_path/'.env')
        provider.keys[origin(DEFAULT)] = 'synthetic-test-key'
        store.setting('model', dict(DEFAULT, max_calls=2, max_tokens=16000))
        workflow = Workflow(store, provider)
        run = workflow.start(p['id'], 0, 'ingest', '')
        await workflow.tasks[run['id']]
        return store.get_record(p['id'], run['id']), store.get(p['id'])

    run, project = asyncio.run(scenario())
    assert run['calls'] == 2 and run['status'] == 'awaiting_user'
    assert all(r['max_tokens'] == 16000 for r in requests)
    assert requests[0]['messages'][0] == requests[1]['messages'][0]
    assert original in requests[1]['messages'][-1]['content']
    evidence = [json.loads(f.read_text('utf8')) for f in (tmp_path/'evidence/model-calls').glob('*.json')]
    assert len(evidence) == 2 and any(e['final_output'] == original and e['validation_result'] != 'accepted' for e in evidence)
    assert any(e['repair_of'] and e['validation_result'] == 'accepted' for e in evidence)
    assert project['questions'][0]['status'] == 'open' and project['active_baseline_id'] is None


def test_valid_source_cannot_be_stripped_during_format_repair():
    from app.ingest import business_anchor, preserve
    from app.core import Problem
    value = empty_ingest()
    value['questions'] = [question()]
    value['questions'][0]['source_refs'] = [dict(source_id='S1', excerpt_id='E1')]
    excerpts = [dict(source_id='S1', id='E1')]
    anchor = business_anchor(value, excerpts)
    value['questions'][0]['source_refs'] = []
    with pytest.raises(Problem, match='格式修复改变'):
        preserve(anchor, value, excerpts)
