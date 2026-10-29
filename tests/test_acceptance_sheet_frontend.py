"""Executable frontend state regression: model replies stay visible without losing edits."""
from tests.test_scope_frontend import run_ts

LOAD = r'''
import assert from 'node:assert/strict';
import {build} from 'esbuild';
const built=await build({entryPoints:['src/pm-prototype/refinement-sheet-state.ts'],bundle:true,write:false,format:'esm',platform:'node'});
const {initial,mergeSuggestions,mergeAcceptanceItems,acceptancePayload,restoreSheet,resolveExpiredAcceptance,clearAnswer,linkedClauseItems,answeredLinkedClauses,linkedClausePayload}=await import('data:text/javascript;base64,'+Buffer.from(built.outputFiles[0].text).toString('base64'));
const req={id:'REQ-1',kind:'requirement',title:'电价模板查看',statement:'打开电价模板列表',applies_to:'to_be',selection_status:'selected',change_type:'unspecified',related_refs:[],behavior:{}};
const ac=(id,statement,status='candidate',extra={})=>({id,kind:'acceptance',statement,applies_to:'to_be',selection_status:status,related_refs:['REQ-1'],...extra});
const project=(items=[],revision=1)=>({id:'P-sheet',revision,items:[req,...items],questions:[],product_context:{intent:'查看电价模板',users:'管理员',scope_ids:['REQ-1']},scope_profile:{scenario:'new_product',dependencies:'independent'}});
const confirmed=[{requirement_id:'REQ-1',current:true}];
'''


def test_explicit_completion_fills_review_drafts_but_preserves_human_answers():
    run_ts(LOAD+r'''
      const p=project([],2);p.product_context.intent='';p.context_field_states={intent:{status:'unknown'}};
      p.product_context_proposal={intent:'建议：便于值班人员核对电价模板。'};
      p.product_context_proposal_evidence={intent:{uncertain:true}};
      p.questions=[{id:'Q1',question:'权限采用什么方案？',answer:null,related_refs:['REQ-1']},
        {id:'Q2',question:'是否保留原筛选？',answer:'人工确认保留',related_refs:['REQ-1']}];
      const before=initial(p,confirmed);
      p.scope_profile={scenario:'modify_existing',dependencies:'unknown'};
      p.refinement_completion={mode:'required',run_id:'RUN-fill',answers:{Q1:{text:'建议：沿用既有授权。'},Q2:{text:'建议：清空筛选。'}},change_types:{'REQ-1':'modified'},scope_ids:['REQ-1']};
      p.refinement_completion.profile={scenario:'new_module',dependencies:'shared'};
      const snapshot=JSON.stringify(p),draft=initial(p,confirmed);
      assert.equal(draft.values.intent,p.product_context_proposal.intent);
      assert.equal(draft.states.intent.status,'known','a concrete unconfirmed proposal can be reviewed without retyping');
      assert.equal(draft.answers.Q1,'建议：沿用既有授权。');assert.equal(draft.answers.Q2,'人工确认保留');
      assert.equal(draft.rows['REQ-1'].change_type,'modified');
      assert.deepEqual(draft.profile,{scenario:'modify_existing',dependencies:'shared'},'only the missing part of the profile is proposed');
      const mine=structuredClone(before);mine.answers.Q1='浏览器里尚未确认的人工修改';
      const merged=mergeSuggestions(mine,before,draft);assert.equal(merged.answers.Q1,mine.answers.Q1);
      assert.equal(JSON.stringify(p),snapshot,'draft fill cannot save an answer or confirmation');
    ''')


def test_completion_candidate_can_be_reviewed_against_confirmed_row_without_overwriting_it():
    run_ts(LOAD+r'''
      const p=project([{...req,id:'REV-fill',target_item_id:'REQ-1',statement:'建议：打开模板后显示地区。',selection_status:'candidate'}],3);
      const old=initial(p,confirmed);assert.equal(old.rows['REQ-1'].statement,req.statement);
      p.refinement_completion={mode:'all',candidate_ids:['REV-fill'],answers:{},change_types:{}};
      const draft=initial(p,confirmed);assert.equal(draft.rows['REQ-1'].statement,'建议：打开模板后显示地区。');
      assert.equal(draft.rows['REQ-1'].candidate_id,'REV-fill');assert.equal(p.items[0].statement,req.statement);
      p.items[1].selection_status='deferred';assert.equal(initial(p,confirmed).rows['REQ-1'].statement,req.statement);
    ''')


def test_unclassified_business_supplement_preserves_existing_human_change_choice():
    run_ts(LOAD+r'''
      for(const change_type of ['new','modified','preserved']){
        const p=project([{...req,id:'REV-1',target_item_id:'REQ-1',selection_status:'candidate',
          statement:'补齐后的需求正文',change_type:'unspecified',scope_evidence:[]}],2);
        p.items[0]={...p.items[0],change_type};
        const before=JSON.stringify(p),row=initial(p,[]).rows['REQ-1'];
        assert.equal(row.statement,'补齐后的需求正文');
        assert.equal(row.candidate_id,'REV-1');
        assert.equal(row.change_type,change_type,'an unclassified draft must preserve the saved human choice');
        assert.equal(JSON.stringify(p),before);
      }
    ''')


def test_confirmed_requirement_keeps_new_acceptance_candidates_visible():
    run_ts(LOAD+r'''
      const p=project([ac('AC-1','既有验收','selected'),ac('AC-NEW','建议：打开列表后显示模板'),ac('AC-REJECTED','不应出现','rejected'),ac('AC-OLD','不应出现','candidate',{superseded_by:'AC-NEW'})]);
      const before=JSON.stringify(p),row=initial(p,confirmed).rows['REQ-1'];
      assert.deepEqual(row.acceptance_items.map(a=>a.id),['AC-1','AC-NEW']);
      assert.equal(row.acceptance_items[1].ai_suggested,true);
      assert.equal(JSON.stringify(p),before);
    ''')


def test_revised_acceptance_requires_explicit_choice_and_uses_original_identity():
    run_ts(LOAD+r'''
      const p=project([ac('AC-1','已确认验收','selected'),ac('AC-REV','建议：修订验收','candidate',{target_item_id:'AC-1',action:'revise'})]);
      const draft=initial(p,confirmed).rows['REQ-1'].acceptance_items[0];
      assert.equal(draft.statement,'已确认验收');
      assert.equal(draft.candidate_id,undefined);
      assert.deepEqual(draft.suggestions,[{candidate_id:'AC-REV',statement:'建议：修订验收'}]);
      assert.deepEqual(acceptancePayload([draft]),[{id:'AC-1',statement:'已确认验收'}]);
      const used={...draft,...draft.suggestions[0]};
      assert.deepEqual(acceptancePayload([used]),[{id:'AC-1',statement:'建议：修订验收',candidate_id:'AC-REV'}]);
      used.statement='人工调整后的验收';
      assert.equal(acceptancePayload([used])[0].candidate_id,'AC-REV');
    ''')


def test_new_model_response_populates_empty_acceptance_while_manual_cells_survive():
    run_ts(LOAD+r'''
      const base=initial(project(),[]),mine=structuredClone(base);
      mine.rows['REQ-1'].change_type='new';mine.values.users='人工填写的运维人员';
      const latest=initial(project(Array.from({length:4},(_,n)=>ac('AC-'+n,'验收 '+n)),2),[]);
      const merged=mergeSuggestions(mine,base,latest);
      assert.equal(merged.revision,2);assert.equal(merged.rows['REQ-1'].change_type,'new');
      assert.equal(merged.values.users,'人工填写的运维人员');
      assert.equal(merged.rows['REQ-1'].acceptance_items.length,4);
      assert.deepEqual(merged.merge_conflicts,[]);
      assert.equal(mine.rows['REQ-1'].acceptance_items.length,0);
    ''')


def test_merge_preserves_individual_edits_deletion_and_manual_addition_without_hiding_new_ai():
    run_ts(LOAD+r'''
      const base=[{id:'AC-1',statement:'旧1'},{id:'AC-2',statement:'旧2'}];
      const mine=[{id:'AC-1',statement:'人工修改1'},{statement:'人工新增'}];
      const latest=[...base,{id:'AC-3',statement:'AI 新增'}],conflicts=[];
      const merged=mergeAcceptanceItems(mine,base,latest,conflicts,'模板');
      assert.deepEqual(merged.map(a=>a.statement),['人工修改1','AI 新增','人工新增']);
      assert(!merged.some(a=>a.id==='AC-2'));assert.deepEqual(conflicts,[]);
    ''')


def test_concurrent_edits_are_retained_and_conflicts_are_visible():
    run_ts(LOAD+r'''
      const base=initial(project([ac('AC-1','原文','selected')]),[]),mine=structuredClone(base);
      mine.rows['REQ-1'].acceptance_items[0].statement='本地人工修改';mine.values.users='本地用户';
      const remote=project([ac('AC-1','另一页面已保存','selected')],2);remote.product_context.users='另一页面用户';
      const merged=mergeSuggestions(mine,base,initial(remote,[]));
      const cell=merged.rows['REQ-1'].acceptance_items[0];
      assert.equal(cell.statement,'本地人工修改');assert.equal(cell.conflict_statement,'另一页面已保存');
      assert.equal(merged.values.users,'本地用户');assert(merged.merge_conflicts.length>=2);
      assert(restoreSheet(merged,initial(remote,[]),initial(remote,[])).merge_conflicts.length>=2,'refresh cannot silently acknowledge conflicts');
    ''')


def test_restored_old_or_same_revision_browser_draft_merges_new_suggestions():
    run_ts(LOAD+r'''
      for(const revision of [1,2]){
        const base=initial(project([],revision),[]),saved=structuredClone(base);
        saved.rows['REQ-1'].change_type='new';saved.rows['REQ-1'].acceptance_items=[{statement:''}];
        const latest=initial(project([ac('AC-NEW','可验证的 AI 建议')],2),[]);
        const restored=restoreSheet(saved,base,latest);
        assert.equal(restored.rows['REQ-1'].change_type,'new');
        assert.deepEqual(restored.rows['REQ-1'].acceptance_items.map(a=>a.id),['AC-NEW']);
        assert.equal(restored.revision,2);assert.deepEqual(restored.merge_conflicts,[]);
      }
      const missingBase=initial(project(),[]);missingBase.values.users='本地输入';
      const restored=restoreSheet(missingBase,null,initial(project([ac('AC-NEW','新建议')],2),[]));
      assert.equal(restored.values.users,'本地输入');assert.equal(restored.rows['REQ-1'].acceptance_items.length,1);
      assert(restored.merge_conflicts.length>0,'missing base cannot silently overwrite a nonempty value');
    ''')


def test_expired_candidate_binding_is_cleared_only_for_recorded_matching_application():
    run_ts(LOAD+r'''
      const old=project([ac('AC-1','原文','selected'),ac('AC-REV','AI 修订','candidate',{target_item_id:'AC-1',action:'revise'})]);
      const base=initial(old,confirmed),mine=structuredClone(base);
      Object.assign(mine.rows['REQ-1'].acceptance_items[0],{statement:'AI 修订',candidate_id:'AC-REV'});
      const applied=project([ac('AC-1','AI 修订','selected',{confirmed_revisions:[{candidate_id:'AC-REV'}]}),ac('AC-REV','AI 修订','deferred',{target_item_id:'AC-1',action:'revise'})],2);
      const alreadySaved=mergeSuggestions(mine,base,initial(applied,confirmed)).rows['REQ-1'].acceptance_items[0];
      assert.equal(alreadySaved.candidate_id,undefined);assert.equal(alreadySaved.invalid_candidate,undefined);
      assert.deepEqual(acceptancePayload([alreadySaved]),[{id:'AC-1',statement:'AI 修订'}]);
      for(const status of ['deferred','rejected','excluded']){
        const latest=project([ac('AC-1','原文','selected'),ac('AC-REV','AI 修订',status,{target_item_id:'AC-1',action:'revise'})],2);
        const kept=mergeSuggestions(mine,base,initial(latest,confirmed)).rows['REQ-1'].acceptance_items[0];
        assert.equal(kept.statement,'AI 修订');assert.equal(kept.candidate_id,'AC-REV');assert.equal(kept.invalid_candidate.id,'AC-REV');
        const manual=resolveExpiredAcceptance(kept,'manual');assert.equal(manual.statement,'AI 修订');assert.equal(manual.candidate_id,undefined);assert.equal(manual.invalid_candidate,undefined);
        const saved=resolveExpiredAcceptance(kept,'saved');assert.equal(saved.statement,'原文');assert.equal(saved.candidate_id,undefined);
      }
      const replaced=initial(project([ac('AC-1','原文','selected'),ac('AC-REV','AI 修订','candidate',{target_item_id:'AC-1',action:'revise',superseded_by:'AC-NEXT'}),ac('AC-NEXT','新建议','candidate',{target_item_id:'AC-1',action:'revise'})],2),confirmed);
      const outdated=mergeSuggestions(mine,base,replaced).rows['REQ-1'].acceptance_items[0];
      assert(outdated.invalid_candidate);assert.equal(outdated.statement,'AI 修订');assert.deepEqual(outdated.suggestions.map(s=>s.candidate_id),['AC-NEXT']);
      // Same text without evidence of applying this candidate still needs a decision.
      const merelySame=initial(project([ac('AC-1','AI 修订','selected')],2),confirmed);
      assert(mergeSuggestions(mine,base,merelySame).rows['REQ-1'].acceptance_items[0].invalid_candidate);
      const different=structuredClone(applied);different.items[0].title='另一标题';different.items[1].statement='异文';
      const conflict=mergeSuggestions(mine,base,initial(different,confirmed));
      assert(conflict.rows['REQ-1'].acceptance_items[0].invalid_candidate);
      assert(conflict.merge_conflicts.some(message=>message.includes('另一标题')),'conflict identifies the requirement');
    ''')


def test_missing_acceptance_explains_full_draft_entry_and_partial_result_without_claiming_ready():
    run_ts(r'''
      import assert from 'node:assert/strict';
      import {build} from 'esbuild';
      import {createRequire} from 'node:module';
      const require=createRequire(import.meta.url),React=require('react'),{renderToStaticMarkup}=require('react-dom/server');
      const built=await build({entryPoints:['src/pm-prototype/RequirementTable.tsx'],bundle:true,write:false,format:'cjs',platform:'node',jsx:'automatic',loader:{'.css':'empty'},external:['react','react-dom/server']});
      const module={exports:{}};new Function('require','module','exports',built.outputFiles[0].text)(require,module,module.exports);
      const {DraftPreparationNotice,aiInstructions}=module.exports;
      const draft={values:{intent:'已有目标'},rows:{},answers:{}};
      assert.equal(aiInstructions('',draft,structuredClone(draft)), '', 'unchanged sheet must not fabricate a user source');
      assert(aiInstructions('',{...draft,values:{intent:'我的真实修改'}},draft).includes('我的真实修改'));
      const missing=[{id:'REQ-1',title:'电价模板列表'},{id:'REQ-2',title:'电价模板导入'}];
      const render=preparation=>renderToStaticMarkup(React.createElement(DraftPreparationNotice,{missing,preparation}));
      const pending=render(null);assert(pending.includes('2 条需求待补充验收内容'));assert(pending.includes('一次起草整表'));
      const partial=render({status:'ready_with_limits',reason:'有一部分模型结果未能保存为有效候选'});
      assert(partial.includes('本次已尝试起草'));assert(partial.includes('电价模板列表、电价模板导入'));assert(partial.includes('有一部分模型结果未能保存为有效候选'));
      assert(partial.includes('不会自动重复调用'));assert(!partial.includes('完整草稿已完成'));
      const cleared=render({status:'ready_with_limits',reason:'本次结果保留',draft_coverage:{missing_acceptance_ids:[]}});
      assert(cleared.includes('这些单元格当前为空'));assert(!cleared.includes('仍未形成可用建议'));
      assert.equal(renderToStaticMarkup(React.createElement(DraftPreparationNotice,{missing:[],preparation:null})), '');
    ''')


def test_answered_linked_rules_are_deduplicated_explicit_and_never_inherit_answer_text():
    run_ts(LOAD+r'''
      const rule={id:'RULE-1',kind:'rule',title:'电价模板可见范围',statement:'业主只能看到自己及中车运维建立的模板',applies_to:'to_be',selection_status:'candidate',behavior:{permissions:'保留模板账号权限'},source_refs:[{source_id:'SRC-1',excerpt_id:'EX-1'}]};
      const p=project([rule]);p.questions=[{id:'Q-1',question:'收益可见范围？',blocking:false,related_refs:['REQ-1','RULE-1']},{id:'Q-2',question:'第二个相关问题',blocking:false,related_refs:['RULE-1']}];
      const before=JSON.stringify(p),sheet=initial(p,[]);
      assert.deepEqual(linkedClausePayload(p,sheet),[],'empty optional answers never adopt a linked candidate');
      sheet.answers['Q-1']='与本账号有权限的园区一致';sheet.answers['Q-2']='沿用模板权限';
      assert.deepEqual(answeredLinkedClauses(p,sheet).map(i=>i.id),['RULE-1']);
      const payload=linkedClausePayload(p,sheet);assert.equal(payload.length,1);
      assert.deepEqual(Object.keys(payload[0]).sort(),['behavior','item_id','statement']);
      assert.equal(payload[0].statement,rule.statement);assert.equal(payload[0].behavior.permissions,'保留模板账号权限');
      assert.deepEqual(Object.keys(payload[0].behavior).sort(),['actor','data','entry','exceptions','flow','permissions','result']);
      assert.equal(JSON.stringify(p),before,'AI option and payload preparation do not mutate originals or approval');
      sheet.answers['Q-1']='';sheet.answers['Q-2']='暂不确定';assert.deepEqual(linkedClausePayload(p,sheet),[]);
    ''')


def test_linked_rule_eligibility_rejects_history_revisions_unrelated_and_ineligible_items():
    run_ts(LOAD+r'''
      const rule=(id,extra={})=>({id,kind:'rule',title:id,statement:'原规则',applies_to:'to_be',selection_status:'candidate',...extra});
      const items=[rule('R-OK'),rule('C-OK',{kind:'constraint',selection_status:'selected'}),rule('G-OK',{kind:'goal'}),rule('R-OTHER'),rule('R-OLD',{superseded_by:'R-OK'}),rule('R-REV',{target_item_id:'R-OK'}),rule('R-ASIS',{applies_to:'as_is'}),...['deferred','excluded','rejected'].map(status=>rule(status,{selection_status:status}))];
      const p=project(items);p.questions=[{id:'Q-1',question:'确认范围',related_refs:items.filter(i=>i.id!=='R-OTHER').map(i=>i.id),answer:'已明确'}];
      assert.deepEqual(linkedClauseItems(p).map(i=>i.id),['R-OK','C-OK','G-OK']);
      assert.deepEqual(linkedClausePayload(p,initial(p,[])).map(i=>i.item_id),['R-OK','C-OK','G-OK']);
      for(const answer of ['','  ','未知','待核对','暂时不清楚。','尚未明确','后续再说!'])assert.equal(clearAnswer(answer),false,answer);
      assert.equal(clearAnswer('仅日收益详情新增，其余沿用'),true);
    ''')


def test_old_sheet_draft_upgrades_linked_rules_and_preserves_manual_answer():
    run_ts(LOAD+r'''
      const rule={id:'RULE-1',kind:'rule',title:'模板可见范围',statement:'原模板权限',applies_to:'to_be',selection_status:'candidate'};
      const p=project([rule]);p.questions=[{id:'Q-1',question:'收益权限',related_refs:['RULE-1']}];
      const base=initial(p,[]),saved=structuredClone(base);delete base.linked_clauses;delete saved.linked_clauses;
      saved.answers['Q-1']='人工已填写的收益权限';
      const restored=restoreSheet(saved,base,initial(p,[]));
      assert.equal(restored.answers['Q-1'],'人工已填写的收益权限');
      assert.equal(restored.linked_clauses['RULE-1'].statement,'原模板权限');
      assert.equal(linkedClausePayload(p,restored)[0].statement,'原模板权限');assert.deepEqual(restored.merge_conflicts,[]);
    ''')


def test_linked_rule_three_way_merge_preserves_manual_edits_and_reports_remote_conflicts():
    run_ts(LOAD+r'''
      const rule={id:'RULE-1',kind:'rule',title:'模板可见范围',statement:'原规则',applies_to:'to_be',selection_status:'candidate',behavior:{permissions:'原权限'}};
      const p=project([rule]);p.questions=[{id:'Q-1',question:'权限',related_refs:['RULE-1'],answer:'已明确'}];
      const base=initial(p,[]),mine=structuredClone(base);mine.linked_clauses['RULE-1'].statement='人工修改规则';mine.linked_clauses['RULE-1'].behavior.permissions='人工修改权限';
      const next=structuredClone(p);next.revision=2;next.items[1].statement='另一页面修改规则';next.items[1].behavior={permissions:'另一页面权限',actor:'资料新增角色'};
      const merged=restoreSheet(mine,base,initial(next,[]));
      assert.equal(merged.linked_clauses['RULE-1'].statement,'人工修改规则');assert.equal(merged.linked_clauses['RULE-1'].behavior.permissions,'人工修改权限');assert.equal(merged.linked_clauses['RULE-1'].behavior.actor,'资料新增角色');
      assert(merged.merge_conflicts.some(message=>message.includes('模板可见范围')&&message.includes('正文')));assert(merged.merge_conflicts.some(message=>message.includes('权限约束')));
      const fresh=initial(next,[]),restored=restoreSheet(merged,fresh,fresh);assert.equal(restored.linked_clauses['RULE-1'].statement,'人工修改规则');
      const removed=structuredClone(next);removed.items[1].selection_status='deferred';const hidden=restoreSheet(mine,base,initial(removed,[]));
      assert.deepEqual(linkedClausePayload(removed,hidden),[]);assert(hidden.merge_conflicts.some(message=>message.includes('关联规则已在其他位置移除')));
    ''')


def test_linked_clause_editor_exposes_original_nonempty_content_and_keeps_empty_dimensions_optional():
    run_ts(r'''
      import assert from 'node:assert/strict';
      import {build} from 'esbuild';
      import {createRequire} from 'node:module';
      const require=createRequire(import.meta.url),React=require('react'),{renderToStaticMarkup}=require('react-dom/server');
      const built=await build({entryPoints:['src/pm-prototype/RequirementTable.tsx'],bundle:true,write:false,format:'cjs',platform:'node',jsx:'automatic',loader:{'.css':'empty'},external:['react','react-dom/server']});
      const module={exports:{}};new Function('require','module','exports',built.outputFiles[0].text)(require,module,module.exports);
      const item={title:'电价模板可见范围',behavior:{permissions:'原权限必须可见'}};
      const row={item_id:'RULE-1',statement:'原模板规则必须可见',behavior:{actor:'',entry:'',flow:'',data:'',permissions:'原权限必须可见',result:'',exceptions:''}};
      const markup=renderToStaticMarkup(React.createElement(module.exports.LinkedClauseEditor,{item,row,disabled:false,onChange:()=>{}}));
      assert(markup.includes('aria-label="电价模板可见范围 关联规则正文"'));assert(markup.includes('原模板规则必须可见'));
      assert(markup.indexOf('原权限必须可见')<markup.indexOf('<details>'),'nonempty rule content cannot be collapsed');
      assert(markup.includes('其他业务说明（选填）'));assert(!markup.includes('required='));assert(!markup.includes('<details open'));
      const original=structuredClone(row);original.behavior.permissions='';
      const cleared=renderToStaticMarkup(React.createElement(module.exports.LinkedClauseEditor,{item,row:original,disabled:false,onChange:()=>{}}));
      assert(cleared.indexOf('关联规则 权限约束')<cleared.indexOf('<details>'),'clearing text must not hide the original nonempty field');
    ''')
