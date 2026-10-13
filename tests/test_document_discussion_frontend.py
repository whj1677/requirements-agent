"""Document follow-ups retain precise provenance and explicit checkpoints."""
from tests.test_scope_frontend import run_ts


LOAD = r'''
  import assert from 'node:assert/strict';import {build} from 'esbuild';
  const built=await build({entryPoints:['src/document-discussion-context.ts'],bundle:true,write:false,format:'esm',platform:'node'});
  const {documentDiscussionContext,documentDiscussionQuestionIds,documentDiscussionQuestionComplete,
    documentDiscussionCanCheckpoint,documentDiscussionAnswer,documentDiscussionSectionForQuestion}=await import('data:text/javascript;base64,'+Buffer.from(built.outputFiles[0].text).toString('base64'));
'''


def test_document_discussion_uses_exact_applied_result_ids_and_legacy_audits():
    run_ts(LOAD + r'''
      const project={questions:[{id:'Q-new'},{id:'Q-old'},{id:'Q-unrelated'},{id:'Q-failed'},{id:'Q-temp'}],items:[
        {id:'REV-new',selection_status:'candidate'},
        {id:'REV-old',selection_status:'candidate',generation_evidence:{run_id:'RUN-old'}},
        {id:'REV-other',selection_status:'candidate',generation_evidence:{run_id:'RUN-other'}},
        {id:'REV-adopted',selection_status:'selected',generation_evidence:{run_id:'RUN-old'}}]};
      const tasks=[{id:'TASK-old',retry_input:{target:{kind:'document',id:'DOC-1',section_id:'section-1'}},run_ids:['RUN-old','RUN-failed']},
        {id:'TASK-other',retry_input:{target:{kind:'document',id:'DOC-2'}},run_ids:['RUN-other']}];
      const runs=[{id:'RUN-new',discussion_target:{kind:'document',id:'DOC-1'},discussion_result:{question_ids:['Q-new'],item_ids:['REV-new']}},
        {id:'RUN-old',result_applied:true,decision_review:{proposal_question_ids:['Q-old']},response:{questions:[{temp_id:'Q-temp'}]}},
        {id:'RUN-failed',result_applied:false,decision_review:{proposal_question_ids:['Q-failed']}},
        {id:'RUN-other',result_applied:true,decision_review:{proposal_question_ids:['Q-unrelated']}}];
      const before=JSON.stringify({project,tasks,runs});
      assert.deepEqual([...documentDiscussionQuestionIds(project,tasks,runs,'DOC-1')],['Q-new','Q-old']);
      assert.deepEqual(documentDiscussionContext(project,tasks,runs,'DOC-1').candidateItems.map(i=>i.id),['REV-new','REV-old']);
      assert.deepEqual([...documentDiscussionQuestionIds(project,tasks,runs,'DOC-missing')],[]);
      assert.equal(JSON.stringify({project,tasks,runs}),before);
    ''')


def test_document_discussion_includes_split_descendants_and_exact_group_only():
    run_ts(LOAD + r'''
      const project={items:[],questions:[{id:'Q-parent',superseded_by:['Q-child']},
        {id:'Q-child',decision_group_id:'QG-owned'},
        {id:'Q-sibling',decision_group_id:'QG-owned'},
        {id:'Q-descendant',prior_question_id:'Q-child'},
        {id:'Q-lookalike',decision_group_id:'QG-other',question:'同名问题'},
        {id:'Q-deferred',prior_question_id:'Q-parent',status:'deferred'}]};
      const runs=[{id:'RUN-1',discussion_target:{kind:'document',id:'DOC-1'},discussion_result:{question_ids:['Q-parent']}}];
      assert.deepEqual([...documentDiscussionQuestionIds(project,[],runs,'DOC-1')],['Q-child','Q-sibling','Q-descendant']);
      assert.deepEqual([...documentDiscussionQuestionIds(project,[],runs,'DOC-2')],[]);
    ''')


def test_document_discussion_tracks_new_and_legacy_question_followups_to_fixed_point():
    run_ts(LOAD + r'''
      const project={questions:['Q-1','Q-2','Q-3','Q-4','Q-unrelated','Q-failed'].map(id=>({id,related_refs:['RULE-shared']})),items:[]};
      const tasks=[{id:'TASK-3',retry_input:{target:{kind:'question',id:'Q-3'}},run_ids:['RUN-4']},
        {id:'TASK-2',retry_input:{target:{kind:'question',id:'Q-2'}},run_ids:['RUN-3']}];
      const runs=[
        {id:'RUN-4',result_applied:true,decision_review:{reviewed_question_ids:['Q-4']}},
        {id:'RUN-3',result_applied:true,decision_review:{proposal_question_ids:['Q-3']}},
        {id:'RUN-2',discussion_target:{kind:'question',id:'Q-1'},discussion_result:{question_ids:['Q-2']}},
        {id:'RUN-1',discussion_target:{kind:'document',id:'DOC-1'},discussion_result:{question_ids:['Q-1']}},
        {id:'RUN-other',discussion_target:{kind:'question',id:'Q-outside'},discussion_result:{question_ids:['Q-unrelated']}},
        {id:'RUN-failed',discussion_target:{kind:'question',id:'Q-2'},result_applied:false,discussion_result:{question_ids:['Q-failed']}}];
      const before=JSON.stringify({project,tasks,runs});
      assert.deepEqual([...documentDiscussionQuestionIds(project,tasks,runs,'DOC-1')],['Q-1','Q-2','Q-3','Q-4']);
      assert.deepEqual(documentDiscussionContext(project,tasks,runs,'DOC-1').tasks.map(t=>t.id),['TASK-3','TASK-2']);
      assert.deepEqual([...documentDiscussionQuestionIds(project,tasks,runs,'DOC-2')],[]);
      assert.equal(JSON.stringify({project,tasks,runs}),before);
    ''')


def test_document_discussion_completion_uses_server_coverage_and_actual_blockers():
    run_ts(LOAD + r'''
      const q={id:'Q-1',answer:'运维人员',status:'answered',understanding_status:'applied',decision_coverage_complete:true};
      const project={product_flow:[{step:3,blockers:[]}]};
      assert.equal(documentDiscussionQuestionComplete(project,q),true);
      assert.equal(documentDiscussionQuestionComplete(project,{...q,decision_coverage_complete:undefined}),false);
      assert.equal(documentDiscussionQuestionComplete(project,{...q,understanding_status:'pending'}),false);
      assert.equal(documentDiscussionQuestionComplete(project,{...q,decision_pending_group_ids:['GROUP-1']}),false);
      assert.equal(documentDiscussionQuestionComplete({product_flow:[{step:3,blockers:[{actions:[{question_id:'Q-1'}]}]}]},q),false);
      assert.equal(documentDiscussionQuestionComplete({product_flow:[{step:3,blockers:[{actions:[{question_id:'Q-other'}]}]}]},q),true);
    ''')


def test_document_discussion_checkpoint_needs_valid_predecessors_and_empty_blockers():
    run_ts(LOAD + r'''
      const project={product_flow:[{step:1,complete:true},{step:2,complete:true},
        {step:3,complete:false,available:true,missing:[],blockers:[]}]};
      assert.equal(documentDiscussionCanCheckpoint(project),true);
      for(const patch of [{available:false},{complete:true},{missing:['必须回答']},{blockers:[{code:'stale_answer'}]}]){
        const p=structuredClone(project);Object.assign(p.product_flow[2],patch);
        assert.equal(documentDiscussionCanCheckpoint(p),false);
      }
      for(const index of [0,1]){const p=structuredClone(project);p.product_flow[index].complete=false;assert.equal(documentDiscussionCanCheckpoint(p),false);}
      assert.equal(documentDiscussionCanCheckpoint({}),false);
    ''')


def test_document_discussion_answer_uses_saved_raw_group_without_sibling_guessing():
    run_ts(LOAD + r'''
      const project={decision_answers:[{question_id:'QG-1',text:'旧原话'}, {question_id:'Q-other',text:'其他回答'}, {question_id:'QG-1',text:'新原话'}]};
      assert.equal(documentDiscussionAnswer(project,{id:'Q-child',decision_group_id:'QG-1',answer:null}),'新原话');
      assert.equal(documentDiscussionAnswer(project,{id:'Q-new',answer:'单题原话'}),'单题原话');
      assert.equal(documentDiscussionAnswer(project,{id:'Q-empty'}),'');
    ''')


def test_chapter_scope_excludes_other_chapters_even_when_clauses_and_titles_match():
    run_ts(LOAD + r'''
      const project={questions:['Q-first','Q-followup','Q-second','Q-old-version'].map(id=>({id,question:'同名问题',related_refs:['RULE-shared']})),items:[]};
      const tasks=[{id:'TASK-first',retry_input:{target:{kind:'document',id:'DOC-1',section_id:'S1'}},run_ids:['RUN-first']},
        {id:'TASK-followup',retry_input:{target:{kind:'question',id:'Q-first'}},run_ids:['RUN-followup']},
        {id:'TASK-second',retry_input:{target:{kind:'document',id:'DOC-1',section_id:'S2'}},run_ids:['RUN-second']}];
      const runs=[{id:'RUN-first',result_applied:true,discussion_target:{kind:'document',id:'DOC-1',section_id:'S1'},discussion_result:{question_ids:['Q-first']}},
        {id:'RUN-followup',result_applied:true,discussion_target:{kind:'question',id:'Q-first'},discussion_result:{question_ids:['Q-followup']}},
        {id:'RUN-second',result_applied:true,discussion_target:{kind:'document',id:'DOC-1',section_id:'S2'},discussion_result:{question_ids:['Q-second']}},
        {id:'RUN-old',result_applied:true,discussion_target:{kind:'document',id:'DOC-old',section_id:'S1'},discussion_result:{question_ids:['Q-old-version']}}];
      const frozen=JSON.stringify({project,tasks,runs});
      assert.deepEqual(documentDiscussionContext(project,tasks,runs,'DOC-1','S1').questions.map(q=>q.id),['Q-first','Q-followup']);
      assert.deepEqual(documentDiscussionContext(project,tasks,runs,'DOC-1','S2').questions.map(q=>q.id),['Q-second']);
      assert.deepEqual(documentDiscussionContext(project,tasks,runs,'DOC-1','unknown').questions,[]);
      const artifact={id:'DOC-1',content:{sections:[{section_id:'S1'},{section_id:'S2'}]}};
      assert.equal(documentDiscussionSectionForQuestion(project,tasks,runs,artifact,'Q-followup'),'S1');
      assert.equal(documentDiscussionSectionForQuestion(project,tasks,runs,artifact,'Q-old-version'),undefined);
      assert.equal(JSON.stringify({project,tasks,runs}),frozen);
    ''')


def test_unsent_drafts_keep_exact_words_and_base_without_crossing_projects_or_versions():
    run_ts(r'''
      import assert from 'node:assert/strict';import {build} from 'esbuild';
      const built=await build({entryPoints:['src/discussion-draft.ts'],bundle:true,write:false,format:'esm',platform:'node'});
      const {discussionDraftKey,readDiscussionDraft,writeDiscussionDraft}=await import('data:text/javascript;base64,'+Buffer.from(built.outputFiles[0].text).toString('base64'));
      const values=new Map(),storage={getItem:key=>values.get(key)||null,setItem:(key,value)=>values.set(key,value),removeItem:key=>values.delete(key)};
      const first=discussionDraftKey('P1','DOC1','S1','Q1');
      writeDiscussionDraft(storage,first,{text:'  用户尚未发送的原话\n待确认  ',base:'已保存的旧回答'});
      assert.deepEqual(readDiscussionDraft(storage,first),{text:'  用户尚未发送的原话\n待确认  ',base:'已保存的旧回答'});
      for(const parts of [['P2','DOC1','S1','Q1'],['P1','DOC2','S1','Q1'],['P1','DOC1','S2','Q1'],['P1','DOC1','S1','Q2']])assert.equal(readDiscussionDraft(storage,discussionDraftKey(...parts)),null);
      writeDiscussionDraft(storage,first,{text:'已确认原话',base:'已确认原话'});assert.equal(readDiscussionDraft(storage,first),null);
      storage.setItem(first,'{invalid');assert.equal(readDiscussionDraft(storage,first),null);
      assert.notEqual(discussionDraftKey('P:1','D','S'),discussionDraftKey('P','1:D','S'));
    ''')


def test_explicit_chapter_result_does_not_claim_unrelated_batch_review_questions():
    run_ts(LOAD + r'''
      const project={questions:[{id:'Q-reused'},{id:'Q-other'},{id:'Q-child',prior_question_id:'Q-reused'}],items:[]};
      const runs=[{id:'RUN-1',result_applied:true,
        discussion_target:{kind:'document',id:'DOC-1',section_id:'S1'},
        discussion_result:{question_ids:['Q-reused'],item_ids:[]},
        decision_review:{proposal_question_ids:['Q-other'],reviewed_question_ids:['Q-other']}}];
      assert.deepEqual(documentDiscussionContext(project,[],runs,'DOC-1','S1').questions.map(q=>q.id),['Q-reused','Q-child']);
      assert.deepEqual(documentDiscussionContext(project,[],runs,'DOC-1','S2').questions,[]);
    ''')
