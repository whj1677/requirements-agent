"""Acceptance regressions: actual HTTP state and model inputs, isolated data only."""
import asyncio
import copy
import json
import os
import time
import uuid

from fastapi.testclient import TestClient
import pytest

from app.core import Problem
from app.document_reader import export_readiness
from app.intake import SAVE_REASON
from app.main import create_app
from app.provider import DEFAULT, Provider, assemble
from app.sources import save_source
from tests.helpers import prepared


@pytest.fixture
def client_case(tmp_path, monkeypatch):
    monkeypatch.delenv('RA_DEEPSEEK_API_KEY', raising=False)
    app = create_app(tmp_path / 'data', access_token='local-test', env_path=tmp_path / 'absent.env')
    with TestClient(app) as client:
        csrf = client.post('/api/session/login', json={'key':'local-test'}).json()['csrf']
        client.headers.update({'Origin':'http://testserver','X-CSRF-Token':csrf})
        yield client, app


def intake(client, root, text):
    revision = client.get(root).json()['revision']
    response = client.post(root + '/intake', json=dict(expected_revision=revision, text=text,
                                                     platform='合成联系人列表', preserved='权限保持不变'))
    assert response.status_code == 200, response.text
    return client.get(root).json()


def test_current_intake_excludes_replaced_versions_from_actual_model_input(client_case):
    client, app = client_case
    p = client.post('/api/projects', json={'name':'合成首步修改'}).json()
    root = '/api/projects/' + p['id']
    first = intake(client, root, '备注必填，最多120字')
    second = intake(client, root, '备注选填，最多200字')
    final = intake(client, root, '备注选填，最多300字，超长阻止保存并保留输入')
    current = [s for s in final['sources'] if not s['excluded']]
    assert len(current) == 1 and current[0]['intake_version'] == 3
    assert final['intake']['current_source_id'] == current[0]['id']
    assert all(s.get('superseded_by') for s in final['sources'] if s['excluded'])
    messages, _, _ = assemble(final, 'ingest', '', DEFAULT, app.state.store.folder)
    context = json.loads(messages[1]['content'][0]['text'])
    assert {x['source_id'] for x in context['excerpts']} == {current[0]['id']}
    assert '最多120字' not in json.dumps(context, ensure_ascii=False)
    unchanged = intake(client, root, '备注选填，最多300字，超长阻止保存并保留输入')
    assert len(unchanged['sources']) == 3
    sid = first['sources'][0]['id']
    assert client.get(root + '/sources/' + sid + '/file').status_code == 200
    assert client.post(root + '/sources/' + sid + '/exclude', json={'expected_revision':unchanged['revision']}).status_code == 409


def test_legacy_intake_ownership_uses_revision_provenance_not_filename(client_case):
    client, app = client_case
    store = app.state.store
    p = store.create('合成历史版本')
    with store.edit(p['id'], p['revision'], '独立上传资料') as (p, db):
        independent = save_source(store, '本次诉求.txt', '独立附件：保留操作日志'.encode(), 'goal')
        p['sources'].append(independent)
    old_ids = []
    for text in ('早先诉求', '修订诉求'):
        p = store.get(p['id'])
        with store.edit(p['id'], p['revision'], SAVE_REASON) as (p, db):
            source = save_source(store, '本次诉求.txt', text.encode(), 'goal')
            p['sources'].append(source)
            old_ids.append(source['id'])
            p['intake'] = dict(text=text, platform='', preserved='', source_ids=[s['id'] for s in p['sources']])
    p = intake(client, '/api/projects/' + p['id'], '最终诉求')
    assert all(s['excluded'] for s in p['sources'] if s['id'] in old_ids)
    separate = next(s for s in p['sources'] if s['id'] == independent['id'])
    assert not separate['excluded'] and 'origin' not in separate


def test_deferred_note_survives_reload_without_answer_or_adoption(client_case):
    client, app = client_case
    p = prepared(app.state.store)
    root = '/api/projects/' + p['id']
    note = '暂存：200字符可能适合，需向同事核实。'
    response = client.post(root + '/questions/Q-0001/defer', json={'note':note,'expected_revision':p['revision']})
    assert response.status_code == 200, response.text
    current = client.get(root).json()
    q = current['questions'][0]
    assert q['deferred_note'] == note and q['status'] == 'open' and q['answer'] is None
    assert current['items'] == p['items'] and current['sources'] == p['sources']
    assert not current['document_export_readiness']['prd']['ready']
    stale = client.post(root + '/questions/Q-0001/defer', json={'note':'旧窗口编辑','expected_revision':p['revision']})
    assert stale.status_code == 409
    assert client.get(root).json()['questions'][0]['deferred_note'] == note
    response = client.post(root + '/answers', json={'answers':{'Q-0001':'200字符'},'expected_revision':current['revision']})
    assert response.status_code == 200, response.text
    q = response.json()['questions'][0]
    assert q['status'] == 'answered' and 'deferred_at' not in q and 'deferred_note' not in q
    assert q['deferred_history'][-1]['note'] == note
    assert client.post(root + '/questions/Q-0001/defer', json={'note':'','expected_revision':response.json()['revision']}).status_code == 409


def test_readiness_distinguishes_answers_from_application_and_skips_split_parent(client_case):
    _, app = client_case
    p = prepared(app.state.store)
    original = p['questions'][0]
    original.update(status='answered', answer='明确答案', understanding_status='pending')
    p['questions'].append(dict(original, id='Q-next', question='另一个未决问题', status='open', answer=None))
    p['questions'].append(dict(original, id='Q-split', question='原复合问题', status='open', answer=None, superseded_by=['Q-next']))
    readiness = export_readiness(p, 'prd')
    problems = {q['question_id']:q for q in readiness['problems']}
    assert set(problems) == {'Q-0001', 'Q-next'}
    assert problems['Q-0001']['state'] == 'answer_pending'
    assert problems['Q-0001']['action'] == 'apply_answer'
    assert original['question'] in problems['Q-0001']['message']
    assert problems['Q-next']['state'] == 'unanswered'
    original.update(understanding_status='applied', applied_item_ids=['REQ-0001'])
    assert all(q['question_id'] != 'Q-0001' for q in export_readiness(p, 'prd')['problems'])


def test_local_requirement_help_runs_before_scope_checkpoint_and_keeps_retry_intent(client_case, monkeypatch):
    client, app = client_case
    p = prepared(app.state.store)
    root = '/api/projects/' + p['id']
    p = intake(client, root, '只调整备注，权限保持不变')
    client.put('/api/models/model/key', json={'key':'synthetic-credential'})
    # Deterministically stop before any HTTP transport. This verifies dispatch,
    # stage gates and durable retry scope, not semantic model quality.
    async def fail_request(*args, **kwargs):
        raise Problem('AUTH_FAILED', '合成故障：未调用外部模型')
    monkeypatch.setattr(app.state.provider, 'request', fail_request)
    body = dict(expected_revision=p['revision'], action='clarify', message='只帮助核对这个需求的备注长度',
                document_type='prd', option_id=None, target={'kind':'item','id':'REQ-0001'})
    preview = client.post(root + '/actions/plan', json=body).json()
    assert not preview['missing'], preview
    response = client.post(root + '/actions', json=dict(body,plan_hash=preview['plan_hash'],
                          idempotency_key=uuid.uuid4().hex,authorize=True))
    assert response.status_code == 200, response.text
    for _ in range(100):
        task = client.get(root + '/actions').json()[-1]
        if task['status'] not in ('queued','running'):
            break
        time.sleep(.02)
    assert task['status'] == 'failed' and task['error'] == 'AUTH_FAILED'
    expected = {k:v for k,v in body.items() if k != 'expected_revision'}
    expected['target'] = dict(expected['target'], section_id=None)
    assert task['retry_input'] == expected
    current = client.get(root).json()
    assert not current['product_flow'][1]['complete']
    assert current['items'] == p['items']
    for action in ('document','review'):
        blocked = client.post(root + '/actions/plan', json=dict(body,expected_revision=current['revision'],action=action,target=None)).json()
        assert blocked['missing']


def test_project_summary_has_recorded_update_time_and_actual_stage(client_case):
    client, app = client_case
    p = app.state.store.create('同名测试')
    root = '/api/projects/' + p['id']
    intake(client, root, '合成目标')
    row = next(x for x in client.get('/api/projects').json() if x['id'] == p['id'])
    assert row['updated_at'] >= row['created']
    assert row['flow_current_title'] == '核对现状、价值与改动范围'


def test_basic_model_save_preserves_masked_proxy_without_echoing_it(client_case):
    client, app = client_case
    app.state.store.setting('model', dict(DEFAULT,proxy='http://127.0.0.1:19999'))
    shown = client.get('/api/models').json()['model']
    assert shown['proxy'] == '已配置'
    payload = {k:shown[k] for k in DEFAULT if k != 'proxy'}
    payload['model'] = 'synthetic-model'
    saved = client.put('/api/models/model', json=payload)
    assert saved.status_code == 200, saved.text
    assert app.state.store.setting('model')['proxy'] == 'http://127.0.0.1:19999'
    assert client.put('/api/models/model', json=dict(payload,proxy='')).status_code == 200
    assert app.state.store.setting('model')['proxy'] == ''


@pytest.mark.skipif(os.name != 'nt', reason='Requires actual Windows user-bound DPAPI')
def test_persisted_key_restarts_is_origin_bound_and_never_plaintext(client_case, tmp_path):
    client, app = client_case
    secret = 'synthetic-persisted-credential-for-ux'
    result = client.put('/api/models/model/key', json={'key':secret,'persist':True})
    assert result.status_code == 200, result.text
    assert result.json()['key_source'] == 'protected_store'
    assert secret not in client.get('/api/models').text
    folder = app.state.store.folder
    for path in folder.rglob('*'):
        if path.is_file():
            assert secret.encode() not in path.read_bytes()
    restarted = create_app(folder, access_token='restart', env_path=tmp_path / 'absent.env')
    assert restarted.state.provider.key(DEFAULT) == secret
    assert not restarted.state.provider.key(dict(DEFAULT,base_url='https://other.example.invalid'))
    # Moving an encrypted blob to another origin must fail closed.
    storage = restarted.state.provider.credential_store
    storage.path('https://other.example.invalid').write_bytes(storage.path('https://api.deepseek.com').read_bytes())
    status = restarted.state.provider.key_status(dict(DEFAULT,base_url='https://other.example.invalid'))
    assert not status['key_configured'] and status['key_storage_error']
    assert client.put('/api/models/model/key', json={'key':'','persist':True}).status_code == 200
    assert not Provider(env_path=tmp_path/'absent.env', credential_dir=folder/'.credentials').key(DEFAULT)


@pytest.mark.skipif(os.name != 'nt', reason='Requires actual Windows user-bound DPAPI')
def test_failed_secure_save_retains_old_key_without_plaintext_fallback(client_case, monkeypatch):
    client, app = client_case
    client.put('/api/models/model/key', json={'key':'synthetic-old','persist':True})
    import app.credentials as credentials
    def failure(*args, **kwargs):
        raise Problem('KEY_STORAGE_FAILED', '合成写入失败')
    with monkeypatch.context() as m:
        m.setattr(credentials, 'dpapi', failure)
        failed = client.put('/api/models/model/key', json={'key':'synthetic-new','persist':True})
        assert failed.status_code == 400
    assert app.state.provider.key(DEFAULT) == 'synthetic-old'
