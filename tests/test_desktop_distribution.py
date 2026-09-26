"""Isolated customer-entry regressions; no listener, paid model call or daily data."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def customer_app(tmp_path, monkeypatch):
    # app.main has a module-level developer factory. Isolate even first import.
    monkeypatch.setenv('RA_DATA_DIR', str(tmp_path / 'module-data'))
    monkeypatch.setenv('RA_ACCESS_TOKEN', 'off')
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'profile'))
    import app.main as main_module
    import app.licensing as licensing
    monkeypatch.setattr(main_module, 'FROZEN', True)
    monkeypatch.setattr(main_module, 'runtime_fingerprint', lambda: 'isolated-customer-test')
    gate = SimpleNamespace(code=None, calls=0)

    class LicenseGate:
        def verify_cached(self):
            gate.calls += 1
            if gate.code:
                raise licensing.LicenseError(gate.code)
            return {'product': 'requirements-agent'}

    monkeypatch.setattr(licensing, 'LicenseManager', LicenseGate)
    return main_module, gate, tmp_path


def test_customer_factory_rejects_before_database_and_token_off_cannot_bypass(customer_app):
    main_module, gate, directory = customer_app
    from app.licensing import LicenseError
    gate.code = 'LICENSE_MISSING'
    business = directory / 'must-not-exist'
    with pytest.raises(LicenseError, match='尚未安装授权文件'):
        main_module.create_app(business, env_path=directory / 'absent.env')
    assert not business.exists()
    assert gate.calls == 1


@pytest.mark.parametrize('path,method', [('/', 'GET'), ('/api/projects', 'GET'),
                                        ('/api/session', 'GET'), ('/api/session/login', 'POST')])
def test_customer_runtime_blocks_all_business_paths_after_license_removed(customer_app, path, method):
    main_module, gate, directory = customer_app
    from fastapi.testclient import TestClient
    app = main_module.create_app(directory / 'data', env_path=directory / 'absent.env')
    with TestClient(app) as client:
        assert client.get('/api/session').status_code == 200
        gate.code = 'LICENSE_MISSING'
        response = client.request(method, path, json={'key': 'synthetic'} if method == 'POST' else None)
        assert response.status_code == 403
        assert response.json()['code'] == 'LICENSE_MISSING'
        assert app.state.store.list() == []


def test_paid_provider_checks_license_before_key_or_network(tmp_path, monkeypatch):
    monkeypatch.setenv('RA_DATA_DIR', str(tmp_path / 'module-data'))
    from app import core, licensing
    from app.provider import Provider, DEFAULT
    monkeypatch.setattr(core, 'FROZEN', True)

    class Denied:
        def verify_cached(self):
            raise licensing.LicenseError('LICENSE_MISSING')

    monkeypatch.setattr(licensing, 'LicenseManager', Denied)
    provider = Provider(env_path=tmp_path / 'absent.env')
    monkeypatch.setattr(provider, 'key', lambda _: pytest.fail('Key accessed before license check'))
    with pytest.raises(core.Problem) as rejected:
        asyncio.run(provider.request(DEFAULT, []))
    assert rejected.value.code == 'LICENSE_MISSING'
    assert rejected.value.status == 403


@pytest.mark.parametrize('mode', [['--smoke-test'], ['--parse-worker', '.docx']])
def test_unlicensed_customer_cli_does_not_create_business_data(tmp_path, mode):
    profile = tmp_path / 'profile'
    result_file = tmp_path / 'rejection.json'
    environment = dict(os.environ, LOCALAPPDATA=str(profile),
                       RA_DATA_DIR=str(tmp_path / 'bypass-attempt'), RA_ACCESS_TOKEN='off')
    command = ('import sys; sys.frozen=True; '
               'from app.desktop_runtime import main; '
               f'raise SystemExit(main({mode + ["--output", str(result_file)]!r}))')
    result = subprocess.run([sys.executable, '-c', command], cwd=ROOT, env=environment,
                            capture_output=True, timeout=30)
    assert result.returncode == 1, result.stderr.decode('utf-8', errors='replace')
    assert json.loads(result_file.read_text('utf-8'))['code'] == 'LICENSE_MISSING'
    assert not (profile / 'RequirementsAgent' / 'data').exists()
    assert not (tmp_path / 'bypass-attempt').exists()


def test_customer_profile_paths_ignore_developer_data_override(tmp_path):
    profile = tmp_path / 'profile'
    environment = dict(os.environ, LOCALAPPDATA=str(profile), RA_DATA_DIR=str(tmp_path / 'wrong-data'))
    command = ('import sys,json; sys.frozen=True; from app.core import USER_HOME,DATA; '
               'from app.config import ProjectEnvironment; '
               'print(json.dumps([str(USER_HOME),str(DATA),str(ProjectEnvironment().path)]))')
    result = subprocess.run([sys.executable, '-c', command], cwd=ROOT, env=environment,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    home = profile / 'RequirementsAgent'
    assert json.loads(result.stdout) == [str(home), str(home / 'data'), str(home / '.env')]
    assert not home.exists()


def test_customer_parser_subprocess_reenters_packaged_binary(tmp_path, monkeypatch):
    monkeypatch.setenv('RA_DATA_DIR', str(tmp_path / 'module-data'))
    from app import sources
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    observed = {}

    def run(command, **kwargs):
        observed.update(command=command, **kwargs)
        return SimpleNamespace(returncode=0, stdout=b'[[["paragraph","synthetic"]],"read","",null]')

    monkeypatch.setattr(sources.subprocess, 'run', run)
    parsed = sources.parse_bounded('example.DOCX', b'synthetic document')
    assert parsed[1] == 'read'
    assert observed['command'] == [sys.executable, '--parse-worker', '.docx']
    assert observed['input'] == b'synthetic document'
    assert observed['timeout'] == 30


ACTIVATION_ORIGIN = 'http://127.0.0.1:8765'


@pytest.fixture
def web_gateway(tmp_path):
    from app.activation import ActivationGateway
    from app.licensing import LicenseError
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    state = SimpleNamespace(code='LICENSE_MISSING', factory_calls=0, shutdown_calls=0,
                            requests=[], imports=[], installed=None, tasks=[],
                            slow_entered=None, slow_finish=None)
    payload = {'product': 'requirements-agent', 'subject': {'displayName': '合成验收客户'}}

    class Manager:
        def verify_cached(self):
            if state.code:
                raise LicenseError(state.code)
            return payload

        def create_request(self, name):
            state.requests.append(name)
            return {'product': 'requirements-agent', 'subject': {'displayName': name}}

        def install_license(self, path, replace=False):
            raw = path.read_bytes()
            state.imports.append((raw, replace, path))
            if raw == b'{"schema":"one","schema":"two"}' or raw == b'invalid':
                raise LicenseError('LICENSE_DOCUMENT_INVALID')
            if state.installed is not None and state.installed != raw and not replace:
                raise LicenseError('LICENSE_ALREADY_INSTALLED')
            state.installed = raw
            state.code = None
            return {'payload': payload}

    business = FastAPI()

    @business.get('/')
    @business.get('/api/projects')
    async def project():
        return {'business': True}

    @business.get('/api/slow')
    async def slow():
        state.slow_entered.set()
        await state.slow_finish.wait()
        return {'finished': True}

    @business.get('/api/failure')
    async def failure():
        raise RuntimeError('synthetic business error')

    def factory():
        state.factory_calls += 1
        (tmp_path / 'business-data').mkdir()
        return business

    def shutdown():
        state.shutdown_calls += 1

    assets = tmp_path / 'activation-assets'
    assets.mkdir()
    (assets / 'index.html').write_text('<html>授权与启动</html>', encoding='utf-8')
    (assets / 'app.js').write_text('/* activation only */', encoding='utf-8')
    gateway = ActivationGateway(manager=Manager(), business_factory=factory,
                                shutdown=shutdown, active_tasks=lambda: state.tasks,
                                asset_dir=assets, instance_id='isolated-test-instance')
    with TestClient(gateway, base_url=ACTIVATION_ORIGIN, client=('127.0.0.1', 53210),
                    raise_server_exceptions=False) as client:
        yield gateway, state, client, tmp_path


def activation_headers(client):
    response = client.get('/activation/api/status')
    assert response.status_code == 200
    return {'Origin': ACTIVATION_ORIGIN, 'X-Activation-CSRF': response.json()['csrf']}


def test_unlicensed_web_gateway_exposes_only_activation_and_never_initializes_business(web_gateway):
    gateway, state, client, directory = web_gateway
    assert '授权与启动' in client.get('/').text
    assert client.get('/activation/assets/app.js').status_code == 200
    assert client.get('/activation/assets/other.js').status_code == 404
    status = client.get('/activation/api/status')
    assert status.json()['licensed'] is False
    assert status.json()['business_ready'] is False
    assert status.json()['code'] == 'LICENSE_MISSING'
    assert status.headers['cache-control'] == 'no-store'
    assert "frame-ancestors 'none'" in status.headers['content-security-policy']
    for path in ('/api/session', '/api/projects', '/openapi.json', '/private'):
        response = client.get(path)
        assert response.status_code == 403
        assert response.json()['code'] == 'LICENSE_REQUIRED'
    denied = client.post('/activation/api/start', headers=activation_headers(client))
    assert denied.status_code == 403 and denied.json()['code'] == 'LICENSE_MISSING'
    assert gateway.business is None and state.factory_calls == 0
    assert not (directory / 'business-data').exists()


def test_instance_probe_never_waits_for_license_or_hardware_and_does_not_initialize_business(web_gateway, monkeypatch):
    gateway, state, client, directory = web_gateway
    monkeypatch.setattr(gateway.manager, 'verify_cached', lambda: pytest.fail('instance probe must not call hardware verification'))
    response = client.get('/activation/api/instance')
    assert response.status_code == 200
    assert response.json() == {'product': 'requirements-agent', 'instance_id': 'isolated-test-instance',
                               'business_ready': False}
    assert state.factory_calls == 0 and gateway.business is None
    assert not (directory / 'business-data').exists()


def test_launcher_readiness_uses_lightweight_instance_probe(monkeypatch):
    import io
    from app import desktop_runtime
    observed = []

    class Opener:
        def open(self, url, timeout):
            observed.append((url, timeout))
            return io.BytesIO(b'{"instance_id":"synthetic-instance","business_ready":false}')

    monkeypatch.setattr(desktop_runtime.urllib.request, 'build_opener', lambda *_: Opener())
    assert desktop_runtime.runtime_at_port()['instance_id'] == 'synthetic-instance'
    assert observed == [(ACTIVATION_ORIGIN + '/activation/api/instance', 2)]


@pytest.mark.parametrize('headers,code', [
    ({'Host': 'untrusted.example:8765'}, 'HOST_DENIED'),
    ({'Host': 'localhost:8765'}, 'HOST_DENIED'),
    ({'Host': '127.0.0.1:9999'}, 'HOST_DENIED'),
    ({'Origin': 'https://untrusted.example'}, 'ORIGIN_DENIED'),
    ({'Origin': 'null'}, 'ORIGIN_DENIED'),
])
def test_activation_status_and_csrf_are_not_exposed_to_foreign_sites(web_gateway, headers, code):
    _, state, client, _ = web_gateway
    response = client.get('/activation/api/status', headers=headers)
    assert response.status_code == 403
    assert response.json() == {'code': code}
    assert 'access-control-allow-origin' not in response.headers
    assert state.factory_calls == state.shutdown_calls == 0


def test_gateway_rejects_non_loopback_client_even_with_spoofed_local_host(web_gateway):
    from fastapi.testclient import TestClient
    gateway, _, _, _ = web_gateway
    with TestClient(gateway, base_url=ACTIVATION_ORIGIN, client=('192.0.2.55', 53210)) as remote:
        response = remote.get('/activation/api/status', headers={'X-Forwarded-For': '127.0.0.1'})
    assert response.status_code == 403
    assert response.json()['code'] == 'HOST_DENIED'


@pytest.mark.parametrize('action', ['request', 'install', 'start', 'shutdown'])
@pytest.mark.parametrize('missing', ['origin', 'csrf', 'wrong-csrf'])
def test_all_activation_mutations_require_origin_and_csrf(web_gateway, action, missing):
    _, state, client, _ = web_gateway
    headers = activation_headers(client)
    if missing == 'origin':
        headers.pop('Origin')
    elif missing == 'csrf':
        headers.pop('X-Activation-CSRF')
    else:
        headers['X-Activation-CSRF'] = 'not-the-current-session'
    response = client.post('/activation/api/' + action, headers=headers,
                           json={'display_name': '合成验收客户'})
    assert response.status_code == 403
    assert response.json()['code'] == 'CSRF_DENIED'
    assert state.requests == state.imports == []
    assert state.factory_calls == state.shutdown_calls == 0


def test_request_download_does_not_unlock_or_load_business(web_gateway):
    _, state, client, directory = web_gateway
    response = client.post('/activation/api/request', headers=activation_headers(client),
                           json={'display_name': '自填授权名称'})
    assert response.status_code == 200
    assert response.json()['subject']['displayName'] == '自填授权名称'
    assert 'attachment;' in response.headers['content-disposition']
    assert state.requests == ['自填授权名称']
    assert state.factory_calls == 0
    assert client.get('/activation/api/status').json()['licensed'] is False
    assert not (directory / 'business-data').exists()


def test_license_upload_preserves_raw_bytes_and_cleans_temporary_file_on_rejection(web_gateway):
    _, state, client, directory = web_gateway
    raw = b'{"schema":"one","schema":"two"}'
    response = client.post('/activation/api/install', content=raw, headers=activation_headers(client))
    assert response.status_code == 403
    assert response.json()['code'] == 'LICENSE_DOCUMENT_INVALID'
    assert len(state.imports) == 1
    recorded, replacement, source = state.imports[0]
    assert recorded == raw and replacement is False
    assert not source.exists()
    assert state.installed is None
    assert not (directory / 'business-data').exists()


def test_large_chunked_upload_is_rejected_even_without_content_length(web_gateway):
    _, state, client, _ = web_gateway
    from app.licensing import MAX_LICENSE_BYTES
    response = client.post('/activation/api/install',
                           content=iter([b'x' * MAX_LICENSE_BYTES, b'y']),
                           headers=activation_headers(client))
    assert response.status_code == 403
    assert response.json()['code'] == 'LICENSE_DOCUMENT_INVALID'
    assert state.imports == [] and state.installed is None


def test_license_replacement_is_explicit_and_invalid_new_license_preserves_previous(web_gateway):
    _, state, client, _ = web_gateway
    headers = activation_headers(client)
    assert client.post('/activation/api/install', content=b'first-valid', headers=headers).status_code == 200
    response = client.post('/activation/api/install', content=b'second-valid', headers=headers)
    assert response.status_code == 403 and response.json()['code'] == 'LICENSE_ALREADY_INSTALLED'
    assert state.installed == b'first-valid'
    assert client.post('/activation/api/install?replace=1', content=b'second-valid', headers=headers).status_code == 403
    assert client.post('/activation/api/install?replace=true', content=b'invalid', headers=headers).status_code == 403
    assert state.installed == b'first-valid'
    assert client.post('/activation/api/install?replace=true', content=b'second-valid', headers=headers).status_code == 200
    assert state.installed == b'second-valid'
    assert state.factory_calls == 0


def test_license_install_and_start_are_separate_and_start_loads_business_once(web_gateway):
    gateway, state, client, directory = web_gateway
    headers = activation_headers(client)
    assert client.post('/activation/api/install', content=b'valid', headers=headers).status_code == 200
    assert gateway.business is None and state.factory_calls == 0
    assert not (directory / 'business-data').exists()
    first = client.post('/activation/api/start', headers=headers)
    second = client.post('/activation/api/start', headers=headers)
    assert first.json() == second.json() == {'ready': True, 'url': '/'}
    assert state.factory_calls == 1 and (directory / 'business-data').is_dir()
    assert client.get('/api/projects').json() == {'business': True}
    status = client.get('/activation/api/status').json()
    assert status['business_ready'] and status['licensed']


def test_shutdown_refuses_queued_tasks_then_closes_gateway_even_if_license_removed(web_gateway):
    gateway, state, client, _ = web_gateway
    headers = activation_headers(client)
    state.tasks.append('synthetic-queued-task')
    response = client.post('/activation/api/shutdown', headers=headers)
    assert response.status_code == 409
    assert state.shutdown_calls == 0 and not gateway.stopping
    state.tasks.clear()
    response = client.post('/activation/api/shutdown', headers=headers)
    assert response.json() == {'status': 'STOPPING'}
    assert state.shutdown_calls == 1 and gateway.stopping
    assert client.post('/activation/api/start', headers=headers).status_code == 503
    assert client.get('/activation/api/status').status_code == 503
    assert state.factory_calls == 0


def test_shutdown_refuses_inflight_business_and_counter_resets_after_error(web_gateway):
    gateway, state, client, _ = web_gateway
    headers = activation_headers(client)
    state.code = None
    assert client.post('/activation/api/start', headers=headers).status_code == 200
    import httpx

    async def exercise():
        state.slow_entered, state.slow_finish = asyncio.Event(), asyncio.Event()
        transport = httpx.ASGITransport(app=gateway, client=('127.0.0.1', 53210), raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url=ACTIVATION_ORIGIN) as asynchronous:
            slow = asyncio.create_task(asynchronous.get('/api/slow'))
            await asyncio.wait_for(state.slow_entered.wait(), 5)
            assert gateway.active_requests == 1
            stopping = await asynchronous.post('/activation/api/shutdown', headers=headers)
            assert stopping.status_code == 409 and state.shutdown_calls == 0
            state.slow_finish.set()
            assert (await slow).status_code == 200
            assert gateway.active_requests == 0
            assert (await asynchronous.get('/api/failure')).status_code == 500
            assert gateway.active_requests == 0
            state.code = 'LICENSE_MISSING'
            assert (await asynchronous.post('/activation/api/shutdown', headers=headers)).status_code == 200
            assert gateway.stopping and state.shutdown_calls == 1

    asyncio.run(exercise())


@pytest.mark.parametrize('action', ['request', 'install'])
def test_shutdown_refuses_inflight_activation_operations(web_gateway, monkeypatch, action):
    import httpx
    import threading
    gateway, state, client, _ = web_gateway
    headers = activation_headers(client)
    entered, release = threading.Event(), threading.Event()
    method = 'create_request' if action == 'request' else 'install_license'
    original = getattr(gateway.manager, method)

    def delayed(*args):
        entered.set()
        assert release.wait(5), 'test did not release activation worker'
        return original(*args)

    monkeypatch.setattr(gateway.manager, method, delayed)

    async def exercise():
        transport = httpx.ASGITransport(app=gateway, client=('127.0.0.1', 53210))
        async with httpx.AsyncClient(transport=transport, base_url=ACTIVATION_ORIGIN) as asynchronous:
            body = {'json': {'display_name': '合成名称'}} if action == 'request' else {'content': b'valid'}
            operation = asyncio.create_task(asynchronous.post('/activation/api/' + action, headers=headers, **body))
            try:
                assert await asyncio.to_thread(entered.wait, 3)
                assert gateway.active_requests == 1
                response = await asynchronous.post('/activation/api/shutdown', headers=headers)
                assert response.status_code == 409
                assert state.shutdown_calls == 0 and not gateway.stopping
            finally:
                release.set()
            assert (await operation).status_code == 200
            assert gateway.active_requests == 0

    asyncio.run(exercise())


def test_concurrent_start_initializes_business_once_and_blocks_shutdown_during_validation(web_gateway, monkeypatch):
    import httpx
    import threading
    gateway, state, client, _ = web_gateway
    headers = activation_headers(client)
    state.code = None
    entered, release = threading.Event(), threading.Event()
    original = gateway.manager.verify_cached

    def delayed():
        entered.set()
        assert release.wait(5), 'test did not release license verification'
        return original()

    monkeypatch.setattr(gateway.manager, 'verify_cached', delayed)

    async def exercise():
        transport = httpx.ASGITransport(app=gateway, client=('127.0.0.1', 53210))
        async with httpx.AsyncClient(transport=transport, base_url=ACTIVATION_ORIGIN) as asynchronous:
            first = asyncio.create_task(asynchronous.post('/activation/api/start', headers=headers))
            second = asyncio.create_task(asynchronous.post('/activation/api/start', headers=headers))
            try:
                assert await asyncio.to_thread(entered.wait, 3)
                assert gateway.start_lock.locked()
                assert state.factory_calls == 0
                response = await asynchronous.post('/activation/api/shutdown', headers=headers)
                assert response.status_code == 409 and state.shutdown_calls == 0
            finally:
                release.set()
            results = await asyncio.gather(first, second)
            assert [item.status_code for item in results] == [200, 200]
            assert state.factory_calls == 1
            assert gateway.active_requests == 0 and not gateway.start_lock.locked()

    asyncio.run(exercise())


def test_request_remains_active_until_entire_stream_has_finished(web_gateway):
    import httpx
    gateway, state, client, _ = web_gateway
    headers = activation_headers(client)

    async def exercise():
        entered, finish = asyncio.Event(), asyncio.Event()

        async def streaming_business(scope, receive, send):
            await send({'type': 'http.response.start', 'status': 200, 'headers': []})
            await send({'type': 'http.response.body', 'body': b'first', 'more_body': True})
            entered.set()
            await finish.wait()
            await send({'type': 'http.response.body', 'body': b'last', 'more_body': False})

        gateway.business = streaming_business
        transport = httpx.ASGITransport(app=gateway, client=('127.0.0.1', 53210))
        async with httpx.AsyncClient(transport=transport, base_url=ACTIVATION_ORIGIN) as asynchronous:
            stream = asyncio.create_task(asynchronous.get('/stream'))
            await asyncio.wait_for(entered.wait(), 5)
            assert gateway.active_requests == 1
            assert (await asynchronous.post('/activation/api/shutdown', headers=headers)).status_code == 409
            assert state.shutdown_calls == 0
            finish.set()
            assert (await stream).content == b'firstlast'
            assert gateway.active_requests == 0

    asyncio.run(exercise())


def test_gateway_initialization_and_unlicensed_status_do_not_import_main_in_fresh_process(tmp_path):
    profile = tmp_path / 'clean-profile'
    result_file = tmp_path / 'gateway-result.json'
    environment = dict(os.environ, LOCALAPPDATA=str(profile), RA_DATA_DIR=str(tmp_path / 'override'))
    command = (
        'import sys,json;sys.frozen=True;'
        'from pathlib import Path;from app.activation import ActivationGateway;'
        'from fastapi.testclient import TestClient;gateway=ActivationGateway();'
        "client=TestClient(gateway,base_url='http://127.0.0.1:8765',client=('127.0.0.1',53210));"
        "status=client.get('/activation/api/status');"
        f"Path({str(result_file)!r}).write_text(json.dumps({{'status':status.json(),'main_imported':'app.main' in sys.modules}}),encoding='utf-8')"
    )
    result = subprocess.run([sys.executable, '-c', command], cwd=ROOT, env=environment,
                            capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr.decode('utf-8', errors='replace')
    inspected = json.loads(result_file.read_text('utf-8'))
    assert inspected['status']['code'] == 'LICENSE_MISSING'
    assert inspected['status']['business_ready'] is False
    assert inspected['main_imported'] is False
    assert not (profile / 'RequirementsAgent' / 'data').exists()
    assert not (tmp_path / 'override').exists()
