export type JsonObject = Record<string, any>;
export interface ProjectSummary { id: string; name: string; revision: number }
export interface Run { id: string; stage: string; status: string; message: string; calls: number; error?: string; cost?: number }
export interface Project extends ProjectSummary {
  mode: string; reference_mode: string; sources: JsonObject[]; items: JsonObject[]; options: JsonObject[];
  questions: JsonObject[]; messages: JsonObject[]; documents: Record<string, JsonObject>;
  ui?: JsonObject; hashes: Record<string, string | null>; confirmation_issues: string[];
  baselines: JsonObject[]; exports: JsonObject[]; active_baseline_id?: string;
}
let csrf = '';
export function setCsrf(value: string) { csrf = value; }
export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string) { super(message); }
}
export async function api<T = any>(path: string, method = 'GET', body?: unknown, signal?: AbortSignal): Promise<T> {
  const options: RequestInit = { method, credentials: 'same-origin', signal, headers: { 'X-CSRF-Token': csrf } };
  if (body instanceof FormData) options.body = body;
  else if (body !== undefined) { options.body = JSON.stringify(body); options.headers = { ...options.headers, 'Content-Type': 'application/json' }; }
  const response = await fetch('/api' + path, options);
  const data = await response.json();
  if (!response.ok) throw new ApiError(response.status, data.code || 'HTTP_ERROR', data.message || JSON.stringify(data.detail));
  return data as T;
}
