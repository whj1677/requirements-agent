import React, { useEffect, useRef, useState } from 'react';
import { api, ApiError, categorizeError, ErrorCategory, ActionInput, ActionPlan, BusinessAction, Project, Run, UserTask } from './api';
import { ArtifactTabs } from './workbench';
import { SourcePanel, VersionHistory, ConfirmationPanel, DocumentStudio } from './panels';
import { useDialog } from './shell';
import { ProductCheckpoint, QuestionList } from './product-flow';
import { useDirty, useNavigation } from './editing';
import { Icon } from './ui-icons';
import { phases, TaskStatus, ActionDialog, OptionDecision } from './guided';
type Obj = Record<string, any>;
export type Compat = { status: 'ok' | 'incompatible' | 'unverified'; missing: string[] };
const categoryText: Record<ErrorCategory, string> = { 'not-found': '项目不存在或已被删除', 'route-missing': '所需接口缺失，前后端版本可能不兼容', network: '无法连接服务', 'non-json': '服务返回了无法识别的内容', session: '登录已失效，请重新登录', forbidden: '访问被拒绝', conflict: '内容版本已变化', server: '暂时无法加载此项目', unknown: '暂时无法加载此项目' };

export function Workspace({ id, active, models, onProjectsChanged, compat, onExit, onReLogin }: { id: string; active: boolean; models: Obj; onProjectsChanged: () => Promise<void>; compat: Compat; onExit: () => void; onReLogin: () => void }) {
  const [p, setP] = useState<Project | null>(null), [phase, setPhase] = useState(()=>{const n=Number(sessionStorage.getItem('ra-phase-'+id));return n===3?4:n>=0&&n<6?n:0;}), [runs, setRuns] = useState<Run[]>([]), [tasks, setTasks] = useState<UserTask[]>([]);
  const [error,setError]=useState(''),[busy,setBusy]=useState(false);
  const [sourcesOpen, setSourcesOpen] = useState(false), [sourceText, setSourceText] = useState(''), [url, setUrl] = useState(''), [purpose, setPurpose] = useState('goal'), [dynamic, setDynamic] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false), [history, setHistory] = useState<Obj[]>([]), [artifacts, setArtifacts] = useState<Obj>({ document_artifact: [] });
  const [docType, setDocType] = useState('prd'), [selectedDocId, setSelectedDocId] = useState(''), [rename, setRename] = useState(''), [renameOpen, setRenameOpen] = useState(false);
  const [, setShowAdvanced]=useState(false),[,setStage]=useState('ingest');
  const [pending, setPending] = useState<{ plan: ActionPlan; input: ActionInput; key: string } | null>(null), [actionError, setActionError] = useState('');
  const [optionDecision, setOptionDecision] = useState<Obj | null>(null), [previewOption, setPreviewOption] = useState<Obj | null>(null);
  const [budget, setBudget] = useState(8), [focusRefs, setFocusRefs] = useState<Obj[]>([]);
  const [fatal, setFatal] = useState<ApiError | null>(null), [stale, setStale] = useState<string[]>([]), [staleNotice, setStaleNotice] = useState(''), [tasksLoaded, setTasksLoaded] = useState(false), [compatDismissed, setCompatDismissed] = useState(false);
  const [help,setHelp]=useState<Obj|null>(null),[helpText,setHelpText]=useState(''),[candidateOpen,setCandidateOpen]=useState(false),[candidateTitle,setCandidateTitle]=useState(''),[candidateText,setCandidateText]=useState('');
  const guard=useNavigation();
  function go(n:number){if(n===3)n=4;guard(()=>{setPhase(n);sessionStorage.setItem('ra-phase-'+id,String(n));});}
  const projectRef=useRef<Project|null>(null);
  const serial = useRef(0), root = '/projects/' + id;
  const mainScroll = useRef<HTMLElement | null>(null), scrollPositions = useRef<Record<number, number>>({});
  useEffect(() => { if (mainScroll.current) mainScroll.current.scrollTop = scrollPositions.current[phase] || 0; }, [phase]);
  useDialog(sourcesOpen, () => guard(()=>setSourcesOpen(false)));
  useDialog(!!help,()=>guard(()=>setHelp(null)));
  useDialog(candidateOpen,()=>guard(()=>setCandidateOpen(false)));
  async function saveSourceDraft(){if(sourceText.trim()) {await mutate('/sources/text',{text:sourceText,purpose});setSourceText('');}if(url.trim()){await mutate('/sources/url',{url,dynamic,authorized_public:true,purpose});setUrl('');}}
  useDirty(id+'-sources',Boolean(sourceText.trim()||url.trim()),'项目资料',saveSourceDraft,()=>{setSourceText('');setUrl('');});
  useDirty(id+'-help',Boolean(helpText.trim()),'对象讨论输入',async()=>{await mutate('/sources/text',{text:help?.label+'\n'+helpText,purpose:'goal'});setHelpText('');},()=>setHelpText(''));
  async function saveCandidate(){await mutate('/items',{title:candidateTitle,statement:candidateText,change_type:'new'});setCandidateTitle('');setCandidateText('');setCandidateOpen(false);}
  useDirty(id+'-candidate',Boolean(candidateTitle||candidateText),'候选需求',saveCandidate,()=>{setCandidateTitle('');setCandidateText('');});
  useDialog(historyOpen, () => setHistoryOpen(false));
  useDialog(renameOpen, () => setRenameOpen(false));
  async function refresh() {
    const n = ++serial.current;
    // Read task status before its result snapshot: a fast completion must not
    // stop polling while leaving the previously fetched project on screen.
    const responses=await Promise.allSettled([api<Run[]>(root+'/runs'),api<UserTask[]>(root+'/actions'),api<Obj>(root+'/artifacts')]);
    let project: Project;
    try { project = await api<Project>(root); }
    catch (e) { if (n === serial.current) setFatal(e as ApiError); throw e; }
    if (n !== serial.current) return;
    projectRef.current=project; setFatal(null); setStaleNotice(''); setP(project);
    const failed: string[] = [];
    if(n!==serial.current)return;
    responses.forEach((result,index)=>{if(result.status==='rejected'){failed.push(['运行记录','任务列表','产物记录'][index]);return;}if(index===0)setRuns(result.value as Run[]);if(index===1){setTasks(result.value as UserTask[]);setTasksLoaded(true);}if(index===2)setArtifacts(result.value as Obj);});
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
    return () => {clearTimeout(timer);document.querySelectorAll('.source-highlight').forEach(el=>el.classList.remove('source-highlight'));};
  }, [sourcesOpen, focusRefs]);
  async function mutate(path: string, body: Obj, method = 'POST') { try{await api(root + path, method, { expected_revision: projectRef.current?.revision, ...body });await refresh();}catch(e){if(e instanceof ApiError&&e.status===409)await refresh().catch(onRefreshFail);throw e;} }
  async function planAction(action: BusinessAction, optionId?: string, target?: Obj, text = '') {
    const current=projectRef.current;
    if (!current) return;
    if (actionBlockReason) { setError(actionBlockReason); return; }
    const input: ActionInput = { expected_revision: current.revision, action, message: text, document_type: docType, option_id: optionId || null, max_calls: budget, ...(target?{target:{kind:target.kind,id:target.id,...(target.section_id?{section_id:target.section_id}:{})}}:{}) };
    const plan = await api<ActionPlan>(root + '/actions/plan', 'POST', input);
    setActionError(''); setPending({ input, plan, key: crypto.randomUUID() });
  }
  async function startAction() {
    if (!pending) return;
    if (actionBlockReason) { setActionError(actionBlockReason); return; }
    setBusy(true); setActionError('');
    try { await api(root + '/actions', 'POST', { ...pending.input, plan_hash: pending.plan.plan_hash, idempotency_key: pending.key, authorize: pending.plan.recipients.some(r => r.needs_authorization) }); setPending(null); if(pending.input.target){setHelpText('');setHelp(null);} await refresh(); }
    catch(e) { setActionError((e as Error).message); } finally { setBusy(false); }
  }
  function navigate(tab:string){if(tab==='sources'){setSourcesOpen(true);return;}go(tab==='documents'?4:tab==='scope'?1:2);}
  function openHelp(target:Obj){guard(()=>{setHelp(target);setHelpText('');});}
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
  const direction = p.options.find(o => o.direction_status === 'selected');
  const phaseActions=[['organize'],['organize','explore'],['clarify','change'],['prototype','change'],['document','review'],['review']][phase];
  const latest=tasks.filter(t=>phaseActions.includes(t.action)).slice(-1)[0];
  const shared = { busy, project: p, setTab: () => {}, onOption: (option: Obj, action: string) => setOptionDecision({ option, action }), onItem: (item: Obj, status: string) => act(() => mutate('/items/' + item.id, { selection_status: status })), onEditItem: (item: Obj, statement: string, fields: Obj = {}) => mutate('/items/' + item.id, { selection_status: item.selection_status, statement, ...fields }), onStage: stageAction, onActivate: (cid: string) => act(() => mutate('/ui-candidates/' + cid + '/activate', {})), onReject: (cid: string) => act(() => mutate('/ui-candidates/' + cid + '/reject', {})), onNavigate: navigate, previewUrl: '/api' + root + '/prototype', runs, models, onCancel: (rid: string) => act(async () => { await api(root + '/runs/' + rid + '/cancel', 'POST'); await refresh(); }), onResume: (rid: string) => stageAction(runs.find(r => r.id === rid)?.stage || 'ingest'), embedded: true, onSource: (refs: Obj[]) => { setFocusRefs(refs); setSourcesOpen(true); }, onPreviewOption: setPreviewOption };
  const currentPreviewOption = previewOption && (p.options.find(o => o.id === previewOption.id) || previewOption);
  const intakeTask=tasks.filter(t=>t.action==='organize').slice(-1)[0];
  const intakeLabel=intakeTask&&['failed','cancelled','paused_budget'].includes(intakeTask.status)?'资料已保存 · 分析未成功':intakeTask&&['queued','running'].includes(intakeTask.status)?'资料已保存 · 分析中':!p.messages.some(m=>m.role==='assistant'&&m.stage==='ingest')?'资料已保存 · 待分析':'分析已生成 · 待核对';
  const taskStatus = latest&&(latest.status!=='succeeded'||running)&&<TaskStatus task={latest} onCancel={()=>act(async()=>{await api(root+'/actions/'+latest.id+'/cancel','POST');await refresh();})} onRetry={()=>act(()=>planAction(latest.action))} onViewResult={['succeeded','partial'].includes(latest.status)?()=>go(latest.action==='organize'?1:latest.action==='document'?4:phase):undefined}/>;
  return <div hidden={!active} className="project-workspace">
    <header className="project-bar"><div className="project-identity"><h1 title={p.name}>{p.name}</h1><small>内容草稿 v{p.revision} · {p.product_flow?.[5]?.complete?'当前版本已建立确认基线':p.active_baseline_id?'有历史确认基线，当前草稿须重新核对':'尚无正式确认基线'}</small></div><div className="toolbar"><button onClick={() => { setError(''); setFocusRefs([]); setSourcesOpen(true); }}>项目资料 · {p.sources.length}</button><button onClick={() => act(async () => { setHistory(await api<Obj[]>(root + '/history')); setHistoryOpen(true); })}>历史版本</button><button onClick={() => { setRename(p.name); setRenameOpen(true); }}>重命名</button></div></header>
    <nav className="phase-nav" aria-label="需求工作阶段">{phases.map((name,n)=>{if(n===3)return null;const st=p.product_flow?.[n];const state=st?.needs_recheck?'recheck':st?.complete?'complete':!st?.available?'blocked':phase===n?'current':'pending';return <button key={name} data-state={state} aria-current={phase===n?'step':undefined} onClick={()=>go(n)}><span className="step-number">{state==='complete'&&phase!==n?<Icon name="check"/>:n>3?n:n+1}</span><b>{state==='blocked'&&<Icon name="lock"/>}{name}</b><small>{n===0&&st?.complete?intakeLabel:state==='recheck'?'需重新核对':state==='complete'?(phase===n?'已核对 · 当前查看':'已完成'):state==='blocked'?'前置未完成':phase===n?'当前进行中':'尚未开始'}</small></button>;})}</nav>
    {compat.status === 'incompatible' && <div className="error banner" role="alert">后端缺少必需接口能力（{compat.missing.join('、')}），前后端版本可能不兼容；请重启服务并重新加载本标签页。发起动作已暂停，查看与返回不受影响。</div>}
    {compat.status === 'unverified' && !compatDismissed && <div className="notice banner" role="status">后端未提供能力信息，兼容性未核实；如遇功能异常，后端代码变更后需重启服务、标签页需重新加载。<button onClick={() => setCompatDismissed(true)}>知道了</button></div>}
    {staleNotice && <div className="error banner" role="alert">最新状态尚未取得：{staleNotice}<button onClick={retry}>重试</button><button onClick={() => setStaleNotice('')}>关闭</button></div>}
    {!staleNotice && stale.length > 0 && <div className="notice banner" role="status">部分状态未能更新（{stale.join('、')}）；页面显示的是已取得的最近内容。<button onClick={retry}>重试</button></div>}
    {error && <div className="error banner" role="alert">{error}<button onClick={() => act(refresh)}>刷新内容并保留输入</button><button onClick={() => setError('')}>关闭</button></div>}
    <div className="workflow-grid">
      <section className="workflow-main" ref={mainScroll} onScroll={e=>{scrollPositions.current[phase]=e.currentTarget.scrollTop;}}>
        <div className="workflow-page" data-phase={phase}><div className="workflow-heading"><div className="task-icon" aria-hidden="true"><Icon name="document"/></div><p className="eyebrow">当前任务 · 第 {phase>3?phase:phase+1} / 5 步</p><h2>{phases[phase]}</h2><p className="muted">{['请先描述您要解决的问题，资料可随时补充。我们将基于你的描述，梳理现状并识别改动范围。','纠正理解偏差，确定这次改什么、保留什么。','回答会影响业务行为的问题，未知不会被自动补齐。','对照原页面，核对本次增量与操作意图。','分别阅读并核对 MRD、PRD 的当前版本。','复核范围与交付版本，再由服务端建立确认基线。'][phase]}</p>{phase<3&&<aside className="task-guidance"><Icon name="target"/><div><b>{['先提供已知信息，缺失项稍后澄清','核对整理结果','为什么需要澄清这些问题？'][phase]}</b><p>{['我们将梳理现状、改动目标与约束条件，再由你核对。','分析建议尚非业务决定，请检查现状、目标、保持项和不在本期的范围。','这些问题涉及业务规则、流程和约束，直接影响后续设计、开发与验收。'][phase]}</p></div></aside>}{phase===4&&<aside className="document-version"><span>当前底稿</span><b>v{p.revision}</b><small>{(p as any).document_review_status?.[docType]?.reviewed?'当前文档已核对':'当前文档待核对'}</small></aside>}</div>
        {!(phase===4&&latest?.status==='partial')&&taskStatus}
        <div hidden={phase!==0}><ProductCheckpoint p={p} phase={0} busy={busy||running} mutate={mutate} go={go} onSources={()=>setSourcesOpen(true)} onAnalyze={()=>act(()=>planAction('organize'))}/></div>
        <div hidden={phase!==1}>
          <ProductCheckpoint p={p} phase={1} busy={busy||running} mutate={mutate} go={go} onSources={()=>setSourcesOpen(true)} onHelp={openHelp} onCandidate={()=>setCandidateOpen(true)} onItem={shared.onItem} onSource={shared.onSource}/>

          <details className="secondary-tools"><summary>按需比较方案方向（不是必选步骤）</summary><p>当前方向：{direction?.name||'尚未选择'}。方向不会自动采纳关联假设。</p><button disabled={busy||running||!!actionBlockReason} onClick={()=>act(()=>planAction('explore'))}>探索可选方案</button><ArtifactTabs {...shared} tab="options"/>{currentPreviewOption&&<article><h3>{currentPreviewOption.name}</h3><p>{currentPreviewOption.user_path}</p><p>方向讨论不等于条目采纳或正式确认。</p><button onClick={()=>setPreviewOption(null)}>收起预览</button></article>}</details>
        </div>
        <div hidden={phase!==2}>
          <QuestionList p={p} mutate={mutate} busy={busy||running} onHelp={openHelp}/>
          <details className="secondary-tools"><summary>核对条目与业务行为，或根据回答更新理解</summary><div className="toolbar"><button disabled={busy||running||!!actionBlockReason} onClick={()=>act(()=>planAction('clarify'))}>根据已保存回答更新理解</button><small>明确的模型任务，发送前核对范围。</small></div>
          <ArtifactTabs {...shared} tab="summary"/>
          </details><ProductCheckpoint p={p} phase={2} busy={busy||running} mutate={mutate} go={go}/>
        </div>
        <div className="document-stage" hidden={phase!==4} id={'document-result-'+id}>
          <DocumentStudio taskStatus={latest?.status==='partial'?taskStatus:undefined} p={p} docType={docType} setDocType={setDocType} selectedDocId={selectedDocId} setSelectedDocId={setSelectedDocId} artifacts={artifacts} setStage={setStage} setShowAdvanced={setShowAdvanced} setTab={navigate} setOutcome={()=>{}} root={root} act={act} mutate={mutate} busy={busy||running} onHelp={openHelp} onGenerate={()=>act(()=>planAction('document'))}/>
          <ProductCheckpoint p={p} phase={4} busy={busy||running} mutate={mutate} go={go}/>
        </div>
        <section hidden={phase!==5} id={'confirmation-'+id}><ConfirmationPanel p={p} busy={busy} setBusy={setBusy} setError={setError} setTab={navigate} setStage={setStage} setShowAdvanced={setShowAdvanced} root={root} act={act} refresh={refresh} onReview={()=>act(()=>planAction('review'))}/></section>
        </div>
      </section>
    </div>
    {help&&<div className="drawer-backdrop"><section className="source-drawer context-help" role="dialog" aria-modal="true" aria-label="当前对象的 AI 帮助"><button onClick={()=>guard(()=>setHelp(null))}>关闭帮助</button><p className="eyebrow">只围绕当前对象</p><h2>{help.label}</h2><small>{help.id}{help.section_id?' · '+help.section_id:''}</small><p>建议形成后仍需核对，不会自动回答问题或采纳条目。</p><label>希望怎样调整或澄清<textarea rows={5} value={helpText} onChange={e=>setHelpText(e.target.value)}/></label><button className="primary" disabled={busy||running||!helpText.trim()} onClick={()=>act(()=>planAction(help.kind==='ui'?'change':'clarify',undefined,help,helpText))}>核对本次讨论任务</button><details><summary>任务预算</summary><label>本次最多请求数<input type="number" min="1" max="30" value={budget} onChange={e=>setBudget(Number(e.target.value))}/></label></details><details><summary>已保存的讨论历史 · {p.messages.length}</summary>{p.messages.map((m,n)=><article key={n}><b>{m.role==='user'?'用户输入':'助手建议'} · {m.stage}</b><p>{m.text}</p></article>)}</details></section></div>}
    {candidateOpen&&<div className="modal-backdrop"><form className="modal" role="dialog" aria-modal="true" aria-label="补充候选需求" onSubmit={e=>{e.preventDefault();act(saveCandidate);}}><h2>补充一条候选需求</h2><p>保留你的原文和来源；保存不会自动采纳。</p><label>需求名称<input required maxLength={200} value={candidateTitle} onChange={e=>setCandidateTitle(e.target.value)}/></label><label>需求原文<textarea required maxLength={6000} value={candidateText} onChange={e=>setCandidateText(e.target.value)}/></label>{error&&<p role="alert">{error}</p>}<div className="toolbar"><button type="button" onClick={()=>guard(()=>setCandidateOpen(false))}>取消</button><button disabled={busy} className="primary">保存候选</button></div></form></div>}
    {sourcesOpen && <div className="drawer-backdrop"><section className="source-drawer" role="dialog" aria-modal="true" aria-label="项目资料"><button onClick={() => guard(()=>setSourcesOpen(false))}>关闭项目资料</button><SourcePanel p={p} models={models} purpose={purpose} setPurpose={setPurpose} sourceText={sourceText} setSourceText={setSourceText} url={url} setUrl={setUrl} dynamic={dynamic} setDynamic={setDynamic} busy={busy} act={act} error={error} mutate={mutate} refresh={refresh} root={root} pendingGrant={null} setPendingGrant={() => {}} guided /></section></div>}
    {historyOpen && <div className="drawer-backdrop"><section className="source-drawer" role="dialog" aria-modal="true" aria-label="历史版本"><button onClick={() => setHistoryOpen(false)}>返回当前任务</button><VersionHistory history={history} /><details><summary>完整讨论记录 · {p.messages.length}</summary>{p.messages.map((m,n)=><article className="card" key={n}><b>{m.role==='user'?'用户输入':'助手建议'} · {m.stage}</b><p>{m.text}</p></article>)}</details></section></div>}
    {renameOpen && <div className="modal-backdrop"><form className="modal" role="dialog" aria-label="重命名项目" onSubmit={e => { e.preventDefault(); if (!rename.trim()) return; act(async () => { await mutate('', { name: rename.trim() }, 'PATCH'); setRenameOpen(false); await onProjectsChanged(); }); }}><h2>重命名项目</h2><label>项目名称<input value={rename} onChange={e => setRename(e.target.value)} /></label><button type="button" onClick={() => setRenameOpen(false)}>取消</button><button disabled={!rename.trim() || busy}>保存</button></form></div>}
    {pending && <ActionDialog plan={pending.plan} input={pending.input} error={actionError} busy={busy} onClose={() => setPending(null)} onSubmit={startAction} />}
    {optionDecision && <OptionDecision option={optionDecision.option} action={optionDecision.action} p={p} submit={mutate} onClose={() => setOptionDecision(null)} />}
  </div>;
}
