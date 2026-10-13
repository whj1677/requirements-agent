export type DiscussionDraft = { text: string; base: string };
type StorageAccess = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;

export function discussionDraftKey(projectId: string, documentId: string, sectionId = '', questionId = ''): string {
  return 'ra-discussion-draft-v1:' + JSON.stringify([projectId, documentId, sectionId, questionId]);
}

export function readDiscussionDraft(storage: StorageAccess, key: string): DiscussionDraft | null {
  const raw = storage.getItem(key);
  if (!raw) return null;
  try {
    const value = JSON.parse(raw);
    return typeof value.text === 'string' && typeof value.base === 'string' ? value : null;
  } catch { return null; }
}

export function writeDiscussionDraft(storage: StorageAccess, key: string, draft: DiscussionDraft): void {
  if (draft.text === draft.base) storage.removeItem(key);
  else storage.setItem(key, JSON.stringify(draft));
}
