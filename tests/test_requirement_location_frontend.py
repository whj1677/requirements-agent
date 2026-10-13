"""Only uniquely linked current requirements may replace a clause/question view."""
from tests.test_scope_frontend import run_ts


LOAD = r'''
  import assert from 'node:assert/strict';import {build} from 'esbuild';
  const built=await build({entryPoints:['src/pm-prototype/requirement-location.ts'],bundle:true,write:false,format:'esm',platform:'node'});
  const {singleRequirementRoot:locate}=await import('data:text/javascript;base64,'+Buffer.from(built.outputFiles[0].text).toString('base64'));
  const req=(id,extra={})=>({id,kind:'requirement',applies_to:'to_be',selection_status:'candidate',...extra});
  const ac=(id,extra={})=>req(id,{kind:'acceptance',...extra});
  const project=(items,questions=[])=>({items,questions});
'''


def test_direct_requirement_and_candidate_chain_resolve_without_mutating_inputs():
    run_ts(LOAD + r'''
      const p=project([req('REQ-1',{selection_status:'selected'}),req('REV-1',{target_item_id:'REQ-1'}),req('REV-2',{target_item_id:'REV-1'}),req('REQ-other')]);
      const before=JSON.stringify(p);
      for(const itemId of ['REQ-1','REV-1','REV-2'])assert.equal(locate(p,{itemId}),'REQ-1');
      assert.equal(locate(p,{}),undefined);
      assert.equal(locate(p,{itemId:'missing'}),undefined);
      assert.equal(JSON.stringify(p),before);
    ''')


def test_invalid_candidate_chains_fail_safely_instead_of_guessing_a_root():
    run_ts(LOAD + r'''
      const p=project([req('SELF',{target_item_id:'SELF'}),req('A',{target_item_id:'B'}),req('B',{target_item_id:'A'}),
        req('ORPHAN',{target_item_id:'missing'}),req('WRONG-KIND',{target_item_id:'AC'}),ac('AC')]);
      for(const itemId of ['SELF','A','B','ORPHAN','WRONG-KIND'])assert.equal(locate(p,{itemId}),undefined);
    ''')


def test_acceptance_and_related_clause_support_both_link_directions_and_revisions():
    run_ts(LOAD + r'''
      const forward=project([req('REQ-1'),ac('AC-1',{related_refs:['REQ-1']})]);
      const reverse=project([req('REQ-1',{related_refs:['AC-1']}),ac('AC-1')]);
      for(const p of [forward,reverse])assert.equal(locate(p,{itemId:'AC-1'}),'REQ-1');
      const revisions=project([req('REQ-1',{related_refs:['AC-1']}),req('REV',{target_item_id:'REQ-1'}),
        ac('AC-1',{related_refs:['REQ-1']}),ac('AC-revision',{target_item_id:'AC-1',related_refs:['REV']}),
        req('RULE',{kind:'rule',related_refs:['REV']})]);
      assert.equal(locate(revisions,{itemId:'AC-revision'}),'REQ-1');
      assert.equal(locate(revisions,{itemId:'RULE'}),'REQ-1');
    ''')


def test_shared_acceptance_never_selects_the_first_related_requirement():
    run_ts(LOAD + r'''
      const p=project([req('REQ-A'),req('REQ-B'),ac('AC',{related_refs:['REQ-A','REQ-B']})]);
      assert.equal(locate(p,{itemId:'AC'}),undefined);
      p.items[2].related_refs=['REQ-A'];p.items[1].related_refs=['AC'];
      assert.equal(locate(p,{itemId:'AC'}),undefined,'merge both directions before deciding uniqueness');
      p.items[2].related_refs=[];p.items[0].related_refs=['AC'];
      assert.equal(locate(p,{itemId:'AC'}),undefined,'two reverse links are also shared');
    ''')


def test_questions_use_actual_unique_links_without_falling_back_to_any_requirement():
    run_ts(LOAD + r'''
      const p=project([req('REQ-A'),req('REQ-B'),req('REV',{target_item_id:'REQ-A'}),ac('AC',{related_refs:['REQ-A']})],[
        {id:'Q-direct',related_refs:['REV','REQ-A']},{id:'Q-ac',related_refs:['AC']},
        {id:'Q-cross',related_refs:['REQ-A','REQ-B']},{id:'Q-empty',related_refs:[]},
        {id:'Q-unrelated',related_refs:['not-an-item']},{id:'Q-affected',affected_requirements:[{id:'REQ-A'}]},
        {id:'Q-answer'}]);
      p.items[2].answer_refs=['Q-answer'];
      for(const questionId of ['Q-direct','Q-ac','Q-affected','Q-answer'])assert.equal(locate(p,{questionId}),'REQ-A');
      for(const questionId of ['Q-cross','Q-empty','Q-unrelated','missing'])assert.equal(locate(p,{questionId}),undefined);
      assert.equal(locate(p,{itemId:'REQ-A',questionId:'Q-direct'}),'REQ-A');
      assert.equal(locate(p,{itemId:'REQ-B',questionId:'Q-direct'}),undefined);
      assert.equal(locate(p,{itemId:'REQ-A',questionId:'Q-empty'}),undefined);
    ''')


def test_shared_answer_groups_preserve_cross_requirement_context():
    run_ts(LOAD + r'''
      const p=project([req('REQ-A'),req('REQ-B')],[
        {id:'Q-A',related_refs:['REQ-A'],decision_group_id:'G'},
        {id:'Q-B',related_refs:['REQ-B'],decision_group_id:'G'},
        {id:'Q-retired',related_refs:['REQ-B'],decision_group_id:'SAME',superseded_by:['Q-new']},
        {id:'Q-new',related_refs:['REQ-A'],decision_group_id:'SAME'}]);
      assert.equal(locate(p,{questionId:'Q-A'}),undefined);
      assert.equal(locate(p,{questionId:'Q-B',itemId:'REQ-B'}),undefined);
      assert.equal(locate(p,{questionId:'Q-new'}),'REQ-A');
      p.questions[1].related_refs=['REQ-A'];assert.equal(locate(p,{questionId:'Q-A'}),'REQ-A');
    ''')


def test_history_and_non_to_be_objects_cannot_target_an_invisible_table_row():
    run_ts(LOAD + r'''
      for(const extra of [{selection_status:'rejected'},{selection_status:'deferred'},{selection_status:'excluded'},
        {applies_to:'as_is'},{applies_to:undefined},{superseded_by:'NEW'}]){
        const p=project([req('OLD',extra),req('CANDIDATE',{target_item_id:'OLD'}),ac('AC',{related_refs:['OLD']})]);
        for(const itemId of ['OLD','CANDIDATE','AC'])assert.equal(locate(p,{itemId}),undefined);
      }
      for(const extra of [{selection_status:'rejected'},{selection_status:'deferred'},{selection_status:'excluded'},{applies_to:'as_is'}]){
        const p=project([req('REQ'),ac('AC',{related_refs:['REQ'],...extra}),req('REV',{target_item_id:'REQ',...extra})]);
        assert.equal(locate(p,{itemId:'AC'}),undefined);assert.equal(locate(p,{itemId:'REV'}),undefined);
      }
      for(const extra of [{superseded_by:['NEW']},{kind:'parent'},{status:'deferred'},{status:'out_of_scope'},{out_of_scope_reason:'不在本期'}]){
        const p=project([req('REQ')],[{id:'Q',related_refs:['REQ'],...extra}]);assert.equal(locate(p,{questionId:'Q'}),undefined);
      }
    ''')
