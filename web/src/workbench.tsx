import { downloadFile } from './api';
import React, { useEffect, useRef, useState } from 'react';
import { useDialog } from './shell';
import { ConflictReview, DraftConflictError, useDirty, useNavigation } from './editing';

type Obj = Record<string, any>;
const stages: Record<string, string> = { ingest: '理解材料', brainstorm: '探索方案', clarify: '澄清规则', change: '修改方案', ui: '生成页面原型', prd: '生成需求文档', review: '审查当前版本', vision: '理解图片' };
const kinds: Record<string, string> = { goal: '本期目标', actor: '目标用户', requirement: '需求', rule: '规则', acceptance: '验收条件', non_goal: '暂不包含', constraint: '约束与保持不变' };
const applicability: Record<string, string> = { as_is: '平台现状', to_be: '本期目标 / 约束', reference: '参考内容' };
const status: Record<string, string> = { candidate: '候选', selected: '草稿已采纳', deferred: '暂缓', rejected: '未采纳', open: '待回答', answered: '已回答' };

function CandidateDetails({item,project}:{item:Obj;project:Obj}) {
  const target=project.items.find((i:Obj)=>i.id===item.target_item_id);
  const labels:Obj={actor:'谁操作',entry:'入口及前提',flow:'主要流程',data:'业务数据',permissions:'权限',result:'操作结果',exceptions:'边界和异常'};
  return <details><summary>{target?`修订 ${target.id} · 采纳后保留原编号`:'查看行为、关联与来源'}</summary>
    {target&&<p><b>原条款：</b>{target.statement}</p>}
    {Object.entries(labels).map(([key,label])=>item.behavior?.[key]&&<p key={key}><b>{String(label)}：</b>{item.behavior[key]}{target&&target.behavior?.[key]!==item.behavior[key]&&<small className="preserve-lines">原内容：{target.behavior?.[key]||'未填写'}</small>}</p>)}
    <p>关联：{(item.related_refs||[]).join('、')||'尚未关联'}</p><p>来源：{(item.source_refs||[]).map((r:Obj)=>project.sources.find((s:Obj)=>s.id===r.source_id)?.title||r.source_id).filter((s:string,n:number,a:string[])=>a.indexOf(s)===n).join('、')||'未提供'}</p>
  </details>;
}

export function ArtifactTabs({ busy = false, project, tab, setTab, onOption, onItem, onEditItem, onStage, onActivate, onReject, onNavigate, previewUrl, runs, models, onCancel, onResume, embedded = false, onSource, onPreviewOption }: { embedded?: boolean; onSource?: (refs: Obj[]) => void; onPreviewOption?: (option: Obj) => void; busy?: boolean; project: Obj; tab: string; setTab: (s: string) => void; onOption: (option: Obj, status: string) => void; onItem: (item: Obj, status: string) => void; onEditItem: (item: Obj, statement: string, fields?: Obj) => Promise<unknown>; onStage: (s: string) => void; onActivate: (id: string) => void; onReject: (id: string) => void; onNavigate: (tab: string) => void; previewUrl: string; runs: Obj[]; models: Obj; onCancel: (id: string) => void; onResume: (id: string) => void }) {
  const [editing, setEditing] = useState<Obj | null>(null), [editText, setEditText] = useState(''), [editTitle,setEditTitle]=useState(''), [editKind,setEditKind]=useState(''), [editError, setEditError] = useState(''),[conflict,setConflict]=useState<DraftConflictError|null>(null);
  const guard=useNavigation(),[saving,setSaving]=useState(false);
  async function saveEdit(){if(!editing)return;setSaving(true);try{await onEditItem(editing,editText,{title:editTitle,expected_revision:editing.edit_revision,__edit:{kind:'item',id:editing.id,before:{title:editing.title,statement:editing.statement,change_type:editing.change_type||''},mine:{title:editTitle,statement:editText,change_type:editKind}},...(editKind?{change_type:editKind}:{})});setEditing(null);setConflict(null);}catch(e){if(e instanceof DraftConflictError)setConflict(e);throw e;}finally{setSaving(false);}}
  const dirty=!!editing&&(editText!==editing.statement||editTitle!==editing.title||editKind!==(editing.change_type||''));
  useDirty(project.id+'-item-edit',dirty,'需求原文修改',saveEdit,()=>setEditing(null));
  useDialog(Boolean(editing),()=>{if(!saving)guard(()=>setEditing(null));});
  const [idQuery,setIdQuery]=useState('');
  const selected = project.items.filter((i: Obj) => i.selection_status === 'selected');
  const groups = ['actor', 'goal', 'constraint', 'non_goal', 'requirement', 'rule', 'acceptance'];
  return <section className="artifact-panel"><div hidden={embedded} className="artifact-tabs" role="tablist" aria-label="工作成果">{[['summary', '需求摘要'], ['options', '方案对比'], ['document', 'PRD 草稿']].map(([id, label]) => <button role="tab" aria-selected={tab === id} key={id} className={tab === id ? 'active' : ''} onClick={() => setTab(id)}>{label}</button>)}</div>
    <div className="artifact-scroll">
      {tab === 'summary' && <><div className="section-heading"><h2>需求摘要</h2><span>草稿 v{project.revision}</span></div>{!project.items.length && <div className="empty">底稿尚无条目。先添加材料并运行理解阶段。</div>}{(['as_is', 'reference'] as const).map(scope => { const items = selected.filter((i: Obj) => i.applies_to === scope); return items.length > 0 && <article className="card" key={scope}><h3>{applicability[scope]}（不计入本期新增范围）</h3>{items.map((i: Obj) => <div key={i.id} className="summary-item"><strong>{i.id}｜{i.title} · v{i.content_version||1}</strong><p>{i.statement}</p><small>{i.id} · {status[i.selection_status]} · {applicability[scope]}</small><div><button onClick={() => onSource ? onSource(i.source_refs || []) : onNavigate('sources')}>查看来源</button><button onClick={() => { setEditing({...i,edit_revision:project.revision}); setEditText(i.statement); setEditTitle(i.title); setEditKind(i.change_type||''); }}>编辑原文</button></div></div>)}</article>; })}{groups.map(kind => { const items = selected.filter((i: Obj) => i.kind === kind && i.applies_to === 'to_be'); return items.length > 0 && <article className="card" key={kind}><h3>{kinds[kind]}</h3>{items.map((i: Obj) => <div key={i.id} className="summary-item"><strong>{i.id}｜{i.title} · v{i.content_version||1}</strong><p>{i.statement}</p><small>{i.id} · 来源 {i.source_refs?.map((s: Obj) => s.excerpt_id).join('、') || '待核对'}</small><div><button onClick={() => onSource ? onSource(i.source_refs || []) : onNavigate('sources')}>查看来源</button><button onClick={() => { setEditing({...i,edit_revision:project.revision}); setEditText(i.statement); setEditTitle(i.title); setEditKind(i.change_type||''); }}>编辑原文</button></div></div>)}</article>; })}{project.items.filter((i: Obj) => i.selection_status === 'candidate').length > 0 && <article className="card"><h3>待采纳候选</h3>{project.items.filter((i: Obj) => i.selection_status === 'candidate').map((i: Obj) => <div key={i.id} className="summary-item"><strong>{i.id}｜{i.title} · v{i.content_version||1}</strong><p>{i.statement}</p><small>{applicability[i.applies_to] || i.applies_to} · {({reported:'材料陈述',inferred:'推断，待核对',proposed:'建议，非已定规则'} as Obj)[i.epistemic_status]||i.epistemic_status}</small><CandidateDetails item={i} project={project}/><div className="toolbar"><button disabled={busy} onClick={() => onItem(i, 'selected')}>采纳</button><button disabled={busy} onClick={() => onItem(i, 'deferred')}>暂缓</button></div></div>)}</article>}<label>按需求编号定位<input value={idQuery} onChange={e=>setIdQuery(e.target.value)} placeholder="输入 REQ 编号"/></label><details open={!!idQuery || undefined}><summary>查看全部底稿条目与状态</summary>{project.items.filter((i: Obj)=>!idQuery||i.id.toLowerCase().includes(idQuery.toLowerCase())).map((i: Obj) => <div key={i.id} className="summary-item"><strong>{i.id} · {i.title} · {status[i.selection_status]} · {applicability[i.applies_to] || i.applies_to}</strong><p>{i.statement}</p><select disabled={busy} aria-label={'采纳状态 ' + i.title} value={i.selection_status} onChange={e => onItem(i, e.target.value)}>{['candidate', 'selected', 'deferred', 'rejected'].map(value => <option key={value} value={value}>{status[value]}</option>)}</select><button onClick={() => { setEditing({...i,edit_revision:project.revision}); setEditText(i.statement); setEditTitle(i.title); setEditKind(i.change_type||''); }}>编辑原文</button></div>)}</details></>}
      {tab === 'options' && <><div className="section-heading"><h2>方案对比</h2><button onClick={() => onStage('brainstorm')}>探索方案</button></div>{!project.options.length && <div className="empty">尚无方案。数量由分析结果决定。</div>}{project.options.map((o: Obj) => <article className="card" key={o.id}><div className="section-heading"><h3>{o.name}</h3><span className="pill">{o.direction_status ? '讨论方向：' + (o.direction_status === 'selected' ? '已选定' : status[o.direction_status]) : o.selection_status !== 'candidate' ? '历史整包操作：' + status[o.selection_status] : '尚未选方向'}</span></div><p><strong>最小范围：</strong>{o.mvp_scope?.join("；") || "尚待说明"}</p><details><summary>查看完整路径、收益与风险</summary><p><strong>用户路径</strong><br />{o.user_path}</p>{[['benefits', '收益'], ['costs', '代价'], ['risks', '风险'], ['open_questions', '待定事项']].map(([key, label]) => <p key={key}><strong>{label}：</strong>{o[key]?.join('；') || '未列出'}</p>)}<small>关联条目：{o.proposed_item_refs?.join('、') || '无'}</small></details>{project.questions.some((q: Obj) => q.blocking && q.status !== 'answered') && <p className="notice">关键业务规则仍待决定；此方向仅供讨论，不能视为可开发方案或正式确认。</p>}<div className="toolbar">{onPreviewOption&&<button onClick={() => onPreviewOption(o)}>查看方向说明</button>}<button onClick={() => onOption(o, 'selected')}>按此方向继续讨论</button><button onClick={() => onOption(o, 'items')}>采纳指定条目</button><button onClick={() => onOption(o, 'deferred')}>暂缓方向</button><button onClick={() => onOption(o, 'rejected')}>拒绝方向</button></div></article>)}</>}
      {tab === 'document' && <><div className="section-heading"><h2>PRD 草稿</h2><button onClick={() => onStage('prd')}>生成或重新生成</button></div>{project.documents.prd ? <DocumentViewer document={project.documents.prd} items={project.items} currentRevision={project.revision} currentBriefHash={project.hashes.brief_hash} updateNeeded={project.stale_document_kinds?.includes('prd')} readiness={project.document_export_readiness?.prd} downloadRoot={previewUrl.replace('/prototype', '')} /> : <div className="empty">尚未生成 PRD。生成是明确的模型任务。</div>}</>}
      <details className="task-details"><summary>任务详情与接收端</summary><p>主分析接收端：{models.model?.name} · {models.model?.base_url} · Key {models.model?.key_configured ? '已配置' : '未配置'}</p>{runs.slice(-5).reverse().map((r: Obj) => <div key={r.id} className="card"><strong>{stages[r.stage]} · {r.status}</strong><p>{r.error ? r.message : `调用 ${r.calls} 次 · 费用 ${r.cost ?? '未知'}`}</p>{['queued', 'running'].includes(r.status) ? <button onClick={() => onCancel(r.id)}>取消后续请求</button> : ['failed', 'paused_budget', 'cancelled'].includes(r.status) && <button onClick={() => onResume(r.id)}>继续此阶段（可能计费）</button>}</div>)}</details>
    </div>{editing && <div className="modal-backdrop"><form className="modal" role="dialog" aria-modal="true" aria-label="编辑需求原文" onSubmit={async e => { e.preventDefault(); setEditError(''); try { await saveEdit(); } catch(error) { setEditError((error as Error).message); } }}><h2>编辑需求原文</h2><label>需求名称<input value={editTitle} onChange={e=>setEditTitle(e.target.value)} required/></label><label>本次业务改动性质<select value={editKind} onChange={e=>setEditKind(e.target.value)}><option value="">未指定</option><option value="new">新增功能</option><option value="modified">修改现有功能</option><option value="preserved">保持约束</option><option value="existing">现状</option></select></label>{editError&&!conflict && <p role="alert" className="error">{editError}</p>}<ConflictReview conflict={conflict} onReviewed={()=>{setEditing({...editing,...(conflict?.latest as Obj),edit_revision:project.revision});setConflict(null);setEditError('');}}/><p>保存后形成新草稿，旧基线保持不变。</p><textarea aria-label="需求原文" value={editText} onChange={e => setEditText(e.target.value)} required /><div className="toolbar"><button type="button" disabled={saving} onClick={()=>guard(()=>setEditing(null))}>取消</button><button disabled={saving} className="primary">保存原文</button></div></form></div>}</section>;
}

export function PrototypeCanvas({ project, url, onStage, onActivate, onReject, compact=false }: { compact?:boolean; project: Obj; url: string; onStage: (s: string) => void; onActivate?: (id: string) => void; onReject?: (id: string) => void }) {
  const [expanded, setExpanded] = useState(false);
  return <div className={(expanded ? 'prototype-canvas expanded' : 'prototype-canvas')+(compact?' compact-canvas':'')}><div className="section-heading"><div className="canvas-heading"><h2>功能草图画布</h2><small>需求草图，非最终 UI 设计 · 模拟数据 · 不连接业务系统</small></div><div className="toolbar"><button onClick={() => setExpanded(!expanded)}>{expanded ? '收起画布' : '展开画布'}</button><button onClick={() => onStage('ui')}>生成草图候选</button></div></div>
    {project.ui ? <><p className="notice">UI v{project.ui.spec.draft_revision} · 对应底稿 v{project.ui.verification?.draft_revision || project.ui.spec.draft_revision}{project.document_update_needed ? ' · 文档及审查需要更新' : ''}</p><iframe key={project.hashes.ui_spec_hash} title="低保真页面预览" sandbox="allow-scripts" src={url + '?content=' + project.hashes.ui_spec_hash} className="prototype" /><details><summary>设计意图与关联</summary><p>{project.ui.spec.design_intent}</p>{project.ui.spec.pages.map((page: Obj) => <p key={page.page_id}>{page.title}：{page.requirement_refs.join('、')}</p>)}</details>
      {(project.ui_candidates || []).filter((c: Obj) => c.status === 'candidate').map((candidate: Obj) => <article className="card" key={candidate.id}><h3>待比较的页面候选</h3><p>原设计意图：{project.ui.spec.design_intent}</p><p>候选设计意图：{candidate.spec.design_intent}</p><p>页面布局差异：{candidate.spec.pages.map((page: Obj) => page.title + '（' + page.regions.map((region: Obj) => region.components.map((component: Obj) => component.type + ' ' + component.label).join('、')).join('；') + '）').join('；')}</p><p className="notice">保持不变：当前底稿中的本期规则与验收条目。候选激活不会修改这些条目；若规则需变更，请返回需求澄清。</p><iframe title="候选页面预览" sandbox="allow-scripts" src={url.replace('/prototype', '/ui-candidates/' + candidate.id + '/prototype')} className="prototype" /><div className="toolbar"><button onClick={() => onReject?.(candidate.id)}>保留旧方案</button><button className="primary" onClick={() => onActivate?.(candidate.id)}>采纳候选并建立新草稿</button></div></article>)}
    </> : <div className="empty">尚未生成业务页面。画布不使用固定业务示例。</div>}
  </div>;
}

const readingPositions=new Map<string,{top:number;selected:string}>();
function ReadingNode({node, documentId, help}:{node:Obj;documentId:string;help?:()=>void}) {
  if(node.kind==='heading') {
    const depth=node.level+1;
    const title=<><span className="chapter-number">{node.number ? node.number+' ' : ''}</span>{node.text}</>;
    const props={id:documentId+'-'+node.id,className:'reading-heading reading-level-'+Math.min(node.level,3),'data-outline-id':node.id,'data-item-kind':node.item_kind};
    return <div className="reading-heading-row">{depth<=6 ? React.createElement('h'+depth,props,title) : <div {...props} role="heading" aria-level={depth}>{title}</div>}{help&&<button onClick={help}>讨论此章节</button>}</div>;
  }
  return <p className={node.kind==='metadata'?'reading-meta':'reading-paragraph'} data-item-id={node.item_id} data-block-kind={node.block_kind}>{node.text}</p>;
}
export function DocumentViewer({ document, items, currentRevision, currentBriefHash, updateNeeded = false, historical = false, downloadRoot, readiness, onClarify, onHelp }: { onHelp?:(section:Obj)=>void; document: Obj; items: Obj[]; currentRevision: number; currentBriefHash: string; updateNeeded?: boolean; historical?: boolean; downloadRoot: string; readiness?: Obj; onClarify?: () => void }) {
  const [downloadError,setDownloadError]=useState(''),[downloading,setDownloading]=useState(false);
  const content = document.reader || document.content;
  const outline:Obj[]=content.outline || content.sections.map((s:Obj)=>({id:s.section_id,text:s.title,level:s.level}));
  const [selected, setSelected] = useState(readingPositions.get(document.id)?.selected||outline[0]?.id);
  const readerRoot=useRef<HTMLDivElement|null>(null),selectedRef=useRef(selected);selectedRef.current=selected;
  useEffect(()=>{
    const scroller=readerRoot.current?.querySelector('.document-reader');if(!scroller)return;
    const saved=readingPositions.get(document.id);if(saved)scroller.scrollTop=saved.top;
    const save=()=>{
      const headings=Array.from(scroller.querySelectorAll<HTMLElement>('[data-outline-id]'));
      const top=scroller.getBoundingClientRect().top+48;
      const active=headings.filter(h=>h.getBoundingClientRect().top<=top).at(-1)||headings[0];
      if(active){selectedRef.current=active.dataset.outlineId!;setSelected(selectedRef.current);}
      readingPositions.set(document.id,{top:scroller.scrollTop,selected:selectedRef.current});
    };
    scroller.addEventListener('scroll',save);return()=>scroller.removeEventListener('scroll',save);
  },[document.id]);
  const jump=(id:string)=>{
    const scroller=readerRoot.current?.querySelector('.document-reader');
    const target=globalThis.document.getElementById(document.id+'-'+id);
    if(scroller&&target){scroller.scrollTop+=target.getBoundingClientRect().top-scroller.getBoundingClientRect().top-16;}
    selectedRef.current=id;setSelected(id);readingPositions.set(document.id,{top:scroller?.scrollTop||0,selected:id});
  };
  const stale = document.brief_hash !== currentBriefHash || updateNeeded;
  const snapshot: Obj[] = document.item_snapshot || (stale || historical ? [] : items);
  const questions: Obj[] = (readiness?.issues || []).filter((i: Obj) => i.code === 'CLARIFICATION_REQUIRED');
  const downloadable = !historical && !stale && readiness?.ready === true;
  return <div ref={readerRoot} className="document-layout" data-presentation-version={content.presentation_version||'legacy'}>
    <nav aria-label="文档目录"><h3>文档目录</h3>{outline.map(node=><button key={node.id} style={{paddingInlineStart:10+(node.level-1)*16}} className={selected===node.id?'active':''} aria-current={selected===node.id?'location':undefined} onClick={()=>jump(node.id)}>{node.number ? node.number+' ' : ''}{node.text}</button>)}</nav>
    <article className="document-reader" aria-label={content.document_type.toUpperCase()+' 正文'}><div className="document-controls">
    <p className={stale || historical ? 'notice' : 'muted'}>{historical ? '历史文档只读 · ' : stale ? '基于旧版本 · ' : ''}版本 v{document.draft_revision} · 需求名称：{document.requirement_name || document.content.title}</p>
    {!historical && questions.length > 0 && <aside className="notice" aria-label="下载前待澄清事项"><strong>还有 {questions.length} 项未澄清，暂不能下载</strong><p>请先回答以下问题，再更新文档。当前草稿仍可阅读。</p><ul>{questions.map(q => <li key={q.question_id}>{q.message}</li>)}</ul>{onClarify && <button onClick={onClarify}>去澄清这些问题</button>}</aside>}
    {!historical && stale && <p className="notice">需求或页面已更新，请使用“更新讨论稿”生成对应版本后下载。</p>}
    {downloadable && <div className="toolbar">{['docx', 'md', 'zip'].map(format => <button key={format} disabled={downloading} onClick={async()=>{setDownloading(true);setDownloadError('');try{await downloadFile(downloadRoot+'/documents/'+content.document_type+'/'+format+'?document_id='+encodeURIComponent(document.id),(document.requirement_name||content.title)+'.'+format);}catch(e){setDownloadError((e as Error).message);}finally{setDownloading(false);}}}>{format.toUpperCase()} 下载</button>)}</div>}
    {downloadError&&<p role="alert" className="error">{downloadError} · 原文档仍可阅读，核对后可重试下载。</p>}</div><h1 className="reading-title">{content.title}</h1><p className="reading-meta">需求评审稿 · 本文下载不代表已完成正式确认</p>
    {content.sections.map((section:Obj)=>{
      const nodes:Obj[]=section.reading_nodes || [{kind:'heading',id:section.section_id,text:section.title,level:section.level},...section.blocks.map((block:Obj)=>({kind:'paragraph',text:block.text||block.ref_ids.map((id:string)=>snapshot.find((i:Obj)=>i.id===id)?.statement||'旧版原文不可用').join('\n')}))];
      return <section key={section.section_id} className="document-section">{nodes.map((node,index)=><ReadingNode key={node.id||index} node={node} documentId={document.id} help={node.id===section.section_id&&onHelp?()=>onHelp(section):undefined}/>)}</section>;
    })}</article></div>;
}
