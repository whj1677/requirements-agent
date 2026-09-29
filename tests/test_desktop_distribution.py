"""Isolated customer-entry regressions; no listener or paid model call."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
DESKTOP_ORIGIN = 'http://127.0.0.1:8765'


@pytest.fixture
def customer_app(tmp_path, monkeypatch):
    monkeypatch.setenv('RA_DATA_DIR', str(tmp_path / 'module-data'))
    monkeypatch.setenv('RA_ACCESS_TOKEN', 'off')
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'profile'))
    import app.main as main_module
    monkeypatch.setattr(main_module, 'FROZEN', True)
    monkeypatch.setattr(main_module, 'runtime_fingerprint', lambda: 'isolated-customer-test')
    return main_module, tmp_path


def test_customer_factory_works_with_missing_or_corrupt_legacy_license(customer_app, monkeypatch):
    main_module, directory = customer_app
    from fastapi.testclient import TestClient
    import app.licensing as licensing

    class ForbiddenLicenseRead:
        def __init__(self, *_args, **_kwargs):
            pytest.fail('customer business startup must not read device-license state')

    monkeypatch.setattr(licensing, 'LicenseManager', ForbiddenLicenseRead)
    legacy_license = directory / 'profile' / 'RequirementsAgent' / 'license' / 'license.json'
    legacy_license.parent.mkdir(parents=True)
    legacy_license.write_bytes(b'{corrupt old license')
    business = directory / 'business-data'
    app = main_module.create_app(business, env_path=directory / 'absent.env')
    with TestClient(app) as client:
        assert client.get('/api/session').status_code == 200
        assert client.get('/api/projects').json() == []
        created = client.post('/api/projects', json={'name': '无设备许可项目'})
        assert created.status_code == 200
        assert client.get('/api/projects').json()[0]['name'] == '无设备许可项目'
    assert business.exists()
    assert legacy_license.read_bytes() == b'{corrupt old license'


def test_frozen_provider_still_requires_customer_key_without_network(customer_app, monkeypatch):
    main_module, directory = customer_app
    from app import core, licensing
    from app.provider import Provider, DEFAULT

    class ForbiddenLicenseRead:
        def __init__(self, *_args, **_kwargs):
            pytest.fail('provider must not read device-license state')

    monkeypatch.setattr(licensing, 'LicenseManager', ForbiddenLicenseRead)
    monkeypatch.setattr(core, 'FROZEN', True)
    provider = Provider(env_path=directory / 'absent.env')
    monkeypatch.setattr(provider, 'key', lambda _config: '')
    with pytest.raises(core.Problem) as rejected:
        asyncio.run(provider.request(DEFAULT, []))
    assert rejected.value.code == 'CONFIG_MISSING'
    assert rejected.value.message == '尚未配置此接收端的 API Key；未发送材料'


@pytest.fixture
def desktop_gateway():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.desktop_gateway import DesktopGateway

    state = SimpleNamespace(factory_calls=0, shutdown_calls=0, tasks=[])
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

    def factory():
        state.factory_calls += 1
        return business

    def shutdown():
        state.shutdown_calls += 1

    gateway = DesktopGateway(business_factory=factory, shutdown=shutdown,
                             active_tasks=lambda: state.tasks,
                             instance_id='isolated-desktop-instance')
    with TestClient(gateway, base_url=DESKTOP_ORIGIN, client=('127.0.0.1', 53210),
                    raise_server_exceptions=False) as client:
        yield gateway, state, client


def desktop_headers(client):
    status = client.get('/desktop/api/status')
    assert status.status_code == 200
    return {'Origin': DESKTOP_ORIGIN, 'X-Desktop-CSRF': status.json()['csrf']}


def test_gateway_loads_business_immediately_and_reports_desktop_status(desktop_gateway):
    gateway, state, client = desktop_gateway
    assert gateway.business is not None
    assert state.factory_calls == 1
    assert client.get('/').json() == {'business': True}
    assert client.get('/api/projects').json() == {'business': True}
    assert client.get('/desktop/api/instance').json() == {
        'product': 'requirements-agent',
        'instance_id': 'isolated-desktop-instance',
        'business_ready': True,
    }
    status = client.get('/desktop/api/status').json()
    assert status['license_required'] is False
    assert status['business_ready'] is True
    assert isinstance(status['csrf'], str) and status['csrf']
    assert state.factory_calls == 1


def test_legacy_activation_urls_redirect_root_or_return_not_found(desktop_gateway):
    _, _, client = desktop_gateway
    for path in ('/activation', '/activation/'):
        response = client.get(path, follow_redirects=False)
        assert response.status_code in (301, 302, 307, 308)
        assert response.headers['location'] == '/'
    for path in ('/activation/api/status', '/activation/api/start', '/activation/assets/app.js'):
        assert client.get(path).status_code == 404


def test_desktop_gateway_has_no_device_licensing_dependency_in_fresh_process(tmp_path):
    result_file = tmp_path / 'gateway-imports.json'
    command = (
        'import sys,json;from fastapi import FastAPI;'
        'from fastapi.testclient import TestClient;'
        'from app.desktop_gateway import DesktopGateway;'
        "business=FastAPI();gateway=DesktopGateway(business_factory=lambda:business);"
        "client=TestClient(gateway,base_url='http://127.0.0.1:8765',client=('127.0.0.1',53210));"
        "client.get('/desktop/api/status');"
        f"open({str(result_file)!r},'w').write(json.dumps(dict(licensing_imported=('app.licensing' in sys.modules))))"
    )
    result = subprocess.run([sys.executable, '-c', command], cwd=ROOT,
                            capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr.decode('utf-8', errors='replace')
    assert json.loads(result_file.read_text('utf-8')) == {'licensing_imported': False}


@pytest.mark.parametrize('headers,code', [
    ({'Host': 'untrusted.example:8765'}, 'HOST_DENIED'),
    ({'Origin': 'https://untrusted.example'}, 'ORIGIN_DENIED'),
    ({'Origin': 'null'}, 'ORIGIN_DENIED'),
])
def test_desktop_status_rejects_foreign_hosts_and_origins(desktop_gateway, headers, code):
    _, state, client = desktop_gateway
    response = client.get('/desktop/api/status', headers=headers)
    assert response.status_code == 403
    assert response.json()['code'] == code
    assert state.factory_calls == 1
    assert state.shutdown_calls == 0


def test_gateway_rejects_non_loopback_client_even_with_forwarded_local_address(desktop_gateway):
    from fastapi.testclient import TestClient
    gateway, _, _ = desktop_gateway
    with TestClient(gateway, base_url=DESKTOP_ORIGIN, client=('192.0.2.55', 53210)) as remote:
        response = remote.get('/desktop/api/status', headers={'X-Forwarded-For': '127.0.0.1'})
    assert response.status_code == 403
    assert response.json()['code'] == 'HOST_DENIED'


@pytest.mark.parametrize('missing', ['origin', 'csrf', 'wrong-csrf'])
def test_shutdown_requires_same_origin_and_current_csrf(desktop_gateway, missing):
    _, state, client = desktop_gateway
    headers = desktop_headers(client)
    if missing == 'origin':
        headers.pop('Origin')
    elif missing == 'csrf':
        headers.pop('X-Desktop-CSRF')
    else:
        headers['X-Desktop-CSRF'] = 'not-the-current-session'
    response = client.post('/desktop/api/shutdown', headers=headers)
    assert response.status_code == 403
    assert response.json()['code'] == 'CSRF_DENIED'
    assert state.shutdown_calls == 0


def test_shutdown_refuses_queued_or_running_work(desktop_gateway):
    gateway, state, client = desktop_gateway
    headers = desktop_headers(client)
    state.tasks.extend([{'status': 'queued'}, {'status': 'running'}])
    response = client.post('/desktop/api/shutdown', headers=headers)
    assert response.status_code == 409
    assert state.shutdown_calls == 0
    state.tasks.clear()
    response = client.post('/desktop/api/shutdown', headers=headers)
    assert response.status_code == 200
    assert response.json() == {'status': 'STOPPING'}
    assert state.shutdown_calls == 1
    assert gateway.stopping
    assert client.get('/api/projects').status_code == 503


def test_shutdown_waits_for_stream_response_to_finish(desktop_gateway):
    import httpx
    gateway, state, client = desktop_gateway
    headers = desktop_headers(client)

    async def exercise():
        state.slow_entered, state.slow_finish = asyncio.Event(), asyncio.Event()
        async def stream(scope, receive, send):
            await send({'type': 'http.response.start', 'status': 200, 'headers': []})
            await send({'type': 'http.response.body', 'body': b'first', 'more_body': True})
            state.slow_entered.set()
            await state.slow_finish.wait()
            await send({'type': 'http.response.body', 'body': b'last', 'more_body': False})

        gateway.business = stream
        transport = httpx.ASGITransport(app=gateway, client=('127.0.0.1', 53210))
        async with httpx.AsyncClient(transport=transport, base_url=DESKTOP_ORIGIN) as asynchronous:
            request = asyncio.create_task(asynchronous.get('/api/slow'))
            await asyncio.wait_for(state.slow_entered.wait(), 5)
            stopping = await asynchronous.post('/desktop/api/shutdown', headers=headers)
            assert stopping.status_code == 409
            assert state.shutdown_calls == 0
            state.slow_finish.set()
            assert (await request).content == b'firstlast'
            stopped = await asynchronous.post('/desktop/api/shutdown', headers=headers)
            assert stopped.status_code == 200
            assert state.shutdown_calls == 1

    asyncio.run(exercise())


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


def test_parser_worker_cli_handles_invalid_document_without_license_gate(tmp_path):
    profile = tmp_path / 'profile'
    legacy_license = profile / 'RequirementsAgent' / 'license' / 'license.json'
    legacy_license.parent.mkdir(parents=True)
    legacy_license.write_bytes(b'broken legacy license')
    environment = dict(os.environ, LOCALAPPDATA=str(profile), RA_DATA_DIR=str(tmp_path / 'data'))
    command = ('import sys; sys.frozen=True; from app.desktop_runtime import main; '
               "raise SystemExit(main(['--parse-worker', '.docx']))")
    result = subprocess.run([sys.executable, '-c', command], cwd=ROOT, env=environment,
                            input=b'not a document', capture_output=True, timeout=30)
    assert result.returncode == 2, result.stderr.decode('utf-8', errors='replace')
    assert json.loads(result.stdout)['code'] == 'SOURCE_FAILED'
    assert b'LICENSE_' not in result.stdout + result.stderr
    assert legacy_license.read_bytes() == b'broken legacy license'
    assert not (tmp_path / 'data').exists()
