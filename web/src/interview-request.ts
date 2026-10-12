import { ActionInput, ActionPlan, JsonObject, UserTask } from './api';

export function interviewSourceIds(project: {sources: JsonObject[]; active_source_ids?: string[]; domain_context?: {selected_source_ids?: string[] | null}}): string[] {
  if (Array.isArray(project.active_source_ids)) return [...project.active_source_ids];
  if (project.domain_context?.selected_source_ids != null) throw Error('本次资料范围尚未取得，请刷新后再发送。');
  return project.sources.filter(source => !source.excluded).map(source => source.id);
}

export type ChatReceipt = { input: ActionInput; plan_hash: string; idempotency_key: string; authorize: boolean };
type Request = <T>(path: string, method?: string, body?: unknown) => Promise<T>;
// The send button authorizes exactly the displayed receiver and source set.
// Keep the approved receipt when acknowledgement is lost, so retry is idempotent.
export async function sendInterview(request: Request, root: string, input: ActionInput,
  receiver: {origin: string; model: string}, sourceIds: string[], prepared: (r: ChatReceipt) => void,
  receipt?: ChatReceipt): Promise<UserTask> {
  if (!receipt) {
    const plan = await request<ActionPlan>(root+'/actions/plan','POST',input);
    if (plan.missing.length) throw Error(plan.missing.join('；'));
    if (plan.stages.length!==1 || plan.stages[0]!=='interview')
      throw Error('本次包含图片识别，请核对图片范围后通过“识别图片并发送”继续。');
    if (plan.recipients.length!==1 || plan.recipients[0].origin!==receiver.origin || plan.recipients[0].model!==receiver.model)
      throw Error('模型接收端已变化，请刷新页面核对后再发送。');
    if (plan.expected_revision!==input.expected_revision || JSON.stringify(plan.sources.map(s=>s.id).sort())!==JSON.stringify([...sourceIds].sort()))
      throw Error('资料范围已变化，请刷新核对后再发送。');
    receipt={input,plan_hash:plan.plan_hash,idempotency_key:crypto.randomUUID(),authorize:true};
    prepared(receipt);
  }
  return request<UserTask>(root+'/actions','POST',{...receipt.input,plan_hash:receipt.plan_hash,idempotency_key:receipt.idempotency_key,authorize:receipt.authorize});
}
