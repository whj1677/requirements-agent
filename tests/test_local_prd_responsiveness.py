"""Offline local PRD routing and concurrent submission checks; no listener."""
import pytest

from tests.test_export_responsiveness import run_isolated_program


SETUP = r'''
import asyncio
import copy
import threading
import httpx
import app.main as main
import app.local_prd as local_prd
from tests.test_local_prd import body
from tests.test_local_prd_flow_recovery import basic_project
from tests.test_runtime import FakeProvider

folder = Path(os.environ['RA_DATA_DIR']) / 'case'
_, project = basic_project(folder)
provider = FakeProvider([])
app = main.create_app(folder, access_token='isolated-local-prd', provider=provider,
                      env_path=folder / 'absent.env')
store = app.state.store
request = body(project)
url = '/api/projects/' + project['id'] + '/documents/prd/'

async def authenticate(client):
    login = await client.post('/api/session/login', json={'key': 'isolated-local-prd'})
    assert login.status_code == 200, login.text
    client.headers.update({'Origin': 'http://testserver', 'X-CSRF-Token': login.json()['csrf']})
'''


@pytest.mark.parametrize('operation', ['preview', 'generate'])
def test_local_prd_work_does_not_block_other_api_requests(tmp_path, operation):
    run_isolated_program(tmp_path, SETUP + r'''
operation = os.environ['RA_TEST_LOCAL_OPERATION']
real_operation = getattr(local_prd, operation)
started, release, finished = threading.Event(), threading.Event(), threading.Event()

def controlled_operation(*args, **kwargs):
    started.set()
    try:
        # The finite wait allows a broken event-loop implementation to finish,
        # so the assertion reports the blocking instead of leaving a worker.
        release.wait(2)
        return real_operation(*args, **kwargs)
    finally:
        finished.set()

setattr(local_prd, operation, controlled_operation)

async def exercise():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                base_url='http://testserver') as client:
        await authenticate(client)
        payload = {'expected_revision': project['revision']} if operation == 'preview' else request
        suffix = 'local-preview' if operation == 'preview' else 'local-review'
        pending = asyncio.create_task(client.post(url + suffix, json=payload))
        try:
            assert await asyncio.to_thread(started.wait, 3), 'local PRD operation never started'
            response = await client.get('/api/projects')
            assert response.status_code == 200, response.text
            assert not finished.is_set(), 'unrelated API waited for local PRD work'
        finally:
            release.set()
            response = await asyncio.wait_for(pending, 5)
        assert response.status_code == 200, response.text
        if operation == 'preview':
            assert response.json()['preview_hash'] == request['preview_hash']
            assert store.get(project['id']) == project
        else:
            result = response.json()
            assert result['receipt']['model_calls'] == 0
            assert result['project']['revision'] == project['revision'] + 1
            repeat = await client.post(url + suffix, json=request)
            assert repeat.status_code == 200 and repeat.json()['repeated'], repeat.text
            assert repeat.json()['artifact_id'] == result['artifact_id']
            assert len(store.records(project['id'], 'document_artifact')) == 1
        assert not provider.received

asyncio.run(exercise())
''', RA_TEST_LOCAL_OPERATION=operation)


@pytest.mark.parametrize('second_request', ['same_request', 'different_key', 'changed_payload'])
def test_concurrent_local_prd_serializes_receipt_and_revision_checks(tmp_path, second_request):
    run_isolated_program(tmp_path, SETUP + r'''
first_started, second_checked, release = threading.Event(), threading.Event(), threading.Event()
real_compile = local_prd.compile_plan
real_records = store.records
lookup_count = 0
lookup_lock = threading.Lock()

def controlled_compile(*args, **kwargs):
    first_started.set()
    assert release.wait(4), 'concurrent local PRD test did not release the first transaction'
    return real_compile(*args, **kwargs)

def observe_records(pid, kind, db=None):
    global lookup_count
    rows = real_records(pid, kind, db)
    if kind == local_prd.RECEIPT_KIND and db is None:
        with lookup_lock:
            lookup_count += 1
            if lookup_count == 2:
                # Both submissions have observed no committed receipt. The
                # first still holds the real SQLite write transaction.
                assert rows == [], rows
                second_checked.set()
    return rows

local_prd.compile_plan = controlled_compile
store.records = observe_records

async def exercise():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                base_url='http://testserver') as client:
        await authenticate(client)
        first = asyncio.create_task(client.post(url + 'local-review', json=request))
        second = None
        try:
            assert await asyncio.to_thread(first_started.wait, 3), 'first generation never started'
            assert not first.done(), 'first transaction did not remain in progress'
            second_payload = copy.deepcopy(request)
            mode = os.environ['RA_TEST_SECOND_REQUEST']
            if mode == 'different_key':
                second_payload['idempotency_key'] = 'different-local-request'
            elif mode == 'changed_payload':
                second_payload['preview_hash'] = '0' * 64
            second = asyncio.create_task(client.post(url + 'local-review', json=second_payload))
            assert await asyncio.to_thread(second_checked.wait, 3), 'second receipt lookup never overlapped'
            assert (await client.get('/api/projects')).status_code == 200
        finally:
            release.set()
            first_response = await asyncio.wait_for(first, 5)
            second_response = await asyncio.wait_for(second, 5) if second is not None else None
        assert first_response.status_code == 200, first_response.text
        if mode == 'same_request':
            assert second_response.status_code == 200, second_response.text
            assert second_response.json()['repeated']
            assert second_response.json()['artifact_id'] == first_response.json()['artifact_id']
        else:
            assert second_response.status_code == 409, second_response.text
            code = 'STALE_REVISION' if mode == 'different_key' else 'IDEMPOTENCY_CONFLICT'
            assert second_response.json()['code'] == code, second_response.text
        for kind in ('document_artifact', 'local_prd_receipt', 'user_task'):
            assert len(real_records(project['id'], kind)) == 1, kind
        current = store.get(project['id'])
        assert current['revision'] == project['revision'] + 1
        assert current['items'] == project['items'] and current['questions'] == project['questions']
        assert not provider.received

asyncio.run(exercise())
''', RA_TEST_SECOND_REQUEST=second_request)
