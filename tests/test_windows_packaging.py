"""Customer-distribution boundaries; executable behavior is validated separately."""
from pathlib import Path
import runpy

import pytest

from scripts.build_windows import (copy_browser, required_inputs, validate_distribution,
                                   render_customer_guides, verify_native_file_hashes, sha256)


def minimum_distribution(folder):
    for name in ('RequirementsAgent.exe', '_internal/python311.dll',
                 '_internal/app/license_runtime/DeviceLicense.dll',
                 '_internal/app/license_runtime/DeviceLicense.Bridge.exe',
                 '_internal/web/dist/index.html', '_internal/playwright/driver/node.exe',
                 '_internal/web/activation/index.html', '_internal/web/activation/app.js',
                 '_internal/web/activation/style.css',
                 'docs/user-guide.md', 'docs/windows-installation.md',
                 'docs/user-guide.html', 'docs/windows-installation.html',
                 'licenses/Python-LICENSE.txt', 'licenses/inventory.json'):
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'synthetic boundary fixture, not an executable')


@pytest.mark.parametrize('forbidden', [
    '.env', '_internal/.env', '_internal/app/licensing.py',
    'data/requirements.sqlite3', 'issuer/tool.exe', 'keys/owner-private-key.json',
    'owner.pfx', 'keys/signing.key',
])
def test_customer_package_rejects_secrets_data_issuer_and_plaintext_source(tmp_path, forbidden):
    minimum_distribution(tmp_path)
    validate_distribution(tmp_path)
    unexpected = tmp_path / forbidden
    unexpected.parent.mkdir(parents=True, exist_ok=True)
    unexpected.write_bytes(b'synthetic forbidden input')
    with pytest.raises(RuntimeError):
        validate_distribution(tmp_path)


def test_browser_copy_excludes_cache_logs_and_rejects_profiles(tmp_path):
    source = tmp_path / 'cache'
    source.mkdir()
    (source / 'chrome-headless-shell.exe').write_bytes(b'synthetic browser')
    (source / 'LICENSE.headless_shell').write_text('synthetic notice', 'utf-8')
    (source / 'debug.log').write_text('do not redistribute host logs', 'utf-8')
    output = tmp_path / 'copy'
    copy_browser(source, output)
    assert sorted(path.name for path in output.iterdir()) == ['LICENSE.headless_shell', 'chrome-headless-shell.exe']
    (source / 'Default').mkdir()
    (source / 'Default/Cookies').write_bytes(b'synthetic user profile')
    with pytest.raises(RuntimeError, match='user state'):
        copy_browser(source, tmp_path / 'rejected-copy')


def test_customer_browser_runtime_ignores_external_overrides(tmp_path, monkeypatch):
    import sys
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, '_MEIPASS', str(tmp_path), raising=False)
    monkeypatch.setenv('PLAYWRIGHT_BROWSERS_PATH', 'external browser')
    monkeypatch.setenv('PLAYWRIGHT_NODEJS_PATH', 'external node')
    monkeypatch.delenv('PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD', raising=False)
    import os
    runpy.run_path(str(Path(__file__).resolve().parents[1] / 'packaging/runtime_hook.py'))
    assert os.environ['PLAYWRIGHT_BROWSERS_PATH'] == str(tmp_path / 'browsers')
    assert 'PLAYWRIGHT_NODEJS_PATH' not in os.environ


def test_build_inputs_do_not_collect_working_directory_secrets(tmp_path):
    required = ['requirements.lock.txt', 'scripts/desktop.py', 'scripts/build_windows.py',
                'web/package.json', 'web/package-lock.json', 'web/index.html', 'web/tsconfig.json',
                'docs/user-guide.md', 'docs/windows-installation.md',
                'app/license_runtime/DeviceLicense.dll', 'app/license_runtime/DeviceLicense.Bridge.exe',
                'app/license_runtime/build-manifest.json', 'app/license_runtime/README.md']
    for name in required + ['app/licensing.py', 'app/license_trust.py', 'web/src/main.tsx',
                            '.env', 'data/requirements.sqlite3', 'issuer/private.json']:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('synthetic', 'utf-8')
    for name in ('examples', 'prompts', 'references'):
        (tmp_path / 'requirements-agent-codex-kit-v1.1' / name).mkdir(parents=True)
    (tmp_path / 'packaging').mkdir()
    paths = {path.relative_to(tmp_path).as_posix() for path in required_inputs(tmp_path)}
    assert 'app/license_trust.py' in paths
    assert not paths & {'.env', 'data/requirements.sqlite3', 'issuer/private.json'}


def test_guides_open_offline_and_link_only_to_bundled_documents(tmp_path):
    docs = tmp_path / 'docs'
    docs.mkdir()
    for name in ('windows-installation.md', 'user-guide.md'):
        (docs / name).write_text('# 中文指南\n\n[业务](user-guide.md)\n\n[工程](../README.md)\n\n'
                                  '|列一|列二|\n|---|---|\n|甲|乙|\n\n```powershell\nGet-Date\n```', 'utf-8')
    destination = tmp_path / 'output'
    render_customer_guides(tmp_path, destination)
    page = (destination / 'windows-installation.html').read_text('utf-8')
    assert '<table>' in page and '<pre>' in page and '<h1' in page
    assert 'href="user-guide.html"' in page
    assert '../README.md' not in page and '工程（源码仓库资料）' in page
    assert 'Content-Security-Policy' in page
    assert '<script' not in page


def test_windows_native_reader_rejects_bytes_that_differ_from_manifest(tmp_path):
    import json
    sample = tmp_path / 'synthetic.dat'
    sample.write_bytes(b'non-sensitive package-check fixture')
    hashes = {'synthetic.dat': sha256(sample)}
    report = tmp_path / 'native-check.json'
    assert verify_native_file_hashes(tmp_path, hashes, report)['status'] == 'NATIVE_BYTES_MATCH'
    sample.write_bytes(b'changed after the manifest was calculated')
    with pytest.raises(RuntimeError, match='different package bytes'):
        verify_native_file_hashes(tmp_path, hashes, report)
    result = json.loads(report.read_text('utf-8'))
    assert result['status'] == 'NATIVE_BYTES_DIFFER'
    assert result['mismatches'][0]['file'] == 'synthetic.dat'
