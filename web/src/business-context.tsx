import React, { useEffect, useRef, useState } from 'react';
import { api, ApiError, uploadFile, type UploadProgress } from './api';
import { useDialog } from './shell';
import './business-context.css';

type Obj = Record<string, any>;
const list = (value: unknown): Obj[] => Array.isArray(value) ? value : [];
const ids = (value: unknown): string[] => Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : [];
const same = (left: string[], right: string[]) => left.length === right.length && left.every(id => right.includes(id));
const originNames: Record<string, string> = { code_observation: '代码或配置观察', document_claim: '文档陈述', inference: '推断' };
const dimensionNames: Record<string, string> = {
  architecture: '架构', data_flow: '数据流', data_model: '数据模型', interface: '接口',
  dependency: '依赖与影响', constraint: '实现约束', deployment: '部署', business: '业务',
  object: '业务对象', actor: '参与者与权限', scenario: '场景', process: '业务流程',
  state: '状态', rule: '业务规则', data: '数据', exception: '异常与边界',
};
const dimensionOrder = ['architecture', 'data_flow', 'data_model', 'interface', 'dependency', 'constraint', 'deployment', 'business'];
const importKey = (root: string) => 'business-context-import:' + root;
const draftKey = (root: string, id: string) => 'business-context-draft:' + root + ':' + id;
function stored<T>(key: string): T | null { try { const raw = sessionStorage.getItem(key); return raw ? JSON.parse(raw) as T : null; } catch { return null; } }
function persist(key: string, value: unknown) { try { sessionStorage.setItem(key, JSON.stringify(value)); } catch { /* Optional. */ } }
function forget(key: string) { try { sessionStorage.removeItem(key); } catch { /* Optional. */ } }
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
      setPhase('done');
      setMessage(source.business_active === false
        ? `“${source.business_context?.project?.name || source.title || file.name}”已在资料库中，但此版本尚未启用。可在下方打开并启用。`
        : `已导入“${source.business_context?.project?.name || source.title || file.name}”，默认使用整包工程上下文；可在下方调整参考范围。`);
      try { await refresh(); setFile(null); if (input.current) input.current.value = ''; }
      catch { setMessage('导入已返回成功，但资料列表刷新失败。所选文件仍保留；请点“刷新资料列表”核对结果。'); }
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
    <div className="business-import__heading"><div><p className="eyebrow">独立资料入口</p><h3 id="business-import-title">导入工程上下文</h3></div><span className="pill">business-context.json</span></div>
    <p>导入有出处的业务与工程现状，包含架构、数据流、数据模型、接口和约束。新包默认使用全部模块；可按需缩小范围。</p>
    <label htmlFor="business-context-file">工程上下文 JSON</label>
    <input id="business-context-file" ref={input} type="file" accept=".json,application/json" disabled={working || busy || refreshing} onChange={event => { const selected = event.currentTarget.files?.[0] || null; setFile(selected); if (selected) { setPhase('idle'); setMessage(''); } }} />
    {file && <small>待导入：{file.name}</small>}
    <div className="toolbar"><button className="primary" type="button" disabled={!file || working || busy || refreshing || phase === 'uncertain' || phase === 'done'} onClick={() => void upload()}>{working ? '正在导入…' : '导入工程上下文'}</button>{phase === 'uncertain' && <><button type="button" disabled={refreshing} onClick={() => void check()}>{refreshing ? '正在刷新…' : '刷新资料列表核对'}</button><button type="button" disabled={refreshing} onClick={() => { setPhase('idle'); setMessage('已完成核对；如列表中没有该包，可以重新提交所选文件。'); }}>已核对，可重新提交</button></>}{phase === 'done' && <button type="button" disabled={refreshing} onClick={() => void check()}>刷新资料列表</button>}</div>
    {phase === 'upload' && <div role="status" aria-live="polite"><progress max={100} value={progress?.total ? Math.floor(progress.loaded / progress.total * 100) : undefined} /><p>{progress?.total ? `上传 ${Math.floor(progress.loaded / progress.total * 100)}%` : '正在上传；暂时无法计算百分比。'}</p></div>}
    {phase === 'reading' && <p role="status">文件已上传，服务器正在校验和解析工程上下文，请勿重复提交。</p>}
    {phase === 'failed' && <button type="button" disabled={refreshing} onClick={() => void check()}>刷新资料列表</button>}
    {message && <p role={phase === 'uncertain' || phase === 'failed' ? 'alert' : 'status'} className={phase === 'uncertain' || phase === 'failed' ? 'error' : 'business-import__message'}>{message}</p>}
  </section>;
}

export function BusinessContextView({ source, root, revision, refresh, onClose, busy }: { source: Obj; root: string; revision: number; refresh: () => Promise<void>; onClose: () => void; busy: boolean }) {
  const bundle = source.business_context || {}, project = bundle.project || {};
  const modules = list(bundle.modules), claims = list(bundle.claims), evidence = list(bundle.evidence), unknowns = list(bundle.unknowns), conflicts = list(bundle.conflicts);
  const savedModules = ids(source.business_selection?.module_ids), savedClaims = ids(source.business_selection?.confirmed_claim_ids);
  const key = draftKey(root, source.id);
  const initial = stored<{ module_ids?: string[] }>(key);
  const [moduleIds, setModuleIds] = useState<string[]>(() => initial?.module_ids || savedModules);
  const [submitting, setSubmitting] = useState(false), [message, setMessage] = useState(''), [error, setError] = useState('');
  const lock = useRef(false), moduleDirty = !same(moduleIds, savedModules);
  function related(selection: string[]): Set<string> {
    const found = new Set<string>(), pending = [...selection];
    while (pending.length) { const id = pending.pop()!; if (found.has(id)) continue; found.add(id); pending.push(...ids(modules.find(module => module.id === id)?.depends_on)); }
    return found;
  }
  const activeModules = related(savedModules), nextModulesRelated = related(moduleIds);
  const retainedClaims = savedClaims.filter(id => claims.some(claim => claim.id === id && ids(claim.module_ids).some(moduleId => nextModulesRelated.has(moduleId))));
  const conflictClaims = new Set(conflicts.flatMap(item => ids(item.claim_ids)));
  const coverage = bundle.coverage || {};
  const groups = Array.from(new Set(claims.map(claim => text(claim.dimension) || 'other')))
    .sort((a, b) => { const ai = dimensionOrder.indexOf(a), bi = dimensionOrder.indexOf(b); return (ai < 0 ? 100 : ai) - (bi < 0 ? 100 : bi) || a.localeCompare(b); });
  const active = !source.excluded && source.business_active !== false && savedModules.length > 0;
  useEffect(() => { if (moduleDirty) persist(key, { module_ids: moduleIds }); else forget(key); }, [key, moduleDirty, moduleIds]);
  useEffect(() => { if (!moduleDirty) setModuleIds(savedModules); }, [source.business_selection]);
  useEffect(() => { const warn = (event: BeforeUnloadEvent) => { if (moduleDirty) { event.preventDefault(); event.returnValue = ''; } }; window.addEventListener('beforeunload', warn); return () => window.removeEventListener('beforeunload', warn); }, [moduleDirty]);
  function close() { if (lock.current) return; if (moduleDirty && !window.confirm('参考范围尚未保存。草稿会保留在此浏览器；仍要关闭吗？')) return; onClose(); }
  useDialog(true, close);
  async function save() {
    if (lock.current || busy || source.excluded || !moduleIds.length) return;
    lock.current = true; setSubmitting(true); setError(''); setMessage('');
    try {
      await api(root + '/business-context/' + encodeURIComponent(source.id) + '/selection', 'POST', {
        expected_revision: revision, module_ids: moduleIds, confirmed_claim_ids: retainedClaims,
      });
      setMessage('参考范围已保存；当前版本已启用。');
      try { await refresh(); } catch { setError('保存请求已返回成功，但页面刷新失败。请刷新项目核对最新状态，草稿仍保留。'); }
    } catch (caught) { setError(caught instanceof ApiError && caught.status === 409 ? `项目版本已变化：${caught.message}。请刷新项目核对后重新保存。` : (caught instanceof Error ? caught.message : '保存失败；编辑仍保留。')); }
    finally { lock.current = false; setSubmitting(false); }
  }
  return <div className="modal-backdrop preview-backdrop"><section className="modal business-reader" role="dialog" aria-modal="true" aria-label="工程上下文">
    <header className="business-reader__header"><div><p className="eyebrow">工程上下文 · {active ? `已启用 ${savedModules.length} 个模块` : '此版本未启用'}</p><h2>{text(project.name) || source.title}</h2></div><button type="button" disabled={submitting} onClick={close} aria-label="关闭工程上下文">关闭</button></header>
    {source.excluded && <p className="notice">此工程上下文包已排除，当前不会用于分析。</p>}
    {!source.excluded && !active && <p className="notice">此版本当前未启用。{savedModules.length ? '原模块选择已保留。' : '请先选择至少一个模块。'}{savedModules.length > 0 && <button type="button" disabled={busy || submitting || moduleDirty} onClick={() => void save()}>启用此版本</button>}</p>}
    <section className="business-reader__intro"><h3>工程总览</h3><p>{text(bundle.overview) || '包中未提供产品概览。'}</p><p>{text(bundle.technical_summary) || '包中未提供工程摘要。'}</p><small>描述适用于下列源码快照；部署环境与目标版本须另行核对。</small></section>
    <dl className="business-overview"><div><dt>产品标识</dt><dd>{text(project.id) || '未标明'}</dd></div><div><dt>生成时间</dt><dd>{displayDate(bundle.generated_at)}</dd></div><div><dt>包编号</dt><dd>{text(bundle.bundle_id) || '未标明'}</dd></div><div><dt>包版本</dt><dd>v{source.version || 1} · 格式 {text(bundle.schema_version) || '未标明'}</dd></div><div><dt>部署版本</dt><dd>{text(bundle.source_snapshot?.deployment) || '未标明'}</dd></div></dl>
    <details><summary>来源仓库版本</summary><ul>{list(bundle.source_snapshot?.repositories).map(repository => <li key={repository.id}>{repository.id} · {repository.revision} · {repository.dirty === null ? '工作树状态未知' : repository.dirty ? '含未提交修改' : '工作树无未提交修改'}</li>)}</ul></details>
    <details><summary>覆盖范围与限制</summary><div className="business-coverage"><p><b>深入查看：</b>{ids(coverage.inspected_modules).join('、') || '未标明'}</p><p><b>仅建索引：</b>{ids(coverage.indexed_modules).join('、') || '未标明'}</p><p><b>排除范围：</b>{Array.isArray(coverage.excluded) ? coverage.excluded.map(String).join('、') : text(coverage.excluded) || '未标明'}</p><p><b>限制：</b>{Array.isArray(coverage.limitations) ? coverage.limitations.map(String).join('；') : text(coverage.limitations) || '未标明'}</p></div></details>
    <details className="business-scope"><summary>调整参考范围 · {savedModules.length}/{modules.length} 个模块</summary><p className="muted">新包默认使用全部模块。可缩小范围；依赖模块会一并纳入分析。此操作不会改变包内事实。</p><div className="business-module-list">{modules.map(module => <label className="business-module" key={module.id}><input type="checkbox" checked={moduleIds.includes(module.id)} disabled={busy || submitting || source.excluded} onChange={() => { setModuleIds(moduleIds.includes(module.id) ? moduleIds.filter(id => id !== module.id) : [...moduleIds, module.id]); setError(''); setMessage(''); }} /><span><b>{module.name || module.id}</b><small>{module.summary}</small>{ids(module.depends_on).length > 0 && <small>依赖：{ids(module.depends_on).map(id => modules.find(item => item.id === id)?.name || id).join('、')}</small>}</span></label>)}</div>{!moduleIds.length && <p className="notice">至少选择一个模块才能保存并启用此版本；若要停用，请启用另一版本或排除此包。</p>}{moduleDirty && <p className="notice">参考范围尚未保存；草稿保留在此浏览器。</p>}<button type="button" className="primary" disabled={(!moduleDirty && active) || !moduleIds.length || busy || submitting || source.excluded} onClick={() => void save()}>{submitting ? '正在保存…' : active ? '保存参考范围' : '保存并启用此版本'}</button></details>
    <section className="business-reader__section"><h3>业务与工程陈述</h3><p className="muted">代码与配置观察可作为该快照的现状引用；文档陈述需注明来源。推断和冲突保持原标记，不作为无争议事实。本包不会自动生成本期新需求。</p>{groups.map(group => <section className="business-claim-group" key={group} aria-label={dimensionNames[group] || group}><h4>{dimensionNames[group] || group} <small>· {claims.filter(claim => (text(claim.dimension) || 'other') === group).length} 条</small></h4>{claims.filter(claim => (text(claim.dimension) || 'other') === group).map(claim => { const conflicted = conflictClaims.has(claim.id), inRange = ids(claim.module_ids).some(id => activeModules.has(id)); return <article className="business-claim" key={claim.id}><div className="business-claim__tags"><span className={'business-claim__tag' + (claim.origin === 'inference' ? ' is-caution' : '')}>{originNames[claim.origin] || claim.origin || '来源未标明'}</span>{conflicted && <span className="business-claim__tag is-caution">存在冲突</span>}{active && !inRange && <span className="business-claim__tag">当前范围外</span>}</div><p>{claim.text}</p><small>关联模块：{ids(claim.module_ids).map(id => modules.find(item => item.id === id)?.name || id).join('、') || '未标明'}</small><details><summary>查看出处</summary>{ids(claim.evidence_ids).length ? ids(claim.evidence_ids).map(id => { const item = evidence.find(entry => entry.id === id); return item ? <div className="business-evidence" key={id}><b>{item.repository_id || '仓库'} · {item.path || '路径未标明'}{item.symbol ? ` · ${item.symbol}` : ''}</b><small>行 {item.line_start || '?'}–{item.line_end || '?'} · SHA-256 {item.sha256 || '未标明'} · {item.kind || '出处'}</small>{item.snippet && <pre>{item.snippet}</pre>}</div> : <p key={id}>出处 {id} 未在包中找到</p>; }) : <p>此陈述未关联具体出处。</p>}</details></article>; })}</section>)}{!claims.length && <p className="material-empty">此包没有可展示的陈述。</p>}</section>
    <div className="business-reader__columns"><section className="business-reader__section"><h3>仍待确认</h3>{unknowns.length ? unknowns.map(item => <p key={item.id} className="business-note">{item.question}<small>{ids(item.module_ids).map(id => modules.find(module => module.id === id)?.name || id).join('、')}</small></p>) : <p className="material-empty">包中未列出未知项。</p>}</section><section className="business-reader__section"><h3>资料冲突</h3>{conflicts.length ? conflicts.map(item => <p key={item.id} className="business-note">{item.description}<small>涉及陈述：{ids(item.claim_ids).join('、')}</small></p>) : <p className="material-empty">包中未列出冲突。</p>}</section></div>
    {message && <p role="status" className="business-import__message">{message}</p>}{error && <p role="alert" className="error">{error}</p>}
    <footer className="business-reader__footer"><small>资料编号 {source.id} · 原件 SHA-256 {source.sha256 || '未取得'}</small><button type="button" disabled={submitting} onClick={close}>关闭</button></footer>
  </section></div>;
}
