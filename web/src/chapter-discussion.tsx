import React, { useEffect, useMemo, useRef, useState } from 'react';
import type { JsonObject, Project, UserTask } from './api';
import { DocumentDiscussionPanel } from './document-discussion';
import { documentDiscussionContext, documentDiscussionQuestionComplete } from './document-discussion-context';
import { useDiscussionDraft } from './use-discussion-draft';
import { useNavigation } from './editing';
import { useDialog } from './shell';
import { TaskStatus } from './guided';

export type ChapterTarget = { id: string; section_id?: string; label: string; artifact: JsonObject };
export type ChapterSubmission = { id: string; documentId: string; sectionId?: string; message: string };

export function ChapterDiscussion({ project, target, tasks, runs, busy, running, blocked, currentDocumentId,
  submission, focusQuestion, hidden = false, operationError = '', mutate, onSubmit, onClarify, onRefresh, onSources, onScope, onClose, onUpdate, onCancel, onRetry }: {
  project: Project; target: ChapterTarget; tasks: UserTask[]; runs: JsonObject[]; busy: boolean; running: boolean;
  blocked: string; currentDocumentId?: string; submission?: ChapterSubmission; focusQuestion?: {id:string;nonce:number};
  hidden?: boolean;
  operationError?: string;
  mutate: (path: string, body: JsonObject, method?: string) => Promise<any>;
  onSubmit: (text: string) => void; onClarify: (questionId: string) => void; onRefresh: () => void;
  onSources: (refs?: JsonObject[]) => void; onScope: () => void; onClose: () => void; onUpdate: () => void;
  onCancel: (task: UserTask) => void; onRetry: (task: UserTask) => void;
}) {
  const context = useMemo(() => documentDiscussionContext(project, tasks, runs, target.id, target.section_id),
    [project, tasks, runs, target.id, target.section_id]);
  const draft = useDiscussionDraft(project.id, target.id, target.section_id);
  const navigate = useNavigation();
  const [newTopic, setNewTopic] = useState(false);
  const pending = context.questions.filter(question => !documentDiscussionQuestionComplete(project, question));
  const latest = context.tasks.at(-1), versionChanged = currentDocumentId !== target.id;
  const section: JsonObject | undefined = target.artifact.content?.sections?.find((value: JsonObject) => value.section_id === target.section_id);
  const original = section?.blocks?.map((block: JsonObject) => block.text || (block.ref_ids || []).map((id: string) =>
    target.artifact.item_snapshot?.find((item: JsonObject) => item.id === id)?.statement || '').join('\n')).filter(Boolean).join('\n\n') || '';
  const canUpdate = !versionChanged && !pending.length && project.product_flow?.find(stage => stage.step === 3)?.complete;
  const submittedTask = submission?.documentId === target.id && submission.sectionId === target.section_id ? submission : undefined;
  const seenSubmission = useRef(submittedTask?.id);
  useEffect(() => {
    if (!submittedTask || seenSubmission.current === submittedTask.id) return;
    seenSubmission.current = submittedTask.id;
    // A slow submission must not erase a new message typed in the meantime.
    if (draft.text === submittedTask.message) draft.reset('');
    setNewTopic(false);
  }, [submittedTask?.id]);
  useEffect(() => { if (focusQuestion) setNewTopic(false); }, [focusQuestion?.nonce]);
  useDialog(!hidden, () => { if (!busy) navigate(onClose); });
  return <div className="drawer-backdrop" hidden={hidden}><section className="source-drawer chapter-discussion-drawer" role="dialog"
    aria-modal="true" aria-label="章节讨论">
    <header className="chapter-discussion-header"><div><p className="eyebrow">章节讨论</p><h2>{target.label}</h2>
      <small>文档 v{target.artifact.document_version || 1} · 讨论、补充和修改核对都在这里</small></div>
      <button type="button" disabled={busy} onClick={() => navigate(onClose)}>返回原章节</button></header>
    <details className="chapter-original"><summary>查看讨论章节原文</summary><p className="preserve-lines">{original || '请对照左侧文档原文。'}</p></details>
    {versionChanged && <p className="notice" role="status">文档已有新版本。这份讨论和草稿仍保留，请返回文档核对当前章节后再发起修改。</p>}
    {latest && <TaskStatus task={{...latest, message: ['queued', 'running', 'failed', 'cancelled', 'paused_budget'].includes(latest.status)
      ? latest.message : pending.length ? '本章建议已生成，仍有待补充或待确认内容。'
      : context.questions.length ? '本次变更已核对；文档仍需按当前底稿更新并阅读核对。' : '本章建议已生成，尚未确认。'}}
      busy={busy} uncertain={!!blocked} onRefresh={onRefresh} onCancel={() => onCancel(latest)} onRetry={() => onRetry(latest)} />}
    {(!pending.length || newTopic) && <div className="chapter-composer">
      <label htmlFor={'chapter-message-'+project.id}>继续讨论这章<textarea id={'chapter-message-'+project.id} rows={3}
        value={draft.text} disabled={busy || running || versionChanged} onChange={event => draft.change(event.target.value)}
        placeholder="说明希望调整的内容，或直接提出你的疑问。" /></label>
      <p className="muted">输入自动保留为本章草稿；关闭侧栏不会改变项目资料和业务版本。</p>
      <button type="button" className="primary" disabled={busy || running || versionChanged || !!blocked || !draft.text.trim()}
        onClick={() => onSubmit(draft.text)}>发送本章讨论</button>
      {newTopic && pending.length > 0 && <button type="button" onClick={() => navigate(() => setNewTopic(false))}>继续处理本次追问</button>}
    </div>}
    {blocked && <p role="status" className="notice">{blocked}</p>}
    {operationError && <p role="alert" className="error">{operationError}</p>}
    {draft.error && <p role="alert" className="error">{draft.error}</p>}
    {!newTopic && <DocumentDiscussionPanel key={target.id+'-'+(target.section_id || '')} project={project} document={target.artifact}
      sectionId={target.section_id} tasks={tasks} runs={runs} busy={busy || versionChanged || !!blocked} running={running}
      mutate={mutate} onClarify={onClarify} onRefresh={onRefresh} onSources={onSources} onScope={onScope}
      focusQuestion={focusQuestion} onContinueDocument={onClose} />}
    {pending.length > 0 && !newTopic && <button type="button" disabled={busy || running || versionChanged}
      onClick={() => navigate(() => setNewTopic(true))}>另提一个章节修改意见</button>}
    {!!context.tasks.length && <details className="chapter-history"><summary>本章已发送的讨论 · {context.tasks.length}</summary>
      {context.tasks.map(task => <article key={task.id}><b>讨论输入</b><p className="preserve-lines">{task.retry_input?.message || '根据已保存的补充继续核对关联修改。'}</p></article>)}
    </details>}
    <footer className="chapter-discussion-footer"><p>确认只保存预览展示的变更；更新后的文档还需核对。</p>
      <button type="button" disabled={busy || running || !canUpdate} onClick={() => navigate(onUpdate)}>更新当前文档</button></footer>
  </section></div>;
}
