import type { UserTask } from './api';

export const taskStateLabels: Record<string, string> = {
  queued: '等待开始', running: '正在处理', succeeded: '已生成，待核对',
  partial: '已处理可用内容，仍有限制', awaiting_user: '等待你的决定',
  failed: '本次未完成，已有内容保留', cancelled: '已停止后续步骤', paused_budget: '已暂停，请核对后重新发起',
};
export const taskStageLabels: Record<string, string> = {
  interview: '聊清需求想法', vision: '分析待处理图片', ingest: '整理材料和目标',
  clarify: '更新需求理解', brainstorm: '比较可选方向', ui: '生成讨论原型',
  prd: '生成文档讨论稿', review: '审查当前内容', change: '分析修改建议',
};
export const taskRunning = (task: UserTask) => ['queued', 'running'].includes(task.status);
export const taskGenerated = (task: UserTask) => Boolean(task.local_recovery)||['succeeded', 'partial'].includes(task.status);
export const taskNeedsAttention = (task: UserTask) => !task.local_recovery&&['failed', 'paused_budget', 'awaiting_user'].includes(task.status);
export const inlinePrewriteFailure = (task:UserTask) => task.status==='failed'&&task.action==='organize'&&task.retry_input?.draft_request?.mode==='prewrite';

export interface ProgressSnapshot {
  projectId: string; projectName: string; tasks: UserTask[]; busy: boolean; uncertain: boolean; inlineDraft?:boolean;
}
export interface ProgressEntry {
  projectId: string; projectName: string; task: UserTask; busy: boolean; uncertain: boolean; inlineDraft?:boolean;
}
export interface ProgressState { entries: ProgressEntry[]; selected: string; expanded: boolean; dismissed: string[] }
export const emptyProgress: ProgressState = { entries: [], selected: '', expanded: false, dismissed: [] };

// Track live/submitted tasks only: loading historical terminal records is not a new event.
export function reconcileProgress(state: ProgressState, snapshot: ProgressSnapshot, submittedId?: string): ProgressState {
  let entries = [...state.entries], selected = state.selected, expanded = state.expanded;
  for (let task of snapshot.tasks) {
    if (state.dismissed.includes(task.id)) continue;
    const previous = entries.find(entry => entry.task.id === task.id && entry.projectId === snapshot.projectId);
    if (!previous && !taskRunning(task) && task.id !== submittedId) continue;
    // A late poll cannot restart an already terminal task; retries have new IDs.
    if (previous && !taskRunning(previous.task) && taskRunning(task)) task = previous.task;
    const entry: ProgressEntry = { projectId: snapshot.projectId, projectName: snapshot.projectName,
      task, busy: snapshot.busy, uncertain: snapshot.uncertain,...(snapshot.inlineDraft!==undefined?{inlineDraft:snapshot.inlineDraft}:{}) };
    const inlineDecision=entry.inlineDraft&&(task.status==='awaiting_user'&&['prewrite','document'].includes(task.action)||inlinePrewriteFailure(task));
    if (!previous) {
      entries = entries.filter(row => row.projectId !== snapshot.projectId || taskRunning(row.task));
      entries.push(entry);
      selected = task.id;
      expanded = !taskGenerated(task)&&!inlineDecision;
    } else {
      entries = entries.map(row => row === previous ? entry : row);
      if (taskRunning(previous.task) && taskNeedsAttention(task)) { selected = task.id; expanded = true; }
      if (selected === task.id && taskRunning(previous.task) && taskGenerated(task)) expanded = false;
      if(selected===task.id&&inlineDecision&&(taskRunning(previous.task)||!previous.inlineDraft))expanded=false;
    }
  }
  return { entries, selected, expanded, dismissed: state.dismissed };
}

export function dismissProgress(state: ProgressState, taskId: string): ProgressState {
  const entry = state.entries.find(row => row.task.id === taskId);
  if (!entry || taskRunning(entry.task)) return state;
  const entries = state.entries.filter(row => row !== entry);
  return { entries, selected: state.selected === taskId ? entries.at(-1)?.task.id || '' : state.selected,
    expanded: state.selected === taskId ? false : state.expanded, dismissed: [...state.dismissed, taskId] };
}

export function taskElapsed(task: UserTask, now = Date.now()): string {
  const start = Date.parse(task.created || '');
  const end = taskRunning(task) ? Math.max(start, now) : Date.parse(task.completed || '');
  if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) return '耗时暂未取得';
  const seconds = Math.floor((end - start) / 1000);
  const minutes = Math.floor(seconds / 60), hours = Math.floor(minutes / 60);
  return hours ? `${hours} 小时 ${minutes % 60} 分 ${seconds % 60} 秒` : minutes ? `${minutes} 分 ${seconds % 60} 秒` : `${seconds} 秒`;
}

export function taskCurrentStage(task: UserTask): string {
  if (task.local_recovery) return '已恢复待核对草稿（未重新调用 AI）';
  if (task.status === 'queued') return '等待任务开始';
  const current = task.step_states?.find(step => step.status === 'running');
  if (taskRunning(task)) return current ? taskStageLabels[current.stage] || current.stage : '等待最新步骤状态';
  return taskStateLabels[task.status] || '任务状态待核对';
}
