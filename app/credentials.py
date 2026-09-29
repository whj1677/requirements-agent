"""Windows user-bound credentials. No plaintext fallback or project export."""
import ctypes
import hashlib
import os
from pathlib import Path
import tempfile

from .core import Problem, require


def dpapi(data, endpoint, decrypt=False):
    require(os.name == 'nt', 'KEY_STORAGE_UNSUPPORTED', '此系统不支持本机加密保存，请选择临时使用')
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]

    def blob(value):
        buffer = ctypes.create_string_buffer(value)
        return Blob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))), buffer

    incoming, input_buffer = blob(data)
    entropy, entropy_buffer = blob(('RequirementsAgent:credential:v1:' + endpoint).encode('utf-8'))
    outgoing = Blob()
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    operation = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    operation.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob), ctypes.c_void_p,
                          ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    operation.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    # CRYPTPROTECT_UI_FORBIDDEN; intentionally omit LOCAL_MACHINE binding.
    if not operation(ctypes.byref(incoming), None, ctypes.byref(entropy), None, None, 1, ctypes.byref(outgoing)):
        raise Problem('KEY_STORAGE_FAILED', '本机加密凭据无法读取或保存，请重新输入 Key；原配置未替换', 400)
    try:
        return ctypes.string_at(outgoing.data, outgoing.size)
    finally:
        kernel.LocalFree(outgoing.data)


class ProtectedKeyStore:
    def __init__(self, folder):
        self.folder = Path(folder)

    @property
    def supported(self):
        return os.name == 'nt'

    def path(self, endpoint):
        return self.folder / (hashlib.sha256(endpoint.encode('utf-8')).hexdigest() + '.dpapi')

    def load(self, endpoint):
        path = self.path(endpoint)
        if not path.exists():
            return None
        try:
            data = path.read_bytes()
            require(data.startswith(b'RAKEY1\n') and len(data) <= 16384,
                    'KEY_STORAGE_FAILED', '本机加密凭据格式无效，请重新输入 Key')
            return dpapi(data[7:], endpoint, decrypt=True).decode('utf-8')
        except Problem:
            raise
        except (OSError, UnicodeError):
            raise Problem('KEY_STORAGE_FAILED', '本机加密凭据无法读取，请重新输入 Key') from None

    def save(self, endpoint, key):
        require(self.supported, 'KEY_STORAGE_UNSUPPORTED', '此系统不支持本机加密保存，请选择临时使用')
        if not key:
            try:
                self.path(endpoint).unlink(missing_ok=True)
            except OSError:
                raise Problem('KEY_STORAGE_FAILED', '无法移除本机保存的 Key，请重试') from None
            return
        encrypted = b'RAKEY1\n' + dpapi(key.encode('utf-8'), endpoint)
        temporary = None
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=self.folder, prefix='pending-', delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(encrypted)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path(endpoint))
        except OSError:
            raise Problem('KEY_STORAGE_FAILED', '无法保存本机加密 Key，原配置未替换') from None
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)
