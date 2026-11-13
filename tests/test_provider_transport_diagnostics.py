"""Offline transport faults preserve evidence without retries or partial acceptance."""
import asyncio
import json

import httpx
import pytest

from app.core import Problem
from app.provider import DEFAULT
from tests.test_deepseek_responses import Budget, completed, evidence, messages, provider


class FaultStream(httpx.AsyncByteStream):
    def __init__(self, fault=None, wait=False, complete=False):
        self.fault, self.wait, self.complete = fault, wait, complete
        self.closed = False

    async def __aiter__(self):
        yield b'\n\n'
        if self.wait:
            await asyncio.sleep(10)
        if self.fault:
            raise self.fault('SECRET-ERROR-BODY')
        yield json.dumps(completed() if self.complete else {'partial': True}).encode()

    async def aclose(self):
        self.closed = True


def install(monkeypatch, stream=None, connect_error=False):
    real_client = httpx.AsyncClient
    requests = []
    def respond(request):
        requests.append(json.loads(request.content))
        if connect_error:
            raise httpx.ConnectError('SECRET-ERROR-BODY')
        return httpx.Response(200, stream=stream)
    monkeypatch.setattr('app.provider.httpx.AsyncClient',
                        lambda **kw: real_client(**kw, transport=httpx.MockTransport(respond)))
    return requests


@pytest.mark.parametrize('fault,code', [(httpx.ReadTimeout, 'TIMEOUT'),
                                      (httpx.RemoteProtocolError, 'NETWORK_ERROR'),
                                      (None, 'SCHEMA_INVALID')])
def test_body_fault_keeps_header_and_byte_evidence_without_acceptance(tmp_path, monkeypatch, fault, code):
    stream = FaultStream(fault)
    requests = install(monkeypatch, stream)
    budget = Budget()
    proof = evidence(tmp_path, budget)
    with pytest.raises(Problem) as error:
        asyncio.run(provider(tmp_path).request(dict(DEFAULT, max_calls=1), messages(), proof))
    assert error.value.code == code
    if fault:
        assert '模型接收端已响应' in error.value.message
        assert '无法连接' not in error.value.message
    trace = proof.value['transport']
    assert trace['http_status'] == 200 and trace['received_bytes'] >= 2
    assert trace['headers_elapsed_seconds'] <= trace['first_byte_elapsed_seconds'] <= trace['last_byte_elapsed_seconds']
    assert trace['phase'] == ('reading_response_body' if fault else 'response_body_complete')
    if fault:
        assert trace['exception_type'] == fault.__name__
        assert proof.value['response_received'] is False
    assert proof.value['final_output'] is None
    assert len(requests) == len(budget.reservations) == 1
    assert requests[0]['stream'] is False and requests[0]['max_output_tokens'] == DEFAULT['max_tokens']
    assert 'SECRET-ERROR-BODY' not in proof.path.read_text('utf-8')
    assert stream.closed


def test_connect_error_stays_before_headers(tmp_path, monkeypatch):
    requests = install(monkeypatch, connect_error=True)
    proof = evidence(tmp_path)
    with pytest.raises(Problem) as error:
        asyncio.run(provider(tmp_path).request(DEFAULT, messages(), proof))
    assert error.value.code == 'NETWORK_ERROR'
    assert '未取得模型接收端响应' in error.value.message
    trace = proof.value['transport']
    assert trace['phase'] == 'awaiting_response_headers'
    assert trace['exception_type'] == 'ConnectError' and trace['received_bytes'] == 0
    assert 'http_status' not in trace and proof.value['response_received'] is False
    assert len(requests) == 1


def test_outer_deadline_keeps_partial_transport_but_no_response(tmp_path, monkeypatch):
    stream = FaultStream(wait=True)
    requests = install(monkeypatch, stream)
    proof = evidence(tmp_path)
    async def run():
        await asyncio.wait_for(provider(tmp_path).request(DEFAULT, messages(), proof), .03)
    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(run())
    trace = json.loads(proof.path.read_text('utf-8'))['transport']
    assert trace['cancelled'] and trace['exception_type'] == 'CancelledError'
    assert trace['phase'] == 'reading_response_body' and trace['received_bytes'] == 2
    assert proof.value['response_received'] is False and proof.value['final_output'] is None
    assert len(requests) == 1 and stream.closed


def test_complete_body_still_validates_and_does_not_save_reasoning(tmp_path, monkeypatch):
    stream = FaultStream(complete=True)
    requests = install(monkeypatch, stream)
    budget = Budget()
    proof = evidence(tmp_path, budget)
    result, meta = asyncio.run(provider(tmp_path).request(dict(DEFAULT, max_calls=1), messages(), proof))
    assert result == {'draft': '待人工核对'} and meta['finish_reason'] == 'stop'
    assert proof.value['transport']['phase'] == 'response_body_complete'
    assert proof.value['response_received'] and len(requests) == len(budget.reservations) == 1
    assert 'DO-NOT-SAVE-HIDDEN-REASONING' not in proof.path.read_text('utf-8')
    assert stream.closed
