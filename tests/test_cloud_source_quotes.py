"""Synthetic energy-cloud regressions; no paid calls or daily data writes."""
import asyncio
import copy
import json

import httpx
import pytest

from app.contracts import validate_response
from app.core import Problem, digest
from app.ingest import business_anchor, preserve, preserve_response, response_anchor
from app.provider import DEFAULT, Provider, origin
from app.source_quotes import normalize_scope_quotes
from app.sources import save_source
from app.store import Store
from app.workflow import Workflow
from tests.test_runtime import empty_ingest


TEXT = '新增云平台电价时段：边界对齐15分钟刻度，完整覆盖 [00:00,24:00)；相邻时段合法，重叠时拒绝发布。'
QUOTE = TEXT.split('；')[0] + '。'


def response(ref=None):
    ref = ref or dict(source_id='SRC-cloud', excerpt_id='EX-tariff')
    value = empty_ingest()
    value['used_source_refs'] = [copy.deepcopy(ref)]
    value['proposals'] = [dict(temp_id='TMP-cloud-tariff', action='add', target_item_id=None,
        kind='requirement', title='电价时段', statement=TEXT, applies_to='to_be',
        epistemic_status='reported', source_refs=[copy.deepcopy(ref)], related_refs=[],
        change_type='new', behavior={'flow': TEXT},
        scope_evidence=[dict(state='change', quote=QUOTE, source_ref=copy.deepcopy(ref))])]
    return value


def excerpts():
    return [dict(source_id='SRC-cloud', id='EX-tariff', text=TEXT)]


def test_unique_quote_normalizes_without_mutating_raw_or_business_text():
    raw = response()
    snapshot = copy.deepcopy(raw)
    with pytest.raises(Problem, match='分类依据不是'):
        validate_response(raw, 'ingest', dict(items=[], questions=[], ui=None), excerpts())
    normalized, changes = normalize_scope_quotes(raw, excerpts())
    validate_response(normalized, 'ingest', dict(items=[], questions=[], ui=None), excerpts())
    assert raw == snapshot
    assert normalized['proposals'][0]['statement'] == TEXT
    assert normalized['proposals'][0]['scope_evidence'][0]['quote'] == QUOTE[:-1] + '；'
    assert changes[0]['original_hash'] == digest(QUOTE)
    assert changes[0]['normalized_hash'] == digest(QUOTE[:-1] + '；')
    assert len(changes) == 1
    assert normalize_scope_quotes(normalized, excerpts()) == (normalized, [])


@pytest.mark.parametrize('source_ending,model_ending', [('\r','\n'), ('\r\n','\n'), ('\n','\r\n')])
def test_only_line_endings_are_restored_to_the_unique_source_span(source_ending, model_ending):
    text = source_ending.join(['【电价名称】：输入框', '【创建时间】：日期范围选择器'])
    raw = response()
    quote = text.replace(source_ending, model_ending)
    raw['proposals'][0]['scope_evidence'][0]['quote'] = quote
    sent = [dict(excerpts()[0], text='前文：' + text + '。后文')]
    before = copy.deepcopy(raw)
    fixed, changes = normalize_scope_quotes(raw, sent)
    assert len(changes) == 1 and changes[0]['rule'] == 'scope-quote-line-endings-1'
    assert fixed['proposals'][0]['scope_evidence'][0]['quote'] == text
    proof = changes[0]
    assert sent[0]['text'][proof['match_start']:proof['match_end']] == text
    assert raw == before
    other = copy.deepcopy(fixed)
    other['proposals'][0]['scope_evidence'][0]['quote'] = quote
    assert other == raw


@pytest.mark.parametrize('case', ['duplicate_span', 'space_change', 'missing_line', 'number_change',
                                  'visual_placeholder', 'wrong_pair', 'duplicate_pair'])
def test_line_endings_do_not_allow_fuzzy_or_unrelated_quote_recovery(case):
    raw = response()
    text = '仅管理员可编辑\r电价阈值15元'
    quote = text.replace('\r', '\n')
    sent = [dict(excerpts()[0], text=text)]
    if case == 'duplicate_span': sent[0]['text'] += '\n' + text.replace('\r','\n')
    elif case == 'space_change': quote = quote.replace('15', ' 15')
    elif case == 'missing_line': quote = quote.replace('\n', '')
    elif case == 'number_change': quote = quote.replace('15','30')
    elif case == 'visual_placeholder': sent[0]['text'] = '图片像素已提取，尚未视觉分析'
    elif case == 'wrong_pair': sent[0]['source_id'] = 'SRC-other'
    elif case == 'duplicate_pair': sent.append(copy.deepcopy(sent[0]))
    raw['proposals'][0]['scope_evidence'][0]['quote'] = quote
    before = copy.deepcopy(raw)
    assert normalize_scope_quotes(raw, sent) == (before, [])
    assert raw == before


@pytest.mark.parametrize('case', ['wrong_source', 'wrong_excerpt', 'pair_from_another_source',
    'duplicate_match', 'duplicate_pair', 'internal_punctuation', 'number', 'negation',
    'permission', 'ascii_period', 'other_punctuation', 'empty_body', 'exact_quote',
    'non_requirement', 'unrelated_stage', 'malformed_ref', 'malformed_proposals'])
def test_only_the_proven_terminal_semicolon_case_is_changed(case):
    raw, sent = response(), excerpts()
    item = raw['proposals'][0]
    evidence = item['scope_evidence'][0]
    if case == 'wrong_source': evidence['source_ref']['source_id'] = 'SRC-other'
    elif case == 'wrong_excerpt': evidence['source_ref']['excerpt_id'] = 'EX-other'
    elif case == 'pair_from_another_source': sent[0]['source_id'] = 'SRC-other'
    elif case == 'duplicate_match': sent[0]['text'] += TEXT
    elif case == 'duplicate_pair': sent.append(copy.deepcopy(sent[0]))
    elif case == 'internal_punctuation': evidence['quote'] = QUOTE.replace('，', '；')
    elif case == 'number': evidence['quote'] = QUOTE.replace('15', '30')
    elif case == 'negation': evidence['quote'] = QUOTE.replace('完整覆盖', '不完整覆盖')
    elif case == 'permission': evidence['quote'] = '所有角色可发布；' + QUOTE
    elif case == 'ascii_period': evidence['quote'] = QUOTE[:-1] + '.'
    elif case == 'other_punctuation': sent[0]['text'] = TEXT.replace('；', '，')
    elif case == 'empty_body': evidence['quote'] = '。'
    elif case == 'exact_quote': evidence['quote'] = QUOTE[:-1]
    elif case == 'non_requirement': item['kind'] = 'rule'
    elif case == 'unrelated_stage': raw['stage'] = 'review'
    elif case == 'malformed_ref': evidence['source_ref']['source_id'] = []
    else: raw['proposals'] = None
    snapshot = copy.deepcopy(raw)
    assert normalize_scope_quotes(raw, sent) == (snapshot, [])
    assert raw == snapshot


@pytest.mark.parametrize('stage', ['ingest', 'clarify', 'change'])
@pytest.mark.parametrize('mutation', [None, 'statement', 'behavior', 'source', 'quote_body'])
def test_repair_anchors_still_lock_business_content_and_source_identity(stage, mutation):
    raw, sent = response(), excerpts()
    raw['stage'] = stage
    fixed, _ = normalize_scope_quotes(raw, sent)
    # A second valid source with the same text is still a different identity.
    sent.append(dict(source_id='SRC-other', id='EX-other', text=TEXT))
    if mutation == 'statement': fixed['proposals'][0]['statement'] = '审计员也可以发布电价。'
    elif mutation == 'behavior': fixed['proposals'][0]['behavior']['flow'] = '重叠时允许发布。'
    elif mutation == 'source': fixed['proposals'][0]['scope_evidence'][0]['source_ref'] = dict(source_id='SRC-other', excerpt_id='EX-other')
    elif mutation == 'quote_body': fixed['proposals'][0]['scope_evidence'][0]['quote'] = '相邻时段合法，重叠时拒绝发布。'

    def check():
        if stage == 'ingest':
            preserve(business_anchor(raw, sent), fixed, sent)
        else:
            preserve_response(response_anchor(raw, sent, stage), fixed, sent, stage, original=raw)

    if mutation:
        with pytest.raises(Problem, match='格式修复改变'):
            check()
    else:
        check()


@pytest.mark.parametrize('schema_repair', [False, True])
def test_workflow_saves_candidate_and_both_rounds_raw_evidence(tmp_path, monkeypatch, schema_repair):
    original_outputs = []

    class HTTPFixture:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, *args, **kwargs):
            text = original_outputs[len(requests)]
            requests.append(kwargs['json'])
            return httpx.Response(200, json={'choices': [{'message': {'content': text}, 'finish_reason': 'stop'}],
                'usage': {'prompt_tokens': 1, 'completion_tokens': 2, 'total_tokens': 3}})

    requests = []
    monkeypatch.setattr(httpx, 'AsyncClient', HTTPFixture)

    async def scenario():
        store = Store(tmp_path)
        project = store.create('合成储能云平台')
        source = save_source(store, '云平台电价.txt', TEXT.encode())
        with store.edit(project['id'], 0, '合成来源') as (draft, _):
            draft['sources'] = [source]
            draft['grants'][origin(DEFAULT)] = {'source_ids': [source['id']]}
        ref = dict(source_id=source['id'], excerpt_id=source['excerpts'][0]['id'])
        valid = response(ref)
        if schema_repair:
            invalid = copy.deepcopy(valid)
            invalid['unexpected'] = 'format defect'
            original_outputs.append(json.dumps(invalid, ensure_ascii=False))
        original_outputs.append(json.dumps(valid, ensure_ascii=False))
        provider = Provider(tmp_path / '.env')
        # This fixture exercises runtime-1.1 scope-quote normalization and its
        # repair contract, not the production business-draft-v1 response shape.
        provider.business_draft_protocol = None
        provider.keys[origin(DEFAULT)] = 'synthetic-test-key'
        flow = Workflow(store, provider)
        run = flow.start(project['id'], store.get(project['id'])['revision'], 'ingest', '')
        await flow.tasks[run['id']]
        return store.get_record(project['id'], run['id']), store.get(project['id'])

    run, project = asyncio.run(scenario())
    assert run['result_applied'] and run['calls'] == 1 + schema_repair
    assert project['active_baseline_id'] is None
    assert len(project['items']) == 1 and project['items'][0]['selection_status'] == 'candidate'
    evidence = [json.loads(p.read_text('utf-8')) for p in (tmp_path / 'evidence/model-calls').glob('*.json')]
    assert {call['final_output'] for call in evidence} == set(original_outputs)
    assert all(call['response_normalizations'][0]['count'] == 1 for call in evidence)
    assert all(attempt['response_normalizations'][0]['count'] == 1 for attempt in run['attempts'])
    assert project['items'][0]['scope_evidence'][0]['quote'] == QUOTE[:-1] + '；'
