"""Detailed requirement editing preserves human drafts, identity and review evidence."""
from tests.test_scope_frontend import run_ts


LOAD = r'''
import assert from 'node:assert/strict';
import {build} from 'esbuild';
const load=async entry=>{const built=await build({entryPoints:[entry],bundle:true,write:false,format:'esm',platform:'node'});return import('data:text/javascript;base64,'+Buffer.from(built.outputFiles[0].text).toString('base64'));};
const {emptyDetailContract,mergeDetailContract,resolveDetailConflict,detailFieldValue,detailEditingIssues}=await load('src/pm-prototype/detail-contract.ts');
const contract=()=>({...emptyDetailContract(),fields:[{id:'FD-level',name:'告警级别',meaning:'上报的级别',data_type:'enum',source:'EMS上报',required:'必填',default_value:'无默认值；空值显示未知',constraints:'仅允许枚举选项',visibility:'值班人员',editability:'只读，由EMS上报更新',enum_values:['一级','二级'],evidence:{constraints:{status:'proposal'}}}]});
const project=(detail,revision=1)=>({id:'P-detail',revision,items:[{id:'REQ-1',kind:'requirement',title:'查看告警',statement:'查看告警信息',applies_to:'to_be',selection_status:'selected',change_type:'new',behavior:{actor:'值班人员'},related_refs:[],...(detail?{detail_contract:detail}:{})},{id:'AC-1',kind:'acceptance',statement:'显示告警级别',applies_to:'to_be',selection_status:'selected',related_refs:['REQ-1']}],questions:[],product_context:{intent:'查看告警',users:'值班人员',scope_ids:['REQ-1']},scope_profile:{scenario:'new_product',dependencies:'independent'}});
'''


def test_detail_merge_uses_stable_ids_and_preserves_independent_field_changes():
    run_ts(LOAD + r'''
      const base=contract(),mine=structuredClone(base),remote=structuredClone(base);
      mine.fields[0].constraints='人工限制';mine.fields.push({id:'FD-manual',name:'人工新增'});
      remote.fields[0].meaning='最新业务含义';remote.fields.unshift({id:'FD-ai',name:'新增建议'});
      const before=JSON.stringify({base,mine,remote}),merged=mergeDetailContract(mine,base,remote);
      assert.deepEqual(merged.value.fields.map(row=>row.id),['FD-ai','FD-level','FD-manual']);
      assert.equal(merged.value.fields.find(row=>row.id==='FD-level').constraints,'人工限制');
      assert.equal(merged.value.fields.find(row=>row.id==='FD-level').meaning,'最新业务含义');
      assert.deepEqual(merged.conflicts,[]);assert.equal(JSON.stringify({base,mine,remote}),before);
    ''')


def test_concurrent_property_conflict_keeps_manual_value_and_its_evidence():
    run_ts(LOAD + r'''
      const base=contract(),mine=structuredClone(base),remote=structuredClone(base);
      mine.fields[0].constraints='人工范围';remote.fields[0].constraints='来源范围';
      remote.fields[0].evidence.constraints={status:'source',source_refs:[{source_id:'S1',excerpt_id:'E1'}]};
      const merged=mergeDetailContract(mine,base,remote);
      assert.equal(merged.value.fields[0].constraints,'人工范围');
      assert.equal(merged.value.fields[0].evidence.constraints.status,'proposal','a retained manual value cannot inherit a source claim for the remote text');
      const conflict=merged.conflicts.find(row=>row.path==='detail_contract.fields.FD-level.constraints');
      assert.deepEqual([conflict.local,conflict.remote],['人工范围','来源范围']);
      const accepted=resolveDetailConflict(merged.value,conflict,true);
      assert.equal(accepted.fields[0].constraints,'来源范围');
      assert.equal(merged.value.fields[0].constraints,'人工范围','resolution must not mutate saved browser drafts');
    ''')


def test_deletion_conflicts_retain_reviewable_content_and_allow_explicit_resolution():
    run_ts(LOAD + r'''
      const base=contract(),mine=structuredClone(base),remote=structuredClone(base);
      mine.fields[0].meaning='本地修改';remote.fields=[];
      const removedRemote=mergeDetailContract(mine,base,remote);
      assert.equal(removedRemote.value.fields[0].meaning,'本地修改');
      assert.equal(removedRemote.conflicts[0].path,'detail_contract.fields.FD-level');
      assert.equal(resolveDetailConflict(removedRemote.value,removedRemote.conflicts[0],true).fields.length,0);
      const removedLocal=mergeDetailContract(remote,base,mine);
      assert.equal(removedLocal.value.fields.length,0,'local deletion is retained pending review');
      assert.equal(removedLocal.conflicts[0].remote.meaning,'本地修改');
      assert.equal(resolveDetailConflict(removedLocal.value,removedLocal.conflicts[0],true).fields[0].id,'FD-level');
    ''')


def test_sheet_drafts_keep_detail_changes_and_strip_only_ui_metadata_on_submit():
    run_ts(LOAD + r'''
      const {initial,mergeSuggestions,requirementPayload}=await load('src/pm-prototype/refinement-sheet-state.ts');
      assert.equal(initial(project(),[]).rows['REQ-1'].detail_contract,undefined,'legacy projects cannot silently opt into the new standard');
      const base=initial(project(contract()),[]),mine=structuredClone(base),remote=project(contract(),2);
      mine.rows['REQ-1'].detail_contract.fields[0].meaning='本地字段修改';
      remote.items[0].detail_contract.fields[0].meaning='服务端字段修改';
      const merged=mergeSuggestions(mine,base,initial(remote,[])),row=merged.rows['REQ-1'];
      assert.equal(row.detail_contract.fields[0].meaning,'本地字段修改');
      assert.equal(row.detail_conflicts[0].remote,'服务端字段修改');
      assert(merged.merge_conflicts.some(message=>message.includes('详细定义')));
      row.detail_suggestion={contract:contract(),candidate_id:'REQ-C1'};
      const payload=requirementPayload(row);
      assert.equal(payload.detail_contract.fields[0].meaning,'本地字段修改');
      assert.equal(payload.detail_conflicts,undefined);assert.equal(payload.detail_suggestion,undefined);
      assert(row.detail_conflicts.length,'payload construction cannot erase visible conflicts');
      const next=project(contract(),3);next.items[0].detail_contract.fields[0].meaning='第三版本字段修改';
      const again=mergeSuggestions(merged,initial(remote,[]),initial(next,[]));
      assert.equal(again.rows['REQ-1'].detail_conflicts[0].remote,'第三版本字段修改','comparison must show the latest remote value');
    ''')


def test_structured_answer_witnesses_are_stable_and_invalidated_by_real_edits():
    run_ts(LOAD + r'''
      const {validImplementationRefs,implementationFieldValue}=await load('src/answer-application-review.ts');
      const {initial,retainSheetImplementations}=await load('src/pm-prototype/refinement-sheet-state.ts');
      const p=project(contract()),path='detail_contract.fields.FD-level.enum_values';
      assert.equal(implementationFieldValue(p.items[0],path),'一级、二级');
      const ref={item_id:'REQ-1',field:path,quote:'一级、二级'};
      assert.equal(validImplementationRefs([ref],p.items).length,1);
      p.items[0].detail_contract.fields.unshift({id:'FD-other',name:'其他字段'});
      assert.equal(validImplementationRefs([ref],p.items).length,1,'insertion before a field does not move an ID-based witness');
      const sheet=initial(p,[]);sheet.answers.Q1='两个级别';sheet.answer_implementations={Q1:[ref]};
      assert.equal(retainSheetImplementations(sheet).answer_implementations.Q1.length,1);
      sheet.rows['REQ-1'].detail_contract.fields.find(row=>row.id==='FD-level').enum_values.push('三级');
      assert.equal(retainSheetImplementations(sheet).answer_implementations.Q1,undefined,'the original quote cannot silently update with the field');
    ''')


def test_detailed_edits_cannot_reuse_the_old_return_to_delivery_checkpoint():
    run_ts(LOAD + r'''
      const {initial}=await load('src/pm-prototype/refinement-sheet-state.ts');
      const {canReturnToDelivery}=await load('src/pm-prototype/refinement-return.ts');
      const p=project(contract());p.stage_checks=Object.fromEntries([1,2,3].map(step=>[step,{actor:'authenticated_local_user',content_hash:'hash-'+step}]));p.product_flow=[1,2,3].map(step=>({step,content_hash:'hash-'+step}));
      const states=[{requirement_id:'REQ-1',current:true}],sheet=initial(p,states);
      assert.equal(canReturnToDelivery(p,sheet,states,p.revision),true);
      sheet.rows['REQ-1'].detail_contract.fields[0].required='满足条件后必填';
      assert.equal(canReturnToDelivery(p,sheet,states,p.revision),false);
      const unchanged=initial(p,states);unchanged.rows['REQ-1'].detail_conflicts=[{path:'detail_contract.fields.FD-level.required',local:'a',remote:'b'}];
      assert.equal(canReturnToDelivery(p,unchanged,states,p.revision),false,'unresolved detail conflicts always need review');
    ''')


def test_empty_unknown_and_inapplicable_definitions_are_not_treated_as_complete():
    run_ts(LOAD + r'''
      const empty=emptyDetailContract();assert.equal(detailEditingIssues(empty).length,4);
      for(const key of Object.keys(empty.applicability))empty.applicability[key]={applicable:false,reason:'本查询仅描述入口，不涉及该类定义'};
      assert.deepEqual(detailEditingIssues(empty),[]);
      empty.fields=contract().fields;
      assert(detailEditingIssues(empty).some(issue=>issue.path==='detail_contract.fields'),'inapplicable sections with retained definitions must be reviewed');
      empty.applicability.fields={applicable:true,reason:''};
      empty.fields[0].evidence.required={status:'unknown',reason:'资料未明确'};
      assert(detailEditingIssues(empty).some(issue=>issue.path==='detail_contract.fields.FD-level.required'));
      empty.fields[0].data_type='number';
      assert(detailEditingIssues(empty).some(issue=>issue.path.endsWith('.unit')));
      assert(detailEditingIssues(empty).some(issue=>issue.path.endsWith('.precision')));
    ''')


def test_editor_renders_business_inputs_evidence_and_conflict_comparison():
    run_ts(LOAD + r'''
      import {createRequire} from 'node:module';
      const require=createRequire(import.meta.url),React=require('react'),{renderToStaticMarkup}=require('react-dom/server');
      const built=await build({entryPoints:['src/pm-prototype/DetailContractEditor.tsx'],bundle:true,write:false,format:'cjs',platform:'node',jsx:'automatic',loader:{'.css':'empty'},external:['react','react-dom/server']});
      const module={exports:{}};new Function('require','module','exports',built.outputFiles[0].text)(require,module,module.exports);
      const value=contract();value.fields[0].evidence.required={status:'unknown',reason:'尚未明确'};
      const html=renderToStaticMarkup(React.createElement(module.exports.DetailContractEditor,{requirementId:'REQ-1',title:'告警',value,conflicts:[{path:'detail_contract.fields.FD-level.required',local:'原要求',remote:'最新要求'}],onChange:()=>{}}));
      for(const label of ['数据字段','交互逻辑','业务流程','状态变化','可选值','可见角色与条件','可编辑角色与条件','待明确','原要求','最新要求','采用最新内容'])assert(html.includes(label),label);
      assert(html.includes('data-cell-key="requirement-REQ-1-detail_contract.fields.FD-level.required"'));
      assert(!html.includes('编辑 JSON'));assert(!html.includes('确认此字段'));
    ''')
