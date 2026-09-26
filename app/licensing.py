"""Offline product licensing. Verification and hardware binding stay in the DLL.

This module does not import the application or initialize business data. Customer
entry points use the default trust root; constructor overrides are for tests only.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone

PRODUCT = "requirements-agent"
PROFILE = "windows-baseboard-v1"
COMPONENT_VERSION = "1.0.0-rsa"
MAX_LICENSE_BYTES = 32768
MAX_RESPONSE_BYTES = 65536
BRIDGE_TIMEOUT_SECONDS = 15
CACHE_SECONDS = 30
COMPONENT_SHA256 = {
    "DeviceLicense.dll": "28d98ade39cf079b065861ae56e8150d1f2054a5cccdcda75211e5e6e06643bd",
    "DeviceLicense.Bridge.exe": "606ad1ce435bf0e67dbb17849435a1d22f6ea95ac869dc51421f62078651c6f1",
}

_MESSAGES = {
    "LICENSE_MISSING": "尚未安装授权文件，请先申请并导入本设备的授权。",
    "LICENSE_SUBJECT_INVALID": "请填写 1 至 120 个字符的授权使用者名称，不能包含控制字符。",
    "LICENSE_ALREADY_INSTALLED": "已有不同的授权；如需重新授权，请明确选择替换。",
    "LICENSE_HARDWARE_MISMATCH": "授权与当前主板不匹配，请重新申请本设备的授权。",
    "HARDWARE_ID_UNAVAILABLE": "无法读取有效的主板身份，不能申请或使用授权。",
    "LICENSE_ISSUER_MISMATCH": "该授权不是本软件固定发行方签发的授权。",
    "LICENSE_SIGNATURE_INVALID": "授权签名无效，请使用发行方提供的原始授权文件。",
    "LICENSE_PRODUCT_MISMATCH": "此授权不适用于需求 Agent。",
    "LICENSE_DOCUMENT_INVALID": "授权文件格式无效或超过大小限制。",
    "LICENSE_STORAGE_INVALID": "授权文件位置不是有效的普通文件。",
    "LICENSE_STORAGE_UNAVAILABLE": "无法安全读取或保存授权文件，请检查本机文件权限。",
    "LICENSE_STORAGE_PATH_INVALID": "授权保存位置必须是绝对路径。",
    "LICENSE_REPLACEMENT_FLAG_INVALID": "重新授权的替换选项必须是布尔值。",
    "LICENSE_PLATFORM_UNSUPPORTED": "设备授权仅支持 Windows。",
    "LICENSE_BRIDGE_UNAVAILABLE": "授权组件无法运行，请修复安装后重试。",
    "LICENSE_BRIDGE_TIMEOUT": "授权组件读取设备超时，请重试。",
    "LICENSE_BRIDGE_INVALID": "授权组件返回无效结果，请修复安装后重试。",
    "LICENSE_COMPONENT_VERSION_MISMATCH": "授权组件版本不匹配，请修复安装后重试。",
    "LICENSE_COMPONENT_INTEGRITY_INVALID": "授权组件已被更改，请修复安装后重试。",
    "LICENSE_TRUST_ROOT_INVALID": "软件内置的授权信任配置无效，请修复安装。",
}


class LicenseError(Exception):
    def __init__(self, code: str, message: str | None = None):
        self.code = code
        self.message = message or _MESSAGES.get(code, "授权验证未通过，请联系软件发行方。")
        super().__init__(self.message)


def _strict_json(text: str):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def invalid_constant(_value):
        raise ValueError("invalid JSON constant")

    return json.loads(text, object_pairs_hook=pairs, parse_constant=invalid_constant)


def _serialized(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _bounded_process(executable: Path, wire: bytes) -> tuple[int, bytes]:
    """Bound both output streams and stdin lifetime, without exposing stderr."""
    try:
        process = subprocess.Popen(
            [str(executable)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError as exc:
        raise LicenseError("LICENSE_BRIDGE_UNAVAILABLE") from exc
    output = bytearray()
    overflow = threading.Event()

    def read(stream, capture):
        received = 0
        try:
            while True:
                chunk = stream.read(4096)
                if not chunk:
                    break
                received += len(chunk)
                if received > MAX_RESPONSE_BYTES:
                    overflow.set()
                    try:
                        process.kill()
                    except OSError:
                        pass
                    break
                if capture:
                    output.extend(chunk)
        finally:
            stream.close()

    def write():
        try:
            process.stdin.write(wire)
            process.stdin.flush()
        except OSError:
            pass
        finally:
            try:
                process.stdin.close()
            except OSError:
                pass

    workers = [threading.Thread(target=read, args=(process.stdout, True), daemon=True),
               threading.Thread(target=read, args=(process.stderr, False), daemon=True),
               threading.Thread(target=write, daemon=True)]
    for worker in workers:
        worker.start()
    timed_out = False
    try:
        process.wait(timeout=BRIDGE_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        timed_out = True
        process.kill()
        process.wait(timeout=5)
    finally:
        for worker in workers:
            worker.join(timeout=1)
    if timed_out:
        raise LicenseError("LICENSE_BRIDGE_TIMEOUT")
    if overflow.is_set() or any(worker.is_alive() for worker in workers):
        raise LicenseError("LICENSE_BRIDGE_INVALID")
    return process.returncode, bytes(output)


class LicenseManager:
    def __init__(self, resource_dir=None, license_path=None, public_key_xml=None):
        self.resource_dir = Path(resource_dir) if resource_dir is not None else Path(__file__).resolve().parent / "license_runtime"
        if license_path is None:
            local = os.environ.get("LOCALAPPDATA")
            if not local:
                raise LicenseError("LICENSE_STORAGE_PATH_INVALID")
            license_path = Path(local) / "RequirementsAgent" / "license" / "license.json"
        self.license_path = Path(license_path)
        if not self.license_path.is_absolute():
            raise LicenseError("LICENSE_STORAGE_PATH_INVALID")
        if public_key_xml is None:
            try:
                from .license_trust import PUBLIC_KEY_XML
                public_key_xml = PUBLIC_KEY_XML
            except (ImportError, AttributeError) as exc:
                raise LicenseError("LICENSE_TRUST_ROOT_INVALID") from exc
        if not isinstance(public_key_xml, str) or not public_key_xml or len(public_key_xml) > 4096:
            raise LicenseError("LICENSE_TRUST_ROOT_INVALID")
        self.public_key_xml = public_key_xml
        self._lock = threading.RLock()
        self._cache = None

    def _call_bridge(self, request: dict) -> dict:
        if os.name != "nt":
            raise LicenseError("LICENSE_PLATFORM_UNSUPPORTED")
        try:
            wire = _serialized(request).encode("utf-8")
        except (ValueError, TypeError, UnicodeError) as exc:
            raise LicenseError("LICENSE_DOCUMENT_INVALID") from exc
        if len(wire) > MAX_LICENSE_BYTES:
            raise LicenseError("LICENSE_DOCUMENT_INVALID")
        self._check_components()
        result_code, stdout = _bounded_process(self.resource_dir / "DeviceLicense.Bridge.exe", wire)
        if result_code != 0:
            raise LicenseError("LICENSE_BRIDGE_UNAVAILABLE")
        try:
            response = _strict_json(stdout.decode("utf-8-sig"))
        except (UnicodeError, ValueError) as exc:
            raise LicenseError("LICENSE_BRIDGE_INVALID") from exc
        if not isinstance(response, dict):
            raise LicenseError("LICENSE_BRIDGE_INVALID")
        if response.get("componentVersion") != COMPONENT_VERSION:
            raise LicenseError("LICENSE_COMPONENT_VERSION_MISMATCH")
        if response.get("ok") is not True:
            code = response.get("error")
            if not isinstance(code, str) or not re.fullmatch(r"(?:LICENSE|HARDWARE)_[A-Z0-9_]+", code):
                code = "LICENSE_COMPONENT_FAILED"
            raise LicenseError(code)
        result = response.get("result")
        if not isinstance(result, dict):
            raise LicenseError("LICENSE_BRIDGE_INVALID")
        return result

    def _check_components(self):
        # These hashes are part of the application code, not supplied by an
        # editable manifest or an imported license. Updating DLLs is a release.
        for filename, expected in COMPONENT_SHA256.items():
            component = self.resource_dir / filename
            try:
                metadata = component.lstat()
                if not stat.S_ISREG(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & 0x400:
                    raise LicenseError("LICENSE_COMPONENT_INTEGRITY_INVALID")
                with component.open("rb") as source:
                    content = source.read(1024 * 1024 + 1)
            except OSError as exc:
                raise LicenseError("LICENSE_BRIDGE_UNAVAILABLE") from exc
            if len(content) > 1024 * 1024 or hashlib.sha256(content).hexdigest() != expected:
                raise LicenseError("LICENSE_COMPONENT_INTEGRITY_INVALID")

    def create_request(self, display_name: str) -> dict:
        if not isinstance(display_name, str):
            raise LicenseError("LICENSE_SUBJECT_INVALID")
        name = display_name.strip()
        try:
            units = len(name.encode("utf-16-le")) // 2
        except UnicodeError as exc:
            raise LicenseError("LICENSE_SUBJECT_INVALID") from exc
        if not 1 <= units <= 120 or re.search(r"[\x00-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]", name):
            raise LicenseError("LICENSE_SUBJECT_INVALID")
        hardware = self._call_bridge({"command": "collect", "product": PRODUCT})
        fingerprint = hardware.get("hardwareFingerprint")
        if set(hardware) != {"profileId", "hardwareFingerprint"} or hardware.get("profileId") != PROFILE or not isinstance(fingerprint, str) or not re.fullmatch(r"[a-f0-9]{64}", fingerprint) or fingerprint == "0" * 64:
            raise LicenseError("LICENSE_BRIDGE_INVALID")
        return {
            "schema": "offline-device-license/request-v1", "product": PRODUCT,
            "requestId": "request-" + str(uuid.uuid4()), "nonce": secrets.token_hex(32),
            "createdAt": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "subject": {"displayName": name}, **hardware,
        }

    def verify_document(self, rawstr: str) -> dict:
        if not isinstance(rawstr, str):
            raise LicenseError("LICENSE_DOCUMENT_INVALID")
        rawstr = rawstr.removeprefix("\ufeff")
        try:
            if len(rawstr.encode("utf-8")) > MAX_LICENSE_BYTES:
                raise LicenseError("LICENSE_DOCUMENT_INVALID")
        except UnicodeError as exc:
            raise LicenseError("LICENSE_DOCUMENT_INVALID") from exc
        # Pass the original document to the DLL: reserializing first would erase
        # duplicate JSON fields and undermine the DLL's strict parser.
        payload = self._call_bridge({"command": "verify", "expectedProduct": PRODUCT,
                                     "publicKeyXml": self.public_key_xml, "licenseDocument": rawstr})
        if payload.get("product") != PRODUCT or payload.get("profileId") != PROFILE or payload.get("scope") != "permanent" or payload.get("schema") != "offline-device-license/payload-v1":
            raise LicenseError("LICENSE_BRIDGE_INVALID")
        return payload

    @staticmethod
    def _read_document(path: Path) -> tuple[bytes, tuple]:
        try:
            before = path.lstat()
            if not stat.S_ISREG(before.st_mode) or stat.S_ISLNK(before.st_mode) or getattr(before, "st_file_attributes", 0) & 0x400 or before.st_size > MAX_LICENSE_BYTES:
                raise LicenseError("LICENSE_STORAGE_INVALID")
            with path.open("rb") as source:
                opened = os.fstat(source.fileno())
                if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
                    raise LicenseError("LICENSE_STORAGE_UNAVAILABLE")
                data = source.read(MAX_LICENSE_BYTES + 1)
                after = os.fstat(source.fileno())
            if len(data) > MAX_LICENSE_BYTES:
                raise LicenseError("LICENSE_STORAGE_INVALID")
            identity = (after.st_dev, after.st_ino, after.st_mtime_ns, after.st_size)
            if identity != (opened.st_dev, opened.st_ino, opened.st_mtime_ns, opened.st_size):
                raise LicenseError("LICENSE_STORAGE_UNAVAILABLE")
            return data, identity
        except FileNotFoundError as exc:
            raise LicenseError("LICENSE_MISSING") from exc
        except OSError as exc:
            raise LicenseError("LICENSE_STORAGE_UNAVAILABLE") from exc

    @staticmethod
    def _text(data: bytes) -> str:
        try:
            return data.decode("utf-8-sig")
        except UnicodeError as exc:
            raise LicenseError("LICENSE_DOCUMENT_INVALID") from exc

    def _remember(self, data, identity, payload):
        self._cache = (data, identity, copy.deepcopy(payload), time.monotonic() + CACHE_SECONDS)

    def load_license(self) -> dict:
        """Read and verify afresh, including DLL hardware collection."""
        with self._lock:
            self._cache = None
            data, identity = self._read_document(self.license_path)
            text = self._text(data)
            payload = self.verify_document(text)
            try:
                license_document = _strict_json(text)
            except ValueError as exc:
                raise LicenseError("LICENSE_DOCUMENT_INVALID") from exc
            self._remember(data, identity, payload)
            return {"license": license_document, "payload": copy.deepcopy(payload)}

    def verify_cached(self) -> dict:
        """Every use reads the file; only unchanged bytes can reuse a short cache."""
        with self._lock:
            try:
                data, identity = self._read_document(self.license_path)
                if self._cache:
                    cached_data, cached_identity, payload, deadline = self._cache
                    if data == cached_data and identity == cached_identity and time.monotonic() < deadline:
                        return copy.deepcopy(payload)
                self._cache = None
                payload = self.verify_document(self._text(data))
                self._remember(data, identity, payload)
                return copy.deepcopy(payload)
            except LicenseError:
                self._cache = None
                raise

    def install_license(self, source_path, replace: bool = False) -> dict:
        if type(replace) is not bool:
            raise LicenseError("LICENSE_REPLACEMENT_FLAG_INVALID")
        with self._lock:
            source_data, _ = self._read_document(Path(source_path))
            source_text = self._text(source_data)
            payload = self.verify_document(source_text)
            try:
                document = _strict_json(source_text)
                serialized = (_serialized(document) + "\n").encode("utf-8")
            except (ValueError, UnicodeError) as exc:
                raise LicenseError("LICENSE_DOCUMENT_INVALID") from exc

            def existing():
                current = self.load_license()
                if _serialized(current["license"]) != _serialized(document):
                    raise LicenseError("LICENSE_ALREADY_INSTALLED")
                return {"installed": False, "alreadyInstalled": True, "payload": current["payload"]}

            try:
                return existing()
            except LicenseError as exc:
                if exc.code != "LICENSE_MISSING" and not replace:
                    raise
                if exc.code != "LICENSE_MISSING":
                    # Explicit replacement may repair invalid bytes, but must not
                    # follow a symlink or replace a directory/device.
                    try:
                        current = self.license_path.lstat()
                    except OSError as storage_error:
                        raise LicenseError("LICENSE_STORAGE_UNAVAILABLE") from storage_error
                    if not stat.S_ISREG(current.st_mode) or getattr(current, "st_file_attributes", 0) & 0x400:
                        raise LicenseError("LICENSE_STORAGE_INVALID")
            destination = self.license_path
            temporary = destination.with_name(destination.name + "." + uuid.uuid4().hex + ".tmp")
            try:
                destination.parent.mkdir(parents=True, exist_ok=True)
                fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                with os.fdopen(fd, "wb") as output:
                    output.write(serialized)
                    output.flush()
                    os.fsync(output.fileno())
                try:
                    if replace:
                        os.replace(temporary, destination)
                    else:
                        # Atomic no-overwrite publication, including concurrent
                        # installers. Unsupported filesystems fail closed.
                        os.link(temporary, destination)
                except FileExistsError:
                    return existing()
                self._cache = None
                return {"installed": True, "alreadyInstalled": False, "replaced": replace,
                        "payload": copy.deepcopy(payload)}
            except OSError as exc:
                raise LicenseError("LICENSE_STORAGE_UNAVAILABLE") from exc
            finally:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
