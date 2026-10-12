import React, { useEffect, useMemo, useRef, useState } from 'react';
import { api, JsonObject, Project, UserTask } from './api';
import { useDirty, useNavigation } from './editing';
import { AnswerConfirmation } from './pm-prototype/AnswerConfirmation';
import { documentDiscussionAnswer, documentDiscussionCanCheckpoint, documentDiscussionContext,
  documentDiscussionQuestionComplete } from './document-discussion-context';
import './document-discussion.css';

export { documentDiscussionQuestionIds, documentDiscussionContext } from './document-discussion-context';

type Props = {
  project: Project; tasks: UserTask[]; runs: JsonObject[]; document: JsonObject;
  busy: boolean; running: boolean;
  mutate: (path: string, body: JsonObject, method?: string) => Promise<any>;
  onClarify: (questionId: string) => void;
  onSources: (refs?: JsonObject[]) => void;
  onRefresh: () => void;
  onScope?: () => void;
  focusQuestion?: { id: string; nonce: number };
};

/** Chapter follow-ups stay beside their document and reuse the same explicit
 * answer/preview/commit boundary as requirement review. */
export function DocumentDiscussionPanel(props: Props) {
  const { project, tasks, runs, document: artifact, busy, running, mutate, onRefresh } = props;
  const context = useMemo(() => documentDiscussionContext(project, tasks, runs, artifact.id),
    [project, tasks, runs, artifact.id]);
  const navigate = useNavigation(), panel = useRef<HTMLElement>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [notice, setNotice] = useState(''), [error, setError] = useState(''), [working, setWorking] = useState(false);
  const lock = useRef(false);
  const firstPending = context.questions.find(question => !documentDiscussionQuestionComplete(project, question));
  const question = project.questions.find(value => value.id === selected) ||
    (selected === null ? firstPending : undefined);
  const pending = context.questions.filter(value => !documentDiscussionQuestionComplete(project, value)).length;
  const canCheckpoint = documentDiscussionCanCheckpoint(project);
  const latestTask = context.tasks.at(-1);

  useEffect(() => { setSelected(null); setNotice(''); setError(''); }, [project.id, artifact.id]);
  // Pin the initially opened question. A polling update that completes it must
  // not replace its editor with another pending question or discard a draft.
  useEffect(() => { if (selected === null && firstPending) setSelected(firstPending.id); }, [selected, firstPending?.id]);
  useEffect(() => {
    if (!props.focusQuestion || !context.questions.some(value => value.id === props.focusQuestion?.id)) return;
    const id = props.focusQuestion.id;
    navigate(() => { setSelected(id); requestAnimationFrame(() => panel.current?.scrollIntoView({ block: 'start' })); });
  }, [props.focusQuestion?.nonce]);

  async function checkpoint() {
    if (lock.current) return;
    lock.current = true; setWorking(true); setError('');
    try {
      const latest = await api<Project>('/projects/' + project.id);
      if (!documentDiscussionCanCheckpoint(latest)) throw Error('本次补充状态已变化，请核对页面中的待处理内容后继续。');
      const stage = latest.product_flow!.find(value => value.step === 3)!;
      await mutate('/stage-checks/3', { expected_revision: latest.revision, expected_hash: stage.content_hash });
      setNotice('本次补充已核对，可以继续核对文档。'); setSelected(''); onRefresh();
    } catch (e) { setError((e as Error).message); }
    finally { lock.current = false; setWorking(false); }
  }

  if (!context.tasks.length && !context.runs.length && !context.questions.length) return null;
  return <section className="document-discussion" aria-label="文档章节讨论" ref={panel}>
    <div className="document-discussion-heading"><div><h3>本篇文档的讨论补充</h3>
      <p>在这里回答章节讨论中的问题，核对关联修改后继续阅读文档。</p></div>
      <span>{pending ? `${pending} 项待处理` : context.questions.length ? '本次问题已核对' : '查看讨论结果'}</span></div>
    {latestTask && !context.questions.length && <p role="status">{latestTask.message || '讨论任务已记录，结果会显示在这里。'}</p>}
    {!!context.questions.length && <div className="document-discussion-list" aria-label="本篇文档的讨论问题">
      {context.questions.map(value => <button type="button" key={value.id} disabled={busy || running || working}
        aria-pressed={question?.id === value.id} onClick={() => navigate(() => { setSelected(value.id); setNotice(''); })}>
        <span>{value.question || value.title}</span><small>{documentDiscussionQuestionComplete(project, value) ? '已核对' : value.answer ? '原话已存 · 待核对' : '待回答'}</small>
      </button>)}
    </div>}
    {question && <DocumentDiscussionAnswer key={question.decision_group_id || question.id} {...props} question={question}
      questions={context.questions} disabled={working} onNotice={text => { setNotice(text); onRefresh(); }} />}
    {!question && context.candidateItems.length > 0 && <div className="document-discussion-candidates">
      <h4>本次讨论生成的待确认建议</h4>
      <p>以下建议尚未采用，不会自动写入文档。需要修改时，可在上方问题继续说明并核对修改结果。</p>
      {context.candidateItems.map(item => <article key={item.id}>
        <b>{item.title}</b><p>{item.statement}</p><button type="button" onClick={() => props.onSources(item.source_refs || [])}>核对建议来源</button>
      </article>)}
    </div>}
    {notice && <p className="document-discussion-notice" role="status">{notice}</p>}
    {error && <p className="error" role="alert">{error}</p>}
    <div className="document-discussion-footer">
      {canCheckpoint && <button type="button" className="primary" disabled={busy || running || working}
        onClick={() => navigate(() => void checkpoint())}>{working ? '正在保存核对结果…' : '确认本次补充已处理'}</button>}
      {question && <button type="button" disabled={busy || running || working} onClick={() => navigate(() => setSelected(''))}>继续核对文档</button>}
      {!canCheckpoint && !project.product_flow?.find(value => value.step === 3)?.complete && pending > 0 &&
        <span>先处理上方讨论问题；已确认的内容按实际影响检查。</span>}
      {canCheckpoint && <span>关联问题已满足当前检查，点击后在文档页继续。</span>}
    </div>
  </section>;
}

function DocumentDiscussionAnswer({ project, question, questions, busy, running, disabled, mutate, onClarify,
  onSources, onScope, onRefresh, onNotice }: Props & { question: JsonObject; questions: JsonObject[]; disabled: boolean; onNotice: (text: string) => void }) {
  const saved = documentDiscussionAnswer(project, question), qid = question.decision_group_id || question.id;
  const [answer, setAnswer] = useState(saved), [base, setBase] = useState(saved);
  const [working, setWorking] = useState(false), [error, setError] = useState('');
  const lock = useRef(false), dirty = answer !== base;
  const decisions = question.decision_group_id ? questions.filter(value => value.decision_group_id === question.decision_group_id) : [question];
  useEffect(() => { if (!dirty) { setAnswer(saved); setBase(saved); } }, [saved]);

  async function saveAnswer() {
    if (lock.current) throw Error('回答正在保存，请稍后再继续。');
    if (!answer.trim()) throw Error('请先填写这条回答。');
    lock.current = true; setWorking(true); setError('');
    try {
      const latest = await api<Project>('/projects/' + project.id);
      const current = latest.questions.find(value => value.id === question.id);
      if (!current || current.superseded_by) throw Error('这条问题已经变化，输入仍保留，请刷新后核对。');
      if (documentDiscussionAnswer(latest, current) !== base) throw Error('这条回答已经在别处更新。你的输入仍保留，请核对最新原话后再保存。');
      const result = await mutate('/decision-review/answer', { expected_revision: latest.revision, question_id: qid, answer });
      setBase(answer); onRefresh(); return result;
    } catch (e) { setError((e as Error).message); throw e; }
    finally { lock.current = false; setWorking(false); }
  }
  useDirty(project.id + '-document-answer-' + qid, dirty, '文档讨论的回答', async () => { await saveAnswer(); },
    () => { setAnswer(saved); setBase(saved); setError(''); });
  const completed = decisions.every(value => documentDiscussionQuestionComplete(project, value));
  return <div className="document-discussion-answer">
    {completed && !dirty && <p className="document-discussion-notice">这条补充及关联内容已核对，可以继续核对文档。</p>}
    <AnswerConfirmation project={project} question={question} decisions={decisions} answer={answer} answerDirty={dirty}
      onAnswerChange={setAnswer} onSaveAnswer={saveAnswer} busy={busy || disabled || working} running={running}
      blockReason="" proposalCurrent={question.decision_review_proposal_current === true && !dirty}
      onOrganize={() => onClarify(question.id)} mutate={mutate} onSaved={(_latest, text) => onNotice(text + ' 请继续核对文档中的本次补充。')}
      onSources={onSources} onScope={onScope || (() => {})} onRefresh={onRefresh} />
    {dirty && <button type="button" disabled={busy || running || disabled || working || !answer.trim()}
      onClick={() => void saveAnswer().catch(() => {})}>保存回答原话</button>}
    {error && <p className="error" role="alert">{error}</p>}
  </div>;
}
