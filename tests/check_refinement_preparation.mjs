// Tests the real TypeScript coordinator with controlled transport promises.
// No HTTP server, daily data, browser, or external model is used.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import ts from '../web/node_modules/typescript/lib/typescript.js';
const source=fs.readFileSync(new URL('../web/src/refinement-preparation.ts',import.meta.url),'utf8');
const compiled=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText;
const {RefinementPreparation}=await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);
const results=[];
const flush=()=>new Promise(resolve=>setTimeout(resolve,0));
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};};
const task=(id='TASK-1',status='running',action='organize')=>({id,status,action,message:'合成任务失败原因',retry_input:{action}});
const ready=(status='needs_organize')=>({revision:3,input_hash:'input-1',status,needs_organize:status==='needs_organize',auto_allowed:status==='needs_organize',reason:'已保存的资料待整理',retry_requires_explicit:true,source_ids:['SRC-1'],entry_mode:'chat'});
function fixture(){
  const state={status:ready(),active:true,plans:[],posts:[],records:[],states:[],errors:[],navigations:0,refreshes:0,authorization:null,keys:0,tasks:[],receipt:null};
  const deps={active:()=>state.active,blocked:()=>'',status:async()=>state.status,tasks:async()=>state.tasks,
    plan:async input=>{state.plans.push(input);return {plan_hash:'hash',recipients:[],missing:[],expected_revision:input.expected_revision};},
    submit:async request=>{state.posts.push(structuredClone(request));return task();},
    refresh:async()=>{state.refreshes++;},record:value=>state.records.push(value),navigate:consume=>{if(consume())state.navigations++;},
    state:value=>state.states.push(value),authorization:value=>{state.authorization=value;},error:(message,stale)=>state.errors.push({message,stale}),key:()=>`key-${++state.keys}`,
    receipt:()=>structuredClone(state.receipt),saveReceipt:value=>{state.receipt=structuredClone(value);}};
  return {state,deps,controller:new RefinementPreparation(()=>deps)};
}
async function check(name,run){await run();results.push({name,status:'passed'});}

await check('double click shares one plan and task, matching completion enters once',async()=>{
  const {state,deps,controller}=fixture(), gate=deferred();deps.status=()=>gate.promise;
  const one=controller.prepare(),two=controller.prepare();assert.equal(one,two);gate.resolve(ready());await one;
  assert.equal(state.posts.length,1);assert.equal(state.plans.length,1);assert.equal(state.posts[0].authorize,false);
  state.status=ready('ready');deps.status=async()=>state.status;
  controller.observe([task('OTHER','succeeded')]);await flush();assert.equal(state.navigations,0);
  controller.observe([task('TASK-1','succeeded')]);controller.observe([task('TASK-1','succeeded')]);await flush();
  assert.equal(state.navigations,1);assert.equal(state.posts.length,1);
});
await check('new recipient requires explicit existing authorization dialog',async()=>{
  const {state,deps,controller}=fixture();const original=deps.plan;deps.plan=async input=>({...await original(input),recipients:[{needs_authorization:true}]});
  await controller.prepare();assert.equal(state.posts.length,0);assert.equal(state.states.at(-1).state,'authorization');
  await controller.authorize(state.authorization.preparationToken);assert.equal(state.posts.length,1);assert.equal(state.posts[0].authorize,true);
});
await check('ready with limits reuses result without plan or model submission',async()=>{
  const {state,controller}=fixture();state.status=ready('ready_with_limits');await controller.prepare();
  assert.equal(state.navigations,1);assert.equal(state.plans.length,0);assert.equal(state.posts.length,0);
});
await check('running organize is attached while unrelated running task is not',async()=>{
  const f=fixture();f.state.status={...ready('running'),task_id:'EXISTING'};f.state.tasks=[task('EXISTING')];
  await f.controller.prepare();assert.equal(f.state.states.at(-1).taskId,'EXISTING');assert.equal(f.state.posts.length,0);
  const g=fixture();g.state.status={...ready('running'),task_id:'INTERVIEW'};g.state.tasks=[task('INTERVIEW','running','interview')];
  await g.controller.prepare();assert.equal(g.state.states.at(-1).state,'failed');assert.equal(g.state.posts.length,0);
});
await check('failed task stays put, requires explicit retry and new key',async()=>{
  const {state,controller}=fixture();await controller.prepare();controller.observe([task('TASK-1','failed')]);await flush();
  assert.equal(state.states.at(-1).state,'failed');assert.equal(state.navigations,0);
  state.status=ready('failed');await controller.prepare();assert.equal(state.posts.length,1);
  await controller.prepare(true);assert.equal(state.posts.length,2);assert.notEqual(state.posts[0].key,state.posts[1].key);
});
await check('lost POST response retry reuses exact input, plan and key',async()=>{
  const {state,deps,controller}=fixture();deps.submit=async request=>{state.posts.push(structuredClone(request));if(state.posts.length===1)throw {status:0,kind:'network',message:'断线'};return task();};
  await controller.prepare();assert.equal(state.states.at(-1).state,'failed');
  await controller.prepare(true);assert.equal(state.posts.length,2);
  for(const key of ['input','plan','key','authorize'])assert.deepEqual(state.posts[0][key],state.posts[1][key]);
  assert.equal(state.plans.length,1);assert.equal(state.receipt,null);
});
await check('lost response discovers existing task without another POST',async()=>{
  const {state,deps,controller}=fixture();deps.submit=async request=>{state.posts.push(request);throw {status:0,kind:'network',message:'断线'};};
  await controller.prepare();state.status={...ready('running'),task_id:'KNOWN'};state.tasks=[task('KNOWN')];await controller.prepare(true);
  assert.equal(state.posts.length,1);assert.equal(state.states.at(-1).taskId,'KNOWN');
});
await check('accepted task with failed refresh remains attached and can finish',async()=>{
  const {state,deps,controller}=fixture();deps.refresh=async()=>{if(!state.refreshes++)throw Error('refresh failed');};
  await controller.prepare();assert.equal(state.states.at(-1).state,'running');
  state.status=ready('ready');controller.observe([task('TASK-1','succeeded')]);await flush();
  assert.equal(state.navigations,1);assert.equal(state.posts.length,1);
});
await check('switching project before submission response forbids late navigation',async()=>{
  const {state,deps,controller}=fixture(),gate=deferred();deps.submit=async request=>{state.posts.push(request);return gate.promise;};
  const pending=controller.prepare();await flush();state.active=false;controller.cancel();gate.resolve(task('TASK-1','succeeded'));await pending;
  state.active=true;state.status=ready('ready');controller.observe([task('TASK-1','succeeded')]);await flush();
  assert.equal(state.navigations,0);assert.equal(state.records.length,1);
});
await check('cancelling before readiness returns prevents task creation',async()=>{
  const {state,deps,controller}=fixture(),gate=deferred();deps.status=()=>gate.promise;
  const pending=controller.prepare();controller.cancel();gate.resolve(ready());await pending;
  assert.equal(state.posts.length,0);assert.equal(state.navigations,0);
});
await check('cancelled slow readiness does not consume a new entry or clear its flight',async()=>{
  const {state,deps,controller}=fixture(),oldRead=deferred(),newRead=deferred();let reads=0;
  deps.status=()=>++reads===1?oldRead.promise:newRead.promise;
  const previous=controller.prepare();state.active=false;controller.cancel();
  state.active=true;const returning=controller.prepare();assert.notEqual(returning,previous);
  assert.equal(reads,2);oldRead.resolve(ready());await previous;
  assert.equal(state.posts.length,0);assert.equal(state.navigations,0);
  assert.equal(controller.prepare(),returning,'old completion must not detach the current flight');
  newRead.resolve(ready());await returning;
  assert.equal(state.plans.length,1);assert.equal(state.posts.length,1);
  assert.equal(state.states.at(-1).state,'running');
});
await check('cancel and reenter before POST completes reuses one idempotent task',async()=>{
  const {state,deps,controller}=fixture(),response=deferred(),created=new Map();
  deps.submit=async request=>{
    state.posts.push(structuredClone(request));
    if(!created.has(request.key))created.set(request.key,response.promise);
    return created.get(request.key);
  };
  const previous=controller.prepare();await flush();assert.equal(state.posts.length,1);
  const sent=structuredClone(state.receipt);assert.ok(sent);
  state.active=false;controller.cancel();assert.deepEqual(state.receipt,sent);
  state.active=true;const returning=controller.prepare();assert.notEqual(returning,previous);await flush();
  assert.equal(state.posts.length,2);assert.equal(created.size,1);
  for(const key of ['input','plan','key','authorize'])assert.deepEqual(state.posts[0][key],state.posts[1][key]);
  assert.equal(state.plans.length,1);assert.equal(state.keys,1);
  response.resolve(task());await Promise.all([previous,returning]);
  assert.equal(state.receipt,null);assert.equal(state.states.at(-1).state,'running');
  assert.equal(state.navigations,0);
  state.status=ready('ready');controller.observe([task('TASK-1','succeeded')]);await flush();
  assert.equal(state.navigations,1);assert.equal(created.size,1);assert.equal(state.posts.length,2);
});
await check('changed inputs after task completion do not navigate or auto-retry',async()=>{
  const {state,controller}=fixture();await controller.prepare();controller.observe([task('TASK-1','partial')]);await flush();
  assert.equal(state.navigations,0);assert.equal(state.posts.length,1);assert.equal(state.states.at(-1).state,'failed');
});
await check('empty project gives a local explanation and performs no model action',async()=>{
  const {state,controller}=fixture();state.status=ready('empty');await controller.prepare();
  assert.equal(state.posts.length,0);assert.equal(state.states.at(-1).state,'failed');assert.equal(state.navigations,0);
});
await check('cancel and controller reload preserve an uncertain submitted receipt',async()=>{
  const {state,deps,controller}=fixture();deps.submit=async request=>{state.posts.push(structuredClone(request));if(state.posts.length===1)throw {status:0,kind:'network',message:'断线'};return task();};
  await controller.prepare();const original=structuredClone(state.receipt);assert.ok(original);
  controller.cancel();assert.deepEqual(state.receipt,original);
  const remounted=new RefinementPreparation(()=>deps);await remounted.prepare();
  assert.equal(state.posts.length,2);assert.equal(state.posts[0].key,state.posts[1].key);assert.equal(state.plans.length,1);assert.equal(state.receipt,null);
});
await check('definitive modal rejection closes dialog, then explicit retry works',async()=>{
  const {state,deps,controller}=fixture();const original=deps.plan;deps.plan=async input=>({...await original(input),recipients:[{needs_authorization:true}]});
  deps.submit=async request=>{state.posts.push(structuredClone(request));if(state.posts.length===1)throw {status:409,code:'STALE_REVISION',message:'版本已变化'};return task();};
  await controller.prepare();await controller.authorize(state.authorization.preparationToken);
  assert.equal(state.authorization,null);assert.equal(state.states.at(-1).state,'failed');assert.equal(state.receipt,null);
  await controller.prepare(true);await controller.authorize(state.authorization.preparationToken);
  assert.equal(state.posts.length,2);assert.notEqual(state.posts[0].key,state.posts[1].key);
});
await check('old failure visible during unknown submission does not create a fresh key',async()=>{
  const {state,deps,controller}=fixture();state.status=ready('failed');
  deps.submit=async request=>{state.posts.push(structuredClone(request));if(state.posts.length===1)throw {status:0,kind:'network',message:'断线'};return task();};
  await controller.prepare(true);controller.cancel();await controller.prepare(true);
  assert.equal(state.posts.length,2);assert.equal(state.posts[0].key,state.posts[1].key);assert.equal(state.plans.length,1);
});
await check('deferred dirty-draft navigation is cancelled when user leaves',async()=>{
  const {state,deps,controller}=fixture();let consume;
  deps.navigate=value=>{consume=value;};state.status=ready('ready');await controller.prepare();
  assert.equal(typeof consume,'function');controller.cancel();assert.equal(consume(),false);assert.equal(state.navigations,0);
});
await check('local busy rejection cannot discard an earlier unknown server receipt',async()=>{
  const {state,deps,controller}=fixture();deps.submit=async request=>{state.posts.push(structuredClone(request));throw {status:0,kind:'network',message:'断线'};};
  await controller.prepare();const key=state.receipt.key;
  deps.submit=async()=>{throw Error('当前操作尚未完成');};await controller.prepare(true);
  assert.equal(state.receipt.key,key);assert.equal(state.plans.length,1);
});
await check('default full draft uses server contract without fabricating a user message or extra grant',async()=>{
  const {state,deps,controller}=fixture(),plan=deps.plan;
  deps.plan=async input=>({...await plan(input),recipients:input.message?[{needs_authorization:true}]:[]});
  await controller.prepare();
  assert.equal(state.posts.length,1);assert.equal(state.posts[0].input.message,'');
  assert.equal(state.posts[0].input.action,'organize');assert.equal(state.posts[0].authorize,false);
  assert.equal(state.authorization,null);
});
await check('partial full draft is shown without automatic retry or discarded human content',async()=>{
  const {state,controller}=fixture();await controller.prepare();
  state.status={...ready('ready_with_limits'),reason:'本次已起草，仍有两条验收未生成'};
  controller.observe([task('TASK-1','partial')]);await flush();
  assert.equal(state.navigations,1);assert.equal(state.posts.length,1);
  await controller.prepare();assert.equal(state.posts.length,1);assert.equal(state.plans.length,1);
});
const report={tests:results.length,external_model_requests:0,results};
if(process.argv[2]){fs.mkdirSync(path.dirname(process.argv[2]),{recursive:true});fs.writeFileSync(process.argv[2],JSON.stringify(report,null,2));}
console.log(JSON.stringify(report,null,2));
