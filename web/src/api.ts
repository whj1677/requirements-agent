import { beginActivity } from './activity';
export type JsonObject = Record<string, any>;
export interface ProjectSummary { id: string; name: string; revision: number }
export interface Run { id: string; stage: string; status: string; message: string; calls: number; error?: string; cost?: number }
export type BusinessAction = 'organize' | 'explore' | 'clarify' | 'prototype' | 'document' | 'review' | 'change';
export interface ActionInput { expected_revision: number; action: BusinessAction; message: string; document_type: string; option_id?: string | null; max_calls?: number | null; target?: JsonObject }
export interface BudgetPolicy { mode: 'function_first'|'fixed'|'unlimited'; initial_output_tokens: number; maximum_output_tokens: number; truncation_escalations: number; action_seconds: number | null; input_measure: string; input_allowance: number | null }
export interface ActionPlan { budget_policies?: Record<string,BudgetPolicy>; plan_hash: string; label: string; stages: string[]; max_calls: number | null; expected_revision: number; pending_text: string; context_scope: string; missing: string[]; recipients: { origin: string; model: string; needs_authorization: boolean }[]; sources: { id: string; title: string; status: string }[]; generation_target?: JsonObject }
export interface UserTask { id: string; action: BusinessAction; label: string; status: string; message: string; error?: string; calls: number; max_calls: number | null; run_ids: string[]; source_ids: string[]; completed_steps: number; stages: string[]; cost: number | null }
export interface Project extends ProjectSummary {
  product_flow?: {step:number;title:string;content_hash:string;complete:boolean;available:boolean;missing:string[];needs_recheck:boolean}[];
  product_context?: JsonObject; product_context_proposal?: JsonObject; sketch_review?: JsonObject;
  requirement_relations?: JsonObject[];
  mode: string; reference_mode: string; sources: JsonObject[]; items: JsonObject[]; options: JsonObject[];
  questions: JsonObject[]; messages: JsonObject[]; documents: Record<string, JsonObject>;
  ui?: JsonObject; hashes: Record<string, string | null>; confirmation_issues: string[];
  document_export_readiness?: Record<string, { ready: boolean; issues: { code: string; message: string; question_id?: string }[] }>;
  baselines: JsonObject[]; exports: JsonObject[]; active_baseline_id?: string;
}
export interface BackendInfo { runtime_id: string; started_at: string; capabilities: string[]; distribution?: string }
export interface SessionInfo { csrf: string; mode: string; version: string; backend?: BackendInfo }
let csrf = '';
export function setCsrf(value: string) { csrf = value; }
export type ErrorKind = 'json' | 'text' | 'empty' | 'network';
export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string, public kind: ErrorKind = 'json', public path = '') { super(message); }
}
export function requestTimeout(path: string, method = 'GET') {
  if (/\/models\/[^/]+\/test$/.test(path)) return 75_000;
  if (/\/(sources|exports)(\/|$)/.test(path) && method !== 'GET') return 300_000;
  return method === 'GET' ? 15_000 : 30_000;
}
function requestLabel(path: string) {
  if (/\/models\/[^/]+\/test$/.test(path)) return '正在测试模型连接';
  if (path.endsWith('/actions/plan')) return '正在核对本次任务';
  if (path.endsWith('/actions')) return '正在提交任务';
  if (path.endsWith('/cancel')) return '正在停止后续步骤';
  if (path.includes('/sources')) return '正在处理项目资料';
  if (path.endsWith('/exports')) return '正在生成交接包';
  if (path.endsWith('/confirmations')) return '正在提交版本确认';
  if (path === '/projects') return '正在创建项目';
  return '正在提交当前操作';
}
export async function api<T = any>(path: string, method = 'GET', body?: unknown, signal?: AbortSignal): Promise<T> {
  const controller = new AbortController();
  let timedOut = false;
  const cancel = () => controller.abort();
  if (signal?.aborted) cancel(); else signal?.addEventListener('abort', cancel, { once: true });
  const timer = setTimeout(() => { timedOut = true; controller.abort(); }, requestTimeout(path, method));
  const finish = method === 'GET' ? undefined : beginActivity(requestLabel(path));
  const options: RequestInit = { method, credentials: 'same-origin', signal: controller.signal, headers: { 'X-CSRF-Token': csrf } };
  if (body instanceof FormData) options.body = body;
  else if (body !== undefined) { options.body = JSON.stringify(body); options.headers = { ...options.headers, 'Content-Type': 'application/json' }; }
  try {
  const response = await fetch('/api' + path, options);
  const text = await response.text();
  let data: any = null, kind: ErrorKind = 'empty';
  if (text) {
    try { data = JSON.parse(text); kind = 'json'; }
    catch { kind = 'text'; }
  }
  if (!response.ok) {
    if (kind === 'json' && data && (data.code || data.message)) throw new ApiError(response.status, data.code || 'HTTP_ERROR', data.message || '请求失败', 'json', path);
    if (response.status === 422) throw new ApiError(422, 'VALIDATION', '输入格式或必填项不符合要求，请检查当前表单。', 'json', path);
    if (kind === 'json') throw new ApiError(response.status, 'ROUTE_OR_OBJECT', `请求未成功（HTTP ${response.status}）`, 'json', path);
    if (kind === 'text') throw new ApiError(response.status, 'NON_JSON', `服务返回了无法识别的内容（HTTP ${response.status}）`, 'text', path);
    throw new ApiError(response.status, 'EMPTY_RESPONSE', `服务未返回内容（HTTP ${response.status}）`, 'empty', path);
  }
  if (kind !== 'json' || data === null) throw new ApiError(response.status, kind === 'text' ? 'NON_JSON' : 'EMPTY_RESPONSE', '未取得有效处理结果，请刷新核对状态；不要直接重复提交。', kind, path);
  finish?.(path.endsWith('/actions') ? '任务已提交，请查看本步运行进度。' : '请求已处理，请查看页面结果。');
  return data as T;
  } catch (e) {
    if (signal?.aborted && !timedOut) { finish?.(); throw e; }
    const unknown = method !== 'GET' ? '服务端是否已处理尚未确认，请先刷新核对，避免重复提交。' : '请确认服务和网络正常后重试。';
    const error = e instanceof ApiError ? e : new ApiError(0, timedOut ? 'REQUEST_TIMEOUT' : 'NETWORK', (timedOut ? '等待响应超时。' : '连接中断，未取得完整响应。') + unknown, 'network', path);
    finish?.(error.message, true);
    throw error;
  } finally { clearTimeout(timer); signal?.removeEventListener('abort', cancel); }
}

export interface UploadProgress { loaded: number; total?: number }
export function uploadFile<T = any>(path: string, body: FormData, onProgress: (progress: UploadProgress) => void, onUploaded: () => void = () => {}): Promise<T> {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open('POST', '/api' + path);
    request.withCredentials = true;
    request.timeout = 300_000;
    request.setRequestHeader('X-CSRF-Token', csrf);
    request.upload.addEventListener('progress', event => onProgress({ loaded: event.loaded, ...(event.lengthComputable && event.total > 0 ? { total: event.total } : {}) }));
    request.upload.addEventListener('load', onUploaded);
    request.addEventListener('load', () => {
      const text = request.responseText;
      let data: any = null, kind: ErrorKind = 'empty';
      if (text) { try { data = JSON.parse(text); kind = 'json'; } catch { kind = 'text'; } }
      if (request.status < 200 || request.status >= 300) {
        if (kind === 'json' && data && (data.code || data.message)) reject(new ApiError(request.status, data.code || 'HTTP_ERROR', data.message || '请求失败', 'json', path));
        else if (kind === 'json') reject(new ApiError(request.status, 'ROUTE_OR_OBJECT', `请求未成功（HTTP ${request.status}）`, 'json', path));
        else if (kind === 'text') reject(new ApiError(request.status, 'NON_JSON', `服务返回了无法识别的内容（HTTP ${request.status}）`, 'text', path));
        else reject(new ApiError(request.status, 'EMPTY_RESPONSE', `服务未返回内容（HTTP ${request.status}）`, 'empty', path));
        return;
      }
      if (kind === 'empty') { reject(new ApiError(request.status, 'EMPTY_RESPONSE', '服务未返回内容', 'empty', path)); return; }
      if (kind === 'text') { reject(new ApiError(request.status, 'NON_JSON', '服务返回了无法识别的内容', 'text', path)); return; }
      resolve(data as T);
    });
    request.addEventListener('error', () => reject(new ApiError(0, 'NETWORK', '上传连接中断；服务端是否已收到文件尚未确认', 'network', path)));
    request.addEventListener('timeout', () => reject(new ApiError(0, 'NETWORK', '上传或服务器读取等待超过 300 秒；服务端是否已收到并处理文件尚未确认', 'network', path)));
    request.addEventListener('abort', () => reject(new ApiError(0, 'NETWORK', '上传连接已中断；服务端是否已收到文件尚未确认', 'network', path)));
    request.send(body);
  });
}
export type ErrorCategory = 'not-found' | 'route-missing' | 'network' | 'non-json' | 'session' | 'forbidden' | 'conflict' | 'server' | 'unknown';
export function categorizeError(e: unknown): ErrorCategory {
  if (!(e instanceof ApiError)) return 'unknown';
  if (e.kind === 'network') return 'network';
  if (e.status === 404 && e.kind === 'json') return e.code === 'ROUTE_OR_OBJECT' ? 'route-missing' : 'not-found';
  if (e.kind === 'text' || e.kind === 'empty') return 'non-json';
  if (e.status === 401) return 'session';
  if (e.status === 403) return 'forbidden';
  if (e.status === 409) return 'conflict';
  if (e.status >= 500) return 'server';
  return 'unknown';
}

export async function downloadFile(url:string,fallback:string){
 const controller=new AbortController();let timedOut=false;
 const timer=setTimeout(()=>{timedOut=true;controller.abort();},300_000),finish=beginActivity('正在准备下载文件');
 try{
 const r=await fetch(url,{credentials:'same-origin',headers:{'X-CSRF-Token':csrf},signal:controller.signal});
 if(!r.ok){const data=await r.json().catch(()=>null);throw new ApiError(r.status,data?.code||'DOWNLOAD_FAILED',data?.message||`下载未完成（HTTP ${r.status}）`);}
 const type=r.headers.get('content-type')||'';
 if(type.includes('text/html')&&!fallback.endsWith('.html')||type.includes('application/json')&&!fallback.endsWith('.json'))throw new ApiError(r.status,'DOWNLOAD_INVALID','服务未返回预期文件，请刷新核对后重试。');
 const blob=await r.blob();if(!blob.size)throw new ApiError(r.status,'DOWNLOAD_EMPTY','下载文件为空，请核对生成状态后重试。');
 const href=URL.createObjectURL(blob),a=document.createElement('a');
 const disposition=r.headers.get('content-disposition')||'';
 const encoded=disposition.match(/filename\*=UTF-8''([^;]+)/i);
 a.href=href;try{a.download=encoded?decodeURIComponent(encoded[1]):fallback;}catch{a.download=fallback;}a.click();setTimeout(()=>URL.revokeObjectURL(href),1000);
 finish('文件已交给浏览器下载，请在下载列表中查看。');
 }catch(e){const error=e instanceof ApiError?e:new ApiError(0,'DOWNLOAD_FAILED',timedOut?'下载等待超时，请核对生成状态后重试。':'下载连接中断，请确认服务后重试。','network',url);finish(error.message,true);throw error;}finally{clearTimeout(timer);}
}

export const sourceStatusNames: JsonObject = { added: '已添加', reading: '读取中', read: '已读取', awaiting_vision: '待视觉分析', partial: '部分读取', failed: '读取失败', office_required: '需要本机 Office 处理', permission_denied: '权限受限' };
