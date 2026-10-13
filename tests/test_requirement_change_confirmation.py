"""Human change classification is part of the displayed, atomic row review."""
import copy

import pytest

from app.core import digest
from app.product_flow import blockers
from app.requirement_confirmation import confirmation_current
from app.requirements import behavior_fingerprint, behavior_review_current
from app.store import Store
from tests.test_decision_review import api
from tests.test_requirement_confirmation import setup, payload, preview, commit


def pending_change(store):
    p = setup(store)
    with store.edit(p['id'], p['revision'], '合成储能云改动性质待核对') as (p, _):
        p['sources'][0]['excerpts'][0]['text'] = '两名值班人员协作处理电站告警，处理记录可查看；改动性质由产品经理核对。'
        p['items'][0].update(change_type='unspecified', classification_reason='材料未说明现有系统是否已有本功能。')
        p['items'][2]['statement'] = '值班人员处理电站告警后，可以查看本次处理记录。'
        p['questions'][0].update(question='其他值班人员能否修改这条处理记录？', blocking=True)
    return store.get(p['id'])


def test_unknown_to_human_new_confirms_requirement_and_acceptance_once(tmp_path):
    store = Store(tmp_path)
    p = pending_change(store)
    assert 'change_type_unknown' in {issue['code'] for issue in blockers(p, 2)}
    with api(store) as client:
        viewed = preview(client, p, change_type='new')
        assert store.get(p['id']) == p
        assert viewed['before']['change_type'] == 'unspecified'
        assert viewed['after']['change_type'] == 'new'
        assert viewed['preview_hash'] == digest({key: value for key, value in viewed.items()
                                               if key not in ('preview_id', 'preview_hash')})
        response = commit(client, p, viewed)
        assert response.status_code == 200, response.text
        final = response.json()['project']
        req, acceptance = final['items'][0], final['items'][2]
        assert req['change_type'] == 'new'
        assert req['selection_status'] == acceptance['selection_status'] == 'selected'
        assert req['source_refs'] == p['items'][0]['source_refs']
        assert req['classification_reason'] == p['items'][0]['classification_reason']
        assert final['sources'] == p['sources']  # Classification is not fabricated source prose.
        assert req['behavior_review']['actor'] == 'authenticated_local_user'
        assert req['behavior_review']['content_hash'] == behavior_fingerprint(req)
        assert behavior_review_current(req) and confirmation_current(final, req)
        assert 'change_type_unknown' not in {issue['code'] for issue in blockers(final, 2)}
        assert 'question_open' in {issue['code'] for issue in blockers(final, 3)}
        repeat = commit(client, p, viewed)
        assert repeat.status_code == 200 and repeat.json()['repeated']
        assert store.get(p['id']) == final


@pytest.mark.parametrize('change_type', ['new', 'modified', 'preserved', 'unspecified'])
def test_only_explicit_valid_to_be_classification_is_written(tmp_path, change_type):
    store = Store(tmp_path)
    p = pending_change(store)
    with api(store) as client:
        response = commit(client, p, preview(client, p, change_type=change_type))
        assert response.status_code == 200, response.text
        final = response.json()['project']
        assert final['items'][0]['change_type'] == change_type
        assert ('change_type_unknown' in {issue['code'] for issue in blockers(final, 2)}) == (change_type == 'unspecified')


@pytest.mark.parametrize('prior', ['unspecified', 'modified', None])
def test_legacy_payload_preserves_original_classification_even_with_candidate(tmp_path, prior):
    store = Store(tmp_path)
    p = pending_change(store)
    with store.edit(p['id'], p['revision'], '合成分类不同的AI候选') as (p, _):
        if prior is None:
            p['items'][0].pop('change_type')
        else:
            p['items'][0]['change_type'] = prior
        candidate = copy.deepcopy(p['items'][0])
        candidate.update(id='REQ-CANDIDATE', target_item_id='REQ-0001', action='revise', change_type='new')
        p['items'].append(candidate)
    p = store.get(p['id'])
    with api(store) as client:
        viewed = preview(client, p, candidate_id='REQ-CANDIDATE')
        assert viewed['candidate']['change_type'] == 'new'
        assert viewed['after'].get('change_type') == prior
        response = commit(client, p, viewed)
        assert response.status_code == 200, response.text
        req = response.json()['project']['items'][0]
        assert req.get('change_type') == prior
        assert ('change_type' in req) == (prior is not None)


def test_explicit_candidate_classification_keeps_ai_provenance(tmp_path):
    store = Store(tmp_path)
    p = pending_change(store)
    with store.edit(p['id'], p['revision'], '合成带独立来源的AI候选') as (p, _):
        source = copy.deepcopy(p['sources'][0])
        source.update(id='SRC-CANDIDATE', sha256='candidate-source')
        source['excerpts'] = [dict(source['excerpts'][0], id='EX-CANDIDATE', source_id=source['id'])]
        p['sources'].append(source)
        candidate = copy.deepcopy(p['items'][0])
        candidate.update(id='REQ-CANDIDATE', target_item_id='REQ-0001', action='revise',
                         change_type='modified', source_refs=[dict(source_id=source['id'], excerpt_id='EX-CANDIDATE')])
        p['items'].append(candidate)
    p = store.get(p['id'])
    with api(store) as client:
        viewed = preview(client, p, candidate_id='REQ-CANDIDATE', change_type='modified')
        response = commit(client, p, viewed)
        assert response.status_code == 200, response.text
        final = response.json()['project']
        req = final['items'][0]
        assert req['change_type'] == 'modified'
        assert req['source_refs'] == p['items'][0]['source_refs'] + p['items'][-1]['source_refs']
        assert final['sources'] == p['sources']
        assert req['confirmed_revisions'][0]['source_refs'] == p['items'][-1]['source_refs']
        assert confirmation_current(final, req)


@pytest.mark.parametrize('invalid', ['existing', '', 'change', None, 1, True, {}, []])
def test_invalid_change_type_rejected_without_project_or_source_mutation(tmp_path, invalid):
    store = Store(tmp_path)
    p = pending_change(store)
    with api(store) as client:
        response = client.post(f'/api/projects/{p["id"]}/requirement-confirmation/preview',
                               json=payload(p, change_type=invalid))
        assert response.status_code == 400 and response.json()['code'] == 'CONTEXT_INVALID'
        assert store.get(p['id']) == p
        assert not store.records(p['id'], 'requirement_confirmation_preview')


@pytest.mark.parametrize('mutation', ['stale', 'hash', 'commit_field', 'stored_preview'])
def test_change_type_preview_cannot_be_reused_stale_or_tampered(tmp_path, mutation):
    store = Store(tmp_path)
    p = pending_change(store)
    with api(store) as client:
        viewed = preview(client, p, change_type='new')
        extra = {}
        if mutation == 'stale':
            with store.edit(p['id'], p['revision'], '另一个页面已选择改动性质') as (fresh, _):
                fresh['items'][0]['change_type'] = 'preserved'
        elif mutation == 'hash':
            extra['preview_hash'] = 'changed-hash'
        elif mutation == 'commit_field':
            extra['change_type'] = 'modified'
        else:
            changed = dict(viewed['after'], change_type='modified')
            store.update_record(p['id'], viewed['preview_id'], 'requirement_confirmation_preview', after=changed)
        before = store.get(p['id'])
        response = commit(client, p, viewed, **extra)
        assert response.status_code in (400, 409), response.text
        assert store.get(p['id']) == before
        assert not store.records(p['id'], 'requirement_confirmation_receipt')
