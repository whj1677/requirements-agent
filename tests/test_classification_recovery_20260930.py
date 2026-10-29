"""Synthetic counterexamples for evidence-derived labels and preserved business text."""
import asyncio
import copy

import pytest

from app.core import Problem
from app import ingest
from app.contracts import validate_response
from app.provider import DEFAULT, origin
from app.sources import save_source
from app.store import Store
from app.understanding import normalize_classification_labels
from app.workflow import Workflow
from tests.test_runtime import FakeProvider, empty_ingest
from tests.helpers import EXAMPLES, prepared
from tests.product_flow_helpers import check_prepared


def response(excerpts):
    value = empty_ingest()
    refs = [dict(source_id=e['source_id'], excerpt_id=e['id']) for e in excerpts]
    value['proposals'] = [dict(temp_id='TMP-batch', action='add', target_item_id=None,
        kind='requirement', title='现有列表增加批量导出', statement='保持单条导出，在现有列表增加批量导出。',
        applies_to='to_be', epistemic_status='reported', related_refs=[], source_refs=refs,
        change_type='new', scope_evidence=[dict(state=state, quote=e['text'], source_ref=ref)
            for state, e, ref in zip(('current', 'change'), excerpts, refs)])]
    value['used_source_refs'] = refs
    return value


def excerpts():
    return [dict(id='EX-current', source_id='SRC-fixture', text='现有列表可导出单条报告。'),
            dict(id='EX-change', source_id='SRC-fixture', text='本期在列表增加批量导出。')]


def test_redundant_label_corrected_without_changing_business_or_source():
    original = response(excerpts())
    before = copy.deepcopy(original)
    normalized, changes = normalize_classification_labels(original, excerpts())
    assert original == before
    assert changes == [dict(temp_id='TMP-batch', original='new', derived='modified')]
    assert normalized['proposals'][0]['change_type'] == 'modified'
    normalized['proposals'][0]['change_type'] = 'new'
    assert normalized == before  # No quote, identity, statement, question or scope edits.


@pytest.mark.parametrize('fault', ['fabricated_quote', 'foreign_source', 'preservation_mixture', 'pure_preservation'])
def test_invalid_or_conflicting_evidence_remains_blocked(fault):
    material = excerpts()
    value = response(material)
    support = value['proposals'][0]['scope_evidence']
    if fault == 'fabricated_quote':
        support[1]['quote'] = '材料未给出的新增业务'
    elif fault == 'foreign_source':
        support[1]['source_ref']['source_id'] = 'SRC-other'
    elif fault == 'preservation_mixture':
        support.append(dict(state='preserve', quote=material[0]['text'], source_ref=support[0]['source_ref']))
    else:
        material[1]['text'] = '沿用原有权限，不改动。'
        support[1]['quote'] = material[1]['text']
    normalized, changes = normalize_classification_labels(value, material)
    assert changes == [] and normalized == value
    with pytest.raises(Problem):
        validate_response(normalized, 'ingest', dict(items=[], questions=[], ui=None), material)


def test_format_repair_still_cannot_rewrite_statement_or_drop_evidence():
    value = response(excerpts())
    anchor = ingest.business_anchor(value, excerpts())
    fixed, _ = normalize_classification_labels(value, excerpts())
    ingest.preserve(anchor, fixed, excerpts())
    fixed['proposals'][0]['statement'] = '允许跨租户导出，无需权限。'
    with pytest.raises(Problem, match='业务内容'):
        ingest.preserve(anchor, fixed, excerpts())
    fixed, _ = normalize_classification_labels(value, excerpts())
    fixed['proposals'][0]['scope_evidence'].pop(0)
    with pytest.raises(Problem):
        ingest.preserve(anchor, fixed, excerpts())


@pytest.mark.parametrize('format_repair', [False, True])
@pytest.mark.parametrize('stage', ['ingest', 'clarify', 'change'])
def test_workflow_recovers_label_and_only_creates_candidates(tmp_path, format_repair, stage):
    async def scenario():
        store = Store(tmp_path)
        project = (store.create('合成分类恢复') if stage == 'ingest' else prepared(store))
        if stage != 'ingest':
            project = check_prepared(store, project['id'], through=2)
        source = save_source(store, '分类来源.txt', '现有列表可导出单条报告。\n本期在列表增加批量导出。'.encode(), 'goal')
        with store.edit(project['id'], project['revision'], '合成材料授权') as (draft, _):
            draft['sources'].append(source)
            draft['grants'][origin(DEFAULT)] = {'source_ids': [s['id'] for s in draft['sources']]}
        project = store.get(project['id'])
        valid = response(source['excerpts'])
        if stage != 'ingest':
            valid.pop('product_context_proposal')
            valid['stage'] = stage
            valid['result'] = copy.deepcopy(EXAMPLES[stage]['result'])
            valid['proposals'][0].update(action='revise', target_item_id='REQ-0001')
            if stage == 'change':
                valid['result']['changes'] = [dict(operation='revise', target_ref='REQ-0001',
                    proposal_ref='TMP-batch', rationale='合成来源明确给出现有列表本期改动。',
                    source_refs=copy.deepcopy(valid['used_source_refs']))]
        original = copy.deepcopy(valid)
        outputs = [valid]
        if format_repair:
            malformed = copy.deepcopy(valid)
            malformed['unexpected'] = 'schema fault'
            outputs.insert(0, malformed)
        provider = FakeProvider(outputs)
        workflow = Workflow(store, provider)
        store.setting('model', dict(DEFAULT, max_calls=None))
        before = copy.deepcopy(project)
        run = workflow.start(project['id'], project['revision'], stage, '')
        await workflow.tasks[run['id']]
        return store.get_record(project['id'], run['id']), store.get(project['id']), original, valid, before
    run, project, original, valid, before = asyncio.run(scenario())
    assert run['error'] is None and run['result_applied']
    assert run['calls'] == (2 if format_repair else 1)
    assert valid == original
    candidate = next(i for i in project['items'] if i['statement'] == original['proposals'][0]['statement'])
    assert candidate['change_type'] == 'modified'
    assert candidate['scope_evidence'] == original['proposals'][0]['scope_evidence']
    assert candidate['selection_status'] == 'candidate'
    assert project['questions'] == before['questions']
    for item in before['items']:
        assert next(i for i in project['items'] if i['id'] == item['id']) == item
    assert project['active_baseline_id'] is None
    assert run['classification_corrections'][0]['temp_id'] == 'TMP-batch'
