"""Synthetic connection-test timeout contract; isolated from daily data."""
import os
from pathlib import Path
import subprocess
import sys
import textwrap


def test_model_connection_test_has_bounded_wait_and_preserves_errors(tmp_path):
    environment = dict(os.environ, RA_DATA_DIR=str(tmp_path / 'module-data'),
                       RA_ACCESS_TOKEN='off', PYTHONUTF8='1')
    prelude = '''
import os
from pathlib import Path
import app.config
app.config.ROOT = Path(os.environ['RA_DATA_DIR'])
'''
    program = r'''
import asyncio
import time
from pathlib import Path
from fastapi.testclient import TestClient
import app.main as main
from app.core import Problem

main.MODEL_TEST_TIMEOUT_SECONDS = 0.08

class FakeProvider:
    mode = 'ok'
    cancelled = False
    async def request(self, config, messages):
        if self.mode == 'timeout':
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                self.cancelled = True
                raise
        if self.mode == 'error':
            raise Problem('AUTH_FAILED', '合成认证失败')
        return {'ok': True}, {'elapsed_seconds': 0.001}

provider = FakeProvider()
app = main.create_app(Path(__import__('os').environ['RA_DATA_DIR']) / 'case',
                      access_token='test-token', provider=provider,
                      env_path=Path(__import__('os').environ['RA_DATA_DIR']) / 'absent.env')
client = TestClient(app)
login = client.post('/api/session/login', json={'key': 'test-token'})
client.headers.update({'Origin': 'http://testserver',
                       'X-CSRF-Token': login.json()['csrf']})

# Normal result retains the established response contract.
response = client.post('/api/models/model/test')
assert response.status_code == 200, response.text
assert response.json()['connection'] == 'succeeded'

# A timeout is bounded by the fixed short cap even when model config permits more.
settings = dict(main.DEFAULT, timeout=10)
app.state.store.setting('model', settings)
provider.mode = 'timeout'
started = time.monotonic()
response = client.post('/api/models/model/test')
elapsed = time.monotonic() - started
assert response.status_code == 400, response.text
assert response.json()['code'] == 'TIMEOUT', response.text
assert response.json()['message'] == '连接测试等待超时；可重试；不会改变项目资料', response.text
assert elapsed < 1
assert provider.cancelled, 'wait_for did not cancel the fake provider request'
assert 'key' not in response.text.lower()

# A shorter configured request timeout remains the effective bound.
settings['timeout'] = 0.01
app.state.store.setting('model', settings)
provider.cancelled = False
started = time.monotonic()
response = client.post('/api/models/model/test')
elapsed = time.monotonic() - started
assert response.json()['code'] == 'TIMEOUT'
assert elapsed < 0.07
assert provider.cancelled

# Existing provider Problems keep their code and message.
provider.mode = 'error'
response = client.post('/api/models/model/test')
assert response.status_code == 400
assert response.json() == {'code': 'AUTH_FAILED', 'message': '合成认证失败'}
'''
    result = subprocess.run(
        [sys.executable, '-c', textwrap.dedent(prelude) + textwrap.dedent(program)],
        cwd=Path(__file__).resolve().parents[1], env=environment,
        capture_output=True, text=True, encoding='utf-8', timeout=20)
    assert result.returncode == 0, result.stderr
