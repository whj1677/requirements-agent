"""Synthetic regressions for mechanisms observed in PRD-01, no private material."""
import asyncio
import copy
import io
import json
import pytest
from docx import Document
from app.core import Problem, dumps
from app.store import Store
from app.provider import DEFAULT, assemble, origin
from app.workflow import Workflow, apply_response
from app.contracts import gate, validate_response
from tests.helpers import prepared
from tests.test_runtime import FakeProvider
from tests.test_ui02_actions import case, plan as action_plan, start, finished


def plan(refs=None, discussion=None):
    return dict(plan_version='2', sections=[dict(
        section_key='function', context_refs=[], normative_refs=refs or [],
        discussion_refs=discussion or [])])


def state(tmp_path):
    store=Store(tmp_path); p=prepared(store)
    with store.edit(p['id'],p['revision'],'isolated scenario') as (p, _):
        p['ui']=None; p['documents']={}; p['review']=None
        p['items'][0]['statement']='管理员可以编辑联系人姓名。'
        p['items'][1]['statement']='只读角色不能编辑联系人。'
        p['items'][2]['statement']='只读角色点击编辑，应被拒绝且联系人保持不变。'
        p['sources'][0]['excerpts'][0]['text']='维护联系人；只读角色不能编辑。'
        p['questions'][0].update(question='重复姓名如何处理？',blocking=True)
        p['items'].append(dict(p['items'][0],id='REQ-CANDIDATE',statement='建议删除时必填原因。',selection_status='candidate',epistemic_status='proposed'))
        p['grants'][origin(DEFAULT)]={'source_ids':['SRC-0001']}
    return store,store.get(p['id'])


def test_candidate_normative_rejected_without_mutation(tmp_path):
    from app.prd import compile_plan
    _,p=state(tmp_path);before=copy.deepcopy(p)
    with pytest.raises(Problem) as exc:compile_plan(plan(['REQ-CANDIDATE']),p,'prd')
    assert exc.value.code=='SEMANTIC_BLOCKED'
    assert 'sections[0].normative_refs[0]' in exc.value.message and 'REQ-CANDIDATE' in exc.value.message
    assert p==before


def test_plan_schema_and_reference_errors_are_specific(tmp_path):
    from app.prd import compile_plan
    _,p=state(tmp_path);invalid=plan();invalid['sections'][0]['normative_refs']='REQ-0001'
    with pytest.raises(Problem,match='normative_refs'):compile_plan(invalid,p,'prd')
    with pytest.raises(Problem,match='REQ-MISSING'):compile_plan(plan(['REQ-MISSING']),p,'prd')


def test_partitioned_input_excludes_history_and_other_contracts(tmp_path):
    _,p=state(tmp_path)
    p['messages'].append({'text':'UNRELATED_OLD_ARTIFACT','response':{'huge':'OLD_WIREFRAME'}})
    messages,_,_=assemble(p,'prd','',DEFAULT,tmp_path)
    serialized=dumps(messages)
    assert 'UNRELATED_OLD_ARTIFACT' not in serialized and 'OLD_WIREFRAME' not in serialized
    ctx=json.loads(messages[1]['content'][0]['text'])
    assert ctx['A_normative']['requirement'][0]['id']=='REQ-0001'
    assert any(i['id']=='REQ-CANDIDATE' for i in ctx['C_discussion']['items'])
    assert 'wireframe.schema.json' not in messages[0]['content']
    assert '重复姓名如何处理' in serialized


def test_draft_assembly_preserves_answers_unknowns_and_exports(tmp_path):
    from app.prd import compile_plan
    _,p=state(tmp_path)
    p['questions'].append(dict(p['questions'][0],id='Q-ANSWER',status='answered',answer='姓名规则尚未确定，先保留原记录。',related_refs=['REQ-0001']))
    r=compile_plan(plan(['REQ-0001','RULE-0001','AC-0001'],['REQ-CANDIDATE']),p,'prd')
    validate_response(r,'prd',p,p['sources'][0]['excerpts'])
    apply_response(p,r)
    assert gate(p) and p['active_baseline_id'] is None
    assert p['items'][-1]['selection_status']=='candidate'
    from app.exports import document_files
    files=asyncio.run(document_files(p,'prd'))
    md=files['PRD.md'].decode('utf-8')
    word='\n'.join(x.text for x in Document(io.BytesIO(files['PRD.docx'])).paragraphs)
    for text in [p['items'][0]['statement'],'建议删除时必填原因。','重复姓名如何处理？','姓名规则尚未确定，先保留原记录。','未采纳','SRC-0001','EX-0001','尚无原型','不代表业务负责人批准']:
        assert text in md and text in word
    assert '电价' not in md
    assert all(c['item_id']!='REQ-CANDIDATE' for c in r['result']['coverage'])


def test_truncation_never_increases_approved_budget(tmp_path,monkeypatch):
    # Budget engine contract; candidate/unknown input is blocked by the new product path.
    monkeypatch.setattr('app.workflow.execution_issues',lambda *a:[])
    async def run():
        store,p=state(tmp_path)
        class Capture(FakeProvider):
            def __init__(self):super().__init__([Problem('OUTPUT_TRUNCATED','length')]*3);self.configs=[]
            async def request(self,c,m,evidence=None):
                self.configs.append(dict(c));return await super().request(c,m,evidence)
        provider=Capture();w=Workflow(store,provider);store.setting('model',dict(DEFAULT,max_tokens=700,max_calls=2))
        approved=dict(DEFAULT,max_tokens=700,max_calls=2)
        r=w.start(p['id'],p['revision'],'prd','',config_override=approved)
        approved['max_tokens']=2800  # later setting/caller changes cannot alter this approved run
        await w.tasks[r['id']]
        result=store.get_record(p['id'],r['id'])
        assert [c['max_tokens'] for c in provider.configs]==[700,700]
        assert result['calls']==2 and result['status']=='paused_budget'
        assert result['repair_count']==1  # a scheduled-but-unfunded third request did not happen
        assert store.get(p['id'])['documents']=={}
    asyncio.run(run())


def test_template_example_cannot_become_normative(tmp_path):
    from app.prd import compile_plan
    _,p=state(tmp_path)
    p['sources'].append(dict(p['sources'][0],id='SRC-TEMPLATE',purpose='template',excerpts=[dict(p['sources'][0]['excerpts'][0],id='EX-TEMPLATE',source_id='SRC-TEMPLATE',text='模板示例：驳回原因必填。')]))
    p['items'][-1].update(statement='建议驳回原因必填。',source_refs=[dict(source_id='SRC-TEMPLATE',excerpt_id='EX-TEMPLATE')])
    r=compile_plan(plan(['REQ-0001'],['REQ-CANDIDATE']),p,'prd')
    blocks=[b for s in r['result']['sections'] for b in s['blocks']]
    assert any('建议驳回原因必填' in (b['text'] or '') and '未采纳' in b['text'] for b in blocks)
    assert not any('REQ-CANDIDATE' in b['ref_ids'] for b in blocks if b['kind'] in ('requirement','rule','acceptance'))
    with pytest.raises(Problem,match='REQ-CANDIDATE'):compile_plan(plan(['REQ-CANDIDATE']),p,'prd')


def test_empty_section_is_not_coverage_and_source_limit_stays_visible(tmp_path):
    from app.prd import compile_plan
    _,p=state(tmp_path)
    p['sources'][0].update(parse_status='partial',failure_reason='仅部分段落可读')
    p['items'][0]['selection_status']='candidate'
    r=compile_plan(plan(discussion=['REQ-0001']),p,'prd',omitted=['EX-OMITTED'])
    assert all(c['item_id']!='REQ-0001' for c in r['result']['coverage'])
    assert all(m['disposition']=='pending' for m in r['result']['reference_mapping'])
    rendered=dumps(r)
    assert '仅部分段落可读' in rendered and 'EX-OMITTED' in rendered


def prepare_http(case):
    c,app,model,root=case
    from tests.product_flow_helpers import isolate_legacy_engine
    isolate_legacy_engine(app)
    body,preview=action_plan(c,root)
    assert start(c,root,body,preview).status_code==200
    assert finished(c,root)['status']=='succeeded'
    return c,app,model,root


def test_http_candidate_rejected_and_explicit_discussion_retained(case):
    c,app,model,root=prepare_http(case)
    before=c.get(root).json()
    ref=before['items'][0]['id']
    model['transform']=lambda value,body,ctx:plan([ref])
    body,preview=action_plan(c,root,'document','',max_calls=2)
    assert start(c,root,body,preview).status_code==200
    task=finished(c,root)
    assert task['status']=='failed' and task['error']=='SEMANTIC_BLOCKED' and task['calls']==1
    assert c.get(root).json()['items']==before['items'] and not c.get(root).json()['documents']
    model['transform']=lambda value,body,ctx:plan(discussion=[ref])
    body,preview=action_plan(c,root,'document','',max_calls=2)
    assert start(c,root,body,preview).status_code==200
    assert finished(c,root)['status']=='succeeded'
    after=c.get(root).json()
    assert after['documents']['prd'] and after['confirmation_issues'] and not after['active_baseline_id']
    assert after['items']==before['items']


def test_http_failed_schema_repair_keeps_each_request_and_input_identity(case):
    c,app,model,root=prepare_http(case)
    model['invalid_next']=True
    body,preview=action_plan(c,root,'document','',max_calls=2)
    assert start(c,root,body,preview).status_code==200
    task=finished(c,root)
    assert task['calls']==2 and task['status']=='succeeded'
    run=app.state.store.get_record(root.rsplit('/',1)[-1],task['run_ids'][0])
    assert task['approved_plan_hash']==preview['plan_hash']
    approved_tokens=task['authorized_configs']['prd']['max_tokens']
    assert approved_tokens==model['config']['max_tokens']>0
    entries=[json.loads((app.state.store.folder/'evidence/model-calls'/(a['call_id']+'.json')).read_text('utf-8')) for a in run['attempts']]
    assert entries[0]['validation_result']=='SCHEMA_INVALID' and entries[0]['final_output']=='{invalid first response'
    assert entries[1]['repair_of']==entries[0]['call_id'] and entries[1]['validation_result']=='accepted'
    assert entries[0]['actual_input_hash']!=entries[1]['actual_input_hash']
    for e in entries:
        assert e['max_tokens']==e['authorization']['approved_max_tokens']==approved_tokens
        assert e['authorization']['schema_hash'] and e['authorization']['prompt_hash'] and e['authorization']['config_hash']
        assert e['usage']==dict(prompt_tokens=10,completion_tokens=10,total_tokens=20)
        assert 'ui02-synthetic-key' not in dumps(e)
    assert entries[1]['input_metrics']['repair_context']['characters']>entries[0]['input_metrics']['repair_context']['characters']


def test_http_truncation_shared_task_ceiling_and_old_document_preserved(case):
    c,app,model,root=prepare_http(case)
    body,preview=action_plan(c,root,'document','')
    assert start(c,root,body,preview).status_code==200
    assert finished(c,root)['status']=='succeeded'
    old=c.get(root).json()['documents']
    config=dict(model['config'],max_tokens=700)
    assert c.put('/api/models/model',json=config).status_code==200
    model['truncate']=True
    before=len(model['requests'])
    body,preview=action_plan(c,root,'document','',max_calls=2)
    assert start(c,root,body,preview).status_code==200
    task=finished(c,root)
    assert task['calls']==2 and task['status']=='paused_budget'
    assert [b['max_tokens'] for b in model['requests'][before:]]==[700,700]
    assert c.get(root).json()['documents']==old
    assert task['cost'] is None
