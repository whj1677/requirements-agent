from tests.test_scope_frontend import run_ts


def test_detail_conflict_choices_preserve_unrelated_edits_and_clear_the_exact_gate():
    run_ts(r'''
      import assert from 'node:assert/strict';import {build} from 'esbuild';
      const out=await build({stdin:{contents:"export {batchConflicts,resolveBatchConflict} from './src/pm-prototype/prewrite-conflicts.ts'; export {mergeSuggestions} from './src/pm-prototype/refinement-sheet-state.ts'",resolveDir:process.cwd()},bundle:true,write:false,format:'esm',platform:'node'});
      const {batchConflicts,resolveBatchConflict,mergeSuggestions}=await import('data:text/javascript;base64,'+Buffer.from(out.outputFiles[0].text).toString('base64'));
      const d={schema_version:1,applicability:{},fields:[],interactions:[{id:'I1',name:'查询结果',preconditions:'旧条件',evidence:{}}],flow:{nodes:[],edges:[]},state_transitions:[]};
      const p={items:[{id:'R',kind:'requirement',title:'结果查询',detail_contract:d}],questions:[{id:'Q',question:'查询状态如何确定？',related_refs:['R']}],sources:[]};
      const base={revision:1,labels:{R:'结果查询',Q:'查询状态如何确定？'},profile:{},scope:['R'],values:{intent:'原目标'},states:{},rows:{R:{requirement_id:'R',statement:'正文',change_type:'new',behavior:{},acceptance_items:[],detail_contract:d}},answers:{Q:'原答案'},linked_clauses:{}};
      const mine=structuredClone(base),fresh=structuredClone(base);mine.rows.R.detail_contract.interactions[0].preconditions='我的明确条件';mine.values.intent='其他人工改动';fresh.rows.R.detail_contract.interactions[0].preconditions='AI明确条件';fresh.revision=2;
      const merged=mergeSuggestions(mine,base,fresh),before=structuredClone(merged),entries=batchConflicts(p,merged,fresh);
      assert.equal(entries.length,1);assert(entries[0].label.includes('查询结果'));assert(!entries[0].label.includes('I1'));
      const kept=resolveBatchConflict(p,merged,entries[0],false,fresh);
      assert.equal(kept.rows.R.detail_contract.interactions[0].preconditions,'我的明确条件');assert.equal(kept.rows.R.detail_conflicts.length,0);assert.equal(kept.merge_conflicts.length,0);
      const adopted=resolveBatchConflict(p,merged,entries[0],true,fresh);
      assert.equal(adopted.rows.R.detail_contract.interactions[0].preconditions,'AI明确条件');assert.equal(adopted.values.intent,'其他人工改动');assert.equal(adopted.rows.R.detail_conflicts.length,0);assert.equal(adopted.merge_conflicts.length,0);
      assert.deepEqual(merged,before,'a choice must not mutate the displayed source draft');
      mine.answers.Q='我的答案';fresh.answers.Q='AI答案';const answers=mergeSuggestions(mine,base,fresh);
      const entry=batchConflicts(p,answers,fresh).find(e=>e.kind==='answer');assert(entry);
      const chosen=resolveBatchConflict(p,answers,entry,true,fresh);assert.equal(chosen.answers.Q,'AI答案');assert.equal(chosen.rows.R.detail_conflicts.length,1,'another conflict cannot be cleared by this choice');
    ''')


def test_applied_candidate_handles_do_not_poison_a_second_confirmation():
    run_ts(r'''
      import assert from 'node:assert/strict';import {build} from 'esbuild';
      const out=await build({entryPoints:['src/pm-prototype/prewrite-batch-state.ts'],bundle:true,write:false,format:'esm',platform:'node'});
      const {refreshBatchCandidates}=await import('data:text/javascript;base64,'+Buffer.from(out.outputFiles[0].text).toString('base64'));
      const p={items:[{id:'R',confirmed_revisions:[{candidate_id:'OLD'}]},{id:'OLD',selection_status:'deferred'},{id:'NEW',selection_status:'candidate'},
        {id:'AC',confirmed_revisions:[{candidate_id:'ACOLD'}]},{id:'ACOLD',selection_status:'deferred'},{id:'ACNEW',selection_status:'candidate'}]};
      const s={rows:{R:{candidate_id:'OLD',statement:'保留的正文',behavior:{permissions:'其他手工修改'},acceptance_items:[{id:'AC',candidate_id:'ACOLD',statement:'保留的验收修改',invalid_candidate:{id:'ACOLD'}}]}}};
      const fresh={rows:{R:{candidate_id:'NEW',acceptance_items:[{id:'AC',candidate_id:'ACNEW'}]}}};
      const before=structuredClone(s),result=refreshBatchCandidates(p,s,fresh);
      assert.equal(result.rows.R.candidate_id,'NEW');assert.equal(result.rows.R.acceptance_items[0].candidate_id,'ACNEW');assert(!result.rows.R.acceptance_items[0].invalid_candidate);
      assert.equal(result.rows.R.behavior.permissions,'其他手工修改');assert.equal(result.rows.R.acceptance_items[0].statement,'保留的验收修改');assert.deepEqual(s,before);
      p.items[0].confirmed_revisions=[];assert.equal(refreshBatchCandidates(p,s,fresh).rows.R.candidate_id,'OLD','unapplied or rejected advice must not be silently relabelled');
    ''')


def test_only_current_explicit_review_draft_can_keep_reviewed_soft_unknowns():
    run_ts(r'''
      import assert from 'node:assert/strict';import {build} from 'esbuild';
      globalThis.sessionStorage={getItem:()=>null};
      const out=await build({entryPoints:['src/document-preparation.ts'],bundle:true,write:false,format:'esm',platform:'node',loader:{'.css':'empty'},external:['react','react/jsx-runtime']});
      const {createRequire}=await import('node:module');const require=createRequire(import.meta.url);
      const built=await build({entryPoints:['src/document-preparation.ts'],bundle:true,write:false,format:'cjs',platform:'node',loader:{'.css':'empty'},external:['react','react/jsx-runtime']});
      const mod={exports:{}};new Function('require','module','exports',built.outputFiles[0].text)(require,mod,mod.exports);const {documentPreparation}=mod.exports;
      const values=Object.fromEntries(['product','module','intent','current_state','users','value','change_scope','preserve_scope','out_of_scope','priority'].map(k=>[k,'合成已明确内容']));
      const p={id:'P',revision:1,items:[{id:'R',kind:'requirement',title:'结果查询',statement:'仅查询已有任务',change_type:'new',selection_status:'selected',applies_to:'to_be',behavior:{},related_refs:[]},{id:'AC',kind:'acceptance',statement:'显示返回结果',selection_status:'selected',applies_to:'to_be',related_refs:['R']}],questions:[{id:'Q',question:'允许哪些结果查询？',related_refs:['R'],answer:null,status:'open',blocking:true,prewrite_origin:'prewrite-1'}],sources:[],product_context:{...values,scope_ids:['R']},scope_profile:{scenario:'new_module',dependencies:'independent'},context_field_states:Object.fromEntries(Object.keys(values).map(k=>[k,{status:'known'}])),prewrite_review:{current:true,status:'needs_confirmation',findings:[{question_id:'Q',status:'open',related_refs:['R'],hard_block:false}]}};
      assert(documentPreparation(p,[]).issues.some(i=>i.question_id==='Q'));
      assert(!documentPreparation(p,[],true).issues.some(i=>i.question_id==='Q'));
      p.prewrite_review.current=false;assert(documentPreparation(p,[],true).issues.some(i=>i.question_id==='Q'));p.prewrite_review.current=true;
      p.prewrite_review.status='failed';assert(documentPreparation(p,[],true).issues.some(i=>i.question_id==='Q'));p.prewrite_review.status='needs_confirmation';
      const detail={schema_version:1,applicability:Object.fromEntries(['fields','interactions','flow','state_transitions'].map(k=>[k,{applicable:k==='fields',reason:'本例仅展示字段'}])),fields:[{id:'F',name:'派单结果',meaning:'当前结果',data_type:'enum',source:'系统记录',required:'只读',default_value:'不设默认',constraints:'按系统记录',visibility:'有权限可见',editability:'不可编辑'}],interactions:[],flow:{nodes:[],edges:[]},state_transitions:[]};
      p.items[0].detail_contract=detail;p.items[0].detail_required=true;
      assert(documentPreparation(p,[],true).issues.some(i=>i.path==='detail_contract.fields.F.enum_values'),'a missing field not covered by the review still needs preparation');
      const f={kind:'structure',path:'detail_contract.fields.F.enum_values',related_refs:['R'],status:'open',hard_block:false};p.prewrite_review.findings.push(f);
      assert(!documentPreparation(p,[],true).issues.some(i=>i.path==='detail_contract.fields.F.enum_values'));
      f.hard_block=true;assert(documentPreparation(p,[],true).issues.some(i=>i.path==='detail_contract.fields.F.enum_values'));
    ''')
