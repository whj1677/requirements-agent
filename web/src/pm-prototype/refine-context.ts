import type { Project } from '../api';

export function currentQuestions(project: Project) {
  return project.questions.filter(q => !q.superseded_by && q.kind !== 'parent' && !['deferred', 'out_of_scope'].includes(q.status))
    .sort((a, b) => Number(a.status === 'answered' || a.decision_coverage === 'clear') - Number(b.status === 'answered' || b.decision_coverage === 'clear'));
}

export function questionsForItem(project: Project, itemOrId: Record<string, any> | string) {
  const item = typeof itemOrId === 'string' ? project.items.find(i => i.id === itemOrId) : itemOrId;
  if (!item) return [];
  const ids = [item.id, item.target_item_id].filter(Boolean);
  const questions = currentQuestions(project);
  const linked = questions.filter(q => (q.related_refs || []).some((id: string) => ids.includes(id)) ||
    (item.answer_refs || []).includes(q.id) || (q.affected_requirements || []).some((r: { id: string }) => ids.includes(r.id)));
  // A saved answer can serve several independent decisions. Keep its siblings
  // visible together instead of silently dropping part of the review context.
  const groups = new Set(linked.map(q => q.decision_group_id).filter(Boolean));
  return questions.filter(q => linked.includes(q) || (q.decision_group_id && groups.has(q.decision_group_id)));
}
