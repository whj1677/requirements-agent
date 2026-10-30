"""Local, read-only Office extraction. Never calls a model or changes protection."""
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import threading
import uuid

OFFICE_EXTENSIONS = {'.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx'}
OFFICE_TIMEOUT = 90
_office_lock = threading.Lock()
WORKER = Path(__file__).with_name('office_worker.py')
APPLICATIONS = {
    'Word': (('KWPS.Application', 'wps', 'WPS 文字'), ('Word.Application', 'WINWORD', 'Microsoft Word')),
    'Excel': (('KET.Application', 'et', 'WPS 表格'), ('Excel.Application', 'EXCEL', 'Microsoft Excel')),
    'PowerPoint': (('KWPP.Application', 'wpp', 'WPS 演示'), ('PowerPoint.Application', 'POWERPNT', 'Microsoft PowerPoint')),
}


def registered_application(kind):
    """Inspect registration without launching an app or changing COM registration."""
    inspected = inspect_application(kind)
    if any(check['status'] == 'permission_denied' for check in inspected['checks']):
        raise PermissionError('Office registration access denied')
    return inspected['selected']


def inspect_application(kind):
    """Check both COM registry views; diagnostics contain no user paths or documents."""
    import winreg
    expected = {process.lower(): label for _, process, label in APPLICATIONS[kind]}
    expected['wps'] = APPLICATIONS[kind][0][2]
    checks = []
    def value(path, view, name=''):
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, path, 0, winreg.KEY_READ | view) as key:
            return winreg.QueryValueEx(key, name)[0]
    for progid, _process, _label in APPLICATIONS[kind]:
        for bits, view in ((64, winreg.KEY_WOW64_64KEY), (32, winreg.KEY_WOW64_32KEY)):
            check = dict(progid=progid, registry_view=bits, status='not_found')
            checks.append(check)
            try:
                try:
                    clsid = value(progid + r'\CLSID', view)
                except FileNotFoundError:
                    current = value(progid + r'\CurVer', view)
                    clsid = value(current + r'\CLSID', view)
                clsid = '{' + str(uuid.UUID(clsid)) + '}'
                check.update(clsid=clsid, status='server_missing')
                server_key = 'CLSID\\' + clsid + r'\LocalServer32'
                command = value(server_key, view)
                try:
                    server = value(server_key, view, 'ServerExecutable')
                except FileNotFoundError:
                    server = command
                # A Microsoft compatibility ProgID can also refer to WPS.
                match = re.search(r'(?:^|[\\/])([^\\/"\s]+)\.exe(?:"|\s|$)', server, re.I)
                if match and match[1].lower() in expected:
                    process = match[1].lower()
                    # Current WPS uses wps.exe for all three components.
                    component = {'Excel': 'et', 'PowerPoint': 'wpp'}.get(kind)
                    if process == 'wps' and component and not re.search(r'(?:^|\s)/' + component + r'(?:\s|$)', command, re.I):
                        check['status'] = 'unrecognized_server'
                        continue
                    check['status'] = 'registered'
                    return dict(selected=dict(progid=progid, process=process, application=expected[process],
                                              clsid=clsid, registry_view=bits), checks=checks)
                check['status'] = 'unrecognized_server'
            except FileNotFoundError:
                continue
            except PermissionError:
                check['status'] = 'permission_denied'
                break
            except (OSError, ValueError, TypeError) as error:
                check.update(status='registration_error', winerror=getattr(error, 'winerror', None))
        if checks[-1]['status'] == 'permission_denied':
            break
    return dict(selected=None, checks=checks)


def registration_diagnostics():
    import struct
    from .version import VERSION
    return dict(version=VERSION, engine='native-com', process_bits=struct.calcsize('P') * 8,
                applications={kind: inspect_application(kind) for kind in APPLICATIONS},
                documents_opened=0, registry_modified=False, model_calls=0)


def worker_command():
    import sys
    return [sys.executable, '--office-worker'] if getattr(sys, 'frozen', False) else [sys.executable, '-m', 'app.office_worker']


def probe_runtime(folder):
    with tempfile.TemporaryDirectory(prefix='com-probe-', dir=folder) as temporary:
        output = Path(temporary).resolve() / 'result.json'
        completed = subprocess.run(worker_command() + ['--probe-runtime', '--output', str(output)],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=20,
                                   cwd=WORKER.parent.parent,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        return completed.returncode == 0 and output.is_file() and json.loads(output.read_text('utf-8')).get('status') == 'COM_RUNTIME_READY'


def extract_office(title, data, folder):
    if os.name != 'nt':
        return dict(status='office_required', reason='需要在支持本机办公软件自动化读取的 Windows 电脑处理。', rows=[], code='OFFICE_UNAVAILABLE')
    kind = {'.doc': 'Word', '.docx': 'Word', '.xls': 'Excel', '.xlsx': 'Excel',
            '.ppt': 'PowerPoint', '.pptx': 'PowerPoint'}.get(Path(title).suffix.lower())
    if not kind:
        raise ValueError('Unsupported local Office format')
    try:
        application = registered_application(kind)
    except PermissionError:
        return dict(status='permission_denied', reason='当前程序无权检查本机办公软件接口的注册信息；尚未打开资料，原件保留。',
                    rows=[], code='OFFICE_PERMISSION_DENIED', failure_stage='application_registration')
    if application is None:
        return dict(status='office_required', reason='当前程序未能识别对应的 Microsoft Office 或 WPS 读取接口，尚未打开资料；需核对接口注册情况，不能据此断定未安装办公软件。',
                    rows=[], code='OFFICE_UNAVAILABLE', failure_stage='application_registration')
    # Office automation is serialized; each invocation owns only its own instance.
    with _office_lock, tempfile.TemporaryDirectory(prefix='office-read-', dir=folder) as temp:
        temp = Path(temp).resolve()
        source = temp / ('source' + Path(title).suffix.lower())
        source.write_bytes(data)
        output, owner = temp / 'result.json', temp / 'owner.json'
        command = worker_command() + ['--input', str(source), '--output', str(output), '--owner', str(owner),
                                      '--progid', application['progid'], '--process', application['process']]
        if application.get('clsid'):
            command += ['--clsid', application['clsid']]
        flags = subprocess.CREATE_NO_WINDOW
        result = None
        try:
            completed = subprocess.run(command,
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                           cwd=WORKER.parent.parent,
                           timeout=OFFICE_TIMEOUT, creationflags=flags, check=False)
            if not output.exists():
                result = dict(status='office_required', reason=f'本机原生读取进程未返回结果（退出码 {completed.returncode}）；原件保留，尚未取得可用内容。',
                              rows=[], code='OFFICE_NO_RESULT', failure_stage='worker_execution', worker_exit_code=completed.returncode)
            else:
                result = json.loads(output.read_text(encoding='utf-8-sig'))
            if result.get('status') not in ('partial', 'read', 'office_required', 'permission_denied'):
                raise ValueError('Invalid Office status')
        except subprocess.TimeoutExpired:
            result = dict(status='office_required', reason='本机办公软件读取超时（90 秒），可能等待登录、密码或权限提示；未取得内容，原件保留。', rows=[], code='OFFICE_TIMEOUT', failure_stage='worker_timeout')
        except (OSError, ValueError):
            result = dict(status='office_required', reason='当前本机原生读取组件不可用，未取得有效内容；原件保留。', rows=[], code='OFFICE_UNAVAILABLE', failure_stage='worker_execution')
        finally:
            if owner.exists():
                from .office_process import cleanup_owned
                try:
                    cleaned = cleanup_owned(owner)
                except (OSError, ValueError, KeyError):
                    cleaned = False
                if result is not None:
                    result['owned_process_cleanup'] = 'verified' if cleaned else 'unconfirmed'
        result.update(application=application['application'], automation_progid=application['progid'],
                      process_name=application['process'], engine='native-com')
        if application.get('registry_view'):
            result['registry_view'] = application['registry_view']
        return result
