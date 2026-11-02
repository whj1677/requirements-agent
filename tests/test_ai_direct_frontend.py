"""Exercise the shipped click handlers with controlled promises, never a server/model."""
from tests.test_scope_frontend import run_ts


LOAD = r'''
import assert from 'node:assert/strict';
import fs from 'node:fs';
import ts from 'typescript';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url),React=require('react'),{renderToStaticMarkup}=require('react-dom/server');
const source=ts.createSourceFile('workspace.tsx',fs.readFileSync('src/workspace.tsx','utf8'),ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
const workspace=source.statements.find(n=>ts.isFunctionDeclaration(n)&&n.name?.text==='Workspace');
const actual=name=>{
  const node=workspace.body.statements.find(n=>ts.isFunctionDeclaration(n)&&n.name?.text===name);
  assert(node,`exercise shipped Workspace.${name}`);return node.getText(source);
};
function callback(tag,name){
  let found;
  function visit(node){
    if((ts.isJsxSelfClosingElement(node)||ts.isJsxOpeningElement(node))&&node.tagName.getText(source)===tag){
      const attr=node.attributes.properties.find(p=>ts.isJsxAttribute(p)&&p.name.getText(source)===name);
      if(attr?.initializer&&ts.isJsxExpression(attr.initializer))found=attr.initializer.expression.getText(source);
    }
    ts.forEachChild(node,visit);
  }
  visit(workspace);assert(found,`${tag}.${name} must remain wired`);return found;
}
const uncertain=source.statements.find(n=>ts.isVariableStatement(n)&&n.declarationList.declarations.some(d=>d.name.getText(source)==='actionSubmissionUncertain')).getText(source);
const top=name=>source.statements.find(n=>ts.isFunctionDeclaration(n)&&n.name?.text===name||ts.isVariableStatement(n)&&n.declarationList.declarations.some(d=>d.name.getText(source)===name)).getText(source);
const declaration=name=>workspace.body.statements.find(n=>ts.isVariableStatement(n)&&n.declarationList.declarations.some(d=>d.name.getText(source)===name)).getText(source);
const initialState=name=>workspace.body.statements.filter(ts.isVariableStatement).flatMap(n=>[...n.declarationList.declarations]).find(d=>ts.isArrayBindingPattern(d.name)&&d.name.elements[0].name.getText(source)===name).initializer.arguments[0].getText(source);
const effect=needle=>workspace.body.statements.find(n=>ts.isExpressionStatement(n)&&ts.isCallExpression(n.expression)&&n.expression.expression.getText(source)==='useEffect'&&n.expression.arguments[0].getText(source).includes(needle)).expression.arguments[0].getText(source);
let recoveryBanner;
function findRecoveryBanner(node){if(ts.isJsxExpression(node)){const text=node.expression?.getText(source)||'';if(text.startsWith('(actionError||actionRecovery')&&text.includes('上一次 AI 提交结果尚未确认'))recoveryBanner=text;}ts.forEachChild(node,findRecoveryBanner);}
findRecoveryBanner(workspace);assert(recoveryBanner,'the actual recovery banner must remain rendered');
const compiled=ts.transpileModule(`
  ${uncertain}
  class ApiError extends Error {constructor(status,code,message){super(message);this.status=status;this.code=code;}}
  function fixture(scenario={}){
    const state={events:[],posts:[],plans:[],tasks:scenario.tasks||[],errors:[],recovery:null,stale:false,keys:0,refreshes:0,tasksLoaded:true,helpText:'未发送本地补充',preview:{input_revision:7,preview_hash:'LOCAL-PREVIEW'},documentPreview:{input_revision:7,preview_hash:'PRD-PREVIEW'}};
    const id=scenario.projectId||'P',root='/projects/'+id,docType='prd',phase=0,active=true,running=false,actionBlockReason='';
    let actionRecovery=null,actionError='',actionStale=false,actionRecoveryPersistent,actionRecoveryNotice='',actionPostingReceipt='',busy=false,tasks=state.tasks,tasksLoaded=true,prdGenerationPreview=null;
    const projectRef={current:{id,revision:7,messages:[],items:[],questions:[],documents:{},sources:[]}};
    const currentView={current:{active:true,phase}},busyRef={current:false},actionFlight={current:null},actionIntent={current:0},actionRecoveryRef={current:null};
    const useRef=value=>({current:value});
    ${declaration('actionReceiptId')}
    ${declaration('actionReceiptSettlements')}
    ${declaration('actionPostControllers')}
    const analysisNavigation={current:null},prewriteNavigation={current:null},documentPreparationNavigation={current:null};
    const detailRef={current:{open:false}},connectedRef={current:{dataset:{step:'information'}}};
    const documentPreparationKey=id+'-document-preparation',storage=scenario.storage||new Map(),fallback=new Map();
    const session=scenario.session||{getItem:key=>fallback.get(key)||storage.get(key)||null,isPersistent:key=>!fallback.has(key)&&storage.has(key),setItem:(key,value)=>{if(scenario.storageFailure){fallback.set(key,value);return false;}storage.set(key,value);return true;},removeItem:key=>{fallback.delete(key);storage.delete(key);}};
    ${top('actionRecoveryStorageKey')}
    ${top('recoveryActions')}
    ${top('readActionRecovery')}
    const initialPersistent=${initialState('actionRecoveryPersistent')};actionRecoveryPersistent=typeof initialPersistent==='function'?initialPersistent():initialPersistent;
    const crypto={randomUUID:()=> 'key-'+(++state.keys)};
    const setBusy=value=>{busy=state.busy=value;},setError=value=>{state.error=value;};
    const beginActivity=()=>()=>{},setActionError=value=>{actionError=state.actionError=value;if(value)state.errors.push(value);};
    const setActionStale=value=>{actionStale=state.stale=value;},setActionRecovery=value=>{actionRecovery=value;state.recovery=value;};
    const setActionRecoveryNotice=value=>{actionRecoveryNotice=state.recoveryNotice=value;},setActionRecoveryPersistent=value=>{actionRecoveryPersistent=state.persistent=value;};
    const setActionPostingReceipt=value=>{actionPostingReceipt=typeof value==='function'?value(actionPostingReceipt):value;};
    const setDocumentPreparationNotice=value=>{state.preparationNotice=value;},setDocumentReviewRequested=value=>{state.reviewRequested=value;};
    const setPrdGenerationPreview=value=>{prdGenerationPreview=state.prdGenerationPreview=value;};
    const prewriteCanDraft=()=>false,documentPreparation=()=>({issues:scenario.preparationIssues||[],instruction:'补齐已保存草稿'});
    const api=async(path,method,body,signal)=>{
      state.events.push({path,method,body:body&&structuredClone(body)});
      if(path.endsWith('/requirement-confirmation/state'))return {revision:projectRef.current.revision,rows:[]};
      if(path.endsWith('/actions/plan')){
        state.plans.push(structuredClone(body));
        return scenario.plan?await scenario.plan(body):{plan_hash:'PLAN-7',expected_revision:7,label:'合成任务',stages:['ingest'],missing:[],recipients:[],context_scope:'当前范围',...(body.action==='document'?{document_preview:state.documentPreview}:{})};
      }
      assert.equal(path,root+'/actions');state.posts.push(structuredClone(body));state.receiptAtPost=session.getItem('ra-action-submission-'+id);
      return scenario.submit?await scenario.submit(body,signal):{id:'TASK-1',idempotency_key:body.idempotency_key,status:'running',action:body.action};
    };
    const setTasks=fn=>{tasks=state.tasks=fn(state.tasks);},recordProgressSubmission=task=>{state.recorded=task;},setTasksLoaded=value=>{tasksLoaded=state.tasksLoaded=value;};
    const setBenchmarkSubmission=value=>{state.benchmark=value;},setInterviewSubmission=value=>{state.interview=value;};
    const setChapterSubmission=value=>{state.chapter=value;},setHelpText=value=>{state.helpText=value;},setHelp=value=>{state.help=value;};
    const setTaskContinuation=value=>{state.continuation=value;};
    const refresh=async()=>{state.refreshes++;if(scenario.failRefresh)throw Error('refresh offline');if(scenario.fetchTasks){tasks=state.tasks=await scenario.fetchTasks();reconcile();}};
    const onRefreshFail=error=>{state.refreshError=error.message;},activityNotice=value=>{state.notice=value;};
    const requestAnimationFrame=fn=>fn();
    ${actual('act')}
    ${actual('saveActionRecovery')}
    ${actual('clearActionRecovery')}
    ${actual('settleActionReceipt')}
    ${actual('recordSubmittedTask')}
    ${actual('restoreSubmittedAction')}
    ${actual('planAction')}
    ${actual('confirmPrdGeneration')}
    ${actual('submitAction')}
    ${actual('continueActionSubmission')}
    ${actual('showMain')}
    ${actual('retry')}
    const restore=${effect('readActionRecovery(id)')};
    const reconcile=${effect('value.idempotency_key===prepared.key')};
    restore();reconcile();
    const localPrdPreview=state.preview;
    const mutate=async(path,body)=>{state.localWrite={path,body};};
    const setLocalPrdPreview=value=>{state.preview=value;};
    const revealDocument=kind=>{state.revealed=kind;},openDetails=(step,kind,after)=>{state.opened={step,kind};after?.();};
    const onGenerate=${callback('DocumentStudio','onGenerate')};
    const onRecommend=${callback('ConnectedWorkspace','onRecommendRequirement')};
    const onLocalConfirm=${callback('LocalPrdPreviewDialog','onSubmit')};
    const onPrdConfirm=${callback('PrdGenerationPreviewDialog','onSubmit')};
    const onPrdCancel=${callback('PrdGenerationPreviewDialog','onClose')};
    return {state,scenario,storage,session,projectRef,currentView,actionIntent,actionFlight,busyRef,actionRecoveryRef,analysisNavigation,prewriteNavigation,documentPreparationNavigation,ApiError,
      onGenerate,onRecommend,onLocalConfirm,onPrdConfirm,onPrdCancel,leave:showMain,
      confirmPreview:()=>act(confirmPrdGeneration),
      restore,reconcile,refresh,clearRecovery:clearActionRecovery,
      renderRecovery:()=>{${declaration('actionRecoveryPending')}return React.createElement(React.Fragment,null,${recoveryBanner});},
      observe:values=>{tasks=state.tasks=values;reconcile();},
      continueSubmission:()=>act(continueActionSubmission),
      click:(action='organize',text='原始输入',extra)=>act(()=>planAction(action,undefined,undefined,text,extra))};
  }
`,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext,jsx:ts.JsxEmit.React}}).outputText;
const fixture=new Function('React',compiled+';return fixture;')(React);
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
const flush=()=>new Promise(resolve=>setTimeout(resolve,0));
'''


def test_real_document_click_previews_then_explicit_confirmation_submits_once():
    run_ts(LOAD + r'''
      const f=fixture();assert.deepEqual(f.state.events,[],'construct/render handlers performs no request');
      await f.onGenerate();
      assert.equal(f.state.plans.length,1);assert.equal(f.state.posts.length,0);
      assert.deepEqual(f.state.prdGenerationPreview.plan.document_preview,f.state.documentPreview);
      f.onPrdConfirm();await flush();
      assert.equal(f.state.plans.length,1);assert.equal(f.state.posts.length,1);
      assert.deepEqual(f.state.posts[0],{...f.state.plans[0],plan_hash:'PLAN-7',idempotency_key:'key-1',authorize:true});
      assert.equal(f.state.posts[0].action,'document');assert.equal(f.state.posts[0].document_type,'prd');
      assert.equal(f.state.recovery,null);assert.equal(f.state.tasks[0].id,'TASK-1');assert.equal(f.busyRef.current,false);
      f.onPrdConfirm();await flush();assert.equal(f.state.posts.length,1,'the closed preview cannot submit again');
      const cancelled=fixture();await cancelled.onGenerate();cancelled.onPrdCancel();
      assert.equal(cancelled.state.prdGenerationPreview,null);assert.equal(cancelled.state.posts.length,0);
      const g=fixture();await g.onRecommend('REQ-1','只调整此条原文');
      assert.equal(g.state.posts.length,1);assert.deepEqual(g.state.posts[0].target,{kind:'item',id:'REQ-1'});
      assert.equal(g.state.posts[0].message,'只调整此条原文');
    ''')


def test_missing_or_mismatching_plan_never_posts_and_legacy_flags_do_not_pause():
    run_ts(LOAD + r'''
      for(const needs_authorization of [false,true]){
        const f=fixture({plan:async()=>({plan_hash:'PLAN-7',expected_revision:7,missing:[],recipients:[{needs_authorization}]})});
        await f.click();assert.equal(f.state.posts.length,1);assert.equal(f.state.recovery,null);
      }
      for(const plan of [{plan_hash:'P',expected_revision:7,missing:['先保存资料'],recipients:[]},
                         {plan_hash:'P',expected_revision:8,missing:[],recipients:[]}]){
        const f=fixture({plan:async()=>plan});await f.click();await flush();
        assert.equal(f.state.posts.length,0);assert.equal(f.state.keys,0);assert(f.state.error);
      }
    ''')


def test_rapid_double_click_keeps_one_plan_and_one_task_with_outer_activity_lock():
    run_ts(LOAD + r'''
      const gate=deferred(),f=fixture({plan:()=>gate.promise});
      const first=f.onGenerate(),second=f.onGenerate();
      await flush();
      assert.equal(f.state.plans.length,1);assert.equal(f.state.posts.length,0);
      gate.resolve({plan_hash:'P',expected_revision:7,missing:[],recipients:[],document_preview:f.state.documentPreview});
      await Promise.all([first,second]);assert.equal(f.state.posts.length,0);assert.equal(f.state.keys,1);
      await Promise.all([f.confirmPreview(),f.confirmPreview()]);assert.equal(f.state.posts.length,1);
      assert.equal(f.state.tasks.length,1);assert.equal(f.actionFlight.current,null);assert.equal(f.busyRef.current,false);
    ''')


def test_stale_server_rejection_and_changed_plan_input_do_not_retry_automatically():
    run_ts(LOAD + r'''
      for(const code of ['STALE_REVISION','STALE_PLAN']){
        const f=fixture();f.scenario.submit=async()=>{throw new f.ApiError(409,code,'内容已变化');};
        await f.click('change','未发送成功的人工输入');await flush();
        assert.equal(f.state.plans.length,1);assert.equal(f.state.posts.length,1);
        assert.equal(f.state.posts[0].message,'未发送成功的人工输入');assert.equal(f.state.stale,true);
        assert.equal(f.state.recovery,null);assert.equal(f.state.tasks.length,0);
      }
      const gate=deferred(),f=fixture({plan:()=>gate.promise});const pending=f.click();
      f.projectRef.current={...f.projectRef.current,revision:8};
      gate.resolve({plan_hash:'P',expected_revision:7,missing:[],recipients:[]});await pending;await flush();
      assert.equal(f.state.posts.length,0);assert.equal(f.state.plans.length,1);assert(f.state.error.includes('项目内容已更新'));
    ''')


def test_uncertain_submission_preserves_exact_request_and_only_explicit_continue_reuses_it():
    run_ts(LOAD + r'''
      for(const failure of [{status:0,kind:'network',message:'断线'},
                            {status:502,message:'网关响应未知'},{status:200,kind:'empty',message:'响应为空'}]){
        const f=fixture();let calls=0;f.scenario.submit=async body=>{if(!calls++)throw failure;return {id:'KNOWN',status:'running',action:body.action};};
        await f.click('change','请保留原对象和输入',{target:{kind:'item',id:'REQ-1'}});await flush();
        const prepared=structuredClone(f.state.recovery),sent=structuredClone(f.state.posts[0]);
        assert(prepared);assert.equal(prepared.key,sent.idempotency_key);assert.equal(f.state.posts.length,1);
        await f.click('organize','另一个输入');await flush();assert.equal(f.state.posts.length,1);assert.equal(f.state.plans.length,1);
        assert.deepEqual(f.state.recovery,prepared);
        await f.continueSubmission();assert.equal(f.state.posts.length,2);assert.deepEqual(f.state.posts[1],sent);
        assert.equal(f.state.plans.length,1);assert.equal(f.state.keys,1);assert.equal(f.state.recovery,null);
      }
    ''')


def test_navigation_cancels_pending_plan_but_accepted_task_refresh_failure_does_not_resubmit():
    run_ts(LOAD + r'''
      for(const leaveProject of [false,true]){
        const gate=deferred(),f=fixture({plan:()=>gate.promise});const pending=f.onGenerate();
        if(leaveProject)f.currentView.current.active=false;else f.leave();
        gate.resolve({plan_hash:'P',expected_revision:7,missing:[],recipients:[]});await pending;
        assert.equal(f.state.posts.length,0);assert.equal(f.state.tasks.length,0);
      }
      const f=fixture({failRefresh:true});await f.click();await flush();
      assert.equal(f.state.posts.length,1);assert.equal(f.state.tasks.length,1);assert.equal(f.state.recovery,null);
      assert(f.state.notice.includes('任务已提交'));assert.equal(f.state.plans.length,1);
    ''')


def test_document_preparation_and_local_prd_still_need_their_manual_content_confirmation():
    run_ts(LOAD + r'''
      const blocked=fixture();blocked.storage.set('P-document-preparation','TASK-DRAFT');
      await blocked.onGenerate();assert.equal(blocked.state.events.length,0);assert.equal(blocked.state.reviewRequested,true);
      const prep=fixture({preparationIssues:['必填草稿未完整']});await prep.onGenerate();
      assert.equal(prep.state.posts.length,1);assert.equal(prep.state.posts[0].action,'organize');
      assert.deepEqual(prep.state.posts[0].draft_request,{mode:'required'});
      assert.equal(prep.storage.get('P-document-preparation'),'TASK-1');
      assert.equal(prep.state.localWrite,undefined,'AI generation cannot confirm saved content');
      const local=fixture();assert.equal(local.state.localWrite,undefined);
      local.onLocalConfirm();await flush();
      assert.deepEqual(local.state.localWrite,{path:'/documents/prd/local-review',body:{expected_revision:7,idempotency_key:'key-1',review_draft_confirmed:true,preview_hash:'LOCAL-PREVIEW'}});
      assert.equal(local.state.plans.length,0);assert.equal(local.state.posts.length,0);
      assert.equal(local.state.preview,null);assert.equal(local.state.revealed,'prd');
    ''')
