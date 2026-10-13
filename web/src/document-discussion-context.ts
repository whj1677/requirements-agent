import type { JsonObject, Project, UserTask } from './api';

const ids = (value: unknown): string[] => Array.isArray(value)
  ? value.filter((id): id is string => typeof id === 'string' && !!id) : [];
const documentTarget = (target: JsonObject | undefined, documentId: string, sectionId?: string) =>
  target?.kind === 'document' && target.id === documentId && (!sectionId || target.section_id === sectionId);
const activeQuestion = (question: JsonObject) => !question.superseded_by && question.kind !== 'parent' &&
  !['deferred', 'out_of_scope'].includes(question.status) && !question.out_of_scope_reason;

/** Uses persisted result identities, never title similarity or model temp IDs. */
export function documentDiscussionContext(project: Project, tasks: UserTask[], runs: JsonObject[], documentId: string, sectionId?: string) {
  const selectedTaskIds = new Set<string>(), selectedRunIds = new Set<string>(), processedRunIds = new Set<string>();
  const questionIds = new Set<string>(), itemIds = new Set<string>();
  const belongs = (target: JsonObject | undefined) => documentTarget(target, documentId, sectionId) ||
    target?.kind === 'question' && questionIds.has(target.id);
  // Follow only the persisted document -> question -> follow-up result chain.
  // Repeating also supports out-of-order run records and multiple follow-ups.
  let changed = true;
  while (changed) {
    changed = false;
    const addQuestion = (id: string) => { if (!questionIds.has(id)) { questionIds.add(id); changed = true; } };
    for (const task of tasks) if (belongs(task.retry_input?.target) && !selectedTaskIds.has(task.id)) {
      selectedTaskIds.add(task.id); changed = true;
    }
    const legacyRunIds = new Set(tasks.filter(task => selectedTaskIds.has(task.id)).flatMap(task => task.run_ids || []));
    for (const run of runs) {
      if (!belongs(run.discussion_target) && (run.discussion_target || !legacyRunIds.has(run.id))) continue;
      if (!selectedRunIds.has(run.id)) { selectedRunIds.add(run.id); changed = true; }
      // New result metadata exists only after application; failed outputs and
      // raw model temporary IDs never establish ownership of questions.
      if (processedRunIds.has(run.id) || run.result_applied === false || !run.discussion_result && run.result_applied !== true) continue;
      processedRunIds.add(run.id);
      for (const id of ids(run.discussion_result?.question_ids)) addQuestion(id);
      for (const id of ids(run.discussion_result?.item_ids)) itemIds.add(id);
      // A whole-draft review can include old questions from other chapters.
      // Explicit applied identities take precedence over legacy batch audits.
      if (!run.discussion_result) for (const key of ['proposal_question_ids', 'reviewed_question_ids', 'split_question_ids'])
        for (const id of ids(run.decision_review?.[key])) addQuestion(id);
    }
    for (const question of project.questions)
      if (processedRunIds.has(question.generation_evidence?.run_id)) addQuestion(question.id);
    for (const item of project.items)
      if (processedRunIds.has(item.generation_evidence?.run_id)) itemIds.add(item.id);
    const groups = new Set(project.questions.filter(q => questionIds.has(q.id))
      .map(q => q.decision_group_id).filter(Boolean));
    for (const question of project.questions) {
      if (questionIds.has(question.id)) {
        for (const child of ids(question.superseded_by)) if (!questionIds.has(child)) {
          questionIds.add(child); changed = true;
        }
      }
      if ((questionIds.has(question.prior_question_id) || groups.has(question.decision_group_id)) && !questionIds.has(question.id)) {
        questionIds.add(question.id); changed = true;
      }
    }
  }
  return {
    tasks: tasks.filter(task => selectedTaskIds.has(task.id)), runs: runs.filter(run => selectedRunIds.has(run.id)),
    questions: project.questions.filter(question => questionIds.has(question.id) && activeQuestion(question)),
    candidateItems: project.items.filter(item => itemIds.has(item.id) && item.selection_status === 'candidate' && !item.superseded_by),
  };
}

/** Return an exact persisted chapter, never infer ownership from shared clauses. */
export function documentDiscussionSectionForQuestion(project: Project, tasks: UserTask[], runs: JsonObject[], artifact: JsonObject, questionId: string): string | undefined {
  const sections: JsonObject[] = artifact.content?.sections || [];
  const owners = sections.filter(section => documentDiscussionContext(project, tasks, runs, artifact.id, section.section_id)
    .questions.some(question => question.id === questionId));
  return owners.length === 1 ? owners[0].section_id : undefined;
}

export function documentDiscussionQuestionIds(project: Project, tasks: UserTask[], runs: JsonObject[], documentId: string): Set<string> {
  return new Set(documentDiscussionContext(project, tasks, runs, documentId).questions.map(question => question.id));
}

export function documentDiscussionQuestionComplete(project: Project, question: JsonObject): boolean {
  if (question.decision_coverage_complete !== true || question.understanding_status !== 'applied' ||
    ids(question.decision_pending_group_ids).length) return false;
  return !(project.product_flow || []).flatMap(stage => stage.blockers || []).some(blocker =>
    blocker.actions.some(action => action.question_id === question.id));
}

export function documentDiscussionCanCheckpoint(project: Project): boolean {
  const stages = project.product_flow || [], stage = stages.find(value => value.step === 3);
  return !!stage && !stage.complete && stage.available &&
    [1, 2].every(step => stages.find(value => value.step === step)?.complete) &&
    stage.missing.length === 0 && (stage.blockers || []).length === 0;
}

export function documentDiscussionAnswer(project: Project, question: JsonObject): string {
  const qid = question.decision_group_id || question.id;
  const record = [...((project as JsonObject).decision_answers || [])].reverse()
    .find((answer: JsonObject) => answer.question_id === qid);
  return record?.text ?? question.answer ?? '';
}
