"""Imports and ordinary store access must not pretend a server restarted."""
import os
from pathlib import Path
import subprocess
import sys

from app.core import digest
from app.store import Store


def test_factory_import_does_not_create_default_database(tmp_path):
    folder = tmp_path / 'not-created'
    result = subprocess.run(
        [sys.executable, '-B', '-c', 'from app.main import create_app'],
        cwd=Path(__file__).resolve().parents[1],
        env=dict(os.environ, RA_DATA_DIR=str(folder), PYTHONUTF8='1'),
        capture_output=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr.decode('utf-8', errors='replace')
    assert not folder.exists()


def test_opening_store_or_factory_preserves_active_tasks_until_explicit_recovery(tmp_path):
    from app.main import create_app

    store = Store(tmp_path)
    project = store.create('合成恢复检查')
    before = {}
    for kind, status in [('run', 'running'), ('user_task', 'queued')]:
        rid = store.record(project['id'], kind, dict(status=status, calls=1, request_budget={'used': 1}))
        before[kind] = store.get_record(project['id'], rid, kind)
    finished = store.record(project['id'], 'run', dict(status='succeeded', calls=2))
    app = create_app(tmp_path, env_path=tmp_path / 'absent.env')
    reopened = app.state.store
    for kind, value in before.items():
        assert reopened.get_record(project['id'], value['id'], kind) == value

    reopened.recover_interrupted_tasks()
    for kind, value in before.items():
        after = reopened.get_record(project['id'], value['id'], kind)
        assert after == dict(value, status='paused_budget', error='RESTARTED',
                             message='服务已重启。已发请求可能计费；请明确续跑。')
    assert reopened.get_record(project['id'], finished)['status'] == 'succeeded'
    assert reopened.get(project['id']) == project
    snapshot = digest(reopened.records(project['id'], 'run') + reopened.records(project['id'], 'user_task'))
    reopened.recover_interrupted_tasks()
    assert digest(reopened.records(project['id'], 'run') + reopened.records(project['id'], 'user_task')) == snapshot


def test_record_index_preserves_existing_payloads_and_record_order(tmp_path):
    store = Store(tmp_path)
    project = store.create('合成索引升级')
    expected = [store.record(project['id'], 'run', {'status': 'succeeded', 'calls': n}) for n in range(30)]
    with store.connect() as db:
        db.execute('DROP INDEX records_project_kind')
        before = db.execute('SELECT * FROM records ORDER BY rowid').fetchall()
        before = [tuple(row) for row in before]
    reopened = Store(tmp_path)
    with reopened.connect() as db:
        assert [tuple(row) for row in db.execute('SELECT * FROM records ORDER BY rowid')] == before
        plan = db.execute('EXPLAIN QUERY PLAN SELECT id,payload FROM records '
                          'WHERE project_id=? AND kind=? ORDER BY rowid', (project['id'], 'run')).fetchall()
        assert any('USING INDEX records_project_kind' in row['detail'] for row in plan)
    assert [row['id'] for row in reopened.records(project['id'], 'run')] == expected


def test_source_server_recovers_only_after_owning_listener(monkeypatch):
    from types import SimpleNamespace
    from app import main as main_module
    from scripts import serve
    import uvicorn

    events = []
    listener = SimpleNamespace(close=lambda: events.append('close'))
    app = SimpleNamespace(state=SimpleNamespace(
        store=SimpleNamespace(recover_interrupted_tasks=lambda: events.append('recover')),
        runtime={'runtime_id': 'synthetic'},
    ))

    def reserve():
        events.append('reserve')
        return listener

    def factory(folder):
        assert events == ['reserve']
        events.append('create')
        return app

    def run(sockets):
        assert sockets == [listener]
        events.append('serve')

    monkeypatch.delenv('RA_PORT', raising=False)
    monkeypatch.setattr(serve, 'install_crash_logging', lambda: None)
    monkeypatch.setattr(serve, 'reserve_listener', reserve)
    monkeypatch.setattr(main_module, 'create_app', factory)
    monkeypatch.setattr(uvicorn, 'Config', lambda *args, **kwargs: None)
    monkeypatch.setattr(uvicorn, 'Server', lambda config: SimpleNamespace(run=run))
    # serve.main sets the production directory only for its own invocation.
    monkeypatch.setenv('RA_DATA_DIR', 'isolated-synthetic-unused')
    serve.main()
    assert events == ['reserve', 'create', 'recover', 'serve', 'close']
