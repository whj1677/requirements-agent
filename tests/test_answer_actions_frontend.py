"""Answer actions preserve pending decisions and require explicit clause review."""
from tests.test_scope_frontend import run_ts


LOAD = r'''
  import assert from 'node:assert/strict';import {build} from 'esbuild';
  const built=await build({entryPoints:['src/pm-prototype/answer-actions.ts'],bundle:true,write:false,format:'esm',platform:'node'});
  const {primaryAnswerAction,manualReviewReady,directReviewTargets,previewGroups}=await import('data:text/javascript;base64,'+Buffer.from(built.outputFiles[0].text).toString('base64'));
'''


def test_answer_primary_action_invalidates_stale_results_and_reuses_current_review():
    run_ts(LOAD + r'''
      const saved={hasAnswer:true,answerDirty:false,proposalCurrent:false,manualReady:false,complete:false,hasPreview:false};
      assert.equal(primaryAnswerAction({...saved,hasAnswer:false,complete:true,hasPreview:true}),'answer');
      assert.equal(primaryAnswerAction(saved),'organize','saved text alone cannot make an empty pending preview useful');
      assert.equal(primaryAnswerAction({...saved,proposalCurrent:true}),'preview','current AI suggestions should not trigger another paid call');
      assert.equal(primaryAnswerAction({...saved,manualReady:true}),'preview','manual review needs no AI');
      assert.equal(primaryAnswerAction({...saved,hasPreview:true}),'review');
      assert.equal(primaryAnswerAction({...saved,complete:true}),'next');
      assert.equal(primaryAnswerAction({...saved,answerDirty:true,proposalCurrent:true,hasPreview:true,complete:true}),'organize','edits invalidate every stale result');
      assert.equal(primaryAnswerAction({...saved,answerDirty:true,manualReady:true,hasPreview:true,complete:true}),'preview');
    ''')


def test_manual_review_checks_every_decision_and_exact_quotes_without_completing_siblings():
    run_ts(LOAD + r'''
      const decisions=[{id:'Q-price'},{id:'Q-recalc'}],answer='新电价只能未来生效。重算暂不确定。';
      const coverage={'Q-price':{status:'clear',answer_quote:'新电价只能未来生效。'},'Q-recalc':{status:'pending',answer_quote:''}};
      const before=JSON.stringify(coverage);
      assert.equal(manualReviewReady(decisions,coverage,answer),true);
      assert.equal(JSON.stringify(coverage),before,'readiness cannot approve or change a pending sibling');
      assert.equal(manualReviewReady(decisions,{'Q-price':coverage['Q-price']},answer),false,'omitted decisions are not ready');
      assert.equal(manualReviewReady(decisions,{...coverage,'Q-price':{status:'pending',answer_quote:''}},answer),false);
      assert.equal(manualReviewReady(decisions,{...coverage,'Q-price':{status:'clear',answer_quote:''}},answer),false);
      assert.equal(manualReviewReady(decisions,{...coverage,'Q-price':{status:'clear',answer_quote:'新电价只能过去生效。'}},answer),false);
      assert.equal(manualReviewReady(decisions,{...coverage,'Q-price':{status:'partial',answer_quote:'编造引文'}},answer),false);
      assert.equal(manualReviewReady(decisions,{...coverage,'Q-price':{status:'conflict',answer_quote:''}},answer),true);
      assert.equal(manualReviewReady(decisions,{...coverage,'Q-price':{status:'unknown',answer_quote:''}},answer),false);
      assert.equal(manualReviewReady([{id:'Q-price'},{id:'Q-price'}],coverage,answer),false);
      assert.equal(manualReviewReady([],{},answer),false);
      assert.equal(manualReviewReady(decisions,coverage,'  '),false);
    ''')


def test_direct_targets_exclude_unrelated_candidates_revisions_and_non_normative_items():
    run_ts(LOAD + r'''
      const project={items:[
        {id:'REQ-1',kind:'requirement',selection_status:'selected'},
        {id:'RULE-1',kind:'rule',selection_status:'selected'},
        {id:'AC-1',kind:'acceptance',selection_status:'selected'},
        {id:'CON-1',kind:'constraint',selection_status:'selected'},
        {id:'GOAL-1',kind:'goal',selection_status:'selected'},
        {id:'REQ-other',kind:'requirement',selection_status:'selected'},
        {id:'REQ-candidate',kind:'requirement',selection_status:'candidate'},
        {id:'REQ-deferred',kind:'requirement',selection_status:'deferred'},
        {id:'REV-1',kind:'requirement',selection_status:'selected',target_item_id:'REQ-1'},
        {id:'RISK-1',kind:'risk',selection_status:'selected'},
      ]};
      const decision={id:'Q-1',related_refs:['REQ-1','RULE-1','AC-1','CON-1','GOAL-1','REQ-candidate','REQ-deferred','REV-1','RISK-1'],affected_requirements:[{id:'REQ-other'}]};
      const before=JSON.stringify({project,decision});
      assert.deepEqual(directReviewTargets(project,decision).map(item=>item.id),['REQ-1','RULE-1','AC-1','CON-1','GOAL-1']);
      assert.deepEqual(directReviewTargets(project,{id:'Q-no-ref'}),[]);
      assert.equal(JSON.stringify({project,decision}),before);
    ''')


def test_preview_groups_never_infer_manual_approval_and_preserve_candidate_mapping():
    run_ts(LOAD + r'''
      const project={items:[{id:'REQ-1',kind:'requirement',selection_status:'selected'},
        {id:'RULE-1',kind:'rule',selection_status:'selected'},
        {id:'REV-1',kind:'requirement',selection_status:'candidate',target_item_id:'REQ-1',answer_refs:['Q-1']}]};
      const decisions=[{id:'Q-1',question:'电价如何生效？',related_refs:['REQ-1','RULE-1']},{id:'Q-2',related_refs:['REQ-1']}];
      const coverage={'Q-1':{status:'clear',answer_quote:'未来生效',candidate_item_ids:[]},'Q-2':{status:'pending',answer_quote:'',candidate_item_ids:['REV-1']}};
      const before=JSON.stringify({project,decisions,coverage});
      assert.deepEqual(previewGroups(project,decisions,coverage),[],'no candidate is not permission to approve existing clauses');
      assert.deepEqual(previewGroups(project,decisions,coverage,{'Q-2':['REQ-1']}),[],'pending answers cannot apply selected targets');
      assert.deepEqual(previewGroups(project,decisions,coverage,{'Q-1':['RULE-1']}),[{
        id:'group-Q-1',title:'电价如何生效？',decision_ids:['Q-1'],candidate_item_ids:[],target_item_ids:['RULE-1'],depends_on:[]}]);
      const chosen={...coverage,'Q-1':{...coverage['Q-1'],candidate_item_ids:['REV-1']}};
      assert.deepEqual(previewGroups(project,decisions,chosen)[0].target_item_ids,['REQ-1']);
      assert.deepEqual(previewGroups(project,decisions,chosen,{'Q-1':['REQ-1','RULE-1']})[0],{
        id:'group-Q-1',title:'电价如何生效？',decision_ids:['Q-1'],candidate_item_ids:['REV-1'],target_item_ids:['REQ-1','RULE-1'],depends_on:[]});
      assert.equal(JSON.stringify({project,decisions,coverage}),before,'building a preview must not approve or mutate source state');
    ''')


def test_preview_groups_fail_closed_when_explicit_targets_or_candidates_have_changed():
    run_ts(LOAD + r'''
      const project={items:[{id:'REQ-1',kind:'requirement',selection_status:'selected'},
        {id:'REQ-other',kind:'requirement',selection_status:'selected'},
        {id:'REQ-new',kind:'requirement',selection_status:'candidate'},
        {id:'REV-old',kind:'requirement',selection_status:'deferred',target_item_id:'REQ-1'}]};
      const decisions=[{id:'Q-1',related_refs:['REQ-1','REQ-new']}];
      const coverage={'Q-1':{status:'clear',answer_quote:'明确',candidate_item_ids:[]}};
      for(const id of ['REQ-other','REQ-new','missing'])
        assert.throws(()=>previewGroups(project,decisions,coverage,{'Q-1':[id]}),/直接关联且已采纳/);
      for(const id of ['missing','REV-old'])
        assert.throws(()=>previewGroups(project,decisions,{'Q-1':{...coverage['Q-1'],candidate_item_ids:[id]}}),/待核对修改已变化/);
    ''')


def test_shared_candidate_preserves_every_answer_binding_in_one_atomic_group():
    run_ts(LOAD + r'''
      const project={items:[{id:'REQ-1',kind:'requirement',selection_status:'selected'},
        {id:'REV-1',kind:'requirement',selection_status:'candidate',target_item_id:'REQ-1',answer_refs:['Q-a','Q-b']}]};
      const decisions=[{id:'Q-a',question:'谁操作？',related_refs:['REQ-1']},{id:'Q-b',question:'可否互改？',related_refs:['REQ-1']}];
      const coverage={'Q-a':{status:'clear',answer_quote:'运维人员',candidate_item_ids:['REV-1']},
        'Q-b':{status:'clear',answer_quote:'不可互改',candidate_item_ids:['REV-1']}};
      const before=JSON.stringify({project,decisions,coverage});
      assert.deepEqual(previewGroups(project,decisions,coverage),[{
        id:'group-Q-a--Q-b',title:'谁操作？；可否互改？',decision_ids:['Q-a','Q-b'],
        candidate_item_ids:['REV-1'],target_item_ids:['REQ-1'],depends_on:[]}]);
      const selectedOnce={...coverage,'Q-b':{...coverage['Q-b'],candidate_item_ids:[]}};
      assert.deepEqual(previewGroups(project,decisions,selectedOnce)[0].decision_ids,['Q-a','Q-b'],
        'one explicit revision retains both already clear bindings even when listed once');
      assert.equal(JSON.stringify({project,decisions,coverage}),before);
      assert.throws(()=>previewGroups(project,decisions,{...coverage,'Q-b':{status:'pending',answer_quote:''}}),/尚未明确/);
      assert.throws(()=>previewGroups(project,[decisions[0]],coverage),/尚未明确/,'missing decision cannot be fabricated');
    ''')


def test_shared_targets_form_connected_groups_but_unrelated_decisions_stay_independent():
    run_ts(LOAD + r'''
      const project={items:['REQ-1','RULE-1','AC-1'].map(id=>({id,kind:id.startsWith('REQ')?'requirement':id.startsWith('RULE')?'rule':'acceptance',selection_status:'selected'}))};
      const decisions=[{id:'Q-a',related_refs:['REQ-1']},{id:'Q-b',related_refs:['REQ-1','RULE-1']},
        {id:'Q-c',related_refs:['RULE-1']},{id:'Q-d',related_refs:['AC-1']}];
      const coverage=Object.fromEntries(decisions.map(d=>[d.id,{status:'clear',answer_quote:'已明确'}]));
      const manual={'Q-a':['REQ-1'],'Q-b':['REQ-1','RULE-1'],'Q-c':['RULE-1'],'Q-d':['AC-1']};
      const groups=previewGroups(project,decisions,coverage,manual);
      assert.equal(groups.length,2);
      assert.deepEqual(groups[0].decision_ids,['Q-a','Q-b','Q-c']);
      assert.deepEqual(groups[0].target_item_ids,['REQ-1','RULE-1']);
      assert.deepEqual(groups[1].decision_ids,['Q-d']);
      assert.deepEqual(groups[1].target_item_ids,['AC-1']);
    ''')


def test_two_distinct_revisions_of_one_target_are_not_silently_merged_or_overwritten():
    run_ts(LOAD + r'''
      const project={items:[{id:'REQ-1',kind:'requirement',selection_status:'selected'},
        {id:'REV-a',kind:'requirement',selection_status:'candidate',target_item_id:'REQ-1',answer_refs:['Q-a'],statement:'A'},
        {id:'REV-b',kind:'requirement',selection_status:'candidate',target_item_id:'REQ-1',answer_refs:['Q-b'],statement:'B'}]};
      const decisions=[{id:'Q-a',related_refs:['REQ-1']},{id:'Q-b',related_refs:['REQ-1']}];
      const coverage={'Q-a':{status:'clear',answer_quote:'A',candidate_item_ids:['REV-a']},
        'Q-b':{status:'clear',answer_quote:'B',candidate_item_ids:['REV-b']}};
      assert.throws(()=>previewGroups(project,decisions,coverage),/同一条款有多份不同修订/);
      const other={...coverage,'Q-b':{status:'clear',answer_quote:'B',candidate_item_ids:[]}};
      const groups=previewGroups(project,decisions,other,{'Q-b':['REQ-1']});
      assert.deepEqual(groups[0].decision_ids,['Q-a','Q-b']);
      assert.deepEqual(groups[0].candidate_item_ids,['REV-a']);
      assert.deepEqual(groups[0].target_item_ids,['REQ-1']);
    ''')
