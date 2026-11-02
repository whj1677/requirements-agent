"""Saved unknowns and model provenance survive ordinary, bulk and restored drafts."""
import pytest

from tests.test_detail_editor_frontend import LOAD
from tests.test_scope_frontend import run_ts


STATE = LOAD + r'''
const {initial,restoreSheet}=await load('src/pm-prototype/refinement-sheet-state.ts');
const {scopeDraft,suggestedKeys}=await load('src/scope-draft.ts');
const {initialBatch}=await load('src/pm-prototype/prewrite-batch-state.ts');
'''


def test_only_current_server_review_resolution_removes_an_old_question():
    run_ts(STATE + r'''
      const {sheetQuestions}=await load('src/pm-prototype/refinement-sheet-state.ts');
      const p=project(contract());
      p.questions=[
        {id:'resolved',status:'open',answer:null,prewrite_resolution_current:true},
        {id:'stale',status:'open',answer:null,prewrite_resolution_current:false,prewrite_resolution:{basis:'reviewed_dispute'}},
        {id:'legacy',status:'open',answer:null,prewrite_resolution:{basis:'reviewed_dispute'}},
        {id:'pending',status:'open',answer:null},
      ];
      const before=JSON.stringify(p);
      assert.deepEqual(sheetQuestions(p).map(q=>q.id),['stale','legacy','pending']);
      assert.equal(JSON.stringify(p),before,'a projection cannot fabricate answers or change history');
    ''')


@pytest.mark.parametrize('mode', ['', 'required', 'all'])
@pytest.mark.parametrize('status', ['unknown', 'none', 'not_applicable'])
def test_saved_empty_state_cannot_reappear_as_old_ai_fact(mode, status):
    run_ts(STATE + 'const mode=' + repr(mode) + ',status=' + repr(status) + ';' + r'''
      const p=project(contract());
      p.product_context.current_state='';
      p.context_field_states={current_state:{status,note:'保留已保存的明确状态'}};
      p.product_context_proposal={current_state:'旧的无依据现状描述'};
      p.product_context_proposal_evidence={current_state:{uncertain:false}};
      if(mode)p.refinement_completion={mode,run_id:'RUN-completion'};
      const before=JSON.stringify(p),sheet=initial(p,[]);
      assert.equal(scopeDraft(p).current_state,'');
      assert.equal(sheet.values.current_state,'');
      assert.equal(sheet.states.current_state.status,status);
      assert.equal(sheet.states.current_state.note,'保留已保存的明确状态');
      assert(!suggestedKeys(p,sheet.values,sheet.states).includes('current_state'));
      assert.equal(JSON.stringify(p),before);
    ''')


def test_fresh_blank_without_saved_state_still_displays_ai_proposal():
    run_ts(STATE + r'''
      const p=project(contract());p.product_context.current_state='';
      p.product_context_proposal={current_state:'建议先核对原有工作流程'};
      const sheet=initial(p,[]);
      assert.equal(sheet.values.current_state,p.product_context_proposal.current_state);
      assert(suggestedKeys(p,sheet.values,sheet.states).includes('current_state'));
    ''')


@pytest.mark.parametrize('mode', ['required', 'all'])
@pytest.mark.parametrize('status', ['unknown', 'none', 'not_applicable'])
def test_saved_nonempty_text_equal_to_proposal_keeps_state_and_note(mode, status):
    run_ts(STATE + 'const mode=' + repr(mode) + ',status=' + repr(status) + ';' + r'''
      const p=project(contract());
      p.product_context.current_state='保留的现状原文';
      p.product_context_proposal={current_state:'保留的现状原文'};
      p.context_field_states={current_state:{status,note:'人工保留的状态备注'}};
      p.refinement_completion={mode,run_id:'RUN-synthetic'};
      const before=JSON.stringify(p),sheet=initial(p,[]);
      assert.equal(sheet.values.current_state,p.product_context.current_state);
      assert.deepEqual(sheet.states.current_state,p.context_field_states.current_state);
      assert.equal(JSON.stringify(p),before);
    ''')


@pytest.mark.parametrize('status', ['reported', 'proposed'])
def test_candidate_status_without_run_evidence_does_not_mean_ai(status):
    run_ts(STATE + 'const status=' + repr(status) + ';' + r'''
      const p=project(contract()),ac=p.items.find(i=>i.id==='AC-1');
      Object.assign(ac,{selection_status:'candidate',epistemic_status:status});
      const before=JSON.stringify(p);
      assert(!initial(p,[]).rows['REQ-1'].acceptance_items[0].ai_suggested);
      p.items.push({...ac,id:'AC-revision',target_item_id:'AC-1',action:'revise',statement:'人工修订候选'});
      p.refinement_completion={mode:'prewrite',applied_revision:p.revision,candidate_ids:['AC-revision']};
      const offered=initialBatch(p,[]).rows['REQ-1'].acceptance_items[0];
      assert.equal(offered.candidate_id,'AC-revision');
      assert(!offered.ai_suggested,'batch offer is not itself proof of AI origin');
      p.items.pop();delete p.refinement_completion;
      assert.equal(JSON.stringify(p),before);
    ''')


def test_generation_run_marks_ai_without_using_epistemic_status_as_origin():
    run_ts(STATE + r'''
      const p=project(contract()),ac=p.items.find(i=>i.id==='AC-1');
      Object.assign(ac,{selection_status:'candidate',epistemic_status:'reported',generation_evidence:{run_id:'RUN-synthetic'}});
      assert.equal(initial(p,[]).rows['REQ-1'].acceptance_items[0].ai_suggested,true);
    ''')


def test_restore_refreshes_stale_origin_but_preserves_manual_content_version_and_evidence():
    run_ts(STATE + r'''
      const p=project(contract()),ac=p.items.find(i=>i.id==='AC-1');
      Object.assign(ac,{selection_status:'candidate',epistemic_status:'reported',source_refs:[{source_id:'S1',excerpt_id:'E1'}]});
      p.items.push({...ac,id:'AC-revision',target_item_id:'AC-1',action:'revise',statement:'人工修订候选'});
      const base=initial(p,[]),mine=structuredClone(base);
      base.rows['REQ-1'].acceptance_items[0].ai_suggested=true;
      Object.assign(mine.rows['REQ-1'].acceptance_items[0],{
        ai_suggested:true,statement:'本地尚未保存的人工验收修改',candidate_id:'AC-revision'});
      const savedEvidence=structuredClone(mine.rows['REQ-1'].detail_contract.fields[0].evidence);
      p.revision=2;
      const before=JSON.stringify({p,base,mine}),restored=restoreSheet(mine,base,initial(p,[]));
      const result=restored.rows['REQ-1'].acceptance_items[0];
      assert(!result.ai_suggested,'stale local AI badge must follow latest source provenance');
      assert.equal(result.statement,'本地尚未保存的人工验收修改');
      assert.equal(result.candidate_id,'AC-revision');
      assert.equal(result.id,'AC-1');
      assert.deepEqual(restored.rows['REQ-1'].detail_contract.fields[0].evidence,savedEvidence);
      assert.equal(JSON.stringify({p,base,mine}),before,'restoration does not mutate evidence or saved drafts');
    ''')
