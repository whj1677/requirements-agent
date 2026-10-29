import React, { useEffect, useRef, useState } from 'react';
import { Icon } from '../../ui-icons';
import * as M from './model';
import './styles.css';

function Prose({ text }: { text: string }) { return <div className="uxp-prose">{text.split(/\n\n+/).map((p, i) => <p key={i} style={{ whiteSpace: 'pre-wrap' }}>{p}</p>)}</div>; }
function download(name: string, text: string) {
  const url = URL.createObjectURL(new Blob(['\ufeff', text], { type: 'text/markdown;charset=utf-8' }));
  const a = document.createElement('a'); a.href = url; a.download = name; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function Prototype() {
  const [loaded] = useState(M.readSaved);
  const [s, setState] = useState(loaded.state);
  const [storageError, setStorageError] = useState(loaded.storageError);
  const [expandedQuestions, setExpandedQuestions] = useState(false);
  const [manualOpen, setManualOpen] = useState(false);
  const [editingPatch, setEditingPatch] = useState<string | null>(null);
  const [viewingVersion, setViewingVersion] = useState<number | null>(null);
  const [resetRequested, setResetRequested] = useState(false);
  const mount = useRef(true);
  const hadPending = useRef(false);
  const update = (fn: (next: M.State) => void) => setState(prev => { const next = structuredClone(prev); fn(next); return next; });
  const focus = (id: string) => requestAnimationFrame(() => { const el = document.getElementById(id); el?.scrollIntoView({ behavior: 'auto', block: 'start' }); el?.focus({ preventScroll: true }); });
  const go = (page: M.Page) => update(n => { n.page = page; });

  useEffect(() => { document.title = '需求助手 · V3 交互原型'; }, []);
  useEffect(() => {
    try { localStorage.setItem(M.STORAGE_KEY, JSON.stringify(s)); setStorageError(false); }
    catch { setStorageError(true); }
  }, [s]);
  useEffect(() => {
    if (mount.current) { history.replaceState(null, '', `${location.pathname}${location.search}#${s.page}`); mount.current = false; }
    else if (location.hash !== `#${s.page}`) history.pushState(null, '', `#${s.page}`);
    window.scrollTo({ top: 0 });
  }, [s.page]);
  useEffect(() => {
    const back = () => {
      const page = location.hash.slice(1) as M.Page;
      if (['home', 'intake', 'workspace', 'delivery', 'history'].includes(page)) update(n => { n.page = !M.hasDraft(n) && ['workspace', 'delivery', 'history'].includes(page) ? 'home' : page; });
    };
    window.addEventListener('popstate', back); return () => window.removeEventListener('popstate', back);
  }, []);
  useEffect(() => {
    if (s.job?.status !== 'running') return;
    const id = s.job.id;
    const timer = setTimeout(() => update(n => { if (n.job?.id === id) M.finishJob(n); }), Math.max(0, s.job.due - Date.now()));
    return () => clearTimeout(timer);
  }, [s.job?.id, s.job?.status]);
  const pendingVersion = s.versions.find(v => v.status === 'generating');
  useEffect(() => {
    if (!pendingVersion) return;
    const timer = setTimeout(() => update(n => M.finishFiles(n, pendingVersion.number)), Math.max(0, pendingVersion.due - Date.now()));
    return () => clearTimeout(timer);
  }, [pendingVersion?.number, pendingVersion?.status, pendingVersion?.due]);

  const questions = M.openQuestions(s), critical = M.criticalQuestions(s);
  const q = s.questions.find(q => q.id === s.selectedQ) ?? s.questions[0];
  const pending = M.pendingPatches(s);
  const activeJob = s.job && ['running', 'unknown'].includes(s.job.status);
  const draftDirty = M.dirty(s);
  const title = M.hasDraft(s) ? M.projectName(s.sourceRaw) : '需求助手';
  const selectedVersion = s.versions.find(v => v.number === s.version);
  useEffect(() => {
    if (pending.length) focus('candidate-review');
  }, [s.batch?.epoch]);
  useEffect(() => {
    if (hadPending.current && !pending.length && s.page === 'workspace') focus('current-question');
    hadPending.current = pending.length > 0;
  }, [pending.length, s.page]);

  function openQuestion(id: string, fromCheck = false) {
    setManualOpen(false); setEditingPatch(null);
    update(n => { n.page = 'workspace'; n.selectedQ = id; n.returnToCheck = fromCheck; });
    focus('current-question');
  }
  function editModule(id: string) {
    if (s.editor?.moduleId === id) { focus(`editor-${id}`); return; }
    if (s.editor && s.editor.moduleId !== id && draftDirty) {
      update(n => { n.notice = '请先保存正在编辑的模块，再切换模块；输入仍已暂存在本浏览器。'; });
      focus(`editor-${s.editor.moduleId}`); return;
    }
    update(n => { n.editor = { moduleId: id, text: n.modules.find(m => m.id === id)!.body }; });
    focus(`editor-${id}`);
  }
  function draftDownload() {
    download(`${title}_讨论稿_v${s.version}.md`, M.documentMarkdown(s.modules, s.version, false, s.questions, s.resolutions, 'PRD', s.sourceRaw));
  }
  function versionDownload(v: M.Version, type: 'PRD' | 'MRD') {
    download(M.fileName(v.number, type, v.raw), M.documentMarkdown(v.modules, v.number, true, v.questions, v.resolutions, type, v.raw));
  }

  const jobPanel = s.job && s.job.status !== 'done' ? <section className={`uxp-notice ${s.job.status === 'failed' ? 'error' : 'warning'}`} aria-label="演示任务状态">
    <strong>{s.job.status === 'running' ? '正在演示 AI 整理…' : s.job.status === 'unknown' ? '暂时没有收到任务结果' : '本次整理未完成'}</strong>
    <p>{s.job.status === 'running' ? '原文已经可用，你可以继续编辑。此过程没有真实模型请求。' : '原文与最近保存的正文都已保留，可以继续手工处理。'}</p>
    {s.job.status === 'unknown' && <button onClick={() => update(n => M.finishJob(n, true))}>查看原任务状态</button>}
    {s.job.status === 'failed' && <div className="uxp-actions"><button onClick={() => update(M.retryJob)}>重试这次整理（模拟）</button><button onClick={() => { go('workspace'); if (q) { setManualOpen(true); focus('current-question'); } }}>继续手工编辑</button></div>}
  </section> : null;

  const candidatePanel = s.batch && pending.length ? <section className="uxp-card uxp-candidate" id="candidate-review" tabIndex={-1} aria-label="核对建议">
    <div className="uxp-row"><h3>核对这 {pending.length} 处修改</h3><span className="uxp-badge warning">模拟建议 · 尚未写入</span></div>
    <p className="uxp-small">{s.questions.find(x => x.id === s.batch!.qid)?.title} · 写入对应模块后，此项才可能完成。</p>
    {s.batch.conflict && <div className="uxp-notice error" role="alert"><strong>正文已改变，请重新核对</strong><p>这批建议参考的是旧内容。当前正文保持不变。</p><button onClick={() => update(M.rebase)}>按当前正文重新核对</button></div>}
    {s.batch.rebased && <details open><summary>当前正文（重新核对依据）</summary><Prose text={M.moduleFor(s, s.batch.qid)?.body ?? ''} /></details>}
    {pending.map(p => <article className="uxp-diff" key={p.id} data-testid={`patch-${p.id}`}>
      <h4>{p.title}</h4><div className="uxp-diff-before"><span className="uxp-small">原文</span><p>{p.replace && M.moduleFor(s, s.batch!.qid)?.body.includes(p.replace) ? p.replace : '此处尚未补充，将追加以下内容。'}</p></div>
      <div className="uxp-diff-after"><span className="uxp-small">建议改为</span>{editingPatch === p.id ? <label className="uxp-label">修改{p.title}<textarea aria-label={`修改${p.title}`} value={p.text} onChange={e => update(n => { n.batch!.patches.find(x => x.id === p.id)!.text = e.target.value; })} /></label> : <p>{p.text}</p>}</div>
      <div className="uxp-actions"><button onClick={() => setEditingPatch(editingPatch === p.id ? null : p.id)}>{editingPatch === p.id ? '收起修改' : '修改建议'}</button><button disabled={draftDirty || s.batch?.conflict || !p.text.trim()} onClick={() => update(n => M.adopt(n, p.id))}>采用此处</button><button onClick={() => update(n => M.reject(n, p.id))}>不采用此处</button></div>
    </article>)}
    {draftDirty && <p className="uxp-notice warning">先保存正在编辑的正文，再核对建议；你的输入已暂存。</p>}
    <div className="uxp-actions"><button className="uxp-primary" disabled={draftDirty || s.batch.conflict || pending.some(p => !p.text.trim())} onClick={() => update(n => M.adopt(n))}>采用这 {pending.length} 处修改</button><button onClick={() => update(n => M.reject(n))}>全部不采用</button></div>
    <p className="uxp-footnote">离开页面会保留建议，不会自动采纳。空建议不能采用。</p>
  </section> : null;

  function renderVersion(v: M.Version) {
    return <article key={v.number} className="uxp-version-card" aria-label={`已确认 v${v.number}`}>
      <div className="uxp-row"><h3>已确认 v{v.number}</h3><span className="uxp-badge success">正文已保留</span></div>
      <p className="uxp-small">{new Date(v.confirmedAt).toLocaleString('zh-CN')} · {v.modules.map(m => m.title).join('、')}</p>
      {v.status === 'generating' && <p role="status">正在生成示例文件…</p>}
      {v.status === 'unknown' && <div className="uxp-notice warning"><p>刷新后需查询文件生成状态，不会自动重试。</p><button onClick={() => update(n => M.finishFiles(n, v.number))}>查询 v{v.number} 文件状态</button></div>}
      {v.status === 'failed' && <div className="uxp-notice error"><strong>文件生成模拟失败</strong><p>v{v.number} 正文已确认并保留。只重试同一版本的文件，不重新审批。</p><button onClick={() => update(n => M.retryFiles(n, v.number))}>重新生成 v{v.number} 文件</button></div>}
      <ul className="uxp-files">{v.files.map(type => <li key={type}><span>{M.fileName(v.number, type, v.raw)} <small>Markdown · v{v.number}</small></span>{v.status === 'ready' && <button onClick={() => versionDownload(v, type)}>下载 {type}</button>}</li>)}</ul>
      <div className="uxp-actions"><button onClick={() => setViewingVersion(viewingVersion === v.number ? null : v.number)}>{viewingVersion === v.number ? '收起正文' : `查看 v${v.number} 正文`}</button><button className="uxp-secondary" onClick={() => update(n => M.continueVersion(n, v.number))}>{s.locked ? `基于 v${v.number} 继续修改` : `继续当前 v${s.version} 草稿`}</button></div>
      {viewingVersion === v.number && <section className="uxp-card" aria-label={`v${v.number} 只读正文`}><p className="uxp-badge">只读版本 · 后续修改不会改写此处</p>{v.modules.map(m => <div key={m.id}><h3>{m.title}</h3><Prose text={m.body} /></div>)}</section>}
    </article>;
  }

  return <div className="pm-prototype">
    <div className="uxp-banner">交互原型 · 默认使用合成示例 · 不调用模型 · 内容仅存本浏览器</div>
    <header className="uxp-header"><div className="uxp-logo">需求助手</div><nav className="uxp-nav" aria-label="工作区导航">
      {M.hasDraft(s) ? <>{(['workspace', 'delivery', 'history'] as M.Page[]).map((p, i) => <button key={p} aria-current={s.page === p ? 'page' : undefined} onClick={() => go(p)}>{['需求稿', '交付检查', '资料与版本'][i]}</button>)}</> : <span className="uxp-muted">先把需求写清楚，再确认交付</span>}
    </nav><span className="uxp-small" role="status">{storageError ? '本机保存不可用，请勿关闭页面' : draftDirty ? '编辑输入已暂存 · 尚未写入正文' : '演示内容已存本机'}</span></header>
    <main className="uxp-page" id="prototype-content">
      {storageError && <p className="uxp-notice error" role="alert">浏览器无法保存演示内容。请先下载讨论稿，避免关闭后丢失。</p>}
      {s.notice && <div className="uxp-notice" role="status" data-testid="notice">{s.notice}</div>}

      {(s.page === 'home' || s.page === 'intake') && <>
        {s.page === 'home' ? <><div className="uxp-hero"><p className="uxp-eyebrow">从已有信息开始</p><h1>把想法变成可讨论的需求稿</h1><p>选择适合你的方式。两种入口，整理的是同一份需求。</p></div>
          <div className="uxp-entry-grid"><section className="uxp-entry-card"><Icon name="question" /><h2>和助手聊需求</h2><p>手头只有想法也可以开始。先留下草稿，再逐项补清关键问题。</p><button className="uxp-primary" onClick={() => update(n => { n.mode = 'chat'; n.page = 'intake'; })}>开始聊需求</button></section><section className="uxp-entry-card"><Icon name="edit" /><h2>直接填写需求</h2><p>已有材料或熟悉产品工作，直接粘贴原文，随时手工编辑。</p><button className="uxp-primary" onClick={() => update(n => { n.mode = 'direct'; n.page = 'intake'; })}>直接填写</button></section></div>
          {M.hasDraft(s) && <div className="uxp-hero"><button className="uxp-secondary" onClick={() => go('workspace')}>继续已有演示草稿</button></div>}
        </> : <section className="uxp-card uxp-intake"><p className="uxp-eyebrow">{s.mode === 'chat' ? '和助手聊需求' : '直接填写需求'}</p><h1>{s.mode === 'chat' ? '先说说你要做什么' : '把已有内容放进来'}</h1><p>用三个模块的合成案例体验完整流程。自定义文字只保留原文，不做真实分析。</p>
          <div className="uxp-actions"><button disabled={M.hasDraft(s)} onClick={() => update(n => { n.raw = M.SAMPLE; })}>填入三模块示例</button><button onClick={() => update(n => { n.mode = n.mode === 'chat' ? 'direct' : 'chat'; })}>切换为{s.mode === 'chat' ? '直接填写' : '助手整理'}</button></div>
          <label className="uxp-label">{s.mode === 'chat' ? '你的想法与已有信息' : '已有需求原文'}<textarea className="uxp-textarea" aria-label="需求输入" rows={12} value={s.raw} readOnly={M.hasDraft(s)} onChange={e => update(n => { n.raw = e.target.value; })} placeholder="可先点击“填入三模块示例”体验。" /></label>
          <p className="uxp-small">输入会暂存在本浏览器。原型未接入附件、网址读取或对话模型。</p>
          <div className="uxp-actions">{M.hasDraft(s) ? <button className="uxp-primary" onClick={() => go('workspace')}>继续已有草稿</button> : <button className="uxp-primary" disabled={!s.raw.trim()} onClick={() => update(n => M.startDraft(n, n.mode))}>{s.mode === 'chat' ? '开始整理示例（模拟）' : '保存原文，开始整理'}</button>}<button onClick={() => go('home')}>返回选择入口</button></div>
        </section>}
      </>}

      {s.page === 'workspace' && M.hasDraft(s) && <>
        <div className="uxp-row uxp-workspace-heading"><div><p className="uxp-eyebrow">{s.locked ? '查看已确认需求' : '边看草稿，边补关键问题'}</p><h1>{title}</h1></div><div className="uxp-actions"><span className="uxp-badge">v{s.version} · {s.locked ? '已确认' : '草稿'}</span><button onClick={() => go('delivery')}>查看交付检查</button></div></div>
        {s.returnToCheck && <p className="uxp-notice">正在处理交付检查中的问题。完成核对后会返回检查页。<button onClick={() => update(n => { n.page = 'delivery'; n.returnToCheck = false; })}>返回检查</button></p>}
        {s.locked && <p className="uxp-notice success">当前查看的是已确认 v{s.version}。<button onClick={() => update(n => M.continueVersion(n, s.version))}>基于 v{s.version} 继续修改</button></p>}
        {jobPanel}
        <div className="uxp-workspace"><article className="uxp-document" aria-label="需求正文"><div className="uxp-document-top"><div><h2>需求稿</h2><p className="uxp-small">来源：{s.sourceRaw === M.SAMPLE ? '内置合成材料' : '你粘贴的原文'}与人工核对 · 已知问题随正文维护</p></div><button disabled={draftDirty} onClick={draftDownload}>下载讨论稿</button></div>
          <nav className="uxp-module-nav" aria-label="模块定位">{s.modules.map(m => <button key={m.id} onClick={() => focus(`module-${m.id}`)}>{m.title}</button>)}</nav>
          {draftDirty && <p className="uxp-notice warning">编辑输入已暂存。请点击“保存正文”后再下载或确认。</p>}
          {s.modules.map(m => <section className="uxp-module uxp-anchor" key={m.id} id={`module-${m.id}`} tabIndex={-1} data-testid={`module-${m.id}`}>
            <div className="uxp-module-heading"><h3>{m.title}</h3>{!s.locked && <button onClick={() => editModule(m.id)}>编辑{m.title}</button>}</div>
            {s.editor?.moduleId === m.id ? <div className="uxp-editor"><label className="uxp-label">编辑正文<textarea id={`editor-${m.id}`} aria-label={`编辑${m.title}正文`} rows={15} value={s.editor.text} onChange={e => update(n => { n.editor!.text = e.target.value; })} /></label><div className="uxp-actions"><button className="uxp-primary" disabled={!s.editor.text.trim()} onClick={() => update(M.saveEditor)}>保存正文</button><button onClick={() => { go('delivery'); }}>稍后继续编辑</button></div><p className="uxp-small">输入已暂存到本浏览器，刷新后仍可继续。</p></div> : <Prose text={m.body} />}
            {s.questions.find(x => x.id === s.batch?.qid)?.moduleId === m.id && candidatePanel}
            {s.questions.filter(x => x.moduleId === m.id).map(x => <div key={x.id} className={`uxp-notice ${M.isResolved(s, x) ? 'success' : 'warning'}`}><span>{M.isResolved(s, x) ? '已核对：' : x.critical ? '交付前需确认：' : '后续规划：'}{x.title}</span><button onClick={() => openQuestion(x.id)}>查看{x.critical && !M.isResolved(s, x) ? '并处理' : '此项'}</button></div>)}
          </section>)}
        </article>
        <aside className="uxp-assistant" aria-label="需求助手"><h2>把关键问题补清楚</h2><p data-testid="question-summary">已知待确认 <strong>{questions.length}</strong> 项，其中 <strong>{critical.length}</strong> 项影响本次交付。</p><p className="uxp-small">这是当前已知问题，不代表材料已覆盖全部需求。</p>
          <button className="uxp-quiet" aria-expanded={expandedQuestions} onClick={() => setExpandedQuestions(!expandedQuestions)}>{expandedQuestions ? '收起问题列表' : '查看所有问题'}</button>
          {expandedQuestions && <ul className="uxp-question-list">{s.questions.map(x => <li className={`uxp-question ${x.id === q?.id ? 'active' : ''}`} key={x.id}><button onClick={() => openQuestion(x.id)}>{x.title}</button><span className="uxp-small">{M.isResolved(s, x) ? '已核对' : x.critical ? '影响本期交付' : s.deferred.includes(x.id) ? '已记入后续计划' : '不影响本期'}</span></li>)}</ul>}
          {q && <section id="current-question" tabIndex={-1} className="uxp-anchor"><span className={`uxp-badge ${M.isResolved(s, q) ? 'success' : 'warning'}`}>{M.isResolved(s, q) ? '此项已核对' : q.critical ? '影响本期交付' : '后续规划'}</span><h3>{q.title}</h3><p className="uxp-small">{q.detail}</p><button className="uxp-quiet" onClick={() => focus(`module-${q.moduleId}`)}>定位对应正文</button>
            {!q.critical ? <><p>本期三个模块可以继续交付，该事项会随讨论稿保留。</p><button disabled={s.deferred.includes(q.id) || s.locked} onClick={() => update(n => { n.deferred.push(q.id); n.notice = '已记入后续计划，不影响本期交付。'; })}>{s.deferred.includes(q.id) ? '已记入后续计划' : '记入后续计划'}</button></> : M.isResolved(s, q) ? <><p>规则与验收已写入本模块。</p>{critical.length > 0 ? <button className="uxp-primary" onClick={() => openQuestion(critical[0].id, s.returnToCheck)}>处理下一项关键问题</button> : <button className="uxp-primary" onClick={() => go('delivery')}>查看交付检查</button>}</> : !s.locked && <>
              <fieldset disabled={!!activeJob || pending.length > 0} className="uxp-fieldset"><legend className="uxp-label">你的决定</legend>{q.choices.map(choice => <label className="uxp-choice" key={choice}><input type="radio" name={`answer-${q.id}`} checked={s.answers[q.id] === choice} onChange={() => update(n => { n.answers[q.id] = choice; })} /><span>{choice}</span></label>)}<label className="uxp-label">补充或直接写答案<textarea aria-label="你的决定" value={s.answers[q.id] ?? ''} onChange={e => update(n => { n.answers[q.id] = e.target.value; })} /></label></fieldset>
              <div className="uxp-actions"><button className="uxp-primary" disabled={!s.answers[q.id]?.trim() || !!activeJob || pending.length > 0} onClick={() => update(n => M.beginJob(n, 'proposal', q.id))}>生成修改建议（模拟）</button><button onClick={() => update(n => { n.notice = '此问题继续保留为待确认。你可处理其他模块或下载讨论稿。'; })}>暂时不确定</button></div>
              <button className="uxp-quiet" aria-expanded={manualOpen} onClick={() => setManualOpen(!manualOpen)}>直接手工补齐</button>
              {manualOpen && <section className="uxp-editor"><p className="uxp-small">直接编辑这项规则和验收，保存后写入正文。不会调用 AI。</p><label className="uxp-label">确认的业务规则<textarea aria-label="确认的业务规则" value={s.manual[q.id]?.rule ?? ''} onChange={e => update(n => { n.manual[q.id] = { ...(n.manual[q.id] ?? { rule: '', acceptance: '' }), rule: e.target.value }; })} /></label><label className="uxp-label">对应的验收条件<textarea aria-label="对应的验收条件" value={s.manual[q.id]?.acceptance ?? ''} onChange={e => update(n => { n.manual[q.id] = { ...(n.manual[q.id] ?? { rule: '', acceptance: '' }), acceptance: e.target.value }; })} /></label><button className="uxp-primary" disabled={!s.manual[q.id]?.rule.trim() || !s.manual[q.id]?.acceptance.trim() || draftDirty || pending.length > 0} onClick={() => update(n => M.manualSave(n, q.id))}>保存并完成此项</button>{pending.length > 0 && <p className="uxp-small">请先采用或不采用当前建议，再手工填写。</p>}{draftDirty && <p className="uxp-small">请先保存左侧正文，再写入本项。</p>}</section>}
            </>}
          </section>}
          {pending.length > 0 && <div className="uxp-notice warning"><p>正文旁有 {pending.length} 处修改等你核对。</p><button className="uxp-secondary" onClick={() => focus('candidate-review')}>核对本次修改</button></div>}
        </aside></div>
      </>}

      {s.page === 'delivery' && M.hasDraft(s) && <section><div className="uxp-hero"><p className="uxp-eyebrow">交付检查</p><h1>{s.locked ? `v${s.version} 正文已确认` : critical.length || pending.length || draftDirty || activeJob ? '先补清影响交付的内容' : '这一版可以确认交付'}</h1><p>讨论稿随时可用。正式确认前，核对本期规则、实际正文和文件清单。</p></div>
        {s.locked && selectedVersion ? renderVersion(selectedVersion) : <div className="uxp-delivery-grid"><section className="uxp-card"><h2>本期交付检查</h2>
          {critical.length > 0 ? <ul className="uxp-checklist">{critical.map(x => <li key={x.id}><div><strong>{x.title}</strong><p className="uxp-small">{s.modules.find(m => m.id === x.moduleId)?.title} · {x.detail}</p></div><button className="uxp-secondary" onClick={() => openQuestion(x.id, true)}>去处理</button></li>)}</ul> : <p className="uxp-notice success">本期已知关键问题已核对，正文和验收可以一并预览。</p>}
          {draftDirty && <p className="uxp-notice warning">还有正文编辑未保存。<button onClick={() => { update(n => { n.page = 'workspace'; n.returnToCheck = true; }); focus(`editor-${s.editor!.moduleId}`); }}>去保存正文</button></p>}
          {pending.length > 0 && <p className="uxp-notice warning">还有 {pending.length} 处建议未取舍。<button onClick={() => { openQuestion(s.batch!.qid, true); focus('current-question'); }}>去核对建议</button></p>}
          {jobPanel}
          <details><summary>不影响本期的待决事项（{questions.filter(x => !x.critical).length}）</summary>{questions.filter(x => !x.critical).map(x => <p key={x.id}>{x.title} · 已排除在本期范围外。</p>)}</details>
          <button disabled={draftDirty} onClick={draftDownload}>下载讨论稿（带待确认标记）</button><p className="uxp-footnote">候选建议只有采用后才进入正文。未采用建议不会被导出。</p>
        </section><section className="uxp-card"><span className="uxp-badge">准备确认 v{s.version}</span><h2>确认这一版的交付范围</h2><p><strong>包含：</strong>{s.modules.map(m => m.title).join('、')}</p><p className="uxp-small">范围以当前正文为准。{s.sourceRaw === M.SAMPLE ? '本期不含外部同步、批量导入、自动外呼、销售预测。' : '本原型未分析你的自定义材料，请人工核对排除项。'}</p><button onClick={() => go('workspace')}>回看实际正文</button>
          <h3>本次文件</h3><ul className="uxp-files">{(['PRD', ...(s.includeMrd ? ['MRD'] : [])] as ('PRD' | 'MRD')[]).map(type => <li key={type}><span>{M.fileName(s.version, type, s.sourceRaw)}<small>Markdown · v{s.version}</small></span></li>)}</ul><details><summary>按需增加文档</summary><label className="uxp-choice"><input type="checkbox" checked={s.includeMrd} onChange={e => update(n => { n.includeMrd = e.target.checked; })} /><span>同时提供 MRD 示例（背景与范围）</span></label></details>
          <p className="uxp-small">此原型实际生成 Markdown 示例文件，用于核对内容和版本；未实现 Word、PDF 或正式产品导出。</p><button className="uxp-primary" disabled={M.blockingReasons(s).length > 0} onClick={() => update(M.confirmVersion)}>确认 v{s.version} 并生成示例文件</button><p className="uxp-footnote">{M.blockingReasons(s).length ? '先处理左侧列出的事项。' : '确认后保留当前正文快照，后续修改会建立新草稿。'}</p>
        </section></div>}
      </section>}

      {s.page === 'history' && <section><div className="uxp-row"><div><p className="uxp-eyebrow">资料与版本</p><h1>保留依据，也保留每次确认</h1></div><button onClick={() => go('workspace')}>返回当前需求稿</button></div><details className="uxp-card"><summary>查看本次保存的原始材料</summary><Prose text={s.sourceRaw} /></details><h2>已确认版本</h2>{s.versions.length ? [...s.versions].reverse().map(renderVersion) : <p className="uxp-card">还没有已确认版本。当前草稿可以继续编辑，完成交付检查后再确认。</p>}</section>}

      <details className="uxp-demo-controls"><summary>原型演示控制</summary><p className="uxp-small">仅切换固定的演示结果，用来检查失败恢复。不代表真实模型耗时或质量。</p><div className="uxp-row"><label className="uxp-label">下一次 AI 演示结果<select aria-label="下一次 AI 演示结果" value={s.aiOutcome} onChange={e => update(n => { n.aiOutcome = e.target.value as M.State['aiOutcome']; })}><option value="success">成功，返回待核对建议</option><option value="failed">明确失败，保留草稿</option><option value="unknown">状态未知，可查询原任务</option></select></label><label className="uxp-label">下一次文件生成结果<select aria-label="下一次文件生成结果" value={s.fileOutcome} onChange={e => update(n => { n.fileOutcome = e.target.value as M.State['fileOutcome']; })}><option value="success">成功，可下载</option><option value="failed">失败，保留确认版本</option></select></label></div><p className="uxp-small">已记录 {s.events.length} 个演示事件；不用于推算真实用户效率。</p><button className="uxp-danger" onClick={() => setResetRequested(true)}>重置这个演示</button>{resetRequested && <div className="uxp-notice warning"><p>只清除本浏览器的原型内容；不会影响日常项目。</p><div className="uxp-actions"><button onClick={() => { setState(M.initialState()); setResetRequested(false); setManualOpen(false); setExpandedQuestions(false); setViewingVersion(null); }}>确认重置演示</button><button onClick={() => setResetRequested(false)}>保留演示</button></div></div>}<p><a href="/">返回日常工作台</a></p></details>
    </main>
  </div>;
}
