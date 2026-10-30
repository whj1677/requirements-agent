"""Local, read-only Office extraction. Never calls a model or changes protection."""
import json
import os
from pathlib import Path
import re
import subprocess
import shutil
import tempfile
import threading

OFFICE_EXTENSIONS = {'.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx'}
OFFICE_TIMEOUT = 90
_office_lock = threading.Lock()
WORKER = Path(__file__).with_name('office_worker.ps1')
APPLICATIONS = {
    'Word': (('KWPS.Application', 'wps', 'WPS 文字'), ('Word.Application', 'WINWORD', 'Microsoft Word')),
    'Excel': (('KET.Application', 'et', 'WPS 表格'), ('Excel.Application', 'EXCEL', 'Microsoft Excel')),
    'PowerPoint': (('KWPP.Application', 'wpp', 'WPS 演示'), ('PowerPoint.Application', 'POWERPNT', 'Microsoft PowerPoint')),
}


def registered_application(kind):
    """Inspect registration without launching an app or changing COM registration."""
    import winreg
    expected = {process.lower(): label for _, process, label in APPLICATIONS[kind]}
    for progid, _process, _label in APPLICATIONS[kind]:
        try:
            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, progid + r'\CLSID') as key:
                clsid = winreg.QueryValueEx(key, '')[0]
            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, 'CLSID\\' + clsid + r'\LocalServer32') as key:
                server = winreg.QueryValueEx(key, '')[0]
        except FileNotFoundError:
            continue
        # Compatibility ProgIDs can point to WPS; inspect the registered executable.
        match = re.search(r'(?:^|[\\/])([^\\/"\s]+)\.exe(?:"|\s|$)', server, re.I)
        if match and match[1].lower() in expected:
            process = match[1].lower()
            return dict(progid=progid, process=process, application=expected[process])
    return None


def powershell_host():
    modern = Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'PowerShell/7/pwsh.exe'
    legacy = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    return shutil.which('pwsh.exe') or (str(modern) if modern.exists() else str(legacy))


def extract_office(title, data, folder):
    if os.name != 'nt':
        return dict(status='office_required', reason='需要在支持本机办公软件自动化读取的 Windows 电脑处理。', rows=[], code='OFFICE_UNAVAILABLE')
    kind = {'.doc': 'Word', '.docx': 'Word', '.xls': 'Excel', '.xlsx': 'Excel',
            '.ppt': 'PowerPoint', '.pptx': 'PowerPoint'}.get(Path(title).suffix.lower())
    if not kind:
        raise ValueError('Unsupported local Office format')
    application = registered_application(kind)
    if application is None:
        return dict(status='office_required', reason='本机未发现对应的 Microsoft Office 或 WPS 自动化接口；安装了办公软件不等于已提供可用的读取接口。',
                    rows=[], code='OFFICE_UNAVAILABLE', failure_stage='application_registration')
    # Office automation is serialized; each invocation owns only its own instance.
    with _office_lock, tempfile.TemporaryDirectory(prefix='office-read-', dir=folder) as temp:
        temp = Path(temp).resolve()
        source = temp / ('source' + Path(title).suffix.lower())
        source.write_bytes(data)
        output, owner = temp / 'result.json', temp / 'owner.json'
        shell = powershell_host()
        # Honor the selected host's execution policy; do not use ExecutionPolicy Bypass.
        command = [shell, '-NoProfile', '-NonInteractive', '-STA', '-File', str(WORKER)]
        flags = subprocess.CREATE_NO_WINDOW
        try:
            completed = subprocess.run(command + ['-InputPath', str(source), '-OutputPath', str(output), '-OwnerPath', str(owner),
                                                  '-ProgId', application['progid'], '-ProcessName', application['process']],
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                           timeout=OFFICE_TIMEOUT, creationflags=flags, check=False)
            if not output.exists():
                diagnostic = (completed.stderr or b'').decode('utf-8', errors='replace')
                blocked = 'PSSecurityException' in diagnostic or ('SecurityError' in diagnostic and 'UnauthorizedAccess' in diagnostic)
                reason = ('PowerShell 执行策略拒绝运行本机读取脚本；尚未打开资料，请由本机管理员确认允许的读取方式。' if blocked else
                          f'本机文档读取进程未返回结果（退出码 {completed.returncode}），可能在启动或脚本执行阶段受限；尚不能据此判断办公软件是否安装、激活或文件是否可读。')
                return dict(status='office_required', reason=reason, rows=[],
                            code='OFFICE_WORKER_POLICY_BLOCKED' if blocked else 'OFFICE_NO_RESULT', failure_stage='worker_execution',
                            worker_exit_code=completed.returncode, application=application['application'],
                            automation_progid=application['progid'], process_name=application['process'], worker_shell=Path(shell).name)
            result = json.loads(output.read_text(encoding='utf-8-sig'))
            if result.get('status') not in ('partial', 'read', 'office_required', 'permission_denied'):
                raise ValueError('Invalid Office status')
            result.update(application=application['application'], automation_progid=application['progid'],
                          process_name=application['process'])
            return result
        except subprocess.TimeoutExpired:
            return dict(status='office_required', reason='本机 Office 读取超时（90 秒），可能等待登录、密码或权限提示；未取得内容，原件保留。', rows=[], code='OFFICE_TIMEOUT')
        except (OSError, ValueError):
            return dict(status='office_required', reason='当前本机 Office 读取方式不可用，未取得有效内容；原件保留。', rows=[], code='OFFICE_UNAVAILABLE')
        finally:
            # Worker records PID + creation time only after verifying a new instance.
            # Never use taskkill /IM or close a pre-existing user application.
            if owner.exists():
                try:
                    subprocess.run(command + ['-CleanupOwner', str(owner)], stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, timeout=10, creationflags=flags, check=False)
                except (OSError, subprocess.TimeoutExpired):
                    pass
