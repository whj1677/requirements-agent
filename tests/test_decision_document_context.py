"""Frozen document decisions and exact implementation evidence; zero model calls."""
import copy
import json

import pytest

from app.answer_implementation import implementation_records
from app.core import brief_hash, dumps
from app.document_reader import export_readiness, reader_document
from app.prd import compile_plan
from app.provider import DEFAULT, assemble, origin, review_documents, review_unknown_evidence
from app.store import Store
from app.understanding import applied_targets
from app.workflow import apply_response
from tests.helpers import prepared


def project(tmp_path):
    p = prepared(Store(tmp_path))
    p['documents'] = {}
    p['grants'][origin(DEFAULT)] = {'source_ids': ['SRC-0001']}
    return p


def freeze(p):
    """Use the normal offline compiler and snapshot path, without a provider."""
    proposal = dict(plan_version='2', sections=[dict(section_key='function', context_refs=[],
        normative_refs=['REQ-0001', 'RULE-0001', 'AC-0001'], discussion_refs=[])])
    apply_response(p, compile_plan(proposal, p, 'prd'))
    return p['documents']['prd']


def expressed_answer(p):
    item = p['items'][0]
    text = '管理员保存首尾相接的时段，允许保存。'
    item.setdefault('behavior', {})['flow'] = text
    question = p['questions'][0]
    question.update(status='answered', answer='允许保存首尾相接的时段。',
        understanding_status='applied', answer_implementation_contract=2,
        answer_source_refs=[dict(source_id='SRC-0001', excerpt_id='EX-0001')],
        applied_item_ids=[item['id']], applied_requirement_ids=[item['id']])
    p['sources'][0]['excerpts'][0]['text'] += '\n允许保存首尾相接的时段。'
    question['answer_applications'] = implementation_records(p, question,
        {item['id']: [dict(field='behavior.flow', quote=text)]}, method='offline_human_review')
    return question


def test_review_uses_same_version_frozen_clauses_and_answers_while_current_differs(tmp_path):
    p = project(tmp_path)
    p['items'][0].setdefault('behavior', {})['flow'] = '相接边界的保存行为资料未说明。'
    q = p['questions'][0]
    q.update(status='answered', answer='旧版回答：相接允许保存。', understanding_status='applied')
    artifact = freeze(p)
    frozen = copy.deepcopy(artifact)
    p['items'][0]['statement'] = '新版规范：相接边界拒绝保存。'
    p['items'][0]['behavior']['flow'] = '新版保存时拒绝相接边界。'
    p['questions'][0]['answer'] = '新版回答：相接边界拒绝保存。'
    p['revision'] += 1
    before = copy.deepcopy(p)

    messages, _, _ = assemble(p, 'review', '', DEFAULT, tmp_path)
    context = json.loads(messages[1]['content'][0]['text'])
    doc = context['documents']['prd']

    assert p == before and p['documents']['prd'] == frozen
    assert doc['id'] == artifact['id'] and doc['draft_revision'] == artifact['draft_revision']
    assert doc['current'] is False and doc['snapshot_recorded'] is True
    assert doc['clause_snapshot'][0]['statement'] == frozen['item_snapshot'][0]['statement']
    assert doc['decision_snapshot'][0]['answer'] == '旧版回答：相接允许保存。'
    assert '相接边界的保存行为资料未说明。' in dumps(doc['content'])
    assert '新版规范' not in dumps(doc) and '新版回答' not in dumps(doc)
    assert context['items'][0]['statement'] == p['items'][0]['statement']
    assert context['questions'][0]['answer'] == p['questions'][0]['answer']
    assert '同版本冻结快照' in doc['notice'] and 'applied' in doc['notice']
    blocks = [block for section in doc['content']['sections'] for block in section['blocks']]
    assert all(block['source_location'].startswith(artifact['id'] + '.') for block in blocks)


def test_review_current_document_still_exposes_saved_answer_for_prose_check(tmp_path):
    p = project(tmp_path)
    expressed_answer(p)
    artifact = freeze(p)
    before = copy.deepcopy(p)

    doc = review_documents(p)['prd']

    assert p == before and doc['current'] is True
    assert doc['decision_snapshot'][0]['answer'] == p['questions'][0]['answer']
    assert doc['decision_snapshot'][0]['answer_applications'] == p['questions'][0]['answer_applications']
    assert '管理员保存首尾相接的时段，允许保存。' in dumps(doc['content'])
    assert doc['clause_snapshot'][0]['behavior'] == artifact['item_snapshot'][0]['behavior']


def test_review_historical_source_without_hash_reports_verification_limit(tmp_path):
    p = project(tmp_path)
    artifact = freeze(p)
    artifact['source_snapshot'][0].pop('sha256')
    before = copy.deepcopy(p)

    source, = review_documents(p)['prd']['source_evidence']

    assert p == before
    assert source['source_id'] == 'SRC-0001'
    assert source['version_status'] == 'historical_hash_not_recorded'
    assert source['excerpts'][0]['id'] == 'EX-0001'
    assert '不能声称已验证该历史来源版本' in source['notice']


@pytest.mark.parametrize('change', ['hash', 'excluded', 'superseded'])
def test_review_does_not_supply_changed_or_unavailable_current_source_as_old_evidence(tmp_path, change):
    p = project(tmp_path)
    freeze(p)
    source = p['sources'][0]
    if change == 'hash':
        source['sha256'] = 'new-source-version'
        source['excerpts'][0]['text'] = '新版本不能冒充旧文档的来源。'
    elif change == 'excluded':
        source['excluded'] = True
    else:
        source['superseded_by'] = 'SRC-NEW'

    evidence, = review_documents(p)['prd']['source_evidence']

    assert evidence['excerpts'] == []
    assert evidence['version_status'] == ('changed' if change == 'hash' else 'matched')
    if change != 'hash':
        assert evidence['available_for_current_review'] is False
    assert '须报告资料核对限制' in evidence['notice']


def test_reader_keeps_answered_but_unimplemented_decision_without_raw_answer(tmp_path):
    p = project(tmp_path)
    q = p['questions'][0]
    q.update(status='answered', answer='私人原话标记：相接允许保存。', understanding_status='applied',
             answer_implementation_contract=2, applied_item_ids=['REQ-0001'], answer_applications={})
    artifact = freeze(p)
    before = copy.deepcopy(artifact)

    text = dumps(reader_document(artifact))

    assert q['question'] in text and '已回答，正文及关联验收仍待落实' in text
    assert '私人原话标记' not in text and q['answer'] not in text
    assert artifact == before and artifact['question_snapshot'][0]['answer'] == q['answer']


def test_reader_removes_implemented_decision_from_pending_list_preserving_product_words(tmp_path):
    p = project(tmp_path)
    q = expressed_answer(p)
    artifact = freeze(p)

    text = dumps(reader_document(artifact))

    assert q['question'] not in text and '已回答，正文及关联验收仍待落实' not in text
    assert p['items'][0]['behavior']['flow'] in text
    assert artifact['question_snapshot'][0]['answer'] == q['answer']


def test_export_accepts_current_v2_location_evidence(tmp_path):
    p = project(tmp_path)
    q = expressed_answer(p)
    freeze(p)
    before = copy.deepcopy(p)

    readiness = export_readiness(p, 'prd')

    assert p == before and applied_targets(p, q) == {'REQ-0001'}
    assert readiness['ready'], readiness


@pytest.mark.parametrize('change', ['missing_witness', 'clause', 'answer', 'question', 'legacy_record'])
def test_export_does_not_trust_applied_status_when_v2_application_is_invalid(tmp_path, change):
    p = project(tmp_path)
    q = expressed_answer(p)
    record = q['answer_applications']['REQ-0001']
    if change == 'missing_witness':
        record['witnesses'] = []
    elif change == 'clause':
        p['items'][0]['behavior']['flow'] = '相接边界仍待决定。'
    elif change == 'answer':
        q['answer'] = '相接边界拒绝保存。'
    elif change == 'question':
        q['question'] = '不同场景的相接边界如何处理？'
    else:
        record.pop('contract_version')
        record.pop('witnesses')
    freeze(p)  # A current document alone cannot make the saved application valid.
    assert p['documents']['prd']['brief_hash'] == brief_hash(p)

    readiness = export_readiness(p, 'prd')

    assert q['understanding_status'] == 'applied' and not applied_targets(p, q)
    assert not readiness['ready'] and readiness['problems'][0]['state'] == 'answer_pending'
    assert readiness['problems'][0]['question_id'] == q['id']
    assert readiness['problems'][0]['action'] == 'apply_answer'
    assert not any(issue['code'] == 'STALE_REVISION' for issue in readiness['issues'])


def unknown_review(text, *, rejected=False, revision=1):
    row = dict(requirement_id='REQ-0001', question_temp_ids=['Q-existing'], field_evidence=dict(
        behavior={'exceptions': dict(basis='unknown', text=text,
            source_refs=[dict(source_id='SRC-0001', excerpt_id='EX-0001')])}))
    return dict(input_revision=revision, accepted=[] if rejected else [row],
                rejected=[row] if rejected else [])


def test_latest_rejected_draft_never_reuses_older_successful_unknown_evidence(tmp_path):
    p = project(tmp_path)
    p['business_draft_reviews'] = [unknown_review('旧成功起草的未知不得复用。'),
        dict(input_revision=2, accepted=[], rejected=[dict(requirement_id='REQ-0001',
             reason='本轮来源无效', raw={'behavior': {'exceptions': '未经来源核对'}})])]
    before = copy.deepcopy(p)

    evidence = review_unknown_evidence(p)

    assert evidence == [] and p == before


def test_latest_draft_unknown_preserves_field_source_and_question_clues_for_review(tmp_path):
    p = project(tmp_path)
    p['business_draft_reviews'] = [unknown_review('旧未知。'),
        unknown_review('保存失败后的编辑内容是否保留，仍需核对。', revision=3)]
    before = copy.deepcopy(p)

    evidence, = review_unknown_evidence(p)

    assert p == before and evidence['input_revision'] == 3
    assert evidence['item_id'] == 'REQ-0001' and evidence['question_ids'] == ['Q-existing']
    assert evidence['unknown_fields'] == [dict(field='behavior.exceptions',
        **p['business_draft_reviews'][-1]['accepted'][0]['field_evidence']['behavior']['exceptions'])]
    assert '旧未知' not in dumps(evidence)
    assert '须与当前条款、已保存答案、范围决定核对' in evidence['notice']
