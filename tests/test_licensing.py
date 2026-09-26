"""Authorization tests use isolated paths and ephemeral issuer keys only."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

from app import licensing
from app.licensing import LicenseError, LicenseManager, PRODUCT, PROFILE


def fake_payload(name="测试使用者"):
    return {"schema": "offline-device-license/payload-v1", "product": PRODUCT,
            "scope": "permanent", "profileId": PROFILE, "subject": {"displayName": name}}


@pytest.fixture
def manager(tmp_path):
    return LicenseManager(license_path=tmp_path / "installed" / "license.json", public_key_xml="<test-public-key/>")


def assert_code(code, operation):
    with pytest.raises(LicenseError) as error:
        operation()
    assert error.value.code == code
    assert error.value.message


def test_request_is_product_fixed_and_uses_only_dll_hardware(manager, monkeypatch):
    calls = []
    def bridge(request):
        calls.append(request)
        return {"profileId": PROFILE, "hardwareFingerprint": "a" * 64}
    monkeypatch.setattr(manager, "_call_bridge", bridge)
    monkeypatch.setenv("RA_LICENSE_PRODUCT", "other-agent")
    request = manager.create_request("  测试客户  ")
    assert calls == [{"command": "collect", "product": PRODUCT}]
    assert request["product"] == PRODUCT
    assert request["subject"] == {"displayName": "测试客户"}
    assert len(request["nonce"]) == 64
    assert request["requestId"].startswith("request-")
    assert request["createdAt"].endswith("Z")
    assert not manager.license_path.parent.exists()


@pytest.mark.parametrize("name", ["", "\n", "x" * 121, "😀" * 61, "用户\u202ename", None])
def test_invalid_display_name_does_not_collect_hardware(manager, monkeypatch, name):
    monkeypatch.setattr(manager, "_call_bridge", lambda _: pytest.fail("must validate name first"))
    assert_code("LICENSE_SUBJECT_INVALID", lambda: manager.create_request(name))


def test_verify_preserves_raw_document_for_dll_duplicate_detection(manager, monkeypatch):
    raw = '{"schema":"first","schema":"second"}'
    def bridge(request):
        assert request["licenseDocument"] == raw
        assert request["expectedProduct"] == PRODUCT
        assert request["publicKeyXml"] == "<test-public-key/>"
        raise LicenseError("LICENSE_DOCUMENT_INVALID")
    monkeypatch.setattr(manager, "_call_bridge", bridge)
    assert_code("LICENSE_DOCUMENT_INVALID", lambda: manager.verify_document(raw))


def test_missing_license_ignores_environment_bypass(manager, monkeypatch):
    monkeypatch.setenv("RA_LICENSE_BYPASS", "1")
    monkeypatch.setenv("RA_ACCESS_TOKEN", "off")
    assert_code("LICENSE_MISSING", manager.verify_cached)
    assert not manager.license_path.parent.exists()


def test_cache_rechecks_bytes_even_with_restored_mtime_and_fails_on_deletion(manager, monkeypatch):
    manager.license_path.parent.mkdir()
    manager.license_path.write_bytes(b"good")
    original_stat = manager.license_path.stat()
    calls = []
    def verify(text):
        calls.append(text)
        if text != "good":
            raise LicenseError("LICENSE_SIGNATURE_INVALID")
        return fake_payload()
    monkeypatch.setattr(manager, "verify_document", verify)
    payload = manager.verify_cached()
    payload["subject"]["displayName"] = "caller mutation"
    assert manager.verify_cached()["subject"]["displayName"] == "测试使用者"
    assert calls == ["good"]
    manager.license_path.write_bytes(b"evil")
    os.utime(manager.license_path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    assert_code("LICENSE_SIGNATURE_INVALID", manager.verify_cached)
    manager.license_path.unlink()
    assert_code("LICENSE_MISSING", manager.verify_cached)
    assert manager._cache is None


def test_cache_expiry_recollects_hardware_and_load_always_reverifies(manager, monkeypatch):
    manager.license_path.parent.mkdir()
    manager.license_path.write_text("{}", encoding="utf-8")
    clock = [100.0]
    calls = []
    monkeypatch.setattr(licensing.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(manager, "verify_document", lambda raw: calls.append(raw) or fake_payload())
    manager.verify_cached()
    clock[0] += 29
    manager.verify_cached()
    assert len(calls) == 1
    clock[0] += 1
    manager.verify_cached()
    assert len(calls) == 2
    manager.load_license()
    assert len(calls) == 3


def test_install_requires_explicit_replacement_and_preserves_old_bytes(manager, monkeypatch, tmp_path):
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    invalid = tmp_path / "invalid.json"
    first.write_text('{"id":"one"}', encoding="utf-8")
    second.write_text('{"id":"two"}', encoding="utf-8")
    invalid.write_text('invalid signature', encoding="utf-8")
    def verify(raw):
        if raw == 'invalid signature':
            raise LicenseError("LICENSE_SIGNATURE_INVALID")
        return fake_payload()
    monkeypatch.setattr(manager, "verify_document", verify)
    assert manager.install_license(first)["installed"]
    old = manager.license_path.read_bytes()
    assert manager.install_license(first)["alreadyInstalled"]
    assert_code("LICENSE_ALREADY_INSTALLED", lambda: manager.install_license(second))
    assert_code("LICENSE_SIGNATURE_INVALID", lambda: manager.install_license(invalid, replace=True))
    assert manager.license_path.read_bytes() == old
    assert manager.install_license(second, replace=True)["replaced"]
    assert json.loads(manager.license_path.read_text())["id"] == "two"
    assert not list(manager.license_path.parent.glob("*.tmp"))


def test_corrupt_existing_license_can_only_be_repaired_explicitly(manager, monkeypatch, tmp_path):
    source = tmp_path / "valid.json"
    source.write_text("{}", encoding="utf-8")
    manager.license_path.parent.mkdir()
    manager.license_path.write_bytes(b"bad")
    def verify(raw):
        if raw == "bad":
            raise LicenseError("LICENSE_DOCUMENT_INVALID")
        return fake_payload()
    monkeypatch.setattr(manager, "verify_document", verify)
    assert_code("LICENSE_DOCUMENT_INVALID", lambda: manager.install_license(source))
    assert manager.license_path.read_bytes() == b"bad"
    assert manager.install_license(source, replace=True)["installed"]


def test_explicit_replacement_repairs_oversize_regular_file(manager, monkeypatch, tmp_path):
    source = tmp_path / "valid.json"
    source.write_text("{}", encoding="utf-8")
    manager.license_path.parent.mkdir()
    manager.license_path.write_bytes(b"x" * (licensing.MAX_LICENSE_BYTES + 1))
    monkeypatch.setattr(manager, "verify_document", lambda _: fake_payload())
    assert_code("LICENSE_STORAGE_INVALID", lambda: manager.install_license(source))
    assert manager.license_path.stat().st_size > licensing.MAX_LICENSE_BYTES
    assert manager.install_license(source, replace=True)["replaced"]
    assert manager.license_path.read_bytes() == b"{}\n"


def test_concurrent_installers_publish_once_without_overwriting(manager, monkeypatch, tmp_path):
    source = tmp_path / "valid.json"
    source.write_text("{}", encoding="utf-8")
    managers = [LicenseManager(license_path=manager.license_path, public_key_xml="<test/>") for _ in range(4)]
    for instance in managers:
        monkeypatch.setattr(instance, "verify_document", lambda _: fake_payload())
    with ThreadPoolExecutor(max_workers=4) as pool:
        outcomes = list(pool.map(lambda instance: instance.install_license(source), managers))
    assert sum(result["installed"] for result in outcomes) == 1
    assert sum(result["alreadyInstalled"] for result in outcomes) == 3
    assert not list(manager.license_path.parent.glob("*.tmp"))


@pytest.mark.parametrize("contents", [b"x" * (licensing.MAX_LICENSE_BYTES + 1), b"\xff"], ids=["oversize", "invalid-utf8"])
def test_oversize_and_invalid_utf8_never_reach_verifier(manager, monkeypatch, contents):
    manager.license_path.parent.mkdir()
    manager.license_path.write_bytes(contents)
    monkeypatch.setattr(manager, "verify_document", lambda _: pytest.fail("unsafe document reached verifier"))
    with pytest.raises(LicenseError):
        manager.verify_cached()


def test_directory_is_not_a_license_or_replacement_target(manager, monkeypatch, tmp_path):
    manager.license_path.mkdir(parents=True)
    source = tmp_path / "valid.json"
    source.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(manager, "verify_document", lambda _: fake_payload())
    assert_code("LICENSE_STORAGE_INVALID", manager.verify_cached)
    assert_code("LICENSE_STORAGE_INVALID", lambda: manager.install_license(source, replace=True))
    assert manager.license_path.is_dir()


@pytest.mark.skipif(os.name != "nt", reason="Windows bridge contract")
@pytest.mark.parametrize("response,code", [
    ({"ok": False, "componentVersion": licensing.COMPONENT_VERSION, "error": "LICENSE_SIGNATURE_INVALID"}, "LICENSE_SIGNATURE_INVALID"),
    ({"ok": True, "componentVersion": "other", "result": {}}, "LICENSE_COMPONENT_VERSION_MISMATCH"),
    ({"ok": True, "componentVersion": licensing.COMPONENT_VERSION, "result": []}, "LICENSE_BRIDGE_INVALID"),
    ({"ok": False, "componentVersion": licensing.COMPONENT_VERSION, "error": "raw secret data"}, "LICENSE_COMPONENT_FAILED"),
])
def test_bridge_rejects_failed_or_malformed_success(manager, monkeypatch, response, code):
    monkeypatch.setattr(licensing, "_bounded_process", lambda *_: (0, json.dumps(response).encode()))
    assert_code(code, lambda: manager._call_bridge({"command": "collect", "product": PRODUCT}))


@pytest.mark.parametrize("program,code", [
    ("import sys;sys.stdout.write('x'*70000)", "LICENSE_BRIDGE_INVALID"),
    ("import sys;sys.stderr.write('x'*70000)", "LICENSE_BRIDGE_INVALID"),
    ("import time;time.sleep(5)", "LICENSE_BRIDGE_TIMEOUT"),
])
def test_child_output_and_timeout_are_bounded(monkeypatch, program, code):
    popen = subprocess.Popen
    monkeypatch.setattr(licensing.subprocess, "Popen", lambda _args, **kwargs: popen([sys.executable, "-c", program], **kwargs))
    monkeypatch.setattr(licensing, "BRIDGE_TIMEOUT_SECONDS", 0.3)
    assert_code(code, lambda: licensing._bounded_process(Path("unused.exe"), b"{}"))


@pytest.fixture(scope="module")
def actual_signed_documents(tmp_path_factory):
    """Real DLL + real RSA signatures; private key exists only in a child process."""
    runtime = Path(licensing.__file__).resolve().parent / "license_runtime"
    node = shutil.which("node")
    if os.name != "nt" or not node or not (runtime / "DeviceLicense.Bridge.exe").is_file():
        pytest.skip("Real bridge integration needs Windows, bundled DLL and developer Node")
    temporary = tmp_path_factory.mktemp("actual-license")
    collector = LicenseManager(resource_dir=runtime, license_path=temporary / "license.json", public_key_xml="<collect-only/>")
    request = collector.create_request("独立授权集成测试")
    script = r'''
const fs = require('node:fs');
const crypto = require('node:crypto');
const request = JSON.parse(fs.readFileSync(0, 'utf8'));
const {privateKey, publicKey} = crypto.generateKeyPairSync('rsa', {modulusLength:3072, publicExponent:65537});
const key = publicKey.export({format:'jwk'});
const keyId = crypto.createHash('sha256').update(`offline-device-license:rsa-public-key-v1\n${key.n}\n${key.e}`).digest('hex');
const publicKeyXml = `<RSAKeyValue><Modulus>${Buffer.from(key.n,'base64url').toString('base64')}</Modulus><Exponent>${Buffer.from(key.e,'base64url').toString('base64')}</Exponent></RSAKeyValue>`;
const payload = {schema:'offline-device-license/payload-v1',product:request.product,licenseId:'license-'+crypto.randomUUID(),requestId:request.requestId,requestSha256:'a'.repeat(64),issuedAt:new Date().toISOString(),scope:'permanent',subject:request.subject,profileId:request.profileId,hardwareFingerprint:request.hardwareFingerprint};
function envelope(raw) {
  return {schema:'offline-device-license/rsa-license-v1',algorithm:'RSASSA-PKCS1-v1_5-SHA256',publicKeyId:keyId,payload:Buffer.from(raw).toString('base64url'),signature:crypto.sign('RSA-SHA256', Buffer.from('offline-device-license:rsa-license-v1\n'+raw), {key:privateKey,padding:crypto.constants.RSA_PKCS1_PADDING}).toString('base64url')};
}
const valid=envelope(JSON.stringify(payload));
const wrongBoard=envelope(JSON.stringify({...payload,hardwareFingerprint:'b'.repeat(64)}));
const wrongProduct=envelope(JSON.stringify({...payload,product:'other-agent'}));
const duplicate=envelope(JSON.stringify(payload).replace('{','{"product":"requirements-agent",'));
process.stdout.write(JSON.stringify({publicKeyXml,valid,wrongBoard,wrongProduct,duplicate}));
'''
    process = subprocess.run([node, "-e", script], input=json.dumps(request).encode(), capture_output=True,
                             timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert process.returncode == 0, "ephemeral signing helper failed"
    return runtime, json.loads(process.stdout)


def test_real_dll_signature_install_restart_and_request_schema(actual_signed_documents, tmp_path):
    runtime, documents = actual_signed_documents
    manager = LicenseManager(runtime, tmp_path / "许可证目录" / "license.json", documents["publicKeyXml"])
    source = tmp_path / "授权.json"
    source.write_text(json.dumps(documents["valid"]), encoding="utf-8")
    assert manager.install_license(source)["installed"]
    restarted = LicenseManager(runtime, manager.license_path, documents["publicKeyXml"])
    assert restarted.load_license()["payload"]["scope"] == "permanent"
    assert restarted.install_license(source)["alreadyInstalled"]


@pytest.mark.parametrize("variant,code", [
    ("wrongBoard", "LICENSE_HARDWARE_MISMATCH"),
    ("wrongProduct", "LICENSE_PRODUCT_MISMATCH"),
    ("duplicate", "LICENSE_DOCUMENT_INVALID"),
])
def test_real_dll_rejects_validly_signed_unauthorized_documents(actual_signed_documents, tmp_path, variant, code):
    runtime, documents = actual_signed_documents
    manager = LicenseManager(runtime, tmp_path / "license.json", documents["publicKeyXml"])
    assert_code(code, lambda: manager.verify_document(json.dumps(documents[variant])))
    assert not manager.license_path.exists()


def test_real_dll_rejects_tampered_bytes_and_missing_component(actual_signed_documents, tmp_path):
    runtime, documents = actual_signed_documents
    manager = LicenseManager(runtime, tmp_path / "license.json", documents["publicKeyXml"])
    tampered = dict(documents["valid"], signature="A" + documents["valid"]["signature"][1:])
    if tampered["signature"] == documents["valid"]["signature"]:
        tampered["signature"] = "B" + tampered["signature"][1:]
    assert_code("LICENSE_SIGNATURE_INVALID", lambda: manager.verify_document(json.dumps(tampered)))
    empty = tmp_path / "缺失 DLL"
    empty.mkdir()
    shutil.copy2(runtime / "DeviceLicense.Bridge.exe", empty / "DeviceLicense.Bridge.exe")
    broken = LicenseManager(empty, tmp_path / "license.json", documents["publicKeyXml"])
    assert_code("LICENSE_BRIDGE_UNAVAILABLE", lambda: broken.verify_document(json.dumps(documents["valid"])))


def test_modified_component_cannot_be_trusted_by_editing_manifest(actual_signed_documents, tmp_path):
    runtime, documents = actual_signed_documents
    relocated = tmp_path / "组件副本"
    relocated.mkdir()
    for filename in licensing.COMPONENT_SHA256:
        shutil.copy2(runtime / filename, relocated / filename)
    changed = relocated / "DeviceLicense.dll"
    changed.write_bytes(changed.read_bytes() + b"modified")
    (relocated / "build-manifest.json").write_text('{"artifacts":[]}', encoding="utf-8")
    manager = LicenseManager(relocated, tmp_path / "license.json", documents["publicKeyXml"])
    assert_code("LICENSE_COMPONENT_INTEGRITY_INVALID", lambda: manager.verify_document(json.dumps(documents["valid"])))
