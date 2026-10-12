import React, { useEffect, useRef, useState } from 'react';
import { api, Project, JsonObject as Obj } from './api';
import { useDialog } from './shell';
import { AnswerReview, answerReviewState, recordAnswerReview, adoptAnswerClause } from './answer-application-review';

const kinds: Record<string,string> = { requirement: '需求', rule: '业务规则', acceptance: '验收条件', constraint: '约束' };
const fields: Record<string,string> = { actor: '谁操作', entry: '入口及前置条件', flow: '主要流程', data: '业务数据', permissions: '权限约束', result: '操作结果', exceptions: '边界和异常' };

export function AnswerReviewDialog({ review, revision, confirmed, saving, busy, error, onConfirm, onApply, onAdopt, onEdit, onReload, onClose }: {
  review: AnswerReview; revision: number; confirmed: boolean; saving: string; busy: boolean; error: string;
  onConfirm: (value: boolean) => void; onApply: () => void; onAdopt: (id: string) => void;
  onEdit?: (item: Obj, behavior?: boolean) => void; onReload: () => void; onClose: () => void;
}) {
  const state = answerReviewState(review, revision), locked = !!saving || busy;
  useDialog(true, () => { if (!locked) onClose(); });
  return <div className="modal-backdrop"><section className="modal answer-review-modal" role="dialog" aria-modal="true" aria-label="人工核对回答落实">
    <header className="answer-review-header"><div><h2>核对回答是否落实到条款</h2><p>先把回答落实到关联条款，核对并采纳后再记录。此操作不调用模型。</p></div><button type="button" disabled={locked} onClick={onClose}>关闭核对</button></header>
    <div className="answer-review-body">
      <section className="answer-review-answer"><h3>{review.question}</h3><strong>已保存回答</strong><p>{review.answer}</p></section>
      <h3>关联条款 · {review.targets.length} 条</h3>
      {!review.targets.length && <p role="alert" className="notice">该回答没有关联原条款，暂不能记录落实；请先核对问题关联。</p>}
      {review.targets.map(target => <article key={target.item.id} className="answer-review-clause">
        <div className="section-heading"><h3>{target.item.title}</h3><span className="pill">{target.item.selection_status === 'selected' ? '已采纳' : '待采纳'}</span></div>
        <small>{kinds[target.item.kind] || '条款'} · {target.item.id} · v{target.item.content_version || 1}</small>
        <p>{target.item.statement}</p>
        <dl>{Object.entries(fields).filter(([key]) => target.item.kind === 'requirement' || target.item.behavior?.[key]).map(([key,label]) => <React.Fragment key={key}><dt>{label}</dt><dd>{target.item.behavior?.[key] || <span className="muted">未填写，待澄清</span>}</dd></React.Fragment>)}</dl>
        <details className="answer-review-sources"><summary>查看来源原文 · {target.sources?.length || 0} 处</summary>{target.sources?.map((source,n) => <blockquote key={source.source_id + '-' + source.excerpt_id + '-' + n}><b>{source.title}</b><small>{source.locator || source.excerpt_id}</small><p>{source.text}</p></blockquote>)}</details>
        {!!target.readiness?.issues.length && <div className="notice"><b>此条还需处理</b><ul>{target.readiness.issues.map((issue,n) => <li key={n}>{issue}</li>)}</ul></div>}
        <div className="toolbar">
          {target.readiness?.can_adopt && <button type="button" className="primary" disabled={locked || state.stale || !!error} onClick={() => onAdopt(target.item.id)}>{saving === target.item.id ? '正在采纳此条款…' : '采纳此条款'}</button>}
          {target.item.kind === 'requirement' && target.readiness?.behavior_review_required && <button type="button" disabled={locked || !onEdit} onClick={() => onEdit?.(target.item,true)}>去核对业务行为</button>}
          <button type="button" disabled={locked || !onEdit} onClick={() => onEdit?.(target.item,false)}>定位并修改此条款</button>
        </div>
      </article>)}
    </div>
    <footer className="answer-review-footer">
      {state.stale ? <p role="alert" className="notice">内容已变化，请重新载入最新条款，再核对确认。</p> : state.blocked.length > 0 ? <p role="status" className="notice">还有 {state.blocked.length} 条关联条款需处理。请使用上方对应条目的“采纳此条款”或修改、核对入口；处理完后才能记录回答落实。</p> : !!review.targets.length && <p className="answer-review-ready">关联条款均已采纳并可核对，请确认它们确实承载此回答。</p>}
      {error && <p role="alert" className="error">{error}</p>}
      <label className="answer-review-confirm"><input type="checkbox" checked={confirmed} disabled={locked || !state.ready || !!error} onChange={e => onConfirm(e.target.checked)}/><span>我已逐项核对关联条款的内容和行为说明，确认它们承载以上回答，未增加无依据业务规则。</span></label>
      <div className="toolbar"><button type="button" disabled={locked} onClick={onClose}>返回继续核对</button><button type="button" disabled={locked} onClick={onReload}>{saving === 'reload' ? '正在载入…' : '重新载入核对'}</button><button type="button" className="primary" disabled={locked || !state.ready || !confirmed || !!error} onClick={onApply}>{saving === 'record' ? '正在记录…' : '记录回答落实'}</button></div>
    </footer>
  </section></div>;
}

export function ManualAnswerApplication({ p, q, mutate, busy, onReviewItem, focusReviewNonce=0 }: {
  p: Project; q: Obj; mutate: (path: string, body: Obj) => Promise<unknown>; busy: boolean;
  onReviewItem?: (item: Obj, behavior?: boolean, questionId?: string, afterNavigate?: () => void) => void;
  focusReviewNonce?:number;
}) {
  const [review,setReview] = useState<AnswerReview|null>(null), [confirmed,setConfirmed] = useState(false);
  const [saving,setSaving] = useState(''), [error,setError] = useState(''), [saved,setSaved] = useState(false);
  const lock = useRef(false);
  const lastFocus=useRef(0);
  useEffect(()=>{if(focusReviewNonce&&lastFocus.current!==focusReviewNonce&&!busy){lastFocus.current=focusReviewNonce;void load();}},[focusReviewNonce,busy]);
  const read = () => api<AnswerReview>('/projects/' + p.id + '/questions/' + q.id + '/application-review');
  async function load() {
    if (lock.current || busy) return;
    lock.current = true; setSaving('reload'); setError(''); setConfirmed(false); setSaved(false);
    try { setReview(await read()); } catch(e) { setError((e as Error).message); }
    finally { lock.current = false; setSaving(''); }
  }
  async function adopt(id: string) {
    if (!review || lock.current || busy || answerReviewState(review,p.revision).stale) return;
    lock.current = true; setSaving(id); setConfirmed(false); setError('');
    try { setReview(await adoptAnswerClause(review,id,mutate,read)); }
    catch(e) { setError((e as Error).message); }
    finally { lock.current = false; setSaving(''); }
  }
  async function apply() {
    if (!review || lock.current || busy) return;
    lock.current = true; setSaving('record'); setError('');
    try { await recordAnswerReview(review,p.revision,confirmed,body => mutate('/questions/' + q.id + '/application-review',body)); setReview(null); setConfirmed(false); setSaved(true); }
    catch(e) { setConfirmed(false); setError((e as Error).message); }
    finally { lock.current = false; setSaving(''); }
  }
  const close = () => { if (!lock.current) { setReview(null); setConfirmed(false); setError(''); } };
  return <div className="answer-application"><p>{q.answer_effect || '回答已保存，仍需核对是否落实到条款。'}</p>
    {q.understanding_status !== 'applied' && <button type="button" disabled={busy || !!saving} onClick={load}>人工核对回答与当前条款</button>}
    {saved && <p role="status" className="notice">回答落实已记录。继续核对其余回答和本阶段缺项。</p>}
    {review && <AnswerReviewDialog review={{...review,question:review.question || q.question}} revision={p.revision} confirmed={confirmed} saving={saving} busy={busy} error={error} onConfirm={setConfirmed} onApply={apply} onAdopt={adopt} onReload={load} onClose={close} onEdit={onReviewItem ? (item,behavior) => onReviewItem(item,behavior,q.id,close) : undefined}/>}
    {error && !review && <p role="alert" className="error">{error}</p>}
  </div>;
}
