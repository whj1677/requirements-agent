"""Single workbench: reject duplicate/alternate startup before touching data."""
import builtins

import pytest

from scripts import serve


def test_listener_reservation_excludes_second_instance():
    first = serve.reserve_listener(0)
    port = first.getsockname()[1]
    try:
        with pytest.raises(OSError):
            serve.reserve_listener(port)
    finally:
        first.close()
    with serve.reserve_listener(port):
        pass


def test_busy_port_does_not_import_app_or_initialize_store(monkeypatch):
    first = serve.reserve_listener(0)
    port = first.getsockname()[1]
    original = serve.reserve_listener
    imported = []
    real_import = builtins.__import__

    def watched_import(name, *args, **kwargs):
        imported.append(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.delenv('RA_PORT', raising=False)
    monkeypatch.setattr(serve, 'reserve_listener', lambda: original(port))
    monkeypatch.setattr(builtins, '__import__', watched_import)
    try:
        with pytest.raises(SystemExit, match='已被占用'):
            serve.main()
        assert 'app.main' not in imported
        assert 'uvicorn' not in imported
    finally:
        first.close()


def test_alternate_workbench_port_rejected_before_socket_or_data(monkeypatch):
    monkeypatch.setenv('RA_PORT', '8766')
    monkeypatch.setattr(serve, 'reserve_listener', lambda: pytest.fail('must reject before binding'))
    with pytest.raises(SystemExit, match='统一使用'):
        serve.main()
