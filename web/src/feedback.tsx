import React, { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { activityNotice, activitySnapshot, subscribeActivity } from './activity';
import { downloadFile } from './api';

export function DownloadButton({ href, filename, children }: { href: string; filename: string; children: React.ReactNode }) {
  const lock = useRef(false), [busy, setBusy] = useState(false), [error, setError] = useState('');
  return <span className="download-control"><button type="button" disabled={busy} onClick={async () => {
    if (lock.current) return; lock.current = true; setBusy(true); setError('');
    try { await downloadFile(href, filename); } catch (e) { setError((e as Error).message); }
    finally { lock.current = false; setBusy(false); }
  }}>{busy ? '正在准备下载…' : children}</button>{error && <span className="error" role="alert">{error}</span>}</span>;
}

// Mounted outside drawers and scroll panes so a response cannot hide behind them.
export function RequestFeedback() {
  const state = useSyncExternalStore(subscribeActivity, activitySnapshot);
  const [, tick] = useState(0);
  const pending = state.active.at(-1);
  useEffect(() => {
    if (!pending) return;
    const timer = setInterval(() => tick(n => n + 1), 1000);
    return () => clearInterval(timer);
  }, [pending?.id]);
  useEffect(() => {
    if (pending || !state.message || state.error) return;
    const timer = setTimeout(() => activityNotice(''), 8000);
    return () => clearTimeout(timer);
  }, [pending?.id, state.message, state.error]);
  if (!pending && !state.message) return null;
  const seconds = pending ? Math.floor((Date.now() - pending.started) / 1000) : 0;
  return <aside className={'request-feedback' + (state.error ? ' request-feedback-error' : '')} aria-label="操作反馈">
    {pending && <div role="status" aria-live="polite"><span className="feedback-spinner" aria-hidden="true" />{pending.label}…<small>已等待 {seconds} 秒{seconds >= 10 ? '，请勿重复提交；如连接中断会显示处理建议。' : ''}</small></div>}
    {state.message && <div role={state.error ? 'alert' : 'status'}><p>{state.message}</p><button type="button" onClick={() => activityNotice('')}>关闭提示</button></div>}
  </aside>;
}
