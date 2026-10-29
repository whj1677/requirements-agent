import {session} from '../draft-storage';
import React, { useEffect, useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import { api, Blocker, BlockerAction, Project, UserTask, type ActionInput } from '../api';
import { useDirty, useNavigation } from '../editing';
import { activitySnapshot, subscribeActivity } from '../activity';
import { hasPendingInterviewInput } from '../intake-chat';
import { RequirementTable } from './RequirementTable';
const PrewriteReviewPanel=React.lazy(()=>import('./PrewriteReviewPanel').then(m=>({default:m.PrewriteReviewPanel})));
import { prewriteLabel } from '../prewrite-review';
import { singleRequirementRoot } from './requirement-location';
import { refinementDestination, ConnectedDestination } from './refine-navigation';
export type { ConnectedDestination } from './refine-navigation';
import { currentQuestions } from './refine-context';
import type {RefinementReturnState} from './refinement-return';
import {publicBlocker} from '../workflow-labels';
import './refine-guided.css';
import './connected-workspace.css';

type Obj = Record<string, any>;
type EntryMode = 'chat' | 'direct';
type Step = 'information' | 'refine' | 'delivery';
type DraftResponse = { text: string; entry_mode: EntryMode; revision: number; saved_at?: string };
type DiscussionDraft = { project_id: string; revision: number; formal: false; filename: string; text: string };
type ContextResponse = { revision: number; background: string; suggested_background?: string; terms: Obj[]; selected_source_ids: string[] | null; effective_source_ids: string[]; bound_input_source_ids?: string[]; unavailable_selected_source_ids: string[]; evidence_policy?: string };
const taskStatusText: Record<string, string> = { queued: '排队中', running: '执行中', succeeded: '已完成', partial: '部分完成，待核对', awaiting_user:'等待业务确认', failed: '失败', cancelled: '已取消', paused_budget: '预算已暂停', not_needed: '无需复核', proposal_ready: '建议待人工核对', pending_manual_review: '待人工核对' };
const humanError = (value?: string) => value && /^[A-Z][A-Z0-9_]+$/.test(value) ? '任务发生可定位错误，请打开任务记录查看原因与恢复操作。' : value || '';

export function ConnectedWorkspace({ id, project, tasks, active=true, busy, running, actionBlockReason, mutate, onOrganize, onClarify, onRecommendRequirement, onSources, chatPanel, onCreateRequirement, onCreateAcceptance, onDelivery, onDocuments, onRefresh, taskPanel, destination, workspaceRef, onMainNavigation, onResolveBlocker, deliveryBlockers, preparation={state:'idle'}, onPrepareRefinement, onCancelPreparation, assistantEntryNonce, onPrewrite, onGeneratePrd, onRefinementConfirmed }: {
  id: string; project: Project; tasks: UserTask[]; active?: boolean; busy: boolean; running: boolean; actionBlockReason: string;
  mutate: (path: string, body: Obj, method?: string) => Promise<any>;
  onOrganize: (instruction?:string,request?:ActionInput['draft_request']) => void; onClarify: (questionId?: string) => void; onSources: (refs?: Obj[]) => void;
  onRecommendRequirement: (requirementId: string, instruction: string) => void;
  chatPanel: (beforeSend: () => Promise<Project>, onDirect: (text: string) => void, onViewDraft: () => void, onPrepareDraft: () => void) => React.ReactNode;
  onCreateRequirement: () => void; onCreateAcceptance: (requirementId?: string) => void;
  onDelivery: () => void; onDocuments: () => void; onRefresh: () => void;
  workspaceRef?: React.Ref<HTMLDivElement>; taskPanel?: (task: UserTask) => React.ReactNode; destination?: ConnectedDestination; onMainNavigation?: () => void;
  onResolveBlocker: (action: BlockerAction, row: Blocker) => void;
  deliveryBlockers?: Blocker[];
  preparation?: {state:'idle'|'checking'|'running'|'failed'|'authorization';message?:string;taskId?:string};
  onPrepareRefinement: (retry?:boolean) => Promise<void>; onCancelPreparation: () => void;
  assistantEntryNonce?: number;
  onPrewrite:()=>void; onGeneratePrd:(allowReviewDraft?:boolean)=>void;
  onRefinementConfirmed?:()=>void;
}) {
  const root = '/projects/' + id;
  const navigate = useNavigation();
  const activity=useSyncExternalStore(subscribeActivity,activitySnapshot,activitySnapshot);
  const pendingActivity=activity.active.length>0;
  const [step, setStep] = useState<Step>('information');
  const [refineView,setRefineView]=useState<'requirements'|'prewrite'>('requirements');
  const [mode, setMode] = useState<EntryMode>('chat');
  const [assistantContextOpen, setAssistantContextOpen] = useState(false);
  const [savedMode, setSavedMode] = useState<EntryMode>('chat');
  const modeTouched=useRef(false),draftTouched=useRef(false);
  const assistantEntryRequested=typeof assistantEntryNonce==='number'&&Number.isFinite(assistantEntryNonce);
  const assistantEntrySeen=useRef<number|undefined>(undefined);
  const [draft, setDraft] = useState('');
  const [savedDraft, setSavedDraft] = useState('');
  const [draftRevision, setDraftRevision] = useState<number | null>(null);
  const [draftLoaded, setDraftLoaded] = useState(false);
  const [draftReadAttempt,setDraftReadAttempt]=useState(0);
  const [draftCommitted, setDraftCommitted] = useState(false);
  const [draftError, setDraftError] = useState('');
  const [localBusy, setLocalBusy] = useState(false);
  const preparingNext=useRef(false);
  const manualRefinement=useRef(false),restoreRefinement=useRef(false);
  const [refinementGate,setRefinementGate]=useState(false);
  const [refinementReturn,setRefinementReturn]=useState<RefinementReturnState|null>(null);
  const preparing=['checking','running','authorization'].includes(preparation.state);
  const scrollPositions = useRef<Partial<Record<string, number>>>({});
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [taskOpen, setTaskOpen] = useState(false);
  const [context, setContext] = useState<ContextResponse | null>(null);
  const [contextDraft, setContextDraft] = useState('');
  const [contextSelection, setContextSelection] = useState<string[] | null>(null);
  const [termsDraft, setTermsDraft] = useState<Obj[]>([]);
  const [contextError, setContextError] = useState('');
  const [discussion, setDiscussion] = useState<DiscussionDraft | null>(null);
  const [discussionError, setDiscussionError] = useState('');
  const [navigationError, setNavigationError] = useState('');
  const [entryError,setEntryError]=useState('');
  const [tableFocus,setTableFocus]=useState<{itemId?:string;questionId?:string;field?:string;scope?:boolean;nonce:number}>();
  const [tableValidationRequest,setTableValidationRequest]=useState<number>();
  const questions = useMemo(() => currentQuestions(project), [project.questions]);
  const candidates = project.items.filter(i => i.selection_status !== 'excluded');
  const hasExistingRequirements=project.items.some(i=>i.kind==='requirement'&&i.applies_to==='to_be'&&!i.superseded_by&&!['rejected','excluded'].includes(i.selection_status));
  const latestTask = tasks.at(-1);
  const draftDirty = draft !== savedDraft;
  const contextDirty = !!context && (contextDraft !== context.background || JSON.stringify(contextSelection) !== JSON.stringify(context.selected_source_ids) || JSON.stringify(termsDraft) !== JSON.stringify(context.terms || []));

  useEffect(() => {
    let cancelled = false;
    setNavigationError('');
    modeTouched.current=false;draftTouched.current=false;
    assistantEntrySeen.current=undefined;
    const savedStep = assistantEntryRequested?'information':session.getItem('ra-connected-step-' + id);
    manualRefinement.current=!assistantEntryRequested&&session.getItem('ra-refinement-entry-'+id)==='manual';
    restoreRefinement.current=savedStep==='refine'&&!manualRefinement.current;
    setRefinementGate(restoreRefinement.current);
    setStep(savedStep === 'refine' || savedStep === 'delivery' ? savedStep : 'information'); setMode('chat'); setSavedMode('chat'); setDraft(''); setSavedDraft(''); setDraftLoaded(false); setDraftCommitted(false); setDraftError(''); setTaskOpen(false); setContext(null); setDiscussion(null); setDiscussionError('');
    void api<ContextResponse>(root + '/domain-context').then(value => {
      if (cancelled) return;
      setContext(value); setContextDraft(value.background || ''); setContextSelection(value.selected_source_ids || null); setTermsDraft(value.terms || []);
    }).catch(e => { if (!cancelled) setContextError('项目背景未能读取：' + (e as Error).message); });
    return () => { cancelled = true; };
  }, [root]);

  useEffect(()=>{
    let cancelled=false;setDraftLoaded(false);setDraftError('');
    void api<DraftResponse>(root + '/workspace-draft').then(value => {
      if (cancelled) return;
      if(!draftTouched.current)setDraft(value.text || ''); setSavedDraft(value.text || ''); if(!modeTouched.current&&assistantEntrySeen.current===undefined)setMode(value.entry_mode === 'direct' ? 'direct' : 'chat'); setSavedMode(value.entry_mode === 'direct' ? 'direct' : 'chat'); setDraftRevision(value.revision); setDraftCommitted(Boolean(value.saved_at)); setDraftLoaded(true);
    }).catch(e => { if (!cancelled) { setDraftLoaded(false); setDraftError('工作底稿未能读取：' + (e as Error).message); } });
    return()=>{cancelled=true;};
  },[root,draftReadAttempt]);

  const scrollKey=step==='refine'?step+':'+refineView:step;
  useLayoutEffect(() => { if (active && containerRef.current) containerRef.current.scrollTop = scrollPositions.current[scrollKey] || 0; }, [scrollKey, active]);
  useEffect(() => {
    if (!destination) return;
    setStep(destination.step); session.setItem('ra-connected-step-' + id, destination.step);
    restoreRefinement.current=false;setRefinementGate(false);
    if(destination.step==='refine'){
      setRefineView(destination.prewrite?'prewrite':'requirements');
      if(!destination.prewrite){const reqId=singleRequirementRoot(project,destination);
        if(destination.review&&!reqId&&!destination.questionId&&!destination.field)setTableValidationRequest(value=>(value||0)+1);
        setTableFocus(previous=>({itemId:reqId,questionId:destination.questionId,field:destination.field,scope:destination.scope,nonce:(previous?.nonce||0)+1}));}
    }
    setTaskOpen(Boolean(destination.task));
  }, [id, destination?.nonce]);

  // A normal project entry is a view change. Keep the shared draft and its
  // stored mode/revision, while suppressing old session restoration and AI.
  useEffect(()=>{
    if(!assistantEntryRequested)return;
    assistantEntrySeen.current=assistantEntryNonce;
    modeTouched.current=false;
    restoreRefinement.current=false;manualRefinement.current=false;
    setStep('information');setMode('chat');setRefinementGate(false);
    setRefineView('requirements');
    setRefinementReturn(null);setTableFocus(undefined);setTaskOpen(false);
    setNavigationError('');setEntryError('');
    session.setItem('ra-connected-step-'+id,'information');
    session.removeItem('ra-refinement-entry-'+id);
  },[id,assistantEntryNonce]);

  async function saveDraft(): Promise<DraftResponse & { project?: Project }> {
    if (!draftLoaded) throw Error('工作底稿仍在读取。');
    setLocalBusy(true); setDraftError('');
    try {
      const value = await mutate('/workspace-draft', { text: draft, entry_mode: mode }) as DraftResponse & { project?: Project };
      setSavedDraft(draft); setSavedMode(mode); setDraftRevision(value.revision ?? project.revision); setDraftCommitted(true);
      return value;
    } catch (e) { setDraftError((e as Error).message); throw e; }
    finally { setLocalBusy(false); }
  }
  const markDraftSaved=useDirty(id + '-connected-draft', draftDirty, '共享工作底稿', async () => { await saveDraft(); }, () => { setDraft(savedDraft); setMode(savedMode); });
  async function ensureSharedDraft(): Promise<Project> { if (!draftCommitted || draftDirty) return (await saveDraft()).project || project; return project; }

  async function saveContext() {
    if (!context) return;
    if (termsDraft.some(t => !String(t.term || '').trim() || !String(t.meaning || '').trim())) { setContextError('请填完术语和解释，或删除空白行。'); throw Error('请填完术语和解释，或删除空白行。'); }
    setLocalBusy(true); setContextError('');
    try {
      const value = await mutate('/domain-context', { background: contextDraft, terms: termsDraft, selected_source_ids: contextSelection }) as ContextResponse;
      setContext(value); setContextDraft(value.background || ''); setTermsDraft(value.terms || []); setContextSelection(value.selected_source_ids);
    } catch (e) { setContextError((e as Error).message); throw e; }
    finally { setLocalBusy(false); }
  }
  const markContextSaved=useDirty(id + '-connected-context', contextDirty, '项目背景与本次来源', saveContext, () => { setContextDraft(context?.background || ''); setContextSelection(context?.selected_source_ids || null); setTermsDraft(context?.terms || []); });
  async function prepareRefinement(manual=false,retry=false,restoring=false) {
    if(!draftLoaded){setNavigationError(draftError||'工作底稿仍在读取，请稍候再进入完善需求。');return;}
    if(preparingNext.current||(!manual&&preparing)||busy||localBusy)return;
    setNavigationError('');setEntryError('');
    if(hasPendingInterviewInput(id,mode==='chat')){
      setEntryError('聊天框还有未发送的文字或附件。请先发送，或移除本轮输入，再进入完善需求。');
      requestAnimationFrame(()=>containerRef.current?.querySelector('.connected-entry-error')?.scrollIntoView({block:'nearest'}));
      return;
    }
    preparingNext.current=true;
    try{
      // Reloads consume already saved sources; they must not save an empty or
      // not-yet-loaded information draft over the existing shared draft.
      if(!restoring){
        // Opening an existing table must not create an untouched information
        // draft and invalidate the very review the user is trying to read.
        if(!manual||draftDirty){await ensureSharedDraft();markDraftSaved();}
        if(contextDirty){await saveContext();markContextSaved();}
      }
      navigate(()=>{
        onMainNavigation?.();restoreRefinement.current=false;manualRefinement.current=manual;
        session.setItem('ra-refinement-entry-'+id,manual?'manual':'auto');
        if(manual){
          onCancelPreparation();setRefinementGate(false);setStep('refine');session.setItem('ra-connected-step-'+id,'refine');
          // Read current content/tasks even if a previous task submission lost
          // its response. Refreshing discovers that task without resubmitting it.
          onRefresh();
        }
        else {setRefinementGate(step==='refine');void onPrepareRefinement(retry).catch(e=>setNavigationError((e as Error).message));}
      });
    }catch(e){setNavigationError((e as Error).message);}
    finally{preparingNext.current=false;}
  }
  useEffect(()=>{
    if(!active||!restoreRefinement.current||!draftLoaded||busy||localBusy||preparing||actionBlockReason)return;
    // Restoring a view never starts or resumes model work. Generation remains
    // an explicit action, including when the saved inputs have changed.
    restoreRefinement.current=false;
    void prepareRefinement(true,false,true);
  },[active,draftLoaded,busy,localBusy,preparing,actionBlockReason,root]);
  function chooseStep(next: Step) {
    setNavigationError('');
    if(next==='refine'){
      if(next===step&&manualRefinement.current)return;
      // Opening an existing table is navigation, independent of input freshness,
      // missing fields, previous failures or the last AI action on this project.
      void prepareRefinement(true);return;
    }
    // While preparation is running, explicitly choosing the information step
    // cancels only its automatic navigation, even if that step is already open.
    if(next===step&&!(next==='information'&&preparing))return;
    navigate(()=>{restoreRefinement.current=false;setRefinementGate(false);onCancelPreparation();setStep(next);session.setItem('ra-connected-step-'+id,next);onMainNavigation?.();});
  }
  function resolveHere(action: BlockerAction, row: Blocker) {
    setNavigationError('');
    if (action.kind === 'source') { onSources(); return; }
    if (step === 'delivery') { onResolveBlocker(action, row); return; }
    let target: Omit<ConnectedDestination,'nonce'> | null;
    try { target = refinementDestination(project, action); }
    catch (e) { setNavigationError((e as Error).message); return; }
    if (target) {
      const destination = target;
      navigate(() => {
        setStep(destination.step); session.setItem('ra-connected-step-' + id, destination.step); onMainNavigation?.();
        if(destination.step==='refine')setRefineView(destination.prewrite?'prewrite':'requirements');
        if(destination.step==='refine'&&!destination.prewrite)setTableFocus(previous=>({itemId:singleRequirementRoot(project,{itemId:action.item_id}),questionId:action.question_id,field:action.field,scope:action.kind==='scope',nonce:(previous?.nonce||0)+1}));
        if (action.kind === 'candidate') onCreateRequirement();
        
      });
      return;
    }
    onResolveBlocker(action, row);
  }
  function resolveTable(action:BlockerAction,row:Blocker){
    const reqId=singleRequirementRoot(project,{itemId:action.item_id,questionId:action.question_id});
    if(reqId){navigate(()=>{setTableFocus(previous=>({itemId:reqId,questionId:action.question_id,nonce:(previous?.nonce||0)+1}));});return;}
    if(action.kind==='source'){onSources();return;}
    resolveHere(action,row);
  }
  function reviewTableQuestion(questionId:string){setTableFocus(previous=>({questionId,nonce:(previous?.nonce||0)+1}));}
  async function loadDiscussion() {
    setLocalBusy(true); setDiscussionError('');
    try { setDiscussion(await api<DiscussionDraft>(root + '/discussion-draft')); }
    catch (e) { setDiscussionError((e as Error).message); }
    finally { setLocalBusy(false); }
  }
  function downloadDiscussion() {
    if (!discussion) return;
    const link = document.createElement('a');
    const url = URL.createObjectURL(new Blob([discussion.text], { type: 'text/markdown;charset=utf-8' }));
    link.href = url; link.download = discussion.filename || '讨论稿.md'; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  const saving = busy || localBusy || pendingActivity;
  const blockers = deliveryBlockers ?? project.confirmation_blockers;
  return <div ref={node => { containerRef.current = node; if (typeof workspaceRef === 'function') workspaceRef(node); else if (workspaceRef) workspaceRef.current = node; }} onScroll={e => { if (active) scrollPositions.current[scrollKey] = e.currentTarget.scrollTop; }} className="connected-workspace" data-step={step}>
    {draftError&&<p role="alert" className="connected-error">{draftError}{!draftLoaded&&<button type="button" onClick={()=>setDraftReadAttempt(value=>value+1)}>重新读取工作底稿</button>}</p>}
    {navigationError && <p role="alert" className="connected-error">{navigationError}<button type="button" onClick={() => {
      setNavigationError('');
      if(step==='refine'){const target=containerRef.current?.querySelector<HTMLElement>('.sheet-pending-jump');if(target)target.click();else containerRef.current?.querySelector<HTMLElement>('.refinement-sheet-footer .primary')?.focus();}
      else onRefresh();
    }}>{step==='refine'?'返回表格核对':'刷新处理入口'}</button></p>}
    <div className="workspace-stagebar">
    <nav className="connected-steps" aria-label="需求工作流程">
      {([['information', '1', '提供信息'], ['refine', '2', '完善需求'], ['delivery', '3', '确认交付']] as const).map(([key, number, label]) =>
        <button key={key} type="button" disabled={step==='information'&&key==='refine'&&!draftLoaded} aria-current={step === key ? 'step' : undefined} onClick={() => {
          if(key==='delivery'&&step==='refine'&&!(refinementReturn?.projectId===project.id&&refinementReturn.revision===project.revision&&refinementReturn.canReturn)){
            setNavigationError('');setRefineView('requirements');setTableValidationRequest(value=>(value||0)+1);return;
          }
          chooseStep(key);
        }}><span>{number}</span>{label}</button>)}
    </nav>
    {latestTask && <div className="connected-task" data-state={latestTask.status} role="status"><span title={latestTask.label+' · '+(taskStatusText[latestTask.status]||'状态待核对')+(latestTask.error?' · '+humanError(latestTask.error):'')}><b>{latestTask.label}</b> · {taskStatusText[latestTask.status] || '状态待核对'}{latestTask.error ? ' · ' + humanError(latestTask.error) : ''}</span><button type="button" aria-expanded={taskOpen} onClick={() => setTaskOpen(value => !value)}>{taskOpen ? '收起任务记录' : '查看任务与恢复'}</button><button type="button" onClick={onRefresh}>刷新</button></div>}
    </div>
    {entryError&&<p role="alert" className="connected-error connected-entry-error"><span>{entryError}</span><button type="button" onClick={()=>{chooseStep('information');setMode('chat');requestAnimationFrame(()=>{const input=containerRef.current?.querySelector<HTMLTextAreaElement>('.intake-chat textarea');input?.focus();input?.scrollIntoView({block:'center'});});}}>返回聊天输入</button></p>}
    {(step==='information'||step==='refine')&&(preparation.state!=='idle'||refinementGate)&&<section className="connected-preparation" aria-label="进入完善需求的准备状态" role={preparation.state==='failed'?'alert':'status'}>
      <strong>{preparation.state==='failed'?'整理尚未完成':preparation.state==='authorization'?'确认本次资料使用范围':'正在整理，完成后自动进入完善需求'}</strong>
      <p>{preparation.message||actionBlockReason||'正在检查已保存草稿；缺少时先根据聊天和资料起草摘要、需求说明和验收建议，你在本表核对即可。'}</p>
      {preparation.state==='failed'&&<button type="button" className="primary" disabled={saving||running} onClick={()=>void prepareRefinement(false,true)}>重试整理</button>}
      {refinementGate&&preparation.state==='idle'&&<button type="button" disabled={saving||!draftLoaded} onClick={()=>void prepareRefinement()}>继续检查并整理</button>}
      {refinementGate&&<button type="button" disabled={saving||running||preparing||!draftLoaded} onClick={()=>void prepareRefinement(true)}>不使用 AI，直接完善</button>}
    </section>}
    {(project as Obj).decision_review_status?.status === 'pending_manual_review' && <p className="connected-review-notice" role="status">有效草稿已保留；问题拆分与回答覆盖仍待人工核对。{humanError((project as Obj).decision_review_status.message)}</p>}
    {taskOpen && latestTask && <section className="connected-task-details" aria-label="当前任务记录">{taskPanel?.(latestTask)}</section>}
    {step === 'information' && <div className="connected-grid assistant-grid" data-context-open={assistantContextOpen}>
      <main className="connected-card">
        <h2>从已知信息开始</h2>
        <p className="assistant-intro">提供信息后，点击“整理并进入完善需求”。助手先根据聊天和资料起草整张需求确认表，包含验收建议；你再确认或修改。</p>
        {hasExistingRequirements&&<div className="assistant-resume"><div><strong>已有需求草稿，可以直接继续</strong><p>{project.prewrite_review?.blocking_count?project.prewrite_review.blocking_count+' 项待确认。':'打开已有内容核对或修改。'}无需重新整理，打开不调用 AI。</p></div><button type="button" className="primary" disabled={saving||preparing||!draftLoaded} onClick={()=>{setRefineView(project.prewrite_review?.blocking_count||project.prewrite_review?.status==='passed'&&project.prewrite_review.current?'prewrite':'requirements');void prepareRefinement(true,false,true);}}>继续核对已有草稿</button></div>}
        <div className="connected-mode" role="group" aria-label="输入方式"><button type="button" aria-pressed={mode === 'chat'} onClick={event => {
          modeTouched.current=true;
          setMode('chat');
          const card=event.currentTarget.closest('.connected-card');
          requestAnimationFrame(()=>card?.querySelector('.chat-composer-actions')?.scrollIntoView({block:'nearest'}));
        }}>和助手聊需求</button><button type="button" aria-pressed={mode === 'direct'} onClick={() => {modeTouched.current=true;const pending=session.getItem('ra-chat-draft-'+id)||'';if(!draft.trim()&&pending.trim()){draftTouched.current=true;setDraft(pending);}setMode('direct');}}>直接填写需求</button>{!hasExistingRequirements&&<details className="entry-alternative"><summary>其他方式</summary><button type="button" disabled={saving||preparing||!draftLoaded} onClick={()=>void prepareRefinement(true)}>不使用 AI，直接完善</button></details>}<button type="button" className="assistant-context-toggle" aria-expanded={assistantContextOpen} aria-controls={'assistant-context-'+id} onClick={()=>setAssistantContextOpen(open=>!open)}>项目语境与资料设置</button></div>
        {mode === 'chat' && <div className="connected-chat">{chatPanel(ensureSharedDraft, text => { modeTouched.current=true;setMode('direct'); if (text.trim()){draftTouched.current=true;setDraft(text);} }, () => chooseStep('refine'), () => {void prepareRefinement();})}</div>}
        <div hidden={mode !== 'direct'}>
        <label className="connected-label" htmlFor={'connected-draft-' + id}>填写当前需求的原文底稿</label>
        <textarea id={'connected-draft-' + id} rows={12} value={draft} disabled={!draftLoaded||preparing} onChange={e => {draftTouched.current=true;setDraft(e.target.value);}} placeholder={draftLoaded ? '填写已确认信息；未知处可以明确写“待确定”' : '正在读取底稿…'} />
        <div className="connected-actions"><button className={hasExistingRequirements?'secondary':'primary'} type="button" disabled={saving||preparing||!draftLoaded} onClick={()=>void prepareRefinement()}>{preparing?'正在整理…':hasExistingRequirements?'让 AI 更新草稿':'整理并进入完善需求'}</button><button type="button" disabled={saving||preparing||!draftLoaded||(!draftDirty&&draftCommitted)} onClick={() => void saveDraft().catch(() => {})}>保存工作底稿</button><button type="button" onClick={() => onSources()}>查看与补充资料</button></div>
        {draftDirty && <p className="connected-status">当前编辑尚未保存。保存只写入底稿，不调用模型。</p>}
        {!draftDirty && draftLoaded && draftCommitted && <p className="connected-status">底稿已保存；候选建议仍须逐条审核。</p>}
        {!draftDirty && draftLoaded && !draftCommitted && <p className="connected-status">当前文字尚未保存为共享工作底稿。</p>}
        </div>
      </main>
      <aside className="connected-side assistant-context" id={'assistant-context-'+id}>
        <button type="button" className="assistant-context-close" onClick={()=>setAssistantContextOpen(false)}>收起项目语境与资料设置</button>
        <section className="connected-card"><h3>项目语境与本次来源</h3><p>背景只帮助理解，不作为当前需求的业务依据。</p>{contextError && <p role="alert" className="connected-error">{contextError}</p>}
          {context && <><label className="connected-label" htmlFor={'domain-background-' + id}>产品线简短背景</label><textarea id={'domain-background-' + id} rows={4} value={contextDraft} onChange={e => setContextDraft(e.target.value)} placeholder={context.suggested_background || '可选'} />
          <details><summary>选择本次供模型参考的项目资料</summary><p>默认使用全部未排除参考资料；已写进当前输入的来源仍按原文绑定，停用来源始终不发送。</p><label><input type="checkbox" checked={contextSelection === null} onChange={e => setContextSelection(e.target.checked ? null : context.effective_source_ids)} /> 使用全部未排除参考资料</label>{contextSelection !== null && project.sources.filter(s => !s.excluded).map(s => <label key={s.id}><input type="checkbox" checked={contextSelection.includes(s.id)} onChange={e => setContextSelection(e.target.checked ? [...contextSelection, s.id] : contextSelection.filter(x => x !== s.id))} /> {s.title || s.id}</label>)}{Boolean(context.bound_input_source_ids?.length) && <p>当前输入已绑定来源：{context.bound_input_source_ids?.join('、')}</p>}{context.unavailable_selected_source_ids?.length > 0 && <p className="connected-error">已有选择不可用：{context.unavailable_selected_source_ids.join('、')}</p>}</details>
          <details><summary>按需维护术语 · {termsDraft.length}</summary>{termsDraft.map((t, n) => <div key={t.id || n} className="connected-term"><input aria-label={'术语 ' + (n + 1)} value={t.term || ''} onChange={e => setTermsDraft(current => current.map((x, k) => k === n ? { ...x, term: e.target.value } : x))} placeholder="术语" /><input aria-label={'解释 ' + (n + 1)} value={t.meaning || ''} onChange={e => setTermsDraft(current => current.map((x, k) => k === n ? { ...x, meaning: e.target.value } : x))} placeholder="本项目中的含义" /><select aria-label={'术语状态 ' + (n + 1)} value={t.status || 'reference'} onChange={e => setTermsDraft(current => current.map((x, k) => k === n ? { ...x, status: e.target.value } : x))}><option value="reference">仅供参考</option><option value="confirmed">项目已确认</option></select><button type="button" onClick={() => setTermsDraft(current => current.filter((_, k) => k !== n))}>删除</button></div>)}<button type="button" onClick={() => setTermsDraft(current => [...current, { term: '', meaning: '', status: 'reference' }])}>添加术语</button></details>
          <button type="button" disabled={saving || !contextDirty} onClick={() => void saveContext().catch(() => {})}>保存项目语境</button></>}
        </section>
        <section className="connected-card assistant-state"><h3>当前进度</h3><p>已有 {candidates.length} 条条目 · {questions.length} 个问题。</p><p>打开草稿后集中核对，缺少的内容会直接标出。</p></section>
      </aside>
    </div>}
    {step === 'refine' && !refinementGate && <>
      <div className="refinement-view-toolbar">
      <div className="refinement-view-switch" role="group" aria-label="完善需求视图"><button type="button" disabled={saving} aria-pressed={refineView==='requirements'} onClick={()=>navigate(()=>setRefineView('requirements'))}>需求列表</button><button type="button" disabled={saving} aria-pressed={refineView==='prewrite'} onClick={()=>navigate(()=>setRefineView('prewrite'))}>成文检查{project.prewrite_review?.blocking_count?' · '+project.prewrite_review.blocking_count:''}</button><small>{prewriteLabel(project.prewrite_review)}</small></div>
      {manualRefinement.current&&refineView==='requirements'&&<p className="connected-status">查看与确认不调用 AI</p>}
      </div>
      <div className="refinement-requirements-view" hidden={refineView!=='requirements'}>
      <RequirementTable project={project} active={active&&refineView==='requirements'} busy={saving||running} mutate={mutate} onRefresh={onRefresh} onSources={onSources}
        onRecommend={onRecommendRequirement} onSuggest={onOrganize} onCreate={onCreateRequirement} onResolve={resolveTable} onQuestionReview={reviewTableQuestion} onStageConfirmed={()=>{onRefinementConfirmed?.();chooseStep('delivery');}} onReturnAvailabilityChange={setRefinementReturn} focus={tableFocus} validationRequest={tableValidationRequest}/>
      </div>
      {refineView==='prewrite'&&<React.Suspense fallback={<p role="status">正在打开成文检查…</p>}><PrewriteReviewPanel draftTask={tasks.filter(t=>t.action==='organize'&&t.retry_input?.draft_request?.mode==='prewrite').at(-1)} project={project} busy={saving} running={running} blockReason={actionBlockReason} mutate={mutate} onRefresh={onRefresh} onRecheck={onPrewrite} onGenerate={onGeneratePrd} onDraftAll={onOrganize} onClarify={questionId=>onClarify(questionId)} onSources={onSources} focusFindingId={destination?.findingId} onRequirement={itemId=>navigate(()=>{setRefineView('requirements');if(itemId)setTableFocus(previous=>({itemId:singleRequirementRoot(project,{itemId}),nonce:(previous?.nonce||0)+1}));})}/></React.Suspense>}
    </>}
    {step === 'delivery' && <div className="connected-grid">
      <main className="connected-card">
        <h2>确认交付版本</h2>
        <p>可先查看当前原文和未决项的讨论稿；正式交付由服务端检查来源、问题、条款、文档和确认条件。</p>
        <div className="connected-actions"><button type="button" disabled={saving} onClick={() => void loadDiscussion()}>预览当前讨论稿</button><button type="button" disabled={!discussion || discussion.revision !== project.revision} onClick={downloadDiscussion}>下载讨论稿</button><button type="button" onClick={onDocuments}>查看与核对正式文档</button><button className="primary" type="button" onClick={onDelivery}>打开正式确认</button></div>
        {discussionError && <p role="alert" className="connected-error">讨论稿未能读取：{discussionError}</p>}
        {discussion && <section className="connected-discussion"><h3>非正式讨论稿 · 项目 v{discussion.revision}</h3>{discussion.revision !== project.revision && <p className="connected-error">项目内容已变化，请重新预览后下载。</p>}<pre>{discussion.text}</pre></section>}
        {Boolean(blockers?.length || project.confirmation_issues?.length) && <div className="connected-blockers"><h3>当前交付阻塞</h3>
          {blockers?.length ? blockers.map(publicBlocker).map(row => <div key={row.key} className="connected-blocker-row"><b>{row.title}</b><p>{row.detail || row.message}</p><div className="connected-actions">{row.actions.map((action, n) => <button type="button" key={n} onClick={() => resolveHere(action, row)}>{action.label || '去处理'} →</button>)}{!row.actions.length && <button type="button" onClick={onRefresh}>刷新处理入口</button>}</div></div>) : project.confirmation_issues.map((issue, n) => <p key={n}>{issue}</p>)}
        </div>}
      </main>
      <aside className="connected-side"><section className="connected-card"><h3>任务状态</h3>{latestTask ? <><p>{latestTask.label} · {taskStatusText[latestTask.status] || '状态待核对'}</p>{latestTask.error && <p className="connected-error">{humanError(latestTask.error)}</p>}<button type="button" onClick={() => { setTaskOpen(true); requestAnimationFrame(() => document.querySelector('.project-workspace:not([hidden]) .connected-task-details')?.scrollIntoView({ block: 'start' })); }}>查看任务与恢复操作</button></> : <p>暂无生成任务。</p>}</section></aside>
    </div>}
  </div>;
}
