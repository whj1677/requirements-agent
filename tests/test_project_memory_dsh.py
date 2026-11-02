"""Real installed DSH tool runtime, mocked HTTP: memory is in the read snapshot."""
import json

import pytest

from app import dsh_intake, project_memory
from tests import test_dsh_intake as harness


@pytest.mark.skipif(not dsh_intake.runtime_status()['ready'], reason='pinned SDK not installed')
def test_memory_in_actual_sdk_read_snapshot(tmp_path, monkeypatch):
    original=harness.inputs
    def inputs():
        trace=dict(snapshot_id='SNAP-offline',input_hash='frozen-source-input',entries=[
            dict(id='M1',statement='不允许远程控制。',scope='仅本项目',
                 source_refs=[],confirmation='not_confirmed')])
        return project_memory.attach(original(),trace,'ingest')
    monkeypatch.setattr(harness,'inputs',inputs)
    harness.test_real_sdk_tools_and_counted_mock_transport(tmp_path,monkeypatch,'identity')
    root=tmp_path/'evidence/dsh-intake/CALLsynthetic'
    snapshot=json.loads((root/'snapshot/current-workspace.json').read_text(encoding='utf8'))
    assert snapshot['project_memory']['snapshot_id']=='SNAP-offline'
    proof=json.loads((tmp_path/'evidence/model-calls/CALLsynthetic.json').read_text(encoding='utf8'))
    row=next(r for r in proof['dsh_reading_coverage']['files'] if r['path']=='current-workspace.json')
    assert row['complete'] is True
