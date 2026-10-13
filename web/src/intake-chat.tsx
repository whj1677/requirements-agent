import React, { useEffect, useRef, useState } from 'react';
import { api, ApiError, BenchmarkInput, JsonObject, Project, uploadFile, UserTask } from './api';
import { BenchmarkIntake } from './benchmark-intake.tsx';
import { useDirty } from './editing';
import { ChatReceipt, interviewSourceIds, sendInterview } from './interview-request';
import { extractChatAttachments } from './pasted-images';

const IMAGE_MESSAGE='请结合这些图片继续梳理需求。';
const ATTACHMENT_MESSAGE='请结合这些资料继续梳理需求。';
type PendingAttachment={id:string;name:string;type:string;file?:File;preview?:string;sourceId?:string;uploading?:boolean;parseStatus?:string};
const pendingAttachmentsByProject=new Map<string,PendingAttachment[]>();
export function hasPendingInterviewInput(projectId:string,includeText=true):boolean {
  if((includeText&&sessionStorage.getItem('ra-chat-draft-'+projectId)?.trim())||pendingAttachmentsByProject.get(projectId)?.length)return true;
  try{return (JSON.parse(sessionStorage.getItem('ra-chat-images-'+projectId)||'[]') as unknown[]).length>0;}catch{return false;}
}
function restoredAttachments(project:Project,key:string):PendingAttachment[]{
  try{
    const rows=JSON.parse(sessionStorage.getItem(key)||'[]');
    if(!Array.isArray(rows))return [];
    return rows.flatMap(row=>{
      const source=project.sources.find(s=>s.id===row.sourceId&&s.origin==='interview_attachment');
      return source&&typeof row.id==='string'?[{id:row.id,name:source.title||row.name||'已保存资料',type:source.image_mime||row.type||'',
        sourceId:source.id,parseStatus:source.parse_status,
        preview:source.image_mime?'/api/projects/'+project.id+'/sources/'+source.id+'/image':undefined}]:[];
    });
  }catch{return [];}
}

type Props={p:Project;model:Record<string,any>;busy:boolean;blocked:string;tasks:UserTask[];status:React.ReactNode;
  onContinue?:()=>void;continuing?:boolean;
  beforeSend?:()=>Promise<Project|void>;
  onPrepareInterview?:(message:string,currentProject:Project,attachmentSourceIds:string[])=>Promise<void>;
  onRefreshProject?:()=>Promise<Project>;
  onStarted:(task:UserTask)=>Promise<void>;onRefresh:()=>void;onSources:()=>void;onOrganize:()=>void;go:(step:number)=>void;
  onReferenceSources:(url?:string)=>void;onReferenceImport:(url:string,dynamic:boolean)=>Promise<JsonObject>;onBenchmark:(request:BenchmarkInput,message:string)=>Promise<void>;
  benchmarkSubmission?:{taskId:string;message:string};interviewSubmission?:{taskId:string;message:string;attachmentSourceIds:string[]};onDirectSave:(text:string)=>Promise<void>;directInputNonce?:number;onDirectEntry?:(text:string)=>void};
export function IntakeChat({p,model,busy,blocked,tasks,status,beforeSend,onPrepareInterview,onRefreshProject,onStarted,onRefresh,onSources,onOrganize,go,onReferenceSources,onReferenceImport,onBenchmark,benchmarkSubmission,interviewSubmission,onDirectSave,directInputNonce,onDirectEntry,onContinue,continuing}:Props){
  const draftKey='ra-chat-draft-'+p.id,receiptKey='ra-chat-receipt-'+p.id,imageKey='ra-chat-images-'+p.id;
  const [text,setText]=useState(()=>sessionStorage.getItem(draftKey)||''),[dirty,setDirty]=useState(false);
  const [sending,setSending]=useState(false),[error,setError]=useState('');
  const [direct,setDirect]=useState(false),[savingDirect,setSavingDirect]=useState(false);
  const [attachments,setAttachments]=useState<PendingAttachment[]>(()=>pendingAttachmentsByProject.get(p.id)||restoredAttachments(p,imageKey));
  const attachmentRef=useRef<PendingAttachment[]>(attachments);
  const setPendingAttachments=(next:PendingAttachment[])=>{
    attachmentRef.current=next;
    if(next.length)pendingAttachmentsByProject.set(p.id,next);else pendingAttachmentsByProject.delete(p.id);
    const saved=next.filter(a=>a.sourceId).map(a=>({id:a.id,sourceId:a.sourceId,name:a.name,type:a.type}));
    if(saved.length)sessionStorage.setItem(imageKey,JSON.stringify(saved));else sessionStorage.removeItem(imageKey);
    setAttachments(next);
  };
  const input=useRef<HTMLTextAreaElement|null>(null),fileInput=useRef<HTMLInputElement|null>(null),composer=useRef<HTMLDivElement|null>(null);
  const receipt=useRef<ChatReceipt|undefined>(undefined),locked=useRef(false),tail=useRef<HTMLDivElement|null>(null);
  useEffect(()=>{
    setText(sessionStorage.getItem(draftKey)||'');setDirty(false);setDirect(false);setError('');
    setPendingAttachments(pendingAttachmentsByProject.get(p.id)||restoredAttachments(p,imageKey));
  },[p.id]);
  useEffect(()=>{try{receipt.current=JSON.parse(sessionStorage.getItem(receiptKey)||'null')||undefined;}catch{sessionStorage.removeItem(receiptKey);}},[receiptKey]);
  async function uploadPendingAttachments(project:Project){
    let current=project;
    for(const attachment of attachmentRef.current){
      if(attachment.sourceId)continue;
      if(!attachment.file||!onRefreshProject)throw Error('附件尚未保存到项目，请重新选择后重试。');
      const body=new FormData();body.append('file',attachment.file);body.append('purpose','chat_attachment');body.append('expected_revision',String(current.revision));
      setPendingAttachments(attachmentRef.current.map(a=>a.id===attachment.id?{...a,uploading:true}:a));
      try{
        const source=await uploadFile<JsonObject>('/projects/'+p.id+'/sources/file',body,()=>{});
        if(!source.id)throw Error('附件已上传，但未取得资料 ID；请刷新项目资料核对后再发送。');
        setPendingAttachments(attachmentRef.current.map(a=>a.id===attachment.id?{...a,sourceId:source.id,parseStatus:source.parse_status,uploading:false}:a));
        current=await onRefreshProject();
      }catch(e){setPendingAttachments(attachmentRef.current.map(a=>a.id===attachment.id?{...a,uploading:false}:a));throw e;}
    }
    return current;
  }
  const saveDraft=async()=>{sessionStorage.setItem(draftKey,text);if(attachmentRef.current.some(a=>!a.sourceId)){if(!onRefreshProject)throw Error('无法保存待发送附件，请留在当前页面。');await uploadPendingAttachments(await onRefreshProject());}setDirty(false);};
  const clear=()=>{sessionStorage.removeItem(draftKey);sessionStorage.removeItem(receiptKey);receipt.current=undefined;attachmentRef.current.forEach(a=>{if(a.preview?.startsWith('blob:'))URL.revokeObjectURL(a.preview);});setPendingAttachments([]);setText('');setDirty(false);setError('');markSaved();};
  useEffect(()=>{if(benchmarkSubmission&&benchmarkSubmission.message===text)clear();},[benchmarkSubmission?.taskId]);
  useEffect(()=>{if(interviewSubmission&&interviewSubmission.message===text&&
    JSON.stringify(interviewSubmission.attachmentSourceIds.slice().sort())===JSON.stringify(attachmentRef.current.map(a=>a.sourceId).filter(Boolean).sort()))clear();},[interviewSubmission?.taskId]);
  const markSaved=useDirty(p.id+'-chat',dirty||attachments.some(a=>!a.sourceId),'需求输入与待发送附件（保存后留在本轮待发送区）',saveDraft,clear);
  function openDirect(){setDirect(true);if(!text.trim()){setText(p.product_context?.intent||(p as JsonObject).intake?.text||'');}setError('');}
  function skipInterview(){
    if(attachmentRef.current.length){setError('先发送或移除本轮附件，再切换到直接填写。');return;}
    if(!onDirectEntry){openDirect();return;}
    sessionStorage.setItem(draftKey,text);setDirty(false);markSaved();onDirectEntry(text);
  }
  useEffect(()=>{if(directInputNonce)openDirect();},[directInputNonce]);
  useEffect(()=>{if(direct){input.current?.focus();input.current?.scrollIntoView({block:'center'});}},[direct,directInputNonce]);
  async function saveDirect(){
    if(locked.current||busy||!text.trim())return;
    if(attachmentRef.current.length){setError('先发送或移除本轮附件，再保存直接填写内容。');return;}
    locked.current=true;setSavingDirect(true);setError('');
    try{await onDirectSave(text);clear();go(1);}catch(e){setError((e as Error).message);}finally{locked.current=false;setSavingDirect(false);}
  }
  useEffect(()=>{
    const key=receipt.current?.idempotency_key;
    if(key&&tasks.some(t=>(t as any).idempotency_key===key))clear();
  },[tasks]);
  const messages=p.messages.filter(m=>m.stage==='interview');
  const activeIds=(p as Project & {active_source_ids?:string[]}).active_source_ids;
  const activeSourceIds=Array.isArray(activeIds)?new Set(activeIds):null;
  const attachedSourceIds=new Set(attachments.map(a=>a.sourceId).filter((id):id is string=>Boolean(id)));
  const scopedSourceIds=new Set([...(activeSourceIds||[]),...attachedSourceIds]);
  const unsavedAttachmentCount=attachments.filter(a=>!a.sourceId).length;
  const pendingImageCount=(project:Project)=>{
    const ids=(project as Project & {active_source_ids?:string[]}).active_source_ids;
    if(!Array.isArray(ids))return 0;
    const active=new Set(ids);
    return project.sources.filter(s=>active.has(s.id)&&!s.excluded&&s.image_mime&&!s.vision_run_id).length;
  };
  const pendingImages=pendingImageCount(p);
  useEffect(()=>{
    const log=tail.current?.parentElement;
    if(log)log.scrollTop=log.scrollHeight;
    if(!direct)requestAnimationFrame(()=>composer.current?.scrollIntoView({block:'nearest'}));
  },[messages.length,sending,direct,attachments.length]);
  async function send(){
    if(locked.current||busy||blocked||(!text.trim()&&!attachmentRef.current.length))return;
    locked.current=true;setSending(true);setError('');
    try{
      const message=text.trim()?text:(attachmentRef.current.every(a=>a.type.startsWith('image/'))?IMAGE_MESSAGE:ATTACHMENT_MESSAGE);
      const current=await uploadPendingAttachments(await beforeSend?.() || p);
      const attachmentSourceIds=attachmentRef.current.map(a=>a.sourceId).filter((id):id is string=>Boolean(id));
      if(pendingImageCount(current)>0||attachmentSourceIds.length>0){
        if(!onPrepareInterview)throw Error('当前附件需核对处理范围后发送。');
        await onPrepareInterview(message,current,attachmentSourceIds);
        return;
      }
      const task=await sendInterview(api,'/projects/'+p.id,{action:'interview',expected_revision:current.revision,message,document_type:'prd',max_calls:null},
        {origin:String(model.bound_origin||''),model:String(model.model||'')},interviewSourceIds(current),
        r=>{receipt.current=r;sessionStorage.setItem(receiptKey,JSON.stringify(r));sessionStorage.setItem(draftKey,message);},receipt.current);
      clear();await onStarted(task);
    }catch(e){
      setError((e as Error).message);
      if(e instanceof ApiError&&e.status>0&&e.kind==='json'){receipt.current=undefined;sessionStorage.removeItem(receiptKey);}
      onRefresh();
    }finally{locked.current=false;setSending(false);}
  }
  function addAttachments(files:File[],rejected:string[]){
    if(rejected.length)setError(rejected.join('；'));
    if(!files.length)return;
    const next=[...attachmentRef.current,...files.map(file=>({id:crypto.randomUUID(),name:file.name,type:file.type,file,
      preview:file.type.startsWith('image/')?URL.createObjectURL(file):undefined}))];
    setPendingAttachments(next);
    setDirty(true);
    if(!text.trim()||text===IMAGE_MESSAGE||text===ATTACHMENT_MESSAGE){
      const message=next.every(a=>a.type.startsWith('image/'))?IMAGE_MESSAGE:ATTACHMENT_MESSAGE;
      setText(message);sessionStorage.setItem(draftKey,message);
    }
  }
  function pasteAttachments(event:React.ClipboardEvent<HTMLTextAreaElement>){
    if(direct||busy||sending||receipt.current)return;
    const result=extractChatAttachments(event.clipboardData);
    if(!result.files.length&&!result.rejected.length)return;
    event.preventDefault();
    addAttachments(result.files,result.rejected);
  }
  function dropAttachments(event:React.DragEvent<HTMLDivElement>){
    if(!event.dataTransfer.types.includes('Files'))return;
    event.preventDefault();
    if(direct||busy||sending||receipt.current)return;
    const result=extractChatAttachments(event.dataTransfer);
    addAttachments(result.files,result.rejected);
  }
  function removeAttachment(id:string){const removed=attachmentRef.current.find(a=>a.id===id);if(!removed)return;if(removed.preview?.startsWith('blob:'))URL.revokeObjectURL(removed.preview);const next=attachmentRef.current.filter(a=>a.id!==id);setPendingAttachments(next);
    if(text===IMAGE_MESSAGE||text===ATTACHMENT_MESSAGE){const message=next.length?(next.every(a=>a.type.startsWith('image/'))?IMAGE_MESSAGE:ATTACHMENT_MESSAGE):'';setText(message);if(message)sessionStorage.setItem(draftKey,message);else sessionStorage.removeItem(draftKey);}}
  function readStatus(status?:string){return ({read:'已读取',partial:'部分读取',failed:'读取失败',office_required:'需本机 Office 处理',requires_office:'需本机 Office 处理',permission_denied:'权限受限',awaiting_vision:'图片待识别',reading:'读取中'} as Record<string,string>)[status||'']||'读取状态待核对';}
  function attachmentStatus(a:PendingAttachment){return a.uploading?'正在保存到项目':a.sourceId?readStatus(p.sources.find(s=>s.id===a.sourceId)?.parse_status||a.parseStatus):'待发送';}
  function historyAttachment(sid:string){
    const source=p.sources.find(s=>s.id===sid);
    return <div className="chat-history-attachment" key={sid}>{source?.image_mime?<img src={'/api/projects/'+p.id+'/sources/'+sid+'/image'} alt=""/>:<span className="chat-file-icon" aria-hidden="true">文件</span>}
      <span>{source?.title||`资料 ${sid} 暂不可查看`}{source&&` · ${readStatus(source.parse_status)}`}</span></div>;
  }
  const hasAnalysis=p.messages.some(m=>m.stage==='ingest'&&m.role==='assistant')||p.items.length>0;
  return <article className="card intake-chat" aria-label="需求聊天">
    <div className="chat-intro"><h2>{direct?'直接填写本次需求':'先聊聊你想做什么'}</h2><p>{direct?'写下已有信息，保存到项目后进入草稿核对。未知信息留待后续澄清，此操作不调用模型。':'一句想法、一个困扰或一份参考资料都可以。不知道的地方，我们一起弄清楚。'}</p></div>
    {!direct&&<div className="intake-entry-choice"><p>助手对话可选。已有明确需求，可以直接填写并核对，无需先与 AI 对话。</p><button type="button" disabled={busy||sending||!!receipt.current||attachments.length>0} onClick={skipInterview}>{onDirectEntry?'跳过对话，直接填写需求 →':'直接填写并保存输入'}</button></div>}
    <div className="chat-messages" role="log" aria-label="聊天记录" aria-live="polite">
      {!messages.length&&<p className="chat-invitation">比如：“每天整理客户资料很麻烦，但我还不知道该怎么改。”</p>}
      {!!(p as any).intake?.text&&!messages.some(m=>m.text===(p as any).intake.text)&&<details><summary>此前保存的输入</summary><p>{(p as any).intake.text}</p></details>}
      {messages.map((m,n)=><section className={'chat-message '+m.role} key={m.user_task_id+'-'+m.role+'-'+n}><b>{m.role==='user'?'你':'需求助手'}</b><p>{m.text}</p>{Array.isArray(m.attachment_source_ids)&&m.attachment_source_ids.length>0&&<div className="chat-history-images chat-history-attachments"><span>本轮附件 · {m.attachment_source_ids.length} 个</span><div>{m.attachment_source_ids.slice(0,3).map((sid:string)=>historyAttachment(sid))}</div>{m.attachment_source_ids.length>3&&<details><summary>查看其余 {m.attachment_source_ids.length-3} 个附件</summary><div>{m.attachment_source_ids.slice(3).map((sid:string)=>historyAttachment(sid))}</div></details>}</div>}</section>)}
      {sending&&<p role="status">正在保存消息并提交…</p>}<div ref={tail}/>
    </div>
    {error&&<p className="error" role="alert">{error}</p>}
    {status}
    <div className="chat-input-zone" onDragOver={event=>{if(event.dataTransfer.types.includes('Files'))event.preventDefault();}} onDrop={dropAttachments}>
      <label>{direct?'本次需求':'你的想法'}<textarea ref={input} aria-label={direct?'本次需求':'你的想法'} rows={direct?6:3} value={text} disabled={busy||sending||savingDirect||!!receipt.current}
        placeholder={direct?'随便说说，不需要专业术语…':'输入需求，或粘贴、拖入图片与文件…'} onPaste={pasteAttachments}
        onKeyDown={event=>{if(!direct&&event.key==='Enter'&&!event.shiftKey&&!event.nativeEvent.isComposing&&event.keyCode!==229){event.preventDefault();void send();}}}
        onChange={e=>{setText(e.target.value);setDirty(true);sessionStorage.setItem(draftKey,e.target.value);}}/></label>
      {!direct&&<small className="chat-key-hint">Enter 发送 · Shift+Enter 换行</small>}
      {!direct&&<><input ref={fileInput} className="chat-file-input" type="file" multiple accept=".txt,.md,.docx,.doc,.xlsx,.xls,.pptx,.ppt,.pdf,.png,.jpg,.jpeg,.webp"
        aria-label="选择本轮聊天附件" disabled={busy||sending||!!receipt.current} onChange={event=>{const result=extractChatAttachments(Array.from(event.target.files||[]));addAttachments(result.files,result.rejected);event.target.value='';}}/>
        <button type="button" className="chat-file-button" disabled={busy||sending||!!receipt.current} onClick={()=>fileInput.current?.click()}>＋ 选择图片或文件</button></>}
      {attachments.length>0&&<div className="chat-pasted-images" aria-label="本轮待发送附件">{attachments.map(a=><div className="chat-pasted-image" key={a.id}>{a.preview?<img src={a.preview} alt=""/>:<span className="chat-file-icon" aria-hidden="true">文件</span>}<span>{a.name} · {attachmentStatus(a)}</span><button type="button" disabled={sending} onClick={()=>removeAttachment(a.id)} aria-label={'移除本轮附件 '+a.name}>移除</button></div>)}<small>移除只影响本轮消息；已保存的附件仍可在项目资料中查看。保存后离开会保留本轮附件；未保存时刷新页面需重新选择。</small></div>}
    </div>
    {direct?<div ref={composer} className="chat-composer-actions"><button disabled={busy||savingDirect} onClick={()=>setDirect(false)}>返回聊天，保留输入</button><button className="primary" disabled={busy||savingDirect||!text.trim()} onClick={()=>void saveDirect()}>{savingDirect?'正在保存本次需求…':'保存输入并进入草稿核对 →'}</button></div>:<div ref={composer} className="chat-composer-actions"><button className="chat-sources-link" disabled={busy||sending} onClick={onSources}>查看项目资料</button><button className="primary" disabled={busy||sending||!!blocked||(!text.trim()&&!attachments.length)} onClick={()=>void send()}>{receipt.current?'核对并恢复这次提交':sending?'提交中…':pendingImages>0||attachments.some(a=>a.type.startsWith('image/'))?'识别图片并发送':'发送'}</button></div>}
    <div hidden={direct}>
    <small className="chat-consent">发送至 {model.bound_origin||'尚未配置接收端'} · {model.model||'尚未配置模型'}，包含本项目聊天、需求草稿和 {activeSourceIds?`${scopedSourceIds.size} 份本次拟纳入资料`:`当前有效项目资料${attachedSourceIds.size?`及本轮已保存附件 ${attachedSourceIds.size} 个`:''}`}{unsavedAttachmentCount?`，另有 ${unsavedAttachmentCount} 个本轮附件待保存`:''}。发送前仍需核对任务中的实际资料范围；附件不会因添加而自动发送模型。</small>
    {pendingImages>0&&<p className="notice" role="status">还有 {pendingImages} 张本次有效资料图片待识别。发送后先核对模型接收端与资料范围，再识别图片并继续对话；已识别的图片不会重复识别。“一键 AI 填充需求草稿”也会先识别图片，再整理待核对草稿。</p>}
    {blocked&&<p className="notice">{blocked}</p>}
    <BenchmarkIntake p={p} busy={busy||sending||!!blocked} text={text} onSources={onReferenceSources} onImport={onReferenceImport} onPrepare={onBenchmark}/>
    <div className="chat-next">{onContinue?<><button className="primary" disabled={busy||sending||continuing} onClick={onContinue}>{continuing?'正在整理…':'整理并进入完善需求'}</button><small>已整理过且信息未变时直接进入；新结果由你确认。</small></>:<><button disabled={busy||sending||!!blocked||!!text.trim()||!p.sources.some(s=>!s.excluded)} onClick={onOrganize}>✦ 一键 AI 填充需求草稿 →</button>{hasAnalysis&&p.product_flow?.[1]?.available&&<button disabled={busy||sending} onClick={()=>go(1)}>查看已有草稿</button>}</>}</div>
    <small>AI 整理完成后自动进入草稿审核页，由你修改和确认。</small>
    {!!text.trim()&&<small>可以发送给助手讨论，或选择上方“直接填写”。当前文字会保留，进入填写页后再保存到项目。</small>}
    </div>
  </article>;
}
