import type { Project } from './api';

/** Saved scope is a business decision; a batch selection is temporary UI state. */
export function scopeSelection(p: Project, ids: string[], dirty: boolean) {
  const saved: string[] = p.product_context?.scope_ids || [];
  const requirements = p.items.filter(item => item.kind === 'requirement' && item.applies_to === 'to_be');
  const scoped = requirements.filter(item => ids.includes(item.id));
  const changed = ids.length !== saved.length || ids.some(id => !saved.includes(id));
  const reason = dirty || changed ? '本页有未保存的修改，请先保存本期范围。'
    : !ids.length ? '先将需要的条目纳入本期，再保存范围。'
    : scoped.length !== ids.length ? '本期范围中有不可用条目，请调整并保存范围。' : '';
  return {
    ready: !reason, reason, scoped,
    adopted: scoped.filter(item => item.selection_status === 'selected').length,
    eligible: scoped.filter(item => item.selection_status === 'candidate' && !item.target_item_id).map(item => item.id),
  };
}

export function retainBatchSelection(selection: string[], eligible: string[], active: boolean, ready: boolean) {
  return active && ready ? selection.filter(id => eligible.includes(id)) : [];
}
