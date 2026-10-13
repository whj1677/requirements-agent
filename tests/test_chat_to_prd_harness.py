"""Safety of the opt-in real-chat trial; these tests never contact a provider."""
import asyncio
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from scripts import run_chat_to_prd_validation as trial


def test_unapproved_phase_stops_before_reading_daily_configuration(monkeypatch):
    def forbidden():
        raise AssertionError('Daily configuration must not be read without explicit real execution')
    monkeypatch.setattr(trial, 'load_config', forbidden)
    with pytest.raises(ValueError, match='allow-real-provider'):
        asyncio.run(trial.run(SimpleNamespace(output=trial.ROOT/'work/never-created-opt-in-test',
                                             phase='chat', allow_real_provider=False)))
    assert not (trial.ROOT/'work/never-created-opt-in-test').exists()


def test_active_phase_is_refused_without_pausing_record(tmp_path):
    folder = tmp_path / 'isolated-store'
    folder.mkdir()
    path = folder / 'requirements.sqlite3'
    original = json.dumps({'id': 'TASK-live', 'status': 'running'})
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE records(kind TEXT,payload TEXT)')
        db.execute('INSERT INTO records VALUES(?,?)', ('user_task', original))
    with pytest.raises(RuntimeError, match='Previous phase still has active'):
        trial.assert_idle(folder)
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT payload FROM records').fetchone()[0] == original


def test_evidence_reports_actual_knowledge_and_hides_request_material(tmp_path):
    folder = tmp_path / 'evidence/model-calls'
    folder.mkdir(parents=True)
    trial.write(folder/'CALL-1.json', {
        'call_id': 'CALL-1', 'stage': 'interview', 'origin': 'https://api.deepseek.com',
        'authorization': {'platform_knowledge': {'bundle_id': 'storage', 'sha256': 'abc'}},
        'final_output': 'real response', 'request_input': {'secret_example': 'omit'},
        'api_key': 'omit', 'proxy': 'omit',
    })
    value = trial.call_evidence(tmp_path)[0]
    assert value['platform_knowledge']['sha256'] == 'abc'
    assert value['raw_output_sha256'] == trial.sha(b'real response')
    assert not {'request_input', 'api_key', 'proxy'} & set(value)
