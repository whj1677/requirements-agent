"""Scope applicability is a human input and never an AI adoption shortcut."""
from fastapi.testclient import TestClient

from app.main import create_app
from app.core import digest, brief_hash, execution_hash
from app.business_context import selection_identity
from app.prd import verified_context
from app.product_flow import checkpoint, fingerprint, status, SCOPE
from tests.helpers import prepared


def case(tmp_path):
    app = create_app(tmp_path, access_token='scope-test', env_path=tmp_path / '.env')
    client = TestClient(app)
    client.headers['Origin'] = 'http://testserver'
    client.headers['X-CSRF-Token'] = client.post('/api/session/login', json={'key': 'scope-test'}).json()['csrf']
    project = prepared(app.state.store)
    root = '/api/projects/' + project['id']
    with app.state.store.edit(project['id'], project['revision'], 'scope fixture') as (draft, _):
        draft['intake'] = {'text': '原始诉求', 'platform': '', 'preserved': ''}
        checkpoint(draft, 1, status(draft)[0]['content_hash'])
        draft['items'][0]['change_type'] = 'new'
    return client, app, root


def save(client, root, values=None, profile=None, states=None, ids=None):
    p = client.get(root).json()
    body = {'expected_revision': p['revision'], 'values': values or {}}
    if profile is not None:
        body['scope_profile'] = profile
    if states is not None:
        body['field_states'] = states
    if ids is not None:
        body['scope_ids'] = ids
    response = client.post(root + '/product-context', json=body)
    assert response.status_code == 200, response.text
    return client.get(root).json()


def essentials():
    return {'intent': '新增云资源申请', 'users': '申请人', 'change_scope': '本期新增申请入口'}


def test_new_product_and_independent_module_need_no_current_or_preserve(tmp_path):
    for scenario, dependency in [('new_product', 'unknown'), ('new_module', 'independent')]:
        client, app, root = case(tmp_path / scenario)
        p = save(client, root, essentials(), {'scenario': scenario, 'dependencies': dependency},
                 {'current_state': {'status': 'not_applicable'},
                  'preserve_scope': {'status': 'not_applicable'}}, ['REQ-0001'])
        assert p['scope_field_rules']['current_state']['required'] is False
        assert p['scope_field_rules']['priority']['required'] is False
        assert p['product_flow'][1]['missing'] == []
        checked = client.post(root + '/stage-checks/2', json={
            'expected_revision': p['revision'], 'expected_hash': p['product_flow'][1]['content_hash']})
        assert checked.status_code == 200, checked.text


def test_shared_or_modified_requires_current_and_explicit_preservation(tmp_path):
    for scenario, dependency in [('new_module', 'shared'), ('new_module', 'unknown'),
                                 ('modify_existing', 'independent'), ('unspecified', 'unknown')]:
        client, app, root = case(tmp_path / (scenario + dependency))
        p = save(client, root, essentials(), {'scenario': scenario, 'dependencies': dependency},
                 {'current_state': {'status': 'unknown'}, 'preserve_scope': {'status': 'none'}}, ['REQ-0001'])
        assert p['scope_field_rules']['current_state']['required']
        assert any('当前页面与流程' in issue for issue in p['product_flow'][1]['missing'])
        assert not any('保持不变' in issue for issue in p['product_flow'][1]['missing'])
        p = save(client, root, states={'current_state': {'status': 'none'}})
        assert any('当前页面与流程' in issue for issue in p['product_flow'][1]['missing'])


def test_core_values_and_real_scope_ids_cannot_be_bypassed(tmp_path):
    client, app, root = case(tmp_path)
    profile = {'scenario': 'new_product', 'dependencies': 'unknown'}
    empty = save(client, root, profile=profile, ids=['REQ-0001'])
    assert all(any(label in issue for issue in empty['product_flow'][1]['missing'])
               for label in ('本次改动意图', '目标使用者', '本期新增或修改'))
    p = save(client, root, {'intent': '无', 'change_scope': '新增入口'}, profile,
             {'users': {'status': 'not_applicable'}}, ['REQ-0001'])
    assert any('目标使用者' in issue for issue in p['product_flow'][1]['missing'])
    p = save(client, root, {'users': '申请人'}, states={'users': {'status': 'known'}}, ids=[])
    assert any('至少一条' in issue for issue in p['product_flow'][1]['missing'])
    bad = client.post(root + '/product-context', json={'expected_revision': p['revision'],
        'values': {}, 'scope_ids': ['REQ-fake']})
    assert bad.status_code == 400 and bad.json()['code'] == 'REFERENCE_INVALID'


def test_direct_keeps_literal_source_and_profile_without_scope_adoption(tmp_path):
    app = create_app(tmp_path, access_token='scope-test', env_path=tmp_path / '.env')
    client = TestClient(app)
    client.headers['Origin'] = 'http://testserver'
    client.headers['X-CSRF-Token'] = client.post('/api/session/login', json={'key': 'scope-test'}).json()['csrf']
    p = client.post('/api/projects', json={'name': '直填项目'}).json()
    root = '/api/projects/' + p['id']
    response = client.post(root + '/intake/direct', json={'expected_revision': p['revision'],
        'values': {'intent': '全新申请工具'},
        'scope_profile': {'scenario': 'new_product', 'dependencies': 'unknown'},
        'field_states': {'current_state': {'status': 'not_applicable'}}})
    assert response.status_code == 200, response.text
    p = client.get(root).json()
    assert p['sources'][0]['origin'] == 'direct_intake'
    assert '全新申请工具' in p['sources'][0]['excerpts'][0]['text']
    assert p['scope_profile']['scenario'] == 'new_product'
    assert p['context_field_states']['current_state']['status'] == 'not_applicable'
    assert p['product_flow'][0]['complete'] and not p['product_flow'][1]['complete']
    assert p['items'] == []
    summary = next(row for row in client.get('/api/projects').json() if row['id'] == p['id'])
    assert summary['flow_current_title'] == '核对现状、价值与改动范围'


def test_profile_and_field_state_change_invalidate_old_scope_check(tmp_path):
    client, app, root = case(tmp_path)
    p = save(client, root, essentials(), {'scenario': 'new_product', 'dependencies': 'unknown'},
             ids=['REQ-0001'])
    response = client.post(root + '/stage-checks/2', json={
        'expected_revision': p['revision'], 'expected_hash': p['product_flow'][1]['content_hash']})
    assert response.status_code == 200, response.text
    p = client.get(root).json()
    assert p['product_flow'][1]['complete']
    old_step3_hash = p['product_flow'][2]['content_hash']
    p = save(client, root, states={'priority': {'status': 'unknown'}})
    assert not p['product_flow'][1]['complete'] and p['product_flow'][1]['needs_recheck']
    assert not p['product_flow'][2]['available']
    assert p['product_flow'][2]['content_hash'] != old_step3_hash
    p = save(client, root, profile={'scenario': 'modify_existing', 'dependencies': 'shared'})
    assert any('当前页面与流程' in issue for issue in p['product_flow'][1]['missing'])


def test_legacy_scope_text_still_counts_and_bad_states_are_rejected(tmp_path):
    client, app, root = case(tmp_path)
    values = {**essentials(), 'current_state': '现有流程已运行', 'preserve_scope': '保留导出'}
    p = save(client, root, values, ids=['REQ-0001'])
    assert p['product_flow'][1]['missing'] == []
    bad = client.post(root + '/product-context', json={'expected_revision': p['revision'],
        'values': {}, 'field_states': {'imaginary': {'status': 'none'}}})
    assert bad.status_code == 400 and bad.json()['code'] == 'CONTEXT_INVALID'


def test_untouched_legacy_checkpoint_and_hash_inputs_keep_exact_identity(tmp_path):
    client, app, root = case(tmp_path)
    p = app.state.store.get(root.rsplit('/', 1)[-1])
    p['product_context'] = {**essentials(), 'current_state': '已有流程',
                            'preserve_scope': '导出保持', 'scope_ids': ['REQ-0001']}
    selected = [i for i in p['items'] if i['kind'] == 'requirement' and i['id'] in ['REQ-0001']]
    old_scope = dict(context={k: p['product_context'].get(k) for k in SCOPE},
                     scope_ids=['REQ-0001'],
                     scope=[{k: i.get(k) for k in ('id', 'title', 'applies_to', 'change_type')}
                            for i in selected])
    relevant = [i for i in p['items'] if i['selection_status'] == 'selected' and
                (i['id'] == 'REQ-0001' or 'REQ-0001' in i.get('related_refs', []))]
    old_rules = dict(items=relevant, questions=p['questions'])
    business = selection_identity(p)
    if business:
        old_scope = dict(content=old_scope, business_context=business)
        old_rules = dict(content=old_rules, business_context=business)
    assert fingerprint(p, 2) == digest(old_scope)
    assert fingerprint(p, 3) == digest(old_rules)

    old_brief = {k: p[k] for k in ('items', 'questions', 'options')}
    old_brief['product_context'] = p['product_context']
    if business:
        old_brief['business_context'] = business
    assert brief_hash(p) == digest(old_brief)
    old_execution_keys = ('revision', 'items', 'questions', 'options', 'sources',
                          'documents', 'ui', 'messages', 'reference_mode', 'mode',
                          'product_context', 'stage_checks', 'sketch_review',
                          'delivery_scope', 'requirement_relations')
    assert execution_hash(p) == digest({k: p.get(k) for k in old_execution_keys})


def test_applicability_changes_stale_documents_and_excludes_historical_text(tmp_path):
    client, app, root = case(tmp_path)
    p = app.state.store.get(root.rsplit('/', 1)[-1])
    p['product_context'] = {**essentials(), 'current_state': '旧系统可以导出',
                            'preserve_scope': '旧导出保持', 'value': '旧价值说明',
                            'scope_ids': ['REQ-0001']}
    checkpoint(p, 2, fingerprint(p, 2))
    old_brief, old_execution = brief_hash(p), execution_hash(p)
    for doc in p['documents'].values():
        doc['brief_hash'] = old_brief
    assert verified_context(p)['current_state'] == '旧系统可以导出'

    p['scope_profile'] = {'scenario': 'new_product', 'dependencies': 'unknown'}
    p['context_field_states'] = {
        'current_state': {'status': 'not_applicable'},
        'preserve_scope': {'status': 'none'},
        'value': {'status': 'unknown'},
    }
    assert p['product_context']['current_state'] == '旧系统可以导出'
    assert p['product_context']['preserve_scope'] == '旧导出保持'
    assert brief_hash(p) != old_brief and execution_hash(p) != old_execution
    assert all(doc['brief_hash'] != brief_hash(p) for doc in p['documents'].values())
    assert any('基于旧内容' in issue for issue in status(p)[4]['missing'])
    assert not status(p)[1]['complete']
    checkpoint(p, 2, fingerprint(p, 2))
    confirmed = verified_context(p)
    assert confirmed['intent'] == essentials()['intent']
    assert 'current_state' not in confirmed
    assert 'preserve_scope' not in confirmed
    assert 'value' not in confirmed
    checked_brief, checked_execution = brief_hash(p), execution_hash(p)
    p['context_field_states']['value'] = {'status': 'known'}
    assert brief_hash(p) != checked_brief and execution_hash(p) != checked_execution
    p['context_field_states']['current_state'] = {'status': 'known'}
    checkpoint(p, 2, fingerprint(p, 2))
    assert verified_context(p)['current_state'] == '旧系统可以导出'


def test_new_scope_metadata_allows_optional_product_module_at_first_checkpoint(tmp_path):
    app = create_app(tmp_path, access_token='scope-test', env_path=tmp_path / '.env')
    client = TestClient(app)
    client.headers['Origin'] = 'http://testserver'
    client.headers['X-CSRF-Token'] = client.post('/api/session/login', json={'key': 'scope-test'}).json()['csrf']
    project = prepared(app.state.store)
    root = '/api/projects/' + project['id']
    p = save(client, root, essentials(), {'scenario': 'new_product', 'dependencies': 'unknown'},
             ids=['REQ-0001'])
    assert 'intake' not in p and p['product_context'].get('product') is None
    assert p['product_context'].get('module') is None
    assert p['product_flow'][0]['missing'] == []
    response = client.post(root + '/stage-checks/1', json={
        'expected_revision': p['revision'], 'expected_hash': p['product_flow'][0]['content_hash']})
    assert response.status_code == 200, response.text
    p = client.get(root).json()
    assert p['product_flow'][0]['complete'] and p['product_flow'][1]['available']

    p = save(client, root, states={'intent': {'status': 'unknown'}})
    assert any('本次改动意图' in issue for issue in p['product_flow'][0]['missing'])
    p = save(client, root, states={'intent': {'status': 'known'}})
    assert p['product_flow'][0]['missing'] == []
    with app.state.store.edit(p['id'], p['revision'], 'remove fixture source') as (draft, _):
        draft['sources'] = []
    p = client.get(root).json()
    assert any('至少一份可读取' in issue for issue in p['product_flow'][0]['missing'])


def test_untouched_legacy_first_step_still_requires_product_module(tmp_path):
    app = create_app(tmp_path, access_token='scope-test', env_path=tmp_path / '.env')
    p = prepared(app.state.store)
    p['product_context'] = {'intent': '已有明确诉求'}
    assert 'intake' not in p and 'scope_profile' not in p and 'context_field_states' not in p
    issues = status(p)[0]['missing']
    assert any('现有产品' in issue for issue in issues)
    assert any('相关页面或模块' in issue for issue in issues)
    assert not any('本次改动意图' in issue for issue in issues)


def test_completed_legacy_scope_without_separate_intent_keeps_checkpoint(tmp_path):
    client, app, root = case(tmp_path)
    p = app.state.store.get(root.rsplit('/', 1)[-1])
    p['product_context'] = {key: '历史已核对说明' for key in SCOPE}
    p['product_context']['scope_ids'] = ['REQ-0001']
    assert 'intent' not in p['product_context']
    checkpoint(p, 2, fingerprint(p, 2))
    assert status(p)[1]['complete']
    old_hash = fingerprint(p, 2)
    p['scope_profile'] = {'scenario': 'new_product', 'dependencies': 'unknown'}
    assert fingerprint(p, 2) != old_hash
    assert any('本次改动意图' in issue for issue in status(p)[1]['missing'])
    assert not status(p)[1]['complete']
