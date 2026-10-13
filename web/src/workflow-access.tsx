import { publicStep } from './workflow-labels';
import React from 'react';
import type { Project, UserTask, JsonObject } from './api';
import { phases } from './guided';
import { Icon } from './ui-icons';

export const workflowPhases = [0, 1, 2, 4, 5];
export const displayStep = (phase: number) => workflowPhases.indexOf(phase) + 1;

/** A new manual draft can start before an interview. An invalidated input cannot. */
export function canStartDirect(p: Project) {
  const input = p.product_flow?.[0];
  return !!input && !input.complete && !input.needs_recheck && !(p as JsonObject).intake;
}

/** Manual entry only opens the scope editor; later stages still require saved checkpoints. */
export function stageAccess(p: Project, phase: number) {
  if (phase === 0) return { allowed: true, prerequisite: 0, reason: '' };
  // These are editors and previews. The API still checks every formal action.
  if ((p as JsonObject).workflow_mode === 'shared-draft') return { allowed: true, prerequisite: 0, reason: '' };
  const prior = workflowPhases.filter(n => n < phase);
  const prerequisite = prior.find(n => !p.product_flow?.[n]?.complete);
  const known = !!p.product_flow?.[phase];
  const direct = canStartDirect(p);
  const allowed = known && ((phase === 1 && direct) || (p.product_flow![phase].available && prerequisite === undefined));
  const required = prerequisite === 0 && direct ? 1 : prerequisite ?? 0;
  return { allowed, prerequisite: required, reason: allowed ? '' : !known
    ? '阶段状态尚未取得，请刷新后继续。'
    : `请先完成第 ${displayStep(required)} 步「${phases[required]}」，再处理本步。` };
}

export function isScopeQuestion(q: JsonObject) {
  return !!q.blocking && Number(q.blocking_stage ?? 3) <= 2;
}
export function questionPhase(q: JsonObject) { return isScopeQuestion(q) ? 1 : 2; }

export function intakeStatusLabel(p: Project, task?: UserTask) {
  if (!(p as JsonObject).intake && !p.product_flow?.[0]?.complete) return '';
  if ((p as JsonObject).intake?.mode === 'direct') return '输入已保存 · 直接核对草稿';
  if (task?.local_recovery) return '输入已保存 · 已恢复草稿待核对';
  if (task && ['queued', 'running'].includes(task.status)) return '输入已保存 · AI 整理中';
  if (task && ['failed', 'cancelled', 'paused_budget'].includes(task.status)) return '输入已保存 · 本次整理未完成';
  return p.messages.some(m => m.stage === 'ingest' && m.role === 'assistant')
    ? '输入已保存 · 草稿待核对' : '输入已保存 · 可整理草稿';
}

export function StageNavigation({p, phase, busy, intakeLabel, onGo}: {
  p: Project; phase: number; busy: boolean; intakeLabel: string; onGo: (n: number) => void;
}) {
  return <nav className="phase-nav" aria-label="需求工作阶段">{workflowPhases.map(n => {
    const st = p.product_flow?.[n], access = stageAccess(p, n);
    const state = !access.allowed ? 'blocked' : st?.needs_recheck ? 'recheck' : st?.complete ? 'complete' : phase === n ? 'current' : 'pending';
    return <button key={n} data-state={state} disabled={busy || !access.allowed}
      aria-current={phase === n && access.allowed ? 'step' : undefined} title={!access.allowed ? access.reason : undefined} onClick={() => onGo(n)}>
      <span className="step-number">{state === 'complete' && phase !== n ? <Icon name="check"/> : displayStep(n)}</span>
      <b>{!access.allowed && <Icon name="lock"/>}{phases[n]}</b>
      <small>{!access.allowed ? `先完成第 ${displayStep(access.prerequisite)} 步` : n === 0 && st?.complete ? intakeLabel : n === 0 && canStartDirect(p) ? '可选 · 可跳过对话' : state === 'recheck' ? '需重新核对' : state === 'complete' ? (phase === n ? '已核对 · 当前查看' : '已完成') : phase === n ? '当前进行中' : n === 1 && canStartDirect(p) ? '可直接填写' : '可以开始'}</small>
    </button>;
  })}</nav>;
}

export function StageLock({p, phase, busy, onGo, onRefresh}: {
  p: Project; phase: number; busy: boolean; onGo: (n: number) => void; onRefresh: () => void;
}) {
  const access = stageAccess(p, phase);
  if (access.allowed) return null;
  return <section className="stage-lock" aria-label="本阶段未解锁" role="status">
    <Icon name="lock"/><h2>{publicStep(phase+1).title}尚有前置内容待核对</h2><p>请先核对“{publicStep(access.prerequisite+1).title}”中的待处理内容。</p>
    <p>本页编辑和提交已锁定。已有内容及未保存草稿保留，完成前置核对后继续处理。</p>
    <button type="button" className="primary" disabled={busy} onClick={() => onGo(access.prerequisite)}>前往{publicStep(access.prerequisite+1).title} →</button>
    {!p.product_flow?.[phase] && <button type="button" disabled={busy} onClick={onRefresh}>刷新阶段状态</button>}
  </section>;
}
