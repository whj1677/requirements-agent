import React, { useEffect, useRef, useState } from 'react';
import { api, ApiError, uploadFile, type UploadProgress } from './api';
import { useDialog } from './shell';
import './business-context.css';

type Obj = Record<string, any>;
const list = (value: unknown): Obj[] => Array.isArray(value) ? value : [];
const ids = (value: unknown): string[] => Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : [];
const same = (left: string[], right: string[]) => left.length === right.length && left.every(id => right.includes(id));
const originNames: Record<string, string> = { code_observation: '代码观察', document_claim: '文档陈述', inference: '推断' };
const dimensionNames: Record<string, string> = { object: '业务对象', actor: '参与者与权限', scenario: '场景', process: '业务流程', state: '状态', rule: '业务规则', dependency: '依赖与影响', data: '数据', exception: '异常与边界' };
const importKey = (root: string) => 'business-context-import:' + root;
const draftKey = (root: string, id: string) => 'business-context-draft:' + root + ':' + id;
function stored<T>(key: string): T | null { try { const raw = sessionStorage.getItem(key); return raw ? JSON.parse(raw) as T : null; } catch { return null; } }
function persist(key: string, value: unknown) { try { sessionStorage.setItem(key, JSON.stringify(value)); } catch { /* Session storage is optional. */ } }
function forget(key: string) { try { sessionStorage.removeItem(key); } catch { /* Session storage is optional. */ } }
function text(value: unknown): string { return typeof value === 'string' ? value : ''; }
function displayDate(value: unknown): string { const raw = text(value); if (!raw) return '未标明'; const date = new Date(raw); return Number.isNaN(date.valueOf()) ? raw : date.toLocaleString('zh-CN'); }

export function BusinessContextImport({ root, revision, refresh, busy }: { root: string; revision: number; refresh: () => Promise<void>; busy: boolean }) {
  const input = useRef<HTMLInputElement>(null), lock = useRef(false);
  const [file, setFile] = useState<File | null>(null);
  const [progress, setProgress] = useState<UploadProgress | null>(null);
  const [phase, setPhase] = useState<'idle' | 'upload' | 'reading' | 'done' | 'failed' | 'uncertain'>(() => stored<{ phase?: string }>(importKey(root))?.phase === 'uncertain' ? 'uncertain' : 'idle');
  const [message, setMessage] = useState(() => stored<{ message?: string }>(importKey(root))?.message || '');
  const [lastName, setLastName] = useState(() => stored<{ name?: string }>(importKey(root))?.name || '');
  const [refreshing, setRefreshing] = useState(false);
  const working = phase === 'upload' || phase === 'reading';
  useEffect(() => { if (phase === 'uncertain') persist(importKey(root), { phase, message, name: lastName }); else if (phase === 'done' || phase === 'idle') forget(importKey(root)); }, [root, phase, message, lastName]);
  async function upload() {
    if (!file || lock.current || busy || phase === 'uncertain') return;
    lock.current = true; setProgress(null); setMessage(''); setPhase('upload'); setLastName(file.name);
    const body = new FormData(); body.append('file', file); body.append('expected_revision', String(revision));
    try {
      const source = await uploadFile<Obj>(root + '/business-context', body, setProgress, () => setPhase('reading'));
      setPhase('done'); setMessage(`已导入“${source.business_context?.project?.name || source.title || file.name}”。请在下方选择本次相关模块。`);
      try { await refresh(); setFile(null); if (input.current) input.current.value = ''; } catch { setMessage('导入已返回成功，但资料列表刷新失败。所选文件仍保留；请点“刷新资料列表”核对结果。'); }
    } catch (error) {
      const rejected = error instanceof ApiError && error.status >= 400 && error.status < 500;
      setPhase(rejected ? 'failed' : 'uncertain');
      setMessage(rejected ? `未导入：${error.message}${error.status === 409 ? '。请刷新资料列表后重试。' : '。所选文件已保留。'}` : `${error instanceof Error ? error.message : '导入失败'}。请先刷新资料列表核对，避免重复导入。`);
    } finally { lock.current = false; }
  }
  async function check() {
    if (refreshing || working) return;
    setRefreshing(true);
    try {
      await refresh();
      if (phase === 'done') { setFile(null); if (input.current) input.current.value = ''; }
      setMessage('资料列表已刷新。请核对下方是否已有该包及其身份；确认未导入后可重新提交。');
    } catch { setMessage('资料列表刷新失败，所选文件仍保留在当前页面；请稍后重试刷新。'); }
    finally { setRefreshing(false); }
  }
  return <section className="business-import" aria-labelledby="business-import-title">
    <div className="business-import__heading"><div><p className="eyebrow">独立资料入口</p><h3 id="business-import-title">导入业务背景</h3></div><span className="pill">business-context.json</span></div>
    <p>导入有出处的产品与模块现状，供本次需求理解和提问。导入后默认不选择模块；选择模块不代表事实已核对，也不扩大模型读取授权。</p>
    <label htmlFor="business-context-file">业务背景包 JSON</label>
    <input id="business-context-file" ref={input} type="file" accept=".json,application/json" disabled={working || busy || refreshing} onChange={event => { const selected = event.currentTarget.files?.[0] || null; setFile(selected); if (selected) { setPhase('idle'); setMessage(''); } }} />
    {file && <small>待导入：{file.name}</small>}
    <div className="toolbar"><button className="primary" type="button" disabled={!file || working || busy || refreshing || phase === 'uncertain' || phase === 'done'} onClick={() => void upload()}>{working ? '正在导入…' : '导入业务背景包'}</button>{phase === 'uncertain' && <><button type="button" disabled={refreshing} onClick={() => void check()}>{refreshing ? '正在刷新…' : '刷新资料列表核对'}</button><button type="button" disabled={refreshing} onClick={() => { setPhase('idle'); setMessage('已完成核对；如列表中没有该包，可以重新提交所选文件。'); }}>已核对，可重新提交</button></>}{phase === 'done' && <button type="button" disabled={refreshing} onClick={() => void check()}>刷新资料列表</button>}</div>
    {phase === 'upload' && <div role="status" aria-live="polite"><progress max={100} value={progress?.total ? Math.floor(progress.loaded / progress.total * 100) : undefined} /><p>{progress?.total ? `上传 ${Math.floor(progress.loaded / progress.total * 100)}%` : '正在上传；暂时无法计算百分比。'}</p></div>}
    {phase === 'reading' && <p role="status">文件已上传，服务器正在校验和解析业务背景包，请勿重复提交。</p>}
    {phase === 'failed' && <button type="button" disabled={refreshing} onClick={() => void check()}>刷新资料列表</button>}
    {message && <p role={phase === 'uncertain' || phase === 'failed' ? 'alert' : 'status'} className={phase === 'uncertain' || phase === 'failed' ? 'error' : 'business-import__message'}>{message}</p>}
  </section>;
}

export function BusinessContextView({ source, root, revision, refresh, onClose, busy }: { source: Obj; root: string; revision: number; refresh: () => Promise<void>; onClose: () => void; busy: boolean }) {
  const bundle = source.business_context || {};
  const project = bundle.project || {};
  const modules = list(bundle.modules), claims = list(bundle.claims), evidence = list(bundle.evidence), unknowns = list(bundle.unknowns), conflicts = list(bundle.conflicts);
  const savedModules = ids(source.business_selection?.module_ids), savedClaims = ids(source.business_selection?.confirmed_claim_ids);
  const key = draftKey(root, source.id);
  const initial = stored<{ module_ids?: string[]; confirmed_claim_ids?: string[] }>(key);
  const [moduleIds, setModuleIds] = useState<string[]>(() => initial?.module_ids || savedModules);
  const [claimIds, setClaimIds] = useState<string[]>(() => initial?.confirmed_claim_ids || savedClaims);
  const [submitting, setSubmitting] = useState<'modules' | 'claims' | ''>(''), [message, setMessage] = useState(''), [error, setError] = useState('');
  const lock = useRef(false);
  const moduleDirty = !same(moduleIds, savedModules), claimDirty = !same(claimIds, savedClaims);
  function related(moduleSelection: string[]): Set<string> { const found = new Set<string>(), pending = [...moduleSelection]; while (pending.length) { const id = pending.pop()!; if (found.has(id)) continue; found.add(id); pending.push(...ids(modules.find(module => module.id === id)?.depends_on)); } return found; }
  const activeModules = related(savedModules);
  const eligibleClaims = new Set(claims.filter(claim => ids(claim.module_ids).some(id => activeModules.has(id))).map(claim => claim.id));
  const nextModulesRelated = related(moduleIds);
  const droppedClaims = savedClaims.filter(id => !claims.some(claim => claim.id === id && ids(claim.module_ids).some(moduleId => nextModulesRelated.has(moduleId))));
  useEffect(() => { if (moduleDirty || claimDirty) persist(key, { module_ids: moduleIds, confirmed_claim_ids: claimIds }); else forget(key); }, [key, moduleDirty, claimDirty, moduleIds, claimIds]);
  useEffect(() => { if (!moduleDirty) setModuleIds(savedModules); if (!claimDirty) setClaimIds(savedClaims); }, [source.business_selection]);
  useEffect(() => { const warn = (event: BeforeUnloadEvent) => { if (moduleDirty || claimDirty) { event.preventDefault(); event.returnValue = ''; } }; window.addEventListener('beforeunload', warn); return () => window.removeEventListener('beforeunload', warn); }, [moduleDirty, claimDirty]);
  function close() { if (lock.current) return; if ((moduleDirty || claimDirty) && !window.confirm('尚有未保存的模块或事实选择。草稿会保留在此浏览器；仍要关闭吗？')) return; onClose(); }
  useDialog(true, close);
  function toggle(id: string, values: string[], setter: React.Dispatch<React.SetStateAction<string[]>>) { setter(values.includes(id) ? values.filter(item => item !== id) : [...values, id]); setError(''); setMessage(''); }
  async function save(kind: 'modules' | 'claims') {
    if (lock.current || busy) return;
    if (kind === 'modules' && droppedClaims.length && !window.confirm(`改选模块后，${droppedClaims.length} 条已核对事实将不再关联本次范围，并取消其核对标记。仍要保存吗？`)) return;
    lock.current = true; setSubmitting(kind); setError(''); setMessage('');
    const nextModules = kind === 'modules' ? moduleIds : savedModules;
    const nextClaims = kind === 'claims' ? claimIds : savedClaims.filter(id => !droppedClaims.includes(id));
    try {
      await api(root + '/business-context/' + encodeURIComponent(source.id) + '/selection', 'POST', { expected_revision: revision, module_ids: nextModules, confirmed_claim_ids: nextClaims });
      if (kind === 'modules' && droppedClaims.length) setClaimIds(current => current.filter(id => !droppedClaims.includes(id)));
      setMessage(kind === 'modules' ? (droppedClaims.length ? '相关模块已保存；不再关联的事实核对标记已取消。' : '相关模块已保存；事实核对状态未改变。') : '已核对事实已保存；模块选择未改变。');
      try { await refresh(); } catch { setError('保存请求已返回成功，但页面刷新失败。请刷新项目核对最新状态，草稿仍保留。'); }
    } catch (caught) { setError(caught instanceof ApiError && caught.status === 409 ? `项目版本已变化：${caught.message}。请刷新项目核对后重新保存。` : (caught instanceof Error ? caught.message : '保存失败；编辑仍保留。')); }
    finally { lock.current = false; setSubmitting(''); }
  }
  const coverage = bundle.coverage || {};
  const groupNames = Array.from(new Set(claims.map(claim => text(claim.dimension) || 'other')));
  const active = !source.excluded && source.business_active !== false && savedModules.length > 0;
  return <div className="modal-backdrop preview-backdrop"><section className="modal business-reader" role="dialog" aria-modal="true" aria-label="业务背景包">
    <header className="business-reader__header"><div><p className="eyebrow">业务背景 · {active ? '已选择相关模块' : '未启用相关模块'}</p><h2>{text(project.name) || source.title}</h2><p>{text(bundle.overview) || '包中未提供产品概览。'}</p></div><button type="button" disabled={Boolean(submitting)} onClick={close} aria-label="关闭业务背景">关闭</button></header>
    {source.excluded && <p className="notice">此业务背景包已排除，当前不会用于分析。</p>}
    {!source.excluded && !active && savedModules.length > 0 && <p className="notice">此版本当前未启用，原模块选择与事实核对记录已保留。<button type="button" disabled={busy || Boolean(submitting) || moduleDirty || claimDirty} onClick={() => void save('modules')}>启用此版本</button></p>}
    <dl className="business-overview"><div><dt>产品标识</dt><dd>{text(project.id) || '未标明'}</dd></div><div><dt>生成时间</dt><dd>{displayDate(bundle.generated_at)}</dd></div><div><dt>包编号</dt><dd>{text(bundle.bundle_id) || '未标明'}</dd></div><div><dt>包版本</dt><dd>v{source.version || 1} · 格式 {text(bundle.schema_version) || '未标明'}</dd></div><div><dt>部署版本</dt><dd>{text(bundle.source_snapshot?.deployment) || '未标明'}</dd></div></dl>
    <details><summary>来源仓库版本</summary><ul>{list(bundle.source_snapshot?.repositories).map(repository => <li key={repository.id}>{repository.id} · {repository.revision} · {repository.dirty === null ? '工作树状态未知' : repository.dirty ? '含未提交修改' : '工作树无未提交修改'}</li>)}</ul></details>
    <details><summary>覆盖范围与限制</summary><div className="business-coverage"><p><b>深入查看：</b>{ids(coverage.inspected_modules).join('、') || '未标明'}</p><p><b>仅建索引：</b>{ids(coverage.indexed_modules).join('、') || '未标明'}</p><p><b>排除范围：</b>{Array.isArray(coverage.excluded) ? coverage.excluded.map(String).join('、') : text(coverage.excluded) || '未标明'}</p><p><b>限制：</b>{Array.isArray(coverage.limitations) ? coverage.limitations.map(String).join('；') : text(coverage.limitations) || '未标明'}</p></div></details>
    <section className="business-reader__section"><h3>选择本次相关模块</h3><p className="muted">此选择只决定哪些背景与本次需求相关，不表示事实已确认，也不自动授权向模型发送资料。同一产品切换到另一版本时，旧版本停止参与分析，原模块选择和核对记录保留。</p><div className="business-module-list">{modules.map(module => <label className="business-module" key={module.id}><input type="checkbox" checked={moduleIds.includes(module.id)} disabled={busy || Boolean(submitting) || source.excluded} onChange={() => toggle(module.id, moduleIds, setModuleIds)} /><span><b>{module.name || module.id}</b><small>{module.summary}</small>{ids(module.depends_on).length > 0 && <small>依赖：{ids(module.depends_on).map(id => modules.find(item => item.id === id)?.name || id).join('、')}。保存后会一并用于分析；模块选择不代表事实确认。</small>}</span></label>)}</div>{moduleDirty && <p className="notice">模块选择尚未保存；关闭或刷新页面前请保存。</p>}<button type="button" className="primary" disabled={!moduleDirty || busy || Boolean(submitting) || source.excluded} onClick={() => void save('modules')}>{submitting === 'modules' ? '正在保存…' : '保存相关模块'}</button></section>
    <section className="business-reader__section"><h3>业务事实与出处</h3><p className="muted">代码观察、文档陈述和推断均为现状线索。先保存相关模块，再逐项核对关联事实；核对不等于在本期需求中采纳。</p>{groupNames.map(group => <div className="business-claim-group" key={group}><h4>{dimensionNames[group] || group}</h4>{claims.filter(claim => (text(claim.dimension) || 'other') === group).map(claim => <article className="business-claim" key={claim.id}><label><input type="checkbox" checked={claimIds.includes(claim.id)} disabled={busy || Boolean(submitting) || source.excluded || !active || moduleDirty || !eligibleClaims.has(claim.id)} onChange={() => toggle(claim.id, claimIds, setClaimIds)} /><span>本次已核对</span></label><p>{claim.text}</p><small>{originNames[claim.origin] || claim.origin || '来源未标明'} · {ids(claim.module_ids).map(id => modules.find(item => item.id === id)?.name || id).join('、') || '未标明模块'}{!eligibleClaims.has(claim.id) ? ' · 先选择关联模块' : ''}</small><details><summary>查看出处</summary>{ids(claim.evidence_ids).length ? ids(claim.evidence_ids).map(id => { const item = evidence.find(entry => entry.id === id); return item ? <div className="business-evidence" key={id}><b>{item.repository_id || '仓库'} · {item.path || '路径未标明'}{item.symbol ? ` · ${item.symbol}` : ''}</b><small>行 {item.line_start || '?'}–{item.line_end || '?'} · SHA-256 {item.sha256 || '未标明'} · {item.kind || '出处'}</small>{item.snippet && <pre>{item.snippet}</pre>}</div> : <p key={id}>出处 {id} 未在包中找到</p>; }) : <p>此陈述未关联具体出处。</p>}</details></article>)}</div>)}{!claims.length && <p className="material-empty">此包没有可展示的业务事实。</p>}{claimDirty && <p className="notice">事实核对尚未保存；关闭或刷新页面前请保存。</p>}<button type="button" className="primary" disabled={!claimDirty || moduleDirty || !active || busy || Boolean(submitting) || source.excluded} onClick={() => void save('claims')}>{submitting === 'claims' ? '正在保存…' : '保存已核对事实'}</button></section>
    <details><summary>技术背景摘要</summary><p>{text(bundle.technical_summary) || '未提供'}</p></details>
    <div className="business-reader__columns"><section className="business-reader__section"><h3>仍待确认</h3>{unknowns.length ? unknowns.map(item => <p key={item.id} className="business-note">{item.question}<small>{ids(item.module_ids).map(id => modules.find(module => module.id === id)?.name || id).join('、')}</small></p>) : <p className="material-empty">包中未列出未知项。</p>}</section><section className="business-reader__section"><h3>资料冲突</h3>{conflicts.length ? conflicts.map(item => <p key={item.id} className="business-note">{item.description}<small>涉及事实：{ids(item.claim_ids).join('、')}</small></p>) : <p className="material-empty">包中未列出冲突。</p>}</section></div>
    {message && <p role="status" className="business-import__message">{message}</p>}{error && <p role="alert" className="error">{error}</p>}
    <footer className="business-reader__footer"><small>资料编号 {source.id} · 原件 SHA-256 {source.sha256 || '未取得'}</small><button type="button" disabled={Boolean(submitting)} onClick={close}>关闭</button></footer>
  </section></div>;
}
