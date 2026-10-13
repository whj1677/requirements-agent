import { useState } from 'react';
import { useDirty } from './editing';
import { discussionDraftKey, readDiscussionDraft, writeDiscussionDraft } from './discussion-draft';

/** Local unsent text has no project-source or business-revision side effects. */
export function useDiscussionDraft(projectId: string, documentId: string, sectionId = '', questionId = '', initial = '') {
  const key = discussionDraftKey(projectId, documentId, sectionId, questionId);
  const [draft, setDraft] = useState(() => {
    try { return readDiscussionDraft(localStorage, key) || { text: initial, base: initial }; }
    catch { return { text: initial, base: initial }; }
  });
  const [error, setError] = useState('');
  function persist(value = draft) {
    try { writeDiscussionDraft(localStorage, key, value); setError(''); }
    catch { setError('浏览器未能保留讨论草稿，请先保留输入后再关闭。'); throw Error('讨论草稿保存失败，输入仍保留。'); }
  }
  function change(text: string) {
    const value = { ...draft, text }; setDraft(value);
    try { persist(value); } catch { /* The local navigation guard retains the input. */ }
  }
  function reset(text: string) {
    const value = { text, base: text }; setDraft(value);
    try { persist(value); } catch { /* Show storage failure without losing the input. */ }
  }
  useDirty(key, !!error, '章节讨论草稿', async () => persist(), () => reset(initial));
  return { text: draft.text, base: draft.base, change, reset, error };
}
