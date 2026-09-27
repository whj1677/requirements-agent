import React, { useState } from 'react';
import { useDialog } from './shell';
import { useDirty, useNavigation } from './editing';

type Obj = Record<string, any>;
export const canIncludeReference = (item: Obj) => item.applies_to === 'reference'
  && ['rule', 'acceptance'].includes(item.kind) && !item.target_item_id;

export function ReferenceScopeDialog({ item, project, onInclude, onClose }: {
  item: Obj; project: Obj;
  onInclude: (item: Obj, reason: string, revision: number) => Promise<unknown>;
  onClose: () => void;
}) {
  const [snapshot, setSnapshot] = useState({ item, revision: project.revision });
  const [reason, setReason] = useState(''), [error, setError] = useState(''), [saving, setSaving] = useState(false);
  const guard = useNavigation();
  const current = project.items.find((i: Obj) => i.id === item.id);
  const stale = snapshot.revision !== project.revision;
  const linked = project.items.filter((i: Obj) => i.kind === 'requirement' && i.applies_to === 'to_be'
    && project.product_context?.scope_ids?.includes(i.id)
    && (snapshot.item.related_refs?.includes(i.id) || i.related_refs?.includes(snapshot.item.id)));
  async function save() {
    if (stale) throw Error('底稿已变化，请载入最新条目并重新核对。');
    if (!reason.trim()) throw Error('请填写纳入本期的理由。');
    if (!linked.length) throw Error('此条尚未关联本期需求，请先澄清条目与需求的关系。');
    setSaving(true);
    try { await onInclude(snapshot.item, reason.trim(), snapshot.revision); onClose(); }
    finally { setSaving(false); }
  }
  useDirty(project.id + '-reference-scope-' + item.id, !!reason.trim(), '纳入本期的范围理由', save, onClose);
  useDialog(true, () => { if (!saving) guard(onClose); });
  return <div className="modal-backdrop"><form className="modal" role="dialog" aria-modal="true" aria-label="纳入本期并采纳"
    onSubmit={async e => { e.preventDefault(); setError(''); try { await save(); } catch (e) { setError((e as Error).message); } }}>
    <h2>纳入本期并采纳</h2><p><strong>{snapshot.item.id}｜{snapshot.item.title}</strong></p>
    <p className="preserve-lines">{snapshot.item.statement}</p>
    <p>关联本期需求：{linked.map((i: Obj) => i.id + '｜' + i.title).join('、') || '尚未关联'}</p>
    <p>本次明确采纳这条原文并将其计入本期，记录范围理由。已有文档和审查需要更新，正式确认仍需单独完成。</p>
    {!linked.length && <p className="notice">请先通过条目澄清，将本条关联到已列入本期范围的需求，再执行纳入操作。</p>}
    <label>纳入本期的理由<textarea aria-label="纳入本期的理由" required maxLength={2000} rows={3} value={reason} disabled={saving}
      onChange={e => setReason(e.target.value)} /></label>
    {stale && <div className="notice"><p>底稿已变化；已填写的理由保留，请重新核对最新条目。</p>
      <button type="button" disabled={saving || !current || !canIncludeReference(current)} onClick={() => {
        setSnapshot({ item: current, revision: project.revision }); setError('');
      }}>载入最新条目</button>{current && !canIncludeReference(current) && <p>此条已不再是可纳入的原始参考条目，请返回核对。</p>}</div>}
    {error && <p className="error" role="alert">{error}</p>}
    <div className="toolbar"><button type="button" disabled={saving} onClick={() => guard(onClose)}>取消</button>
      <button className="primary" disabled={saving || stale || !reason.trim() || !linked.length}>确认纳入本期并采纳</button></div>
  </form></div>;
}
