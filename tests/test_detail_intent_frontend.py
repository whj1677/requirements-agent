"""Exercise the shipped row-to-workspace detail-intent callbacks, without models."""
from tests.test_ai_direct_frontend import LOAD as ACTION_LOAD
from tests.test_scope_frontend import run_ts


LOAD = r'''
import assert from 'node:assert/strict';
import fs from 'node:fs';
import ts from 'typescript';
import {build} from 'esbuild';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url);
const built=await build({stdin:{contents:"export {detailDraftRequested,withDetailDraftIntent,aiInstructions} from './src/pm-prototype/RequirementTable'; export {emptyDetailContract} from './src/pm-prototype/detail-contract'; export {initial} from './src/pm-prototype/refinement-sheet-state';",resolveDir:process.cwd()},bundle:true,write:false,format:'cjs',platform:'node',jsx:'automatic',loader:{'.css':'empty'},external:['react','react-dom/server']});
const module={exports:{}};new Function('require','module','exports',built.outputFiles[0].text)(require,module,module.exports);
const {detailDraftRequested,withDetailDraftIntent,aiInstructions,emptyDetailContract,initial}=module.exports;
const parse=path=>ts.createSourceFile(path,fs.readFileSync(path,'utf8'),ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
const table=parse('src/pm-prototype/RequirementTable.tsx'),workspace=parse('src/workspace.tsx'),connected=parse('src/pm-prototype/ConnectedWorkspace.tsx');
function attribute(source,tag,name){
  const found=[];
  function visit(node){
    if((ts.isJsxSelfClosingElement(node)||ts.isJsxOpeningElement(node))&&node.tagName.getText(source)===tag){
      const attr=node.attributes.properties.find(value=>ts.isJsxAttribute(value)&&value.name.getText(source)===name);
      if(attr?.initializer&&ts.isJsxExpression(attr.initializer))found.push(attr.initializer.expression);
    }
    ts.forEachChild(node,visit);
  }
  visit(source);assert.equal(found.length,1,`${tag}.${name} must have one actual binding`);return found[0].getText(source);
}
function labelledButton(source,label,name){
  const found=[];
  function visit(node){
    if(ts.isJsxElement(node)&&node.openingElement.tagName.getText(source)==='button'&&node.children.filter(ts.isJsxText).map(n=>n.text.trim()).join('')===label){
      const attr=node.openingElement.attributes.properties.find(value=>ts.isJsxAttribute(value)&&value.name.getText(source)===name);
      if(attr?.initializer&&ts.isJsxExpression(attr.initializer))found.push(attr.initializer.expression);
    }
    ts.forEachChild(node,visit);
  }
  visit(source);assert.equal(found.length,1,`exercise actual ${label}.${name}`);return found[0].getText(source);
}
const compiled=expression=>ts.transpileModule(`const result=(${expression});`,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.None}}).outputText+';return result;';
const evaluate=(expression,bindings)=>new Function(...Object.keys(bindings),compiled(expression))(...Object.values(bindings));
const workspaceBinding=attribute(workspace,'ConnectedWorkspace','onRecommendRequirement');
const connectedBinding=attribute(connected,'RequirementTable','onRecommend');
const handlers=[['retry',attribute(table,'DraftRowReview','onRetry')],['improve',labelledButton(table,'请 AI 完善此需求','onClick')]];
const flush=async()=>{for(let i=0;i<12;i++)await Promise.resolve();};
const deferred=()=>{let resolve;const promise=new Promise(value=>resolve=value);return {promise,resolve};};
function fixtureRow(extra={}){return {requirement_id:'REQ-ORIGINAL',statement:'当前人工原话',change_type:'modified',behavior:{},acceptance_items:[{statement:'原验收'}],...extra};}
function workspaceSpy(){
  const calls=[],events=[];
  const act=fn=>{events.push('act');return fn();};
  const planAction=(...args)=>{events.push('plan');calls.push(args);return Promise.resolve('planned');};
  const recommend=evaluate(workspaceBinding,{act,planAction});
  const throughConnected=evaluate(connectedBinding,{onRecommendRequirement:recommend});
  return {calls,events,recommend,throughConnected};
}
function rowClick(expression,item,row,saveDraft,navigate,onRecommend){
  const other=fixtureRow({requirement_id:'REQ-UNRELATED',statement:'PRIVATE-OTHER-ROW'});
  const sheet={values:{},answers:{},linked_clauses:{},rows:{[item.id]:row,'REQ-UNRELATED':other}};
  const baseline={current:structuredClone(sheet)};baseline.current.rows[item.id].statement='保存时的原文';
  return evaluate(expression,{item,row,sheet,baseline,props:{onRecommend},saveDraft,navigate,aiInstructions,detailDraftRequested});
}
'''


def test_enabled_empty_editor_and_canonical_requirements_request_details_without_implicit_adoption():
    run_ts(LOAD + r'''
      const empty=emptyDetailContract(),row=fixtureRow(),item={id:'REQ-ORIGINAL'};
      assert.equal(empty.fields.length,0);assert.equal(empty.interactions.length,0);
      const before=JSON.stringify({empty,row,item});
      assert.equal(detailDraftRequested(item,{...row,detail_contract:empty}),true,'enabling an empty editor is explicit detail intent');
      assert.equal(detailDraftRequested({...item,detail_required:true},row),true,'missing browser contract cannot clear a canonical requirement');
      assert.equal(detailDraftRequested({...item,detail_contract:empty},row),true,'existing canonical contract keeps detail intent');
      assert.equal(detailDraftRequested(item,row),false);
      assert.equal(detailDraftRequested({...item,detail_required:false},{...row,detail_suggestion:{contract:empty,candidate_id:'C-UNADOPTED'}}),false);
      assert.equal(detailDraftRequested(item,{...row,candidate_id:'C-BODY-ONLY',detail_suggestion:{contract:empty,candidate_id:'C-UNADOPTED'}}),false);
      assert.equal(JSON.stringify({empty,row,item}),before);
      const canonical={...item,kind:'requirement',title:'原需求',statement:'原说明',applies_to:'to_be',selection_status:'selected'};
      const candidate={...canonical,id:'C-UNADOPTED',target_item_id:item.id,action:'revise',selection_status:'candidate',detail_required:true,detail_contract:empty};
      const project={id:'P',revision:7,items:[canonical,candidate],questions:[],product_context:{scope_ids:[item.id]}};
      const original=JSON.stringify(project),sheet=initial(project,[{requirement_id:item.id,current:true}]);
      assert.equal(sheet.rows[item.id].detail_contract,undefined,'confirmed canonical row does not silently adopt a new candidate');
      assert.equal(sheet.rows[item.id].detail_suggestion.candidate_id,candidate.id);
      assert.equal(detailDraftRequested(canonical,sheet.rows[item.id]),false);
      assert.equal(JSON.stringify(project),original);
    ''')


def test_actual_workspace_callback_keeps_action_instruction_and_exact_target_and_only_adds_true_flag():
    run_ts(LOAD + r'''
      const f=workspaceSpy(),instruction='只处理原条目\n保留人工内容 😀';
      assert.equal(f.calls.length,0,'constructing the JSX callback never invokes AI');
      for(const intent of [true,false,undefined]){
        await f.recommend('REQ-ORIGINAL',instruction,intent);
        const args=f.calls.at(-1);
        assert.deepEqual(args,['clarify',undefined,intent===true?{kind:'item',id:'REQ-ORIGINAL',detail_required:true}:{kind:'item',id:'REQ-ORIGINAL'},instruction]);
        assert(!Object.values(args[2]).includes(false),'false must omit the flag instead of clearing a canonical requirement');
      }
      assert.deepEqual(f.events,['act','plan','act','plan','act','plan']);
      assert.equal(f.calls.length,3);
    ''')


def test_connected_workspace_forwards_the_original_callback_and_optional_third_argument():
    run_ts(LOAD + r'''
      const f=workspaceSpy();assert.equal(f.throughConnected,f.recommend,'ConnectedWorkspace must not drop the optional argument');
      await f.throughConnected('REQ-ORIGINAL','原指令',true);
      assert.deepEqual(f.calls,[['clarify',undefined,{kind:'item',id:'REQ-ORIGINAL',detail_required:true},'原指令']]);
    ''')


def test_both_shipped_row_handlers_wait_for_save_then_navigate_and_request_only_the_original_row():
    run_ts(LOAD + r'''
      for(const [label,expression] of handlers){
        for(const mode of ['enabled-empty','canonical-required','canonical-contract','legacy','unadopted-suggestion']){
          const f=workspaceSpy(),events=[],gate=deferred(),item={id:'REQ-ORIGINAL'},row=fixtureRow();
          if(mode==='enabled-empty')row.detail_contract=emptyDetailContract();
          if(mode==='canonical-required')item.detail_required=true;
          if(mode==='canonical-contract')item.detail_contract=emptyDetailContract();
          if(mode==='unadopted-suggestion')row.detail_suggestion={contract:emptyDetailContract(),candidate_id:'C-UNADOPTED'};
          const expected=['enabled-empty','canonical-required','canonical-contract'].includes(mode),before=JSON.stringify({item,row});
          const click=rowClick(expression,item,row,()=>{events.push('save');return gate.promise;},next=>{events.push('navigate');return next();},(...args)=>{events.push('recommend');return f.throughConnected(...args);});
          assert.deepEqual(events,[]);assert.equal(f.calls.length,0);
          click();assert.deepEqual(events,['save']);await flush();assert.equal(f.calls.length,0,'pending save must hold both navigation and AI');
          gate.resolve();await flush();assert.deepEqual(events,['save','navigate','recommend']);assert.equal(f.calls.length,1,label+' '+mode);
          const [action,question,target,instruction]=f.calls[0];
          assert.equal(action,'clarify');assert.equal(question,undefined);
          assert.deepEqual(target,expected?{kind:'item',id:item.id,detail_required:true}:{kind:'item',id:item.id});
          assert(instruction.includes('当前人工原话'));assert(instruction.includes(item.id));
          assert(!instruction.includes('PRIVATE-OTHER-ROW'));assert(!instruction.includes('REQ-UNRELATED'));
          assert.equal(instruction.includes('完整详细定义草稿'),expected,'the displayed request intent must match the actual detail flag');
          if(expected)for(const word of ['字段','交互','流程图','状态'])assert(instruction.includes(word),word);
          assert.equal(JSON.stringify({item,row}),before,'requesting a draft cannot adopt, clear or rewrite canonical/browser data');
        }
      }
    ''')


def test_both_row_handlers_remain_behind_navigation_guard_and_disabled_props():
    run_ts(LOAD + r'''
      for(const [label,expression] of handlers){
        const f=workspaceSpy(),events=[],row=fixtureRow({detail_contract:emptyDetailContract()});
        const click=rowClick(expression,{id:'REQ-ORIGINAL'},row,async()=>events.push('save'),()=>events.push('blocked-navigation'),f.throughConnected);
        click();await flush();assert.deepEqual(events,['save','blocked-navigation']);assert.equal(f.calls.length,0,label);
      }
      for(const flag of [false,true]){
        assert.equal(evaluate(attribute(table,'DraftRowReview','disabled'),{disabled:flag}),flag);
        assert.equal(evaluate(labelledButton(table,'请 AI 完善此需求','disabled'),{disabled:flag}),flag);
      }
    ''')


def test_actual_plan_and_submit_preserve_detail_intent_with_one_user_click():
    run_ts(ACTION_LOAD + r'''
      for(const requestDetails of [true,false,undefined]){
        const f=fixture(),instruction='只完善这条的详细草稿，原话不变';
        f.projectRef.current.items=[{id:'REQ-ORIGINAL',detail_required:true,statement:'已确认正文'}];
        const before=JSON.stringify(f.projectRef.current.items);
        await f.onRecommend('REQ-ORIGINAL',instruction,requestDetails);
        assert.equal(f.state.plans.length,1);assert.equal(f.state.posts.length,1);
        const target=requestDetails===true?{kind:'item',id:'REQ-ORIGINAL',detail_required:true}:{kind:'item',id:'REQ-ORIGINAL'};
        assert.deepEqual(f.state.plans[0].target,target);assert.deepEqual(f.state.posts[0].target,target);
        assert.equal(f.state.posts[0].action,'clarify');assert.equal(f.state.posts[0].message,instruction);
        assert.equal(f.state.posts[0].idempotency_key,'key-1');assert.equal(f.state.keys,1);
        assert.equal(JSON.stringify(f.projectRef.current.items),before,'omitting false does not clear canonical detail_required');
      }
    ''')


def test_bulk_draft_request_carries_only_enabled_in_scope_original_detail_ids():
    run_ts(LOAD + r'''
      const empty=emptyDetailContract();
      const items=[
        {id:'REQ-A',kind:'requirement',applies_to:'to_be',selection_status:'candidate'},
        {id:'REQ-B',kind:'requirement',applies_to:'to_be',selection_status:'selected'},
        {id:'REQ-C',kind:'requirement',applies_to:'to_be',selection_status:'selected'},
        {id:'REQ-SAVED-REQUIRED',kind:'requirement',applies_to:'to_be',selection_status:'selected',detail_required:true},
        {id:'REQ-SAVED-DETAIL',kind:'requirement',applies_to:'to_be',selection_status:'selected',detail_contract:empty},
        {id:'REQ-OUTSIDE',kind:'requirement',applies_to:'to_be',selection_status:'selected',detail_required:true},
        {id:'REQ-AS-IS',kind:'requirement',applies_to:'as_is',selection_status:'selected',detail_required:true},
        {id:'REQ-SUPERSEDED',kind:'requirement',applies_to:'to_be',selection_status:'selected',superseded_by:'REQ-NEW',detail_required:true},
        {id:'CAND-A',kind:'requirement',applies_to:'to_be',target_item_id:'REQ-A',action:'revise',selection_status:'candidate',detail_required:true},
      ];
      const rows=Object.fromEntries(items.map(item=>[item.id,fixtureRow({requirement_id:item.id})]));
      rows['REQ-A'].detail_contract=empty;
      rows['REQ-C'].detail_suggestion={contract:empty,candidate_id:'CAND-DETAIL'};
      const project={items},sheet={scope:['REQ-A','REQ-B','REQ-C','REQ-SAVED-REQUIRED','REQ-SAVED-DETAIL','REQ-AS-IS','REQ-SUPERSEDED','CAND-A'],rows};
      const before=JSON.stringify({project,sheet}),expected=['REQ-A','REQ-SAVED-REQUIRED','REQ-SAVED-DETAIL'];
      for(const mode of ['all','required']){
        const request={mode};
        assert.deepEqual(withDetailDraftIntent(project,sheet,request),{mode,detail_required_ids:expected});
        assert.deepEqual(request,{mode},'outbound construction must not mutate the click request');
      }
      assert.equal(JSON.stringify({project,sheet}),before,'collecting intent must not adopt or edit details');
      for(const request of [{mode:'question',question_id:'Q1'},{mode:'prewrite'}]){
        assert.equal(withDetailDraftIntent(project,sheet,request),request,'non-bulk requests stay unchanged');
        assert(!('detail_required_ids' in request));
      }
      const noIntent={...sheet,scope:['REQ-B','REQ-C']};
      for(const mode of ['all','required']){
        const request={mode};
        assert.equal(withDetailDraftIntent(project,noIntent,request),request);
        assert(!('detail_required_ids' in request),'empty intent must be omitted, not sent as a clearing list');
      }
    ''')


BULK_CLICK = LOAD + r'''
const component=table.statements.find(n=>ts.isFunctionDeclaration(n)&&n.name?.text==='RequirementTable');
const requestAI=component.body.statements.find(n=>ts.isFunctionDeclaration(n)&&n.name?.text==='requestAI');
assert(requestAI,'exercise the actual top-level draft click handler');
function bulkFixture(options={}){
  const item={id:'REQ-ORIGINAL',kind:'requirement',applies_to:'to_be',selection_status:'candidate'};
  const row=fixtureRow({detail_contract:emptyDetailContract()});
  const project={id:'P-bulk',revision:3,items:[item]},sheet={revision:3,scope:[item.id],values:{},rows:{[item.id]:row},answers:{'Q-human':'只核对两名值班员，其他角色不作认领权限推断。'},linked_clauses:{}};
  const base=structuredClone(sheet);delete base.rows[item.id].detail_contract;base.answers['Q-human']='';
  const gate=deferred(),calls=[],events=[],errors=[],before=JSON.stringify({project,sheet});
  const click=evaluate(requestAI.getText(table),{
    project,sheet:options.noSheet?null:sheet,props:{busy:!!options.busy,onSuggest:(...args)=>calls.push(args)},working:!!options.working,
    root:{current:null},aiAnchor:{current:null},baseline:{current:base},withDetailDraftIntent,aiInstructions,
    saveDraft:()=>{events.push('save');return gate.promise;},navigate:fn=>{events.push('navigate');return fn();},setError:error=>errors.push(error),
  });
  return {click,gate,calls,events,errors,project,sheet,before};
}
'''


def test_actual_bulk_click_waits_for_save_and_carries_current_intent_and_human_answer():
    run_ts(BULK_CLICK + r'''
      for(const mode of ['all','required']){
        const f=bulkFixture(),request={mode};f.click(request,'补齐当前草稿');
        assert.deepEqual(f.events,['save']);assert.equal(f.calls.length,0);
        f.gate.resolve();await flush();
        assert.deepEqual(f.events,['save','navigate']);assert.equal(f.calls.length,1);
        assert.deepEqual(f.calls[0][1],{mode,detail_required_ids:['REQ-ORIGINAL']});
        assert(f.calls[0][0].includes(f.sheet.answers['Q-human']),'the existing human answer remains in the real instructions');
        assert.equal(JSON.stringify({project:f.project,sheet:f.sheet}),f.before,'dispatch does not adopt or rewrite the draft');
        assert.deepEqual(request,{mode});
      }
      const f=bulkFixture(),request={mode:'question',question_id:'Q-human'};
      f.click(request,'补充本题');f.gate.resolve();await flush();
      assert.deepEqual(f.calls[0][1],request);assert(!('detail_required_ids' in f.calls[0][1]));
    ''')


def test_actual_bulk_click_keeps_busy_navigation_and_failed_save_guards():
    run_ts(BULK_CLICK + r'''
      for(const options of [{busy:true},{working:true},{noSheet:true}]){
        const f=bulkFixture(options);f.click({mode:'all'},'补齐当前草稿');await flush();
        assert.deepEqual(f.events,[]);assert.deepEqual(f.calls,[]);
      }
      const f=bulkFixture();
      // Reject the actual save without dispatching or navigating.
      const componentFn=evaluate(requestAI.getText(table),{
        project:f.project,sheet:f.sheet,props:{busy:false,onSuggest:(...args)=>f.calls.push(args)},working:false,
        root:{current:null},aiAnchor:{current:null},baseline:{current:structuredClone(f.sheet)},withDetailDraftIntent,aiInstructions,
        saveDraft:()=>Promise.reject(Error('草稿保存失败，原输入仍在')),navigate:fn=>{f.events.push('navigate');return fn();},setError:error=>f.errors.push(error),
      });
      componentFn({mode:'required'},'补齐当前草稿');await flush();
      assert.deepEqual(f.calls,[]);assert(!f.events.includes('navigate'));
      assert.deepEqual(f.errors,['草稿保存失败，原输入仍在']);
    ''')
