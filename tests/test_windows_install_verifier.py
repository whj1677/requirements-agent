"""Safety boundaries of the installer verifier; no installer runs in these tests."""
import json
import os
from pathlib import Path
import sys
import subprocess

import pytest

from scripts import verify_windows_install as verifier


def installer_fixture(tmp_path):
    installer = tmp_path / 'synthetic-installer.exe'
    installer.write_bytes(b'SYNTHETIC TEST INPUT, NEVER EXECUTED')
    return installer


def test_verifier_refuses_an_existing_output_without_changing_it(tmp_path, monkeypatch):
    installer = installer_fixture(tmp_path)
    output = tmp_path / 'existing'
    output.mkdir()
    marker = output / 'user-file'
    marker.write_bytes(b'keep')
    monkeypatch.setattr(verifier, 'registrations', lambda: pytest.fail('must reject before registry access'))
    with pytest.raises(verifier.VerificationFailure, match='OUTPUT_ALREADY_EXISTS'):
        verifier.prepare_paths(installer, output)
    assert marker.read_bytes() == b'keep'


def test_verifier_refuses_existing_product_registration_before_creating_output(tmp_path, monkeypatch):
    installer = installer_fixture(tmp_path)
    output = tmp_path / 'new-output'
    monkeypatch.setattr(verifier, 'registrations', lambda: [{'location': 'existing-product'}])
    with pytest.raises(verifier.VerificationFailure, match='EXISTING_PRODUCT_REGISTRATION'):
        verifier.prepare_paths(installer, output)
    assert not output.exists()


def test_verifier_refuses_an_occupied_port_without_starting_anything(tmp_path, monkeypatch):
    class OccupiedPort:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def setsockopt(self, *_args):
            pass

        def bind(self, *_args):
            raise OSError('synthetic occupied port')

    installer = installer_fixture(tmp_path)
    output = tmp_path / 'new-output'
    monkeypatch.setattr(verifier, 'registrations', lambda: [])
    monkeypatch.setattr(verifier.socket, 'socket', lambda *_args: OccupiedPort())
    with pytest.raises(verifier.VerificationFailure, match='PORT_ALREADY_IN_USE'):
        verifier.prepare_paths(installer, output)
    assert not output.exists()


def test_customer_environment_has_only_system_path_and_no_developer_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv('RA_DEEPSEEK_API_KEY', 'synthetic-never-inherited')
    monkeypatch.setenv('GITHUB_TOKEN', 'synthetic-never-inherited')
    monkeypatch.setenv('PYTHONPATH', 'synthetic-developer-path')
    monkeypatch.setenv('PLAYWRIGHT_BROWSERS_PATH', 'synthetic-developer-browser')
    environment, home = verifier.customer_environment(tmp_path)
    system = Path(os.environ['SystemRoot']).resolve()
    assert environment['PATH'].split(os.pathsep) == [str(system), str(system / 'System32')]
    assert home == tmp_path / 'profile/Local/RequirementsAgent'
    assert not home.exists()
    assert not set(environment) & {'RA_DEEPSEEK_API_KEY', 'GITHUB_TOKEN', 'PYTHONPATH', 'PLAYWRIGHT_BROWSERS_PATH'}


def test_descendant_selection_does_not_include_peers_or_other_process_trees():
    parents = {10: 1, 11: 10, 12: 11, 20: 1, 21: 20, 30: 30}
    assert verifier.descendant_order(10, parents) == [12, 11]


def test_cleanup_terminates_only_the_verified_process_handle_created_by_the_verifier(tmp_path):
    # Use the base interpreter so a venv launcher does not introduce another host process.
    executable = Path(getattr(sys, '_base_executable', sys.executable))
    process = verifier.OwnedProcess([str(executable), '-c', 'import time;time.sleep(30)'],
                                    dict(os.environ), tmp_path, tmp_path / 'install')
    try:
        assert process.process.poll() is None
        process.terminate_owned()
        assert process.process.poll() is not None
    finally:
        if process.process.poll() is None:
            process.terminate_owned()


def test_cleanup_refuses_identity_mismatch_before_terminating_process(tmp_path, monkeypatch):
    executable = Path(getattr(sys, '_base_executable', sys.executable))
    process = verifier.OwnedProcess([str(executable), '-c', 'import time;time.sleep(30)'],
                                    dict(os.environ), tmp_path, tmp_path / 'install')
    try:
        with monkeypatch.context() as patch:
            patch.setattr(verifier, 'process_identity', lambda _handle: (tmp_path / 'unrelated.exe', process.created))
            with pytest.raises(verifier.VerificationFailure, match='CLEANUP_ROOT_IDENTITY_CHANGED'):
                process.terminate_owned()
        assert process.process.poll() is None
    finally:
        process.terminate_owned()


def test_uninstall_ownership_guard_rejects_other_installation(tmp_path, monkeypatch):
    monkeypatch.setattr(verifier, 'registrations', lambda: [{'location': str(tmp_path / 'other-install')}])
    with pytest.raises(verifier.VerificationFailure, match='INSTALL_REGISTRATION_NOT_OWNED'):
        verifier.owned_registration(tmp_path / 'ours')


def test_shutdown_never_posts_to_an_unrelated_instance(monkeypatch):
    calls = []

    def other_instance(path, method='GET', csrf=None):
        calls.append((path, method))
        return 200, {'instance_id': 'someone-elses-instance'}

    monkeypatch.setattr(verifier, 'request_json', other_instance)
    with pytest.raises(verifier.VerificationFailure, match='SHUTDOWN_INSTANCE_IDENTITY_MISMATCH'):
        verifier.shutdown_owned(None, 'the-instance-created-by-this-verifier')
    assert calls == [('/desktop/api/instance', 'GET')]


def test_installed_manifest_cannot_read_files_outside_install_directory(tmp_path):
    install = tmp_path / 'install'
    install.mkdir()
    outside = tmp_path / 'outside.dat'
    outside.write_bytes(b'not package content')
    manifest = {'distribution': 'windows-x64-direct', 'working_tree_dirty': False,
                'native_file_bytes_verified': True, 'native_file_count': 1,
                'files': {'../outside.dat': verifier.sha256(outside)}}
    (install / 'release-manifest.json').write_text(json.dumps(manifest), 'utf-8')
    with pytest.raises(verifier.VerificationFailure, match='FILE_MANIFEST_ESCAPES_INSTALL'):
        verifier.verify_installed_files(install)


def test_direct_business_checks_exercise_frontend_session_projects_and_legacy_routes(monkeypatch):
    expected = 'owned-instance'
    paths = []

    def json_response(path, method='GET', csrf=None, payload=None, origin=verifier.ORIGIN):
        paths.append(path)
        values = {
            '/desktop/api/instance': {'product': 'requirements-agent', 'instance_id': expected,
                                      'business_ready': True},
            '/desktop/api/status': {'product': 'requirements-agent', 'instance_id': expected,
                                    'business_ready': True, 'license_required': False,
                                    'csrf': 'synthetic-csrf'},
            '/api/session': {'mode': 'live'}, '/api/projects': [],
        }
        return 200, values[path]

    def raw_response(path, method='GET', csrf=None, payload=None, origin=verifier.ORIGIN):
        paths.append(path)
        if path == '/':
            return 200, b'<html></html>', {'Content-Type': 'text/html'}
        if path == '/activation/':
            return 302, b'', {'Location': '/'}
        return 404, b'', {}

    monkeypatch.setattr(verifier, 'request_json', json_response)
    monkeypatch.setattr(verifier, 'request', raw_response)
    status, projects = verifier.verify_business_http(expected)
    assert status['license_required'] is False and projects == []
    assert {'/', '/api/session', '/api/projects', '/activation/', '/activation/api/status',
            '/activation/api/instance', '/activation/api/shutdown', '/activation/api/request',
            '/activation/api/install', '/activation/api/start'} <= set(paths)


def test_direct_business_checks_reject_license_gate_or_false_readiness(monkeypatch):
    def json_response(path, *_args, **_kwargs):
        if path == '/desktop/api/instance':
            return 200, {'product': 'requirements-agent', 'instance_id': 'owned', 'business_ready': True}
        return 200, {'product': 'requirements-agent', 'instance_id': 'owned',
                     'business_ready': False, 'license_required': True, 'csrf': 'csrf'}

    monkeypatch.setattr(verifier, 'request_json', json_response)
    with pytest.raises(verifier.VerificationFailure, match='DIRECT_STATUS_NOT_READY'):
        verifier.verify_business_http('owned')


def test_shutdown_security_sends_wrong_origin_and_csrf_without_stopping(monkeypatch):
    calls = []

    def json_response(path, method='GET', csrf=None, payload=None, origin=verifier.ORIGIN):
        calls.append((path, method, csrf, origin))
        return 403, {'code': 'DENIED'}

    monkeypatch.setattr(verifier, 'request_json', json_response)
    verifier.verify_shutdown_security('valid-csrf')
    assert calls == [('/desktop/api/shutdown', 'POST', 'valid-csrf', 'http://evil.invalid'),
                     ('/desktop/api/shutdown', 'POST', 'invalid-csrf', verifier.ORIGIN)]


def test_business_request_carries_origin_csrf_and_project_payload(monkeypatch):
    captured = []

    class Response:
        status = 200
        headers = {'Content-Type': 'application/json'}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def read(self, _limit):
            return b'{}'

    class Opener:
        def open(self, request, timeout):
            captured.append((request, timeout))
            return Response()

    monkeypatch.setattr(verifier.urllib.request, 'build_opener', lambda *_args: Opener())
    verifier.request_json('/api/projects', 'POST', 'synthetic-csrf', {'name': 'synthetic'})
    request, timeout = captured[0]
    assert timeout == 3
    assert request.get_method() == 'POST'
    assert request.get_header('Origin') == verifier.ORIGIN
    assert request.get_header('X-csrf-token') == 'synthetic-csrf'
    assert json.loads(request.data) == {'name': 'synthetic'}


def test_manifest_rejects_old_distribution(tmp_path):
    install = tmp_path / 'install'
    install.mkdir()
    (install / 'release-manifest.json').write_text(json.dumps({
        'distribution': 'windows-x64-licensed', 'working_tree_dirty': False,
        'native_file_bytes_verified': True, 'native_file_count': 1, 'files': {'app.exe': 'x'},
    }), 'utf-8')
    with pytest.raises(verifier.VerificationFailure, match='WRONG_DISTRIBUTION'):
        verifier.verify_installed_files(install)


@pytest.mark.parametrize('failure', ['query-error', 'different-image'])
def test_constructor_failure_reaps_only_its_created_root_handle(tmp_path, monkeypatch, failure):
    created = []
    real_popen = verifier.subprocess.Popen

    def recorded_popen(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        created.append(process)
        return process

    def identity_failure(_handle):
        if failure == 'query-error':
            raise verifier.VerificationFailure('PROCESS_IMAGE_UNVERIFIABLE')
        return tmp_path / 'another-image.exe', 1

    monkeypatch.setattr(verifier.subprocess, 'Popen', recorded_popen)
    monkeypatch.setattr(verifier, 'process_identity', identity_failure)
    monkeypatch.setattr(verifier, 'process_parents', lambda: pytest.fail('unverified PID must not enumerate descendants'))
    executable = Path(getattr(sys, '_base_executable', sys.executable))
    try:
        with pytest.raises(verifier.VerificationFailure, match='PROCESS_IMAGE_UNVERIFIABLE|CREATED_PROCESS_IMAGE_MISMATCH'):
            verifier.OwnedProcess([str(executable), '-c', 'import time;time.sleep(30)'],
                                  dict(os.environ), tmp_path, tmp_path / 'install')
        assert len(created) == 1 and created[0].poll() is not None
    finally:
        # A failing regression must also clean up the exact root it created.
        for process in created:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)


def test_constructor_cleanup_failure_is_reported_explicitly(tmp_path, monkeypatch):
    class FakeProcess:
        _handle = 1
        waited = False

        def poll(self):
            return None

        def terminate(self):
            raise OSError('synthetic cleanup failure')

        def wait(self, timeout):
            self.waited = True
            raise subprocess.TimeoutExpired('synthetic root', timeout)

    fake = FakeProcess()
    monkeypatch.setattr(verifier.subprocess, 'Popen', lambda *_args, **_kwargs: fake)
    monkeypatch.setattr(verifier, 'process_identity', lambda _handle: (tmp_path / 'wrong.exe', 1))
    with pytest.raises(verifier.VerificationFailure, match='CREATED_PROCESS_INITIALIZATION_CLEANUP_FAILED'):
        verifier.OwnedProcess([str(tmp_path / 'requested.exe')], {}, tmp_path, tmp_path / 'install')
    assert fake.waited


def test_output_parent_junction_is_rejected_before_resolving_or_creating_directories(tmp_path, monkeypatch):
    installer = installer_fixture(tmp_path)
    target = tmp_path / 'junction-target'
    target.mkdir()
    junction = tmp_path / 'junction-alias'
    subprocess.run([os.environ['ComSpec'], '/d', '/c', 'mklink', '/J', str(junction), str(target)],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    monkeypatch.setattr(verifier, 'registrations', lambda: pytest.fail('junction must reject before registry access'))
    try:
        with pytest.raises(verifier.VerificationFailure, match='OUTPUT_REPARSE_POINT'):
            verifier.prepare_paths(installer, junction / 'new-output')
        assert not (target / 'new-output').exists()
    finally:
        # Remove only our junction entry; never recurse into its target.
        assert junction.parent.resolve() == tmp_path.resolve()
        assert junction.lstat().st_file_attributes & 0x400
        junction.rmdir()
    assert target.is_dir()

@pytest.mark.parametrize('path,expected_header,other_header', [
    ('/api/projects', 'x-csrf-token', 'x-desktop-csrf'),
    ('/desktop/api/shutdown', 'x-desktop-csrf', 'x-csrf-token'),
])
def test_verifier_routes_business_and_desktop_csrf_without_network(monkeypatch, path,
                                                                  expected_header, other_header):
    import io
    from types import SimpleNamespace
    observed = {}

    def open_request(request, timeout):
        observed.update(headers={key.lower(): value for key, value in request.header_items()},
                        url=request.full_url, timeout=timeout)
        response = io.BytesIO(b'{}')
        response.status = 200
        response.headers = {'Content-Type': 'application/json'}
        return response

    monkeypatch.setattr(verifier.urllib.request, 'build_opener',
                        lambda *_args: SimpleNamespace(open=open_request))
    status, value = verifier.request_json(path, 'POST', 'selected-session-token',
                                         payload={'name': 'synthetic'})
    assert status == 200 and value == {}
    assert observed['headers'][expected_header] == 'selected-session-token'
    assert other_header not in observed['headers']
    assert observed['headers']['origin'] == verifier.ORIGIN
    assert observed['url'] == verifier.ORIGIN + path


def test_shortcut_opt_in_enables_icons_without_changing_default_install(tmp_path):
    args = (tmp_path / 'setup.exe', tmp_path / 'app', tmp_path / 'install.log')
    normal = verifier.install_command(*args)
    shortcuts = verifier.install_command(*args, shortcuts=True)
    assert '/NOICONS' in normal and '/TASKS=!desktopicon' in normal
    assert '/NOICONS' not in shortcuts and '/TASKS=desktopicon' in shortcuts


def test_existing_shortcut_refuses_install_before_creating_output(tmp_path, monkeypatch):
    installer = installer_fixture(tmp_path)
    existing = tmp_path / 'user-shortcut.lnk'
    existing.write_bytes(b'existing-user-link')
    monkeypatch.setattr(verifier, 'shortcut_paths', lambda: {'desktop': existing})
    monkeypatch.setattr(verifier, 'prepare_paths', lambda *_: pytest.fail('must not prepare installation'))
    with pytest.raises(verifier.VerificationFailure, match='EXISTING_SHORTCUT'):
        verifier.verify(installer, tmp_path / 'new-output', shortcuts=True)
    assert existing.read_bytes() == b'existing-user-link'
    assert not (tmp_path / 'new-output').exists()


@pytest.mark.parametrize('changed,code', [('target', 'SHORTCUT_TARGET_MISMATCH'),
                                         ('working_directory', 'SHORTCUT_WORKDIR_MISMATCH'),
                                         ('arguments', 'SHORTCUT_ARGUMENTS_UNEXPECTED')])
def test_shortcut_metadata_rejects_wrong_target_cwd_or_hidden_arguments(tmp_path, monkeypatch, changed, code):
    install = tmp_path / 'app'
    links = {name: tmp_path / (name + '.lnk') for name in ('desktop', 'start_menu', 'guide')}
    for path in links.values():
        path.write_bytes(b'synthetic-link-not-executed')
    expected = dict(target=str(install / 'RequirementsAgent.exe'), working_directory=str(install), arguments='')
    rows = {name: dict(expected) for name in ('desktop', 'start_menu')}
    rows['guide'] = {'target': str(install / 'docs/windows-installation.html')}
    monkeypatch.setattr(verifier, 'shell_json', lambda *_: rows)
    verifier.verify_shortcuts(links, install)
    rows['desktop'][changed] = str(tmp_path / 'other.exe') if changed != 'arguments' else '--serve'
    with pytest.raises(verifier.VerificationFailure, match=code):
        verifier.verify_shortcuts(links, install)


def test_shortcut_launch_opens_actual_link_through_shell_and_keeps_isolated_environment(tmp_path, monkeypatch):
    import base64
    captured = {}
    link = tmp_path / "user's 需求 Agent.lnk"
    environment = {'LOCALAPPDATA': str(tmp_path / 'private-profile')}
    def capture(command, env, private, install):
        captured.update(command=command, environment=env, private=private, install=install)
        return 'owned-shell-wrapper'
    monkeypatch.setattr(verifier, 'OwnedProcess', capture)
    assert verifier.open_shortcut(link, environment, tmp_path, tmp_path / 'app') == 'owned-shell-wrapper'
    code = base64.b64decode(captured['command'][-1]).decode('utf-16le')
    assert "Start-Process -FilePath '" + str(link).replace("'", "''") + "'" in code
    assert '-WindowStyle Hidden' in code and '--serve' not in code
    assert captured['environment'] is environment


def test_real_temporary_shortcut_inherits_isolated_profile_without_running_product(tmp_path):
    private = tmp_path / 'private'; private.mkdir()
    environment, _ = verifier.customer_environment(private)
    script = private / 'synthetic_environment.py'
    output = private / 'observed-environment.json'
    script.write_text("import json,os,sys\nfrom pathlib import Path\n"
                      "Path(sys.argv[1]).write_text(json.dumps({k:os.environ.get(k) for k in "
                      "('LOCALAPPDATA','USERPROFILE','APPDATA')}),encoding='utf-8')\n", 'utf-8')
    link = private / 'synthetic-profile-probe.lnk'
    executable = Path(getattr(sys, '_base_executable', sys.executable)).resolve()
    verifier.shell_json("$value=[Console]::In.ReadToEnd() | ConvertFrom-Json; "
                        "$shell=New-Object -ComObject WScript.Shell; $link=$shell.CreateShortcut($value.link); "
                        "$link.TargetPath=$value.target; $link.Arguments=$value.arguments; "
                        "$link.WorkingDirectory=$value.cwd; $link.Save(); @{created=$true} | ConvertTo-Json",
                        {'link': str(link), 'target': str(executable), 'cwd': str(private),
                         'arguments': subprocess.list2cmdline(['-I', str(script), str(output)])})
    process = verifier.open_shortcut(link, environment, private, tmp_path / 'unused-install')
    try:
        assert process.wait(20) == 0
        observed = json.loads(output.read_text('utf-8'))
        assert observed == {key: environment[key] for key in ('LOCALAPPDATA', 'USERPROFILE', 'APPDATA')}
    finally:
        if process.process.poll() is None:
            process.terminate_owned()


def test_shortcut_launcher_may_exit_before_its_owned_service_is_ready(monkeypatch):
    from types import SimpleNamespace
    process = SimpleNamespace(process=SimpleNamespace(poll=lambda: 0))
    monkeypatch.setattr(verifier, 'request_json', lambda *_: (200, {'instance_id': 'owned'}))
    assert verifier.wait_for_instance(process, 'owned', launcher=True)['instance_id'] == 'owned'
    with pytest.raises(verifier.VerificationFailure, match='DESKTOP_PROCESS_EXITED'):
        verifier.wait_for_instance(process, 'owned')
    with pytest.raises(verifier.VerificationFailure, match='DESKTOP_INSTANCE_IDENTITY_MISMATCH'):
        verifier.wait_for_instance(process, 'different', launcher=True)


@pytest.mark.parametrize('restarted', [False, True])
def test_repeat_shortcut_requires_same_service_start_time(monkeypatch, tmp_path, restarted):
    from types import SimpleNamespace
    times = iter(['first-start', 'new-start' if restarted else 'first-start'])
    def response(path):
        return (200, {'backend': {'started_at': next(times)}}) if path == '/api/session' else (200, {'instance_id': 'owned'})
    opened = []
    monkeypatch.setattr(verifier, 'request_json', response)
    monkeypatch.setattr(verifier, 'open_shortcut', lambda link, *_: opened.append(link) or SimpleNamespace(wait=lambda _: 0))
    links = {'desktop': tmp_path / 'desktop.lnk', 'start_menu': tmp_path / 'menu.lnk'}
    if restarted:
        with pytest.raises(verifier.VerificationFailure, match='SHORTCUT_REPEAT_RESTARTED_SERVICE'):
            verifier.check_shortcut_reuse(links, 'desktop', 'owned', {}, tmp_path, tmp_path, [])
    else:
        verifier.check_shortcut_reuse(links, 'desktop', 'owned', {}, tmp_path, tmp_path, [])
    assert opened == [links['start_menu']]


def test_synthetic_project_attachment_config_and_protected_key_survive_reopen(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import create_app

    def bridge(client):
        def request(path, method='GET', csrf=None, payload=None, origin=verifier.ORIGIN, *, raw_body=None, content_type=None):
            headers = {'Origin': 'http://testserver'}
            if csrf:
                headers['X-CSRF-Token'] = csrf
            if content_type:
                headers['Content-Type'] = content_type
            response = client.request(method, path, headers=headers, json=payload, content=raw_body)
            return response.status_code, response.content, response.headers
        monkeypatch.setattr(verifier, 'request', request)

    folder = tmp_path / 'data'
    with TestClient(create_app(folder, env_path=tmp_path / 'absent.env')) as client:
        bridge(client)
        pid, sid = verifier.seed_business_data()
        before = verifier.business_snapshot(pid, sid)
    credentials = {p.name: p.read_bytes() for p in (folder / '.credentials').glob('*.dpapi')}
    assert credentials and all(b'installer-synthetic-key' not in value for value in credentials.values())
    with TestClient(create_app(folder, env_path=tmp_path / 'absent.env')) as client:
        bridge(client)
        assert verifier.business_snapshot(pid, sid) == before
        next((folder / '.credentials').glob('*.dpapi')).write_bytes(b'invalid-synthetic-key-file')
        with pytest.raises(verifier.VerificationFailure, match='PERSISTED_KEY_UNREADABLE'):
            verifier.business_snapshot(pid, sid)
    assert len(before['history']) >= 2
    assert before['models']['model']['key_source'] == 'protected_store'
    assert before['models']['model']['model'] == 'synthetic-never-called'


@pytest.mark.parametrize('scenario', ['reinstall', 'upgrade', 'changed-data', 'same-version-source',
                                     'shortcuts', 'leftover-shortcut'])
def test_installation_flow_distinguishes_reinstall_from_upgrade_and_detects_data_loss(tmp_path, monkeypatch,
                                                                                   scenario):
    import copy
    cross_version = scenario != 'reinstall'
    changed_data = scenario == 'changed-data'
    shortcuts = scenario in ('shortcuts', 'leftover-shortcut')
    same_version = scenario == 'same-version-source'
    target = tmp_path / 'new.exe'; target.write_bytes(b'new-installer')
    previous = tmp_path / 'old.exe'; previous.write_bytes(b'old-installer')
    output = tmp_path / 'verification'
    state = {'seeded': False, 'upgraded': False, 'commands': [], 'starts': []}
    home = output / 'private/profile/Local/RequirementsAgent'
    install = output / 'installed-app'
    links = {name: tmp_path / (name + '.lnk') for name in ('desktop', 'start_menu', 'guide')}

    def prepare(installer, folder):
        folder.mkdir()
        return installer.resolve(), folder.resolve()

    class Process:
        def __init__(self, command, *_args):
            state['commands'].append(command)
            self.process = self
            self.finished = False
            if Path(command[0]) in (target, previous):
                state['upgraded'] = state['seeded']
                state['version'] = '1.2.8' if Path(command[0]) == previous and not same_version else '1.2.9'
                install.mkdir(exist_ok=True)
                (install / 'RequirementsAgent.exe').write_bytes(state['version'].encode())
                (install / 'unins000.exe').write_bytes(b'synthetic-uninstaller')
                if shortcuts:
                    assert '/TASKS=desktopicon' in command and '/NOICONS' not in command
                    for path in links.values():
                        path.write_bytes(b'synthetic-installed-link')
            elif Path(command[0]).name == 'unins000.exe':
                (install / 'RequirementsAgent.exe').unlink()
                for name, path in links.items():
                    if scenario != 'leftover-shortcut' or name != 'desktop':
                        path.unlink(missing_ok=True)

        def wait(self, _timeout):
            self.finished = True
            return 0

        def poll(self):
            return 0 if self.finished else None

        def terminate_owned(self):
            self.finished = True

    def seed():
        state['seeded'] = True
        folder = home / 'data/.credentials'; folder.mkdir(parents=True)
        (folder / 'synthetic.dpapi').write_bytes(b'encrypted-synthetic')
        (home / 'data/requirements.sqlite3').write_bytes(b'synthetic-database')
        return 'P-synthetic', 'SRC-synthetic'

    baseline = dict(project={'id': 'P-synthetic', 'name': '合成', 'items': [{'statement': 'preserve original'}]},
                    history=[{'revision': 1}, {'revision': 0}], models={'model': {'key_source': 'protected_store'}},
                    attachment_sha256='synthetic-hash')
    def snapshot(*_):
        value = copy.deepcopy(baseline)
        if changed_data and state['upgraded']:
            value['project']['items'][0]['statement'] = 'lost original'
        return value

    monkeypatch.setattr(verifier, 'prepare_paths', prepare)
    monkeypatch.setattr(verifier, 'shortcut_paths', lambda: links)
    monkeypatch.setattr(verifier, 'verify_shortcuts', lambda *_: state.setdefault('shortcut_verified_versions', []).append(state['version']))
    monkeypatch.setattr(verifier, 'open_shortcut', lambda path, *_: Process([str(path)]))
    monkeypatch.setattr(verifier, 'check_shortcut_reuse', lambda _links, first, *_: state.setdefault('shortcut_first', []).append(first))
    monkeypatch.setattr(verifier, 'OwnedProcess', Process)
    monkeypatch.setattr(verifier, 'owned_registration', lambda *_: None)
    monkeypatch.setattr(verifier, 'registrations', lambda: [])
    monkeypatch.setattr(verifier, 'verify_installed_files', lambda *_: {'version': state['version'], 'source_commit': 'synthetic', 'files': {'app.exe': 'hash'}})
    monkeypatch.setattr(verifier, 'verify_packaged_smoke', lambda *args: state.setdefault('smoke_versions', []).append(state['version']))
    def ready(_process, expected, **_kwargs):
        state['starts'].append(expected)
        return {'product': 'requirements-agent', 'business_ready': True}
    monkeypatch.setattr(verifier, 'wait_for_instance', ready)
    monkeypatch.setattr(verifier, 'verify_business_http', lambda *_: ({'csrf': 'csrf'}, [baseline['project']] if state['seeded'] else []))
    monkeypatch.setattr(verifier, 'verify_shutdown_security', lambda *_: None)
    monkeypatch.setattr(verifier, 'seed_business_data', seed)
    monkeypatch.setattr(verifier, 'business_snapshot', snapshot)
    monkeypatch.setattr(verifier, 'request_json', lambda *_: (200, [baseline['project']]))
    monkeypatch.setattr(verifier, 'shutdown_owned', lambda process, *_: process.wait(20))
    report = verifier.verify(target, output, upgrade_from=previous if cross_version else None, shortcuts=shortcuts)
    assert Path(state['commands'][0][0]) == (previous if cross_version else target)
    assert report['initial_version'] == ('1.2.8' if cross_version and not same_version else '1.2.9')
    assert report['target_version'] == '1.2.9'
    assert report['initial_installer_sha256'] == verifier.sha256(previous if cross_version else target)
    assert report['installer_sha256'] == verifier.sha256(target)
    assert report['upgrade_mode'] == ('cross_version_upgrade' if cross_version and not same_version else 'same_version_reinstall')
    if same_version:
        assert report['status'] == 'INSTALLATION_VERIFICATION_FAILED'
        assert report['failure_code'] == 'UPGRADE_VERSION_UNCHANGED'
        assert len(state['starts']) == 1
        return
    assert (state['starts'][0] != state['starts'][1]) == cross_version
    assert state['smoke_versions'] == (['1.2.8', '1.2.9'] if cross_version else ['1.2.9'])
    if shortcuts:
        assert state['shortcut_first'] == ['desktop', 'start_menu']
        assert state['shortcut_verified_versions'] == ['1.2.8', '1.2.9']
        assert not any('--serve' in command for command in state['commands'])
    if scenario == 'leftover-shortcut':
        assert report['status'] == 'INSTALLATION_VERIFICATION_FAILED'
        assert report['failure_code'] == 'UNINSTALL_LEFT_SHORTCUTS'
        assert links['desktop'].read_bytes() == b'synthetic-installed-link'
    elif changed_data:
        assert report['status'] == 'INSTALLATION_VERIFICATION_FAILED'
        assert report['failure_code'] == 'UPGRADE_CHANGED_PROJECT'
    else:
        assert report['status'] == 'DIRECT_INSTALLATION_VERIFIED'
        assert report['preserved_data']['key_source'] == 'protected_store'
        assert report['checks']['upgrade_preserves_project_history_attachment_config_and_protected_key']
        assert (home / 'data/.credentials/synthetic.dpapi').read_bytes() == b'encrypted-synthetic'
        assert (home / '.env').is_file()
