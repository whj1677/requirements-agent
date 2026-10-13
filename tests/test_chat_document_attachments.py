"""Chat document attachments use the isolated UI02 model, never a real provider."""
import io

import pytest
from docx import Document

from app.domain_context import active_source_ids
from tests.test_interview_images import context, interview_reply, stage, user_messages
from tests.test_ui02_actions import case, finished, plan, start
from tests.test_understanding import word_with_picture


@pytest.fixture(autouse=True)
def isolated_data(tmp_path, monkeypatch):
    monkeypatch.setenv('RA_DATA_DIR', str(tmp_path / 'chat-document-only'))


def upload(client, root, name, content):
    response = client.post(root + '/sources/file',
        data={'expected_revision': client.get(root).json()['revision'],
              'purpose': 'chat_attachment'},
        files={'file': (name, content)})
    return response


def plain_docx():
    document = Document()
    document.add_paragraph('合成文档：联系人列表只记录选填备注；权限规则待确认。')
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


@pytest.mark.parametrize(('name', 'make_content', 'needle'), [
    ('notes.txt', lambda: '合成文字：联系人备注待确认。'.encode(), '联系人备注待确认'),
    ('notes.md', lambda: '# 合成资料\n\n联系人备注待确认。'.encode(), '联系人备注待确认'),
    ('notes.docx', plain_docx, '权限规则待确认'),
], ids=['txt', 'md', 'docx'])
def test_document_unbound_until_explicit_interview_and_no_vision(case, name, make_content, needle):
    client, app, model, root = case
    model['transform'] = interview_reply
    uploaded = upload(client, root, name, make_content())
    assert uploaded.status_code == 200, uploaded.text
    source = uploaded.json()
    sid = source['id']
    assert source['origin'] == 'interview_attachment'
    assert source['parse_status'] == 'read' and source['excerpts']
    assert sid not in active_source_ids(app.state.store.get(root.rsplit('/', 1)[-1]))
    assert sid not in client.get(root).json()['active_source_ids']
    assert not model['requests']

    body, preview = plan(client, root, 'interview', '', attachment_source_ids=[sid])
    assert preview['stages'] == ['interview'] and preview['pending_image_count'] == 0
    assert sid in {s['id'] for s in preview['sources']}
    assert not model['requests']  # Upload and a cancellable preview are local only.
    assert start(client, root, body, preview).status_code == 200
    task = finished(client, root)
    assert task['status'] == 'succeeded' and task['calls'] == 1
    assert [stage(request) for request in model['requests']] == ['interview']
    sent = context(model['requests'][0])
    assert any(ex['source_id'] == sid and needle in ex['text'] for ex in sent['excerpts'])
    assert sid in task['source_ids']
    message = user_messages(client.get(root).json())[-1]
    assert message['text'] == '请结合这些资料继续梳理需求。'
    assert message['attachment_source_ids'] == [sid]
    assert sid in client.get(root).json()['active_source_ids']


def test_docx_parent_and_embedded_image_are_unbound_until_explicit_send(case):
    client, app, model, root = case
    model['transform'] = interview_reply
    uploaded = upload(client, root, 'picture.docx', word_with_picture())
    assert uploaded.status_code == 200, uploaded.text
    parent = uploaded.json()
    project = client.get(root).json()
    children = [s for s in project['sources'] if s.get('container_source_id') == parent['id']]
    assert len(children) == 1 and parent['embedded_image_ids'] == [children[0]['id']]
    assert parent['origin'] == children[0]['origin'] == 'interview_attachment'
    assert children[0]['parse_status'] == 'awaiting_vision'
    assert parent['id'] not in project['active_source_ids'] and children[0]['id'] not in project['active_source_ids']
    assert not model['requests']

    body, preview = plan(client, root, 'interview', '请结合图文资料讨论。', attachment_source_ids=[parent['id']])
    assert preview['stages'] == ['vision', 'interview']
    assert {s['id'] for s in preview['sources']} >= {parent['id'], children[0]['id']}
    assert not model['requests']  # Closing the plan here would send nothing.
    assert start(client, root, body, preview).status_code == 200
    task = finished(client, root)
    assert task['status'] in ('succeeded', 'partial') and task['calls'] == 2
    assert [stage(request) for request in model['requests']] == ['vision', 'interview']
    assert children[0]['id'] in {ex['source_id'] for ex in context(model['requests'][0])['excerpts']}
    sent = context(model['requests'][1])
    assert any(ex['source_id'] == parent['id'] for ex in sent['excerpts'])
    assert sent['vision_observations']
    assert {parent['id'], children[0]['id']} <= set(task['source_ids'])
    assert user_messages(client.get(root).json())[-1]['attachment_source_ids'] == [parent['id']]
    assert {parent['id'], children[0]['id']} <= set(client.get(root).json()['active_source_ids'])


def test_excluded_embedded_image_is_not_sent_or_treated_as_read(case):
    client, _, model, root = case
    model['transform'] = interview_reply
    uploaded = upload(client, root, 'picture.docx', word_with_picture())
    assert uploaded.status_code == 200, uploaded.text
    parent = uploaded.json()
    child_id = parent['embedded_image_ids'][0]
    excluded = client.post(root + '/sources/' + child_id + '/exclude',
        json={'expected_revision': client.get(root).json()['revision']})
    assert excluded.status_code == 200, excluded.text
    body, preview = plan(client, root, 'interview', '只讨论文档可读正文。',
                         attachment_source_ids=[parent['id']])
    assert preview['stages'] == ['interview'] and preview['pending_image_count'] == 0
    assert child_id not in {s['id'] for s in preview['sources']}
    assert start(client, root, body, preview).status_code == 200
    task = finished(client, root)
    assert task['status'] in ('succeeded', 'partial'), (task['status'], task.get('error'), task['run_ids'])
    assert [stage(request) for request in model['requests']] == ['interview']
    assert child_id not in {ex['source_id'] for ex in context(model['requests'][0])['excerpts']}
    child = next(s for s in client.get(root).json()['sources'] if s['id'] == child_id)
    assert child['excluded'] and child['parse_status'] == 'awaiting_vision' and not child.get('vision_run_id')


def test_failed_document_remains_failed_and_unknown_extension_is_rejected(case):
    client, _, model, root = case
    model['transform'] = interview_reply
    failed = upload(client, root, 'broken.pdf', b'not a PDF document')
    assert failed.status_code == 200, failed.text
    source = failed.json()
    assert source['origin'] == 'interview_attachment'
    assert source['parse_status'] == 'failed' and not source['excerpts']
    assert source['failure_reason'] and source['id'] not in client.get(root).json()['active_source_ids']
    unknown = upload(client, root, 'unknown.bin', b'arbitrary bytes')
    assert unknown.status_code == 400 and unknown.json()['code'] == 'SOURCE_FAILED'
    assert not model['requests']
    body, preview = plan(client, root, 'interview', '这份 PDF 当前无法读取，请先讨论已知目标。',
                         attachment_source_ids=[source['id']])
    assert preview['stages'] == ['interview'] and source['id'] in {s['id'] for s in preview['sources']}
    assert start(client, root, body, preview).status_code == 200
    task = finished(client, root)
    assert task['status'] in ('partial', 'succeeded'), (task['status'], task.get('error'), task['run_ids'])
    sent = context(model['requests'][0])
    assert not any(ex['source_id'] == source['id'] for ex in sent['excerpts'])
    after = next(s for s in client.get(root).json()['sources'] if s['id'] == source['id'])
    assert after['parse_status'] == 'failed' and not after['excerpts']


@pytest.mark.parametrize('invalid_kind', ['wrong_project', 'excluded'])
def test_foreign_or_excluded_document_rejected_before_model_call(case, invalid_kind):
    client, _, model, root = case
    if invalid_kind == 'wrong_project':
        other = client.post('/api/projects', json={'name': '隔离的另一个项目'}).json()
        other_root = '/api/projects/' + other['id']
        source_id = upload(client, other_root, 'other.txt', b'isolated notes').json()['id']
    else:
        source_id = upload(client, root, 'excluded.md', b'# excluded notes').json()['id']
        response = client.post(root + '/sources/' + source_id + '/exclude',
            json={'expected_revision': client.get(root).json()['revision']})
        assert response.status_code == 200, response.text
    project = client.get(root).json()
    response = client.post(root + '/actions/plan', json=dict(
        expected_revision=project['revision'], action='interview',
        message='请看资料。', document_type='prd', attachment_source_ids=[source_id]))
    assert response.status_code == 409 and response.json()['code'] == 'ATTACHMENT_INVALID'
    assert not model['requests'] and not user_messages(client.get(root).json())
