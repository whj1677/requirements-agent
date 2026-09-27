"""Offline export responsiveness checks; never bind a port or touch daily data."""
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import textwrap

import pytest


def run_isolated_program(tmp_path, program, **variables):
    environment = dict(os.environ, RA_DATA_DIR=str(tmp_path / 'module-data'),
                       RA_ACCESS_TOKEN='off', PYTHONUTF8='1', **variables)
    # Keep the module-level default app and its dotenv lookup off daily data.
    prelude = '''
import os
from pathlib import Path
import app.config
app.config.ROOT = Path(os.environ['RA_DATA_DIR'])
'''
    result = subprocess.run([sys.executable, '-c', textwrap.dedent(prelude) + textwrap.dedent(program)],
                            cwd=Path(__file__).resolve().parents[1], env=environment,
                            capture_output=True, text=True, encoding='utf-8', timeout=20)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('slow_stage', ['document', 'zip'])
def test_document_download_does_not_block_other_api_requests(tmp_path, slow_stage):
    # app.main has a module-level application. Isolate it before import and redirect
    # the default dotenv location as well as the database; no developer key is read.
    program = r'''
import asyncio
import os
from pathlib import Path
import threading
import httpx
import app.config
app.config.ROOT = Path(os.environ['RA_DATA_DIR'])
import app.main as main
from app.core import brief_hash
from tests.helpers import prepared

app = main.create_app(Path(os.environ['RA_DATA_DIR']) / 'case', env_path=Path(os.environ['RA_DATA_DIR']) / 'absent.env')
store = app.state.store
p = prepared(store)
with store.edit(p['id'], p['revision'], 'synthetic export setup', bump=False) as (p, db):
    p['questions'] = []
    p['documents']['prd']['brief_hash'] = brief_hash(p)
    p['documents']['prd']['item_snapshot'] = p['items']
    p['documents']['prd']['question_snapshot'] = []
    p['documents']['prd']['source_snapshot'] = []
    p['documents']['prd']['requirement_name'] = 'synthetic export responsiveness'

started, release, finished = threading.Event(), threading.Event(), threading.Event()
def slow_work():
    started.set()
    try:
        # A finite wait prevents a broken implementation from leaking a worker.
        release.wait(2)
    finally:
        finished.set()

async def render(*args, **kwargs):
    if os.environ['RA_TEST_SLOW_STAGE'] == 'document':
        slow_work()
    return {'PRD.md': b'synthetic', 'PRD.docx': b'synthetic', 'document_asset_bindings.json': b'[]'}

real_zip = main.zip_files
def compress(files):
    if os.environ['RA_TEST_SLOW_STAGE'] == 'zip':
        slow_work()
    return real_zip(files)

main.document_files, main.zip_files = render, compress

async def exercise():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
        download = asyncio.create_task(client.get('/api/projects/' + p['id'] + '/documents/prd/zip'))
        try:
            assert await asyncio.to_thread(started.wait, 3), 'slow stage never started'
            response = await client.get('/api/projects')
            assert response.status_code == 200, response.text
            assert not finished.is_set(), 'unrelated API waited for the slow export stage'
        finally:
            release.set()
            response = await asyncio.wait_for(download, 5)
        assert response.status_code == 200, response.text

asyncio.run(exercise())
'''
    run_isolated_program(tmp_path, program, RA_TEST_SLOW_STAGE=slow_stage)


def test_ui_change_during_document_export_rejects_stale_download(tmp_path):
    run_isolated_program(tmp_path, r'''
import asyncio
import httpx
import app.main as main
from app.core import brief_hash
from tests.helpers import prepared
app = main.create_app(Path(os.environ['RA_DATA_DIR']) / 'case', env_path=Path(os.environ['RA_DATA_DIR']) / 'absent.env')
store = app.state.store
p = prepared(store)
with store.edit(p['id'], p['revision'], 'synthetic current document', bump=False) as (p, _):
    p['questions'] = []
    p['documents']['prd']['brief_hash'] = brief_hash(p)

async def render(*args, **kwargs):
    current = store.get(p['id'])
    with store.edit(p['id'], current['revision'], 'synthetic concurrent UI acceptance', bump=False) as (current, _):
        current['stale_document_kinds'] = ['prd']
        current['ui']['spec']['title'] = 'synthetic revised UI'
    # UI acceptance leaves the brief and artifact identity unchanged. The final
    # export gate must also recheck the explicit stale-document state.
    assert brief_hash(current) == brief_hash(p)
    assert current['documents']['prd']['id'] == p['documents']['prd']['id']
    return {'PRD.md': b'synthetic', 'PRD.docx': b'synthetic', 'document_asset_bindings.json': b'[]'}

main.document_files = render
async def exercise():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
        response = await client.get('/api/projects/' + p['id'] + '/documents/prd/zip')
        assert response.status_code == 409, response.text
        assert response.json()['code'] == 'STALE_DOCUMENT'
    assert store.records(p['id'], 'document_export') == []
    assert store.get(p['id'])['ui']['spec']['title'] == 'synthetic revised UI'
asyncio.run(exercise())
''')


def test_export_timeout_retains_worker_capacity_until_processing_finishes(tmp_path):
    run_isolated_program(tmp_path, r'''
import asyncio
import threading
import httpx
import app.main as main
from app.core import brief_hash
from tests.helpers import prepared
app = main.create_app(Path(os.environ['RA_DATA_DIR']) / 'case', env_path=Path(os.environ['RA_DATA_DIR']) / 'absent.env')
store = app.state.store
p = prepared(store)
with store.edit(p['id'], p['revision'], 'synthetic current document', bump=False) as (p, _):
    p['questions'] = []
    p['documents']['prd']['brief_hash'] = brief_hash(p)

release = threading.Event()
started = []
lock = threading.Lock()
async def render(*args, **kwargs):
    with lock:
        started.append(threading.get_ident())
    assert release.wait(3), 'bounded synthetic worker was not released'
    return {'PRD.md': b'synthetic', 'PRD.docx': b'synthetic', 'document_asset_bindings.json': b'[]'}
main.document_files = render
original_wait_for = asyncio.wait_for
async def short_export_wait(awaitable, timeout):
    # Shorten only the application's 180-second response wait. Other event-loop
    # deadlines, including subprocess cleanup, retain their original behavior.
    return await original_wait_for(awaitable, .1 if timeout == 180 else timeout)
asyncio.wait_for = short_export_wait

async def exercise():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
        url = '/api/projects/' + p['id'] + '/documents/prd/zip'
        try:
            first = await client.get(url)
            second = await client.get(url)
            assert all(response.status_code == 503 and response.json()['code'] == 'EXPORT_TIMEOUT'
                       for response in (first, second))
            assert len(started) == 2
            busy = await client.get(url)
            assert busy.status_code == 409 and busy.json()['code'] == 'EXPORT_BUSY'
            assert len(started) == 2, 'timeout released a still-running worker slot'
            assert (await client.get('/api/projects')).status_code == 200
        finally:
            release.set()
        # The completion callback releases capacity asynchronously; retry only
        # the harmless busy response within a finite one-second observation.
        for _ in range(50):
            await asyncio.sleep(.02)
            response = await client.get(url)
            if response.status_code == 200:
                break
            assert response.status_code == 409 and response.json()['code'] == 'EXPORT_BUSY'
        assert response.status_code == 200, response.text
        assert len(started) == 3
try:
    asyncio.run(exercise())
finally:
    release.set()
    asyncio.wait_for = original_wait_for
''')


def test_handoff_archive_generation_does_not_hold_database_write_lock(tmp_path, monkeypatch):
    from app.core import hashes
    from app import exports
    from app.store import Store
    from tests.helpers import prepared

    store = Store(tmp_path)
    project = prepared(store)
    baseline_id = store.record(project['id'], 'baseline', dict(
        project=project, hashes=hashes(project), confirmation_id='synthetic-confirmation'))
    real_zip = exports.zip_files
    write_was_blocked = []

    def compress(files):
        # A second connection can acquire a write transaction while the archive
        # is generated. This checks lock scope without timing a real API request.
        with sqlite3.connect(store.path, timeout=.05) as other:
            try:
                other.execute('BEGIN IMMEDIATE')
            except sqlite3.OperationalError as error:
                write_was_blocked.append(str(error))
            finally:
                other.rollback()
        return real_zip(files)

    monkeypatch.setattr(exports, 'zip_files', compress)
    result = exports.handoff(store, project['id'], baseline_id, 'synthetic-lock-test')
    assert (tmp_path / 'exports' / (result['id'] + '.zip')).is_file()
    assert not write_was_blocked, 'archive generation held the database write lock'
