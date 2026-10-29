import { focusBlockerElement, firstActionableBlocker } from './blocker-navigation';
import React, { useEffect, useRef, useState } from 'react';
import { api, ApiError, sourceStatusNames, uploadFile, UploadProgress, UserTask } from './api';
import { DocumentViewer, readinessProblems } from './workbench';
import { useDialog } from './shell';
import { Icon } from './ui-icons';
import { DownloadButton } from './feedback';
import { SourceLibrary, sourcePurposes as sourcePurpose } from './source-library';
import { BusinessContextImport } from './business-context';
import { prewriteLabel } from './prewrite-review';
import './ux-reading.css';
type Obj = Record<string, any>;
const statusNames = sourceStatusNames;
const kindNames: Obj = { requirement: '需求', rule: '规则', acceptance: '验收', goal: '目标', actor: '角色', constraint: '约束' };
const formatBytes = (bytes: number) => bytes < 1024 * 1024 ? `${(bytes / 1024).toFixed(0)} KB` : `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
export function SourcePanel({ p, models, purpose, setPurpose, sourceText, setSourceText, manualPageActive, manualPageUrl, setManualPageUrl, manualPageTitle, setManualPageTitle, manualPageNotice, onSaveText, url, setUrl, dynamic, setDynamic, busy, act, error, mutate, refresh, root, pendingGrant, setPendingGrant, guided = false, focusRefs = [] }: { focusRefs?: Obj[]; guided?: boolean; p: Obj; models: Obj; purpose: string; setPurpose: (s: string) => void; sourceText: string; setSourceText: React.Dispatch<React.SetStateAction<string>>; manualPageActive: boolean; manualPageUrl: string; setManualPageUrl: (url:string)=>void; manualPageTitle: string; setManualPageTitle: (title:string)=>void; manualPageNotice: string; onSaveText: ()=>Promise<void>; url: string; setUrl: React.Dispatch<React.SetStateAction<string>>; dynamic: boolean; setDynamic: (v: boolean) => void; busy: boolean; act: (fn: () => Promise<unknown>) => void; error?: string; mutate: (path: string, body: Obj, method?: string) => Promise<unknown>; refresh: () => Promise<void>; root: string; pendingGrant: Obj | null; setPendingGrant: (v: Obj | null) => void }) { const filePicker=useRef<HTMLInputElement>(null); const [reading, setReading] = useState(''),[urlIssue,setUrlIssue]=useState(''); const [importing,setImporting]=useState<Obj|null>(null),[elapsed,setElapsed]=useState(0),[grantBusy,setGrantBusy]=useState(false),[grantError,setGrantError]=useState(''); const grantLock=useRef(false),timer=useRef<number|undefined>(undefined),failedUrl=useRef(''); useDialog(Boolean(pendingGrant)&&!grantBusy, () => setPendingGrant(null)); React.useEffect(()=>()=>{if(timer.current)window.clearInterval(timer.current);},[]); const statusText=(source:Obj)=>statusNames[source?.parse_status]||source?.parse_status||'状态未知'; const uploadSelected=(file:File,input:HTMLInputElement)=>{if(importing&&(['upload','reading'].includes(importing.phase)||importing.refreshing))return;setElapsed(0);const job:Obj={name:file.name,phase:'upload',progress:0,loaded:0,total:undefined,result:null,error:'',refreshError:'',started:Date.now()};setImporting(job);const tick=()=>setElapsed(Math.floor((Date.now()-job.started)/1000));timer.current=window.setInterval(tick,1000);const data=new FormData();data.append('file',file);data.append('purpose',purpose);data.append('expected_revision',String(p.revision));act(async()=>{try{job.result=await uploadFile(root+'/sources/file',data,(progress:UploadProgress)=>{setImporting({...job,loaded:progress.loaded,total:progress.total,progress:progress.total?Math.min(100,Math.floor(progress.loaded/progress.total*100)):undefined});},()=>{job.phase='reading';setImporting({...job});});job.phase='done';job.refreshing=true;setImporting({...job});input.value='';try{await refresh();}catch{job.refreshError='文件读取结果已返回，但材料列表刷新失败；可在此重试刷新。';}finally{job.refreshing=false;setImporting({...job});}}catch(e){job.phase='failed';job.error=e instanceof Error?e.message:'导入请求失败；服务端是否已收到文件尚未确认。';job.refreshChecked=false;setImporting({...job});input.value='';}finally{if(timer.current)window.clearInterval(timer.current);timer.current=undefined;tick();}});}; return <><div className="section-heading"><h2>项目资料</h2><span>逐项保留读取状态和出处</span></div><p className="muted">支持 Word、Excel、PPT/PPTX、PDF、HTML 和图片；旧版 PPT 等 Office 文件可能需要本机 Office 只读提取。读取材料不会自动发送给模型。</p>{reading && <p role="status">{reading}：读取中，正在等待读取结果。</p>}{error && <p role="alert" className="error">{error}</p>}<SourceLibrary p={p} root={root} busy={busy} act={act} mutate={mutate} refresh={refresh} focusRefs={focusRefs}/><details className="source-add"><summary>添加资料 · 文档、截图、工程包、文字或网页</summary><details className="source-add-type"><summary>工程上下文 JSON</summary><BusinessContextImport key={root} root={root} revision={p.revision} refresh={refresh} busy={busy} /></details><details className="source-add-type" id={"source-file-add-"+p.id}><summary>文件、截图、文字或网页</summary><div className="two-column"><section><div className="card"><label>材料用途<select disabled={busy} value={purpose} onChange={e => setPurpose(e.target.value)}>{Object.entries(sourcePurpose).filter(([key]) => key !== 'business').map(([k, v]) => <option key={k} value={k}>{v as string}</option>)}</select></label></div>
<section className="source-import" aria-labelledby="source-import-title">
<div className="source-import__heading"><span className="source-import__icon" aria-hidden="true"><Icon name="upload"/></span><div><h3 id="source-import-title">导入项目资料</h3><p>选择原始文档或截图，提取内容并保留出处</p></div></div>
<p className="source-import__hint">支持 Word、Excel、PPT/PPTX、PDF、HTML/HTM、TXT、MD 和图片。HTML 仅提取静态文字；PDF 扫描页与图片不算已读取。原文件保留，读取限制会明确显示。</p>
<label className="source-import__picker">{importing?.phase==='upload'?'正在上传…':importing?.phase==='reading'?'文件已上传，正在读取…':importing?.phase==='done'?'选择另一份文件':'选择文件'}<input ref={filePicker} type="file" accept={p.source_capabilities?.extensions?.join(',')} disabled={busy||Boolean(importing&&(['upload','reading','failed','uncertain'].includes(importing.phase)||importing.refreshing))} onChange={e=>{const input=e.currentTarget,file=input.files?.[0];if(file)uploadSelected(file,input);}}/></label><button type="button" className="screenshot-import" disabled={busy||Boolean(importing&&['upload','reading','failed','uncertain'].includes(importing.phase))} onClick={()=>{setPurpose('reference');filePicker.current?.click();}}>导入墨刀原型截图</button><p className="muted">将墨刀页面截图保存为 PNG / JPG 后选择导入；此入口归入“设计参考”，不会自动采纳为业务规则。</p>
{importing&&<div className="source-import__status" role="status" aria-live="polite">
<div className="source-import__status-head"><b>{importing.name}</b><span>{importing.phase==='upload'?'正在上传':importing.phase==='reading'?'服务器正在读取':importing.phase==='failed'?'导入状态待核对':importing.phase==='uncertain'?'需要核对材料列表':'读取结果已返回'}</span></div>
{importing.phase==='upload'&&<><progress className="source-import__progress" aria-label="文件上传进度" max={100} value={importing.total?importing.progress:undefined}/><small>{importing.total?`${importing.progress}% · ${formatBytes(importing.loaded)} / ${formatBytes(importing.total)} 请求数据`:`已传输请求 ${formatBytes(importing.loaded)}；浏览器未提供总量，无法计算百分比。`}</small><p>请求数据传输完成后还需服务器读取，上传到 100% 不代表材料已读取。</p></>}
{importing.phase==='reading'&&<><progress className="source-import__progress" aria-label="文档读取进度"/><p>文件已上传，正在等待读取结果。普通解析失败时，Windows 本机 Office 可能接手只读提取；此阶段没有可测量的完成百分比。</p><small>已等待 {elapsed} 秒。解析可能需要较长时间，请保持此资料窗口打开。</small><p>导入处理中，请勿重复提交。</p></>}
{importing.phase==='failed'&&<><p role="alert" className="error">{importing.error}</p><p>尚未取得可确认的读取结果，服务端是否已保存尚未确认。请先刷新材料列表核对，避免重复提交。</p><button type="button" disabled={busy||importing.refreshing} onClick={()=>act(async()=>{setImporting({...importing,refreshing:true,refreshError:''});try{await refresh();setImporting({...importing,phase:'uncertain',refreshing:false,refreshChecked:true,refreshError:''});}catch{setImporting({...importing,refreshing:false,refreshError:'材料列表刷新失败，请重试；核对前请勿重新上传。'});}})}>{importing.refreshing?'正在刷新材料列表…':'刷新材料列表核对状态'}</button>{importing.refreshError&&<p role="alert" className="error">{importing.refreshError}</p>}</>}
{importing.phase==='uncertain'&&<><p>材料列表已刷新，请在下方项目资料中核对是否已出现“{importing.name}”。如果已出现，请勿重复上传；确认未出现后再重新选择文件。</p><button type="button" onClick={()=>{setImporting(null);setElapsed(0);}}>我已核对，可重新选择文件</button></>}
{importing.phase==='done'&&<><p className={['failed','permission_denied'].includes(importing.result?.parse_status)?'error':importing.result?.parse_status==='read'?'source-import__success':'notice'}>{statusText(importing.result)}{importing.result?.failure_reason?`：${importing.result.failure_reason}`:''}</p>{importing.result?.reading?.method==='local-office'&&<small>读取方式：本机 Office 只读提取。</small>}<small>上传与读取耗时 {elapsed} 秒。原件保留，可在材料列表查看来源片段。</small>{importing.refreshing&&<small>正在刷新材料列表…</small>}{importing.refreshError&&<><p role="alert" className="error">{importing.refreshError}</p><button type="button" disabled={importing.refreshing} onClick={()=>act(async()=>{setImporting({...importing,refreshing:true,refreshError:''});try{await refresh();setImporting({...importing,refreshing:false,refreshError:''});}catch{setImporting({...importing,refreshing:false,refreshError:'材料列表刷新仍未成功；读取结果已保留，可稍后重试刷新。'});}})}>重试刷新材料列表</button></>}</>}</div>}
</section>
<details className="source-intake__auxiliary" open={Boolean(sourceText||url||manualPageActive)}><summary>粘贴文字或添加公开网页（辅助方式）</summary><div className="source-intake__auxiliary-content">{manualPageNotice&&<p role="status">{manualPageNotice}</p>}{manualPageActive&&<><label>来源网址<input type="url" maxLength={2000} value={manualPageUrl} onChange={e=>setManualPageUrl(e.target.value)}/></label><label>页面标题<input maxLength={180} value={manualPageTitle} onChange={e=>setManualPageTitle(e.target.value)} placeholder="请填写实际页面标题"/></label><small>这份材料由你粘贴；网址只用于记录出处，系统没有读取该网页正文。</small></>}<textarea id={'source-paste-'+p.id} aria-label="粘贴材料" value={sourceText} onChange={e => setSourceText(e.target.value)} placeholder="粘贴零散材料，保留原文……" /><div className="toolbar"><button disabled={!sourceText || busy} onClick={() => act(onSaveText)}>保存文字材料</button></div><div className="card"><label>公开网页 URL<input value={url} onChange={e => {setUrl(e.target.value);setUrlIssue('');}} placeholder="https://…" /></label><label className="check"><input type="checkbox" checked={dynamic} onChange={e => {setDynamic(e.target.checked);setUrlIssue('');}} />需要动态页面渲染</label><button disabled={!url || busy} onClick={() => act(async () => { const submitted=url; if(failedUrl.current===submitted||p.sources.some((source:Obj)=>source.uri===submitted&&source.parse_status==='failed')){setUrlIssue('此网址已有读取失败记录，请修改网址或在下方重新读取。');return;} const source=await mutate('/sources/url', { url:submitted, dynamic, authorized_public: true, purpose }) as Obj; if(source.parse_status==='failed'){failedUrl.current=submitted;setUrlIssue('网址已记录，但读取失败；输入已保留。请查看下方失败原因或重新读取。');return;} failedUrl.current='';setUrlIssue('');setUrl(current=>current===submitted?'':current); })}>确认有权读取并添加</button><small>只读取公开网页；失败时可改用截图或导出文件。</small>{urlIssue&&<p role="alert" className="error">{urlIssue}</p>}</div></div></details></section>{!guided && <aside><div className="card"><h3>材料发送授权</h3><p>材料会发送到所选模型接收端。先排除不应发送的材料，再授权当前范围。</p>{['model', 'vision'].map(slot => <div key={slot}><p>{slot === 'model' ? '主分析' : '视觉分析'}：{models[slot]?.base_url}</p><button onClick={() => setPendingGrant({ slot, origin: models[slot]?.base_url, source_ids: p.sources.filter((s: Obj) => !s.excluded).map((s: Obj) => s.id) })}>授权此接收端与当前材料</button></div>)}<small>新增材料后需要再次确认范围；密钥仅用于绑定接收端。</small></div></aside>}</div></details></details>{pendingGrant && <div className="modal-backdrop"><div className="modal" role="dialog" aria-modal="true" aria-label="核对材料发送授权"><h2>核对材料发送范围</h2><p>接收端：{models[pendingGrant.slot]?.name} · {models[pendingGrant.slot]?.model} · {pendingGrant.origin}</p><p>材料：{pendingGrant.source_ids.map((id: string) => p.sources.find((s: Obj) => s.id === id)?.title || id).join('、') || '当前没有未排除材料'}</p>{grantError&&<p role="alert" className="error">{grantError}</p>}<div className="toolbar"><button disabled={grantBusy} onClick={() => setPendingGrant(null)}>取消</button><button className="primary" disabled={grantBusy||busy} onClick={() => {if(grantLock.current)return;grantLock.current=true;setGrantBusy(true);setGrantError('');void (async()=>{try{await mutate('/grants', { slot: pendingGrant.slot, source_ids: pendingGrant.source_ids });setPendingGrant(null);}catch(e){setGrantError((e as Error).message);}finally{grantLock.current=false;setGrantBusy(false);}})();}}>{grantBusy?'正在授权…':'授权当前范围'}</button></div></div></div>}</>; }
export function VersionHistory({ history }: { history: Obj[] }) { return <><h2>版本记录与差异依据</h2><p className="muted">每次人工编辑及模型候选写入保留快照，旧基线不被覆盖。</p>{history.map((h: Obj, index: number) => { const snap = JSON.parse(h.payload), older = history[index + 1] ? JSON.parse(history[index + 1].payload) : null; return <article className="card" key={h.revision}><h3>v{h.revision} · {h.reason}</h3><small>{h.created}</small><details><summary>查看条目与上版变化</summary>{snap.items.map((i: Obj) => { const prev = older?.items.find((x: Obj) => x.id === i.id); return <div key={i.id}><b>{i.title}</b>{prev && prev.statement !== i.statement && <p className="muted">原：{prev.statement}</p>}<p>{prev ? '当前' : '新增'}：{i.statement}</p></div>; })}</details></article>; })}</>; }
export function ConfirmationPanel({ p, busy, setBusy, setError, setTab, setStage, setShowAdvanced, root, act, refresh, onReview, onResolveReview, onClarify, onOpenDocument, onResolveBlocker }: { onResolveBlocker?: (action: import('./api').BlockerAction, row: import('./api').Blocker) => void; onOpenDocument?: (kind:string) => void; onReview?: () => void; onResolveReview?: () => void; onClarify?:(id:string)=>void; p: Obj; busy: boolean; setBusy: (v: boolean) => void; setError: (s: string) => void; setTab: (s: string) => void; setStage: (s: string) => void; setShowAdvanced: (v: boolean) => void; root: string; act: (fn: () => Promise<unknown>) => void; refresh: () => Promise<void> }) { const [confirmationRequest, setConfirmationRequest] = useState<Obj | null>(null); const [confirmationError, setConfirmationError] = useState(''),[exporting,setExporting]=useState<string|null>(null);const confirmationLock=useRef(false),exportLock=useRef(false); useDialog(Boolean(confirmationRequest)&&!busy, () => setConfirmationRequest(null)); return <><div className="section-heading"><div><p className="eyebrow">确认与交接</p><h2>核对指定版本</h2></div><span>内容确认不代表开发或测试验收</span></div><div className="two-column"><section><article className="card"><h3>本期范围、规则与验收</h3>{p.items.filter((i: Obj) => p.confirmation_scope_ids ? p.confirmation_scope_ids.includes(i.id) : i.selection_status === 'selected' && i.applies_to === 'to_be').map((i: Obj) => <p key={i.id}><b>{kindNames[i.kind]} · {i.title}</b><small>{i.id} · v{i.content_version||1}</small><br />{i.statement}</p>)}</article><article className="card"><h3>重要待定与门禁问题</h3>{p.questions.filter((q: Obj) => !q.superseded_by && q.blocking && q.status !== 'answered' && !q.out_of_scope_reason).map((q: Obj) => <p key={q.id} className="notice">{q.question}<button type="button" disabled={busy} onClick={()=>onClarify?.(q.id)}>去回答此问题</button></p>)}{p.confirmation_issues.length ? <p className="notice">还有 {p.confirmation_issues.length} 项交接条件待处理。<button type="button" disabled={busy} onClick={()=>focusBlockerElement('blockers-'+p.id+'-5')}>查看阻塞清单与处理入口 ↑</button></p> : <p>程序门禁已满足。请人工核对业务意图与交付范围。</p>}<button onClick={() => { if(onReview) onReview(); else { setStage('review'); setShowAdvanced(true); setTab('conversation'); } }}>审查当前版本</button></article></section><aside><article className="card"><h3>当前确认对象</h3><p>当前底稿 v{p.revision}</p>{['mrd','prd'].map(k=><div className="delivery-version" key={k}><b>{k.toUpperCase()}</b><p>{p.documents[k]?`文档 v${p.documents[k].document_version||'未记录'} · 底稿 v${p.documents[k].draft_revision}`:'尚未生成'}</p><small>{p.document_review_status?.[k]?.reviewed?'此版本已核对':'尚未核对或需重新核对'}</small><button onClick={()=>onOpenDocument ? onOpenDocument(k) : setTab('documents')}>查看文档</button></div>)}<details><summary>查看完整版本哈希</summary><pre>{JSON.stringify(p.hashes, null, 2)}</pre></details><p>确认后创建不可变基线。后续修改将成为新草稿。</p><button className="primary" disabled={busy || p.product_flow?.[5]?.complete} onClick={() => { if(p.confirmation_issues.length){const first=firstActionableBlocker(p as any);if(first&&onResolveBlocker)onResolveBlocker(first.action,first.row);else focusBlockerElement('blockers-'+p.id+'-5');return;}setConfirmationError(''); setConfirmationRequest({ expected_revision: p.revision, expected_hashes: p.hashes, scope_ids: p.items.filter((i: Obj) => p.confirmation_scope_ids ? p.confirmation_scope_ids.includes(i.id) : i.selection_status === 'selected' && i.applies_to === 'to_be').map((i: Obj) => i.id), idempotency_key: crypto.randomUUID(), scope_snapshot:p.items.filter((i:Obj)=>p.confirmation_scope_ids?.includes(i.id)).map((i:Obj)=>({id:i.id,title:i.title,statement:i.statement})), unknown_snapshot:p.questions.filter((q:Obj)=>q.blocking&&q.status!=='answered'&&!q.out_of_scope_reason).map((q:Obj)=>q.question) }); }}>{p.product_flow?.[5]?.complete?'此版本已确认':'确认此版本的需求与文档'}</button></article></aside></div><h2>已确认基线与交接</h2>{!p.baselines.length&&<div className="empty">尚未建立正式基线。完成上方复核后才会出现指定版本的交接入口。</div>}{p.baselines.map((b: Obj) => <article className="card" key={b.id}><h3>{b.id} <small>{b.id === p.active_baseline_id ? '当前确认基线' : '历史基线'}</small></h3><p>确认记录 {b.confirmation_id} · 底稿 v{b.project.revision}</p><button disabled={busy||Boolean(exporting)} onClick={() => {if(exportLock.current)return;exportLock.current=true;setExporting(b.id);act(async () => { try{await api(root + '/exports', 'POST', { baseline_id: b.id, idempotency_key: 'handoff-' + b.id });await refresh();}finally{exportLock.current=false;setExporting(null);} }); }}>{exporting===b.id?'正在生成交接包…':'生成此基线的研发交接包'}</button></article>)}{p.exports.map((e: Obj) => <p key={e.id}><DownloadButton href={'/api' + root + '/exports/' + e.id} filename={e.id+'.zip'}>下载交接包 · {e.baseline_id}</DownloadButton>{e.baseline_id !== p.active_baseline_id && '（历史版本）'}</p>)}{confirmationRequest && <div className="modal-backdrop"><div className="modal" role="dialog" aria-modal="true" aria-label="确认内容复核"><h2>再次核对本期范围与重要未知</h2>{confirmationError && <p role="alert" className="error">{confirmationError}</p>}<p>底稿 v{confirmationRequest.expected_revision} · 已选条目 {confirmationRequest.scope_ids.length} 项</p><ul>{confirmationRequest.scope_ids.map((id: string) => <li key={id}>{id} · {confirmationRequest.scope_snapshot.find((i:Obj)=>i.id===id)?.title || id}<p>{confirmationRequest.scope_snapshot.find((i:Obj)=>i.id===id)?.statement}</p></li>)}</ul><p>重要待定：{confirmationRequest.unknown_snapshot.join('；') || '无待答阻塞问题'}</p><p className="notice">提交后由服务端核对版本、哈希、范围和门禁，不会自动重放 409 请求。</p><div className="toolbar"><button disabled={busy} onClick={()=>{setConfirmationRequest(null);act(refresh);}}>返回核对</button><button className="primary" disabled={busy||Boolean(confirmationError)} onClick={async () => { if(confirmationLock.current)return;confirmationLock.current=true;setBusy(true);setError('');setConfirmationError('');let committed=false;try{const {scope_snapshot,unknown_snapshot,...body}=confirmationRequest;await api(root + '/confirmations', 'POST', body);committed=true;setConfirmationRequest(null);try{await refresh();}catch{setError('\u786e\u8ba4\u5df2\u63d0\u4ea4\uff0c\u4f46\u9875\u9762\u5237\u65b0\u5931\u8d25\uff1b\u8bf7\u624b\u52a8\u5237\u65b0\u6838\u5bf9\u57fa\u7ebf\u72b6\u6001\u3002');}}catch(e){if(!committed)setConfirmationError(e instanceof ApiError && e.status === 409 ? '版本已变化：' + e.message + '。请重新核对当前内容后再确认。' : (e as Error).message);}finally{confirmationLock.current=false;setBusy(false);}}}>{busy?'\u6b63\u5728\u63d0\u4ea4\u786e\u8ba4…':'\u63d0\u4ea4\u670d\u52a1\u7aef\u786e\u8ba4'}</button></div></div></div>}</>; }
// Bind to the submitted document type, never infer it from a translated label
// or from whichever tab happens to be open when the task finishes.
export function documentTaskForType(tasks: UserTask[], docType: string): UserTask | undefined {
  return tasks.filter(task => {
    const local=task as UserTask&{assembly_origin?:string;document_type?:string};
    const type=task.retry_input?.document_type||(local.assembly_origin==='saved_content'?local.document_type:undefined);
    return (task.action === 'document' || task.retry_input?.target?.kind === 'document')&&type===docType;
  }).at(-1);
}

export function DocumentStudio({
  focusReviewNonce = 0, taskStatus, discussionPanel, p, docType, setDocType, selectedDocId, setSelectedDocId, artifacts,
  setStage, setShowAdvanced, setTab, setOutcome, root, act, mutate,
  onGenerate, busy, onHelp, onSource, onClarify, onPrewrite,
}: {
  busy?: boolean;
  onHelp?: (target: Obj) => void;
  onSource?: (refs: Obj[]) => void;
  onClarify?: (questionId?: string) => void;
  onGenerate?: () => void;
  onPrewrite?: () => void;
  taskStatus?: React.ReactNode; discussionPanel?: React.ReactNode; focusReviewNonce?: number;
  p: Obj; docType: string; setDocType: (s: string) => void;
  selectedDocId: string; setSelectedDocId: (s: string) => void;
  artifacts: Obj; setStage: (s: string) => void; setShowAdvanced: (v: boolean) => void;
  setTab: (s: string) => void; setOutcome: (s: string) => void; root: string;
  act: (fn: () => Promise<unknown>) => void;
  mutate: (path: string, body: Obj, method?: string) => Promise<unknown>;
}) {
  const document = selectedDocId
    ? (artifacts.document_artifact || []).find((entry: Obj) => entry.id === selectedDocId)
    : p.documents[docType];
  const readiness = p.document_export_readiness?.[docType];
  const problems = readinessProblems(readiness);
  const unanswered = problems.filter((problem: Obj) => problem.action === 'answer' || problem.state === 'unanswered');
  const pending = problems.filter((problem: Obj) => problem.action === 'apply_answer' || problem.state === 'answer_pending');
  const clarify = (questionId?: string) => onClarify ? onClarify(questionId) : setTab('clarification');
  const stale = document && (document.brief_hash !== p.hashes.brief_hash || p.stale_document_kinds?.includes(docType));
  const reviewed = !selectedDocId && !stale && p.document_review_status?.[docType]?.reviewed;
  const findings: Obj[] = selectedDocId ? [] : p.review?.response?.findings || [];
  const reviewCurrent = Boolean(p.review_status?.current);
  const [locateMessage, setLocateMessage] = useState('');
  const [reviewOpen, setReviewOpen] = useState(!reviewed);
  const reviewDetails = useRef<HTMLDetailsElement>(null);
  const reviewSection = useRef<HTMLElement>(null);
  const reviewNeedsChanges = reviewCurrent && p.review?.response?.result?.assessment === 'needs_changes';
  const reviewWarnings = findings.filter(f => f.severity === 'warning' || f.severity === 'error').length;
  const reviewSuggestions = findings.filter(f => f.severity === 'info').length;
  useEffect(() => { setReviewOpen(!selectedDocId && (!reviewed || reviewNeedsChanges)); }, [document?.id, selectedDocId, reviewed, reviewNeedsChanges]);
  useEffect(() => {
    if (!focusReviewNonce || selectedDocId) return;
    setReviewOpen(true);
    if (reviewDetails.current) reviewDetails.current.open = true;
    reviewSection.current?.scrollIntoView({block: 'center'});
    reviewSection.current?.focus({preventScroll: true});
  }, [focusReviewNonce]);
  const findingIds = (finding: Obj): string[] => Array.from(new Set(
    [finding.item_id, ...(Array.isArray(finding.related_refs) ? finding.related_refs : [])]
      .filter((id): id is string => typeof id === 'string' && Boolean(id))
  ));
  const jumpToFinding = (finding: Obj, itemId?: string) => {
    const id = finding.section_id || finding.outline_id;
    const target = itemId
      ? Array.from(globalThis.document.querySelectorAll<HTMLElement>('.document-review-surface [data-item-id]'))
        .find(element => element.dataset.itemId === itemId)
      : id ? globalThis.document.getElementById(document.id + '-' + id) : null;
    if (target) { target.scrollIntoView({block: 'center', behavior: 'smooth'}); setLocateMessage(''); }
    else setLocateMessage('此文档版本中未找到关联条款；请核对意见编号和文档版本。');
  };
  return <>
    <div className="document-toolbar"><div className="toolbar">
      <div className="document-tabs" role="tablist" aria-label="文档类型">
        {['prd', 'mrd'].map(kind => <button role="tab" aria-selected={docType === kind} key={kind}
          onClick={() => { setDocType(kind); setSelectedDocId(''); }}>{kind.toUpperCase()} 评审稿</button>)}
      </div>
      <button id={'document-generate-'+p.id+'-'+docType} aria-describedby={'document-generate-help-'+p.id+'-'+docType} disabled={busy} onClick={() => {
        if (onGenerate) onGenerate();
        else { setStage('prd'); setShowAdvanced(true); setTab('conversation'); setOutcome('document'); }
      }}>{p.documents[docType] ? '准备更新' : '准备生成'} {docType.toUpperCase()}</button>
    </div>
      {document && <div className="document-review-action">
        <span>{docType.toUpperCase()} · 文档 v{document.document_version || 1} · {selectedDocId ? '历史版本只读' : stale ? '内容已变化，更新后再核对' : reviewed ? '此版本已核对' : '请阅读并核对当前版本'}</span>
        {!selectedDocId && <button id={'document-review-'+p.id+'-'+docType} disabled={busy || !p.product_flow?.[4]?.available || stale || reviewed}
              onClick={() => act(() => mutate('/documents/' + docType + '/review', {document_id: document.id}))}>
              我已核对这份 {docType.toUpperCase()}
            </button>}
      </div>}
    </div>
    {docType==='prd'&&!selectedDocId&&<div className="document-prewrite-notice"><p><b>{prewriteLabel(p.prewrite_review)}</b><br/>生成 PRD 前先检查当前需求；需要业务决定时，在完善需求中处理。</p><button type="button" disabled={busy} onClick={onPrewrite}>查看成文检查</button></div>}
    {document?.change_summary&&<section className="document-change-summary" aria-label="本版PRD变更摘要"><h3>本版变更摘要{selectedDocId?' · 历史只读':''}</h3><p>{document.change_summary.summary}</p><p>修改 {document.change_summary.changed_item_ids?.length||0} 项 · 新增 {document.change_summary.added_item_ids?.length||0} 项 · 移出 {document.change_summary.removed_item_ids?.length||0} 项</p>{!!document.change_summary.affected_sections?.length&&<><b>受影响章节</b><ul>{document.change_summary.affected_sections.map((section:Obj)=><li key={section.section_id}>{section.title||section.section_id}</li>)}</ul></>}<small>{document.base_document_id?'原文档和确认记录保留；本版需要重新核对。':'本版为初次成文，尚未继承任何文档确认。'}</small></section>}
    <details className="document-options"><summary>历史版本与章节参考设置</summary>
      <details><summary>章节参考设置</summary><label>章节参考方式
        <select aria-label="章节参考方式" value={p.reference_mode || 'user'}
          onChange={event => act(() => mutate('', {reference_mode: event.target.value}, 'PATCH'))}>
          <option value="user">用户原 Word 章节参考</option>
          <option value="builtin">明确改用内置参考（原件不可用时）</option>
        </select></label></details>
      <div className="toolbar"><label>查看文档版本<select aria-label="文档版本" value={selectedDocId}
        onChange={event => setSelectedDocId(event.target.value)}>
        <option value="">当前 {docType.toUpperCase()} 草稿</option>
        {(artifacts.document_artifact || []).filter((entry: Obj) => entry.content?.document_type === docType)
          .map((entry: Obj) => <option key={entry.id} value={entry.id}>
            {entry.requirement_name || entry.content.title} · v{entry.draft_revision} · {entry.created}
          </option>)}
      </select></label></div>
    </details>
    <p className="muted" id={'document-generate-help-'+p.id+'-'+docType}>先核对弹窗中的资料与任务范围，再点击“运行本次任务”（需要授权时为“授权本次范围并运行”），才会开始生成。</p>
    {taskStatus && <section aria-label={docType.toUpperCase()+' 生成任务状态'}>
      <small>最近一次 {docType.toUpperCase()} 生成或修改任务{selectedDocId ? ' · 当前正在查看历史文档' : ''}</small>
      {taskStatus}
    </section>}
    {!selectedDocId && discussionPanel}
    {document ? <div className="document-review-surface">
      <div className="document-reading-area" id={'document-reading-'+p.id+'-'+docType} tabIndex={-1}><DocumentViewer key={document.id}
        onHelp={selectedDocId ? undefined : (section: Obj) => onHelp?.({
          kind: 'document', id: document.id, section_id: section.section_id,
          label: docType.toUpperCase() + ' · ' + section.title,
        })}
        onSource={onSource} onClarify={clarify}
        document={document} items={p.items} currentRevision={p.revision}
        currentBriefHash={p.hashes.brief_hash} updateNeeded={p.stale_document_kinds?.includes(docType)}
        historical={Boolean(selectedDocId)} readiness={readiness} downloadRoot={'/api' + root} />
      </div>
      <details className="card review-sidebar" ref={reviewDetails} open={reviewOpen} onToggle={event => setReviewOpen(event.currentTarget.open)}><summary><Icon name="target"/> 评审与交付状态
        <small>{selectedDocId ? '历史只读' : reviewed ? '文档已核对' : '待核对'} · 澄清 {problems.length} 项
          {!selectedDocId && (reviewCurrent ? reviewNeedsChanges ? ` · 审查要求修改（${reviewWarnings} 条问题、${reviewSuggestions} 条建议）` : ` · 当前审查 ${findings.length} 条意见` : p.review ? ` · 历史审查 ${findings.length} 条意见，需更新` : ' · 内容尚未审查')}</small>
      </summary>
        <div className="review-sidebar-content">
          <p>依据已保存文档与实际检查状态核对。</p>
          <section className="review-focus"><h4>正式交付前需处理</h4>
            <p>{unanswered.length} 项待回答 · {pending.length} 条回答待核对纳入</p>
            {problems.map((problem: Obj, index: number) => <div className="review-problem" key={problem.question_id || index}>
              <b>{problem.title || problem.message}</b>
              <span>{problem.action === 'apply_answer' ? '回答待纳入条款' : '待回答'}</span>
              <button type="button" onClick={() => clarify(problem.question_id)}>
                {problem.action === 'apply_answer' ? '核对纳入' : '去回答'}
              </button>
              <details><summary>原因与编号</summary><p>{problem.message}</p>
                {problem.question_id && <small>{problem.question_id}</small>}</details>
            </div>)}
            {!problems.length && <p>当前准备状态未列出待处理澄清项。</p>}
          </section>
          <section className="review-focus" ref={reviewSection} tabIndex={-1} aria-label="当前版本内容审查"><h4>内容审查</h4>
            <p>{selectedDocId ? '历史文档只读；当前项目审查意见不用于定位此版本。' : p.review ? reviewCurrent ? '已有当前版本审查' : '已有历史审查，需重新核对' : '尚未执行模型内容审查'}</p>
            {locateMessage && <p role="status" className="notice">{locateMessage}</p>}
            {findings.map((finding: Obj, index: number) => {
              const ids = findingIds(finding);
              const linkedItems = ids.map(id => document.item_snapshot?.find((item: Obj) => item.id === id) || p.items.find((item: Obj) => item.id === id)).filter(Boolean);
              const linkedQuestions = ids.map(id => p.questions.find((question: Obj) => question.id === id)).filter(Boolean);
              const sourceRefs = linkedItems.flatMap((item: Obj) => item.source_refs || []);
              return <div className="review-finding" key={index}>
              <b>{finding.title || finding.message?.split(/[。；\n]/)[0] || '审查意见'}</b>
              <p>{finding.suggested_resolution || '请核对相关章节。'}</p>
              <div className="toolbar">
                {(finding.section_id || finding.outline_id) && <button type="button" onClick={() => jumpToFinding(finding)}>定位章节</button>}
                {linkedItems.map((item: Obj) => <button key={item.id} type="button" onClick={() => jumpToFinding(finding, item.id)}>定位关联条款：{item.title || item.id}</button>)}
                {linkedQuestions.map((question: Obj) => <button key={question.id} type="button" onClick={() => clarify(question.id)}>处理关联问题：{question.question}</button>)}
                {onSource && sourceRefs.length > 0 && <button type="button" onClick={() => onSource(sourceRefs)}>查看关联来源</button>}
              </div>
              {!ids.length && !finding.section_id && !finding.outline_id && <small>这条意见没有关联编号，请阅读完整意见后核对相关正文。</small>}
              <details><summary>查看完整意见与关联编号</summary><p>{finding.message}</p>
                {ids.length > 0 && <small>{ids.join('、')}</small>}</details>
            </div>;})}
          </section>
          <section className="review-focus"><h4>交付版本</h4>
            <p>{stale ? '基于旧内容，需要更新后核对。' : '对应当前底稿，正式确认在第五步进行。'}</p>
          </section>
          <div className="document-review-bar"><span>{selectedDocId ? '历史版本只读' : reviewed ? '此文档版本已核对' : '此文档版本尚未核对'}</span>

          </div>
        </div>
      </details>
    </div> : <div className="empty">尚未生成 {docType.toUpperCase()}。</div>}
  </>;
}
