export type JsonObject = Record<string, any>;
export interface ProjectSummary { id: string; name: string; revision: number }
export interface Run { id: string; stage: string; status: string; message: string; calls: number; error?: string; cost?: number }
export type BusinessAction = 'organize' | 'explore' | 'clarify' | 'prototype' | 'document' | 'review' | 'change';
export interface ActionInput { expected_revision: number; action: BusinessAction; message: string; document_type: string; option_id?: string | null; max_calls: number; target?: JsonObject }
export interface ActionPlan { plan_hash: string; label: string; stages: string[]; max_calls: number; expected_revision: number; pending_text: string; context_scope: string; missing: string[]; recipients: { origin: string; model: string; needs_authorization: boolean }[]; sources: { id: string; title: string; status: string }[]; generation_target?: JsonObject }
export interface UserTask { id: string; action: BusinessAction; label: string; status: string; message: string; calls: number; max_calls: number; run_ids: string[]; source_ids: string[]; completed_steps: number; stages: string[]; cost: number | null }
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
export interface BackendInfo { runtime_id: string; started_at: string; capabilities: string[] }
export interface SessionInfo { csrf: string; mode: string; version: string; backend?: BackendInfo }
let csrf = '';
export function setCsrf(value: string) { csrf = value; }
export type ErrorKind = 'json' | 'text' | 'empty' | 'network';
export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string, public kind: ErrorKind = 'json', public path = '') { super(message); }
}
export async function api<T = any>(path: string, method = 'GET', body?: unknown, signal?: AbortSignal): Promise<T> {
  const options: RequestInit = { method, credentials: 'same-origin', signal, headers: { 'X-CSRF-Token': csrf } };
  if (body instanceof FormData) options.body = body;
  else if (body !== undefined) { options.body = JSON.stringify(body); options.headers = { ...options.headers, 'Content-Type': 'application/json' }; }
  let response: Response;
  try {
    response = await fetch('/api' + path, options);
  } catch (e) {
    if (e instanceof DOMException && e.name === 'AbortError') throw e;
    throw new ApiError(0, 'NETWORK', '无法连接服务，请确认服务已启动', 'network', path);
  }
  const text = await response.text();
  let data: any = null, kind: ErrorKind = 'empty';
  if (text) {
    try { data = JSON.parse(text); kind = 'json'; }
    catch { kind = 'text'; }
  }
  if (!response.ok) {
    if (kind === 'json' && data && (data.code || data.message)) throw new ApiError(response.status, data.code || 'HTTP_ERROR', data.message || '请求失败', 'json', path);
    if (kind === 'json') throw new ApiError(response.status, 'ROUTE_OR_OBJECT', `请求未成功（HTTP ${response.status}）`, 'json', path);
    if (kind === 'text') throw new ApiError(response.status, 'NON_JSON', `服务返回了无法识别的内容（HTTP ${response.status}）`, 'text', path);
    throw new ApiError(response.status, 'EMPTY_RESPONSE', `服务未返回内容（HTTP ${response.status}）`, 'empty', path);
  }
  return data as T;
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
 const r=await fetch(url,{credentials:'same-origin',headers:{'X-CSRF-Token':csrf}});
 if(!r.ok){const data=await r.json().catch(()=>null);throw new ApiError(r.status,data?.code||'DOWNLOAD_FAILED',data?.message||`下载未完成（HTTP ${r.status}）`);}
 const blob=await r.blob(),href=URL.createObjectURL(blob),a=document.createElement('a');
 const disposition=r.headers.get('content-disposition')||'';
 const encoded=disposition.match(/filename\*=UTF-8''([^;]+)/i);
 a.href=href;a.download=encoded?decodeURIComponent(encoded[1]):fallback;a.click();setTimeout(()=>URL.revokeObjectURL(href),1000);
}
