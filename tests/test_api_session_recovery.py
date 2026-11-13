"""Fetch-level recovery checks for the shipped API, with no server or model calls.

These assertions exercise bundled production functions; the fake scheduler only
controls network completion/time.
"""
from tests.test_scope_frontend import run_ts


LOAD = r'''
import assert from 'node:assert/strict';
import {build} from 'esbuild';
const built=await build({stdin:{contents:"export {api,setCsrf,requestTimeout,ApiError} from './src/api'; export {activitySnapshot} from './src/activity';",resolveDir:process.cwd()},bundle:true,write:false,format:'cjs',platform:'node'});
const flush=async()=>{for(let i=0;i<30;i++)await Promise.resolve();};
const json=(status,body,extra={})=>({status,body,...extra});
const denied=extra=>json(403,{code:'CSRF_DENIED',message:'old session'},extra);
function fixture(steps){
  let now=0,sequence=0;const timers=new Map(),queue=[...steps];
  const state={requests:[],aborts:0,settled:false};
  const setTimeout=(fn,ms)=>{const id=++sequence;timers.set(id,{at:now+ms,fn});return id;};
  const clearTimeout=id=>timers.delete(id);
  const fetch=(url,options={})=>new Promise((resolve,reject)=>{
    const request={url,method:options.method||'GET',headers:{...options.headers},body:options.body,credentials:options.credentials,cache:options.cache,signal:options.signal};
    state.requests.push(request);
    const step=queue.shift();if(!step){reject(Error('Unexpected fetch'));return;}
    let done=false,delay;
    const cleanup=()=>{if(delay!==undefined)clearTimeout(delay);options.signal?.removeEventListener('abort',abort);};
    const abort=()=>{if(done)return;done=true;state.aborts++;cleanup();reject(new DOMException('aborted','AbortError'));};
    if(options.signal?.aborted){abort();return;}
    options.signal?.addEventListener('abort',abort,{once:true});
    const deliver=()=>{
      if(done)return;done=true;cleanup();
      if(step.network){reject(step.network);return;}
      const text=step.raw??JSON.stringify(step.body);
      resolve({ok:step.status>=200&&step.status<300,status:step.status,text:async()=>text,json:async()=>JSON.parse(text)});
    };
    if(step.hang)return;
    if(step.delay)delay=setTimeout(deliver,step.delay);else Promise.resolve().then(deliver);
  });
  const module={exports:{}};
  new Function('module','exports','fetch','setTimeout','clearTimeout','Date',built.outputFiles[0].text)(module,module.exports,fetch,setTimeout,clearTimeout,{now:()=>now});
  module.exports.setCsrf('OLD-TOKEN');
  const advance=async ms=>{
    const end=now+ms;
    while(true){const next=[...timers].filter(([,t])=>t.at<=end).sort((a,b)=>a[1].at-b[1].at)[0];if(!next)break;now=next[1].at;timers.delete(next[0]);next[1].fn();await flush();}
    now=end;await flush();
  };
  const start=(path='/projects/P/actions',body={message:'人工原话',idempotency_key:'FIRST-EXPLICIT-CLICK'},signal)=>{
    state.settled=false;delete state.error;delete state.value;
    const promise=module.exports.api(path,'POST',body,signal);
    promise.then(value=>{state.settled=true;state.value=value;},error=>{state.settled=true;state.error=error;});
    return promise;
  };
  return {...module.exports,state,start,advance,pendingTimers:()=>timers.size,remainingSteps:()=>queue.length};
}
function observedSignal(){
  const controller=new AbortController(),signal=controller.signal,listeners=new Set();
  const add=signal.addEventListener.bind(signal),remove=signal.removeEventListener.bind(signal);
  signal.addEventListener=(type,fn,options)=>{if(type==='abort')listeners.add(fn);return add(type,fn,options);};
  signal.removeEventListener=(type,fn,options)=>{if(type==='abort')listeners.delete(fn);return remove(type,fn,options);};
  return {controller,signal,listeners};
}
function originalError(error,path='/projects/P/actions'){
  assert.equal(error.status,403);assert.equal(error.code,'CSRF_DENIED');assert.equal(error.kind,'json');assert.equal(error.path,path);
  return true;
}
function assertNoMoreWrites(f,count){assert.equal(f.state.requests.filter(r=>r.method!=='GET').length,count);}
function assertClean(f){assert.equal(f.pendingTimers(),0);assert.equal(f.activitySnapshot().active.length,0);}
'''


def test_csrf_reconnect_never_replays_post_and_requires_second_explicit_call():
    run_ts(LOAD + r'''
      const f=fixture([denied(),json(200,{csrf:'NEW-TOKEN'}),json(200,{id:'TASK-SECOND'})]);
      const input={message:'未发送前的人工原话\n😀',idempotency_key:'CLICK-1',target:{kind:'item',id:'REQ-ORIGINAL'}};
      const before=structuredClone(input),first=f.start('/projects/P/actions',input);
      await assert.rejects(first,error=>{
        originalError(error);assert.match(error.message,/已(?:重新)?连接/);assert(error.message.includes('未提交'));assert(error.message.includes('原按钮'));return true;
      });
      assert.deepEqual(input,before,'the transport must not clear or rewrite user input');
      assert.deepEqual(f.state.requests.map(r=>[r.url,r.method]),[['/api/projects/P/actions','POST'],['/api/session','GET']]);
      const [post,session]=f.state.requests;
      assert.equal(post.headers['X-CSRF-Token'],'OLD-TOKEN');assert.deepEqual(JSON.parse(post.body),before);
      assert.equal(session.credentials,'same-origin');assert.equal(session.cache,'no-store');assert.equal(session.body,undefined);
      assert.equal(session.signal,post.signal,'recovery must use the original cancellation/deadline signal');
      assert.equal(f.activitySnapshot().error,true);assertClean(f);
      await f.advance(500000);assert.equal(f.state.requests.length,2);assertNoMoreWrites(f,1);
      const secondInput={...input,idempotency_key:'CLICK-2'};
      const result=await f.start('/projects/P/actions',secondInput);
      assert.equal(result.id,'TASK-SECOND');assert.equal(f.state.requests.length,3);
      assert.equal(f.state.requests[2].headers['X-CSRF-Token'],'NEW-TOKEN');assert.deepEqual(JSON.parse(f.state.requests[2].body),secondInput);
      assertNoMoreWrites(f,2);assertClean(f);assert.equal(f.activitySnapshot().error,false);
    ''')


def test_reconnect_failure_preserves_original_error_and_old_token_without_retry():
    run_ts(LOAD + r'''
      for(const bad of [json(503,{code:'DOWN'}),denied(),{network:Error('offline')},json(200,null),json(200,{}),json(200,{csrf:''}),json(200,{csrf:'   '}),json(200,{csrf:23}),json(200,{csrf:{token:'BAD'}}),{status:200,raw:'<html>not json</html>'}]){
        const f=fixture([denied(),bad,json(200,{id:'ONLY-USER-RETRY'})]);
        await assert.rejects(f.start(),error=>{originalError(error);assert(error.message.includes('重新打开'));assert.doesNotMatch(error.message,/已(?:重新)?连接/);return true;});
        assert.deepEqual(f.state.requests.map(r=>[r.url,r.method]),[['/api/projects/P/actions','POST'],['/api/session','GET']]);
        assert.equal(f.state.requests[1].cache,'no-store');assertNoMoreWrites(f,1);assertClean(f);
        await f.advance(500000);assert.equal(f.state.requests.length,2);
        await f.start();assert.equal(f.state.requests[2].headers['X-CSRF-Token'],'OLD-TOKEN','failed recovery cannot poison the stored token');assertClean(f);
      }
    ''')


def test_other_http_errors_never_refresh_session_or_retry_any_request():
    run_ts(LOAD + r'''
      for(const rejected of [json(403,{code:'FORBIDDEN',message:'no'}),json(401,{code:'CSRF_DENIED',message:'sign in'}),json(500,{code:'CSRF_DENIED',message:'server'}),json(422,{code:'VALIDATION',message:'input'}),{status:403,raw:'denied'}, {status:403,raw:''},json(403,{message:'permission denied'}),json(403,{code:'csrf_denied',message:'wrong code'})]){
        const f=fixture([rejected]);
        await assert.rejects(f.start(),error=>error.status===rejected.status);
        assert.equal(f.state.requests.length,1);assertNoMoreWrites(f,1);assertClean(f);
        await f.advance(500000);assert.equal(f.state.requests.length,1);
      }
    ''')


def test_unknown_network_result_never_refreshes_session_or_replays_write():
    run_ts(LOAD + r'''
      const f=fixture([{network:Error('lost after possible acceptance')}]);
      await assert.rejects(f.start(),error=>error.code==='NETWORK'&&error.status===0&&error.message.includes('服务端是否已处理尚未确认'));
      assert.equal(f.state.requests.length,1);assertNoMoreWrites(f,1);assertClean(f);
      await f.advance(500000);assert.equal(f.state.requests.length,1);
    ''')


def test_recovery_uses_remaining_total_timeout_and_never_exposes_new_token_after_abort():
    run_ts(LOAD + r'''
      const f=fixture([denied({delay:20000}),{hang:true},json(200,{id:'EXPLICIT-LATER'})]);
      const pending=f.start('/projects/P',{name:'人工名称'}),deadline=f.requestTimeout('/projects/P','POST');
      assert.equal(deadline,30000);
      await f.advance(20000);assert.equal(f.state.requests.length,2);assert.equal(f.state.settled,false);
      assert.equal(f.state.requests[1].signal,f.state.requests[0].signal);assert.equal(f.state.requests[1].cache,'no-store');
      await f.advance(9999);assert.equal(f.state.settled,false);
      await f.advance(1);await assert.rejects(pending);
      assert.equal(f.state.aborts,1);assert.equal(f.state.requests[1].signal.aborted,true);assertNoMoreWrites(f,1);assertClean(f);
      assert.doesNotMatch(f.state.error.message,/已(?:重新)?连接/);
      await f.advance(500000);assert.equal(f.state.requests.length,2);
      await f.start('/projects/P',{name:'人工名称'});assert.equal(f.state.requests[2].headers['X-CSRF-Token'],'OLD-TOKEN');assertClean(f);
    ''')


def test_cancel_during_session_recovery_cleans_listener_timer_and_activity_without_retry():
    run_ts(LOAD + r'''
      const f=fixture([denied(),{hang:true},json(200,{id:'LATER-CLICK'})]),outer=observedSignal();
      const pending=f.start('/projects/P/actions',{message:'保留输入'},outer.signal);
      await flush();assert.equal(f.state.requests.length,2);assert.equal(outer.listeners.size,1);
      outer.controller.abort();await assert.rejects(pending);
      assert.equal(f.state.aborts,1);assert.equal(f.state.requests[1].signal.aborted,true);
      assert.doesNotMatch(f.state.error.message,/已(?:重新)?连接/);
      assert.equal(outer.listeners.size,0);assertNoMoreWrites(f,1);assertClean(f);
      await f.advance(500000);assert.equal(f.state.requests.length,2);
      await f.start();assert.equal(f.state.requests[2].headers['X-CSRF-Token'],'OLD-TOKEN');assertClean(f);
    ''')


def test_cancel_before_original_response_never_attempts_session_recovery():
    run_ts(LOAD + r'''
      const f=fixture([{hang:true}]),outer=observedSignal(),pending=f.start('/projects/P/actions',{},outer.signal);
      assert.equal(f.state.requests.length,1);outer.controller.abort();await assert.rejects(pending);
      assert.equal(f.state.requests.length,1);assert.equal(f.state.aborts,1);assert.equal(outer.listeners.size,0);assertClean(f);
      await f.advance(500000);assert.equal(f.state.requests.length,1);
    ''')


def test_successful_requests_do_not_refresh_and_remove_external_abort_listener():
    run_ts(LOAD + r'''
      const f=fixture([json(200,{id:'ORIGINAL-TASK'})]),outer=observedSignal();
      assert.equal((await f.start('/projects/P/actions',{message:'一次点击'},outer.signal)).id,'ORIGINAL-TASK');
      assert.equal(f.state.requests.length,1);assert.equal(f.state.requests[0].headers['X-CSRF-Token'],'OLD-TOKEN');
      assert.equal(outer.listeners.size,0);assertClean(f);outer.controller.abort();assert.equal(f.state.aborts,0);
    ''')
