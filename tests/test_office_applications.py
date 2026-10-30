"""Synthetic COM registration and worker guards; no WPS installation is claimed."""
import json
import os
from pathlib import Path
import subprocess

import pytest

from app import office

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows registry and PowerShell')


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
        assert '-STA' in command and '-ExecutionPolicy' not in command
        assert command[command.index('-ProgId') + 1] == progid
        assert command[command.index('-ProcessName') + 1] == process
        Path(command[command.index('-OutputPath') + 1]).write_text(json.dumps(dict(
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
def test_worker_refuses_existing_wps_before_com_or_security_changes(tmp_path, extension, progid, process):
    output = tmp_path / 'result.json'
    # Replace the process query in this subprocess with a synthetic busy app.
    # New-Object must never run, so no real COM application is created.
    wrapper = tmp_path / 'busy.ps1'
    quote = lambda path: "'" + str(path).replace("'", "''") + "'"
    wrapper.write_text("function Get-Process { [pscustomobject]@{Id=12345} }\n"
                       "function New-Object { throw 'COM_MUST_NOT_START' }\n"
                       '& ' + quote(office.WORKER) + ' -InputPath synthetic' + extension + ' -OutputPath ' + quote(output) +
                       ' -OwnerPath ' + quote(tmp_path / 'owner.json') +
                       ' -ProgId ' + progid + ' -ProcessName ' + process + '\n', 'utf-8-sig')
    run = subprocess.run([office.powershell_host(), '-NoProfile', '-NonInteractive', '-STA', '-File', str(wrapper)],
                         capture_output=True, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
    assert run.returncode == 0, run.stderr.decode(errors='replace')
    result = json.loads(output.read_text('utf-8-sig'))
    assert result['code'] == 'OFFICE_BUSY' and result['rows'] == []
    assert not (tmp_path / 'owner.json').exists()


def test_execution_policy_rejection_is_not_misreported_as_office_installation_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(office, 'registered_application', lambda _kind:
                        dict(progid='KWPS.Application', process='wps', application='WPS 文字'))
    monkeypatch.setattr(office.subprocess, 'run', lambda command, **_kw:
                        subprocess.CompletedProcess(command, 1, stderr=b'SecurityError UnauthorizedAccess synthetic-private-path'))
    result = office.extract_office('synthetic.docx', b'synthetic-only', tmp_path)
    assert result['code'] == 'OFFICE_WORKER_POLICY_BLOCKED'
    assert result['failure_stage'] == 'worker_execution' and result['rows'] == []
    assert '尚未打开资料' in result['reason']
    assert 'synthetic-private-path' not in json.dumps(result)
