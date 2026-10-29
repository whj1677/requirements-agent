"""Document contracts and snapshot operations must not initialize task execution."""
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize('module', ['document_schema', 'prd', 'request_context', 'response_application'])
def test_document_helpers_import_without_provider_or_workflow(tmp_path, module):
    program = f'''
import importlib
import sys
importlib.import_module('app.{module}')
assert 'app.provider' not in sys.modules
assert 'app.workflow' not in sys.modules
assert 'app.main' not in sys.modules
'''
    result = subprocess.run([sys.executable, '-B', '-c', program],
        cwd=Path(__file__).resolve().parents[1],
        env=dict(os.environ, RA_DATA_DIR=str(tmp_path / 'unused-data'), PYTHONUTF8='1'),
        capture_output=True, text=True, encoding='utf-8', timeout=30)
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / 'unused-data').exists()
