import copy
import pytest
from app.core import Problem
from app.product_flow import status, checkpoint, execution_issues, INTAKE, SCOPE, BEHAVIOR
from app.store import Store
from tests.helpers import prepared


def advance(p, step):
    checkpoint(p,step,status(p)[step-1]['content_hash'])


def setup(tmp_path):
    p=prepared(Store(tmp_path))
    p['product_context']={k:'合成核对：'+label for k,label in {**INTAKE,**SCOPE}.items()}
    p['product_context']['scope_ids']=['REQ-0001']
    p['items'][0]['behavior']={k:'已明确：'+label for k,label in BEHAVIOR.items()}
    return p


def test_legacy_does_not_auto_complete_and_diagnostic_has_same_gate(tmp_path):
    p=prepared(Store(tmp_path));before=copy.deepcopy(p)
    assert not any(s['complete'] for s in status(p))
    assert execution_issues(p,'prd') and execution_issues(p,'ui')
    assert p==before
    with pytest.raises(Problem):advance(p,2)


def test_scope_and_rule_checks_use_actual_content_not_selected_count(tmp_path):
    p=setup(tmp_path);advance(p,1);advance(p,2)
    assert not execution_issues(p,'clarify')
    p['items'][0]['kind']='goal'
    assert any('独立本期需求' in x for x in status(p)[1]['missing'])
    with pytest.raises(Problem):advance(p,3)


def test_checkpoints_bind_content_and_retired_sketch_is_not_required(tmp_path):
    p=setup(tmp_path)
    for n in (1,2,3):advance(p,n)
    assert execution_issues(p,'ui') and not execution_issues(p,'prd')
    assert status(p)[3]['retired'] and not status(p)[3]['complete']
    assert status(p)[4]['available'] and '4' not in p['stage_checks']
    # A legacy sketch check retains its identity; it is never fabricated for the new path.
    p['sketch_review']={'applicable':False,'reason':'本次仅调整后台校验，无页面或交互变化。'}
    advance(p,4)
    assert not execution_issues(p,'prd')
    old=status(p)[2]['content_hash']
    p['items'][1]['statement']='保存行为改变。'
    assert [s['complete'] for s in status(p)[:4]]==[True,True,False,False]
    with pytest.raises(Problem) as e:checkpoint(p,3,old)
    assert e.value.code=='STALE_REVISION'


def test_critical_unknown_blocks_its_stage_not_intake_and_does_not_disappear(tmp_path):
    p=setup(tmp_path);q=p['questions'][0];q.update(blocking=True,blocking_stage=3)
    advance(p,1);advance(p,2)
    with pytest.raises(Problem):advance(p,3)
    assert q['status']=='open'
    q['out_of_scope_reason']='明确移出本期边界；本期不改变旧边界实现。'
    advance(p,3)
    assert q['status']=='open' and q['out_of_scope_reason']


def test_saved_scope_excludes_old_selected_requirement_without_revoking_decision(tmp_path):
    from app.prd import plan_contract, compile_plan
    from app.requirements import delivery_items
    p=setup(tmp_path)
    old=copy.deepcopy(p['items'][0]);old.update(id='REQ-OLD',title='旧范围',related_refs=[])
    p['items'].append(old);before=copy.deepcopy(p['items'])
    assert old['id'] not in plan_contract(p)['normative_item_ids']
    assert old['id'] not in [i['id'] for i in delivery_items(p)]
    plan=dict(plan_version='2',sections=[dict(section_key='scope',context_refs=[],normative_refs=[old['id']],discussion_refs=[])])
    with pytest.raises(Problem) as error:compile_plan(plan,p,'prd')
    assert error.value.code=='SEMANTIC_BLOCKED' and p['items']==before
