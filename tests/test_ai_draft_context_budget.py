"""Atomic draft packing preserves current answers and exact omitted blocks."""
from tests.test_scope_frontend import run_ts


def test_ai_draft_context_budget_preserves_current_information_and_candidate_references():
    run_ts(r'''
import assert from 'node:assert/strict';
import {build} from 'esbuild';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url);
async function load(entry,format='esm'){
  const built=await build({entryPoints:[entry],bundle:true,write:false,format,platform:'node',jsx:'automatic',loader:{'.css':'empty'},external:['react']});
  if(format==='cjs'){const m={exports:{}};new Function('require','module','exports',built.outputFiles[0].text)(require,m,m.exports);return m.exports;}
  return import('data:text/javascript;base64,'+Buffer.from(built.outputFiles[0].text).toString('base64'));
}
const {aiInstructions,withDetailDraftIntent}=await load('src/pm-prototype/RequirementTable.tsx','cjs');
const {initial,requirementPayload}=await load('src/pm-prototype/refinement-sheet-state.ts');
const {emptyDetailContract}=await load('src/pm-prototype/detail-contract.ts');
const PREFIX='本表未提交的人工修改（仅供起草参考，仍待确认）：';
const answer='Only the two already described officers are in scope; other roles are not inferred. 中文原句\n下一行😀';
const long='LONG_MANUAL_DETAIL_'.repeat(1500);
function fixture(big=false){
  const detail=emptyDetailContract();detail.fields=[{id:'F1',name:'当前负责人',meaning:big?long:'既有负责人的展示',data_type:'string',
    source:'现有记录',required:'只读',default_value:'沿用现有值',constraints:'沿用现有约束',visibility:'值班员',editability:'只读',evidence:{meaning:{status:'source',source_refs:[{source_id:'S1',excerpt_id:'E1'}]}}}];
  const project={id:'P-synthetic-budget',revision:1,product_context:{intent:'查看告警',users:'值班员',scope_ids:['REQ-1']},
    scope_profile:{scenario:'new_module',dependencies:'unknown'},questions:[],items:[{id:'REQ-1',kind:'requirement',title:'告警查看',
      statement:'既有需求',applies_to:'to_be',selection_status:'selected',change_type:'new',behavior:{actor:'值班员'},related_refs:[],detail_contract:detail},
      {id:'AC-1',kind:'acceptance',statement:'显示当前负责人',applies_to:'to_be',selection_status:'selected',related_refs:['REQ-1']}]};
  const base=initial(project,[]),mine=structuredClone(base);return {project,base,mine};
}
function core(mine){mine.answers.Q1=answer;mine.profile={scenario:'modify_existing',dependencies:'unknown'};}
function business(text){const at=text.indexOf(PREFIX);if(at<0)return null;return JSON.parse(text.slice(at+PREFIX.length));}
function invoke(mine,base,purpose='请根据已有资料继续起草。'){
  const before=JSON.stringify({mine,base}),text=aiInstructions(purpose,mine,base);
  assert.equal(JSON.stringify({mine,base}),before,'message packing never edits the browser draft');
  assert(text.length<=11500,'all prefixes, references and omission notices fit the existing total target');
  return text;
}
function assertCore(text){const edits=business(text);assert(edits,'short business edits still have a complete JSON section');
  assert.equal(edits.answers.Q1,answer);assert.deepEqual(edits.scope_profile,{scenario:'modify_existing',dependencies:'unknown'});return edits;}
const initialCase=fixture(true);core(initialCase.mine);initialCase.mine.rows['REQ-1'].candidate_id='CANDIDATE-NEW';
const passed=[];async function check(name,fn){await fn();passed.push(name);}
await check('Candidate-only large row cannot crowd out current short answer/profile',()=>{
  const text=invoke(initialCase.mine,initialCase.base),edits=assertCore(text);
  assert.equal(edits.requirements.length,0);assert(text.includes('CANDIDATE-NEW'));assert(text.includes('REQ-1'));assert(!text.includes(long));
});
await check('Genuine long requirement is omitted atomically while short core survives',()=>{
  const {base,mine}=fixture();core(mine);mine.rows['REQ-1'].statement=long;
  const text=invoke(mine,base),edits=assertCore(text);assert.equal(edits.requirements.length,0);
  assert(text.includes('未附带')&&text.includes('REQ-1'));assert(!text.includes(long));
});
await check('One huge answer cannot discard another complete short answer',()=>{
  const {base,mine}=fixture();mine.answers={Q2:long,Q1:answer};mine.profile={scenario:'modify_existing',dependencies:'unknown'};
  const text=invoke(mine,base),edits=assertCore(text);assert(!Object.hasOwn(edits.answers,'Q2'));
  assert(text.includes('未附带')&&text.includes('Q2'));assert(!text.includes(long));
});
await check('Huge linked clause cannot discard short answer/profile',()=>{
  const {base,mine}=fixture();core(mine);mine.linked_clauses={RULE_BIG:{item_id:'RULE_BIG',statement:long,behavior:{flow:'原始人工流程'}}};
  const text=invoke(mine,base),edits=assertCore(text);assert.equal(edits.linked_clauses.length,0);
  assert(text.includes('未附带')&&text.includes('RULE_BIG'));assert(!text.includes(long));
});
await check('Long summary value is omitted without losing a different short summary',()=>{
  const {base,mine}=fixture();mine.values.intent=long;mine.values.users='两个已授权值班员';
  const text=invoke(mine,base),edits=business(text);assert.equal(edits.summary.users,'两个已授权值班员');assert(!Object.hasOwn(edits.summary,'intent'));
  assert(text.includes('未附带'));assert(!text.includes(long));
});
await check('Candidate-only acceptance change keeps compact choice and confirmation binding',()=>{
  const {base,mine}=fixture(true);mine.rows['REQ-1'].acceptance_items[0].candidate_id='AC-CHOICE-NEW';
  const text=invoke(mine,base),edits=business(text);assert(text.includes('AC-1')&&text.includes('AC-CHOICE-NEW'));
  assert(!edits||edits.requirements.length===0);assert(!text.includes(long));
  assert.equal(requirementPayload(mine.rows['REQ-1']).acceptance_items[0].candidate_id,'AC-CHOICE-NEW');
});
await check('Real fitting content plus candidate identities stays an exact whole block',()=>{
  const {base,mine}=fixture();mine.rows['REQ-1'].candidate_id='REQ-CHOICE';mine.rows['REQ-1'].statement='真正人工修改的完整原句';
  mine.rows['REQ-1'].acceptance_items[0].candidate_id='AC-CHOICE';
  const edits=business(invoke(mine,base)),row=edits.requirements[0];assert.equal(row.statement,'真正人工修改的完整原句');
  assert.equal(row.requirement_id,'REQ-1');assert.equal(row.candidate_id,'REQ-CHOICE');assert.equal(row.acceptance_items[0].candidate_id,'AC-CHOICE');
  assert.deepEqual(row.detail_contract,mine.rows['REQ-1'].detail_contract);
});
await check('UI-only metadata never makes a phantom manual edit',()=>{
  const {base,mine}=fixture();mine.answer_provenance={Q1:{saved_answer:'',ai_answer:long}};
  const row=mine.rows['REQ-1'];row.detail_suggestion={contract:{fields:[{meaning:long}]},candidate_id:'NAV-ONLY'};row.detail_conflicts=[];
  Object.assign(row.acceptance_items[0],{ai_suggested:true,applied_candidate_ids:['OLD'],suggestions:[{candidate_id:'NAV',statement:long}]});
  assert.equal(invoke(mine,base),'请根据已有资料继续起草。');
});
await check('Real small edit strips bulky UI-only row fields from transmitted block',()=>{
  const {base,mine}=fixture();const row=mine.rows['REQ-1'];row.statement='真实短改';
  row.detail_suggestion={contract:{fields:[{meaning:long}]},candidate_id:'NAV-ONLY'};row.detail_conflicts=[{path:'x',local:long,remote:long}];
  row.acceptance_items[0].suggestions=[{candidate_id:'NAV',statement:long}];
  const text=invoke(mine,base),sent=business(text).requirements[0];assert.equal(sent.statement,'真实短改');
  assert(!Object.hasOwn(sent,'detail_suggestion'));assert(!Object.hasOwn(sent,'detail_conflicts'));
  assert(!Object.hasOwn(sent.acceptance_items[0],'suggestions'));assert(!text.includes(long));
});
await check('Actual detail evidence status remains a real editable change',()=>{
  const {base,mine}=fixture();mine.rows['REQ-1'].detail_contract.fields[0].evidence.meaning.status='proposal';
  const edits=business(invoke(mine,base));assert.equal(edits.requirements[0].detail_contract.fields[0].evidence.meaning.status,'proposal');
});
await check('Near-boundary purpose accounts for prefixes and omission warning',()=>{
  const {base,mine}=fixture();core(mine);mine.rows['REQ-1'].statement=long;
  const text=invoke(mine,base,'任务目的'.repeat(2400));assertCore(text);assert(text.includes('未附带'));
});
await check('Many candidate references cannot crowd out short business core or overflow',()=>{
  const {base,mine}=fixture();core(mine);
  for(let n=0;n<250;n++){
    const id='AC-LONG-ID-'+n+'-x'.repeat(15);base.rows['REQ-1'].acceptance_items.push({id,statement:'相同既有验收'});
    mine.rows['REQ-1'].acceptance_items.push({id,statement:'相同既有验收',candidate_id:'CHOICE-'+n+'-y'.repeat(40)});
  }
  const text=invoke(mine,base);assertCore(text);assert(text.includes('未附带')&&text.includes('候选'));
});
await check('Small manual acceptance add, delete, text and detail values stay exact',()=>{
  for(const change of ['add','delete','text','detail']){
    const {base,mine}=fixture();const row=mine.rows['REQ-1'];
    if(change==='add')row.acceptance_items.push({statement:'人工新增验收'});
    if(change==='delete')row.acceptance_items=[];
    if(change==='text')row.acceptance_items[0].statement='人工修改验收';
    if(change==='detail')row.detail_contract.fields[0].meaning='人工修改字段含义';
    const sent=business(invoke(mine,base)).requirements[0];
    assert.deepEqual(sent.acceptance_items.map(({id,statement,candidate_id})=>({id,statement,candidate_id})),
      row.acceptance_items.map(({id,statement,candidate_id})=>({id,statement,candidate_id})));
    assert.deepEqual(sent.detail_contract,row.detail_contract);
  }
});
await check('base=null remains conservative and detail request intent is unchanged',()=>{
  const {project,mine}=fixture();mine.answers.Q1=answer;
  const edits=business(invoke(mine,null));assert.equal(edits.answers.Q1,answer);assert.equal(edits.requirements.length,1);
  assert.deepEqual(withDetailDraftIntent(project,mine,{mode:'all'}).detail_required_ids,['REQ-1']);
});

await check('Anonymous local acceptance candidate choice keeps explicit local position navigation',()=>{
  const {base,mine}=fixture(true);delete base.rows['REQ-1'].acceptance_items[0].id;delete mine.rows['REQ-1'].acceptance_items[0].id;
  base.rows['REQ-1'].acceptance_items[0].candidate_id='LOCAL-CHOICE-OLD';mine.rows['REQ-1'].acceptance_items[0].candidate_id='LOCAL-CHOICE-NEW';
  const text=invoke(mine,base),edits=business(text);
  assert(text.includes('REQ-1')&&text.includes('当前本地验收位置 1')&&text.includes('LOCAL-CHOICE-NEW'));
  assert(!edits||edits.requirements.length===0);assert(!text.includes(long));
  assert(!Object.hasOwn(requirementPayload(mine.rows['REQ-1']).acceptance_items[0],'id'));
  assert.equal(requirementPayload(mine.rows['REQ-1']).acceptance_items[0].candidate_id,'LOCAL-CHOICE-NEW');
});
await check('Anonymous local acceptance cancellation remains explicit without fabricating an id',()=>{
  const {base,mine}=fixture(true);delete base.rows['REQ-1'].acceptance_items[0].id;delete mine.rows['REQ-1'].acceptance_items[0].id;
  base.rows['REQ-1'].acceptance_items[0].candidate_id='LOCAL-CHOICE-OLD';
  const text=invoke(mine,base),edits=business(text);
  assert(text.includes('REQ-1')&&text.includes('当前本地验收位置 1')&&text.includes('明确取消'));
  assert(!edits||edits.requirements.length===0);assert(!text.includes(long));
  assert(!Object.hasOwn(requirementPayload(mine.rows['REQ-1']).acceptance_items[0],'id'));
});

assert.equal(passed.length,16);
''')
