"""Local, read-only Office extraction. Never calls a model or changes protection."""
import json
import os
from pathlib import Path
import subprocess
import shutil
import tempfile
import threading

OFFICE_EXTENSIONS = {'.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx'}
OFFICE_TIMEOUT = 90
_office_lock = threading.Lock()
WORKER = Path(__file__).with_name('office_worker.ps1')


def extract_office(title, data, folder):
    if os.name != 'nt':
        return dict(status='office_required', reason='需要在已安装 Microsoft Office 的 Windows 本机读取。', rows=[], code='OFFICE_UNAVAILABLE')
    # Office automation is serialized; each invocation owns only its own instance.
    with _office_lock, tempfile.TemporaryDirectory(prefix='office-read-', dir=folder) as temp:
        temp = Path(temp).resolve()
        source = temp / ('source' + Path(title).suffix.lower())
        source.write_bytes(data)
        output, owner = temp / 'result.json', temp / 'owner.json'
        modern = Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'PowerShell/7/pwsh.exe'
        legacy = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
        shell = shutil.which('pwsh.exe') or (str(modern) if modern.exists() else str(legacy))
        # Honor the selected host's execution policy; do not use ExecutionPolicy Bypass.
        command = [shell, '-NoProfile', '-NonInteractive', '-File', str(WORKER)]
        flags = subprocess.CREATE_NO_WINDOW
        try:
            subprocess.run(command + ['-InputPath', str(source), '-OutputPath', str(output), '-OwnerPath', str(owner)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=OFFICE_TIMEOUT, creationflags=flags, check=False)
            if not output.exists():
                return dict(status='office_required', reason='本机 Office 未返回读取结果；请确认 Office 已安装、已激活且企业策略允许自动化读取。', rows=[], code='OFFICE_NO_RESULT')
            result = json.loads(output.read_text(encoding='utf-8-sig'))
            if result.get('status') not in ('partial', 'read', 'office_required', 'permission_denied'):
                raise ValueError('Invalid Office status')
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
