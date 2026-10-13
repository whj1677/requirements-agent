"""Offline interview image routing with the UI02 loopback model only."""
import io
import json

import pytest
from PIL import Image

from tests.test_ui02_actions import case, finished, image, plan, start


def attachment(client, root):
    data=io.BytesIO()
    Image.new('RGB',(30,30),'white').save(data,format='PNG')
    response=client.post(root+'/sources/file',
        data={'expected_revision':client.get(root).json()['revision'],
              'purpose':'chat_attachment'},
        files={'file':('chat.png',data.getvalue(),'image/png')})
    assert response.status_code==200,response.text
    row=response.json()
    assert row['origin']=='interview_attachment' and row['parse_status']=='awaiting_vision'
    return row['id']


def interview_reply(value, _body, _context):
    if value['stage']=='interview':
        value.update(summary='合成访谈回复：请继续说明最重要的页面行为。',
                     result={'can_organize':False})
    return value


def context(request):
    return json.loads(request['messages'][1]['content'][0]['text'])


def stage(request):
    return json.loads(request['messages'][0]['content'].split('可信任务头：',1)[1])['stage']


def user_messages(project):
    return [m for m in project['messages']
            if m.get('role')=='user' and m.get('stage')=='interview']


def assistant_interviews(project):
    return [m for m in project['messages']
            if m.get('role')=='assistant' and m.get('stage')=='interview']


def test_four_chat_images_run_two_vision_batches_then_interview_once(case):
    client,_,model,root=case
    model['transform']=interview_reply
    ids=[attachment(client,root) for _ in range(4)]
    before=client.get(root).json()
    assert not any(s['id'] in ids and s.get('vision_run_id') for s in before['sources'])
    body,preview=plan(client,root,'interview','请根据页面图片梳理。',
                      attachment_source_ids=ids)
    assert preview['stages']==['vision','vision','interview']
    assert preview['pending_image_count']==4 and not model['requests']
    denied=start(client,root,body,preview,False)
    assert denied.status_code==403 and not model['requests']
    assert start(client,root,body,preview).status_code==200
    task=finished(client,root)
    assert task['status'] in ('succeeded','partial') and task['calls']==3
    assert [stage(request) for request in model['requests']]==[
        'vision','vision','interview']
    batches=[{e['source_id'] for e in context(request)['excerpts']} & set(ids)
             for request in model['requests'][:2]]
    assert sorted(len(group) for group in batches)==[1,3]
    assert batches[0].isdisjoint(batches[1]) and batches[0]|batches[1]==set(ids)
    chat=context(model['requests'][-1])
    assert chat['vision_observations']
    observed={ob['source_ref']['source_id']
              for result in chat['vision_observations']
              for ob in result['result']['observations']}
    assert observed<=set(ids) and observed
    project=client.get(root).json()
    assert {s['id'] for s in project['sources'] if s.get('vision_run_id')}>=set(ids)
    assert user_messages(project)[-1]['attachment_source_ids']==ids
    assert assistant_interviews(project)
    count=len(model['requests'])
    body,preview=plan(client,root,'interview','继续讨论同一组图片。',
                      attachment_source_ids=ids)
    assert preview['stages']==['interview'] and preview['pending_image_count']==0
    assert start(client,root,body,preview).status_code==200
    assert finished(client,root)['status'] in ('succeeded','partial')
    assert [stage(request) for request in model['requests'][count:]]==['interview']


def test_explicit_empty_selection_sends_only_this_turn_attachment(case):
    client,_,model,root=case
    model['transform']=interview_reply
    unbound=attachment(client,root)
    image(client,root)
    other_goal=next(s['id'] for s in client.get(root).json()['sources']
                    if s.get('image_mime') and s['id']!=unbound)
    selected=attachment(client,root)
    project=client.get(root).json()
    response=client.put(root+'/domain-context',json=dict(
        expected_revision=project['revision'],selected_source_ids=[]))
    assert response.status_code==200,response.text
    body,preview=plan(client,root,'interview','',attachment_source_ids=[selected])
    assert preview['stages']==['vision','interview']
    assert start(client,root,body,preview).status_code==200
    task=finished(client,root)
    assert task['status']=='succeeded' and task['calls']==2
    assert unbound not in task['source_ids'] and other_goal not in task['source_ids']
    assert [stage(request) for request in model['requests']]==['vision','interview']
    for request in model['requests']:
        sent={ex['source_id'] for ex in context(request)['excerpts']}
        assert selected in sent and unbound not in sent and other_goal not in sent
    project=client.get(root).json()
    assert user_messages(project)[-1]['text']=='请结合这些图片继续梳理需求。'
    assert user_messages(project)[-1]['attachment_source_ids']==[selected]
    assert all(s['parse_status']=='awaiting_vision' for s in project['sources']
               if s['id'] in (unbound,other_goal))
    assert not any(s.get('vision_run_id') for s in project['sources']
                   if s['id'] in (unbound,other_goal))


@pytest.mark.parametrize('invalid_kind',['excluded','wrong_project','non_image'])
def test_invalid_attachment_id_rejected_before_any_model_call(case,invalid_kind):
    client,_,model,root=case
    if invalid_kind=='wrong_project':
        other=client.post('/api/projects',json={'name':'另一个隔离项目'}).json()
        other_root='/api/projects/'+other['id']
        source_id=attachment(client,other_root)
    elif invalid_kind=='non_image':
        project=client.get(root).json()
        response=client.post(root+'/sources/text',json=dict(
            expected_revision=project['revision'],title='普通文字',
            text='只有文字，没有图片。',purpose='goal'))
        assert response.status_code==200,response.text
        source_id=response.json()['id']
    else:
        source_id=attachment(client,root)
        project=client.get(root).json()
        response=client.post(root+'/sources/'+source_id+'/exclude',json=dict(
            expected_revision=project['revision']))
        assert response.status_code==200,response.text
    project=client.get(root).json()
    response=client.post(root+'/actions/plan',json=dict(
        expected_revision=project['revision'],action='interview',
        message='请看这张图。',document_type='prd',
        attachment_source_ids=[source_id]))
    assert response.status_code==409 and response.json()['code']=='ATTACHMENT_INVALID'
    assert not model['requests']
    assert not user_messages(client.get(root).json())


@pytest.mark.parametrize('failure',['budget','vision'])
def test_interview_failure_keeps_input_and_attachment_without_fake_reply(case,failure):
    client,_,model,root=case
    model['transform']=interview_reply
    ids=[attachment(client,root) for _ in range(4 if failure=='budget' else 1)]
    body,preview=plan(client,root,'interview','请确认这批图片的业务目的。',
                      max_calls=1 if failure=='budget' else None,
                      attachment_source_ids=ids)
    if failure=='vision':model['fail_next']=True
    assert start(client,root,body,preview).status_code==200
    task=finished(client,root)
    assert task['status']==('paused_budget' if failure=='budget' else 'failed')
    assert [stage(request) for request in model['requests']]==['vision']
    project=client.get(root).json()
    assert user_messages(project)[-1]['text']=='请确认这批图片的业务目的。'
    assert user_messages(project)[-1]['attachment_source_ids']==ids
    assert not assistant_interviews(project)


def test_image_only_turn_saves_default_sentence(case):
    client,_,model,root=case
    model['transform']=interview_reply
    source_id=attachment(client,root)
    body,preview=plan(client,root,'interview','',attachment_source_ids=[source_id])
    assert start(client,root,body,preview).status_code==200
    assert finished(client,root)['status']=='succeeded'
    project=client.get(root).json()
    assert user_messages(project)[-1]['text']=='请结合这些图片继续梳理需求。'
    assert user_messages(project)[-1]['attachment_source_ids']==[source_id]
