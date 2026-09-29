"""Isolated API/input provenance checks for business background packages."""
import copy
import json

from app.core import brief_hash, execution_hash
from app.provider import assemble, DEFAULT
from app.prd import verified_business_context
from app.business_context import selected_excerpts
from tests.test_runtime import client
from tests.test_business_context_bundle import bundle, raw


def setup(tmp_path):
    c, app = client(tmp_path)
    p = c.post('/api/projects', json={'name': '业务背景隔离验证'}).json()
    return c, app, p['id'], '/api/projects/' + p['id']


def upload(c, root, revision, value):
    return c.post(root + '/business-context', data={'expected_revision': str(revision)},
                  files={'file': ('business-context.json', raw(value), 'application/json')})


def choose(c, app, pid, root, sid, modules, confirmed=()):
    return c.post(root + '/business-context/' + sid + '/selection', json={
        'expected_revision': app.state.store.get(pid)['revision'],
        'module_ids': modules, 'confirmed_claim_ids': list(confirmed)})


def test_import_is_non_normative_deduplicates_and_rejects_identity_conflict(tmp_path):
    c, app, pid, root = setup(tmp_path)
    original = app.state.store.get(pid)
    response = upload(c, root, 0, bundle())
    assert response.status_code == 200, response.text
    source = response.json()
    assert source['business_active'] is False
    p = app.state.store.get(pid)
    assert not p['items'] and not p['questions']
    assert brief_hash(p) == brief_hash(original)
    assert source['business_selection'] == {'module_ids': [], 'confirmed_claim_ids': []}
    assert c.get(root + '/sources/' + source['id'] + '/file').content == raw(bundle())
    duplicate = upload(c, root, p['revision'], bundle())
    assert duplicate.status_code == 200 and duplicate.json()['id'] == source['id']
    assert app.state.store.get(pid) == p
    conflict = bundle(); conflict['overview'] = '不同内容'
    assert upload(c, root, p['revision'], conflict).status_code == 409
    assert upload(c, root, 0, bundle()).status_code == 409
    invalid = bundle(); invalid['approved'] = True
    assert upload(c, root, p['revision'], invalid).status_code == 400
    assert app.state.store.get(pid) == p
    assert len(list((tmp_path / 'sources').iterdir())) == 1


def test_selection_controls_actual_input_confirmation_and_version_switch(tmp_path):
    c, app, pid, root = setup(tmp_path)
    sid = upload(c, root, 0, bundle()).json()['id']
    before = app.state.store.get(pid)
    assert choose(c, app, pid, root, sid, ['ui'], ['c-other']).status_code == 400
    assert choose(c, app, pid, root, sid, ['ui']).status_code == 200
    p = app.state.store.get(pid)
    assert brief_hash(p) != brief_hash(before)
    assert not verified_business_context(p)
    messages, actual, omitted = assemble(p, 'ingest', '增加明确的批量结果反馈', DEFAULT, tmp_path)
    assert not omitted
    payload = json.loads(messages[1]['content'][0]['text'])
    assert {e['business_claim_id'] for e in actual} == {'c-ui','c-service','c-data'}
    assert 'other 原文' not in json.dumps(payload, ensure_ascii=False)
    assert '未经用户确认' in json.dumps(actual, ensure_ascii=False)
    assert choose(c, app, pid, root, sid, ['ui'], ['c-service']).status_code == 200
    confirmed = app.state.store.get(pid)
    assert list(verified_business_context(confirmed)) == [sid + '/c-service']
    assert confirmed['items'] == [] and confirmed.get('product_context') is None
    assert execution_hash(confirmed) != execution_hash(p)
    other = bundle(); other['bundle_id'] = 'sample-2'
    other['claims'][0]['text'] = '新版本页面观察'
    response = upload(c, root, confirmed['revision'], other)
    assert response.status_code == 200
    sid2 = response.json()['id']
    current = app.state.store.get(pid)
    assert brief_hash(current) == brief_hash(confirmed), 'import alone must not activate new version'
    assert choose(c, app, pid, root, sid2, ['ui']).status_code == 200
    switched = app.state.store.get(pid)
    old = next(s for s in switched['sources'] if s['id'] == sid)
    assert old['business_active'] is False
    assert old['business_selection'] == {'module_ids': ['ui'], 'confirmed_claim_ids': ['c-service']}
    assert selected_excerpts(old) == []
    assert not verified_business_context(switched), 'old confirmations must not migrate'
    sent = assemble(switched, 'ingest', '核对新版', DEFAULT, tmp_path)[1]
    assert {e['source_id'] for e in sent} == {sid2}
    assert brief_hash(switched) != brief_hash(confirmed)
    assert switched['items'] == []

    # Switching back restores the old version's explicit confirmations without migrating
    # them into the new version.
    assert choose(c, app, pid, root, sid, ['ui'], ['c-service']).status_code == 200
    returned = app.state.store.get(pid)
    old = next(s for s in returned['sources'] if s['id'] == sid)
    new = next(s for s in returned['sources'] if s['id'] == sid2)
    assert old['business_active'] is True and new['business_active'] is False
    assert old['business_selection']['confirmed_claim_ids'] == ['c-service']
    assert new['business_selection']['confirmed_claim_ids'] == []
    assert list(verified_business_context(returned)) == [sid + '/c-service']
    assert {e['source_id'] for e in assemble(returned, 'ingest', '', DEFAULT, tmp_path)[1]} == {sid}

    def toggle(target):
        revision = app.state.store.get(pid)['revision']
        response = c.post(root + '/sources/' + target + '/exclude',
                          json={'expected_revision': revision})
        assert response.status_code == 200, response.text

    toggle(sid2)
    toggle(sid2)
    after_inactive_restore = app.state.store.get(pid)
    assert next(s for s in after_inactive_restore['sources'] if s['id'] == sid2)['business_active'] is False
    assert list(verified_business_context(after_inactive_restore)) == [sid + '/c-service']

    toggle(sid)
    toggle(sid)
    after_active_restore = app.state.store.get(pid)
    assert next(s for s in after_active_restore['sources'] if s['id'] == sid)['business_active'] is True
    assert next(s for s in after_active_restore['sources'] if s['id'] == sid2)['business_active'] is False
    assert list(verified_business_context(after_active_restore)) == [sid + '/c-service']

    toggle(sid)
    assert choose(c, app, pid, root, sid2, ['ui']).status_code == 200
    toggle(sid)
    after_switch_then_restore = app.state.store.get(pid)
    assert next(s for s in after_switch_then_restore['sources'] if s['id'] == sid)['business_active'] is False
    assert next(s for s in after_switch_then_restore['sources'] if s['id'] == sid2)['business_active'] is True
    assert not verified_business_context(after_switch_then_restore)


def test_project_scope_and_specialized_source_boundary(tmp_path):
    c, app, pid, root = setup(tmp_path)
    sid = upload(c, root, 0, bundle()).json()['id']
    other = c.post('/api/projects', json={'name': '其它项目'}).json()
    assert c.post('/api/projects/'+other['id']+'/business-context/'+sid+'/selection',
        json={'expected_revision':0,'module_ids':['ui'],'confirmed_claim_ids':[]}).status_code == 404
    revision = app.state.store.get(pid)['revision']
    assert c.post(root+'/sources/'+sid+'/purpose',json={'expected_revision':revision,'purpose':'goal'}).status_code == 400
    assert c.post(root+'/sources/'+sid+'/retry',json={'expected_revision':revision}).status_code == 400
    assert choose(c, app, pid, root, sid, ['ui']).status_code == 200
    active = app.state.store.get(pid)
    assert c.post(root+'/sources/'+sid+'/exclude',json={'expected_revision':active['revision']}).status_code == 200
    excluded = app.state.store.get(pid)
    assert brief_hash(excluded) != brief_hash(active)
    assert choose(c, app, pid, root, sid, ['ui']).status_code == 409
    _, excerpts, _ = assemble(excluded, 'ingest', '', DEFAULT, tmp_path)
    assert excerpts == []
