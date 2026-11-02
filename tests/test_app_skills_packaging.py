"""Verify source and Windows asset lists, not installer execution or release."""
import json
import hashlib
import shutil
import subprocess
from pathlib import Path

from app.core import ROOT, KIT
from scripts.build_windows import builtin_skill_assets, required_inputs
from scripts.package_local import source_paths


def test_windows_git_checkout_preserves_upstream_bytes(tmp_path):
    base = KIT / 'prompts/skills'
    manifest = json.loads((base / 'upstream.json').read_text('utf-8'))
    originals = [file for skill in manifest['skills'] for file in skill['files']]
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    subprocess.run(['git', '-C', str(tmp_path), 'config', 'core.autocrlf', 'true'], check=True)
    shutil.copyfile(ROOT / '.gitattributes', tmp_path / '.gitattributes')
    for file in originals:
        target = tmp_path / base.relative_to(ROOT) / file['path']
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(base / file['path'], target)
    subprocess.run(['git', '-C', str(tmp_path), 'add', '.'], check=True)
    for file in originals:
        (tmp_path / base.relative_to(ROOT) / file['path']).unlink()
    subprocess.run(['git', '-C', str(tmp_path), 'checkout-index', '--all'], check=True)
    for file in originals:
        raw = (tmp_path / base.relative_to(ROOT) / file['path']).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == file['sha256'], file['path']


def test_skill_originals_licenses_policy_and_manifest_are_in_both_packages():
    base = KIT / 'prompts/skills'
    manifest = json.loads((base / 'upstream.json').read_text('utf-8'))
    needed = {base / name for name in ('application-policy.md', 'upstream.json')}
    for skill in manifest['skills']:
        needed.update(base / f['path'] for f in skill['files'])
        needed.add(base / ('vendor/' + skill['id'] + '/SOURCE.json'))
        for file in skill['files']:
            assert hashlib.sha256((base / file['path']).read_bytes()).hexdigest() == file['sha256']
    local = {ROOT / path for path in source_paths(ROOT)}
    windows = set(required_inputs(ROOT))
    assets = dict(builtin_skill_assets(ROOT))
    assert needed <= local and needed <= windows and needed <= assets.keys()
    for path in needed:
        assert assets[path] == path.parent.relative_to(ROOT).as_posix()
    assert all('data' not in p.relative_to(ROOT).parts and p.name != '.env' for p in assets)


def test_nested_skill_assets_do_not_collect_executables_or_credentials(tmp_path):
    root = tmp_path / 'requirements-agent-codex-kit-v1.1/prompts/skills/vendor/sample'
    root.mkdir(parents=True)
    for name in ('SKILL.md', 'LICENSE', 'NOTICE', '.env', 'run.py', 'setup.ps1', 'bundle.exe'):
        (root / name).write_text('synthetic packaging fixture', 'utf-8')
    assets = builtin_skill_assets(tmp_path)
    assert {p.name for p, _ in assets} == {'SKILL.md', 'LICENSE', 'NOTICE'}
    assert all(destination.endswith('/skills/vendor/sample') for _, destination in assets)
