"""Synthetic export atomicity/concurrency checks; no application or network startup."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import copy
import json
import sqlite3
import threading
import zipfile

import pytest

from app.core import Problem, hashes
from app import exports
from app.store import Store
from tests.helpers import prepared


def baseline_fixture(tmp_path):
    store = Store(tmp_path)
    project = prepared(store)
    baseline_id = store.record(project['id'], 'baseline', dict(
        project=project, hashes=hashes(project), confirmation_id='synthetic-confirmation'))
    return store, project, baseline_id


@pytest.mark.parametrize('same_baseline', [True, False])
def test_concurrent_handoff_same_key_publishes_exactly_one_archive(tmp_path, monkeypatch, same_baseline):
    store, project, first_id = baseline_fixture(tmp_path)
    second_id = first_id if same_baseline else store.record(project['id'], 'baseline', dict(
        project=copy.deepcopy(project), hashes=hashes(project), confirmation_id='second-confirmation'))
    barrier = threading.Barrier(2, timeout=5)
    real_zip = exports.zip_files

    def compress(files):
        barrier.wait()
        return real_zip(files)

    def export(baseline_id):
        try:
            return exports.handoff(store, project['id'], baseline_id, 'concurrent-key')
        except Problem as error:
            return error

    monkeypatch.setattr(exports, 'zip_files', compress)
    with ThreadPoolExecutor(max_workers=2) as workers:
        jobs = [workers.submit(export, bid) for bid in (first_id, second_id)]
        results = [job.result(timeout=10) for job in jobs]
    records = store.records(project['id'], 'export')
    assert len(records) == 1
    if same_baseline:
        assert all(isinstance(result, dict) and result['id'] == records[0]['id'] for result in results)
    else:
        assert sum(isinstance(result, dict) for result in results) == 1
        errors = [result for result in results if isinstance(result, Problem)]
        assert len(errors) == 1 and errors[0].code == 'IDEMPOTENCY_CONFLICT'
    paths = list((tmp_path / 'exports').iterdir())
    assert [path.name for path in paths] == [records[0]['id'] + '.zip']
    with zipfile.ZipFile(paths[0]) as archive:
        assert archive.testzip() is None
        assert json.loads(archive.read('manifest.json')) == records[0]['manifest']


def test_existing_idempotent_handoff_does_not_regenerate_or_overwrite(tmp_path, monkeypatch):
    store, project, baseline_id = baseline_fixture(tmp_path)
    first = exports.handoff(store, project['id'], baseline_id, 'same-request')
    path = tmp_path / 'exports' / (first['id'] + '.zip')
    original = path.read_bytes()
    monkeypatch.setattr(exports, 'zip_files', lambda _: pytest.fail('idempotent retry regenerated archive'))
    second = exports.handoff(store, project['id'], baseline_id, 'same-request')
    assert first == second and path.read_bytes() == original
    assert len(store.records(project['id'], 'export')) == 1


@pytest.mark.parametrize('failure_stage', ['compression', 'flush', 'rename', 'record', 'commit'])
def test_handoff_failure_never_leaves_downloadable_or_temporary_archive(tmp_path, monkeypatch, failure_stage):
    store, project, baseline_id = baseline_fixture(tmp_path)

    def fail(*args, **kwargs):
        raise OSError('synthetic export failure')

    if failure_stage == 'compression':
        monkeypatch.setattr(exports, 'zip_files', fail)
    elif failure_stage == 'flush':
        monkeypatch.setattr(exports.os, 'fsync', fail)
    elif failure_stage == 'rename':
        monkeypatch.setattr(exports.os, 'replace', fail)
    elif failure_stage == 'record':
        real_record = store.record

        def record(pid, kind, *args, **kwargs):
            if kind == 'export':
                # The complete renamed file remains inaccessible until its
                # database record commits; the download route requires that row.
                files = list((tmp_path / 'exports').glob('*.zip'))
                assert len(files) == 1
                with pytest.raises(Problem) as error:
                    store.get_record(pid, files[0].stem, 'export')
                assert error.value.code == 'NOT_FOUND'
                fail()
            return real_record(pid, kind, *args, **kwargs)

        monkeypatch.setattr(store, 'record', record)
    else:
        real_connect = store.connect

        @contextmanager
        def connect():
            with real_connect() as db:
                yield db
                if db.in_transaction and db.execute("SELECT 1 FROM records WHERE kind='export'").fetchone():
                    # Exercise a real SQLite commit failure, not merely a
                    # rendering exception before the publication transaction.
                    def authorizer(action, argument, *_):
                        return sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_TRANSACTION and argument == 'COMMIT' else sqlite3.SQLITE_OK
                    db.set_authorizer(authorizer)

        monkeypatch.setattr(store, 'connect', connect)

    with pytest.raises((OSError, sqlite3.DatabaseError)):
        exports.handoff(store, project['id'], baseline_id, 'failed-request')
    assert store.records(project['id'], 'export') == []
    assert not (tmp_path / 'exports').exists() or list((tmp_path / 'exports').iterdir()) == []
    assert store.get_record(project['id'], baseline_id, 'baseline')['project'] == project


def test_project_change_during_render_keeps_confirmed_snapshot(tmp_path, monkeypatch):
    store, project, baseline_id = baseline_fixture(tmp_path)
    baseline_before = store.get_record(project['id'], baseline_id, 'baseline')
    original_statement = project['items'][0]['statement']
    real_zip = exports.zip_files

    def compress(files):
        with store.edit(project['id'], project['revision'], 'synthetic later edit') as (current, _):
            current['items'][0]['statement'] = '之后编辑的内容，不属于已确认基线。'
        return real_zip(files)

    monkeypatch.setattr(exports, 'zip_files', compress)
    result = exports.handoff(store, project['id'], baseline_id, 'snapshot-request')
    with zipfile.ZipFile(tmp_path / 'exports' / (result['id'] + '.zip')) as archive:
        text = archive.read('requirements.md').decode('utf-8')
    assert original_statement in text and '之后编辑的内容' not in text
    assert store.get_record(project['id'], baseline_id, 'baseline') == baseline_before


def test_changed_baseline_during_render_is_rejected(tmp_path, monkeypatch):
    store, project, baseline_id = baseline_fixture(tmp_path)
    real_zip = exports.zip_files

    def compress(files):
        # Baseline editing is not a product operation. Fault injection proves
        # publication cannot silently bind bytes to a replaced baseline record.
        store.update_record(project['id'], baseline_id, 'baseline', confirmation_id='unexpected-change')
        return real_zip(files)

    monkeypatch.setattr(exports, 'zip_files', compress)
    with pytest.raises(Problem) as error:
        exports.handoff(store, project['id'], baseline_id, 'changed-baseline')
    assert error.value.code == 'STALE_BASELINE'
    assert store.records(project['id'], 'export') == []
    assert list((tmp_path / 'exports').iterdir()) == []
