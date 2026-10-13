"""Bulk adoption checks use isolated data and the real project transaction."""
import copy
import pytest

from app.core import digest
from app.requirements import behavior_fingerprint, item_content
from tests.helpers import prepared
from tests.test_project_summary_repair import client


def case(tmp_path):
    browser, app = client(tmp_path)
    p = prepared(app.state.store)
    with app.state.store.edit(p['id'], p['revision'], '合成批量核对项目') as (draft, _):
        first = draft['items'][0]
        first.update(selection_status='candidate', behavior_review_required=True,
                     behavior={'flow': '管理员维护电价时段。'})
        second = copy.deepcopy(first)
        second.update(id='REQ-0002', title='第二条需求', selection_status='candidate')
        second['behavior_review'] = {'content_hash': behavior_fingerprint(second),
                                     'actor': 'authenticated_local_user'}
        draft['items'].append(second)
        draft['items'].append(dict(id='REQ-0003', kind='requirement', title='未选择的需求',
            statement='管理员能够维护电价时段。', applies_to='to_be',
            epistemic_status='reported', source_refs=copy.deepcopy(first['source_refs']),
            related_refs=[], selection_status='candidate'))
    return browser, app, p['id']


def request(browser, pid, path, ids, **extra):
    p = browser.get('/api/projects/' + pid).json()
    return browser.post('/api/projects/' + pid + path,
                        json=dict(expected_revision=p['revision'], item_ids=ids, **extra))


def snapshot(app, pid):
    p = app.state.store.get(pid)
    return p, app.state.store.records(pid, 'audit'), app.state.store.records(pid, 'requirement_change')


def test_prepare_read_only_and_mixed_success(tmp_path):
    browser, app, pid = case(tmp_path)
    before = snapshot(app, pid)
    checked = request(browser, pid, '/item-adoption-review', ['REQ-0001', 'REQ-0002'])
    assert checked.status_code == 200, checked.text
    body = checked.json()
    assert body['blockers'] == []
    assert [i['review_required'] for i in body['items']] == [True, False]
    assert body['items'][0]['sources'][0]['text']
    assert snapshot(app, pid) == before
    hashes = {row['item']['id']: row['content_hash'] for row in body['items']}
    done = request(browser, pid, '/item-adoptions', list(hashes),
                   content_hashes=hashes, review_confirmed=True)
    assert done.status_code == 200, done.text
    after = app.state.store.get(pid)
    assert after['revision'] == before[0]['revision'] + 1
    assert [i['selection_status'] for i in after['items'] if i['kind'] == 'requirement'] == [
        'selected', 'selected', 'candidate']
    assert after['items'][0]['behavior_review']['actor'] == 'authenticated_local_user'
    assert next(i for i in after['items'] if i['id'] == 'REQ-0002')['behavior_review'] == next(
        i for i in before[0]['items'] if i['id'] == 'REQ-0002')['behavior_review']
    assert [i for i in after['items'] if i['id'] not in hashes] == [
        i for i in before[0]['items'] if i['id'] not in hashes]
    assert after.get('product_context') == before[0].get('product_context')


def test_ack_revision_and_hash_fail_without_mutation(tmp_path):
    browser, app, pid = case(tmp_path)
    ids = ['REQ-0001', 'REQ-0002']
    p = app.state.store.get(pid)
    hashes = {i['id']: digest(item_content(i)) for i in p['items'] if i['id'] in ids}
    before = snapshot(app, pid)
    for extra in [dict(content_hashes=hashes, review_confirmed=False),
                  dict(content_hashes={**hashes, 'REQ-0002': 'wrong'}, review_confirmed=True)]:
        result = request(browser, pid, '/item-adoptions', ids, **extra)
        assert result.status_code == 409
        assert snapshot(app, pid) == before
    stale = browser.post('/api/projects/' + pid + '/item-adoptions', json=dict(
        expected_revision=p['revision'] - 1, item_ids=ids,
        content_hashes=hashes, review_confirmed=True))
    assert stale.status_code == 409 and stale.json()['code'] == 'STALE_REVISION'
    assert snapshot(app, pid) == before


@pytest.mark.parametrize('edit', [
    {'statement': '  '}, {'applies_to': 'reference'},
    {'target_item_id': 'REQ-0001'}, {'source_refs': []},
    {'source_refs': [{'source_id': 'missing', 'excerpt_id': 'EX-0001'}]},
])
def test_invalid_sibling_and_references_are_atomic(tmp_path, edit):
    browser, app, pid = case(tmp_path)
    p = app.state.store.get(pid)
    good = digest(item_content(p['items'][0]))
    iid = 'REQ-0003'
    with app.state.store.edit(pid, p['revision'], '合成无效条目') as (draft, _):
        draft['items'][-1].update(edit)
    before = snapshot(app, pid)
    blocked = request(browser, pid, '/item-adoption-review', ['REQ-0001', iid])
    assert blocked.status_code == 200 and blocked.json()['blockers']
    assert snapshot(app, pid) == before
    hashes = {'REQ-0001': good, iid: digest(item_content(before[0]['items'][-1]))}
    failed = request(browser, pid, '/item-adoptions', list(hashes),
                     content_hashes=hashes, review_confirmed=True)
    assert failed.status_code in (400, 404, 409)
    assert snapshot(app, pid) == before
    before = snapshot(app, pid)
    duplicate = request(browser, pid, '/item-adoptions', ['REQ-0001', 'REQ-0001'],
                        content_hashes={'REQ-0001': good}, review_confirmed=True)
    assert duplicate.status_code == 409 and snapshot(app, pid) == before


def test_source_fidelity_blocks_whole_batch(tmp_path):
    browser, app, pid = case(tmp_path)
    p = app.state.store.get(pid)
    with app.state.store.edit(pid, p['revision'], '合成无依据的排他权限') as (draft, _):
        draft['items'][-1]['statement'] = '只有管理员可以查看拒绝原因。'
    before = snapshot(app, pid)
    ids = ['REQ-0001', 'REQ-0003']
    checked = request(browser, pid, '/item-adoption-review', ids)
    assert checked.status_code == 200
    assert checked.json()['blockers'][0]['id'] == 'REQ-0003'
    hashes = {i['id']: digest(item_content(i)) for i in before[0]['items'] if i['id'] in ids}
    result = request(browser, pid, '/item-adoptions', ids,
                     content_hashes=hashes, review_confirmed=True)
    assert result.status_code == 409 and result.json()['code'] == 'SEMANTIC_BLOCKED'
    assert snapshot(app, pid) == before
