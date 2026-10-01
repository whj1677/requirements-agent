"""Public action and human-input boundaries, with a labelled loopback model."""
import copy
import json
import time
import uuid

import pytest

from app.product_flow import SCOPE
from tests.test_ui02_actions import case, plan, start, finished


def chat(model, summary='可以先说说现在最麻烦的那件事吗？', ready=False):
    def transform(value, body, context):
        if value.get('stage')=='interview':
            value['summary']=summary
            value['result']={'can_organize':ready}
        return value
    model['transform']=transform


def test_public_chat_saves_input_before_reply_and_never_approves(case):
    c,app,model,root=case;chat(model,ready=True);model['delay']=.12
    body,preview=plan(c,root,'interview','每天整理资料很烦，我也说不清要怎么改。')
    assert preview['stages']==['interview'] and not preview['missing']
    key=uuid.uuid4().hex
    task=start(c,root,body,preview,key=key).json()
    saved=c.get(root).json()
    assert saved['messages'][0]['text']==body['message']
    assert saved['messages'][0]['user_task_id']==task['id']
    assert saved['sources'][0]['origin']=='interview'
    assert start(c,root,body,preview,key=key).json()['id']==task['id']
    assert finished(c,root)['status']=='succeeded'
    p=c.get(root).json()
    assert len(model['requests'])==1 and len(p['messages'])==2
    assert p['items']==p['questions']==p['baselines']==[] and p['documents']=={}
    assert not p.get('product_context') and p['interview_state']['can_organize']
    assert not p['product_flow'][1]['complete'] and p['flow_current_title']==saved['flow_current_title']


@pytest.mark.parametrize('failure',['html','html-fragment','normative','bad-ref','non-json','provider'])
def test_failed_chat_keeps_literal_input_and_rejects_bad_reply(case,failure):
    c,app,model,root=case;chat(model)
    if failure=='provider':model['fail_next']=True
    elif failure=='non-json':model['invalid_next']=True
    else:
        original=model['transform']
        def bad(v,b,ctx):
            v=original(v,b,ctx)
            if failure=='html':v['summary']='<!doctype html><html>页面</html>'
            elif failure=='html-fragment':v['summary']='<div class="card"><button>保存</button></div>'
            elif failure=='normative':v['proposals']=[{'statement':'系统必须自动发送'}]
            else:v['used_source_refs']=[{'source_id':'imaginary','excerpt_id':'fake'}]
            return v
        model['transform']=bad
    body,preview=plan(c,root,'interview','我想照着图做一个HTML，但业务还没想好。')
    assert start(c,root,body,preview).status_code==200
    task=finished(c,root);p=c.get(root).json()
    assert task['status']=='failed' and task['calls']==1
    assert len(p['messages'])==1 and p['messages'][0]['text']==body['message']
    assert p['items']==[] and p['documents']=={}
    assert list((app.state.store.folder/'evidence/model-calls').glob('*.json'))


def test_chat_cancel_does_not_lose_original_or_store_late_reply(case):
    c,app,model,root=case;chat(model);model['delay']=.25
    body,preview=plan(c,root,'interview','不知道有哪些权限，先不要替我决定。')
    task=start(c,root,body,preview).json();time.sleep(.05)
    assert c.post(root+'/actions/'+task['id']+'/cancel').status_code==200
    time.sleep(.35)
    p=c.get(root).json()
    assert p['messages'][0]['text']==body['message'] and len(p['messages'])==1
    assert p['questions']==[] and p['items']==[]


def test_chat_needs_authorization_and_changed_receiver_cannot_send(case):
    c,app,model,root=case;chat(model)
    body,preview=plan(c,root,'interview','先帮我聊聊。')
    assert start(c,root,body,preview,authorize=False).status_code==403
    config=dict(model['config'],model='changed-receiver')
    assert c.put('/api/models/model',json=config).status_code==200
    response=start(c,root,body,preview)
    assert response.status_code==409 and response.json()['code']=='STALE_PLAN'
    assert not model['requests'] and not c.get(root).json()['messages']


def test_legacy_run_does_not_bypass_chat_input_capture(case):
    c,app,model,root=case;p=c.get(root).json()
    r=c.post(root+'/runs',json={'expected_revision':p['revision'],'stage':'interview','message':'旧入口未授权保存','document_type':'prd'})
    assert r.status_code==400 and r.json()['code']=='STAGE_INVALID'
    assert not c.get(root).json()['messages'] and not model['requests']


def test_multi_turn_then_organize_keeps_sources_and_confirmed_context(case):
    c,app,model,root=case;chat(model,'当前只梳理需求，暂不产出HTML。这个页面给谁使用？')
    texts=['照着截图想做HTML。','给运营使用；客户资料重复整理很烦，导出保持不变。']
    for text in texts:
        body,preview=plan(c,root,'interview',text);assert start(c,root,body,preview).status_code==200
        assert finished(c,root)['status']=='succeeded'
    p=c.get(root).json();sources=copy.deepcopy(p['sources'])
    assert c.post(root+'/product-context',json={'expected_revision':p['revision'],'values':{'preserve_scope':'已保存：导出保持原状'}}).status_code==200
    body,preview=plan(c,root,'organize','');assert start(c,root,body,preview).status_code==200
    assert finished(c,root)['status']=='succeeded'
    p=c.get(root).json()
    assert p['product_context']['preserve_scope']=='已保存：导出保持原状'
    assert p['product_context_proposal']['preserve_scope']!='已保存：导出保持原状'
    assert all(i['selection_status']=='candidate' for i in p['items'])
    assert p['sources']==sources
    ctx=json.loads(model['requests'][-1]['messages'][1]['content'][0]['text'])
    assert all(any(t==e['text'] for e in ctx['excerpts']) for t in texts)


def test_expert_input_is_literal_source_not_scope_approval(case):
    c,app,model,root=case
    values={'intent':'在客户列表增加备注','preserve_scope':'原有导出保持不变'}
    p=c.get(root).json()
    response=c.post(root+'/intake/direct',json={'expected_revision':p['revision'],'values':values})
    assert response.status_code==200,response.text
    p=c.get(root).json()
    assert p['product_context']==values and p['sources'][0]['origin']=='direct_intake'
    literal='\n'.join(e['text'] for e in p['sources'][0]['excerpts'])
    assert all(v in literal for v in values.values())
    assert p['product_flow'][0]['complete'] and not p['product_flow'][1]['complete']
    assert not model['requests'] and p['items']==[]
    check=c.post(root+'/stage-checks/2',json={'expected_revision':p['revision'],'expected_hash':p['product_flow'][1]['content_hash']})
    assert check.status_code==409 and check.json()['code']=='STAGE_BLOCKED'


@pytest.mark.parametrize('values,scope_ids,code',[({'intent':'  '},None,'INPUT_REQUIRED'),({'unknown':'规则'},None,'CONTEXT_INVALID'),({'intent':'规则'},['REQ-fake'],'REFERENCE_INVALID')])
def test_expert_invalid_input_is_not_a_shortcut(case,values,scope_ids,code):
    c,app,model,root=case;p=c.get(root).json()
    response=c.post(root+'/intake/direct',json={'expected_revision':p['revision'],'values':values,'scope_ids':scope_ids})
    assert response.json()['code']==code
    assert c.get(root).json()['sources']==[] and not model['requests']
