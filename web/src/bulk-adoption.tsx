import React, { useRef, useState } from 'react';
import { api, Project, JsonObject as Obj } from './api';
import { useDialog } from './shell';
import { AdoptionReview, prepareBulkAdoption, submitBulkAdoption } from './bulk-adoption-review';

const fields: Record<string, string> = { actor: '谁操作', entry: '入口及前置条件', flow: '主要流程', data: '业务数据', permissions: '权限约束', result: '操作结果', exceptions: '边界和异常' };
const kinds: Record<string, string> = { new: '新增', modified: '修改', preserved: '保持约束', existing: '现状', unspecified: '待确定' };

export function BulkAdoption({ p, ids, busy, save, mutate, onBusy, onEdit, onSelectionReset }: {
  p: Project; ids: string[]; busy: boolean; save: () => Promise<unknown>;
  mutate: (path: string, body: Obj) => Promise<unknown>;
  onBusy: (value: boolean) => void; onEdit: (id: string) => void;
  onSelectionReset?: () => void;
}) {
  const [review, setReview] = useState<AdoptionReview | null>(null), [confirmed, setConfirmed] = useState(false);
  const [saving, setSaving] = useState(false), [error, setError] = useState(''), [result, setResult] = useState('');
  const lock = useRef(false);
  const stale = !!review && p.revision > review.expected_revision;
  const close = () => { if (!lock.current) { setReview(null); setConfirmed(false); setError(''); onSelectionReset?.(); } };
  useDialog(!!review, close);
  async function open() {
    if (lock.current || busy) return;
    lock.current = true; setSaving(true); onBusy(true); setError(''); setResult('');
    try {
      const snapshot = await prepareBulkAdoption({ ids, save, read: () => api<Project>('/projects/' + p.id),
        review: body => api<AdoptionReview>('/projects/' + p.id + '/item-adoption-review', 'POST', body) });
      setConfirmed(false); setReview(snapshot);
    } catch (e) { setError((e as Error).message); }
    finally { lock.current = false; setSaving(false); onBusy(false); }
  }
  async function confirm() {
    if (!review || lock.current || busy || stale) return;
    lock.current = true; setSaving(true); onBusy(true); setError('');
    try {
      await submitBulkAdoption(review, confirmed, body => mutate('/item-adoptions', body));
      setResult(review.items.length + ' 条需求已采纳，本次临时勾选已清空。本期范围与已采纳状态保留。');
      setReview(null); setConfirmed(false); onSelectionReset?.();
    } catch (e) { setError((e as Error).message); setConfirmed(false); }
    finally { lock.current = false; setSaving(false); onBusy(false); }
  }
  return <>
    <button type="button" className="primary" disabled={busy || saving || !ids.length} onClick={open}>
      {saving && !review ? '正在准备批量核对…' : `批量核对并采纳（${ids.length}）`}
    </button>
    {result && <p role="status" className="bulk-adoption-result">{result}</p>}
    {error && !review && <p role="alert" className="error">{error}</p>}
    {review && <div className="modal-backdrop"><section className="modal bulk-adoption-modal" role="dialog" aria-modal="true" aria-label="核对并批量采纳需求">
      <div className="section-heading"><h2>核对并批量采纳 {review.items.length} 条需求</h2><button type="button" disabled={saving} onClick={close}>取消</button></div>
      <p>核对下方需求、行为说明和来源后，一次采纳所选内容。空白行为字段仍待澄清，可在下一阶段补充；采纳不会自动补填或完成阶段确认。</p>
      {!!review.blockers.length && <div role="alert" className="error"><p>以下条目需先处理，本次尚未采纳任何需求：</p><ul>{review.blockers.map((blocker, n) => <li key={blocker.id + '-' + n}>{blocker.title}：{blocker.message}</li>)}</ul></div>}
      <div className="bulk-adoption-items">{review.items.map(row => <article key={row.item.id} className="bulk-adoption-item">
        <div className="section-heading"><h3>{row.item.title}</h3><button type="button" disabled={saving} onClick={() => { close(); onEdit(row.item.id); }}>返回修改此条</button></div>
        <small>{row.item.id} · v{row.item.content_version || 1} · {kinds[row.item.change_type] || '待确定'} · {row.review_required ? '本次一并核对行为' : '无需重新核对行为'}</small>
        <p className="bulk-adoption-statement">{row.item.statement}</p>
        <dl>{Object.entries(fields).map(([key, label]) => <React.Fragment key={key}><dt>{label}</dt><dd>{row.item.behavior?.[key] || <span className="muted">待澄清（空白）</span>}</dd></React.Fragment>)}</dl>
        <details className="bulk-adoption-sources"><summary>核对来源原文 · {row.sources.length} 处</summary>{row.sources.map((source, n) => <blockquote key={source.source_id + '-' + source.excerpt_id + '-' + n}><b>{source.title}</b><small>{source.locator || source.excerpt_id}</small><p>{source.text}</p></blockquote>)}{!row.sources.length && <p>缺少可核对的来源，请返回补充。</p>}</details>
      </article>)}</div>
      <div className="bulk-adoption-actions">
        {stale && <p role="alert" className="error">项目内容已变化，请取消后重新打开，核对最新需求。</p>}
        {error && <p role="alert" className="error">{error}。请取消后重新打开核对。</p>}
        <label className="check"><input type="checkbox" checked={confirmed} disabled={saving || stale || !!review.blockers.length} onChange={e => setConfirmed(e.target.checked)} />我已逐项对照来源核对以上需求和七个行为字段，确认没有无依据的已定规则，同意采纳这些需求。</label>
        <div className="toolbar"><button type="button" disabled={saving} onClick={close}>返回继续修改</button><button type="button" className="primary" disabled={busy || saving || stale || !confirmed || !!review.blockers.length || !!error} onClick={confirm}>{saving ? '正在批量采纳…' : `确认并一键采纳 ${review.items.length} 条`}</button></div>
      </div>
    </section></div>}
  </>;
}
