"""AI draft refreshes preserve manual answers without hiding saved-answer conflicts."""
from tests.test_scope_frontend import run_ts


SETUP = r'''
import assert from 'node:assert/strict';
import {build} from 'esbuild';
const load=async entry=>{
  const built=await build({entryPoints:[entry],bundle:true,write:false,format:'esm',platform:'node'});
  return import('data:text/javascript;base64,'+Buffer.from(built.outputFiles[0].text).toString('base64'));
};
const {initial,mergeSuggestions,restoreSheet,pendingSheetAnswers}=await load('src/pm-prototype/refinement-sheet-state.ts');
const {emptyDetailContract}=await load('src/pm-prototype/detail-contract.ts');
const oldKeys=new Set(['revision','values','states','profile','scope','scope_excluded_ids','rows','answers',
  'answer_implementations','linked_clauses','merge_conflicts','labels']);
const manual='Only the two described operators are in scope; no rule is inferred for other roles.';
const project=(revision,draft='AI proposal A',saved='')=>({
  id:'P-synthetic-answer-banner',revision,
  product_context:{intent:'查看既有告警',users:'值班员',scope_ids:['REQ-1']},
  scope_profile:{scenario:'modify_existing',dependencies:'unknown'},
  items:[{id:'REQ-1',kind:'requirement',title:'查看告警',statement:'既有业务原句',applies_to:'to_be',selection_status:'selected',
    change_type:'modified',behavior:{actor:'值班员'},related_refs:[],detail_contract:emptyDetailContract()},
    {id:'AC-1',kind:'acceptance',statement:'查看后结束',applies_to:'to_be',selection_status:'selected',related_refs:['REQ-1']}],
  questions:[{id:'Q1',question:'本期操作者范围',kind:'decision',status:'open',answer:saved,related_refs:['REQ-1']}],
  refinement_completion:{mode:'all',answers:{Q1:{text:draft,basis:'recommendation'}},scope_ids:['REQ-1']},
});
const fresh=p=>initial(p,[]);
const mineOf=base=>{const s=structuredClone(base);s.answers.Q1=manual;return s;};
const answerConflict=s=>(s.merge_conflicts||[]).some(message=>message.includes('回答'));
function legacy(sheet){
  const value=structuredClone(sheet);for(const key of Object.keys(value))if(!oldKeys.has(key))delete value[key];return value;
}
const p1=project(1),p2=project(2,'AI proposal B'),base=fresh(p1),mine=mineOf(base),latest=fresh(p2);
'''


def test_ai_only_rephrasing_preserves_manual_scope_detail_and_untouched_updates():
    run_ts(SETUP + r'''
      const before=JSON.stringify({p1,p2,base,mine,latest}),merged=mergeSuggestions(mine,base,latest);
      assert.equal(merged.answers.Q1,manual);assert.equal(answerConflict(merged),false);
      assert.deepEqual(merged.scope,mine.scope);assert.deepEqual(merged.profile,mine.profile);
      assert.deepEqual(merged.rows['REQ-1'].detail_contract,mine.rows['REQ-1'].detail_contract);
      assert.equal(JSON.stringify({p1,p2,base,mine,latest}),before);
      const untouched=mergeSuggestions(structuredClone(base),base,latest);
      assert.equal(untouched.answers.Q1,'AI proposal B');assert.equal(answerConflict(untouched),false);
      const third=fresh(project(3,'AI proposal C')),again=mergeSuggestions(merged,latest,third);
      assert.equal(again.answers.Q1,manual);assert.equal(answerConflict(again),false);
    ''')


def test_saved_answer_changes_clearing_and_non_ai_remote_edits_remain_real_conflicts():
    run_ts(SETUP + r'''
      const cases=[
        [mine,base,fresh(project(2,'AI B','Saved remote human decision'))],
        ...[['Saved human decision A','Saved human decision B'],['Saved human decision A','']].map(([oldValue,newValue])=>{
          const b=fresh(project(1,'AI A',oldValue));return [mineOf(b),b,fresh(project(2,'AI B',newValue))];
        }),
      ];
      const external=structuredClone(latest);external.answers.Q1='An actual external draft edit';
      cases.push([mine,base,external]);
      for(const [local,before,remote] of cases){
        const merged=mergeSuggestions(local,before,remote);
        assert.equal(merged.answers.Q1,manual);assert.equal(answerConflict(merged),true);
      }
    ''')


def test_legacy_and_unresolved_or_unrelated_conflicts_are_conservatively_preserved():
    run_ts(SETUP + r'''
      for(const [local,before,remote] of [[legacy(mine),legacy(base),latest],[mine,base,legacy(latest)],[mine,null,latest]]){
        const merged=restoreSheet(local,before,remote);
        assert.equal(merged.answers.Q1,manual);assert.equal(answerConflict(merged),true);
      }
      const conflicted=structuredClone(mine);
      conflicted.merge_conflicts=['真实其他位置正文冲突','旧回答冲突无法证明来源'];
      const kept=mergeSuggestions(conflicted,base,latest);
      assert(kept.merge_conflicts.includes('真实其他位置正文冲突'));
      assert(kept.merge_conflicts.includes('旧回答冲突无法证明来源'));
      const local=structuredClone(mine),remote=structuredClone(latest);
      local.rows['REQ-1'].statement='人工正文';remote.rows['REQ-1'].statement='真正远端正文';
      local.profile={scenario:'new_module',dependencies:'shared'};
      remote.profile={scenario:'new_product',dependencies:'independent'};
      const actual=mergeSuggestions(local,base,remote);
      assert.equal(actual.rows['REQ-1'].statement,'人工正文');assert.deepEqual(actual.profile,local.profile);
      assert(actual.merge_conflicts.some(message=>message.includes('需求正文')));
      assert(actual.merge_conflicts.some(message=>message.includes('需求类型')));
    ''')


def test_ai_only_update_still_invalidates_answer_witness_when_target_text_changes():
    run_ts(SETUP + r'''
      const local=structuredClone(mine),remote=structuredClone(latest),remoteProject=structuredClone(p2);
      local.answer_implementations={Q1:[{item_id:'REQ-1',field:'statement',quote:'既有业务原句'}]};
      remote.rows['REQ-1'].statement='已改业务正文';remoteProject.items[0].statement='已改业务正文';
      const merged=mergeSuggestions(local,base,remote);
      assert.equal(merged.answers.Q1,manual);assert.equal(answerConflict(merged),false);
      assert.deepEqual(merged.answer_implementations.Q1||[],[]);
      assert.equal(pendingSheetAnswers(remoteProject,merged).length,1);
    ''')


def test_provenance_is_ui_metadata_and_never_enters_confirmation_or_manual_instruction():
    run_ts(SETUP + r'''
      import {createRequire} from 'node:module';
      const require=createRequire(import.meta.url);
      const built=await build({entryPoints:['src/pm-prototype/RequirementTable.tsx'],bundle:true,write:false,
        format:'cjs',platform:'node',jsx:'automatic',loader:{'.css':'empty'},external:['react']});
      const module={exports:{}};
      new Function('require','module','exports',built.outputFiles[0].text)(require,module,module.exports);
      const {aiInstructions}=module.exports;
      const {confirmationPayload}=await load('src/pm-prototype/prewrite-batch-state.ts');
      const provenanceKeys=Object.keys(latest).filter(key=>!oldKeys.has(key));
      assert(provenanceKeys.length>0,'snapshots explicitly retain answer origins');
      const metadataOnly=structuredClone(base);
      for(const key of provenanceKeys)metadataOnly[key]=structuredClone(latest[key]);
      assert.equal(aiInstructions('请继续起草',metadataOnly,base),'请继续起草');
      const merged=mergeSuggestions(mine,base,latest),text=aiInstructions('请继续起草',merged,latest);
      const edits=JSON.parse(text.slice(text.indexOf('{')));
      assert.equal(edits.answers.Q1,manual);
      assert.deepEqual(Object.keys(edits).sort(),['answers','linked_clauses','requirements','summary']);
      const payload=confirmationPayload(p2,merged);
      assert.deepEqual(Object.keys(payload).sort(),['answer_implementations','answers','context','expected_revision','linked_clauses','review_confirmed','rows']);
      for(const key of provenanceKeys)assert(!JSON.stringify(payload).includes('"'+key+'"'));
    ''')
