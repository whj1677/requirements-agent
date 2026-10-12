import type { JsonObject, Project } from '../api';

export type AnswerAction = 'answer' | 'organize' | 'preview' | 'review' | 'next';
export type AnswerCoverage = {
  status: 'pending' | 'partial' | 'clear' | 'conflict';
  answer_quote: string;
  candidate_item_ids?: string[];
};
type CoverageMap = Record<string, AnswerCoverage>;
type ItemProject = Pick<Project, 'items'>;

/** Selects a visible action only; it never saves, approves, or starts a model. */
export function primaryAnswerAction({ hasAnswer, answerDirty, proposalCurrent, manualReady, complete, hasPreview }: {
  hasAnswer: boolean; answerDirty: boolean; proposalCurrent: boolean;
  manualReady: boolean; complete: boolean; hasPreview: boolean;
}): AnswerAction {
  if (!hasAnswer) return 'answer';
  // A changed answer invalidates both an old preview and an old completion.
  if (answerDirty) return manualReady ? 'preview' : 'organize';
  if (hasPreview) return 'review';
  if (complete) return 'next';
  return proposalCurrent || manualReady ? 'preview' : 'organize';
}

/** Matches build_preview's status/quote contract without answering pending siblings. */
export function manualReviewReady(decisions: JsonObject[], coverage: CoverageMap, answer: string): boolean {
  if (!answer.trim() || !decisions.length) return false;
  const ids = decisions.map(decision => decision.id);
  if (ids.some(id => typeof id !== 'string' || !id) || new Set(ids).size !== ids.length) return false;
  let hasReviewedDecision = false;
  for (const id of ids) {
    const row = coverage[id];
    if (!row || !['pending', 'partial', 'clear', 'conflict'].includes(row.status)) return false;
    const quote = row.answer_quote ?? '';
    if (typeof quote !== 'string' || (quote && !answer.includes(quote)) || (row.status === 'clear' && !quote)) return false;
    if (row.status !== 'pending') hasReviewedDecision = true;
  }
  return hasReviewedDecision;
}

/** Only directly related, adopted original clauses may be reviewed in place. */
export function directReviewTargets(project: ItemProject, decision: JsonObject): JsonObject[] {
  const directIds = new Set<string>(decision.related_refs || []);
  return project.items.filter(item => directIds.has(item.id) && item.selection_status === 'selected' &&
    !item.target_item_id && ['requirement', 'rule', 'acceptance', 'constraint', 'goal'].includes(item.kind));
}

/** Builds preview input, not approval. Existing clauses require explicit user selection. */
export function previewGroups(project: ItemProject, decisions: JsonObject[], coverage: CoverageMap,
  manualTargetIds: Record<string, string[]> = {}) {
  const byId = new Map(decisions.map(decision => [decision.id as string, decision]));
  const parent = new Map(decisions.map(decision => [decision.id as string, decision.id as string]));
  const root = (id: string): string => {
    const next = parent.get(id)!;
    if (next === id) return id;
    const result = root(next); parent.set(id, result); return result;
  };
  const join = (a: string, b: string) => { const left = root(a), right = root(b); if (left !== right) parent.set(right, left); };
  const ownership = new Map<string, string>();
  const candidateForTarget = new Map<string, string>();
  const rows = decisions.flatMap(decision => {
    const row = coverage[decision.id];
    if (row?.status !== 'clear') return [];
    const candidateIds = [...new Set(row.candidate_item_ids || [])];
    const manualIds = [...new Set(manualTargetIds[decision.id] || [])];
    if (!candidateIds.length && !manualIds.length) return [];
    const candidates = candidateIds.map(id => {
      const item = project.items.find(value => value.id === id && value.selection_status === 'candidate');
      if (!item) throw Error('待核对修改已变化，请重新选择并预览。');
      return item;
    });
    const allowedManualIds = new Set(directReviewTargets(project, decision).map(item => item.id));
    if (manualIds.some(id => !allowedManualIds.has(id))) throw Error('只能核对本问题直接关联且已采纳的原条款，请重新选择。');
    const targetIds = [...new Set([...candidates.map(item => item.target_item_id).filter((id): id is string => typeof id === 'string' && !!id), ...manualIds])];
    for (const candidate of candidates) {
      // A shared revision cannot be detached from the other answers it cites.
      // Their clear coverage must already have been reviewed, never inferred.
      for (const qid of candidate.answer_refs || []) {
        if (!byId.has(qid) || coverage[qid]?.status !== 'clear') throw Error('这条修订同时依赖尚未明确的回答，请先逐项核对，不能只确认其中一项。');
        join(decision.id, qid);
      }
      if (candidate.target_item_id) {
        const previous = candidateForTarget.get(candidate.target_item_id);
        if (previous && previous !== candidate.id) throw Error('同一条款有多份不同修订，请先核对要采用的内容，不能一起覆盖。');
        candidateForTarget.set(candidate.target_item_id, candidate.id);
      }
    }
    // Shared targets and candidates form one atomic group, including the
    // unchanged original clauses explicitly selected by the reviewer.
    for (const key of [...candidateIds.map(id => 'candidate:' + id), ...targetIds.map(id => 'target:' + id)]) {
      const previous = ownership.get(key);
      if (previous) join(decision.id, previous); else ownership.set(key, decision.id);
    }
    return [{ decisionId: decision.id as string, candidateIds, targetIds }];
  });
  const components = new Map<string, typeof rows>();
  for (const row of rows) {
    const id = root(row.decisionId);
    components.set(id, [...(components.get(id) || []), row]);
  }
  return [...components.entries()].map(([id, members]) => {
    const linked = decisions.filter(decision => root(decision.id) === id);
    return {
      id: 'group-' + linked.map(decision => decision.id).join('--'),
      title: linked.map(decision => decision.question || decision.title || decision.id).join('；'),
      decision_ids: linked.map(decision => decision.id),
      candidate_item_ids: [...new Set(members.flatMap(row => row.candidateIds))],
      target_item_ids: [...new Set(members.flatMap(row => row.targetIds))],
      depends_on: [] as string[],
    };
  });
}
