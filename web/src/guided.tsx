import React, { useState } from 'react';
import { ActionInput, ActionPlan, Project, UserTask } from './api';
import { useDialog } from './shell';
type Obj = Record<string, any>;
export const phases = ['提供现状与诉求', '核对现状、价值与改动范围', '澄清流程与规则', '核对功能草图', '评审 MRD 与 PRD', '确认与交接'];
const states: Obj = { queued: '等待开始', running: '正在处理', succeeded: '已生成，待核对', partial: '已处理可用内容，仍有限制', awaiting_user: '等待你的决定', failed: '本次未完成，已有内容保留', cancelled: '已停止后续步骤', paused_budget: '已暂停，请核对后重新发起' };
const steps: Obj = { vision: '分析待处理图片', ingest: '整理材料和目标', clarify: '更新需求理解', brainstorm: '比较可选方向', ui: '生成讨论原型', prd: '生成文档讨论稿', review: '审查当前内容', change: '分析修改建议' };

export function TaskStatus({ task, onCancel, onRetry, onViewResult }: { task?: UserTask; onCancel: () => void; onRetry: () => void; onViewResult?: () => void }) {
  if (!task) return null;
  const generated=onViewResult && ['succeeded','partial'].includes(task.status);
  const running=['queued','running'].includes(task.status);
  const stopped=['failed','cancelled','paused_budget'].includes(task.status);
  const failedOutput=task.status==='failed'&&['SCHEMA_INVALID','REFERENCE_INVALID','SEMANTIC_BLOCKED'].includes(task.error||'');
  const failureExplanation=task.error==='SEMANTIC_BLOCKED'?'分析结果与来源或业务保真校验不一致，结果没有采纳。请核对下方原因，不要为继续流程补造答案。':'模型返回的内容未能整理成有效结果，资料和输入已保存。无需修改业务需求来解决格式问题；重新运行会产生新的模型请求，可能计费。';
  if (failedOutput) return <article className="task-status" role="status"><strong>{task.label} · 分析未成功</strong><p>{failureExplanation}</p><p className="preserve-lines">{task.message}</p><button onClick={onRetry}>核对后重试</button><details><summary>查看调用记录</summary><p>已执行 {task.calls} 次请求{task.max_calls!=null&&` / 历史任务上限 ${task.max_calls}`} · 费用 {task.cost ?? '未知'}</p><small>{task.id} · {task.run_ids.join('、')}</small></details></article>;
  return <article className="task-status" role="status" aria-live="polite">
    <strong>{task.label} · {states[task.status] || '请核对任务状态'}</strong>
    <p className="preserve-lines">{task.message || (running?'任务已开始，正在准备本次分析。':'请核对本次执行记录。')}</p>
    {running&&<><progress aria-label="分析步骤进度" max={Math.max(1,task.stages.length)} value={task.completed_steps}/><p>已完成 {task.completed_steps}/{task.stages.length} 步；当前步骤完成后更新进度。</p></>}
    {stopped&&<p>{task.calls===0?'本次尚未发出模型请求。':'本次已发出 '+task.calls+' 次模型请求。'}输入与已保存资料保留；核对原因后可重新发起。</p>}
    {generated&&<button onClick={onViewResult}>{task.action==='organize'?'查看分析结果':task.action==='document'?'查看讨论稿':task.action==='prototype'?'查看功能草图':'查看本步结果'}</button>}
    {running?<button onClick={onCancel}>停止后续步骤</button>:stopped&&<button onClick={onRetry}>重新核对后发起任务</button>}
    <details><summary>本次资料、限制与执行记录</summary><p>已执行 {task.calls} 次请求{task.max_calls!=null&&` / 历史任务上限 ${task.max_calls}`} · 已处理步骤 {task.completed_steps}/{task.stages.length} · 费用 {task.cost ?? '未知'}</p><p>资料：{task.source_ids.join('、') || '无资料文件'}</p><p>子步骤：{task.stages.map(s => steps[s]).join(' → ')}</p><small>{task.id} · {task.run_ids.join('、')}</small></details>
  </article>;
}

export function ActionDialog({ plan, input, error, busy, onClose, onSubmit }: { plan: ActionPlan; input: ActionInput; error: string; busy: boolean; onClose: () => void; onSubmit: () => void }) {
  useDialog(true, onClose);
  const authorize = plan.recipients.some(r => r.needs_authorization);
  return <div className="modal-backdrop"><div className="modal" role="dialog" aria-modal="true" aria-label="核对本次任务"><h2>{plan.label}</h2><p>底稿 v{plan.expected_revision}</p><p>{plan.stages.map(s => steps[s]).join(' → ')}</p><p>按所需步骤执行；运行中可停止后续步骤。调用模型可能产生费用，实际金额取决于使用量和服务方计价。</p>{plan.recipients.map(r => <p key={r.origin + r.model}><strong>接收端：</strong>{r.origin} · {r.model}<br />{r.needs_authorization ? '本次包含需你授权的内容' : '已有授权覆盖当前范围'}</p>)}<p>{plan.context_scope}</p>{(plan as any).target_label&&<p>本次对象：{(plan as any).target_label}</p>}<ul>{plan.sources.map(s => <li key={s.id}>{s.title} · {s.id}</li>)}</ul>{input.message && <details open><summary>本次新增输入（保存为项目来源）</summary><p className="preserve-lines">{input.message}</p></details>}{plan.generation_target && <p>原型对象：{plan.generation_target.name} · 输入版本 v{plan.expected_revision}</p>}{plan.missing.map(m => <p className="notice" key={m}>{m}</p>)}{error && <p className="error" role="alert">{error}</p>}<div className="toolbar"><button onClick={onClose} disabled={busy}>返回保留输入</button><button className="primary" disabled={busy || !!plan.missing.length} onClick={onSubmit}>{authorize ? '授权本次范围并运行' : '运行本次任务'}</button></div></div></div>;
}

export function OptionDecision({ option, p, action, onClose, submit }: { option: Obj; p: Project; action: string; onClose: () => void; submit: (path: string, body: Obj) => Promise<void> }) {
  const [selected, setSelected] = useState<string[]>([]), [error, setError] = useState(''), [busy, setBusy] = useState(false);
  useDialog(true, onClose);
  return <div className="modal-backdrop"><div className="modal" role="dialog" aria-modal="true" aria-label="核对方案操作"><h2>{action === 'items' ? '采纳指定关联条目' : '记录讨论方向'}</h2><p>{option.name}</p>{action === 'items' ? <><p>仅采纳本次勾选的内容，假设属性保持原状。</p>{option.proposed_item_refs.map((id: string) => { const item = p.items.find(i => i.id === id); const refs = item?.source_refs || []; return <label className="summary-item option-check" key={id}><input type="checkbox" checked={selected.includes(id)} onChange={e => setSelected(e.target.checked ? [...selected, id] : selected.filter(x => x !== id))} /><strong>{item?.title || id}</strong><p>{item?.statement}</p><small>{id} · {item?.epistemic_status === 'proposed' ? '建议/假设，未成为事实' : item?.epistemic_status} · {item?.selection_status}</small>{refs.map((ref: Obj) => <p key={ref.excerpt_id}>来源 {ref.source_id}/{ref.excerpt_id}：{p.sources.find(s => s.id === ref.source_id)?.excerpts.find((x: Obj) => x.id === ref.excerpt_id)?.text || '原文不可见'}</p>)}</label>; })}</> : <p className="notice">只记录讨论方向；关联条目、假设属性、未答问题及既有独立决定保持不变。</p>}<p>方向选择与条目采纳都不等于正式确认。</p>{error && <p role="alert" className="error">{error}</p>}<div className="toolbar"><button onClick={onClose}>取消</button><button className="primary" disabled={busy || (action === 'items' && !selected.length)} onClick={async () => { setBusy(true); setError(''); try { await submit('/options/' + option.id + (action === 'items' ? '/items' : '/direction'), action === 'items' ? { item_ids: selected } : { direction_status: action }); onClose(); } catch(e) { setError((e as Error).message); } finally { setBusy(false); } }}>确认提交</button></div></div></div>;
}
