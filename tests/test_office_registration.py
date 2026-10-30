"""Cross-view COM registration regressions, using synthetic registrations only."""
from contextlib import nullcontext
import os

import pytest

from app import office

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows registry')
CLSID = '{00000000-1111-2222-3333-444444444444}'


def registry(monkeypatch, entries):
    import winreg
    def open_key(_root, path, reserved=0, access=winreg.KEY_READ):
        view = 32 if access & winreg.KEY_WOW64_32KEY else 64
        if not any(key[:2] == (view, path) for key in entries):
            raise FileNotFoundError(path)
        return nullcontext((view, path))
    monkeypatch.setattr(winreg, 'OpenKey', open_key)
    def query(key, name):
        index = (*key, name) if name else key
        if index not in entries:
            raise FileNotFoundError(name)
        return entries[index], winreg.REG_SZ
    monkeypatch.setattr(winreg, 'QueryValueEx', query)


@pytest.mark.parametrize('kind,progid,process', [
    ('Word', 'KWPS.Application', 'wps'),
    ('Excel', 'KET.Application', 'et'),
    ('PowerPoint', 'KWPP.Application', 'wpp'),
])
def test_wps_registered_only_in_32bit_view_is_detected(monkeypatch, kind, progid, process):
    registry(monkeypatch, {
        (32, progid + r'\CLSID'): CLSID,
        (32, 'CLSID\\' + CLSID + r'\LocalServer32'): f'"C:\\合成办公软件\\{process}.exe" /Automation',
    })
    selected = office.registered_application(kind)
    assert selected is not None, '32-bit WPS must not be reported as missing'
    assert selected['progid'] == progid and selected['process'] == process
    assert selected['registry_view'] == 32 and selected['clsid'] == CLSID


def test_curver_and_explicit_server_executable_are_honored(monkeypatch):
    registry(monkeypatch, {
        (64, r'KWPS.Application\CurVer'): 'KWPS.Application.9',
        (64, r'KWPS.Application.9\CLSID'): CLSID,
        (64, 'CLSID\\' + CLSID + r'\LocalServer32'): '"C:\\ambiguous.exe" /Automation',
        (64, 'CLSID\\' + CLSID + r'\LocalServer32', 'ServerExecutable'): r'C:\合成路径\wps.exe',
    })
    selected = office.registered_application('Word')
    assert selected['process'] == 'wps' and selected['clsid'] == CLSID


@pytest.mark.parametrize('kind,progid,component', [
    ('Excel', 'KET.Application', 'et'), ('PowerPoint', 'KWPP.Application', 'wpp'),
])
def test_unified_wps_executable_selects_the_registered_component(monkeypatch, kind, progid, component):
    entries = {
        (32, progid + r'\CLSID'): CLSID,
        (32, 'CLSID\\' + CLSID + r'\LocalServer32'): f'"C:\\合成路径\\wps.exe" /prometheus /{component} /Automation',
    }
    registry(monkeypatch, entries)
    assert office.registered_application(kind)['application'] == ('WPS 表格' if kind == 'Excel' else 'WPS 演示')
    # A text-only registration must not be mistaken for Excel or PPT.
    entries[(32, 'CLSID\\' + CLSID + r'\LocalServer32')] = r'"C:\合成路径\wps.exe" /wps /Automation'
    assert office.registered_application(kind) is None


def test_unknown_server_is_not_started_and_diagnostics_disclose_no_paths(monkeypatch):
    registry(monkeypatch, {
        (32, r'KWPS.Application\CLSID'): CLSID,
        (32, 'CLSID\\' + CLSID + r'\LocalServer32'): r'C:\private-user\unknown.exe',
    })
    assert office.registered_application('Word') is None
    import json
    diagnostic = office.registration_diagnostics()
    text = json.dumps(diagnostic)
    assert 'unrecognized_server' in text and 'private-user' not in text
    assert diagnostic['documents_opened'] == 0 and diagnostic['registry_modified'] is False


def test_registry_permission_denial_is_not_misreported_or_fallen_back(tmp_path, monkeypatch):
    import winreg
    calls = []
    def denied(_root, path, *_args):
        calls.append(path)
        raise PermissionError('synthetic-private-path')
    monkeypatch.setattr(winreg, 'OpenKey', denied)
    monkeypatch.setattr(office.subprocess, 'run', lambda *_a, **_kw: pytest.fail('No worker allowed'))
    result = office.extract_office('synthetic.docx', b'synthetic-only', tmp_path)
    assert len(calls) == 1 and result['status'] == 'permission_denied'
    assert result['code'] == 'OFFICE_PERMISSION_DENIED' and result['failure_stage'] == 'application_registration'
    assert not result['rows'] and not list(tmp_path.iterdir())


def test_discovered_class_is_passed_to_native_worker(tmp_path, monkeypatch):
    import json
    from pathlib import Path
    import subprocess
    monkeypatch.setattr(office, 'registered_application', lambda _kind: dict(
        progid='KWPS.Application', process='wps', application='WPS 文字', clsid=CLSID, registry_view=32))
    def run(command, **_kwargs):
        assert command[command.index('--clsid') + 1] == CLSID
        Path(command[command.index('--output') + 1]).write_text(json.dumps(dict(
            status='partial', rows=[['正文段落 1', '合成正文']], reason='人工未核对')), 'utf-8')
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr(office.subprocess, 'run', run)
    result = office.extract_office('synthetic.docx', b'synthetic-only', tmp_path)
    assert result['registry_view'] == 32 and result['status'] == 'partial'


def test_desktop_diagnostic_never_starts_ui_com_or_initializes_business_data(tmp_path, monkeypatch):
    import json
    from app import desktop_runtime
    monkeypatch.setattr(desktop_runtime, 'server', lambda: pytest.fail('No server'))
    monkeypatch.setattr(desktop_runtime, 'launcher', lambda: pytest.fail('No browser'))
    monkeypatch.setattr(office.subprocess, 'run', lambda *_a, **_kw: pytest.fail('No child or Office'))
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'empty-user'))
    registry(monkeypatch, {})
    output = tmp_path / 'diagnostic.json'
    assert desktop_runtime.main(['--office-diagnostics', '--output', str(output)]) == 0
    diagnostic = json.loads(output.read_text('utf-8'))
    assert diagnostic['documents_opened'] == diagnostic['model_calls'] == 0
    assert set(diagnostic['applications']) == {'Word', 'Excel', 'PowerPoint'}
    assert not (tmp_path / 'empty-user').exists()
