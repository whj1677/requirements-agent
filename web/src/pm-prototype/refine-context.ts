import type { Project } from '../api';

type Obj=Record<string,any>;
const legacyActive=(q:Obj)=>!q.superseded_by&&q.kind!=='parent'&&!q.out_of_scope_reason&&!q.prewrite_resolution&&!['deferred','out_of_scope'].includes(q.status);
const decisionAnswered=(q:Obj)=>q.status==='answered'||q.decision_coverage==='clear';
function select(project:Project,ids:string[]){
  const wanted=new Set(ids);
  return project.questions.filter(q=>wanted.has(q.id));
}

/** Server identities decide currency; question text is never used for de-duplication. */
export function questionGroups(project:Project){
  const view=project.question_view;
  if(view&&[view.open_ids,view.answered_ids,view.history_ids].every(Array.isArray)){
    const open=select(project,view.open_ids),answered=select(project,view.answered_ids),history=select(project,view.history_ids);
    const pending=select(project,view.answer_pending_ids||[]),applied=select(project,view.answer_applied_ids||[]);
    return {current:[...open,...answered],open,answered,history,pending,applied};
  }
  // Isolated legacy fixtures may only carry active_question. If that field is
  // absent too, retain the old status-only projection without guessing history.
  const current=project.questions.filter(q=>q.kind!=='parent'&&(typeof q.active_question==='boolean'?q.active_question:legacyActive(q)));
  const currentIds=new Set(current.map(q=>q.id)),answeredQuestions=current.filter(decisionAnswered);
  return {
    current,
    open:current.filter(q=>!decisionAnswered(q)),
    answered:answeredQuestions,
    history:project.questions.filter(q=>q.kind!=='parent'&&!currentIds.has(q.id)),
    pending:answeredQuestions.filter(q=>q.understanding_status!=='applied'),
    applied:answeredQuestions.filter(q=>q.understanding_status==='applied'),
  };
}

type DuplicateGroup={representative_id:string;question_ids:string[]};
const hasDecisionMetadata=(q:Obj)=>!!(q.decision_group_id||
  (Array.isArray(q.decision_pending_group_ids)?q.decision_pending_group_ids.length:q.decision_pending_group_ids)||
  (Array.isArray(q.decision_points)?q.decision_points.length:q.decision_points)||q.decision_review_proposal);

/** Only server-certified, still-open atomic duplicates are candidates for a shared input. */
export function duplicateGroupCandidates(project:Project):DuplicateGroup[]{
  const open=new Set(questionGroups(project).open.map(q=>q.id)),used=new Set<string>();
  return (project.question_view?.duplicate_groups||[]).flatMap(group=>{
    const ids=[...new Set(group.question_ids||[])];
    if(ids.length<2||!ids.includes(group.representative_id)||ids.some(id=>used.has(id)||!open.has(id)))return [];
    const records=ids.map(id=>project.questions.find(q=>q.id===id));
    if(records.some(q=>!q||hasDecisionMetadata(q)))return [];
    ids.forEach(id=>used.add(id));
    return [{representative_id:group.representative_id,question_ids:ids}];
  });
}

export function openQuestionProgress(project:Project){
  const groups=questionGroups(project),duplicates=duplicateGroupCandidates(project);
  const open=new Set(groups.open.map(q=>q.id));
  const duplicateIds=new Set(duplicates.flatMap(group=>group.question_ids));
  const duplicateRepresentatives=new Map(duplicates.flatMap(group=>group.question_ids.map(id=>[id,group.representative_id] as const)));
  const relatedIds=new Set<string>();
  let relatedGroupCount=0;
  for(const group of project.question_view?.open_groups||[]){
    const ids=[...new Set(group.question_ids||[])].filter(id=>open.has(id));
    const inputs=new Set(ids.map(id=>duplicateRepresentatives.get(id)||id));
    if(ids.length<2||inputs.size<2)continue;
    relatedGroupCount++;
    ids.forEach(id=>relatedIds.add(id));
  }
  return {
    inputCount:groups.open.length-duplicateIds.size+duplicates.length,
    recordCount:groups.open.length,
    duplicateGroupCount:duplicates.length,
    duplicateRecordCount:duplicateIds.size,
    relatedGroupCount,
    relatedRecordCount:relatedIds.size,
  };
}

export function currentQuestions(project: Project) {
  const groups=questionGroups(project);
  return [...groups.open,...groups.answered];
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
