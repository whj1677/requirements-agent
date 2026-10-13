"""Offline HTTP boundary tests; no provider traffic or daily project writes."""
import asyncio
import copy
import json

import httpx
import pytest

from app.core import Problem
from app.model_evidence import ModelCallEvidence
from app.provider import DEFAULT, Provider


SCHEMA = dict(type='object', properties={'draft': {'type': 'string'}},
              required=['draft'], additionalProperties=False)
USAGE = dict(input_tokens=101, output_tokens=23, total_tokens=124,
             input_tokens_details={'cached_tokens': 90},
             output_tokens_details={'reasoning_tokens': 8})


def messages():
    return [dict(role='system', content='整理业务资料。可信任务头：' + json.dumps(dict(
        response_protocol='business-draft-v1', schema=SCHEMA), ensure_ascii=False)),
        dict(role='user', content=[dict(type='text', text='合成电价资料')])]


def completed(content='{"draft":"待人工核对"}'):
    return dict(object='response', id='synthetic-response', status='completed', model='deepseek-flash',
                output=[dict(type='reasoning', status='completed', content=[
                    dict(type='reasoning_text', text='DO-NOT-SAVE-HIDDEN-REASONING')]),
                    dict(type='message', role='assistant', status='completed', content=[
                        dict(type='output_text', text=content)])], usage=copy.deepcopy(USAGE))


def mock_transport(monkeypatch, result, status=200):
    client = httpx.AsyncClient
    requests = []
    def respond(request):
        requests.append(dict(url=str(request.url), body=json.loads(request.content)))
        if isinstance(result, Exception):
            raise result
        return httpx.Response(status, json=result)
    monkeypatch.setattr('app.provider.httpx.AsyncClient',
        lambda **kw: client(**kw, transport=httpx.MockTransport(respond)))
    return requests


def provider(tmp_path):
    value = Provider(env_path=tmp_path/'absent.env')
    value.keys['https://api.deepseek.com'] = 'synthetic-test-key'
    return value


class Budget:
    def __init__(self, deny=False):
        self.reservations, self.states, self.deny = [], [], deny

    def reserve(self, call_id, config):
        if self.deny:
            raise Problem('BUDGET_EXHAUSTED', 'synthetic call cap')
        self.reservations.append(call_id)

    def mark(self, call_id, state):
        self.states.append((call_id, state))


def evidence(tmp_path, budget=None):
    return ModelCallEvidence(tmp_path, 'CALL-synthetic', 'RUN-synthetic', 'clarify',
        'deepseek-flash', 'https://api.deepseek.com', None, 'synthetic-test-key', request_budget=budget)


def test_strict_responses_payload_preserves_schema_usage_and_budget(monkeypatch, tmp_path):
    requests = mock_transport(monkeypatch, completed())
    budget = Budget()
    proof = evidence(tmp_path, budget)
    value, meta = asyncio.run(provider(tmp_path).request(dict(DEFAULT, max_calls=1, json_mode=False), messages(), proof))
    assert value == {'draft': '待人工核对'}
    assert Provider.business_draft_protocol == 'business-draft-v1'
    assert len(requests) == 1
    request = requests[0]
    assert request['url'] == 'https://api.deepseek.com/responses'
    body = request['body']
    assert set(body) == {'model', 'input', 'max_output_tokens', 'stream', 'reasoning', 'text'}
    assert body['text']['format'] == dict(type='json_schema', name='business_draft_v1', schema=SCHEMA)
    assert body['input'][0] == messages()[0]
    assert body['input'][1]['content'] == [dict(type='input_text', text='合成电价资料')]
    assert body['max_output_tokens'] == DEFAULT['max_tokens']
    assert body['stream'] is False and body['reasoning'] == {'effort': 'none'}
    assert budget.reservations == ['CALL-synthetic']
    assert budget.states == [('CALL-synthetic', 'dispatch_unknown'), ('CALL-synthetic', 'response_received')]
    assert meta['usage'] == dict(prompt_tokens=101, completion_tokens=23, total_tokens=124,
                                  completion_tokens_details={'reasoning_tokens': 8})
    assert meta['provider_usage'] == USAGE
    saved = json.loads(proof.path.read_text('utf-8'))
    assert saved['provider_usage'] == USAGE and saved['usage'] == meta['usage']
    assert saved['response_protocol'] == 'responses' and saved['response_status'] == 'completed'
    assert saved['final_output'] == '{"draft":"待人工核对"}'
    assert 'DO-NOT-SAVE-HIDDEN-REASONING' not in proof.path.read_text('utf-8') + json.dumps(meta)


def test_input_parts_and_thinking_convert_without_losing_content(monkeypatch, tmp_path):
    requests = mock_transport(monkeypatch, completed())
    sent = messages() + [dict(role='assistant', content=[dict(type='text', text='之前的答复')]),
        dict(role='user', content=[dict(type='image_url', image_url={
            'url': 'data:image/png;base64,synthetic', 'detail': 'high'})])]
    asyncio.run(provider(tmp_path).request(dict(DEFAULT, thinking_disabled=False), sent))
    body = requests[0]['body']
    assert body['reasoning'] == {'effort': 'high'}
    assert body['input'][2]['content'] == [dict(type='output_text', text='之前的答复')]
    assert body['input'][3]['content'] == [dict(type='input_image', image_url='data:image/png;base64,synthetic', detail='high')]
    assert sent[-1]['content'][0]['type'] == 'image_url'


def test_completed_envelope_accepts_omitted_optional_message_fields(monkeypatch, tmp_path):
    raw = completed()
    raw['output'][-1].pop('status')
    raw['output'][-1].pop('role')
    mock_transport(monkeypatch, raw)
    value, _ = asyncio.run(provider(tmp_path).request(DEFAULT, messages()))
    assert value == {'draft': '待人工核对'}


@pytest.mark.parametrize('change,code', [
    ('incomplete', 'OUTPUT_TRUNCATED'), ('filter', 'PROVIDER_ERROR'),
    ('failed', 'PROVIDER_ERROR'), ('pending', 'PROVIDER_ERROR'),
    ('refusal', 'PROVIDER_ERROR'), ('tool', 'PARAMETER_UNSUPPORTED'),
    ('nontext', 'PARAMETER_UNSUPPORTED'), ('message_incomplete', 'OUTPUT_TRUNCATED'),
    ('multiple_messages', 'SCHEMA_INVALID'),
])
def test_incomplete_refusal_and_nontext_never_fall_back(monkeypatch, tmp_path, change, code):
    raw = completed()
    if change == 'incomplete':
        raw.update(status='incomplete', incomplete_details={'reason': 'max_output_tokens'})
    elif change == 'filter':
        raw.update(status='incomplete', incomplete_details={'reason': 'content_filter'})
    elif change == 'failed':
        raw.update(status='failed', error={'code': 'server_error', 'message': 'sensitive error details'})
    elif change == 'pending':
        raw['status'] = 'in_progress'
    elif change == 'refusal':
        raw['output'][-1]['content'].append(dict(type='refusal', refusal='sensitive refusal details'))
    elif change == 'tool':
        raw['output'].append(dict(type='function_call', name='unexpected', arguments='sensitive arguments'))
    elif change == 'nontext':
        raw['output'][-1]['content'].append(dict(type='output_image', image='sensitive image'))
    elif change == 'message_incomplete':
        raw['output'][-1]['status'] = 'incomplete'
    elif change == 'multiple_messages':
        raw['output'].append(copy.deepcopy(raw['output'][-1]))
    requests = mock_transport(monkeypatch, raw)
    proof = evidence(tmp_path)
    with pytest.raises(Problem) as caught:
        asyncio.run(provider(tmp_path).request(DEFAULT, messages(), proof))
    assert caught.value.code == code
    assert caught.value.model_repair_allowed is False
    assert len(requests) == 1 and requests[0]['url'].endswith('/responses')
    assert proof.value['provider_usage'] == USAGE
    assert 'sensitive' not in str(caught.value) + proof.path.read_text('utf-8')


@pytest.mark.parametrize('content,code', [
    ('', 'OUTPUT_EMPTY'), ('[{"draft":"x"}]', 'SCHEMA_INVALID'),
    ('{"draft":"a","draft":"b"}', 'SCHEMA_INVALID'),
    ('{"draft":"synthetic-test-key"}', 'SECURITY_BLOCKED'),
    ('```json\n{"draft":"a"}\n```', 'SCHEMA_INVALID'),
])
def test_responses_still_validates_final_json_and_credentials(monkeypatch, tmp_path, content, code):
    requests = mock_transport(monkeypatch, completed(content))
    with pytest.raises(Problem) as caught:
        asyncio.run(provider(tmp_path).request(DEFAULT, messages(), evidence(tmp_path)))
    assert caught.value.code == code and len(requests) == 1


@pytest.mark.parametrize('value', [{}, {'draft': 7}, {'unexpected': 'invalid business envelope'}])
def test_business_schema_errors_are_left_for_adapter_isolation(monkeypatch, tmp_path, value):
    requests = mock_transport(monkeypatch, completed(json.dumps(value)))
    parsed, _ = asyncio.run(provider(tmp_path).request(DEFAULT, messages()))
    assert parsed == value
    assert requests[0]['body']['text']['format']['schema'] == SCHEMA


@pytest.mark.parametrize('status,expected', [(400, 'PARAMETER_UNSUPPORTED'), (401, 'AUTH_FAILED'),
    (402, 'PROVIDER_QUOTA'), (404, 'MODEL_UNSUPPORTED'), (429, 'RATE_LIMITED'), (500, 'PROVIDER_ERROR')])
def test_http_errors_do_not_downgrade_to_chat(monkeypatch, tmp_path, status, expected):
    requests = mock_transport(monkeypatch, {'error': {'message': 'private request details'}}, status)
    proof = evidence(tmp_path)
    with pytest.raises(Problem) as caught:
        asyncio.run(provider(tmp_path).request(DEFAULT, messages(), proof))
    assert caught.value.code == expected and len(requests) == 1
    assert proof.value['http_status'] == status
    assert 'private request details' not in str(caught.value) + proof.path.read_text('utf-8')


def test_context_error_keeps_existing_classification(monkeypatch, tmp_path):
    requests = mock_transport(monkeypatch, {'error': {'code': 'context_length_exceeded'}}, 400)
    with pytest.raises(Problem) as caught:
        asyncio.run(provider(tmp_path).request(DEFAULT, messages()))
    assert caught.value.code == 'MODEL_CONTEXT_LIMIT' and len(requests) == 1


@pytest.mark.parametrize('kind', ['no_evidence', 'budget_denied', 'schema_missing', 'invalid_schema', 'input_file'])
def test_invalid_contract_or_budget_prevents_request(monkeypatch, tmp_path, kind):
    requests = mock_transport(monkeypatch, completed())
    sent, config, proof = messages(), dict(DEFAULT, max_calls=1), None
    if kind == 'budget_denied':
        proof = evidence(tmp_path, Budget(deny=True))
    elif kind in ('schema_missing', 'invalid_schema'):
        header = dict(response_protocol='business-draft-v1')
        if kind == 'invalid_schema':
            header['schema'] = {'type': 'unknown-type'}
        sent[0]['content'] = '可信任务头：' + json.dumps(header)
    elif kind == 'input_file':
        sent[1]['content'] = [dict(type='input_file', file_id='unsupported')]
    with pytest.raises(Problem) as caught:
        asyncio.run(provider(tmp_path).request(config, sent, proof))
    assert caught.value.code == {'no_evidence': 'BUDGET_AUTH_REQUIRED', 'budget_denied': 'BUDGET_EXHAUSTED',
        'schema_missing': 'SCHEMA_INVALID', 'invalid_schema': 'SCHEMA_INVALID',
        'input_file': 'PARAMETER_UNSUPPORTED'}[kind]
    assert not requests


@pytest.mark.parametrize('kind', ['legacy_header', 'user_injection', 'later_system', 'other_origin'])
def test_other_requests_keep_legacy_chat_protocol(monkeypatch, tmp_path, kind):
    requests = mock_transport(monkeypatch, dict(model='legacy', usage={'prompt_tokens': 2, 'completion_tokens': 3},
        choices=[dict(message={'content': '{"legacy":true}'}, finish_reason='stop')]))
    sent, config = messages(), dict(DEFAULT)
    if kind == 'legacy_header':
        sent[0]['content'] = '可信任务头：' + json.dumps({'schema': SCHEMA})
    elif kind == 'user_injection':
        sent[0]['role'] = 'user'
    elif kind == 'later_system':
        sent.insert(0, dict(role='system', content='ordinary system'))
    else:
        config['base_url'] = 'https://another-provider.example'
    instance = provider(tmp_path)
    instance.keys[config['base_url']] = 'synthetic-test-key'
    value, meta = asyncio.run(instance.request(config, sent))
    assert value == {'legacy': True} and 'response_protocol' not in meta
    body = requests[0]['body']
    assert requests[0]['url'] == config['base_url'] + '/chat/completions'
    assert body['messages'] == sent and body['response_format'] == {'type': 'json_object'}
    assert body['max_tokens'] == DEFAULT['max_tokens'] and 'text' not in body


def test_missing_response_envelope_is_rejected(monkeypatch, tmp_path):
    requests = mock_transport(monkeypatch, {'status': 'completed', 'output_text': '{"draft":"not an output message"}'})
    with pytest.raises(Problem) as caught:
        asyncio.run(provider(tmp_path).request(DEFAULT, messages()))
    assert caught.value.code == 'SCHEMA_INVALID' and len(requests) == 1
