import type { JsonObject as Obj } from './api';

export interface AnswerReview {
  expected_revision: number; question_id: string; question?: string; answer: string; answer_hash: string;
  targets: { item: Obj; clause_hash: string; sources?: Obj[]; readiness?: {
    can_adopt: boolean; can_confirm: boolean; behavior_review_required: boolean; issues: string[];
  } }[];
}

export function answerReviewState(review: AnswerReview, revision: number) {
  const stale = revision > review.expected_revision;
  const blocked = review.targets.filter(target => target.readiness ? !target.readiness.can_confirm : target.item.selection_status !== 'selected');
  return { stale, blocked, ready: !stale && review.targets.length > 0 && blocked.length === 0 };
}

export async function recordAnswerReview(review: AnswerReview, revision: number, confirmed: boolean, write: (body: Obj) => Promise<unknown>) {
  const state = answerReviewState(review, revision);
  if (state.stale) throw Error('内容已变化，请重新载入核对后再确认。');
  if (!state.ready) throw Error('请先处理关联条款的采纳和核对问题。');
  if (!confirmed) throw Error('请先勾选确认回答已落实到这些条款。');
  return write({ expected_revision: review.expected_revision, answer_hash: review.answer_hash,
    clause_hashes: Object.fromEntries(review.targets.map(target => [target.item.id, target.clause_hash])), review_confirmed: true });
}

export async function adoptAnswerClause(review: AnswerReview, id: string, write: (path: string, body: Obj) => Promise<unknown>, read: () => Promise<AnswerReview>) {
  const target = review.targets.find(target => target.item.id === id);
  if (!target || !target.readiness?.can_adopt) throw Error('此条款需先修改或核对，请使用条目旁的处理入口。');
  await write('/items/' + id, { expected_revision: review.expected_revision, selection_status: 'selected' });
  try { return await read(); }
  catch { throw Error('此条款已采纳，但未取得最新核对内容。请重新载入核对，勿重复采纳。'); }
}
