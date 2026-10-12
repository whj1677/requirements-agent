import type { ActionInput, ActionPlan, UserTask } from './api';

export type PreparationState = {state:'idle'|'checking'|'running'|'failed'|'authorization';message?:string;taskId?:string};
export type PreparationStatus = {
  revision:number; input_hash:string; status:'empty'|'running'|'ready'|'ready_with_limits'|'needs_organize'|'failed';
  needs_organize:boolean; auto_allowed:boolean; task_id?:string|null; reason:string;
  prior_task_id?:string|null; retry_requires_explicit:boolean; source_ids:string[]; entry_mode:string;
  draft_schema?:number; draft_coverage?:{requirement_count:number;missing_acceptance_ids:string[];missing_context_fields:string[];has_usable_draft:boolean;complete:boolean};
};
export type PreparationSubmission = {input:ActionInput;plan:ActionPlan;key:string;preparationToken:number;authorize:boolean};
type Intent = {token:number;taskId?:string;submission?:PreparationSubmission;uncertain?:boolean;resolving?:boolean};
export type PreparationDependencies = {
  active:()=>boolean; blocked:()=>string; status:()=>Promise<PreparationStatus>; tasks:()=>Promise<UserTask[]>;
  plan:(input:ActionInput)=>Promise<ActionPlan>; submit:(request:PreparationSubmission)=>Promise<UserTask>;
  refresh:()=>Promise<void>; record:(task:UserTask)=>void; navigate:(consumeIntent:()=>boolean)=>void;
  state:(state:PreparationState)=>void; authorization:(request:PreparationSubmission|null)=>void;
  error:(message:string,stale:boolean)=>void; key:()=>string;
  receipt:()=>PreparationSubmission|null; saveReceipt:(request:PreparationSubmission|null)=>void;
};
const running = (task:UserTask) => ['queued','running'].includes(task.status);
const standardOrganize = (task:UserTask) => task.action==='organize'&&!task.retry_input?.target&&!task.retry_input?.benchmark;
const uncertainError = (error:unknown) => {
  const e=error as {status?:number;kind?:string};
  return e.status===0||e.kind==='network'||e.kind==='empty'||e.kind==='text'||(e.status!==undefined&&e.status>=500);
};

/** A navigation intent is separate from a paid task: cancelling navigation never
 * retries or deletes the task. Lost POST responses retain their exact request key. */
export class RefinementPreparation {
  private intent:Intent|null=null;
  private sequence=0;
  private flight:Promise<void>|null=null;
  constructor(private getDependencies:()=>PreparationDependencies) {}
  private get deps() {return this.getDependencies();}
  private current(intent:Intent) {return this.intent===intent&&this.deps.active();}
  private update(intent:Intent,state:PreparationState) {if(this.current(intent))this.deps.state(state);}
  cancel() {
    this.intent=null;
    // Detach the cancelled navigation's wait. Its late response is still
    // guarded by current(); any sent request retains its exact receipt/key.
    this.flight=null;
    this.deps.authorization(null);
    this.deps.state({state:'idle'});
  }
  prepare(retry=false):Promise<void> {
    if(this.flight)return this.flight;
    if(!this.deps.active())return Promise.resolve();
    if(this.intent?.taskId||this.intent?.submission&&!this.intent.uncertain)return Promise.resolve();
    const previous=this.intent;
    const receipt=previous?.uncertain?previous.submission:this.deps.receipt();
    const intent:Intent={token:++this.sequence,
      ...(receipt?{submission:receipt,uncertain:true}:{})};
    this.intent=intent;
    this.update(intent,{state:'checking',message:'正在检查已有整表草稿，缺少时先由 AI 起草…'});
    const operation=this.prepareIntent(intent,retry).catch(error=>this.fail(intent,error));
    this.flight=operation;
    void operation.finally(()=>{if(this.flight===operation)this.flight=null;});
    return operation;
  }
  private async prepareIntent(intent:Intent,retry:boolean) {
    const blocked=this.deps.blocked();if(blocked)throw Error(blocked);
    const status=await this.deps.status();
    if(!this.current(intent))return;
    if(status.status==='ready'||status.status==='ready_with_limits'){this.clearReceipt(intent.submission);await this.enter(intent);return;}
    if(status.status==='empty')throw Error(status.reason);
    if(status.status==='running'){
      const task=(await this.deps.tasks()).find(t=>t.id===status.task_id);
      if(!this.current(intent))return;
      if(!task||!standardOrganize(task))throw Error('当前项目已有其他任务正在处理。请等待该任务结束，再进入完善需求。');
      this.clearReceipt(intent.submission);this.accept(intent,task);return;
    }
    // A previous failure may still be the latest visible task while the lost
    // POST is being accepted. Resolve its exact key before any fresh attempt.
    if(intent.uncertain&&intent.submission){
      intent.submission.preparationToken=intent.token;
      await this.submit(intent,intent.submission);return;
    }
    if(status.status==='failed'&&!retry)throw Error(status.reason||'上次整理未完成，请查看原因后点击“重试整理”。');
    intent.uncertain=false;
    const input:ActionInput={expected_revision:status.revision,action:'organize',message:'',document_type:'prd',option_id:null,max_calls:null};
    const plan=await this.deps.plan(input);
    if(!this.current(intent))return;
    const request:PreparationSubmission={input,plan,key:this.deps.key(),preparationToken:intent.token,authorize:false};
    intent.submission=request;
    if(plan.recipients.some(r=>r.needs_authorization)||plan.missing.length){
      this.update(intent,{state:'authorization',message:'请核对本次新增资料和接收端；确认后继续整理。'});
      this.deps.authorization(request);return;
    }
    if(!status.auto_allowed&&!retry)throw Error(status.reason);
    await this.submit(intent,request);
  }
  authorize(token:number):Promise<void> {
    if(this.flight)return this.flight;
    const intent=this.intent, request=intent?.submission;
    if(!intent||!request||intent.token!==token||!this.current(intent))return Promise.resolve();
    request.authorize=request.plan.recipients.some(r=>r.needs_authorization);
    const operation=this.submit(intent,request).catch(error=>this.fail(intent,error));
    this.flight=operation;
    void operation.finally(()=>{if(this.flight===operation)this.flight=null;});
    return operation;
  }
  async replan():Promise<void> {this.cancel();await this.prepare(true);}
  private clearReceipt(request?:PreparationSubmission) {
    if(request&&this.deps.receipt()?.key===request.key)this.deps.saveReceipt(null);
  }
  private async submit(intent:Intent,request:PreparationSubmission) {
    this.update(intent,{state:'checking',message:'正在提交整表草稿任务…'});
    const priorReceipt=this.deps.receipt()?.key===request.key;
    let task:UserTask;
    try {
      // Persist before sending. Reloading or cancelling navigation must not
      // turn a response lost in transit into a new paid action.
      this.deps.saveReceipt(request);
      task=await this.deps.submit(request);
    } catch(error) {
      const status=(error as {status?:number}).status;
      const uncertain=uncertainError(error)||(priorReceipt&&!(status!==undefined&&status>=400&&status<500));
      if(!uncertain)this.clearReceipt(request);
      if(this.current(intent))intent.uncertain=uncertain;
      throw error;
    }
    this.clearReceipt(request);
    if(!this.current(intent)){this.deps.record(task);return;}
    intent.uncertain=false;
    this.deps.authorization(null);
    this.accept(intent,task);
    await this.deps.refresh().catch(()=>this.update(intent,{state:'running',taskId:task.id,
      message:'整理任务已提交，暂未取得最新进度；会继续检查同一任务，请勿重复提交。'}));
  }
  private accept(intent:Intent,task:UserTask) {
    if(!standardOrganize(task))throw Error('返回的任务不是当前资料整理任务，请刷新核对。');
    intent.taskId=task.id;
    this.deps.record(task);
    this.update(intent,{state:'running',message:'正在起草项目摘要、需求说明和验收建议，完成后自动进入完善需求供你核对。',taskId:task.id});
    this.observe([task]);
  }
  observe(tasks:UserTask[]) {
    const intent=this.intent;
    if(!intent?.taskId||!this.current(intent)||intent.resolving)return;
    const task=tasks.find(t=>t.id===intent.taskId&&standardOrganize(t));
    if(!task||running(task))return;
    intent.resolving=true;
    void this.finish(intent,task).catch(error=>this.fail(intent,error));
  }
  private async finish(intent:Intent,task:UserTask) {
    if(!['succeeded','partial'].includes(task.status))throw Error(task.message||'本次整理未完成，已有内容保留。请查看任务记录后重试。');
    // The task and project may come from separate reads. Only the server's
    // current-input decision can authorize this navigation, not item counts.
    const status=await this.deps.status();
    if(!this.current(intent))return;
    if(status.status!=='ready'&&status.status!=='ready_with_limits')throw Error(status.reason||'信息已有变化，请再次整理后进入完善需求。');
    await this.enter(intent);
  }
  private async enter(intent:Intent) {
    await this.deps.refresh();
    if(!this.current(intent))return;
    this.deps.state({state:'idle'});
    this.deps.navigate(()=>{
      if(!this.current(intent))return false;
      this.intent=null;return true;
    });
  }
  private fail(intent:Intent,error:unknown) {
    if(!this.current(intent))return;
    intent.taskId=undefined;intent.resolving=false;
    if(!intent.uncertain)intent.submission=undefined;
    // Every retry stays actionable. A rejected modal submission moves to the
    // local failure/retry UI rather than leaving a now-stale submit button.
    this.deps.authorization(null);
    const e=error as {message?:string;status?:number;code?:string};
    const message=(e.message||'整理状态暂时无法读取，请重试检查。')+(intent.uncertain?' 本次提交结果尚未确认；重试会先核对状态并复用同一次提交，不会主动另建任务。':'');
    this.update(intent,{state:'failed',message});
    this.deps.error(message,e.status===409&&['STALE_REVISION','STALE_PLAN'].includes(e.code||''));
  }
}
