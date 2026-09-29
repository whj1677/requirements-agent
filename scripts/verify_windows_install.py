"""Verify a fresh Windows customer installation and in-place upgrade offline.

Only the selected installer and its verified process descendants are managed.
An existing product registration or occupied 8765 port blocks the entire run.
The output directory must be new. Upload installation-verification.json only;
the private working directory is deliberately excluded from CI artifacts.
"""
import argparse
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
            self.terminate_owned()
            raise VerificationFailure('OWNED_PROCESS_TIMEOUT') from error

    def terminate_owned(self):
        """Only held root handle and verified, younger descendants may be stopped."""
        if self.process.poll() is not None:
            return
        kernel = kernel_api()
        root_image, root_stamp = process_identity(self.handle)
        require(root_image == self.executable and root_stamp == self.created,
                'CLEANUP_ROOT_IDENTITY_CHANGED')
        unverified = False
        natural_exit = []
        for pid in descendant_order(self.process.pid, process_parents()):
            handle = kernel.OpenProcess(0x1000 | 0x0001 | 0x00100000, False, pid)
            if not handle:
                if ctypes.get_last_error() not in (87, 1168):
                    unverified = True
                continue  # A process that already exited needs no termination.
            try:
                try:
                    image, created = process_identity(handle)
                except VerificationFailure:
                    if kernel.WaitForSingleObject(handle, 0) != 0:
                        unverified = True
                    continue
                allowed = image == self.executable or any(image.is_relative_to(root) for root in self.roots)
                if created < self.created or not allowed:
                    # Windows may attach a system console host. Never kill it;
                    # allow it to exit naturally after its owned root closes.
                    natural_exit.append(handle)
                    handle = None
                    continue
                if not kernel.TerminateProcess(handle, 1) and kernel.WaitForSingleObject(handle, 0) != 0:
                    unverified = True
            finally:
                if handle:
                    kernel.CloseHandle(handle)
        self.process.terminate()  # Popen retains the exact created process handle.
        self.process.wait(timeout=10)
        for handle in natural_exit:
            try:
                if kernel.WaitForSingleObject(handle, 3000) != 0:
                    unverified = True
            finally:
                kernel.CloseHandle(handle)
        require(not unverified, 'CLEANUP_DESCENDANT_IDENTITY_UNVERIFIED')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def request(path, method='GET', csrf=None, payload=None, origin=ORIGIN):
    headers = {'Origin': origin} if origin is not None else {}
    if csrf is not None:
        headers['X-Desktop-CSRF'] = csrf
    if payload is not None:
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(ORIGIN + path, method=method,
                                 data=json.dumps(payload).encode('utf-8') if payload is not None else
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


def wait_for_instance(process, expected, timeout=45):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        require(process.process.poll() is None, 'DESKTOP_PROCESS_EXITED')
        try:
            status, value = request_json('/desktop/api/instance')
            require(status == 200 and value.get('instance_id') == expected,
                    'DESKTOP_INSTANCE_IDENTITY_MISMATCH')
            return value
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            time.sleep(0.2)
    raise VerificationFailure('DESKTOP_STARTUP_TIMEOUT')


def shutdown_owned(process, expected):
    status, value = request_json('/desktop/api/instance')
    require(status == 200 and value.get('instance_id') == expected, 'SHUTDOWN_INSTANCE_IDENTITY_MISMATCH')
    status, value = request_json('/desktop/api/status')
    require(status == 200 and isinstance(value.get('csrf'), str), 'SHUTDOWN_CSRF_UNAVAILABLE')
    status, value = request_json('/desktop/api/shutdown', 'POST', value['csrf'])
    require(status == 200 and value.get('status') == 'STOPPING', 'GRACEFUL_SHUTDOWN_REJECTED')
    require(process.wait(20) == 0, 'GRACEFUL_SHUTDOWN_EXIT_FAILED')
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            probe.bind(('127.0.0.1', PORT))
        except OSError as error:
            raise VerificationFailure('PORT_NOT_RELEASED') from error


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


def install_command(installer, install, log):
    return [str(installer), '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART',
            '/CURRENTUSER', '/NOICONS', '/TASKS=!desktopicon', '/DIR=' + str(install),
            '/LOG=' + str(log)]


def verify(installer, output):
    installer, output = prepare_paths(installer, output)
    report = {'format_version': 1, 'status': 'INSTALLATION_VERIFICATION_RUNNING',
              'installer_sha256': sha256(installer), 'checks': {}, 'model_calls': 0,
              'business_validation': {'synthetic_offline': 'RUNNING', 'real_model': 'NOT_RUN'}}
    private, install = output / 'private', output / 'installed-app'
    private.mkdir()
    environment, home = customer_environment(private)
    processes = []
    service = None
    expected_instance = None
    uninstalled = False
    try:
        setup = OwnedProcess(install_command(installer, install, private / 'setup.log'),
                             environment, private, install)
        processes.append(setup)
        require(setup.wait(240) == 0, 'INSTALLER_EXIT_FAILED')
        executable = install / 'RequirementsAgent.exe'
        require(executable.is_file(), 'INSTALLED_EXE_MISSING')
        owned_registration(install)
        manifest = verify_installed_files(install)
        report.update(version=manifest['version'], source_commit=manifest['source_commit'],
                      installed_exe_sha256=sha256(executable), installed_file_count=len(manifest['files']))
        report['checks']['silent_install_and_file_hashes'] = True

        result_file = private / 'offline-smoke.json'
        check = OwnedProcess([str(executable), '--smoke-test', '--output', str(result_file)],
                             environment, private, install)
        processes.append(check)
        require(check.wait(120) == 0, 'OFFLINE_SMOKE_EXIT_FAILED')
        value = json.loads(result_file.read_text('utf-8'))
        expected_checks = {'docx_subprocess', 'xlsx_subprocess', 'pptx_subprocess',
                           'image_subprocess', 'bundled_chromium', 'session', 'frontend', 'projects'}
        require(value.get('status') == 'OFFLINE_SMOKE_VERIFIED' and value.get('model_calls') == 0 and
                isinstance(value.get('checks'), dict) and
                all(value['checks'].get(name) is True for name in expected_checks),
                'OFFLINE_SMOKE_WRONG_RESULT')
        report['checks']['packaged_offline_smoke'] = True

        expected_instance = instance_identity(executable, home)
        service = OwnedProcess([str(executable), '--serve'], environment, private, install)
        processes.append(service)
        instance = wait_for_instance(service, expected_instance)
        require(instance.get('product') == 'requirements-agent' and instance.get('business_ready') is True,
                'DIRECT_INSTANCE_NOT_READY')
        desktop, projects = verify_business_http(expected_instance)
        require(not projects, 'FRESH_PROFILE_HAS_PROJECTS')
        verify_shutdown_security(desktop['csrf'])
        report['checks']['direct_business_http_and_shutdown_security'] = True
        project_name = '离线安装验收合成项目'
        status, project = request_json('/api/projects', 'POST', payload={'name': project_name})
        require(status == 200 and isinstance(project, dict) and isinstance(project.get('id'), str),
                'SYNTHETIC_PROJECT_CREATE_FAILED')
        project_id = project['id']
        require(any(row.get('id') == project_id for row in request_json('/api/projects')[1]),
                'SYNTHETIC_PROJECT_NOT_LISTED')
        shutdown_owned(service, expected_instance)
        report['checks']['origin_csrf_shutdown_and_port_release'] = True

        # The legacy marker is inert; the synthetic project is real persisted business data.
        markers = [home / 'profile-preserve.sentinel', home / 'license/ci-preserve.sentinel']
        sentinel = b'INSTALLER_PRESERVATION_TEST_ONLY_NOT_A_LICENSE\n'
        for marker in markers:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_bytes(sentinel)
        owned_registration(install)
        upgrade = OwnedProcess(install_command(installer, install, private / 'upgrade.log'),
                               environment, private, install)
        processes.append(upgrade)
        require(upgrade.wait(240) == 0, 'UPGRADE_INSTALLER_EXIT_FAILED')
        owned_registration(install)
        verify_installed_files(install)
        service = OwnedProcess([str(executable), '--serve'], environment, private, install)
        processes.append(service)
        wait_for_instance(service, expected_instance)
        _desktop, projects = verify_business_http(expected_instance)
        require(any(row.get('id') == project_id and row.get('name') == project_name for row in projects),
                'UPGRADE_LOST_SYNTHETIC_PROJECT')
        status, loaded = request_json('/api/projects/' + project_id)
        require(status == 200 and loaded.get('id') == project_id and loaded.get('name') == project_name,
                'UPGRADE_PROJECT_UNREADABLE')
        require(all(marker.read_bytes() == sentinel for marker in markers), 'UPGRADE_DELETED_PROFILE_MARKER')
        report['checks']['in_place_upgrade_preserves_synthetic_project'] = True
        shutdown_owned(service, expected_instance)
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
        if service is not None and service.process.poll() is None:
            try:
                shutdown_owned(service, expected_instance)
            except Exception:
                pass
        for owned in reversed(processes):
            if owned.process.poll() is None:
                try:
                    owned.terminate_owned()
                except Exception as error:
                    cleanup_errors.append(str(error) if isinstance(error, VerificationFailure) else type(error).__name__)
        if not uninstalled and (install / 'unins000.exe').is_file():
            try:
                owned_registration(install)
                cleanup = OwnedProcess([str(install / 'unins000.exe'), '/VERYSILENT', '/SUPPRESSMSGBOXES',
                                        '/NORESTART'], environment, private, install)
                require(cleanup.wait(120) == 0, 'CLEANUP_UNINSTALL_FAILED')
                require(not registrations(), 'CLEANUP_REGISTRATION_REMAINS')
            except Exception as error:
                cleanup_errors.append(str(error) if isinstance(error, VerificationFailure) else type(error).__name__)
        report['cleanup_status'] = 'OWNED_PROCESSES_STOPPED' if not cleanup_errors else 'CLEANUP_INCOMPLETE'
        if cleanup_errors:
            report['cleanup_errors'] = cleanup_errors
            report['status'] = 'INSTALLATION_VERIFICATION_FAILED'
        report['finished_at'] = datetime.now(timezone.utc).isoformat()
        (output / 'installation-verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', 'utf-8')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--installer', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    require(os.name == 'nt', 'WINDOWS_REQUIRED')
    try:
        report = verify(args.installer, args.output)
    except Exception as error:
        code = str(error) if isinstance(error, VerificationFailure) else type(error).__name__
        print(json.dumps({'status': 'INSTALLATION_VERIFICATION_NOT_STARTED', 'failure_code': code}))
        return 2
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report['status'] == 'DIRECT_INSTALLATION_VERIFIED' else 1


if __name__ == '__main__':
    raise SystemExit(main())
