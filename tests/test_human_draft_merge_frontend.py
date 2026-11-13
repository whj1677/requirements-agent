"""Three-way draft merges preserve real edits without inventing conflicts."""

from tests.test_detail_editor_frontend import LOAD
from tests.test_scope_frontend import run_ts


INSTRUCTIONS = LOAD + r'''
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url);
const builtTable=await build({entryPoints:['src/pm-prototype/RequirementTable.tsx'],bundle:true,write:false,
  format:'cjs',platform:'node',jsx:'automatic',loader:{'.css':'empty'},external:['react']});
const tableModule={exports:{}};
new Function('require','module','exports',builtTable.outputFiles[0].text)(require,tableModule,tableModule.exports);
const {aiInstructions}=tableModule.exports;
const {initial,restoreSheet}=await load('src/pm-prototype/refinement-sheet-state.ts');
const inputProject=project(contract());
for(let n=2;n<=6;n++)inputProject.items.push({id:'AC-'+n,kind:'acceptance',statement:'已保存验收 '+n,
  applies_to:'to_be',selection_status:'selected',related_refs:['REQ-1']});
const baseline=initial(inputProject,[]);
const restored=restoreSheet(structuredClone(baseline),structuredClone(baseline),initial(inputProject,[]));
const purpose='请按当前资料重新起草，已答决定不重问。';
const submitted=sheet=>{
  const text=aiInstructions(purpose,sheet,baseline);
  assert(text.includes('本表未提交的人工修改'),'real editable changes must be carried into the request');
  return JSON.parse(text.slice(text.indexOf('{')));
};
'''


def test_ai_request_after_initial_restore_does_not_mislabel_unchanged_rows_as_manual_edits():
    run_ts(INSTRUCTIONS + r'''
      const before=JSON.stringify({inputProject,baseline,restored});
      assert.equal(restored.rows['REQ-1'].acceptance_items.length,6,'use the real restored six-acceptance row');
      assert.equal(aiInstructions(purpose,restored,baseline),purpose,
        'opening/restoring the table without editing adds no manual-modification claim or empty JSON');
      assert.equal(aiInstructions('',restored,baseline),'','there is no phantom source when purpose is empty');
      const metadata=structuredClone(restored),row=metadata.rows['REQ-1'];
      row.detail_conflicts=[];
      row.detail_suggestion={contract:contract(),candidate_id:'REQ-old-suggestion'};
      Object.assign(row.acceptance_items[0],{
        ai_suggested:true,applied_candidate_ids:['AC-previously-applied'],
        invalid_candidate:{id:'AC-expired',latest_statement:'服务端原文'},conflict_statement:'供比较的原文',
        suggestions:[{candidate_id:'AC-navigation-only',statement:'尚未选择的建议'}],
      });
      assert.equal(aiInstructions(purpose,metadata,baseline),purpose,
        'derived conflict, suggestion and acceptance navigation metadata are not human business edits');
      assert.equal(JSON.stringify({inputProject,baseline,restored}),before,'instruction construction cannot rewrite saved or restored data');
    ''')


def test_ai_request_carries_only_actual_scope_profile_edits():
    run_ts(INSTRUCTIONS + r'''
      assert.equal(aiInstructions(purpose,restored,baseline),purpose);
      const mine=structuredClone(restored);
      mine.profile={
        scenario:baseline.profile.scenario==='modify_existing'?'new_product':'modify_existing',
        dependencies:baseline.profile.dependencies==='shared'?'independent':'shared',
      };
      mine.answers.Q1='与场景同时保留的人工答案';
      const before=JSON.stringify({mine,baseline});
      const edits=submitted(mine);
      assert.deepEqual(edits.scope_profile,mine.profile);
      assert.equal(edits.answers.Q1,mine.answers.Q1);
      assert.equal(JSON.stringify({mine,baseline}),before);

      const profileOnly=structuredClone(restored);
      profileOnly.profile=mine.profile;
      assert.deepEqual(submitted(profileOnly).scope_profile,mine.profile,
        'a profile-only edit must be sufficient to include the draft context');

      const withoutBase=JSON.parse(aiInstructions(purpose,restored,null).split('本表未提交的人工修改（仅供起草参考，仍待确认）：')[1]);
      assert.deepEqual(withoutBase.scope_profile,restored.profile);

      const legacy=structuredClone(restored),legacyBase=structuredClone(baseline);
      delete legacy.profile;delete legacyBase.profile;
      assert.equal(aiInstructions(purpose,legacy,legacyBase),purpose);
      legacy.values.intent+='人工补充';
      assert(!('scope_profile' in submitted(legacy)),
        'legacy sheets without profiles must not emit an empty scope profile');
    ''')


def test_ai_request_keeps_real_row_acceptance_candidate_and_detail_edits():
    run_ts(INSTRUCTIONS + r'''
      const cases=[
        ['statement',row=>row.statement='人工修改的需求正文',row=>assert.equal(row.statement,'人工修改的需求正文')],
        ['behavior',row=>row.behavior.flow='人工新增的可观察流程',row=>assert.equal(row.behavior.flow,'人工新增的可观察流程')],
        ['change_type',row=>row.change_type='modified',row=>assert.equal(row.change_type,'modified')],
        ['acceptance add',row=>row.acceptance_items.push({statement:'人工新增的验收结果'}),row=>{
          assert.equal(row.acceptance_items.length,7);assert.equal(row.acceptance_items.at(-1).statement,'人工新增的验收结果');}],
        ['acceptance delete',row=>row.acceptance_items.shift(),row=>{
          assert.equal(row.acceptance_items.length,5);assert(!row.acceptance_items.some(item=>item.id==='AC-1'));}],
        ['acceptance text',row=>row.acceptance_items[0].statement='人工修改的验收正文',row=>assert.equal(row.acceptance_items[0].statement,'人工修改的验收正文')],
        ['detail business edit',row=>row.detail_contract.fields[0].meaning='人工修改字段含义',row=>assert.equal(row.detail_contract.fields[0].meaning,'人工修改字段含义')],
      ];
      for(const [name,edit,check] of cases){
        const mine=structuredClone(restored);edit(mine.rows['REQ-1']);
        const before=JSON.stringify(mine),edits=submitted(mine);
        assert.equal(edits.requirements.length,1,name+' remains a real requirement edit');
        assert.equal(edits.requirements[0].requirement_id,'REQ-1');
        check(edits.requirements[0]);
        assert.equal(JSON.stringify(mine),before,name+' is serialized without mutating the browser draft');
      }
      const chosen=structuredClone(restored);
      chosen.rows['REQ-1'].acceptance_items[0].candidate_id='AC-explicitly-chosen';
      const choiceMessage=aiInstructions(purpose,chosen,baseline);
      assert(choiceMessage.includes('AC-explicitly-chosen'));
      assert(choiceMessage.includes(chosen.rows['REQ-1'].acceptance_items[0].id));
      assert(!choiceMessage.includes('本表未提交的人工修改'),
        'a version-only choice is navigation intent, not a whole human business edit');
      const {requirementPayload}=await load('src/pm-prototype/refinement-sheet-state.ts');
      assert.equal(requirementPayload(chosen.rows['REQ-1']).acceptance_items[0].candidate_id,'AC-explicitly-chosen',
        'explicit confirmation retains the selected candidate identity');
      const unknownBase=JSON.parse(aiInstructions(purpose,restored,null).split('本表未提交的人工修改（仅供起草参考，仍待确认）：')[1]);
      assert.equal(unknownBase.requirements.length,1,'without a comparison baseline, preserve current content conservatively');
      assert.equal(unknownBase.requirements[0].statement,restored.rows['REQ-1'].statement);
      assert.equal(unknownBase.requirements[0].acceptance_items.length,6);
      assert.equal(unknownBase.summary.intent,restored.values.intent);
    ''')


def test_metadata_changes_do_not_conflict_or_detach_evidence():
    run_ts(LOAD + r'''
      const base=contract(),mine=structuredClone(base),remote=structuredClone(base);
      const sourceA={source_id:'S1',excerpt_id:'E1'},sourceB={source_id:'S2',excerpt_id:'E2'};
      base.fields[0].evidence.constraints={status:'source',source_refs:[sourceA],reason:'原始依据'};
      mine.fields[0].evidence.constraints={status:'source',source_refs:[sourceA],reason:'本地补充说明'};
      remote.fields[0].evidence.constraints={status:'source',source_refs:[sourceA,sourceB],reason:'最新补充说明'};
      const merged=mergeDetailContract(mine,base,remote);
      assert.equal(merged.value.fields[0].constraints,base.fields[0].constraints);
      assert.deepEqual(merged.conflicts,[],'evidence metadata alone is not a business-value conflict');
      const evidence=merged.value.fields[0].evidence.constraints;
      assert.equal(evidence.status,'source');
      assert(evidence.source_refs.some(ref=>ref.source_id==='S1'&&ref.excerpt_id==='E1'));
      assert(evidence.source_refs.some(ref=>ref.source_id==='S2'&&ref.excerpt_id==='E2'));
    ''')


def test_untouched_ai_version_updates_and_manual_stable_ids_survive():
    run_ts(LOAD + r'''
      const base=contract(),mine=structuredClone(base),remote=structuredClone(base);
      mine.fields.push({id:'FD-manual',name:'人工新增'});
      remote.fields[0].meaning='AI 新版本业务含义';
      remote.fields.push({id:'FD-new-ai',name:'AI 新增'});
      const merged=mergeDetailContract(mine,base,remote);
      assert.equal(merged.value.fields.find(row=>row.id==='FD-level').meaning,'AI 新版本业务含义');
      assert.deepEqual(new Set(merged.value.fields.map(row=>row.id)),new Set(['FD-level','FD-manual','FD-new-ai']));
      assert.deepEqual(merged.conflicts,[]);

      const before=contract();before.fields.push({id:'FD-delete',name:'人工移除'});
      const local=structuredClone(before),latest=structuredClone(before);
      local.fields=local.fields.filter(row=>row.id!=='FD-delete');
      latest.fields.push({id:'FD-new-ai',name:'新建议'});
      const removed=mergeDetailContract(local,before,latest);
      assert.deepEqual(new Set(removed.value.fields.map(row=>row.id)),new Set(['FD-level','FD-new-ai']));
      assert.deepEqual(removed.conflicts,[]);
    ''')


def test_real_concurrent_edit_is_reviewable_and_resolves_without_stale_summary():
    run_ts(LOAD + r'''
      const {initial,mergeSuggestions}=await load('src/pm-prototype/refinement-sheet-state.ts');
      const {batchConflicts,resolveBatchConflict}=await load('src/pm-prototype/prewrite-conflicts.ts');
      const p=project(contract()),base=initial(p,[]),mine=structuredClone(base),latest=structuredClone(base);
      mine.rows['REQ-1'].detail_contract.fields[0].meaning='人工业务修改';
      latest.rows['REQ-1'].detail_contract.fields[0].meaning='AI 业务修改';
      latest.rows['REQ-1'].detail_contract.fields[0].evidence.meaning={status:'source',source_refs:[{source_id:'S2',excerpt_id:'E2'}]};
      latest.revision=2;
      const merged=mergeSuggestions(mine,base,latest);
      assert.equal(merged.rows['REQ-1'].detail_contract.fields[0].meaning,'人工业务修改');
      const entries=batchConflicts(p,merged,latest).filter(entry=>entry.kind==='detail');
      assert.equal(entries.length,1);
      assert.deepEqual([entries[0].local,entries[0].remote],['人工业务修改','AI 业务修改']);
      const resolved=resolveBatchConflict(p,merged,entries[0],false,latest);
      assert.equal(resolved.rows['REQ-1'].detail_contract.fields[0].meaning,'人工业务修改');
      assert.deepEqual(batchConflicts(p,resolved,latest),[]);
      assert.deepEqual(resolved.merge_conflicts,[],'a resolved conflict must not leave a summary');
      assert.equal(merged.rows['REQ-1'].detail_conflicts.length,1,'resolution must not mutate the saved input');
      const accepted=resolveBatchConflict(p,merged,entries[0],true,latest);
      assert.equal(accepted.rows['REQ-1'].detail_contract.fields[0].meaning,'AI 业务修改');
      assert.deepEqual(accepted.rows['REQ-1'].detail_contract.fields[0].evidence.meaning,
        {status:'source',source_refs:[{source_id:'S2',excerpt_id:'E2'}]},'the accepted value must keep its own evidence');
    ''')


def test_converged_remote_content_clears_obsolete_conflict():
    run_ts(LOAD + r'''
      const {initial,mergeSuggestions}=await load('src/pm-prototype/refinement-sheet-state.ts');
      const p=project(contract()),base=initial(p,[]),mine=structuredClone(base),remote=structuredClone(base);
      mine.rows['REQ-1'].detail_contract.fields[0].meaning='人工修改';
      remote.rows['REQ-1'].detail_contract.fields[0].meaning='其他修改';
      const conflicted=mergeSuggestions(mine,base,remote);
      assert.equal(conflicted.rows['REQ-1'].detail_conflicts.length,1);
      const converged=structuredClone(remote);
      converged.rows['REQ-1'].detail_contract.fields[0].meaning='人工修改';
      const merged=mergeSuggestions(conflicted,remote,converged);
      assert.equal(merged.rows['REQ-1'].detail_contract.fields[0].meaning,'人工修改');
      assert.deepEqual(merged.rows['REQ-1'].detail_conflicts,[]);
      assert.deepEqual(merged.merge_conflicts,[],'obsolete summary should disappear when content converges');
    ''')
