"""Exercise cleanup failure branches without touching any Windows process."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import verify_windows_install as verifier


@pytest.mark.parametrize('phase', [
    'open_descendant', 'identify_descendant', 'terminate_descendant', 'wait_unowned_descendant',
])
def test_cleanup_keeps_stable_failure_code_and_reports_only_safe_branch_details(tmp_path, monkeypatch, phase):
    executable = tmp_path / 'private' / 'python.exe'
    terminated, closed = [], []
    root = SimpleNamespace(pid=10, poll=lambda: None,
                           terminate=lambda: terminated.append('held-root'), wait=lambda timeout: 1)
    owned = object.__new__(verifier.OwnedProcess)
    owned.process, owned.handle, owned.executable, owned.created = root, 100, executable, 1000
    owned.roots = [(tmp_path / 'private').resolve()]
    kernel = SimpleNamespace(
        OpenProcess=lambda *_args: 0 if phase == 'open_descendant' else 42,
        TerminateProcess=lambda handle, code: terminated.append(handle) or 0,
        WaitForSingleObject=lambda handle, timeout: 258,
        CloseHandle=closed.append,
    )

    def identity(handle):
        if handle == 100:
            return executable, 1000
        if phase == 'identify_descendant':
            raise verifier.VerificationFailure('PROCESS_IMAGE_UNVERIFIABLE')
        return (Path('C:/Windows/System32/unowned.exe') if phase == 'wait_unowned_descendant' else executable), 1001

    monkeypatch.setattr(verifier, 'kernel_api', lambda: kernel)
    monkeypatch.setattr(verifier, 'process_identity', identity)
    monkeypatch.setattr(verifier, 'process_parents', lambda: {42: 10, 99: 9})
    monkeypatch.setattr(verifier.ctypes, 'get_last_error', lambda: 5)
    with pytest.raises(verifier.VerificationFailure) as caught:
        owned.terminate_owned()
    error = caught.value
    assert str(error) == 'CLEANUP_DESCENDANT_IDENTITY_UNVERIFIED'
    assert error.cleanup_diagnostics[0]['phase'] == phase
    assert error.cleanup_diagnostics[0]['pid'] == 42
    assert set(error.cleanup_diagnostics[0]) <= {'phase', 'pid', 'win32_error', 'wait_result'}
    assert 'cleanup_diagnostics' in error.__notes__[0]
    assert 'unowned.exe' not in error.__notes__[0]
    assert 'held-root' in terminated
    if phase != 'terminate_descendant':
        assert terminated == ['held-root']  # Unverifiable/unowned processes are never killed.
    assert closed == ([] if phase == 'open_descendant' else [42])


def test_cleanup_treats_an_already_exited_descendant_as_gone(tmp_path, monkeypatch):
    executable = tmp_path / 'python.exe'
    terminated = []
    owned = object.__new__(verifier.OwnedProcess)
    owned.process = SimpleNamespace(pid=10, poll=lambda: None,
                                    terminate=lambda: terminated.append('held-root'), wait=lambda timeout: 1)
    owned.handle, owned.executable, owned.created, owned.roots = 100, executable, 1000, []
    monkeypatch.setattr(verifier, 'kernel_api', lambda: SimpleNamespace(OpenProcess=lambda *_args: 0))
    monkeypatch.setattr(verifier, 'process_identity', lambda handle: (executable, 1000))
    monkeypatch.setattr(verifier, 'process_parents', lambda: {42: 10})
    monkeypatch.setattr(verifier.ctypes, 'get_last_error', lambda: 87)
    owned.terminate_owned()
    assert terminated == ['held-root']
