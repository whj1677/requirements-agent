"""Sanitized shapes from LIVE-01; no historical business inputs or outputs."""
import copy
import json
import jsonschema
import pytest
from app.core import Problem, dumps
from app.prd import compile_plan
from tests.test_prd01 import state, plan, prepare_http
from tests.test_ui02_actions import case, plan as action_plan, start, finished


def test_schema_failures_and_shared_example(tmp_path):
    from app.prd import PLAN_SCHEMA, plan_contract, repair_instruction
    _,p=state(tmp_path)
    contract=plan_contract(p)
    jsonschema.validate(contract['example'],PLAN_SCHEMA)
    assert contract['example']['plan_version']=='2'
    assert set(contract['example']['sections'][0])=={'section_key','context_refs','normative_refs','discussion_refs'}
    bad=plan();bad['narration']='wrong level'
    with pytest.raises(Problem) as e:compile_plan(bad,p,'prd')
    repair=repair_instruction(e.value,p)
    assert '根节点' in repair and 'narration' in repair
    bad=plan(['REQ-0001']);bad['sections']*=3
    for s in bad['sections']:s.pop('context_refs',None)
    with pytest.raises(Problem) as e:compile_plan(bad,p,'prd')
    assert all(f'sections[{n}]' in e.value.message for n in range(3))
    assert 'context_refs' in repair_instruction(e.value,p)
    for value in (None,42):
        bad=plan(['REQ-0001']);bad['sections'][0]['narration']=value
        with pytest.raises(Problem):compile_plan(bad,p,'prd')
    for field,value in [('title','自由标题'),('limitations',['虚构未知'])]:
        bad=plan(['REQ-0001']);bad[field]=value
        with pytest.raises(Problem) as e:compile_plan(bad,p,'prd')
        assert e.value.code=='SCHEMA_INVALID'


@pytest.mark.parametrize('collection,label',[('questions','问题'),('options','方案'),('sources','来源')])
def test_reference_types_from_objects_not_prefix(tmp_path,collection,label):
    _,p=state(tmp_path)
    p[collection].append(dict(p[collection][0] if p[collection] else {},id='REQ-EXISTS'))
    before=copy.deepcopy(p)
    with pytest.raises(Problem) as e:compile_plan(plan(discussion=['REQ-EXISTS']),p,'prd')
    assert e.value.code=='REFERENCE_INVALID' and label in e.value.message and '不是条目' in e.value.message
    with pytest.raises(Problem,match='本次允许范围内不存在'):compile_plan(plan(discussion=['Q-ABSENT']),p,'prd')
    assert p==before


def test_empty_normative_whitelist_preserves_draft_unknowns(tmp_path):
    from app.prd import plan_contract
    from app.workflow import apply_response
    from app.contracts import gate, validate_response
    _,p=state(tmp_path)
    for i in p['items']:i['kind']='goal'
    before=copy.deepcopy(p['questions'])
    contract=plan_contract(p)
    assert contract['normative_item_ids']==[] and contract['discussion_item_ids']
    actual=plan(discussion=[contract['discussion_item_ids'][0]])
    r=compile_plan(actual,p,'prd')
    validate_response(r,'prd',p,p['sources'][0]['excerpts'])
    apply_response(p,r)
    assert p['questions']==before and gate(p) and p['active_baseline_id'] is None
    assert r['result']['coverage']==[] and before[0]['question'] in dumps(r)


@pytest.mark.parametrize('shape',['root','missing','question'])
def test_http_actual_contract_and_targeted_repair(case,shape):
    c,app,model,root=prepare_http(case)
    before=c.get(root).json(); calls=[]
    def response(value,body,ctx):
        calls.append(body)
        if len(calls)==1:
            if shape=='root':value['narration']='wrong level'
            if shape=='missing':
                value['sections']=[dict(value['sections'][0]) for _ in range(3)]
                for s in value['sections']:del s['context_refs']
            if shape=='question':value['sections'][0]['discussion_refs']=[before['questions'][0]['id']]
        return value
    model['transform']=response
    body,preview=action_plan(c,root,'document','',max_calls=2)
    assert start(c,root,body,preview).status_code==200
    task=finished(c,root)
    assert task['status']=='succeeded' and task['calls']==2
    headers=[json.loads(b['messages'][0]['content'].split('可信任务头：')[1]) for b in calls]
    assert headers[0]['plan_contract']==headers[1]['plan_contract']
    assert headers[0]['plan_contract']['normative_item_ids']==[]
    repair=calls[1]['messages'][2]['content']
    assert '省略叙述' not in repair and '压缩叙述' not in repair
    assert dumps(headers[0]['plan_contract']) in repair
    if shape=='missing':assert all(f'sections[{n}]' in repair for n in range(3))
    if shape=='question':assert '属于问题，不是条目' in repair
    approved_tokens=task['authorized_configs']['prd']['max_tokens']
    assert approved_tokens==model['config']['max_tokens']>0
    assert [b['max_tokens'] for b in calls]==[approved_tokens,approved_tokens]
    after=c.get(root).json()
    assert after['items']==before['items'] and after['questions']==before['questions']
    assert not after['active_baseline_id'] and after['confirmation_issues']
    assert before['questions'][0]['question'] in dumps(after['documents'])
    run=app.state.store.get_record(root.rsplit('/',1)[-1],task['run_ids'][0])
    entries=[json.loads((app.state.store.folder/'evidence/model-calls'/(a['call_id']+'.json')).read_text('utf-8')) for a in run['attempts']]
    assert all(e['max_tokens']==e['authorization']['approved_max_tokens']==approved_tokens for e in entries)
    assert entries[1]['repair_of']==entries[0]['call_id']
    assert entries[0]['actual_input_hash']!=entries[1]['actual_input_hash']
    assert entries[0]['validation_result'] in ('SCHEMA_INVALID','REFERENCE_INVALID')
    assert entries[1]['validation_result']=='accepted'
    assert 'ui02-synthetic-key' not in dumps(entries)


def test_only_truncation_requests_compression(tmp_path):
    from app.prd import repair_instruction
    _,p=state(tmp_path)
    for code in ('SCHEMA_INVALID','REFERENCE_INVALID','OUTPUT_EMPTY'):
        assert '压缩叙述' not in repair_instruction(Problem(code,'test'),p)
    repair=repair_instruction(Problem('OUTPUT_TRUNCATED','length'),p)
    assert '合并章节' in repair and '有效引用' in repair
