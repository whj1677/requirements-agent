import React, { useEffect, useRef, useState } from 'react';
import { api } from './api';
import { useDirty, useNavigation } from './editing';

type Obj = Record<string, any>;
const CONFIG_FIELDS = ['name', 'base_url', 'model', 'key_env', 'vision', 'json_mode', 'timeout', 'max_tokens', 'review_max_tokens', 'review_retry_max_tokens', 'context_chars', 'action_seconds', 'thinking_disabled', 'review_thinking', 'documented_at', 'local_allowed', 'input_price', 'output_price'] as const;
const editable = (value: Obj) => {
  const current: Obj = {};
  for (const field of CONFIG_FIELDS) if (value && Object.hasOwn(value, field)) current[field] = value[field];
  if (current.vision === 'verified') current.vision = 'documented';
  return current;
};
const keySources: Obj = { '.env': '本机 .env', process_env: '进程环境变量', session: '当前服务进程', protected_store: '此电脑的受保护存储', protected_store_unavailable: '受保护密钥暂不可用', none: '未配置' };
function endpoint(value: string): string {
  try { const url = new URL(value); return url.protocol + '//' + url.host.toLowerCase(); }
  catch { return ''; }
}
function independentKeyEnv(slot: string, origin: string): string {
  let hash = 2166136261;
  for (const char of origin) hash = Math.imul(hash ^ char.charCodeAt(0), 16777619);
  return `RA_CUSTOM_${slot.toUpperCase()}_${(hash >>> 0).toString(16).toUpperCase().padStart(8, '0')}`;
}

export function ModelSettings({ models, busy, act, refresh }: { models: Obj; busy: boolean; act: (fn: () => Promise<unknown>) => void; refresh: () => Promise<void> }) {
  const guard = useNavigation();
  const [slot, setSlot] = useState('model');
  const [form, setForm] = useState<Obj>({});
  const [key, setKey] = useState('');
  const [keyTarget, setKeyTarget] = useState({ slot: '', baseUrl: '' });
  const [result, setResult] = useState('');
  const baseline = useRef<Obj>({});
  const loadedSlot = useRef('');
  const dirty = JSON.stringify(form) !== JSON.stringify(baseline.current);
  const status = models[slot];
  const keyTargetChanged = Boolean(key) && (keyTarget.slot !== slot || keyTarget.baseUrl !== (form.base_url || ''));
  const canPersist = status?.persistent_key_supported !== false;
  const originChanged = Boolean(endpoint(form.base_url || '') && endpoint(baseline.current.base_url || '') && endpoint(form.base_url || '') !== endpoint(baseline.current.base_url || ''));

  useEffect(() => {
    const changedSlot = loadedSlot.current !== slot;
    if (changedSlot || !dirty) {
      const current = editable(models[slot]);
      baseline.current = current;
      loadedSlot.current = slot;
      setForm(current);
    }
    if (changedSlot) { setKey(''); setKeyTarget({ slot: '', baseUrl: '' }); setResult(''); }
  }, [models, slot]);

  const update = (field: string, value: unknown) => {
    setResult('');
    const next = { ...form, [field]: value };
    if (field === 'base_url' && typeof value === 'string') {
      const nextOrigin = endpoint(value);
      const savedOrigin = endpoint(baseline.current.base_url || '');
      const managedName = /^RA_CUSTOM_(MODEL|VISION)_[0-9A-F]{8}$/;
      if (nextOrigin && savedOrigin && (form.key_env === baseline.current.key_env || managedName.test(form.key_env || ''))) {
        next.key_env = nextOrigin === savedOrigin ? baseline.current.key_env : independentKeyEnv(slot, nextOrigin);
      }
    }
    setForm(next);
  };
  async function saveConfig() {
    const payload = { ...form, budget_mode: 'unlimited', max_calls: null };
    await api('/models/' + slot, 'PUT', payload);
    baseline.current = { ...payload };
    setForm({ ...payload });
    await refresh();
  }
  async function saveKey(persist: boolean) {
    if (!key.trim()) throw new Error('请先输入当前接收端的 API Key。');
    if (keyTargetChanged) throw new Error('接收端地址在输入 Key 后发生变化。请重新输入 Key，确认它属于当前接收端。');
    const response = await api<Obj>('/models/' + slot + '/key', 'PUT', { key, persist });
    if (persist && response.key_source !== 'protected_store') {
      throw new Error('服务未确认密钥已保存到此电脑，请核对当前来源；输入仍保留。');
    }
    setKey('');
    try { await refresh(); }
    catch { setResult('密钥写入已确认，但页面状态刷新失败；请重试加载列表与配置以核对来源。'); return; }
    setResult(persist ? '密钥已安全保存到此电脑；项目材料发送仍需单独授权。' : '密钥仅在当前服务进程中使用，退出后需要重新输入。');
  }
  async function saveBasic() {
    setResult('');
    if (keyTargetChanged) throw new Error('接收端地址在输入 Key 后发生变化。请重新输入 Key，确认它属于当前接收端。');
    await saveConfig();
    if (key.trim()) {
      try { await saveKey(canPersist); }
      catch (error) {
        setResult(canPersist ? '接收端和模型已保存，但密钥未能安全保存；输入已保留。可检查错误后重试，或选择临时使用。' : '接收端和模型已保存，但临时 Key 未能启用；输入已保留，请核对错误后重试。');
        throw error;
      }
    } else setResult('接收端和模型已保存。已有密钥的来源见下方；项目材料发送仍需单独授权。');
  }
  useDirty('model-settings', dirty, '模型配置', saveConfig, () => setForm({ ...baseline.current }));
  useDirty('model-key', Boolean(key), '尚未保存的 API Key', () => saveKey(canPersist), () => setKey(''));

  return <div className="workspace settings onboarding-settings">
    <h1>模型设置</h1>
    <p className="muted">填写接收端和模型；如果需要密钥，在这里输入并安全保存到此电脑。连接测试由你手动触发，可能计费，不发送项目材料。</p>
    <div className="settings-card">
      <h2>基础配置</h2>
      <label>配置用途<select value={slot} onChange={e => guard(() => setSlot(e.target.value))}><option value="model">主分析模型</option><option value="vision">图片视觉模型</option></select></label>
      <div className="settings-grid">
        <label>接收端地址（Base URL）<input type="url" value={form.base_url || ''} onChange={e => update('base_url', e.target.value)} placeholder="https://example.com/v1" /></label>
        <label>模型名称<input value={form.model || ''} onChange={e => update('model', e.target.value)} /></label>
      </div>
      {originChanged && <p className="muted settings-help">接收端已改变，已使用独立密钥变量名 {form.key_env}。旧接收端的 Key 不会复制到新接收端；请为新地址输入 Key。</p>}
      <p className="settings-status">当前状态：{status?.key_configured ? '已配置密钥' : '尚未配置密钥'} · 来源：{keySources[status?.key_source] || '未配置'}{status?.bound_origin ? ' · 接收端：' + status.bound_origin : ''}</p>
      {status?.key_storage_error && <p className="error" role="alert">受保护密钥不可用：{String(status.key_storage_error)}</p>}
      <label>当前接收端 API Key<input type="password" value={key} onChange={e => { setResult(''); setKey(e.target.value); setKeyTarget({ slot, baseUrl: form.base_url || '' }); }} autoComplete="new-password" placeholder="已有密钥可留空；不会回显或保存在浏览器" /></label>
      {keyTargetChanged && <p className="error" role="alert">接收端地址已变化。请重新输入 Key，确认它属于当前接收端。</p>}
      <p className="muted settings-help">{canPersist ? '默认将新密钥安全保存到此电脑。' : '当前环境不支持受保护存储，新密钥只能临时使用。'}临时使用只在当前服务进程有效；失败时会保留输入。</p>
      <div className="toolbar">
        <button className="primary" disabled={busy || !form.base_url || !form.model || keyTargetChanged} onClick={() => act(saveBasic)}>{key.trim() ? canPersist ? '保存配置与密钥到此电脑' : '保存配置并临时使用 Key' : '保存接收端与模型'}</button>
        <button disabled={busy || !key.trim() || dirty || keyTargetChanged} onClick={() => act(() => saveKey(false))}>仅临时使用此 Key</button>
        <button disabled={busy || dirty || !status?.key_configured} onClick={() => act(async () => { setResult(''); const r = await api<Obj>('/models/' + slot + '/test', 'POST'); await refresh(); setResult('接口连接成功；请求模型 ' + r.meta.request_model + '，返回模型 ' + r.meta.response_model + '。视觉探针：' + r.vision + '；未验证需求分析语义。'); })}>{slot === 'vision' ? '测试视觉能力（合成图片）' : '测试连接（调用模型）'}</button>
      </div>
      {dirty && <p className="muted">接收端或模型已修改。先保存配置，再测试连接或临时使用密钥。</p>}
      {result && <p role="status" className="notice">{result}</p>}
    </div>
    <details className="settings-advanced"><summary>高级参数与密钥来源</summary>
      <div className="settings-grid">
        <label>服务方名称<input value={form.name || ''} onChange={e => update('name', e.target.value)} /></label>
        <label>项目环境变量名<input value={form.key_env || ''} onChange={e => update('key_env', e.target.value)} /></label>
      </div>
      <label>视觉能力<select value={form.vision || 'unknown'} onChange={e => update('vision', e.target.value)}><option value="unknown">未知</option><option value="documented">官方文档支持，未实测</option><option value="unsupported">不支持</option></select></label>
      <p className="notice">不设累计调用或费用预算；保留单次容量、超时和有限错误重试。</p>
      <div className="settings-grid">{[['max_tokens', '单次输出上限（按模型能力）'], ['timeout', '单请求超时（秒）']].map(([field, label]) => <label key={field}>{label}<input type="number" min="1" max={field === 'max_tokens' ? 393216 : undefined} value={form[field] || ''} onChange={e => update(field, Number(e.target.value))} /></label>)}</div>
      <label className="check"><input type="checkbox" checked={form.json_mode || false} onChange={e => update('json_mode', e.target.checked)} />使用 JSON Output 模式</label>
      <label className="check"><input type="checkbox" checked={form.thinking_disabled || false} onChange={e => update('thinking_disabled', e.target.checked)} />DeepSeek 其它结构化阶段关闭思考模式（整理、成文等）</label>
      {slot === 'model' && <><label className="check"><input type="checkbox" checked={form.review_thinking ?? true} onChange={e => update('review_thinking', e.target.checked)} />DeepSeek 语义审查使用思考模式（建议）</label><small className="muted">思考过程与最终回答都会占用输出额度；此项仅影响 DeepSeek 语义审查。</small></>}
      <label className="check"><input type="checkbox" checked={form.local_allowed || false} onChange={e => update('local_allowed', e.target.checked)} />明确允许此本地 HTTP 模型地址</label>
      <div className="toolbar"><button disabled={busy || !dirty} onClick={() => act(async () => { setResult(''); await saveConfig(); setResult('高级参数已保存。'); })}>保存高级参数</button></div>
      <p className="muted">密钥也可能来自本机 .env 或进程环境变量；已有来源不会在网页回显。更改接收端后请重新核对密钥来源。</p>
      <button disabled={busy} onClick={() => act(async () => { setResult(''); await api('/models/' + slot + '/key', 'PUT', { key: '', persist: false }); setKey(''); await refresh(); setResult('临时 Key 已清除；受保护存储或环境变量中的密钥仍可能生效，请核对当前来源。'); })}>清除临时 Key</button>
      <button disabled={busy} onClick={() => act(async () => { setResult(''); await api('/models/' + slot + '/key', 'PUT', { key: '', persist: true }); setKey(''); await refresh(); setResult('此电脑保存的 Key 已移除；环境变量中的密钥仍可能生效，请核对当前来源。'); })}>移除此电脑保存的 Key</button>
    </details>
  </div>;
}
