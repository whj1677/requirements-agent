"""Offline checks of the package contents and guarded installer sequence."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

import pytest

from scripts.package_local import package, source_paths

ROOT = Path(__file__).resolve().parents[1]


def build_fixture(folder):
    assets = folder / 'assets'
    assets.mkdir(parents=True)
    (assets / 'app.js').write_text('console.log("synthetic")', 'utf-8')
    (folder / 'index.html').write_text('<script src="/assets/app.js"></script>', 'utf-8')


def test_package_includes_current_product_code_and_runs_offline(tmp_path):
    dist = tmp_path / 'reviewed-dist'
    build_fixture(dist)

    def builder(root, destination):
        shutil.copytree(dist, destination)

    output = tmp_path / 'release'
    archive = package(ROOT, dist, output, builder=builder)
    target = output / 'requirements-agent'
    manifest = json.loads((target / 'release-manifest.json').read_text('utf-8'))
    assert manifest['web_build_verified'] is True
    assert isinstance(manifest['working_tree_dirty'], bool)
    assert isinstance(manifest['source_commit'], str) and len(manifest['source_commit']) == 40
    for name in ('app/material_images.py', 'app/understanding.py',
                 'scripts/check_active_tasks.py', 'web/src/main.tsx'):
        assert name in manifest['files'] and (target / name).is_file()
    assert not any(name.startswith(('data/', 'evidence/', '原型/', 'tests/')) or
                   '/.env' in name or name == '.env' for name in manifest['files'])
    assert manifest['files']['web/dist/index.html'] == manifest['web_dist_files']['index.html']

    unpacked = tmp_path / 'unpacked'
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(unpacked)
    extracted = unpacked / 'requirements-agent'
    environment = dict(os.environ, RA_DATA_DIR=str(tmp_path / 'isolated-data'),
                       RA_ACCESS_TOKEN='off', PYTHONUTF8='1')
    check = ('from app.main import create_app; '
             'from fastapi.testclient import TestClient; '
             'app=create_app(__import__("pathlib").Path(__import__("os").environ["RA_DATA_DIR"])); '
             'assert TestClient(app).get("/api/session").status_code == 200')
    result = subprocess.run([sys.executable, '-c', check], cwd=extracted,
                            env=environment, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / 'isolated-data' / 'requirements.sqlite3').is_file()


def test_package_rejects_build_that_differs_from_source(tmp_path):
    dist = tmp_path / 'reviewed-dist'
    build_fixture(dist)

    def divergent(root, destination):
        build_fixture(destination)
        (destination / 'assets' / 'app.js').write_text('different', 'utf-8')

    output = tmp_path / 'release'
    with pytest.raises(RuntimeError, match='不一致'):
        package(ROOT, dist, output, builder=divergent)
    assert not (output / 'requirements-agent').exists()


def test_required_files_and_installer_guard_order():
    paths = {path.as_posix() for path in source_paths(ROOT)}
    assert {'scripts/install.ps1', 'scripts/start.ps1', 'scripts/stop.ps1',
            'scripts/check_active_tasks.py', 'app/office_worker.ps1'} <= paths
    install = (ROOT / 'scripts' / 'install.ps1').read_text('utf-8-sig')
    assert install.index('Assert-WorkbenchIdle\n') < install.index('-m pip install')
    assert install.index('npm.cmd run build -- --outDir $verifiedDist') < install.index('Move-Item -LiteralPath $verifiedDist')
    assert install.rindex('Assert-WorkbenchIdle\n') < install.index('Move-Item -LiteralPath $verifiedDist')
    assert 'npm.cmd run build\n' not in install
