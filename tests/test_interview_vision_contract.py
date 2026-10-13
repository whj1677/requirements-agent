"""Offline contract checks for vision preceding a chat interview."""

from app.product_flow import INTAKE, SCOPE
from tests.test_interview_images import attachment, context, interview_reply, stage
from tests.test_ui02_actions import case, finished, image, plan, start


def _head(request):
    import json
    return json.loads(request['messages'][0]['content'].split('可信任务头：', 1)[1])


def test_chat_vision_cannot_create_formal_context_or_questions(case):
    client, _, model, root = case
    source_id = attachment(client, root)

    def formal_output(value, body, ctx):
        if value['stage'] == 'vision':
            value['product_context_proposal'] = {
                key: '模型擅自生成的上下文' for key in {**INTAKE, **SCOPE}
            }
        return interview_reply(value, body, ctx)

    model['transform'] = formal_output
    before = client.get(root).json()
    body, preview = plan(client, root, 'interview', '这张图是什么页面？',
                         attachment_source_ids=[source_id])
    assert preview['stages'] == ['vision', 'interview']
    assert start(client, root, body, preview).status_code == 200
    task = finished(client, root)
    assert task['status'] == 'failed'
    assert [stage(request) for request in model['requests']] == ['vision']
    head = _head(model['requests'][0])
    assert head['vision_interview_policy']
    assert all(head['schema']['properties'][key] == {'const': []}
               for key in ('proposals', 'questions', 'findings'))
    assert 'product_context_proposal' not in head['schema']['properties']
    after = client.get(root).json()
    assert after['items'] == before['items'] == []
    assert after['questions'] == before['questions'] == []
    assert after.get('product_context') == before.get('product_context')
    assert not any(message.get('stage') == 'vision' for message in after['messages'])
    source = next(s for s in after['sources'] if s['id'] == source_id)
    assert source['parse_status'] == 'awaiting_vision'
    assert not source.get('vision_run_id')


def test_partial_observation_and_unobserved_image_reach_interview_truthfully(case):
    client, _, model, root = case
    observed_id, unread_id = attachment(client, root), attachment(client, root)

    def partial_vision(value, body, ctx):
        if value['stage'] == 'vision':
            assert value['result']['observations'][0]['source_ref']['source_id'] == observed_id
            value['result']['unreadable'] = ['第二张图片中央字迹无法辨认']
            value['result']['unobservable'] = ['静态图片不能证明保存后的行为']
        return interview_reply(value, body, ctx)

    model['transform'] = partial_vision
    body, preview = plan(client, root, 'interview', '请说明图中可见内容。',
                         attachment_source_ids=[observed_id, unread_id])
    assert start(client, root, body, preview).status_code == 200
    task = finished(client, root)
    assert task['status'] in ('succeeded', 'partial')
    assert [stage(request) for request in model['requests']] == ['vision', 'interview']
    chat = context(model['requests'][-1])
    assert len(chat['vision_observations']) == 1
    observations = chat['vision_observations'][0]['result']['observations']
    assert len(observations) == 1
    assert observations[0]['source_ref']['source_id'] == observed_id
    assert chat['vision_observations'][0]['result']['unreadable'] == [
        '第二张图片中央字迹无法辨认']
    statuses = {s['id']: s for s in chat['source_status'] if s['id'] in (observed_id, unread_id)}
    assert statuses[observed_id]['visual_observation_count'] == 1
    assert statuses[unread_id]['visual_observation_count'] == 0
    assert statuses[observed_id]['parse_status'] == 'partial'
    assert statuses[unread_id]['parse_status'] == 'partial'
    assert '已保留此图可见内容' in statuses[observed_id]['failure_reason']
    assert '未取得可靠视觉观察' in statuses[unread_id]['failure_reason']
    assert 'visual_observation_count' in model['requests'][-1]['messages'][0]['content']


def test_ordinary_vision_is_not_subject_to_chat_only_head_constraint(case):
    client, _, model, root = case
    image(client, root)
    source_id = next(s['id'] for s in client.get(root).json()['sources'] if s.get('image_mime'))
    body, preview = plan(client, root, 'organize', '根据图片整理待审核需求。')
    assert preview['stages'][0] == 'vision'
    assert start(client, root, body, preview).status_code == 200
    assert finished(client, root)['status'] in ('succeeded', 'partial')
    request = model['requests'][0]
    assert stage(request) == 'vision'
    head = _head(request)
    assert 'vision_interview_policy' not in head
    assert head['schema']['properties']['questions'] != {'const': []}
    assert 'product_context_proposal' in head['schema']['properties']
    assert any(s['id'] == source_id and s.get('vision_run_id')
               for s in client.get(root).json()['sources'])
