import React, { useEffect, useRef, useState } from 'react';
import { api, ApiError, categorizeError, ErrorCategory, ActionInput, ActionPlan, BusinessAction, Project, Run, UserTask } from './api';
import { ConversationPanel, ArtifactTabs, PrototypeCanvas } from './workbench';
import { SourcePanel, VersionHistory, ConfirmationPanel, DocumentStudio } from './panels';
import { useDialog } from './shell';
import { phases, TaskStatus, ActionDialog, OptionDecision } from './guided';
type Obj = Record<string, any>;
export type Compat = { status: 'ok' | 'incompatible' | 'unverified'; missing: string[] };
const categoryText: Record<ErrorCategory, string> = { 'not-found': '项目不存在或已被删除', 'route-missing': '所需接口缺失，前后端版本可能不兼容', network: '无法连接服务', 'non-json': '服务返回了无法识别的内容', session: '登录已失效，请重新登录', forbidden: '访问被拒绝', conflict: '内容版本已变化', server: '暂时无法加载此项目', unknown: '暂时无法加载此项目' };

export function Workspace({ id, active, models, onProjectsChanged, compat, onExit, onReLogin }: { id: string; active: boolean; models: Obj; onProjectsChanged: () => Promise<void>; compat: Compat; onExit: () => void; onReLogin: () => void }) {
  const [p, setP] = useState<Project | null>(null), [phase, setPhase] = useState(0), [runs, setRuns] = useState<Run[]>([]), [tasks, setTasks] = useState<UserTask[]>([]);
  const [message, setMessage] = useState(''), [lastSent, setLastSent] = useState(''), [error, setError] = useState(''), [busy, setBusy] = useState(false);
  const [sourcesOpen, setSourcesOpen] = useState(false), [sourceText, setSourceText] = useState(''), [url, setUrl] = useState(''), [purpose, setPurpose] = useState('goal'), [dynamic, setDynamic] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false), [history, setHistory] = useState<Obj[]>([]), [artifacts, setArtifacts] = useState<Obj>({ document_artifact: [] });
  const [docType, setDocType] = useState('prd'), [selectedDocId, setSelectedDocId] = useState(''), [rename, setRename] = useState('');
  const [showAll, setShowAll] = useState(false), [showAdvanced, setShowAdvanced] = useState(false), [stage, setStage] = useState('ingest'), [mobilePane, setMobilePane] = useState('content'), [expanded, setExpanded] = useState(false);
  const [pending, setPending] = useState<{ plan: ActionPlan; input: ActionInput; key: string } | null>(null), [actionError, setActionError] = useState('');
  const [optionDecision, setOptionDecision] = useState<Obj | null>(null), [previewOption, setPreviewOption] = useState<Obj | null>(null), [prototypeOpen, setPrototypeOpen] = useState(false);
  const [budget, setBudget] = useState(8), [checkedScope, setCheckedScope] = useState(false), [focusRefs, setFocusRefs] = useState<Obj[]>([]);
  const [fatal, setFatal] = useState<ApiError | null>(null), [stale, setStale] = useState<string[]>([]), [staleNotice, setStaleNotice] = useState(''), [tasksLoaded, setTasksLoaded] = useState(false), [compatDismissed, setCompatDismissed] = useState(false);
  const serial = useRef(0), root = '/projects/' + id;
  const mainScroll = useRef<HTMLElement | null>(null), scrollPositions = useRef<Record<number, number>>({});
  useEffect(() => { if (mainScroll.current) mainScroll.current.scrollTop = scrollPositions.current[phase] || 0; }, [phase]);
  useDialog(sourcesOpen, () => setSourcesOpen(false));
  useDialog(historyOpen, () => setHistoryOpen(false));
  useDialog(Boolean(rename), () => setRename(''));
  async function refresh() {
    const n = ++serial.current;
    let project: Project;
    try { project = await api<Project>(root); }
    catch (e) { if (n === serial.current) setFatal(e as ApiError); throw e; }
    if (n !== serial.current) return;
    setFatal(null); setStaleNotice(''); setP(project);
    const failed: string[] = [];
    const auxiliaries: [string, () => Promise<void>][] = [
      ['运行记录', async () => { setRuns(await api<Run[]>(root + '/runs')); }],
      ['任务列表', async () => { setTasks(await api<UserTask[]>(root + '/actions')); setTasksLoaded(true); }],
      ['产物记录', async () => { setArtifacts(await api<Obj>(root + '/artifacts')); }],
    ];
    await Promise.all(auxiliaries.map(async ([label, load]) => { try { await load(); } catch { failed.push(label); } }));
    if (n !== serial.current) return;
    setStale(failed);
  }
  function onRefreshFail(e: unknown) { setStaleNotice(categoryText[categorizeError(e)]); }
  function retry() { setBusy(true); refresh().catch(onRefreshFail).finally(() => setBusy(false)); }
  async function act(fn: () => Promise<unknown>) { setBusy(true); setError(''); try { await fn(); } catch(e) { setError((e as Error).message); } finally { setBusy(false); } }
  useEffect(() => { if (active) refresh().catch(onRefreshFail); }, [active]);
  const running = tasks.some(t => ['queued','running'].includes(t.status)) || runs.some(r => ['queued','running'].includes(r.status));
  useEffect(() => { if (!active || !running) return; const timer = setInterval(() => refresh().catch(onRefreshFail), 800); return () => clearInterval(timer); }, [active, running]);
  useEffect(() => {
    if (!sourcesOpen || !focusRefs.length) return;
    const timer = setTimeout(() => { const el = document.getElementById('excerpt-' + focusRefs[0].excerpt_id); const details = el?.closest('details'); if (details) details.open = true; el?.scrollIntoView({ block: 'center' }); el?.classList.add('source-highlight'); }, 50);
    return () => clearTimeout(timer);
  }, [sourcesOpen, focusRefs]);
  async function mutate(path: string, body: Obj, method = 'POST') { await api(root + path, method, { expected_revision: p?.revision, ...body }); await refresh(); }
  async function planAction(action: BusinessAction, optionId?: string) {
    if (!p) return;
    if (actionBlockReason) { setError(actionBlockReason); return; }
    const input: ActionInput = { expected_revision: p.revision, action, message: message !== lastSent ? message : '', document_type: docType, option_id: optionId || null, max_calls: budget };
    const plan = await api<ActionPlan>(root + '/actions/plan', 'POST', input);
    setActionError(''); setPending({ input, plan, key: crypto.randomUUID() });
  }
  async function startAction() {
    if (!pending) return;
    if (actionBlockReason) { setActionError(actionBlockReason); return; }
    setBusy(true); setActionError('');
    try { await api(root + '/actions', 'POST', { ...pending.input, plan_hash: pending.plan.plan_hash, idempotency_key: pending.key, authorize: pending.plan.recipients.some(r => r.needs_authorization) }); if (pending.input.message) setLastSent(pending.input.message); setPending(null); await refresh(); }
    catch(e) { setActionError((e as Error).message); } finally { setBusy(false); }
  }
  function navigate(tab: string) { if (tab === 'documents') setPhase(3); else if (tab === 'prototype') { setPhase(1); setPrototypeOpen(true); } else if (tab === 'sources') setSourcesOpen(true); else { setPhase(2); setShowAll(true); } }
  function stageAction(s: string) { const action: Record<string, BusinessAction> = { ingest: 'organize', vision: 'organize', brainstorm: 'explore', clarify: 'clarify', ui: 'prototype', prd: 'document', review: 'review', change: 'change' }; act(() => planAction(action[s] || 'organize')); }
  const actionBlockReason = compat.status === 'incompatible' ? '后端缺少必需接口能力（' + compat.missing.join('、') + '），发起动作已暂停；请重启服务并重新加载本标签页' : stale.includes('任务列表') ? (tasksLoaded ? '任务状态读取失败，当前状态待刷新；页面显示的是此前取得的任务记录，发起动作已暂停，请重试刷新' : '任务状态尚未取得，发起动作已暂停；请先重试刷新') : '';
  if (!p) {
    if (fatal) {
      const category = categorizeError(fatal);
      return <div hidden={!active} className="workspace"><article className="card" role="alert">
        <h2>{categoryText[category]}</h2>
        <p>{category === 'route-missing' ? '当前服务可能缺少此页面所需的接口；后端代码变更后需重启服务，本标签页需重新加载。' : category === 'session' ? '登录状态已失效，可以重新登录后再进入项目。' : '可以重试加载，或返回项目列表。'}</p>
        <div className="toolbar"><button onClick={retry}>重新加载</button><button onClick={onExit}>返回项目列表</button>{category === 'session' && <button onClick={onReLogin}>重新登录</button>}</div>
        <details><summary>诊断信息</summary><p>HTTP 状态：{fatal.status || '无响应'} · 接口：{fatal.path} · 错误码：{fatal.code} · 类型：{fatal.kind}</p></details>
      </article></div>;
    }
    return <div hidden={!active} className="workspace"><p>正在读取项目内容…</p></div>;
  }
  const open = p.questions.filter(q => q.status !== 'answered'), selected = p.items.filter(i => i.selection_status === 'selected');
  const failed = p.sources.filter(s => !s.excluded && ['failed','partial','awaiting_vision'].includes(s.parse_status));
  const direction = p.options.find(o => o.direction_status === 'selected');
  const latest = tasks[tasks.length - 1];
  const primary = [ '整理当前信息', '探索可选方案', '核对本期内容', p.documents[docType] ? '更新讨论稿' : '生成讨论稿', '核对正式确认条件' ][phase];
  const primaryAction = () => phase === 2 ? (setCheckedScope(true), setShowAll(true)) : phase === 4 ? document.getElementById('confirmation-' + id)?.scrollIntoView() : act(() => planAction((['organize','explore','','document'] as BusinessAction[])[phase]));
  const shared = { project: p, setTab: () => {}, onOption: (option: Obj, action: string) => setOptionDecision({ option, action }), onItem: (item: Obj, status: string) => act(() => mutate('/items/' + item.id, { selection_status: status })), onEditItem: (item: Obj, statement: string) => mutate('/items/' + item.id, { selection_status: item.selection_status, statement }), onStage: stageAction, onActivate: (cid: string) => act(() => mutate('/ui-candidates/' + cid + '/activate', {})), onReject: (cid: string) => act(() => mutate('/ui-candidates/' + cid + '/reject', {})), onNavigate: navigate, previewUrl: '/api' + root + '/prototype', runs, models, onCancel: (rid: string) => act(async () => { await api(root + '/runs/' + rid + '/cancel', 'POST'); await refresh(); }), onResume: (rid: string) => stageAction(runs.find(r => r.id === rid)?.stage || 'ingest'), embedded: true, onSource: (refs: Obj[]) => { setFocusRefs(refs); setSourcesOpen(true); }, onPreviewOption: setPreviewOption };
  const optionArtifact = previewOption && [p.ui, ...((p as Obj).ui_candidates || []).filter((c: Obj) => c.status !== 'rejected')].find(a => a?.generation_target?.option_id === previewOption.id);
  return <div hidden={!active} className="project-workspace">
    <header className="project-bar"><div><h1>{p.name}</h1><small>内容草稿 v{p.revision} · {p.active_baseline_id ? '有历史确认基线，当前内容仍须核对' : '尚无正式确认基线'}</small></div><div className="toolbar"><button onClick={() => { setFocusRefs([]); setSourcesOpen(true); }}>项目资料 · {p.sources.length}</button><button onClick={() => act(async () => { setHistory(await api<Obj[]>(root + '/history')); setHistoryOpen(true); })}>历史版本</button><button onClick={() => setRename(p.name)}>重命名</button></div></header>
    <nav className="phase-nav" aria-label="需求工作阶段">{phases.map((name, n) => <button key={name} aria-current={phase === n ? 'step' : undefined} onClick={() => setPhase(n)}><span>{n + 1}</span>{name}</button>)}</nav>
    {compat.status === 'incompatible' && <div className="error banner" role="alert">后端缺少必需接口能力（{compat.missing.join('、')}），前后端版本可能不兼容；请重启服务并重新加载本标签页。发起动作已暂停，查看与返回不受影响。</div>}
    {compat.status === 'unverified' && !compatDismissed && <div className="notice banner" role="status">后端未提供能力信息，兼容性未核实；如遇功能异常，后端代码变更后需重启服务、标签页需重新加载。<button onClick={() => setCompatDismissed(true)}>知道了</button></div>}
    {staleNotice && <div className="error banner" role="alert">最新状态尚未取得：{staleNotice}<button onClick={retry}>重试</button><button onClick={() => setStaleNotice('')}>关闭</button></div>}
    {!staleNotice && stale.length > 0 && <div className="notice banner" role="status">部分状态未能更新（{stale.join('、')}）；页面显示的是已取得的最近内容。<button onClick={retry}>重试</button></div>}
    {error && <div className="error banner" role="alert">{error}<button onClick={() => act(refresh)}>刷新内容并保留输入</button><button onClick={() => setError('')}>关闭</button></div>}
    <div className="guided-mobile-switch"><button onClick={() => setMobilePane('content')}>任务内容</button><button onClick={() => setMobilePane('assistant')}>需求助手</button></div>
    <div className={'guided-grid ' + mobilePane + (expanded ? ' expanded-work' : '')}>
      <section className="guided-main" ref={mainScroll} onScroll={e => { scrollPositions.current[phase] = e.currentTarget.scrollTop; }}><div className="task-guide"><div><small>当前任务</small><h2>{phases[phase]}</h2><p>{p.items.length ? `已有 ${p.items.length} 条内容，其中 ${selected.length} 条已采纳。` : '先描述要解决的问题，资料可随时补充。'}{p.options.length ? `已有 ${p.options.length} 个方案，${direction ? '当前讨论方向：' + direction.name : '方向尚未选择'}。` : ''}</p><p>{open.length ? `仍有 ${open.length} 个问题未决定，可以继续讨论，正式确认另有门禁。` : '请核对当前内容与实际业务是否一致。'}{failed.length ? ` ${failed.length} 项资料尚有读取或图片分析限制。` : ''}</p></div><div className="toolbar"><button className="primary" disabled={busy || running || !!actionBlockReason} onClick={primaryAction}>{primary}</button><button onClick={() => setSourcesOpen(true)}>添加资料</button><button onClick={() => setExpanded(!expanded)}>{expanded ? '恢复助手与导航' : '展开成果阅读'}</button></div>{actionBlockReason && <p className="notice">{actionBlockReason}</p>}</div>
        <TaskStatus task={latest} onCancel={() => act(async () => { await api(root + '/actions/' + latest.id + '/cancel', 'POST'); await refresh(); })} onRetry={() => act(() => planAction(latest.action))} />
        <section hidden={phase !== 0}><article className="card"><h2>目前理解</h2>{p.messages.filter(m => m.role === 'assistant').slice(-2).map((m, i) => <p key={i} className="preserve-lines">{m.text}</p>)}{!p.messages.length && <p>在右侧描述目标，或直接添加文字、文档、截图和公开网址。</p>}<p>资料：{p.sources.filter(s => !s.excluded && s.parse_status === 'read').length} 项已读取 · {failed.length} 项待处理或有限制</p><div className="toolbar"><button onClick={() => { setPhase(2); setShowAll(true); }}>查看内容与待定问题</button><button onClick={() => setPhase(1)}>查看方案方向</button></div></article></section>
        <section hidden={phase !== 1}><ArtifactTabs {...shared} tab="options" />{previewOption && <article className="card"><h3>{previewOption.name} · 对应原型</h3>{optionArtifact ? <><p>生成对象：{optionArtifact.generation_target.name} · 输入底稿 v{optionArtifact.input_revision} · {optionArtifact.generation_run_id}</p><iframe key={optionArtifact.id || p.hashes.ui_spec_hash} title="方案对应原型" sandbox="allow-scripts" className="prototype" src={'/api' + root + (optionArtifact.id ? '/ui-candidates/' + optionArtifact.id + '/prototype' : '/prototype')} /></> : <><p className="notice">此方案尚无原型。项目当前原型不会被当作此方案的原型。</p><p>本批仅支持为当前讨论方向或项目内容生成；为未选方案独立生成暂不支持。</p>{previewOption.direction_status === 'selected' && <button onClick={() => act(() => planAction('prototype', previewOption.id))}>生成此方向讨论原型</button>}</>}<button onClick={() => setPreviewOption(null)}>收起方案预览</button></article>}<div className="toolbar"><button onClick={() => setPrototypeOpen(!prototypeOpen)}>{prototypeOpen ? '收起项目当前原型' : '查看项目当前原型'}</button><button disabled={running || busy || !!actionBlockReason} onClick={() => act(() => planAction('prototype'))}>生成讨论原型</button><button onClick={() => act(() => planAction('change'))}>讨论修改方案</button></div><div hidden={!prototypeOpen}><PrototypeCanvas project={p} url={'/api' + root + '/prototype'} onStage={stageAction} onActivate={shared.onActivate} onReject={shared.onReject} /></div></section>
        <section hidden={phase !== 2}>{checkedScope && <p className="notice">请逐项核对原文、来源、属性与采纳状态。此处核对不创建正式确认。</p>}<ArtifactTabs {...shared} tab="summary" /><button onClick={() => act(() => planAction('clarify'))}>根据回答更新理解</button></section>
        <section hidden={phase !== 3}><p className="notice">讨论稿可包含未决项。当前 {open.length} 项未决定；{p.ui ? '已有项目原型，请核对对应版本。' : '尚无原型，文档不会编造原型图片。'}</p><DocumentStudio p={p} docType={docType} setDocType={setDocType} selectedDocId={selectedDocId} setSelectedDocId={setSelectedDocId} artifacts={artifacts} setStage={setStage} setShowAdvanced={setShowAdvanced} setTab={navigate} setOutcome={() => {}} root={root} act={act} mutate={mutate} onGenerate={() => act(() => planAction('document'))} /><button disabled={busy || running || !!actionBlockReason} onClick={() => act(() => planAction('review'))}>审查当前内容</button></section>
        <section hidden={phase !== 4} id={'confirmation-' + id}><ConfirmationPanel p={p} busy={busy} setBusy={setBusy} setError={setError} setTab={navigate} setStage={setStage} setShowAdvanced={setShowAdvanced} root={root} act={act} refresh={refresh} onReview={() => act(() => planAction('review'))} /></section>
      </section>
      <aside className="guided-assistant"><div className="assistant-status"><strong>需求助手</strong><small>{message && message !== lastSent ? '输入未发送（仅保留在当前会话）' : lastSent ? '本轮输入已保存为项目来源' : '可随时补充目标与约束'}</small></div><ConversationPanel project={p} message={message} setMessage={setMessage} stage={stage} setStage={setStage} docType={docType} setDocType={setDocType} onRun={() => act(() => planAction('organize'))} runActive={running} busy={busy} onAnswer={(qid, answer) => act(() => mutate('/questions/' + qid, { answer }))} showAdvanced={showAdvanced} setShowAdvanced={setShowAdvanced} showAllQuestions={showAll} setShowAllQuestions={setShowAll} guided onUnknown={() => { setPhase(1); setMobilePane('content'); }} onAddSource={() => setSourcesOpen(true)} /><details className="engineering-controls"><summary>工程诊断与任务预算</summary><label>本次总请求上限<input type="number" min="1" max="30" value={budget} onChange={e => setBudget(Number(e.target.value))} /></label><label>诊断用途<select value={stage} onChange={e => setStage(e.target.value)}>{[['ingest','理解'],['brainstorm','方案'],['clarify','澄清'],['ui','原型'],['prd','文档'],['review','审查'],['change','变更']].map(([v,n]) => <option key={v} value={v}>{n}</option>)}</select></label><button onClick={() => stageAction(stage)}>核对并运行诊断任务</button></details></aside>
    </div>
    {sourcesOpen && <div className="drawer-backdrop"><section className="source-drawer" role="dialog" aria-modal="true" aria-label="项目资料"><button onClick={() => setSourcesOpen(false)}>关闭项目资料</button><SourcePanel p={p} models={models} purpose={purpose} setPurpose={setPurpose} sourceText={sourceText} setSourceText={setSourceText} url={url} setUrl={setUrl} dynamic={dynamic} setDynamic={setDynamic} busy={busy} act={act} mutate={mutate} refresh={refresh} root={root} pendingGrant={null} setPendingGrant={() => {}} guided /></section></div>}
    {historyOpen && <div className="drawer-backdrop"><section className="source-drawer" role="dialog" aria-modal="true" aria-label="历史版本"><button onClick={() => setHistoryOpen(false)}>返回当前任务</button><VersionHistory history={history} /></section></div>}
    {rename && <div className="modal-backdrop"><form className="modal" role="dialog" aria-label="重命名项目" onSubmit={e => { e.preventDefault(); act(async () => { await mutate('', { name: rename }, 'PATCH'); setRename(''); await onProjectsChanged(); }); }}><h2>重命名项目</h2><label>项目名称<input value={rename} onChange={e => setRename(e.target.value)} /></label><button type="button" onClick={() => setRename('')}>取消</button><button>保存</button></form></div>}
    {pending && <ActionDialog plan={pending.plan} input={pending.input} error={actionError} busy={busy} onClose={() => setPending(null)} onSubmit={startAction} />}
    {optionDecision && <OptionDecision option={optionDecision.option} action={optionDecision.action} p={p} submit={mutate} onClose={() => setOptionDecision(null)} />}
  </div>;
}
