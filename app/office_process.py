"""Windows process identities for one owned Office automation instance."""
import ctypes
from ctypes import wintypes
import json
from pathlib import Path

ALLOWED = {'winword', 'excel', 'powerpnt', 'wps', 'et', 'wpp'}


def kernel_api():
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    return kernel


def handle_identity(kernel, handle, pid):
    filename = ctypes.create_unicode_buffer(32768)
    length = wintypes.DWORD(len(filename))
    times = [wintypes.FILETIME() for _ in range(4)]
    if not kernel.QueryFullProcessImageNameW(handle, 0, filename, ctypes.byref(length)) or not kernel.GetProcessTimes(handle, *[ctypes.byref(t) for t in times]):
        raise ctypes.WinError(ctypes.get_last_error())
    return dict(pid=pid, name=Path(filename.value).stem.casefold(), image=filename.value,
                started=str((times[0].dwHighDateTime << 32) | times[0].dwLowDateTime))


def processes(name):
    class Entry(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('usage', wintypes.DWORD), ('pid', wintypes.DWORD),
                    ('heap', ctypes.c_size_t), ('module', wintypes.DWORD), ('threads', wintypes.DWORD),
                    ('parent', wintypes.DWORD), ('priority', wintypes.LONG), ('flags', wintypes.DWORD),
                    ('exe', wintypes.WCHAR * 260)]
    kernel = kernel_api()
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    found, entry = [], Entry()
    entry.size = ctypes.sizeof(entry)
    try:
        more = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while more:
            if entry.exe.casefold() == name.casefold() + '.exe':
                handle = kernel.OpenProcess(0x1000, False, entry.pid)
                if not handle:
                    if ctypes.get_last_error() not in (87, 1168):
                        raise ctypes.WinError(ctypes.get_last_error())
                else:
                    try:
                        found.append(dict(handle_identity(kernel, handle, entry.pid), parent_pid=entry.parent))
                    finally:
                        kernel.CloseHandle(handle)
            more = kernel.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)
    return found


def cleanup_owned(owner_path):
    """A held handle, image and creation stamp must match before termination."""
    value = json.loads(Path(owner_path).read_text('utf-8'))
    owners = value if isinstance(value, list) else [value]
    return all([cleanup_identity(owner) for owner in owners])


def cleanup_identity(owner):
    if owner['name'] not in ALLOWED:
        return False
    kernel = kernel_api()
    handle = kernel.OpenProcess(0x1000 | 0x0001 | 0x00100000, False, owner['pid'])
    if not handle:
        return ctypes.get_last_error() in (87, 1168)
    try:
        if kernel.WaitForSingleObject(handle, 1000) == 0:
            return True
        actual = handle_identity(kernel, handle, owner['pid'])
        if actual != {key: owner[key] for key in actual}:
            return False
        if not kernel.TerminateProcess(handle, 0):
            return kernel.WaitForSingleObject(handle, 0) == 0
        return kernel.WaitForSingleObject(handle, 3000) == 0
    finally:
        kernel.CloseHandle(handle)
