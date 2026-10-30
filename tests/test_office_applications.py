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
    clsid = '{00000000-1111-2222-3333-444444444444}'
    registry = {progid + r'\CLSID': clsid,
                'CLSID\\' + clsid + r'\LocalServer32': '"C:\\Synthetic Office\\' + process + '.exe" /Automation'}
    def open_key(_root, path, _reserved=0, _access=winreg.KEY_READ):
        if path not in registry:
            raise FileNotFoundError(path)
        return nullcontext(path)
    monkeypatch.setattr(winreg, 'OpenKey', open_key)
    def query(key, name):
        if name:
            raise FileNotFoundError(name)
        return registry[key], winreg.REG_SZ
    monkeypatch.setattr(winreg, 'QueryValueEx', query)
    assert office.registered_application(kind) == dict(progid=progid, process=process.lower(), application=label,
                                                     clsid=clsid, registry_view=64)


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
def test_worker_refuses_reused_wps_before_security_or_document_changes(tmp_path, monkeypatch, extension, progid, process):
    from types import SimpleNamespace
    from app import office_worker
    collection = SimpleNamespace(Count=1)
    collection_name = {'.docx': 'Documents', '.xlsx': 'Workbooks', '.pptx': 'Presentations'}[extension]
    app = SimpleNamespace(**{collection_name: collection})
    com = SimpleNamespace(COINIT_APARTMENTTHREADED=2, CLSCTX_LOCAL_SERVER=4, IID_IDispatch='dispatch',
                          CoInitializeEx=lambda _: None, CoUninitialize=lambda: None,
                          CoCreateInstance=lambda *_: app)
    monkeypatch.setattr(office_worker, 'com_runtime', lambda: (com, SimpleNamespace(Dispatch=lambda value: value)))
    monkeypatch.setattr(office_worker, 'processes', lambda _name: [{'pid': 12345, 'started': '1'}])
    result = office_worker.extract(tmp_path / ('synthetic' + extension), tmp_path / 'owner.json', progid, process)
    assert result['code'] == 'OFFICE_BUSY' and result['rows'] == []
    assert not (tmp_path / 'owner.json').exists()
    assert not hasattr(app, 'AutomationSecurity')


def test_native_worker_failure_never_changes_or_retries_script_policy(tmp_path, monkeypatch):
    monkeypatch.setattr(office, 'registered_application', lambda _kind:
                        dict(progid='KWPS.Application', process='wps', application='WPS 文字'))
    monkeypatch.setattr(office.subprocess, 'run', lambda command, **_kw:
                        subprocess.CompletedProcess(command, 1, stderr=b'SecurityError UnauthorizedAccess synthetic-private-path'))
    result = office.extract_office('synthetic.docx', b'synthetic-only', tmp_path)
    assert result['code'] == 'OFFICE_NO_RESULT'
    assert result['failure_stage'] == 'worker_execution' and result['rows'] == []
    assert 'synthetic-private-path' not in json.dumps(result)


@pytest.mark.parametrize('one_group', [True, False])
def test_wps_background_and_new_process_group_are_distinguished(tmp_path, monkeypatch, one_group):
    from types import SimpleNamespace
    from app import office_worker
    events = []
    document = SimpleNamespace(ReadOnly=False, Close=lambda *_: events.append('close'))
    app = SimpleNamespace(Documents=SimpleNamespace(Count=0, Open=lambda *_: document),
                          Options=SimpleNamespace(UpdateLinksAtOpen=True), Quit=lambda *_: events.append('quit'))
    com = SimpleNamespace(COINIT_APARTMENTTHREADED=2, CLSCTX_LOCAL_SERVER=4, IID_IDispatch='dispatch',
                          CoInitializeEx=lambda _: None, CoUninitialize=lambda: None, CoCreateInstance=lambda *_: app)
    monkeypatch.setattr(office_worker, 'com_runtime', lambda: (com, SimpleNamespace(Dispatch=lambda value: value)))
    background = dict(pid=10, started='1', parent_pid=1)
    created = [dict(pid=20, started='2', parent_pid=1),
               dict(pid=21, started='3', parent_pid=20 if one_group else 1)]
    snapshots = iter([[background], [background, *created]])
    monkeypatch.setattr(office_worker, 'processes', lambda _: next(snapshots))
    owner = tmp_path / 'owner.json'
    result = office_worker.extract(tmp_path / 'synthetic.docx', owner, 'KWPS.Application', 'wps')
    if one_group:
        assert result['code'] == 'OFFICE_NOT_READONLY'
        assert [p['pid'] for p in json.loads(owner.read_text('utf-8'))] == [20, 21]
        assert events == ['close', 'quit']
    else:
        assert result['code'] == 'OFFICE_BUSY' and not owner.exists()
        assert events == [] and not hasattr(app, 'AutomationSecurity')


def test_wps_deferred_component_start_is_observed_after_collection_query(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from app import office_worker
    ready = False
    class Documents:
        @property
        def Count(self):
            nonlocal ready
            ready = True
            return 0
        def Open(self, *_):
            return SimpleNamespace(ReadOnly=False, Close=lambda *_: None)
    app = SimpleNamespace(Documents=Documents(), Options=SimpleNamespace(UpdateLinksAtOpen=True), Quit=lambda *_: None)
    com = SimpleNamespace(COINIT_APARTMENTTHREADED=2, CLSCTX_LOCAL_SERVER=4, IID_IDispatch='dispatch',
        CoInitializeEx=lambda _: None, CoUninitialize=lambda: None, CoCreateInstance=lambda *_: app)
    monkeypatch.setattr(office_worker, 'com_runtime', lambda: (com, SimpleNamespace(Dispatch=lambda value: value)))
    monkeypatch.setattr(office_worker, 'processes', lambda _: [dict(pid=20, started='2', parent_pid=1)] if ready else [])
    result = office_worker.extract(tmp_path/'synthetic.docx', tmp_path/'owner.json', 'KWPS.Application', 'wps')
    assert result['code'] == 'OFFICE_NOT_READONLY'
    assert (tmp_path/'owner.json').is_file()
