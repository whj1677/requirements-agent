"""Synthetic COM registration and worker guards; no WPS installation is claimed."""
import json
import os
from pathlib import Path
import subprocess

import pytest

from app import office

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows registry and COM')


@pytest.mark.parametrize('kind,progid,process,label', [
    ('Word', 'KWPS.Application', 'wps', 'WPS 文字'),
    ('Excel', 'KET.Application', 'et', 'WPS 表格'),
    ('PowerPoint', 'KWPP.Application', 'wpp', 'WPS 演示'),
    ('Word', 'Word.Application', 'WINWORD', 'Microsoft Word'),
    ('Word', 'Word.Application', 'wps', 'WPS 文字'),
])
def test_registered_executable_selects_native_wps_or_microsoft_without_launching(monkeypatch, kind, progid, process, label):
    import winreg
    from contextlib import nullcontext
    registry = {progid + r'\CLSID': 'synthetic-class',
                r'CLSID\synthetic-class\LocalServer32': '"C:\\Synthetic Office\\' + process + '.exe" /Automation'}
    def open_key(_root, path):
        if path not in registry:
            raise FileNotFoundError(path)
        return nullcontext(path)
    monkeypatch.setattr(winreg, 'OpenKey', open_key)
    monkeypatch.setattr(winreg, 'QueryValueEx', lambda key, _name: (registry[key], winreg.REG_SZ))
    assert office.registered_application(kind) == dict(progid=progid, process=process.lower(), application=label)


def test_missing_registration_never_launches_a_worker(tmp_path, monkeypatch):
    monkeypatch.setattr(office, 'registered_application', lambda _kind: None)
    monkeypatch.setattr(office.subprocess, 'run', lambda *_a, **_kw: pytest.fail('No registered app'))
    result = office.extract_office('synthetic.docx', b'not-real-protected-data', tmp_path)
    assert result['code'] == 'OFFICE_UNAVAILABLE'
    assert result['failure_stage'] == 'application_registration'
    assert not result['rows'] and not list(tmp_path.iterdir())


@pytest.mark.parametrize('extension,kind,progid,process,label', [
    ('.doc', 'Word', 'KWPS.Application', 'wps', 'WPS 文字'),
    ('.docx', 'Word', 'KWPS.Application', 'wps', 'WPS 文字'),
    ('.xls', 'Excel', 'KET.Application', 'et', 'WPS 表格'),
    ('.xlsx', 'Excel', 'KET.Application', 'et', 'WPS 表格'),
    ('.ppt', 'PowerPoint', 'KWPP.Application', 'wpp', 'WPS 演示'),
    ('.pptx', 'PowerPoint', 'KWPP.Application', 'wpp', 'WPS 演示'),
])
def test_wps_worker_arguments_and_recorded_provider_are_explicit(tmp_path, monkeypatch, extension, kind, progid, process, label):
    def select(selected):
        assert selected == kind
        return dict(progid=progid, process=process, application=label)
    monkeypatch.setattr(office, 'registered_application', select)
    def run(command, **_kwargs):
        assert '-m' in command and 'app.office_worker' in command
        assert command[command.index('--progid') + 1] == progid
        assert command[command.index('--process') + 1] == process
        Path(command[command.index('--output') + 1]).write_text(json.dumps(dict(
            status='partial', rows=[['正文段落 1', '合成资料']], reason='人工正文未核对',
            content_match='not_verified')), 'utf-8')
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr(office.subprocess, 'run', run)
    result = office.extract_office('synthetic' + extension, b'synthetic-only', tmp_path)
    assert result['application'] == label and result['automation_progid'] == progid
    assert result['process_name'] == process and result['content_match'] == 'not_verified'
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('extension,progid,process', [
    ('.docx', 'KWPS.Application', 'wps'), ('.xlsx', 'KET.Application', 'et'), ('.pptx', 'KWPP.Application', 'wpp'),
])
def test_worker_refuses_existing_wps_before_com_or_security_changes(tmp_path, monkeypatch, extension, progid, process):
    from types import SimpleNamespace
    from app import office_worker
    com = SimpleNamespace(COINIT_APARTMENTTHREADED=2, CoInitializeEx=lambda _: None, CoUninitialize=lambda: None,
                          CoCreateInstance=lambda *_: pytest.fail('Existing WPS must not be touched'))
    monkeypatch.setattr(office_worker, 'com_runtime', lambda: (com, None))
    monkeypatch.setattr(office_worker, 'processes', lambda _name: [{'pid': 12345}])
    result = office_worker.extract(tmp_path / ('synthetic' + extension), tmp_path / 'owner.json', progid, process)
    assert result['code'] == 'OFFICE_BUSY' and result['rows'] == []
    assert not (tmp_path / 'owner.json').exists()


def test_native_worker_failure_never_changes_or_retries_script_policy(tmp_path, monkeypatch):
    monkeypatch.setattr(office, 'registered_application', lambda _kind:
                        dict(progid='KWPS.Application', process='wps', application='WPS 文字'))
    monkeypatch.setattr(office.subprocess, 'run', lambda command, **_kw:
                        subprocess.CompletedProcess(command, 1, stderr=b'SecurityError UnauthorizedAccess synthetic-private-path'))
    result = office.extract_office('synthetic.docx', b'synthetic-only', tmp_path)
    assert result['code'] == 'OFFICE_NO_RESULT'
    assert result['failure_stage'] == 'worker_execution' and result['rows'] == []
    assert 'synthetic-private-path' not in json.dumps(result)
