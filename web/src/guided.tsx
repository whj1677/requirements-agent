import React, { useRef, useState } from 'react';
import { DocumentPreview, Project, UserTask } from './api';
import { useDialog } from './shell';
import { taskStateLabels as states, taskStageLabels as steps, taskIntakeGroundingStage } from './task-progress-state';
import { TaskFailureNotice, taskFailure, canRecoverDraft } from './task-failure';

type Obj = Record<string, any>;
export const phases = ['聊清需求想法', '核对草稿与规则', '澄清流程与规则', '核对功能草图', '评审 MRD 与 PRD', '确认与交接'];

export function TaskStatus({ task, busy = false, uncertain = false, onRefresh, onCancel, onRetry, onViewResult, onOpenDraft, onRecover }: { task?: UserTask; busy?: boolean; uncertain?: boolean; onRefresh?: () => void; onCancel: () => void; onRetry: () => void; onViewResult?: () => void; onOpenDraft?: () => void; onRecover?: () => void }) {
  if (!task) return null;
  if (task.local_recovery) return <article className="task-status" role="status"><strong>已恢复待核对草稿</strong><p>已从原输出恢复 {task.local_recovery.candidate_ids.length} 条候选，未重新调用 AI。请核对内容并决定是否采纳。</p>{onOpenDraft&&<button className="primary" disabled={busy} onClick={onOpenDraft}>查看恢复的草稿 →</button>}<details><summary>原任务与恢复记录</summary><p>原 AI 请求仍记录为失败，本地恢复单独留痕。</p><small>{task.id} · {task.local_recovery.id}</small></details></article>;
  const recover=onRecover&&canRecoverDraft(task)?onRecover:undefined;
  const generated=onViewResult && ['succeeded','partial','awaiting_user'].includes(task.status);
  const running=['queued','running'].includes(task.status);
  const stopped=['failed','cancelled','paused_budget'].includes(task.status);
  const unknownSlots=task.request_budget?.reservations.filter(r=>r.state!=='response_received').length||0;
  if (taskFailure(task)) return <article className="task-status task-status-failed" role="status">{uncertain&&<p className="error" role="alert">最新任务状态暂未取得，下方为此前记录。请刷新核对，不要重复发起。{onRefresh&&<button disabled={busy} onClick={onRefresh}>刷新任务状态</button>}</p>}<strong>{task.label} · 分析未成功</strong><TaskFailureNotice task={task}/>{recover&&<button className="primary" disabled={busy||uncertain} onClick={recover}>恢复草稿（不调用 AI） →</button>}{onOpenDraft&&<button className={recover?undefined:"primary"} disabled={busy} onClick={onOpenDraft}>{recover?"手工填写草稿":"前往需求草稿 →"}</button>}<button disabled={busy||uncertain} onClick={onRetry}>手动重试</button><details><summary>查看调用记录</summary><p>已执行 {task.calls} 次请求{task.max_calls!=null&&` / 本次上限 ${task.max_calls}`} · 费用 {task.cost ?? '未知'}</p><small>{task.id} · {task.run_ids.join('、')}</small></details></article>;
  return <article className="task-status" role="status" aria-live="polite">
    {uncertain&&<p className="error" role="alert">最新任务状态暂未取得，下方为此前记录。请刷新核对，不要重复发起。{onRefresh&&<button disabled={busy} onClick={onRefresh}>刷新任务状态</button>}</p>}<strong>{task.label} · {taskIntakeGroundingStage(task) || states[task.status] || '请核对任务状态'}</strong>
    <p className="preserve-lines">{task.message || (running?'任务已开始，正在准备本次分析。':'请核对本次执行记录。')}</p>
    {running&&<><progress aria-label="分析步骤进度" max={Math.max(1,task.stages.length)} value={task.completed_steps}/><p>已完成 {task.completed_steps}/{task.stages.length} 步；当前步骤完成后更新进度。</p></>}
    {stopped&&<p>{unknownSlots?'有 '+unknownSlots+' 个已预留或启动的请求结果未知，不释放额度、不自动重发。':task.calls===0?'本次尚未发出模型请求。':'本次已发出 '+task.calls+' 次模型请求。'}输入与已保存资料保留；核对当前内容后可手动重试。</p>}
    {generated&&<button onClick={onViewResult}>{task.status==='awaiting_user'?'处理成文检查问题':task.action==='prewrite'?'查看成文检查':task.action==='organize'?'查看分析结果':task.action==='document'?'查看讨论稿':task.action==='prototype'?'查看功能草图':'查看本步结果'}</button>}
    {running?<button disabled={busy} onClick={onCancel}>停止后续步骤</button>:stopped&&<button disabled={busy||uncertain} onClick={onRetry}>手动重试</button>}
    <details><summary>本次资料、限制与执行记录</summary><p>已执行 {task.calls} 次请求{task.max_calls!=null&&` / 本次上限 ${task.max_calls}`} · 已处理步骤 {task.completed_steps}/{task.stages.length} · 费用 {task.cost ?? '未知'}</p>{task.request_budget&&<p>已占用请求额度 {task.request_budget.used}/{task.request_budget.limit}；其中结果未知 {unknownSlots}。预留与发送之间中断时，额度保守占用，费用未知。</p>}{task.step_states&&<p>{task.step_states.map(s=>`${steps[s.stage]}：${s.status==='NOT_RUN'?'未执行':states[s.status]||s.status}`).join(' → ')}</p>}<p>资料：{task.source_ids.join('、') || '无资料文件'}</p><p>子步骤：{task.stages.map(s => steps[s]).join(' → ')}</p><small>{task.id} · {task.run_ids.join('、')}</small></details>
  </article>;
}


export function PrdChangePreview({preview}:{preview:DocumentPreview}) {
  const counts=preview.counts;
  return <section className="prd-change-preview" aria-label="PRD 生成前变更预览">
    <header><div><span className="preview-kicker">生成前预览 · 底稿 v{preview.input_revision}</span><h3>{preview.first_generation?'首次生成，先核对本次纳入内容':'生成 PRD 前，先核对本版变化'}</h3></div><span className="preview-local">生成前核对 · 尚未生成文档</span></header>
    <div className="preview-counts" aria-label="变更数量">
      {[['新增',counts.added],['修改',counts.modified],['移出本版',counts.removed],['未确认',counts.pending],['排除',counts.excluded],['AI 建议',counts.suggestions]].map(([label,count])=><div key={label} data-attention={label==='未确认'&&Number(count)>0||undefined}><strong>{count}</strong><span>{label}</span></div>)}
    </div>
    <details className="preview-group" open={preview.first_generation||counts.modified>0}><summary>本轮新增与具体修改 <span>{counts.added+counts.modified+counts.removed} 项</span></summary>
      {!preview.changes.length?<p className="preview-empty">当前纳入范围及条目内容与上一版相同；本次将按当前资料重新成文。</p>:preview.changes.map((change,index)=><details className="preview-change" key={change.item_id} open={index===0&&change.change_type==='modified'}><summary><span className="preview-change-type" data-type={change.change_type}>{change.change_label}</span><strong>{change.title}</strong><small>{change.item_id}</small></summary>
        {change.fields.map(field=><section className="preview-field" key={field.field}><h4>{field.label}<small>逐字对比</small></h4><p className="preview-diff">{field.segments.map((segment,n)=>segment.type==='added'?<ins key={n}>{segment.text}</ins>:segment.type==='removed'?<del key={n}>{segment.text}</del>:<span key={n}>{segment.text}</span>)}</p><details className="preview-full-text"><summary>查看完整前后原文</summary><div><section><h5>修改前</h5><pre>{field.before||'（空）'}</pre></section><section><h5>修改后</h5><pre>{field.after||'（空）'}</pre></section></div></details></section>)}
      </details>)}
    </details>
    <details className="preview-group preview-pending" open={counts.pending>0}><summary>还有 {counts.pending} 条未确认 <span>{counts.pending?'需要处理':'已全部核对'}</span></summary>
      {!preview.pending_confirmations.length?<p className="preview-empty">当前没有待确认的问题、需求行或成文检查事项。</p>:<ul>{preview.pending_confirmations.map(row=><li key={row.id}><strong>{row.kind==='question'?'业务问题':row.kind==='answer'?'回答落实':row.kind==='requirement'?'需求确认':'成文检查'}</strong><span>{row.text}</span><small>{row.id}</small></li>)}</ul>}
    </details>
    <details className="preview-group"><summary>哪些内容被排除 <span>{counts.excluded} 项</span></summary>
      {!preview.excluded.length?<p className="preview-empty">当前没有额外排除的需求条目或资料。</p>:<ul>{preview.excluded.map(row=><li key={row.kind+row.id}><strong>{row.title}</strong><span>{row.reason}</span>{row.text&&<p>{row.text}</p>}<small>{row.id}</small></li>)}</ul>}
    </details>
    <details className="preview-group"><summary>AI 建议是什么 <span>{counts.suggestions} 条待核对</span></summary>
      {!preview.ai_suggestions.length?<p className="preview-empty">当前没有待核对的 AI 建议。</p>:<ul>{preview.ai_suggestions.map(row=><li key={row.kind+row.id}><strong>{row.title}</strong><span>{row.status}</span><p>{row.text}</p><small>{row.id}</small></li>)}</ul>}
    </details>
    <p className="preview-notice">{preview.notice}</p>
  </section>;
}

export function PrdGenerationPreviewDialog({preview,error,busy,onClose,onSubmit}:{preview:DocumentPreview;error:string;busy:boolean;onClose:()=>void;onSubmit:()=>void}) {
  useDialog(!busy,onClose);
  return <div className="modal-backdrop"><div className="modal task-approval-dialog" role="dialog" aria-modal="true" aria-label="核对 PRD 生成内容"><div className="task-approval-body"><h2>核对后调用 AI 生成 PRD</h2><section className="task-authorization ai-generation-note" aria-label="AI 生成说明"><span className="task-authorization-flag">核对后调用 AI</span><div><h3>按当前已保存内容生成</h3><p>本预览只用于核对，不修改需求或生成文档。点击“核对后生成 PRD”后，才会按当前已保存内容调用 AI；未确认事项和 AI 建议仍需人工处理。</p></div></section><PrdChangePreview preview={preview}/>{error&&<p className="error" role="alert">{error}</p>}</div><div className="toolbar task-approval-actions"><button onClick={onClose} disabled={busy}>返回继续核对</button><button className="primary task-authorize-button" disabled={busy} onClick={onSubmit}>核对后生成 PRD</button></div></div></div>;
}

export function LocalPrdPreviewDialog({preview,error,busy,onClose,onSubmit}:{preview:DocumentPreview;error:string;busy:boolean;onClose:()=>void;onSubmit:()=>void}) {
  useDialog(!busy,onClose);
  return <div className="modal-backdrop"><div className="modal task-approval-dialog" role="dialog" aria-modal="true" aria-label="确认 PRD 变更预览"><div className="task-approval-body"><h2>确认预览后，本机生成 PRD</h2><section className="task-authorization local-generation-note" aria-label="本机生成说明"><span className="task-authorization-flag">无需外发授权</span><div><h3>不调用 AI，不发送项目资料</h3><p>只把当前已保存的需求、规则和验收编排为 PRD。未确认事项会保留，生成后仍需核对，不代表正式交付。</p></div></section><PrdChangePreview preview={preview}/>{error&&<p className="error" role="alert">{error}</p>}</div><div className="toolbar task-approval-actions"><button onClick={onClose} disabled={busy}>返回继续核对</button><button className="primary task-authorize-button" disabled={busy} onClick={onSubmit}>确认预览并生成 PRD</button></div></div></div>;
}


export function OptionDecision({ option, p, action, onClose, submit }: { option: Obj; p: Project; action: string; onClose: () => void; submit: (path: string, body: Obj) => Promise<void> }) {
  const [selected, setSelected] = useState<string[]>([]), [error, setError] = useState(''), [busy, setBusy] = useState(false); const submitLock=useRef(false);
  useDialog(!busy, onClose);
  return <div className="modal-backdrop"><div className="modal" role="dialog" aria-modal="true" aria-label="核对方案操作"><h2>{action === 'items' ? '采纳指定关联条目' : '记录讨论方向'}</h2><p>{option.name}</p>{action === 'items' ? <><p>仅采纳本次勾选的内容，假设属性保持原状。</p>{option.proposed_item_refs.map((id: string) => { const item = p.items.find(i => i.id === id); const refs = item?.source_refs || []; return <label className="summary-item option-check" key={id}><input type="checkbox" checked={selected.includes(id)} onChange={e => setSelected(e.target.checked ? [...selected, id] : selected.filter(x => x !== id))} /><strong>{item?.title || id}</strong><p>{item?.statement}</p><small>{id} · {item?.epistemic_status === 'proposed' ? '建议/假设，未成为事实' : item?.epistemic_status} · {item?.selection_status}</small>{refs.map((ref: Obj) => <p key={ref.excerpt_id}>来源 {ref.source_id}/{ref.excerpt_id}：{p.sources.find(s => s.id === ref.source_id)?.excerpts.find((x: Obj) => x.id === ref.excerpt_id)?.text || '原文不可见'}</p>)}</label>; })}</> : <p className="notice">只记录讨论方向；关联条目、假设属性、未答问题及既有独立决定保持不变。</p>}<p>方向选择与条目采纳都不等于正式确认。</p>{error && <p role="alert" className="error">{error}</p>}<div className="toolbar"><button disabled={busy} onClick={onClose}>取消</button><button className="primary" disabled={busy || (action === 'items' && !selected.length)} onClick={async () => { if(submitLock.current)return; submitLock.current=true; setBusy(true); setError(''); try { await submit('/options/' + option.id + (action === 'items' ? '/items' : '/direction'), action === 'items' ? { item_ids: selected } : { direction_status: action }); onClose(); } catch(e) { setError((e as Error).message); } finally { submitLock.current=false; setBusy(false); } }}>{busy?'提交中…':'确认提交'}</button></div></div></div>;
}
