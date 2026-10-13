import type { Project, JsonObject as Obj } from './api';

export interface AdoptionReview {
  expected_revision: number;
  items: { item: Obj; content_hash: string; sources: Obj[]; review_required: boolean }[];
  blockers: { id: string; title: string; message: string }[];
}

export async function prepareBulkAdoption(args: {
  ids: string[];
  save: () => Promise<unknown>;
  read: () => Promise<Project>;
  review: (body: Obj) => Promise<AdoptionReview>;
}): Promise<AdoptionReview> {
  const ids = [...args.ids];
  if (!ids.length || new Set(ids).size !== ids.length) throw Error('请先勾选需要采纳的本期需求。');
  await args.save();
  const latest = await args.read();
  const review = await args.review({ item_ids: ids, expected_revision: latest.revision });
  if (review.expected_revision !== latest.revision || review.items.length !== ids.length ||
      review.items.some(row => !ids.includes(row.item.id)) || new Set(review.items.map(row => row.item.id)).size !== ids.length) {
    throw Error('待核对需求已发生变化，请重新打开批量采纳。');
  }
  return review;
}

export async function submitBulkAdoption(review: AdoptionReview, confirmed: boolean, write: (body: Obj) => Promise<unknown>) {
  if (!confirmed) throw Error('请先核对所选需求、行为说明和来源，再勾选确认。');
  if (!review.items.length || review.blockers.length) throw Error('请先处理不能采纳的需求，再重新核对。');
  return write({
    expected_revision: review.expected_revision,
    item_ids: review.items.map(row => row.item.id),
    content_hashes: Object.fromEntries(review.items.map(row => [row.item.id, row.content_hash])),
    review_confirmed: true,
  });
}
