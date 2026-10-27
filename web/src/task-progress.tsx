import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import type { UserTask } from './api';
import { dismissProgress, emptyProgress, ProgressEntry, ProgressSnapshot, reconcileProgress,
  taskCurrentStage, taskElapsed, taskGenerated, taskNeedsAttention, taskRunning, taskStageLabels, taskStateLabels,inlinePrewriteFailure } from './task-progress-state';
import './task-progress.css';
import { TaskFailureNotice, taskFailure, canRecoverDraft } from './task-failure';

export interface ProgressControls {
  cancel: (task: UserTask) => Promise<void>;
  refresh: () => Promise<void>;
  open: (task: UserTask, retry: boolean, afterOpen: () => void) => void;
}
type ControlRef = React.MutableRefObject<ProgressControls>;
type ProgressBridge = {
  publish: (snapshot: ProgressSnapshot, submittedId?: string) => void;
  register: (projectId: string, controls: ControlRef) => () => void;
};
const noopBridge: ProgressBridge = { publish: () => {}, register: () => () => {} };
const ProgressContext = createContext<ProgressBridge>(noopBridge);
const ProgressPresentContext = createContext(false);
export const useTaskProgress = () => useContext(ProgressContext);
export const useProgressPresent = () => useContext(ProgressPresentContext);

export function ProgressContents({ entry, now }: { entry: ProgressEntry; now: number }) {
  const { task, uncertain } = entry;
  return <>
    <p className="ai-progress-project">{entry.projectName}</p>
    <h3>{task.label}</h3>
    <p className="ai-progress-stage" data-status={task.status} role="status" aria-live="polite">
      {taskRunning(task) && <span className="feedback-spinner" aria-hidden="true" />}
      {uncertain ? '最新状态暂未取得' : taskCurrentStage(task)}
    </p>
    <p className="ai-progress-time" aria-live="off">{taskRunning(task) ? '已用时' : '本次耗时'}：{taskElapsed(task, now)}</p>
    {uncertain && <p className="notice">下方保留上次取得的进度。请刷新状态，避免重复发起任务。</p>}
    {task.local_recovery ? <p>已从原输出恢复 {task.local_recovery.candidate_ids.length} 条候选，请核对并决定是否采纳。原失败记录保留。</p> : taskFailure(task) ? <TaskFailureNotice task={task}/> : <p className={taskNeedsAttention(task) ? 'error' : 'preserve-lines'}>{task.message || '任务已提交，正在等待最新状态。'}</p>}
    {task.stages.length > 0 && <ol className="ai-progress-steps" aria-label="本次任务步骤">{task.stages.map((stage, index) => {
      const state = task.step_states?.[index]?.stage === stage ? task.step_states[index].status : 'NOT_RUN';
      const text = state === 'NOT_RUN' ? '未执行' : taskStateLabels[state] || '状态待核对';
      return <li key={stage + index} data-state={state} aria-current={state === 'running' ? 'step' : undefined}>
        <span className="ai-progress-step-number" aria-hidden="true">{index + 1}</span>
        <span>{taskStageLabels[stage] || stage}</span><small>{text}</small>
      </li>;
    })}</ol>}
    {taskRunning(task) && <p className="muted">可以收起面板并继续查看资料；模型生成时间不代表完成百分比。</p>}
    {!task.local_recovery&&['failed', 'paused_budget'].includes(task.status) && <p className="muted">输入与已保存资料保留。重新运行需核对原任务范围，并可能产生新的模型费用。</p>}
    {task.status === 'cancelled' && <p className="notice">已停止后续步骤；已发送的请求可能仍在结束处理中并计费。</p>}
  </>;
}

export function TaskProgressProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState(emptyProgress), [now, setNow] = useState(Date.now);
  const [operation, setOperation] = useState(''), [operationError, setOperationError] = useState('');
  const controls = useRef(new Map<string, ControlRef>()), operationLock = useRef(false);
  const heading = useRef<HTMLHeadingElement>(null), compactButton = useRef<HTMLButtonElement>(null);
  const focusRequested = useRef(false);
  const publish = useCallback((snapshot: ProgressSnapshot, submittedId?: string) => {
    setState(current => reconcileProgress(current, snapshot, submittedId));
  }, []);
  const register = useCallback((projectId: string, ref: ControlRef) => {
    controls.current.set(projectId, ref);
    return () => { if (controls.current.get(projectId) === ref) controls.current.delete(projectId); };
  }, []);
  const bridge = useMemo(() => ({ publish, register }), [publish, register]);
  const entry = state.entries.find(row => row.task.id === state.selected) || state.entries.at(-1);
  const anyRunning = state.entries.some(row => taskRunning(row.task));
  useEffect(() => { if (!anyRunning) return; const timer = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(timer); }, [anyRunning]);
  useEffect(() => { setOperationError(''); }, [entry?.task.id]);
  useEffect(() => {
    if (!focusRequested.current) return;
    focusRequested.current = false;
    (state.expanded ? heading.current : compactButton.current)?.focus();
  }, [state.expanded, state.selected]);
  function expand(taskId: string) { focusRequested.current = true; setState(s => ({ ...s, selected: taskId, expanded: true })); }
  function collapse() { focusRequested.current = true; setState(s => ({ ...s, expanded: false })); }
  async function execute(kind: 'refresh' | 'cancel') {
    if (!entry || operationLock.current) return;
    const target = controls.current.get(entry.projectId)?.current;
    if (!target) { setOperationError('项目连接尚未就绪，请重新进入项目后查看状态。'); return; }
    operationLock.current = true; setOperation(kind); setOperationError('');
    try { if (kind === 'cancel') await target.cancel(entry.task); else await target.refresh(); }
    catch (error) { setOperationError((error as Error).message); }
    finally { operationLock.current = false; setOperation(''); }
  }
  function openResult(retry = false) {
    if (!entry) return;
    const target = controls.current.get(entry.projectId)?.current;
    if (!target) { setOperationError('请先进入对应项目。'); return; }
    target.open(entry.task, retry, () => setState(s => ({ ...s, expanded: false })));
  }
  const inlineDone=entry?.inlineDraft&&!state.expanded&&(
    entry.task.retry_input?.draft_request&&taskGenerated(entry.task)||
    entry.task.status==='awaiting_user'&&['prewrite','document'].includes(entry.task.action)||inlinePrewriteFailure(entry.task));
  return <ProgressContext.Provider value={bridge}><ProgressPresentContext.Provider value={Boolean(entry)&&!inlineDone}>{children}{entry && !inlineDone && <aside className={'ai-progress' + (state.expanded ? ' ai-progress-expanded' : '')} aria-label="AI 任务进度" onKeyDown={event => { if (state.expanded && event.key === 'Escape') { event.stopPropagation(); collapse(); } }}>
    {state.expanded ? <>
      <header className="ai-progress-header"><h2 ref={heading} tabIndex={-1}>AI 任务进度</h2><button type="button" onClick={collapse}>收起进度</button></header>
      <div className="ai-progress-body">
        {state.entries.length > 1 && <label>查看任务<select value={entry.task.id} onChange={event => setState(s => ({ ...s, selected: event.target.value }))}>{state.entries.map(row => <option key={row.task.id} value={row.task.id}>{row.projectName} · {taskStateLabels[row.task.status] || row.task.status}</option>)}</select></label>}
        <ProgressContents entry={entry} now={now} />
        {operationError && <p className="error" role="alert">{operationError}</p>}
      </div>
      <footer className="ai-progress-actions">
        {taskRunning(entry.task) ? <><button type="button" disabled={Boolean(operation) || entry.busy || entry.uncertain} onClick={() => void execute('cancel')}>{operation === 'cancel' ? '正在停止后续步骤…' : '停止后续步骤'}</button><small>已发送请求可能继续并计费。</small></> : <>
          <button type="button" className="primary" disabled={Boolean(operation) || entry.busy || entry.uncertain} onClick={() => openResult(false)}>{canRecoverDraft(entry.task) ? '恢复草稿（不调用 AI）' : taskGenerated(entry.task) ? '查看结果并核对' : entry.task.action==='organize'&&!entry.task.retry_input?.target&&taskFailure(entry.task) ? '前往需求草稿' : '返回任务处理'}</button>
          {!entry.task.local_recovery&&['failed', 'paused_budget', 'cancelled'].includes(entry.task.status) && <button type="button" disabled={Boolean(operation) || entry.busy || entry.uncertain || !entry.task.retry_input} onClick={() => openResult(true)}>核对后重试</button>}
          <button type="button" onClick={() => setState(s => dismissProgress(s, entry.task.id))}>关闭进度提示</button>
        </>}
        {entry.uncertain && <button type="button" disabled={Boolean(operation)} onClick={() => void execute('refresh')}>{operation === 'refresh' ? '正在刷新…' : '刷新任务状态'}</button>}
      </footer>
    </> : <button ref={compactButton} type="button" className="ai-progress-compact" aria-expanded="false" onClick={() => expand(entry.task.id)}>
      <span aria-hidden="true" className={taskRunning(entry.task) ? 'feedback-spinner' : 'ai-progress-dot'} />
      <span><strong>{entry.uncertain ? 'AI 状态待刷新' : taskRunning(entry.task) ? 'AI 正在处理' : taskGenerated(entry.task) ? 'AI 结果待核对' : 'AI 任务需查看'}{state.entries.length > 1 ? ` · ${state.entries.length} 个任务` : ''}</strong><small>{entry.projectName} · {entry.uncertain ? '上次记录' : taskCurrentStage(entry.task)}</small></span><span>展开</span>
    </button>}
  </aside>}</ProgressPresentContext.Provider></ProgressContext.Provider>;
}
