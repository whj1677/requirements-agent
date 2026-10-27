"""No-network recovery and typed duplicate checks, with isolated saved evidence."""
import asyncio
import copy
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from app.core import Problem, digest, execution_hash, now
from app.ingest_recovery import recover
from app.model_evidence import ModelCallEvidence
from app.model_json import identical_members
from app.provider import DEFAULT, Provider, assemble
from app import ingest
from tests.test_runtime import client, empty_ingest


@pytest.mark.parametrize('raw', [
    '{"a":1,"a":2}', '{"a":true,"a":1}', '{"a":1,"a":1.0}',
    '{"a":0.12345678901234567890,"a":0.12345678901234567891}',
    '{"a":[1,2],"a":[2,1]}', '{"a":null,"a":"null"}',
    '{"a":{"k":1,"k":2},"a":{"k":2}}',
    '{"a":1,"a":1,"b":{"x":true,"x":false}}',
    '{"a":NaN,"a":NaN}', '{"a":1e999,"a":1e999}',
    '{"a":1,"a":1}{}', '{"a":1,"a":1', '{"a":1}',
])
def test_ambiguous_or_incomplete_json_is_not_normalized(raw):
    assert identical_members(raw) is None


def test_nested_identical_members_preserve_types_values_and_order():
    raw='{"a":[{"source_refs":[{"id":"X"}],"source_refs":[{"id":"X"}]}],"n":false,"n":false}'
    value, paths=identical_members(raw)
    assert value=={'a':[{'source_refs':[{'id':'X'}]}],'n':False}
    assert paths==[['a',0,'source_refs'],['n']]


@pytest.mark.parametrize('stage,conflict', [(s,c) for s in ('ingest','review','clarify','change','interview','prd') for c in (False,True)])
def test_provider_only_normalizes_allowed_complete_identical_responses_and_keeps_raw(tmp_path,monkeypatch,stage,conflict):
    raw='{"stage":"'+stage+'","summary":"原句","summary":"'+('不同' if conflict else '原句')+'"}'
    original=httpx.AsyncClient
    transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'choices':[{'message':{'content':raw},'finish_reason':'stop'}]}))
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:original(transport=transport,**kw))
    provider=Provider(tmp_path/'absent.env');provider.keys['https://api.deepseek.com']='test-key'
    proof=ModelCallEvidence(tmp_path,'CALL-test','RUN-test',stage,'synthetic','https://api.deepseek.com',None,'test-key')
    if not conflict:
        result,meta=asyncio.run(provider.request(DEFAULT,[],evidence=proof))
        assert result['summary']=='原句'
        assert meta['syntax_normalization']['paths']==[['summary']]
        assert 'json_integrity_failure' not in proof.value
    else:
        with pytest.raises(Problem):asyncio.run(provider.request(DEFAULT,[],evidence=proof))
        assert proof.value['json_integrity_failure']=='duplicate_member'
    assert json.loads(proof.path.read_text('utf-8'))['final_output']==raw


def seed_failed_draft(app):
    """A historical failed response: no provider request is made by this fixture."""
    store=app.state.store;p=store.create('历史整理恢复 · 合成验证')
    from app.sources import save_source
    from app.interview import record_input
    text='管理员维护联系人列表。只读角色不能修改。'
    source=save_source(store,'对话原话.txt',text.encode(),purpose='goal')
    with store.edit(p['id'],p['revision'],'合成历史对话') as (p,db):
        p['sources'].append(source)
        record_input(p,text,'TASK-chat',source['id'])
    p=store.get(p['id']);messages,excerpts,omitted=assemble(p,'ingest','',DEFAULT,store.folder)
    ref=[dict(source_id=excerpts[0]['source_id'],excerpt_id=excerpts[0]['id'])]
    value=empty_ingest()
    value['proposals']=[dict(temp_id='TMP-R',action='add',target_item_id=None,kind='requirement',
        title='联系人维护',statement='管理员维护联系人列表。',applies_to='to_be',epistemic_status='reported',source_refs=ref,related_refs=[])]
    value['questions']=[dict(temp_id='QTMP-parent',topic_key='boundary',question='如何处理边界？',
        why='需要明确两项边界',options=[],blocking=True,blocking_stage=3,related_refs=['TMP-R'],source_refs=ref,
        decision_points=[dict(temp_id='QTMP-'+str(i),topic_key='boundary-'+str(i),question=q,
            why='边界未定',options=[],blocking=False,blocking_stage=4,related_refs=['TMP-R'],source_refs=ref)
            for i,q in enumerate(['重复姓名如何处理？','空姓名如何处理？'])])]
    raw=json.dumps(value,ensure_ascii=False)
    literal='"source_refs": '+json.dumps(ref,ensure_ascii=False)
    raw=raw.replace(literal,literal+', '+literal,1)
    proof=ModelCallEvidence(store.folder,'CALL-history','RUN-history','ingest','synthetic','http://127.0.0.1',None,'')
    proof.request_context(DEFAULT,messages,{},{});proof.response(raw,http_status=200,finish_reason='stop')
    proof.value['json_integrity_failure']='duplicate_member';proof.finish('SCHEMA_INVALID')
    run=dict(stage='ingest',status='failed',error='SEMANTIC_BLOCKED',revision=p['revision'],message='',
        document_type='prd',calls=1,attempts=[dict(call_id='CALL-history',actual_input_hash=digest(messages),evidence_limitations=[])],
        input_state_hash=execution_hash(p),sent_excerpt_ids=[e['id'] for e in excerpts],omitted_excerpt_ids=omitted,
        user_task_id='TASK-history',result_applied=False)
    task=dict(action='organize',label='整理当前信息',status='failed',error='SEMANTIC_BLOCKED',calls=1,
        message='模型 JSON 有重复键，无法证明格式修复保持原义；原始证据保留，未追加修复请求',
        input_revision=p['revision'],run_ids=['RUN-history'],stages=['ingest'],completed_steps=0,
        step_states=[dict(stage='ingest',status='failed')],source_ids=[source['id']],max_calls=None,cost=None,
        created=now(),completed=now(),retry_input=dict(action='organize',message='',document_type='prd'))
    store.record(p['id'],'run',run,'RUN-history');store.record(p['id'],'user_task',task,'TASK-history')
    return p,proof,value


def test_new_question_gates_inherit_without_rewriting_business_or_old_questions(tmp_path):
    c,app=client(tmp_path);p,proof,value=seed_failed_draft(app)
    before=copy.deepcopy(value);derived,changes=ingest.inherit_new_question_gates(value,p)
    assert value==before and len(changes)==2
    for child,original in zip(derived['questions'][0]['decision_points'],value['questions'][0]['decision_points']):
        assert child==dict(original,blocking=True,blocking_stage=3)
    value['questions'][0]['target_question_id']='Q-old'
    assert ingest.inherit_new_question_gates(value,p)==(value,[])


def test_recovery_is_atomic_idempotent_candidate_only_and_preserves_original_evidence(tmp_path,monkeypatch):
    c,app=client(tmp_path);p,proof,value=seed_failed_draft(app);root='/api/projects/'+p['id']
    async def forbidden(*args,**kwargs):raise AssertionError('recovery must never call a model')
    monkeypatch.setattr(app.state.provider,'request',forbidden)
    raw=proof.path.read_bytes();run=app.state.store.get_record(p['id'],'RUN-history');task=app.state.store.get_record(p['id'],'TASK-history')
    response=c.post(root+'/actions/TASK-history/recover-draft',json={'expected_revision':p['revision']})
    assert response.status_code==200,response.text
    receipt=response.json();after=c.get(root).json()
    assert after['revision']==p['revision']+1
    assert len(after['items'])==1 and after['items'][0]['selection_status']=='candidate'
    assert after['items'][0]['statement']==value['proposals'][0]['statement']
    assert all(q['blocking'] and q['blocking_stage']==3 for q in after['questions'])
    assert after['active_baseline_id'] is None and not after['product_flow'][1]['complete']
    assert after['messages'][:len(p['messages'])]==p['messages']
    assert proof.path.read_bytes()==raw
    assert app.state.store.get_record(p['id'],'RUN-history')==run
    assert app.state.store.get_record(p['id'],'TASK-history')==task
    assert receipt['evidence_sha256']==hashlib.sha256(raw).hexdigest() and receipt['external_model_calls']==0
    again=c.post(root+'/actions/TASK-history/recover-draft',json={'expected_revision':p['revision']})
    assert again.status_code==200 and again.json()['id']==receipt['id']
    assert c.get(root).json()['revision']==after['revision']
    projection=c.get(root+'/actions').json()[-1]
    assert projection['status']=='failed' and projection['calls']==1 and projection['local_recovery']['id']==receipt['id']


@pytest.mark.parametrize('mutation', ['stale','active','conflict','truncated','redacted','source','request_hash','wrong_run','semantic','benchmark','cancelled'])
def test_recovery_rejects_unsafe_inputs_without_writes(tmp_path,mutation):
    c,app=client(tmp_path);p,proof,_=seed_failed_draft(app);store=app.state.store
    if mutation=='stale':
        with store.edit(p['id'],p['revision'],'新的聊天') as (current,db):current['messages'].append(dict(role='user',text='新的业务内容'))
    elif mutation=='active':store.record(p['id'],'user_task',dict(status='running'),'TASK-active')
    elif mutation in ('benchmark','cancelled'):
        store.update_record(p['id'],'TASK-history','user_task',**({'benchmark':{'target':'other'}} if mutation=='benchmark' else {'status':'cancelled'}))
    else:
        e=json.loads(proof.path.read_text('utf-8'))
        if mutation=='conflict':e['final_output']=e['final_output'].replace('"source_refs": [','"source_refs": [], "source_refs": [',1)
        elif mutation in ('truncated','redacted'):e['evidence_limitations']=['final_output_'+mutation]
        elif mutation=='source':
            request=json.loads(e['request_input']);ctx=json.loads(request[1]['content'][0]['text']);ctx['excerpts'][0]['text']='不一致的来源'
            request[1]['content'][0]['text']=json.dumps(ctx,ensure_ascii=False);e['request_input']=json.dumps(request,ensure_ascii=False);e['actual_input_hash']=digest(request)
            store.update_run(p['id'],'RUN-history',attempts=[dict(call_id='CALL-history',actual_input_hash=digest(request))])
        elif mutation=='request_hash':e['actual_input_hash']='wrong'
        elif mutation=='wrong_run':e['run_id']='RUN-other'
        elif mutation=='semantic':e['final_output']=e['final_output'].replace('"TMP-R"','"TMP-missing"',1)
        proof.path.write_text(json.dumps(e,ensure_ascii=False),'utf-8')
    before=store.get(p['id']);revision=before['revision']
    with pytest.raises(Problem):recover(store,p['id'],'TASK-history',revision)
    assert store.get(p['id'])==before and not store.records(p['id'],'local_recovery')


def test_concurrent_recovery_cannot_apply_twice(tmp_path):
    c,app=client(tmp_path);p,_,_=seed_failed_draft(app);store=app.state.store
    def run():
        try:return recover(store,p['id'],'TASK-history',p['revision'])
        except Problem as error:return error.code
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:run(),range(2)))
    assert any(isinstance(row,dict) for row in results)
    assert len(store.get(p['id'])['items'])==1 and len(store.records(p['id'],'local_recovery'))==1
