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
    assert calls == [('/activation/api/instance', 'GET')]


def test_installed_manifest_cannot_read_files_outside_install_directory(tmp_path):
    install = tmp_path / 'install'
    install.mkdir()
    outside = tmp_path / 'outside.dat'
    outside.write_bytes(b'not package content')
    manifest = {'distribution': 'windows-x64-licensed', 'working_tree_dirty': False,
                'native_file_bytes_verified': True, 'files': {'../outside.dat': verifier.sha256(outside)}}
    (install / 'release-manifest.json').write_text(json.dumps(manifest), 'utf-8')
    with pytest.raises(verifier.VerificationFailure, match='FILE_MANIFEST_ESCAPES_INSTALL'):
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
