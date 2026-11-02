"""Run the shipped transport, handlers, and restore effects without network or models."""
from tests.test_ai_direct_frontend import LOAD
from tests.test_scope_frontend import run_ts


API_LOAD = r'''
import assert from 'node:assert/strict';
import {build} from 'esbuild';
const built=await build({stdin:{contents:"export {api,requestTimeout} from './src/api'; export {activitySnapshot} from './src/activity';",resolveDir:process.cwd()},bundle:true,write:false,format:'cjs',platform:'node'});
function transportFixture(delay){
  let now=0,sequence=0;const timers=new Map(),state={requests:[],aborts:0,settled:false};
  const setTimeout=(fn,ms)=>{const id=++sequence;timers.set(id,{at:now+ms,fn});return id;};
  const clearTimeout=id=>timers.delete(id);
  const fetch=(url,options)=>new Promise((resolve,reject)=>{
    state.requests.push({url,options});
    const response=setTimeout(()=>resolve({ok:true,status:200,text:async()=>JSON.stringify({id:'TASK-EXACT',idempotency_key:'EXACT-KEY'})}),delay);
    options.signal.addEventListener('abort',()=>{state.aborts++;clearTimeout(response);reject(new DOMException('aborted','AbortError'));},{once:true});
  });
  const module={exports:{}};
  new Function('module','exports','fetch','setTimeout','clearTimeout','Date',built.outputFiles[0].text)(module,module.exports,fetch,setTimeout,clearTimeout,{now:()=>now});
  const microtasks=async()=>{for(let i=0;i<12;i++)await Promise.resolve();};
  const advance=async ms=>{
    const end=now+ms;
    while(true){const next=[...timers].filter(([,timer])=>timer.at<=end).sort((a,b)=>a[1].at-b[1].at)[0];if(!next)break;now=next[1].at;timers.delete(next[0]);next[1].fn();await microtasks();}
    now=end;await microtasks();
  };
  const start=path=>{const promise=module.exports.api(path,'POST',{idempotency_key:'EXACT-KEY'});promise.then(value=>{state.settled=true;state.value=value;},error=>{state.settled=true;state.error=error;});return promise;};
  return {...module.exports,state,advance,start};
}
'''


def test_real_transport_waits_for_40_second_plan_and_50_second_submission():
    run_ts(API_LOAD + r'''
      for(const [suffix,delay,label] of [['/actions/plan',40000,'正在核对本次任务'],['/actions',50000,'正在提交任务']]){
        const f=transportFixture(delay),pending=f.start('/projects/P'+suffix);
        assert.equal(f.activitySnapshot().active.length,1);assert.equal(f.activitySnapshot().active[0].label,label);
        await f.advance(30000);assert.equal(f.state.settled,false);assert.equal(f.state.aborts,0);assert.equal(f.activitySnapshot().active.length,1);
        await f.advance(delay-30000);const result=await pending;
        assert.equal(result.id,'TASK-EXACT');assert.equal(f.state.requests.length,1);assert.equal(f.state.aborts,0);
        assert.equal(f.activitySnapshot().active.length,0);assert.equal(f.activitySnapshot().error,false);
      }
    ''')


def test_real_transport_deadline_aborts_once_and_distinguishes_plan_from_unknown_submit():
    run_ts(API_LOAD + r'''
      for(const suffix of ['/actions/plan','/actions']){
        const f=transportFixture(500000),pending=f.start('/projects/P'+suffix),deadline=f.requestTimeout('/projects/P'+suffix,'POST');
        assert.equal(deadline,120000);
        await f.advance(deadline-1);assert.equal(f.state.settled,false);assert.equal(f.state.aborts,0);
        await f.advance(1);await assert.rejects(pending,error=>error.code==='REQUEST_TIMEOUT'&&error.path==='/projects/P'+suffix);
        assert.equal(f.state.aborts,1);assert.equal(f.state.requests.length,1);assert.equal(f.activitySnapshot().active.length,0);
        if(suffix.endsWith('/plan'))assert(f.state.error.message.includes('尚未提交 AI 任务'));
        else assert(f.state.error.message.includes('服务端是否已处理尚未确认'));
        await f.advance(500000);assert.equal(f.state.requests.length,1,'timeout must never retry automatically');
      }
    ''')


def test_receipt_exists_before_post_and_preserves_every_input_field():
    run_ts(LOAD + r'''
      const f=fixture();f.scenario.submit=async()=>{throw {status:0,kind:'network',message:'lost response'};};
      const extra={target:{kind:'item',id:'REQ-EXACT'},attachment_source_ids:['SRC-1'],draft_request:{mode:'question',question_id:'Q1'},allow_review_draft:true};
      await f.click('clarify','完整原话\n😀',extra);
      const before=JSON.parse(f.state.receiptAtPost),after=JSON.parse(f.storage.get('ra-action-submission-P'));
      assert.deepEqual(before,after);assert.equal(before.projectId,'P');assert.equal(before.key,f.state.posts[0].idempotency_key);
      assert.deepEqual({...before.input,plan_hash:before.plan.plan_hash,idempotency_key:before.key,authorize:true},f.state.posts[0]);
      assert.equal(before.input.message,'完整原话\n😀');for(const [key,value] of Object.entries(extra))assert.deepEqual(before.input[key],value);
      assert.equal(f.state.posts.length,1);assert.equal(f.state.plans.length,1);
    ''')


def test_pending_post_hides_only_local_recovery_warning_and_failure_reveals_it():
    run_ts(LOAD + r'''
      const gate=deferred(),f=fixture({submit:()=>gate.promise});
      const pending=f.click('review','仍在等待的原话');await flush();
      assert.equal(f.state.posts.length,1);assert.equal(f.state.plans.length,1);
      assert.equal(f.state.receiptAtPost,f.storage.get('ra-action-submission-P'),'receipt must be durable before the POST starts');
      assert(f.state.recovery);assert.equal(f.busyRef.current,true);
      assert.equal(renderToStaticMarkup(f.renderRecovery()),'','a normal local POST wait must not render unknown recovery UI');

      await f.click('organize','等待期间不能另建');
      assert.equal(f.state.posts.length,1);assert.equal(f.state.plans.length,1);
      gate.resolve({id:'TASK-WAITED',idempotency_key:f.state.posts[0].idempotency_key,status:'running',action:'review'});
      await pending;
      assert.equal(f.state.posts.length,1);assert.equal(f.state.tasks.length,1);
      assert.equal(f.state.recovery,null);assert.equal(f.busyRef.current,false);

      const failed=fixture();failed.scenario.submit=async()=>{throw {status:0,kind:'network',message:'lost response'};};
      await failed.click('review','失败后的原话');
      const unknown=renderToStaticMarkup(failed.renderRecovery());
      assert(unknown.includes('本次提交结果尚未确认'));
      assert(unknown.includes('继续同一次提交'));assert(unknown.includes('刷新任务状态'));
      assert.equal(failed.state.posts.length,1);
    ''')


def test_lost_post_response_finds_original_task_by_exact_key_on_refresh_without_resubmitting():
    run_ts(LOAD + r'''
      const f=fixture();f.scenario.submit=async()=>{throw {status:0,kind:'network',message:'lost response'};};await f.click('change','只调整这条',{target:{kind:'item',id:'REQ-1'}});
      const key=f.state.posts[0].idempotency_key;
      f.scenario.fetchTasks=async()=>[{id:'TASK-UNRELATED',idempotency_key:'other-key',action:'change',status:'succeeded'},{id:'TASK-ORIGINAL',idempotency_key:key,action:'change',status:'succeeded'}];
      await f.refresh();
      assert.equal(f.state.recovery,null);assert.equal(f.storage.get('ra-action-submission-P'),undefined);
      assert.equal(f.state.recorded.id,'TASK-ORIGINAL');assert.equal(f.state.recorded.retry_input.target.id,'REQ-1');
      assert.equal(f.state.recoveryNotice,'已找到原任务，继续查看。');assert.equal(f.state.actionError,'');
      assert.equal(f.state.posts.length,1);assert.equal(f.state.plans.length,1);
    ''')


def test_absent_original_or_failed_refresh_preserves_unknown_and_only_explicit_continue_posts_same_request():
    run_ts(LOAD + r'''
      const f=fixture();let sends=0;f.scenario.submit=async body=>{if(!sends++)throw {status:0,kind:'network',message:'lost response'};return {id:'TASK-ORIGINAL',idempotency_key:body.idempotency_key,status:'running',action:body.action};};
      await f.click('review','原评审要求');const original=structuredClone(f.state.posts[0]),receipt=structuredClone(f.state.recovery);
      for(const values of [[],[{id:'WRONG',idempotency_key:'unrelated',action:'review',status:'succeeded'}]]){f.observe(values);assert.deepEqual(f.state.recovery,receipt);}
      f.scenario.failRefresh=true;await assert.rejects(f.refresh(),/refresh offline/);assert.deepEqual(f.state.recovery,receipt);
      await f.click('organize','不能替换原任务');assert.equal(f.state.posts.length,1);assert.equal(f.state.plans.length,1);
      f.scenario.failRefresh=false;await f.continueSubmission();assert.equal(f.state.posts.length,2);assert.deepEqual(f.state.posts[1],original);
      assert.equal(f.state.keys,1);assert.equal(f.state.plans.length,1);assert.equal(f.state.recorded.id,'TASK-ORIGINAL');assert.equal(f.state.recovery,null);
    ''')


def test_reload_restores_receipt_without_any_automatic_plan_or_post_and_reattaches_original_task():
    run_ts(LOAD + r'''
      const first=fixture();first.scenario.submit=async()=>{throw {status:0,kind:'network',message:'lost response'};};await first.click('document','旧文档修改',{target:{kind:'document',id:'DOC-1',section_id:'S1'}});await first.confirmPreview();
      const second=fixture({storage:first.storage});
      assert.equal(second.state.events.length,0);assert.equal(second.state.keys,0);assert.deepEqual(second.state.recovery,JSON.parse(JSON.stringify(first.state.recovery)));
      assert(renderToStaticMarkup(second.renderRecovery()).includes('未找到原任务不代表未提交'));
      second.observe([{id:'TASK-BEFORE-RELOAD',idempotency_key:first.state.recovery.key,action:'document',status:'partial'}]);
      assert.equal(second.state.posts.length,0);assert.equal(second.state.plans.length,0);assert.equal(second.state.recorded.id,'TASK-BEFORE-RELOAD');
      assert.equal(second.state.recorded.retry_input.target.id,'DOC-1');assert.equal(second.state.recorded.retry_input.target.section_id,'S1');assert.equal(second.state.recovery,null);
    ''')


def test_separate_project_receipts_and_late_response_do_not_attach_or_clear_another_project():
    run_ts(LOAD + r'''
      const storage=new Map(),gate=deferred(),a=fixture({projectId:'A',storage,submit:()=>gate.promise});
      const pending=a.click('review','项目A原话');await flush();const receiptA=JSON.parse(storage.get('ra-action-submission-A'));
      a.currentView.current.active=false;
      const b=fixture({projectId:'B',storage});assert.equal(b.state.recovery,null);assert.equal(b.state.events.length,0);
      b.scenario.submit=async()=>{throw {status:0,kind:'network',message:'B lost response'};};await b.click('review','项目B原话');
      const receiptB=storage.get('ra-action-submission-B');assert.equal(JSON.parse(receiptB).projectId,'B');
      gate.resolve({id:'TASK-A',idempotency_key:receiptA.key,action:'review',status:'succeeded'});await pending;
      assert.equal(a.state.recorded.id,'TASK-A');assert.equal(b.state.recorded,undefined);assert.equal(storage.get('ra-action-submission-B'),receiptB);
      assert.equal(storage.get('ra-action-submission-A'),undefined);assert.equal(b.state.recovery.projectId,'B');
      assert.equal(b.state.posts[0].message,'项目B原话');assert.equal(a.state.posts[0].message,'项目A原话');
    ''')


def test_project_mismatched_receipt_is_not_loaded_and_late_response_cannot_record_into_changed_project():
    run_ts(LOAD + r'''
      const first=fixture();first.scenario.submit=async()=>{throw {status:0,kind:'network',message:'lost'};};await first.click();
      const storage=new Map([['ra-action-submission-OTHER',first.storage.get('ra-action-submission-P')]]),other=fixture({projectId:'OTHER',storage});
      assert.equal(other.state.recovery,null);assert.equal(other.state.events.length,0);assert(other.state.actionError.includes('恢复信息无法识别'));
      const gate=deferred(),late=fixture({submit:()=>gate.promise});const pending=late.click();await flush();
      late.projectRef.current={...late.projectRef.current,id:'OTHER'};gate.resolve({id:'OLD-PROJECT-TASK',idempotency_key:'key-1',action:'organize',status:'succeeded'});await pending;
      assert.equal(late.state.tasks.length,0);assert.equal(late.state.recorded,undefined);
    ''')


def test_definite_4xx_clears_receipt_and_retains_original_input_without_retrying():
    run_ts(LOAD + r'''
      for(const [status,code] of [[409,'STALE_REVISION'],[409,'STALE_PLAN'],[404,'NOT_FOUND'],[422,'VALIDATION']]){
        const f=fixture();f.scenario.submit=async()=>{throw new f.ApiError(status,code,'明确拒绝');};
        await f.click('clarify','保留人工原话',{target:{kind:'item',id:'REQ-1'}});
        assert.equal(f.state.recovery,null);assert.equal(f.storage.get('ra-action-submission-P'),undefined);
        assert.equal(f.state.posts.length,1);assert.equal(f.state.plans.length,1);assert.equal(f.state.posts[0].message,'保留人工原话');
        assert.equal(f.state.stale,status===409);assert.equal(f.state.tasks.length,0);
      }
    ''')


def test_storage_failure_keeps_page_recovery_and_renders_the_actual_nonpersistent_warning():
    run_ts(LOAD + r'''
      const f=fixture({storageFailure:true});f.scenario.submit=async()=>{throw {status:0,kind:'network',message:'lost response'};};await f.click('review','本页保留');
      assert.equal(f.state.persistent,false);assert(f.state.recovery);assert.equal(f.storage.size,0);
      assert.equal(JSON.parse(f.session.getItem('ra-action-submission-P')).input.message,'本页保留');
      const view=renderToStaticMarkup(f.renderRecovery());assert(view.includes('恢复信息仅暂存于本页，请勿刷新或关闭'));
      assert(view.includes('继续同一次提交'));assert(view.includes('刷新任务状态'));
      await f.click('review','新内容');assert.equal(f.state.posts.length,1);assert.equal(f.state.plans.length,1);
    ''')


def test_real_session_adapter_catches_browser_storage_failure_and_returns_nonpersistent_status():
    run_ts(r'''
      import assert from 'node:assert/strict';import fs from 'node:fs';import ts from 'typescript';
      const compiled=ts.transpileModule(fs.readFileSync('src/draft-storage.ts','utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
      const module={exports:{}};let writes=0;
      const window={sessionStorage:{getItem:()=>{throw Error('blocked');},setItem:()=>{writes++;throw Error('QuotaExceededError');},removeItem:()=>{throw Error('blocked');}}};
      new Function('window','module','exports',compiled)(window,module,module.exports);
      const {session}=module.exports;assert.equal(session.setItem('key','original receipt'),false);assert.equal(writes,1);
      assert.equal(session.getItem('key'),'original receipt');assert.equal(session.isPersistent('key'),false);
      session.removeItem('key');assert.equal(session.getItem('key'),null);assert.equal(session.isPersistent('key'),false);
      const stored=new Map();window.sessionStorage={getItem:key=>stored.get(key)||null,setItem:(key,value)=>stored.set(key,value),removeItem:key=>stored.delete(key)};
      assert.equal(session.isPersistent('key'),false);assert.equal(session.setItem('key','durable receipt'),true);assert.equal(session.isPersistent('key'),true);assert.equal(session.getItem('key'),'durable receipt');
    ''')


def test_corrupt_or_incomplete_receipt_never_triggers_an_automatic_request():
    run_ts(LOAD + r'''
      for(const value of ['{bad-json}',JSON.stringify({projectId:'P',key:'K'}),JSON.stringify({projectId:'P',input:{action:'anything'}})]){
        const storage=new Map([['ra-action-submission-P',value]]),f=fixture({storage});
        assert.equal(f.state.recovery,null);assert.equal(f.state.events.length,0);assert.equal(f.state.keys,0);
        assert(f.state.actionError.includes('恢复信息无法识别'));assert.equal(storage.has('ra-action-submission-P'),false);
      }
    ''')


def test_matching_original_task_is_attached_in_every_terminal_or_running_state():
    run_ts(LOAD + r'''
      for(const status of ['queued','running','partial','succeeded','failed','cancelled']){
        const f=fixture();f.scenario.submit=async()=>{throw {status:0,kind:'network',message:'lost response'};};await f.click('review','原范围');
        const original={id:'TASK-'+status,idempotency_key:f.state.recovery.key,action:'review',status};f.observe([original]);
        assert.equal(f.state.recorded.id,original.id);assert.equal(f.state.recorded.status,status);assert.equal(f.state.recovery,null);
        assert.equal(f.state.posts.length,1);assert.equal(f.state.plans.length,1);
      }
    ''')


def test_double_continue_waits_for_one_original_key_and_success_refresh_failure_does_not_retry():
    run_ts(LOAD + r'''
      const f=fixture();f.scenario.submit=async()=>{throw {status:0,kind:'network',message:'lost response'};};await f.click('review','原范围');
      const sent=structuredClone(f.state.posts[0]),gate=deferred();f.scenario.submit=()=>gate.promise;
      const first=f.continueSubmission(),second=f.continueSubmission();await flush();assert.equal(f.state.posts.length,2);
      gate.resolve({id:'TASK-ORIGINAL',idempotency_key:sent.idempotency_key,action:'review',status:'running'});await Promise.all([first,second]);
      assert.deepEqual(f.state.posts[1],sent);assert.equal(f.state.plans.length,1);assert.equal(f.state.keys,1);
      const failedRefresh=fixture({failRefresh:true});await failedRefresh.click();
      assert.equal(failedRefresh.state.posts.length,1);assert.equal(failedRefresh.state.recovery,null);assert.equal(failedRefresh.state.recorded.id,'TASK-1');
      assert(failedRefresh.state.notice.includes('任务已提交'));assert.equal(failedRefresh.storage.get('ra-action-submission-P'),undefined);
    ''')


def test_reload_terminal_task_stays_in_assistant_and_requires_explicit_exact_task_continuation():
    run_ts(LOAD + r'''
      for(const action of ['organize','document']){
        const first=fixture();first.scenario.submit=async()=>{throw {status:0,kind:'network',message:'lost'};};await first.click(action,'原任务');
            if(action==='document')await first.confirmPreview();
        const prepared=JSON.parse(first.storage.get('ra-action-submission-P'));
        const restored=fixture({storage:first.storage,tasks:[{id:'TASK-EXACT-'+action,idempotency_key:prepared.key,action,status:'succeeded'}]});
        assert.equal(restored.state.posts.length,0);assert.equal(restored.state.plans.length,0);
        assert.equal(restored.analysisNavigation.current,null,'finding a recovered result must not arm automatic refinement navigation');
        assert.equal(restored.prewriteNavigation.current,null,'finding a recovered document must not arm automatic document navigation');
        assert.equal(restored.documentPreparationNavigation.current,null);
        assert.deepEqual(restored.state.continuation,{id:'TASK-EXACT-'+action,retry:false},'the assistant offers this exact task for an explicit continue');
      }
    ''')


def test_reload_terminal_targeted_task_does_not_clear_unsent_local_input():
    run_ts(LOAD + r'''
      const first=fixture();first.scenario.submit=async()=>{throw {status:0,kind:'network',message:'lost'};};await first.click('clarify','原已发文字',{target:{kind:'item',id:'REQ-1'}});
      const key=first.state.recovery.key,restored=fixture({storage:first.storage,tasks:[{id:'TARGETED',idempotency_key:key,action:'clarify',status:'succeeded'}]});
      assert.equal(restored.state.helpText,'未发送本地补充','GET recovery may not clear a newer local input');
      assert.equal(restored.state.continuation.id,'TARGETED');assert.equal(restored.state.recovery,null);
    ''')


def test_terminal_get_releases_local_post_wait_by_abort_only_for_the_matching_project_and_key():
    run_ts(LOAD + r'''
      let signal,aborts=0;
      const f=fixture({submit:(body,value)=>{signal=value;return new Promise((resolve,reject)=>signal?.addEventListener('abort',()=>{aborts++;reject(new DOMException('aborted','AbortError'));},{once:true}));}});
      const pending=f.click('review','原评审');await flush();const key=f.state.posts[0].idempotency_key;
      f.observe([{id:'OTHER',idempotency_key:'different-key',action:'review',status:'succeeded'}]);await flush();
      assert.equal(aborts,0);assert.equal(f.busyRef.current,true);assert(f.state.recovery);
      f.observe([{id:'EXACT',idempotency_key:key,action:'review',status:'succeeded'}]);await flush();
      assert.equal(aborts,1,'terminal GET must abort the local wait, without cancelling the server task');
      await pending;assert.equal(f.busyRef.current,false);assert.equal(f.actionFlight.current,null);
      assert.equal(f.state.posts.length,1);assert.equal(f.state.recovery,null);assert.equal(f.state.tasks.find(task=>task.id==='EXACT').status,'succeeded');
    ''')


def test_terminal_get_and_new_navigation_are_not_regressed_by_late_post_running_reply():
    run_ts(LOAD + r'''
      const gate=deferred(),f=fixture({submit:()=>gate.promise});await f.click('document','当前文档');const pending=f.confirmPreview();await flush();const key=f.state.posts[0].idempotency_key;
      f.observe([{id:'EXACT',idempotency_key:key,action:'document',status:'succeeded'}]);f.leave();f.scenario.failRefresh=true;
      gate.resolve({id:'EXACT',idempotency_key:key,action:'document',status:'running'});await pending;
      assert.equal(f.state.tasks.find(task=>task.id==='EXACT').status,'succeeded','late POST may not replace a terminal GET result with running');
      assert.equal(f.prewriteNavigation.current,null,'late acknowledgement may not reinstate a navigation intent the user left');
      assert.equal(f.analysisNavigation.current,null);assert.equal(f.state.recovery,null);assert.equal(f.state.posts.length,1);
    ''')


def test_late_server_error_does_not_resurrect_unknown_after_original_task_was_found():
    run_ts(LOAD + r'''
      const gate=deferred(),f=fixture({submit:()=>gate.promise});const pending=f.click('review','原评审');await flush();const key=f.state.posts[0].idempotency_key;
      f.observe([{id:'EXACT',idempotency_key:key,action:'review',status:'succeeded'}]);
      gate.reject({status:503,message:'late gateway failure'});await pending;
      assert.equal(f.state.recovery,null,'known receipt cannot become unknown because a stale transport failed');
      assert.equal(f.storage.has('ra-action-submission-P'),false);assert.equal(f.state.tasks.find(task=>task.id==='EXACT').status,'succeeded');
      assert.equal(f.state.posts.length,1);
    ''')


def test_clearing_old_receipt_cannot_remove_a_newer_receipt_for_the_same_project():
    run_ts(LOAD + r'''
      const f=fixture();f.scenario.submit=async()=>{throw {status:0,kind:'network',message:'lost'};};await f.click('review','旧任务');
      const old=structuredClone(f.state.recovery),next={...old,key:'NEW-KEY',input:{...old.input,message:'新任务原话'}};
      f.storage.set('ra-action-submission-P',JSON.stringify(next));f.restore();f.clearRecovery(old);
      assert(f.storage.has('ra-action-submission-P'),'an old clear must not delete the newer receipt');
      assert.deepEqual(JSON.parse(f.storage.get('ra-action-submission-P')),JSON.parse(JSON.stringify(next)),'receipt removal must match both project and exact key');
      assert.equal(f.state.recovery.key,'NEW-KEY');
    ''')


def test_soft_remount_of_memory_only_receipt_keeps_the_nonpersistent_warning_visible():
    run_ts(LOAD + r'''
      const original=fixture({storageFailure:true});original.scenario.submit=async()=>{throw {status:0,kind:'network',message:'lost response'};};await original.click('review','本页原任务');
      assert.equal(original.storage.size,0);assert(renderToStaticMarkup(original.renderRecovery()).includes('恢复信息仅暂存于本页，请勿刷新或关闭'));
      const remounted=fixture({storage:original.storage,session:original.session});
      assert.equal(remounted.state.posts.length,0);assert.equal(remounted.state.plans.length,0);assert(remounted.state.recovery);
      assert(renderToStaticMarkup(remounted.renderRecovery()).includes('恢复信息仅暂存于本页，请勿刷新或关闭'),'React remount cannot turn a memory-only receipt into a safely persisted one');
      assert.equal(remounted.state.recovery.key,original.state.recovery.key);assert.equal(remounted.storage.size,0);
    ''')
