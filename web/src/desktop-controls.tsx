import React, { useState } from 'react';

async function desktopRequest(path: string, csrf?: string) {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), 10000);
  try {
    const response = await fetch('/desktop/api/' + path, {
      method: csrf ? 'POST' : 'GET',
      headers: csrf ? { 'X-Desktop-CSRF': csrf } : {},
      credentials: 'same-origin', signal: controller.signal,
    });
    const value = await response.json();
    if (!response.ok) throw new Error(value.message || '无法退出工作台，请稍后重试。');
    return value;
  } catch (error) {
    if ((error as Error).name === 'AbortError') throw new Error('等待退出确认超时。请确认工作台状态后重试。');
    throw error;
  } finally { window.clearTimeout(timer); }
}

export function DesktopExit({ onClose, onStopped }: { onClose: () => void; onStopped: () => void }) {
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  async function stop() {
    if (busy) return;
    setBusy(true); setError('');
    try {
      const status = await desktopRequest('status');
      if (!status.csrf) throw new Error('无法核对本机服务状态，请稍后重试。');
      const result = await desktopRequest('shutdown', status.csrf);
      if (result.status !== 'STOPPING') throw new Error('服务尚未确认退出，请稍后重试。');
      onStopped();
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  return <div className="modal-backdrop"><div className="modal" role="dialog" aria-modal="true" aria-labelledby="desktop-exit-title">
    <h2 id="desktop-exit-title">退出工作台</h2>
    <p>确认后将停止本机服务。已保存的项目和模型配置会保留；再次使用时，从“需求 Agent”快捷方式打开。</p>
    <p className="muted">若仍有材料读取或模型任务，程序会提示等待任务结束。</p>
    {error && <p className="error" role="alert">{error}</p>}
    <div className="toolbar"><button autoFocus disabled={busy} onClick={onClose}>继续使用</button><button className="primary" disabled={busy} onClick={() => void stop()}>{busy ? '正在退出…' : '确认退出'}</button></div>
  </div></div>;
}
