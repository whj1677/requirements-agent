"""Verify a fresh Windows customer installation and in-place upgrade offline.

Only the selected installer and its verified process descendants are managed.
An existing product registration or occupied 8765 port blocks the entire run.
The output directory must be new. Upload installation-verification.json only;
the private working directory is deliberately excluded from CI artifacts.
"""
import argparse
import base64
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import winreg

APP_ID = '{F72D927C-410A-4C24-8435-BB4D13C7D1B8}_is1'
REGISTRY_KEY = 'SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\' + APP_ID
ORIGIN = 'http://127.0.0.1:8765'
PORT = 8765


class VerificationFailure(RuntimeError):
    """Stable, non-sensitive failure codes for the public report."""


def require(condition, code):
    if not condition:
        raise VerificationFailure(code)


def sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def registrations():
    found = []
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
            try:
                with winreg.OpenKey(hive, REGISTRY_KEY, 0, winreg.KEY_READ | view) as key:
                    try:
                        location = winreg.QueryValueEx(key, 'InstallLocation')[0]
                    except FileNotFoundError:
                        location = ''
                    found.append({'hive': hive, 'view': view, 'location': location})
            except FileNotFoundError:
                continue
    return found


def prepare_paths(installer, output):
    # Inspect the lexical absolute path before resolve() removes junctions.
    unresolved_output = Path(output).absolute()
    for parent in (unresolved_output, *unresolved_output.parents):
        try:
            attributes = parent.lstat().st_file_attributes
        except FileNotFoundError:
            continue
        require(not bool(attributes & 0x400), 'OUTPUT_REPARSE_POINT')
    installer, output = Path(installer).resolve(), unresolved_output.resolve()
    require(installer.is_file() and installer.suffix.lower() == '.exe', 'INSTALLER_NOT_FOUND')
    require(not output.exists(), 'OUTPUT_ALREADY_EXISTS')
    require(output != installer.parent and output not in installer.parents, 'OUTPUT_CONTAINS_INPUT')
    require(output.parent.is_dir(), 'OUTPUT_PARENT_NOT_FOUND')
    require(not registrations(), 'EXISTING_PRODUCT_REGISTRATION')
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            probe.bind(('127.0.0.1', PORT))
        except OSError as error:
            raise VerificationFailure('PORT_ALREADY_IN_USE') from error
    output.mkdir()
    return installer, output


def customer_environment(private):
    system = Path(os.environ['SystemRoot']).resolve()
    profile = private / 'profile'
    local = profile / 'Local'
    roaming = profile / 'Roaming'
    temporary = private / 'temp'
    for path in (local, roaming, temporary):
        path.mkdir(parents=True, exist_ok=True)
    # This is an allowlist, not a filtered copy of the developer environment.
    environment = {
        'SystemRoot': str(system), 'WINDIR': str(system),
        'SystemDrive': system.drive, 'ComSpec': str(system / 'System32/cmd.exe'),
        'PATH': str(system) + os.pathsep + str(system / 'System32'),
        'PATHEXT': '.COM;.EXE;.BAT;.CMD', 'OS': 'Windows_NT',
        'USERPROFILE': str(profile), 'LOCALAPPDATA': str(local), 'APPDATA': str(roaming),
        'TEMP': str(temporary), 'TMP': str(temporary),
        'PYTHONUTF8': '1', 'NO_PROXY': '127.0.0.1,localhost',
    }
    return environment, local / 'RequirementsAgent'


def kernel_api():
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    return kernel


def process_identity(handle):
    kernel = kernel_api()
    size = wintypes.DWORD(32768)
    buffer = ctypes.create_unicode_buffer(size.value)
    require(kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)),
            'PROCESS_IMAGE_UNVERIFIABLE')
    created, exited, kernel_time, user_time = [wintypes.FILETIME() for _ in range(4)]
    require(kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                                   ctypes.byref(kernel_time), ctypes.byref(user_time)),
            'PROCESS_CREATION_UNVERIFIABLE')
    stamp = (created.dwHighDateTime << 32) | created.dwLowDateTime
    return Path(buffer.value).resolve(), stamp


def process_parents():
    class Entry(ctypes.Structure):
        _fields_ = [('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD),
                    ('pid', wintypes.DWORD), ('heap', ctypes.c_void_p),
                    ('module', wintypes.DWORD), ('threads', wintypes.DWORD),
                    ('parent', wintypes.DWORD), ('priority', wintypes.LONG),
                    ('flags', wintypes.DWORD), ('name', wintypes.WCHAR * 260)]
    kernel = kernel_api()
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    require(snapshot and snapshot != ctypes.c_void_p(-1).value, 'PROCESS_SNAPSHOT_FAILED')
    entries = {}
    try:
        entry = Entry()
        entry.dwSize = ctypes.sizeof(entry)
        more = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while more:
            entries[entry.pid] = entry.parent
            more = kernel.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)
    return entries


def descendant_order(root_pid, parents):
    depths = {root_pid: 0}
    for _ in range(len(parents) + 1):
        added = False
        for pid, parent in parents.items():
            if pid not in depths and parent in depths:
                depths[pid] = depths[parent] + 1
                added = True
        if not added:
            break
    return sorted((pid for pid in depths if pid != root_pid), key=lambda pid: depths[pid], reverse=True)


class OwnedProcess:
    def __init__(self, command, environment, private, install):
        self.executable = Path(command[0]).resolve()
        self.roots = [private.resolve(), install.resolve()]
        self.process = subprocess.Popen(command, env=environment, cwd=private,
                                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            self.handle = int(self.process._handle)
            actual, self.created = process_identity(self.handle)
            require(actual == self.executable, 'CREATED_PROCESS_IMAGE_MISMATCH')
        except BaseException:
            # The caller cannot register an object whose constructor failed.
            # Popen still holds the exact root process handle we just created;
            # never enumerate descendants through an unverified PID here.
            try:
                try:
                    if self.process.poll() is None:
                        self.process.terminate()
                finally:
                    self.process.wait(timeout=10)
            except BaseException as cleanup_error:
                raise VerificationFailure('CREATED_PROCESS_INITIALIZATION_CLEANUP_FAILED') from cleanup_error
            raise

    def wait(self, timeout):
        try:
            return self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired as error:
            diagnostics = shortcut_diagnostics(self) if hasattr(self, 'shortcut_trace') else None
            self.terminate_owned()
            failure = VerificationFailure('OWNED_PROCESS_TIMEOUT')
            if diagnostics is not None:
                diagnostics['wrapper_exit_code_after_cleanup'] = self.process.poll()
                failure.add_note(json.dumps({'shortcut_launch': diagnostics}))
            raise failure from error

    def terminate_owned(self):
        """Only held root handle and verified, younger descendants may be stopped."""
        if self.process.poll() is not None:
            return
        kernel = kernel_api()
        root_image, root_stamp = process_identity(self.handle)
        require(root_image == self.executable and root_stamp == self.created,
                'CLEANUP_ROOT_IDENTITY_CHANGED')
        diagnostics = []
        natural_exit = []
        owned_descendants = []
        parents=process_parents()
        verified_created={self.process.pid:self.created}
        for pid in reversed(descendant_order(self.process.pid, parents)):
            if parents.get(pid) not in verified_created:continue
            handle = kernel.OpenProcess(0x1000 | 0x0001 | 0x00100000, False, pid)
            if not handle:
                error = ctypes.get_last_error()
                if error not in (87, 1168):
                    diagnostics.append(dict(phase='open_descendant', pid=pid, win32_error=error))
                continue  # A process that already exited needs no termination.
            try:
                try:
                    image, created = process_identity(handle)
                except VerificationFailure:
                    wait = kernel.WaitForSingleObject(handle, 0)
                    if wait != 0:
                        diagnostics.append(dict(phase='identify_descendant', pid=pid, wait_result=wait))
                    continue
                # Parent PIDs survive in Windows after their process exits and
                # can be reused. Older trees are unrelated, not ours to await.
                if created < verified_created[parents[pid]]:continue
                verified_created[pid]=created
                allowed = image == self.executable or any(image.is_relative_to(root) for root in self.roots)
                if not allowed:
                    # Windows may attach a system console host. Never kill it;
                    # allow it to exit naturally after its owned root closes.
                    natural_exit.append((pid, handle))
                    handle = None
                    continue
                owned_descendants.append((pid,handle))
                handle=None
            finally:
                if handle:
                    kernel.CloseHandle(handle)
        # Identify parent chains before stopping them; terminate deepest first.
        for pid,handle in reversed(owned_descendants):
            try:
                if not kernel.TerminateProcess(handle, 1):
                    error = ctypes.get_last_error()
                    wait = kernel.WaitForSingleObject(handle, 0)
                    if wait != 0:
                        diagnostics.append(dict(phase='terminate_descendant', pid=pid,
                                                win32_error=error, wait_result=wait))
            finally:
                kernel.CloseHandle(handle)
        self.process.terminate()  # Popen retains the exact created process handle.
        self.process.wait(timeout=10)
        for pid, handle in natural_exit:
            try:
                wait = kernel.WaitForSingleObject(handle, 3000)
                if wait != 0:
                    diagnostics.append(dict(phase='wait_unowned_descendant', pid=pid, wait_result=wait))
            finally:
                kernel.CloseHandle(handle)
        if diagnostics:
            error = VerificationFailure('CLEANUP_DESCENDANT_IDENTITY_UNVERIFIED')
            error.cleanup_diagnostics = diagnostics
            error.add_note(json.dumps({'cleanup_diagnostics': diagnostics}))
            raise error


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def request(path, method='GET', csrf=None, payload=None, origin=ORIGIN, *, raw_body=None, content_type=None):
    headers = {'Origin': origin} if origin is not None else {}
    if csrf is not None:
        headers['X-Desktop-CSRF' if path.startswith('/desktop/') else 'X-CSRF-Token'] = csrf
    if payload is not None:
        headers['Content-Type'] = 'application/json'
    if content_type:
        headers['Content-Type'] = content_type
    req = urllib.request.Request(ORIGIN + path, method=method,
                                 data=raw_body if raw_body is not None else json.dumps(payload).encode('utf-8') if payload is not None else
                                 (b'' if method == 'POST' else None), headers=headers)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        response = opener.open(req, timeout=3)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        body = response.read(512 * 1024 + 1)
        require(len(body) <= 512 * 1024, 'HTTP_RESPONSE_TOO_LARGE')
        return response.status, body, response.headers


def request_json(path, method='GET', csrf=None, payload=None, origin=ORIGIN):
    status, body, _headers = request(path, method, csrf, payload, origin)
    try:
        value = json.loads(body)
    except (ValueError, UnicodeError) as error:
        raise VerificationFailure('INVALID_DESKTOP_RESPONSE') from error
    require(isinstance(value, (dict, list)), 'INVALID_DESKTOP_RESPONSE')
    return status, value


def instance_identity(executable, home):
    return hashlib.sha256((sha256(executable) + str(home.resolve()).casefold()).encode('utf-8')).hexdigest()[:24]


def wait_for_instance(process, expected, timeout=45, launcher=False):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        exit_code = process.process.poll()
        require(exit_code is None or (launcher and exit_code == 0), 'DESKTOP_PROCESS_EXITED')
        try:
            status, value = request_json('/desktop/api/instance')
            require(status == 200 and value.get('instance_id') == expected,
                    'DESKTOP_INSTANCE_IDENTITY_MISMATCH')
            return value
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            time.sleep(0.2)
    raise VerificationFailure('DESKTOP_STARTUP_TIMEOUT')


def shutdown_owned(process, expected):
    deadline = time.monotonic() + 5
    while True:
        status, value = request_json('/desktop/api/instance')
        require(status == 200 and value.get('instance_id') == expected, 'SHUTDOWN_INSTANCE_IDENTITY_MISMATCH')
        status, value = request_json('/desktop/api/status')
        require(status == 200 and isinstance(value.get('csrf'), str), 'SHUTDOWN_CSRF_UNAVAILABLE')
        status, value = request_json('/desktop/api/shutdown', 'POST', value['csrf'])
        if not hasattr(process, 'shutdown_attempts'):
            process.shutdown_attempts = []
        attempt = {'http_status': status, 'code': value.get('code')}
        process.shutdown_attempts.append(attempt)
        if status == 200 and value.get('status') == 'STOPPING':
            break
        # A real browser may still be finishing a poll. Only the gateway's
        # explicit busy response allows a short wait; each retry rechecks identity.
        if status != 409 or value.get('code') != 'WORKBENCH_BUSY' or time.monotonic() >= deadline:
            failure = VerificationFailure('GRACEFUL_SHUTDOWN_REJECTED')
            failure.add_note(json.dumps({'shutdown_response': attempt}))
            raise failure
        time.sleep(0.2)
    require(process.wait(20) == 0, 'GRACEFUL_SHUTDOWN_EXIT_FAILED')
    wait_for_port_release()


def wait_for_port_release(timeout=10):
    deadline = time.monotonic() + timeout
    while True:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            try:
                probe.bind(('127.0.0.1', PORT))
                return
            except OSError as error:
                if time.monotonic() >= deadline:
                    raise VerificationFailure('PORT_NOT_RELEASED') from error
        time.sleep(0.1)


def verify_installed_files(install):
    manifest = json.loads((install / 'release-manifest.json').read_text('utf-8'))
    require(manifest.get('distribution') == 'windows-x64-direct', 'WRONG_DISTRIBUTION')
    require(manifest.get('working_tree_dirty') is False, 'DIRTY_BUILD_NOT_ALLOWED')
    require(manifest.get('native_file_bytes_verified') is True, 'NATIVE_BYTE_CHECK_MISSING')
    require(manifest.get('native_file_count', 0) > 0, 'NATIVE_BYTE_CHECK_EMPTY')
    files = manifest.get('files')
    require(isinstance(files, dict) and files, 'FILE_MANIFEST_EMPTY')
    for relative, expected in files.items():
        path = (install / relative).resolve()
        require(path.is_relative_to(install.resolve()) and not path.is_symlink(), 'FILE_MANIFEST_ESCAPES_INSTALL')
        require(path.is_file() and sha256(path) == expected, 'INSTALLED_FILE_HASH_MISMATCH')
    return manifest


def owned_registration(install):
    present = registrations()
    require(present, 'INSTALL_REGISTRATION_MISSING')
    require(all(row['location'] and Path(row['location']).resolve() == install.resolve()
                for row in present), 'INSTALL_REGISTRATION_NOT_OWNED')


def verify_business_http(expected):
    status, instance = request_json('/desktop/api/instance')
    require(status == 200 and instance.get('product') == 'requirements-agent' and
            instance.get('instance_id') == expected and instance.get('business_ready') is True,
            'DIRECT_INSTANCE_NOT_READY')
    status, desktop = request_json('/desktop/api/status')
    require(status == 200 and desktop.get('product') == 'requirements-agent' and
            desktop.get('instance_id') == expected and desktop.get('business_ready') is True and
            desktop.get('license_required') is False and isinstance(desktop.get('csrf'), str) and
            bool(desktop['csrf']), 'DIRECT_STATUS_NOT_READY')
    status, body, headers = request('/')
    require(status == 200 and 'text/html' in headers.get('Content-Type', '') and body,
            'FRONTEND_UNAVAILABLE')
    status, session = request_json('/api/session')
    require(status == 200 and session.get('mode') == 'live', 'SESSION_UNAVAILABLE')
    status, projects = request_json('/api/projects')
    require(status == 200 and isinstance(projects, list), 'PROJECT_LIST_UNAVAILABLE')
    status, _body, _headers = request('/activation/')
    require(status in (301, 302, 303, 307, 308), 'OLD_ACTIVATION_NOT_REDIRECTED')
    for path in ('/activation/api/status', '/activation/api/instance', '/activation/api/shutdown'):
        require(request(path)[0] == 404, 'OLD_LICENSE_ENDPOINT_STILL_PRESENT')
    for path in ('/activation/api/request', '/activation/api/install', '/activation/api/start'):
        require(request(path, 'POST')[0] == 404, 'OLD_LICENSE_ENDPOINT_STILL_PRESENT')
    return desktop, projects


def verify_shutdown_security(csrf):
    status, _ = request_json('/desktop/api/shutdown', 'POST', csrf, origin='http://evil.invalid')
    require(status == 403, 'SHUTDOWN_ORIGIN_NOT_ENFORCED')
    status, _ = request_json('/desktop/api/shutdown', 'POST', 'invalid-csrf')
    require(status == 403, 'SHUTDOWN_CSRF_NOT_ENFORCED')


def powershell_command(script):
    executable = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    encoded = base64.b64encode(script.encode('utf-16le')).decode('ascii')
    return [str(executable), '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded]


def shell_json(script, payload=None, *, environment=None):
    script = "$ErrorActionPreference='Stop'; [Console]::OutputEncoding=New-Object Text.UTF8Encoding($false); " + script
    result = subprocess.run(powershell_command(script),
                            input=json.dumps(payload, ensure_ascii=True).encode('ascii'),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30,
                            env=environment,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    require(result.returncode == 0, 'SHORTCUT_INSPECTION_FAILED')
    return json.loads(result.stdout.decode('utf-8-sig'))


def shortcut_paths(environment=None):
    # Windows expands known-folder registry values in the caller's environment,
    # just as Inno does. Resolve even before the installer creates these folders.
    folders = shell_json("[pscustomobject]@{ desktop=[Environment]::GetFolderPath('DesktopDirectory','DoNotVerify'); "
                         "programs=[Environment]::GetFolderPath('Programs','DoNotVerify') } | ConvertTo-Json -Compress",
                         environment=environment)
    require(folders.get('desktop') and folders.get('programs'), 'SHORTCUT_FOLDERS_UNAVAILABLE')
    group = Path(folders['programs']) / '需求 Agent'
    return {'desktop': Path(folders['desktop']) / '需求 Agent.lnk',
            'start_menu': group / '需求 Agent.lnk', 'guide': group / '使用说明.lnk'}


def verify_shortcuts(links, install, diagnostics=None):
    present = {name: str(path) for name, path in links.items() if path.is_file()}
    # WScript.Shell.CreateShortcut silently loses paths outside the system
    # ANSI code page. Read existing links through the Unicode Windows Shell.
    values = shell_json("$paths=[Console]::In.ReadToEnd() | ConvertFrom-Json; "
                        "$shell=New-Object -ComObject Shell.Application; $result=@{}; "
                        "foreach($entry in $paths.PSObject.Properties) { "
                        "$folder=$shell.Namespace([IO.Path]::GetDirectoryName([string]$entry.Value)); "
                        "$item=$folder.ParseName([IO.Path]::GetFileName([string]$entry.Value)); $link=$item.GetLink; "
                        "$result[$entry.Name]=@{target=$link.Path; working_directory=$link.WorkingDirectory; arguments=$link.Arguments} }; "
                        "$result | ConvertTo-Json -Depth 3 -Compress",
                        present) if present else {}
    if diagnostics is not None:
        diagnostics.update({name: {'path': str(path), 'exists': name in present,
                                  'expected_target': str(install / ('docs/windows-installation.html' if name == 'guide'
                                                                    else 'RequirementsAgent.exe')),
                                  'expected_working_directory': str(install) if name != 'guide' else None,
                                  'found': values.get(name)} for name, path in links.items()})
    require(len(present) == len(links), 'SHORTCUT_MISSING')
    for name in ('desktop', 'start_menu'):
        row = values[name]
        require(Path(row['target']).resolve() == (install / 'RequirementsAgent.exe').resolve(),
                'SHORTCUT_TARGET_MISMATCH')
        require(Path(row['working_directory']).resolve() == install.resolve(), 'SHORTCUT_WORKDIR_MISMATCH')
        require(not row['arguments'], 'SHORTCUT_ARGUMENTS_UNEXPECTED')
    require(Path(values['guide']['target']).resolve() == (install / 'docs/windows-installation.html').resolve(),
            'SHORTCUT_GUIDE_TARGET_MISMATCH')


def open_shortcut(path, environment, private, install):
    # Open the actual .lnk through ShellExecute. Wait only for the launcher;
    # the service it starts is subsequently checked and stopped through its
    # installation/profile identity and CSRF endpoint, never guessed by PID.
    quoted = str(path).replace("'", "''")
    trace = private / ('shortcut-' + str(time.time_ns()) + '.trace')
    trace_path = str(trace).replace("'", "''")
    # Record before invoking cmdlets so a cold PowerShell or Shell startup can
    # be distinguished from waiting for the target. Never log arguments/env.
    script = ("$ErrorActionPreference='Stop'; $trace='" + trace_path + "'; "
              "$clock=[Diagnostics.Stopwatch]::StartNew(); "
              "function Trace([string]$name,[string]$value) { "
              "[IO.File]::AppendAllText($trace,$name+\"`t\"+$value+\"`t\"+$clock.ElapsedMilliseconds+[Environment]::NewLine) }; "
              "Trace 'phase' 'powershell_started'; try { "
              "Trace 'phase' 'shell_open_requested'; $process=Start-Process -FilePath '" + quoted +
              "' -PassThru -WindowStyle Hidden; Trace 'phase' 'shell_open_returned'; "
              "Trace 'target_pid' ([string]$process.Id); "
              "try { Trace 'target_image' $process.ProcessName } catch { Trace 'target_image' 'unavailable' }; "
              "Trace 'phase' 'wait_for_target'; $process.WaitForExit(); "
              "Trace 'target_exit_code' ([string]$process.ExitCode); Trace 'phase' 'target_exited'; "
              "exit $process.ExitCode } catch { Trace 'phase' 'shell_error'; exit 1 }")
    owned = OwnedProcess(powershell_command(script), environment, private, install)
    owned.shortcut_trace = trace
    return owned


def shortcut_diagnostics(owned):
    result = {'wrapper_exit_code': owned.process.poll(), 'stages': []}
    trace = owned.shortcut_trace
    if trace.is_file():
        for line in trace.read_text('utf-8-sig').splitlines():
            fields = line.split('\t')
            # A timeout can sample the last append before its line is complete.
            if len(fields) != 3 or not fields[2].isdigit():
                continue
            name, value, elapsed = fields
            if name == 'phase':
                result['stages'].append({'phase': value, 'elapsed_ms': int(elapsed)})
            elif name in ('target_pid', 'target_image', 'target_exit_code'):
                result[name] = value
    return result


def check_shortcut_reuse(links, first, expected, environment, private, install, processes):
    status, before = request_json('/api/session')
    started = before.get('backend', {}).get('started_at')
    require(status == 200 and started, 'SHORTCUT_RUNTIME_IDENTITY_UNAVAILABLE')
    # The other shortcut exercises the same launcher while the first service
    # is still running. A second service must not replace its startup identity.
    second = 'start_menu' if first == 'desktop' else 'desktop'
    repeated = open_shortcut(links[second], environment, private, install)
    processes.append(repeated)
    require(repeated.wait(60) == 0, 'SHORTCUT_REPEAT_EXIT_FAILED')
    status, instance = request_json('/desktop/api/instance')
    require(status == 200 and instance.get('instance_id') == expected, 'SHORTCUT_REPEAT_INSTANCE_CHANGED')
    status, after = request_json('/api/session')
    require(status == 200 and after.get('backend', {}).get('started_at') == started,
            'SHORTCUT_REPEAT_RESTARTED_SERVICE')


SYNTHETIC_ATTACHMENT = b'SYNTHETIC INSTALLER CHECK: preserve this source attachment and its history.\n'


def seed_business_data():
    status, session = request_json('/api/session')
    require(status == 200 and session.get('csrf'), 'BUSINESS_SESSION_UNAVAILABLE')
    csrf = session['csrf']
    status, project = request_json('/api/projects', 'POST', csrf,
                                  payload={'name': '离线安装验收合成项目'})
    require(status == 200 and isinstance(project.get('id'), str), 'SYNTHETIC_PROJECT_CREATE_FAILED')
    pid = project['id']
    boundary = 'requirements-agent-installer-check'
    parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="expected_revision"\r\n\r\n{project["revision"]}\r\n'.encode(),
             f'--{boundary}\r\nContent-Disposition: form-data; name="purpose"\r\n\r\ngoal\r\n'.encode(),
             f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="installer-synthetic.txt"\r\nContent-Type: text/plain\r\n\r\n'.encode(),
             SYNTHETIC_ATTACHMENT, f'\r\n--{boundary}--\r\n'.encode()]
    status, body, _ = request(f'/api/projects/{pid}/sources/file', 'POST', csrf,
                              raw_body=b''.join(parts), content_type='multipart/form-data; boundary=' + boundary)
    require(status == 200, 'SYNTHETIC_ATTACHMENT_UPLOAD_FAILED')
    source = json.loads(body)
    require(source.get('sha256') == hashlib.sha256(SYNTHETIC_ATTACHMENT).hexdigest(),
            'SYNTHETIC_ATTACHMENT_HASH_MISMATCH')
    config = dict(name='Installer offline verification', base_url='https://installer-verification.invalid',
                  model='synthetic-never-called', key_env='RA_INSTALLER_SYNTHETIC_KEY', vision='unsupported')
    require(request_json('/api/models/model', 'PUT', csrf, config)[0] == 200, 'SYNTHETIC_CONFIG_SAVE_FAILED')
    status, key_state = request_json('/api/models/model/key', 'PUT', csrf,
                                    {'key': 'installer-synthetic-key-never-sent-20261029', 'persist': True})
    require(status == 200 and key_state.get('key_configured') is True and
            key_state.get('key_source') == 'protected_store', 'SYNTHETIC_KEY_NOT_PERSISTED')
    return pid, source['id']


def business_snapshot(pid, source_id):
    result = {}
    for name, path in [('project', f'/api/projects/{pid}'), ('history', f'/api/projects/{pid}/history'),
                       ('models', '/api/models')]:
        status, result[name] = request_json(path)
        require(status == 200, 'BUSINESS_SNAPSHOT_UNREADABLE')
    require(result['project'].get('id') == pid and len(result['history']) >= 2, 'BUSINESS_HISTORY_MISSING')
    model = result['models']['model']
    require(model.get('key_configured') is True and model.get('key_source') == 'protected_store' and
            not model.get('key_storage_error'), 'PERSISTED_KEY_UNREADABLE')
    status, body, _ = request(f'/api/projects/{pid}/sources/{source_id}/file')
    require(status == 200 and body == SYNTHETIC_ATTACHMENT, 'SAVED_ATTACHMENT_UNREADABLE')
    result['attachment_sha256'] = hashlib.sha256(body).hexdigest()
    return result


def install_command(installer, install, log, shortcuts=False):
    icons = ['/TASKS=desktopicon'] if shortcuts else ['/NOICONS', '/TASKS=!desktopicon']
    return [str(installer), '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART',
            '/CURRENTUSER', *icons, '/DIR=' + str(install), '/LOG=' + str(log)]


def verify_packaged_smoke(executable, environment, private, install, processes, name, native_com=False):
    result_file = private / (name + '-offline-smoke.json')
    check = OwnedProcess([str(executable), '--smoke-test', '--output', str(result_file)],
                         environment, private, install)
    processes.append(check)
    require(check.wait(120) == 0, 'OFFLINE_SMOKE_EXIT_FAILED')
    value = json.loads(result_file.read_text('utf-8'))
    expected_checks = {'docx_subprocess', 'xlsx_subprocess', 'pptx_subprocess',
                       'image_subprocess', 'bundled_chromium', 'session', 'frontend', 'projects'}
    if native_com:
        expected_checks.add('windows_com_runtime')
    require(value.get('status') == 'OFFLINE_SMOKE_VERIFIED' and value.get('model_calls') == 0 and
            isinstance(value.get('checks'), dict) and
            all(value['checks'].get(name) is True for name in expected_checks),
            'OFFLINE_SMOKE_WRONG_RESULT')


def verify(installer, output, upgrade_from=None, shortcuts=False):
    initial_installer = Path(upgrade_from).resolve() if upgrade_from else Path(installer).resolve()
    require(initial_installer.is_file() and initial_installer.suffix.lower() == '.exe', 'INITIAL_INSTALLER_NOT_FOUND')
    links = shortcut_paths() if shortcuts else {}
    require(not any(path.exists() for path in links.values()), 'EXISTING_SHORTCUT')
    installer, output = prepare_paths(installer, output)
    report = {'format_version': 1, 'status': 'INSTALLATION_VERIFICATION_RUNNING',
              'installer_sha256': sha256(installer), 'checks': {}, 'model_calls': 0,
              'shortcuts_requested': shortcuts,
              'initial_installer_sha256': sha256(initial_installer),
              'business_validation': {'synthetic_offline': 'RUNNING', 'real_model': 'NOT_RUN'}}
    private, install = output / 'private', output / 'installed-app'
    private.mkdir()
    environment, home = customer_environment(private)
    processes = []
    service = None
    expected_instance = None
    uninstalled = False
    try:
        if shortcuts:
            links = shortcut_paths(environment)
            require(not any(path.exists() for path in links.values()), 'EXISTING_SHORTCUT')
        setup = OwnedProcess(install_command(initial_installer, install, private / 'setup.log', shortcuts),
                             environment, private, install)
        processes.append(setup)
        require(setup.wait(240) == 0, 'INSTALLER_EXIT_FAILED')
        executable = install / 'RequirementsAgent.exe'
        require(executable.is_file(), 'INSTALLED_EXE_MISSING')
        owned_registration(install)
        manifest = verify_installed_files(install)
        initial_version = manifest['version']
        report.update(initial_version=initial_version, initial_source_commit=manifest['source_commit'],
                      initial_exe_sha256=sha256(executable))
        report.update(version=manifest['version'], source_commit=manifest['source_commit'],
                      installed_exe_sha256=sha256(executable), installed_file_count=len(manifest['files']))
        report['checks']['silent_install_and_file_hashes'] = True

        verify_packaged_smoke(executable, environment, private, install, processes, 'initial', not bool(upgrade_from))
        report['checks']['packaged_offline_smoke'] = True

        expected_instance = instance_identity(executable, home)
        if shortcuts:
            report['shortcut_checks'] = {'initial': {}}
            verify_shortcuts(links, install, report['shortcut_checks']['initial'])
            service = open_shortcut(links['desktop'], environment, private, install)
        else:
            service = OwnedProcess([str(executable), '--serve'], environment, private, install)
        processes.append(service)
        instance = wait_for_instance(service, expected_instance, launcher=shortcuts)
        require(instance.get('product') == 'requirements-agent' and instance.get('business_ready') is True,
                'DIRECT_INSTANCE_NOT_READY')
        desktop, projects = verify_business_http(expected_instance)
        require(not projects, 'FRESH_PROFILE_HAS_PROJECTS')
        if shortcuts:
            check_shortcut_reuse(links, 'desktop', expected_instance, environment, private, install, processes)
            report['checks']['initial_desktop_start_and_start_menu_reuse'] = True
        verify_shutdown_security(desktop['csrf'])
        report['checks']['direct_business_http_and_shutdown_security'] = True
        project_id, source_id = seed_business_data()
        before = business_snapshot(project_id, source_id)
        require(any(row.get('id') == project_id for row in request_json('/api/projects')[1]),
                'SYNTHETIC_PROJECT_NOT_LISTED')
        shutdown_owned(service, expected_instance)
        service = None
        report['checks']['origin_csrf_shutdown_and_port_release'] = True
        credential_files = {path.relative_to(home): sha256(path) for path in (home / 'data/.credentials').glob('*.dpapi')}
        require(credential_files, 'SYNTHETIC_CREDENTIAL_FILE_MISSING')

        # The legacy marker is inert; the synthetic project is real persisted business data.
        markers = [home / 'profile-preserve.sentinel', home / 'license/ci-preserve.sentinel', home / '.env']
        sentinel = b'INSTALLER_PRESERVATION_TEST_ONLY_NOT_A_LICENSE\n'
        for marker in markers:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_bytes(sentinel)
        owned_registration(install)
        upgrade = OwnedProcess(install_command(installer, install, private / 'upgrade.log', shortcuts),
                               environment, private, install)
        processes.append(upgrade)
        require(upgrade.wait(240) == 0, 'UPGRADE_INSTALLER_EXIT_FAILED')
        owned_registration(install)
        manifest = verify_installed_files(install)
        report.update(version=manifest['version'], target_version=manifest['version'],
                      source_commit=manifest['source_commit'], installed_exe_sha256=sha256(executable),
                      installed_file_count=len(manifest['files']),
                      upgrade_mode='cross_version_upgrade' if manifest['version'] != initial_version else 'same_version_reinstall')
        require(not upgrade_from or manifest['version'] != initial_version, 'UPGRADE_VERSION_UNCHANGED')
        if upgrade_from:
            verify_packaged_smoke(executable, environment, private, install, processes, 'target', True)
        report['checks']['packaged_native_com_runtime'] = True
        expected_instance = instance_identity(executable, home)
        if shortcuts:
            report['shortcut_checks']['target'] = {}
            verify_shortcuts(links, install, report['shortcut_checks']['target'])
            service = open_shortcut(links['start_menu'], environment, private, install)
        else:
            service = OwnedProcess([str(executable), '--serve'], environment, private, install)
        processes.append(service)
        wait_for_instance(service, expected_instance, launcher=shortcuts)
        _desktop, projects = verify_business_http(expected_instance)
        require(any(row.get('id') == project_id and row.get('name') == before['project']['name'] for row in projects),
                'UPGRADE_LOST_SYNTHETIC_PROJECT')
        after = business_snapshot(project_id, source_id)
        for name in before:
            require(after[name] == before[name], 'UPGRADE_CHANGED_' + name.upper())
        require(all((home / relative).is_file() and sha256(home / relative) == digest
                    for relative, digest in credential_files.items()), 'UPGRADE_CHANGED_CREDENTIAL_FILES')
        if shortcuts:
            check_shortcut_reuse(links, 'start_menu', expected_instance, environment, private, install, processes)
            report['checks']['target_start_menu_start_and_desktop_reuse'] = True
        require(all(marker.read_bytes() == sentinel for marker in markers), 'UPGRADE_DELETED_PROFILE_MARKER')
        report['checks']['in_place_upgrade_preserves_synthetic_project'] = True
        report['checks']['upgrade_preserves_project_history_attachment_config_and_protected_key'] = True
        report['preserved_data'] = dict(project_json='identical', history_entries=len(after['history']),
                                       history='identical', attachment_sha256=after['attachment_sha256'],
                                       model_config='identical', key_source=after['models']['model']['key_source'],
                                       credential_files=len(credential_files))
        shutdown_owned(service, expected_instance)
        service = None
        data_files = {p.relative_to(home): sha256(p) for p in (home / 'data').rglob('*') if p.is_file()}
        require(data_files, 'SYNTHETIC_PROJECT_DATA_MISSING')
        owned_registration(install)
        uninstall = OwnedProcess([str(install / 'unins000.exe'), '/VERYSILENT', '/SUPPRESSMSGBOXES',
                                  '/NORESTART', '/LOG=' + str(private / 'uninstall.log')],
                                 environment, private, install)
        processes.append(uninstall)
        require(uninstall.wait(120) == 0, 'UNINSTALLER_EXIT_FAILED')
        deadline = time.monotonic() + 10
        while executable.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        require(not executable.exists() and not registrations(), 'UNINSTALL_INCOMPLETE')
        uninstalled = True
        require(not any(path.exists() for path in links.values()), 'UNINSTALL_LEFT_SHORTCUTS')
        if shortcuts:
            report['checks']['uninstall_removes_created_shortcuts'] = True
        require(all(marker.read_bytes() == sentinel for marker in markers), 'UNINSTALL_DELETED_PROFILE_MARKER')
        require(all((home / relative).is_file() and sha256(home / relative) == digest
                    for relative, digest in data_files.items()), 'UNINSTALL_CHANGED_BUSINESS_DATA')
        report['checks']['uninstall_preserves_business_data_and_legacy_marker'] = True
        report['business_validation']['synthetic_offline'] = 'VERIFIED'
        report['status'] = 'DIRECT_INSTALLATION_VERIFIED'
    except Exception as error:
        report['status'] = 'INSTALLATION_VERIFICATION_FAILED'
        report['failure_code'] = str(error) if isinstance(error, VerificationFailure) else type(error).__name__
    finally:
        cleanup_errors = (['CREATED_PROCESS_INITIALIZATION_CLEANUP_FAILED']
                          if report.get('failure_code') == 'CREATED_PROCESS_INITIALIZATION_CLEANUP_FAILED' else [])
        if service is not None:
            try:
                shutdown_owned(service, expected_instance)
            except Exception:
                if shortcuts:
                    # A shortcut launcher may already have exited. Without a
                    # matching service identity there is no safe process to kill.
                    try:
                        wait_for_port_release(timeout=1)
                    except VerificationFailure:
                        cleanup_errors.append('SHORTCUT_SERVICE_CLEANUP_UNCONFIRMED')
        for owned in reversed(processes):
            if owned.process.poll() is None:
                try:
                    owned.terminate_owned()
                except Exception as error:
                    cleanup_errors.append(str(error) if isinstance(error, VerificationFailure) else type(error).__name__)
                    if getattr(error, 'cleanup_diagnostics', None):
                        report.setdefault('cleanup_diagnostics', []).extend(error.cleanup_diagnostics)
        if not uninstalled and (install / 'unins000.exe').is_file():
            try:
                owned_registration(install)
                cleanup = OwnedProcess([str(install / 'unins000.exe'), '/VERYSILENT', '/SUPPRESSMSGBOXES',
                                        '/NORESTART'], environment, private, install)
                require(cleanup.wait(120) == 0, 'CLEANUP_UNINSTALL_FAILED')
                require(not registrations(), 'CLEANUP_REGISTRATION_REMAINS')
                require(not any(path.exists() for path in links.values()), 'CLEANUP_SHORTCUTS_REMAIN')
            except Exception as error:
                cleanup_errors.append(str(error) if isinstance(error, VerificationFailure) else type(error).__name__)
        report['cleanup_status'] = 'OWNED_PROCESSES_STOPPED' if not cleanup_errors else 'CLEANUP_INCOMPLETE'
        if cleanup_errors:
            report['cleanup_errors'] = cleanup_errors
            report['status'] = 'INSTALLATION_VERIFICATION_FAILED'
        report['shutdown_attempts'] = [attempt for owned in processes
                                       for attempt in getattr(owned, 'shutdown_attempts', [])]
        if shortcuts:
            report['shortcut_launches'] = [shortcut_diagnostics(owned) for owned in processes
                                          if hasattr(owned, 'shortcut_trace')]
            report['shortcut_installer_logs'] = {}
            for name in ('setup', 'upgrade'):
                log = private / (name + '.log')
                if log.is_file():
                    # Only shortcut destination/creation lines, no command line
                    # or unrelated installer context, enter the public report.
                    report['shortcut_installer_logs'][name] = [line for line in log.read_text('utf-8-sig').splitlines()
                        if ('Dest filename: ' in line and line.endswith('.lnk')) or
                        line.endswith(('Creating the icon.', 'Successfully created the icon.'))]
        report['finished_at'] = datetime.now(timezone.utc).isoformat()
        (output / 'installation-verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', 'utf-8')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--installer', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--upgrade-from', type=Path, help='Install this older release before upgrading to --installer')
    parser.add_argument('--shortcuts', action='store_true', help='Verify real desktop/menu shortcuts on a clean CI or Windows user account')
    args = parser.parse_args()
    require(os.name == 'nt', 'WINDOWS_REQUIRED')
    try:
        report = verify(args.installer, args.output, upgrade_from=args.upgrade_from, shortcuts=args.shortcuts)
    except Exception as error:
        code = str(error) if isinstance(error, VerificationFailure) else type(error).__name__
        print(json.dumps({'status': 'INSTALLATION_VERIFICATION_NOT_STARTED', 'failure_code': code}))
        return 2
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report['status'] == 'DIRECT_INSTALLATION_VERIFIED' else 1


if __name__ == '__main__':
    raise SystemExit(main())
