import type { Blocker, BlockerAction, Project } from '../api';
import { acceptanceTargets } from '../blocker-navigation';
import { scopeLabels } from '../scope-draft';
import { refinementBlockers } from './refine-readiness';

export type RefineTask = {
  id: string;
  title: string;
  area: 'context' | 'scope' | 'question' | 'source' | 'review';
  action: BlockerAction;
  actions: BlockerAction[];
  blockers: Blocker[];
  itemId?: string;
  questionId?: string;
  field?: string;
};

/** Follow the visible order; the last item goes to the checklist, never wraps. */
export function nextRefineTask(before:RefineTask[], currentId:string, after:RefineTask[]=before):RefineTask|undefined {
  const index=before.findIndex(task=>task.id===currentId);
  if(index<0)return after.find(task=>task.id!==currentId);
  for(const previous of before.slice(index+1)){
    const remaining=after.find(task=>task.id===previous.id);
    if(remaining)return remaining;
  }
  return after.find(task=>task.id!==currentId&&!before.some(previous=>previous.id===task.id));
}

/** A location only: the caller validates the destination and controls navigation. */
export function taskForAction(project: Project, action: BlockerAction, title?: string): RefineTask {
  const task: RefineTask = { id: '', title: title || action.label, area: 'review', action, actions: [action], blockers: [] };
  if (action.kind === 'context') {
    return { ...task, id: 'context:' + (action.field || ''), title: scopeLabels[action.field || ''] || task.title, area: 'context', field: action.field };
  }
  if (action.kind === 'question' || action.kind === 'answer') {
    const question = project.questions.find(q => q.id === action.question_id);
    return { ...task, id: question?.decision_group_id ? 'question-group:' + question.decision_group_id : 'question:' + (action.question_id || ''),
      title: question?.question || task.title, area: 'question', questionId: action.question_id };
  }
  if (action.kind === 'scope' || action.kind === 'candidate') {
    return { ...task, id: 'scope', title: '确定本期需求', area: 'scope' };
  }
  if (['item', 'requirement', 'behavior', 'acceptance', 'add_acceptance'].includes(action.kind)) {
    // Acceptance links carry a requirement ID. Resolve the actual candidate,
    // rather than opening the requirement while claiming to show acceptance.
    const itemId = action.kind === 'acceptance' ? acceptanceTargets(project, action.item_id || '')[0]?.id : action.item_id;
    const item = project.items.find(i => i.id === itemId);
    return { ...task, id: itemId ? 'item:' + itemId : 'action:' + action.kind + ':' + (action.item_id || ''),
      title: item?.title || task.title, area: 'scope', itemId };
  }
  if (action.kind === 'source') return { ...task, id: 'source', area: 'source' };
  return { ...task, id: ['review', action.kind, action.document || '', action.step || '', action.item_id || ''].join(':') };
}

function appendAction(actions: BlockerAction[], action: BlockerAction) {
  const same = (other: BlockerAction) => other.kind === action.kind && other.label === action.label &&
    other.item_id === action.item_id && other.question_id === action.question_id && other.field === action.field &&
    other.document === action.document && other.step === action.step && JSON.stringify(other.fields) === JSON.stringify(action.fields);
  if (!actions.some(same)) actions.push(action);
}

/** Backend blockers are the whole required queue; optional questions add no tasks. */
export function buildRefineTasks(project: Project): RefineTask[] {
  const tasks = new Map<string, RefineTask>();
  const allRows = project.product_flow?.slice(0, 3).flatMap(state => state.blockers || []) || [];
  for (const row of refinementBlockers(project)) {
    const actions: BlockerAction[] = [];
    // A blocker may recur at more than one stage with extra navigation links.
    for (const occurrence of allRows.filter(other => other.key === row.key))
      for (const action of occurrence.actions) appendAction(actions, action);
    const locations = actions.length ? actions : [{ kind: 'stage', step: 3, label: '查看待办详情' }];
    for (const original of locations) {
      const candidates = original.kind === 'acceptance' ? acceptanceTargets(project, original.item_id || '') : [];
      const targets = candidates.length ? candidates.map(item => ({ ...original, kind: 'requirement', item_id: item.id })) : [original];
      for (const target of targets) {
        const next = taskForAction(project, target, row.title);
        if (!actions.length) { next.id = 'blocker:' + row.key; next.actions = []; }
        const task = tasks.get(next.id) || next;
        if (!tasks.has(next.id)) tasks.set(next.id, task);
        if (actions.length) appendAction(task.actions, original);
        if (!task.blockers.some(blocker => blocker.key === row.key)) task.blockers.push(row);
      }
    }
  }
  const priority = (task: RefineTask) => ({ question: 0, context: 1, source: 2, scope: task.itemId ? 4 : 3, review: 5 })[task.area];
  return [...tasks.values()].sort((a, b) => priority(a) - priority(b));
}
