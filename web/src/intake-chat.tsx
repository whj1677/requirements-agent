import {session} from './draft-storage';
import React, { useEffect, useRef, useState } from 'react';
import { api, ApiError, BenchmarkInput, JsonObject, Project, ProjectMemorySourceRef, Run, uploadFile, UserTask } from './api';
import { BenchmarkIntake } from './benchmark-intake.tsx';
import { useDirty } from './editing';
import { ChatReceipt, interviewSourceIds, sendInterview } from './interview-request';
import { extractChatAttachments } from './pasted-images';
import { ProjectMemoryHistory, ProjectMemoryMessageTrace } from './project-memory';

const IMAGE_MESSAGE='请结合这些图片继续梳理需求。';
const ATTACHMENT_MESSAGE='请结合这些资料继续梳理需求。';
type PendingAttachment={id:string;name:string;type:string;file?:File;preview?:string;sourceId?:string;uploading?:boolean;parseStatus?:string;uploadPhase?:'reading';uploadLoaded?:number;uploadTotal?:number;uploadStartedAt?:number;uploadReviewRequired?:boolean};
type AttachmentCheck={candidates:Project['sources']};
const pendingAttachmentsByProject=new Map<string,PendingAttachment[]>();
export function hasPendingInterviewInput(projectId:string,includeText=true):boolean {
  if((includeText&&session.getItem('ra-chat-draft-'+projectId)?.trim())||pendingAttachmentsByProject.get(projectId)?.length)return true;
  try{return (JSON.parse(session.getItem('ra-chat-images-'+projectId)||'[]') as unknown[]).length>0;}catch{return false;}
}
function restoredAttachments(project:Project,key:string):PendingAttachment[]{
  try{
    const rows=JSON.parse(session.getItem(key)||'[]');
    if(!Array.isArray(rows))return [];
    return rows.flatMap(row=>{
      const source=project.sources.find(s=>s.id===row.sourceId&&s.origin==='interview_attachment');
      return source&&typeof row.id==='string'?[{id:row.id,name:source.title||row.name||'已保存资料',type:source.image_mime||row.type||'',
        sourceId:source.id,parseStatus:source.parse_status,
        preview:source.image_mime?'/api/projects/'+project.id+'/sources/'+source.id+'/image':undefined}]:[];
    });
  }catch{return [];}
}

type Props={p:Project;model:Record<string,any>;busy:boolean;blocked:string;tasks:UserTask[];runs:Run[];status:React.ReactNode;
  onContinue?:()=>void;continuing?:boolean;
  beforeSend?:()=>Promise<Project|void>;
  onPrepareInterview?:(message:string,currentProject:Project,attachmentSourceIds:string[])=>Promise<void>;
  onRefreshProject?:()=>Promise<Project>;
  onStarted:(task:UserTask)=>Promise<void>;onRefresh:()=>void;onSources:()=>void;onSourceRefs:(refs:ProjectMemorySourceRef[])=>void;onOrganize:()=>void;go:(step:number)=>void;
  onReferenceSources:(url?:string)=>void;onReferenceImport:(url:string,dynamic:boolean)=>Promise<JsonObject>;onBenchmark:(request:BenchmarkInput,message:string)=>Promise<void>;
  benchmarkSubmission?:{taskId:string;message:string};interviewSubmission?:{taskId:string;message:string;attachmentSourceIds:string[]};onDirectSave:(text:string)=>Promise<void>;directInputNonce?:number;onDirectEntry?:(text:string)=>void};
export function IntakeChat({p,model,busy,blocked,tasks,runs,status,beforeSend,onPrepareInterview,onRefreshProject,onStarted,onRefresh,onSources,onSourceRefs,onOrganize,go,onReferenceSources,onReferenceImport,onBenchmark,benchmarkSubmission,interviewSubmission,onDirectSave,directInputNonce,onDirectEntry,onContinue,continuing}:Props){
  const draftKey='ra-chat-draft-'+p.id,receiptKey='ra-chat-receipt-'+p.id,imageKey='ra-chat-images-'+p.id;
  const [text,setText]=useState(()=>session.getItem(draftKey)||''),[dirty,setDirty]=useState(false);
  const [sending,setSending]=useState(false),[error,setError]=useState('');
  const [direct,setDirect]=useState(false),[savingDirect,setSavingDirect]=useState(false);
  const [attachments,setAttachments]=useState<PendingAttachment[]>(()=>pendingAttachmentsByProject.get(p.id)||restoredAttachments(p,imageKey));
  const attachmentRef=useRef<PendingAttachment[]>(attachments);
  const [attachmentChecks,setAttachmentChecks]=useState<Record<string,AttachmentCheck>>({});
  const [checkingAttachment,setCheckingAttachment]=useState('');
  const [uploadNow,setUploadNow]=useState(Date.now());
  const hasUploadingAttachments=attachments.some(a=>a.uploading);
  useEffect(()=>{if(!hasUploadingAttachments)return;const timer=window.setInterval(()=>setUploadNow(Date.now()),1000);return()=>window.clearInterval(timer);},[hasUploadingAttachments]);
  const setPendingAttachments=(next:PendingAttachment[])=>{
    attachmentRef.current=next;
    if(next.length)pendingAttachmentsByProject.set(p.id,next);else pendingAttachmentsByProject.delete(p.id);
    const saved=next.filter(a=>a.sourceId).map(a=>({id:a.id,sourceId:a.sourceId,name:a.name,type:a.type}));
    if(saved.length)session.setItem(imageKey,JSON.stringify(saved));else session.removeItem(imageKey);
    setAttachments(next);
  };
  const input=useRef<HTMLTextAreaElement|null>(null),fileInput=useRef<HTMLInputElement|null>(null),composer=useRef<HTMLDivElement|null>(null);
  const receipt=useRef<ChatReceipt|undefined>(undefined),locked=useRef(false),tail=useRef<HTMLDivElement|null>(null);
  useEffect(()=>{
    setText(session.getItem(draftKey)||'');setDirty(false);setDirect(false);setError('');
    setAttachmentChecks({});setCheckingAttachment('');
    setPendingAttachments(pendingAttachmentsByProject.get(p.id)||restoredAttachments(p,imageKey));
  },[p.id]);
  useEffect(()=>{try{receipt.current=JSON.parse(session.getItem(receiptKey)||'null')||undefined;}catch{session.removeItem(receiptKey);}},[receiptKey]);
  async function uploadPendingAttachments(project:Project){
    const unconfirmed=attachmentRef.current.find(a=>!a.sourceId&&a.uploadReviewRequired);
    if(unconfirmed)throw Error(`附件“${unconfirmed.name}”的上传结果尚未核对。请先查看项目资料，确认未出现后再重试。`);
    let current=project;
    for(const attachment of attachmentRef.current){
      if(attachment.sourceId)continue;
      const file=attachment.file;
      if(!file||!onRefreshProject)throw Error('附件尚未保存到项目，请重新选择后重试。');
      const body=new FormData();body.append('file',file);body.append('purpose','chat_attachment');body.append('expected_revision',String(current.revision));
      const startedAt=Date.now();setUploadNow(startedAt);
      setPendingAttachments(attachmentRef.current.map(a=>a.id===attachment.id?{...a,uploading:true,uploadPhase:undefined,uploadReviewRequired:false,uploadLoaded:0,uploadTotal:undefined,uploadStartedAt:startedAt}:a));
      try{
        const source=await uploadFile<JsonObject>('/projects/'+p.id+'/sources/file',body,
          progress=>setPendingAttachments(attachmentRef.current.map(a=>a.id===attachment.id?{...a,uploadLoaded:progress.loaded,uploadTotal:progress.total}:a)),
          ()=>setPendingAttachments(attachmentRef.current.map(a=>a.id===attachment.id?{...a,uploadPhase:'reading',uploadLoaded:file.size,uploadTotal:file.size}:a)));
        if(!source.id)throw Error('附件已上传，但未取得资料 ID；请刷新项目资料核对后再发送。');
        setPendingAttachments(attachmentRef.current.map(a=>a.id===attachment.id?{...a,sourceId:source.id,parseStatus:source.parse_status,uploading:false,uploadPhase:undefined,uploadReviewRequired:false}:a));
        current=await onRefreshProject();
      }catch(e){setPendingAttachments(attachmentRef.current.map(a=>a.id===attachment.id?{...a,uploading:false,uploadPhase:undefined,uploadReviewRequired:!a.sourceId}:a));throw e;}
    }
    return current;
  }
  const saveDraft=async()=>{session.setItem(draftKey,text);if(attachmentRef.current.some(a=>!a.sourceId)){if(!onRefreshProject)throw Error('无法保存待发送附件，请留在当前页面。');await uploadPendingAttachments(await onRefreshProject());}setDirty(false);};
  const clear=()=>{session.removeItem(draftKey);session.removeItem(receiptKey);receipt.current=undefined;attachmentRef.current.forEach(a=>{if(a.preview?.startsWith('blob:'))URL.revokeObjectURL(a.preview);});setPendingAttachments([]);setText('');setDirty(false);setError('');markSaved();};
  useEffect(()=>{if(benchmarkSubmission&&benchmarkSubmission.message===text)clear();},[benchmarkSubmission?.taskId]);
  useEffect(()=>{if(interviewSubmission&&interviewSubmission.message===text&&
    JSON.stringify(interviewSubmission.attachmentSourceIds.slice().sort())===JSON.stringify(attachmentRef.current.map(a=>a.sourceId).filter(Boolean).sort()))clear();},[interviewSubmission?.taskId]);
  const markSaved=useDirty(p.id+'-chat',dirty||attachments.some(a=>!a.sourceId),'需求输入与待发送附件（保存后留在本轮待发送区）',saveDraft,clear);
  function openDirect(){setDirect(true);if(!text.trim()){setText(p.product_context?.intent||(p as JsonObject).intake?.text||'');}setError('');}
  function skipInterview(){
    if(attachmentRef.current.length){setError('先发送或移除本轮附件，再切换到直接填写。');return;}
    if(!onDirectEntry){openDirect();return;}
    session.setItem(draftKey,text);setDirty(false);markSaved();onDirectEntry(text);
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
        r=>{receipt.current=r;session.setItem(receiptKey,JSON.stringify(r));session.setItem(draftKey,message);},receipt.current);
      clear();await onStarted(task);
    }catch(e){
      setError((e as Error).message);
      if(e instanceof ApiError&&e.status>0&&e.kind==='json'){receipt.current=undefined;session.removeItem(receiptKey);}
      onRefresh();
    }finally{locked.current=false;setSending(false);}
  }
  function addAttachments(files:File[],rejected:string[],suppressDefault=false){
    if(rejected.length)setError(rejected.join('；'));
    if(!files.length)return;
    const next=[...attachmentRef.current,...files.map(file=>({id:crypto.randomUUID(),name:file.name,type:file.type,file,
      preview:file.type.startsWith('image/')?URL.createObjectURL(file):undefined}))];
    setPendingAttachments(next);
    setDirty(true);
    if(!suppressDefault&&(!text.trim()||text===IMAGE_MESSAGE||text===ATTACHMENT_MESSAGE)){
      const message=next.every(a=>a.type.startsWith('image/'))?IMAGE_MESSAGE:ATTACHMENT_MESSAGE;
      setText(message);session.setItem(draftKey,message);
    }
  }
  function pasteAttachments(event:React.ClipboardEvent<HTMLTextAreaElement>){
    if(direct||busy||sending||receipt.current)return;
    const result=extractChatAttachments(event.clipboardData);
    if(!result.files.length&&!result.rejected.length)return;
    const pastedText=event.clipboardData.getData('text/plain');
    if(pastedText){
      const textarea=event.currentTarget,start=textarea.selectionStart,end=textarea.selectionEnd;
      const value=textarea.value;
      const next=value.slice(0,start)+pastedText+value.slice(end);
      const caret=start+pastedText.length;
      event.preventDefault();
      setText(next);setDirty(true);session.setItem(draftKey,next);
      addAttachments(result.files,result.rejected,true);
      requestAnimationFrame(()=>{if(input.current===textarea){textarea.focus();textarea.setSelectionRange(caret,caret);}});
      return;
    }
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
  function removeAttachment(id:string){
    const removed=attachmentRef.current.find(a=>a.id===id);if(!removed)return;
    if(removed.preview?.startsWith('blob:'))URL.revokeObjectURL(removed.preview);
    const next=attachmentRef.current.filter(a=>a.id!==id);setPendingAttachments(next);
    clearAttachmentCheck(id);
    if(text===IMAGE_MESSAGE||text===ATTACHMENT_MESSAGE){const message=next.length?(next.every(a=>a.type.startsWith('image/'))?IMAGE_MESSAGE:ATTACHMENT_MESSAGE):'';setText(message);if(message)session.setItem(draftKey,message);else session.removeItem(draftKey);}}
  function clearAttachmentCheck(id:string){setAttachmentChecks(current=>{const next={...current};delete next[id];return next;});}
  async function checkAttachment(attachment:PendingAttachment){
    if(!onRefreshProject){setError('当前无法刷新项目资料，请前往项目资料核对。');return;}
    setCheckingAttachment(attachment.id);setError('');
    try{
      const latest=await onRefreshProject();
      const candidates=latest.sources.filter(s=>s.origin==='interview_attachment'&&(s.title||'')===attachment.name);
      setAttachmentChecks(current=>({...current,[attachment.id]:{candidates}}));
    }catch(e){setError((e as Error).message);}finally{setCheckingAttachment('');}
  }
  function confirmCheckedAttachment(id:string,source:Project['sources'][number]){
    setPendingAttachments(attachmentRef.current.map(a=>a.id===id?{...a,sourceId:source.id,parseStatus:source.parse_status,uploading:false,uploadPhase:undefined,uploadReviewRequired:false}:a));
    clearAttachmentCheck(id);
    setError('');
  }
  function allowAttachmentRetry(id:string){
    setPendingAttachments(attachmentRef.current.map(a=>a.id===id?{...a,uploadReviewRequired:false,uploadPhase:undefined,uploadLoaded:undefined,uploadTotal:undefined,uploadStartedAt:undefined}:a));
    clearAttachmentCheck(id);
    setError('');
  }
  function readStatus(status?:string){return ({read:'已读取',partial:'部分读取',failed:'读取失败',office_required:'需本机办公软件处理',requires_office:'需本机办公软件处理',permission_denied:'权限受限',awaiting_vision:'图片待识别',reading:'读取中'} as Record<string,string>)[status||'']||'读取状态待核对';}
  function formatBytes(bytes:number){if(bytes<1024)return `${bytes} B`;if(bytes<1024*1024)return `${(bytes/1024).toFixed(1)} KiB`;return `${(bytes/1024/1024).toFixed(1)} MiB`;}
  function attachmentStatus(a:PendingAttachment){
    if(a.uploading){
      const elapsed=a.uploadStartedAt?Math.max(0,Math.floor((uploadNow-a.uploadStartedAt)/1000)):0;
      if(a.uploadPhase==='reading')return `上传完成，正在读取 · 已用时 ${elapsed} 秒 · 上传请求超时 300 秒`;
      const loaded=a.uploadLoaded||0,total=a.uploadTotal;
      return total&&total>0?`正在上传 ${Math.min(100,Math.floor(loaded/total*100))}% · ${formatBytes(loaded)} / ${formatBytes(total)} · 已用时 ${elapsed} 秒 · 上传请求超时 300 秒`:`正在上传 · 已传输 ${formatBytes(loaded)} · 已用时 ${elapsed} 秒 · 上传请求超时 300 秒`;
    }
    if(a.sourceId)return readStatus(p.sources.find(s=>s.id===a.sourceId)?.parse_status||a.parseStatus);
    return a.uploadReviewRequired?'上传结果待核对':'待发送';
  }
  function historyAttachment(sid:string){
    const source=p.sources.find(s=>s.id===sid);
    return <div className="chat-history-attachment" key={sid}>{source?.image_mime?<img src={'/api/projects/'+p.id+'/sources/'+sid+'/image'} alt=""/>:<span className="chat-file-icon" aria-hidden="true">文件</span>}
      <span>{source?.title||`资料 ${sid} 暂不可查看`}{source&&` · ${readStatus(source.parse_status)}`}</span></div>;
  }
  const hasAnalysis=p.messages.some(m=>m.stage==='ingest'&&m.role==='assistant')||p.items.length>0;
  return <article className="card intake-chat" aria-label="需求聊天" data-has-history={messages.length>0}>
    <div className="chat-intro"><h2>{direct?'直接填写本次需求':'先聊聊你想做什么'}</h2><p>{direct?'写下已有信息，保存到项目后进入草稿核对。未知信息留待后续澄清，此操作不调用模型。':'一句想法、一个困扰或一份参考资料都可以。不知道的地方，我们一起弄清楚。'}</p></div>

    <div className="chat-messages" role="log" aria-label="聊天记录" aria-live="polite">
      {!messages.length&&<p className="chat-invitation">比如：“每天整理客户资料很麻烦，但我还不知道该怎么改。”</p>}
      {!!(p as any).intake?.text&&!messages.some(m=>m.text===(p as any).intake.text)&&<details><summary>此前保存的输入</summary><p>{(p as any).intake.text}</p></details>}
      {messages.map((m,n)=><section className={'chat-message '+m.role} key={m.user_task_id+'-'+m.role+'-'+n}><b>{m.role==='user'?'你':'需求助手'}</b><p>{m.text}</p>{Array.isArray(m.attachment_source_ids)&&m.attachment_source_ids.length>0&&<div className="chat-history-images chat-history-attachments"><span>本轮附件 · {m.attachment_source_ids.length} 个</span><div>{m.attachment_source_ids.slice(0,3).map((sid:string)=>historyAttachment(sid))}</div>{m.attachment_source_ids.length>3&&<details><summary>查看其余 {m.attachment_source_ids.length-3} 个附件</summary><div>{m.attachment_source_ids.slice(3).map((sid:string)=>historyAttachment(sid))}</div></details>}</div>}<ProjectMemoryMessageTrace message={m} tasks={tasks} runs={runs} sources={p.sources} onSourceRefs={onSourceRefs}/></section>)}
      {sending&&<p role="status">正在保存消息并提交…</p>}<div ref={tail}/>
    </div>
    <ProjectMemoryHistory tasks={tasks} runs={runs} sources={p.sources} onSourceRefs={onSourceRefs}/>
    {error&&<p className="error" role="alert">{error}</p>}
    {status}
    <div className="chat-compose-dock"><div className="chat-input-zone" onDragOver={event=>{if(event.dataTransfer.types.includes('Files'))event.preventDefault();}} onDrop={dropAttachments}>
      <label>{direct?'本次需求':'你的想法'}<textarea ref={input} aria-label={direct?'本次需求':'你的想法'} rows={direct?6:2} value={text} disabled={busy||sending||savingDirect||!!receipt.current}
        placeholder={direct?'随便说说，不需要专业术语…':'输入需求，或粘贴、拖入图片与文件…'} onPaste={pasteAttachments}
        onKeyDown={event=>{if(!direct&&event.key==='Enter'&&!event.shiftKey&&!event.nativeEvent.isComposing&&event.keyCode!==229){event.preventDefault();void send();}}}
        onChange={e=>{setText(e.target.value);setDirty(true);session.setItem(draftKey,e.target.value);}}/></label>
      {!direct&&<small className="chat-key-hint">Enter 发送 · Shift+Enter 换行</small>}
      {!direct&&<><input ref={fileInput} className="chat-file-input" type="file" multiple accept=".txt,.md,.docx,.doc,.xlsx,.xls,.pptx,.ppt,.pdf,.html,.htm,.png,.jpg,.jpeg,.webp"
        aria-label="选择本轮聊天附件" disabled={busy||sending||!!receipt.current} onChange={event=>{const result=extractChatAttachments(Array.from(event.target.files||[]));addAttachments(result.files,result.rejected);event.target.value='';}}/>
        <button type="button" className="chat-file-button" disabled={busy||sending||!!receipt.current} onClick={()=>fileInput.current?.click()}>＋ 选择图片或文件</button></>}
      {attachments.length>0&&<div className="chat-pasted-images" aria-label="本轮待发送附件">{attachments.map(a=><div className="chat-pasted-image" key={a.id}>
        {a.preview?<img src={a.preview} alt=""/>:<span className="chat-file-icon" aria-hidden="true">文件</span>}
        <span role={a.uploading?'status':undefined}>{a.name} · {attachmentStatus(a)}</span>
        <button type="button" disabled={sending} onClick={()=>removeAttachment(a.id)} aria-label={'移除本轮附件 '+a.name}>移除</button>
        {a.uploadReviewRequired&&!a.sourceId&&<div className="chat-attachment-review">
          <small>上次上传未确认是否保存成功。请先核对，避免重复上传；同名不代表同一文件。</small>
          <div>
            <button type="button" disabled={busy||sending||!!checkingAttachment} onClick={()=>void checkAttachment(a)}>{checkingAttachment===a.id?'正在刷新…':'刷新核对'}</button>
            <button type="button" disabled={busy||sending} onClick={onSources}>去项目资料核对</button>
            <button type="button" disabled={busy||sending} onClick={()=>allowAttachmentRetry(a.id)}>已核对未出现，可重试</button>
          </div>
          {attachmentChecks[a.id]&&<div>
            <small>{attachmentChecks[a.id]!.candidates.length?'已获取最新项目资料。只有确认就是这份文件时才关联，不会自动复用同名资料。':'已获取最新项目资料，未找到同名访谈附件。仍请核对后选择是否重试。'}</small>
            {attachmentChecks[a.id]!.candidates.map(source=><div key={source.id}>
              <span>{source.title||a.name} · {readStatus(source.parse_status)}</span>
              <button type="button" disabled={busy||sending} onClick={()=>confirmCheckedAttachment(a.id,source)}>确认是这份已保存文件</button>
            </div>)}
          </div>}
        </div>}
      </div>)}<small>移除只影响本轮消息；已保存的附件仍可在项目资料中查看。保存后离开会保留本轮附件；未保存时刷新页面需重新选择。</small></div>}
    </div>
    {direct?<div ref={composer} className="chat-composer-actions"><button disabled={busy||savingDirect} onClick={()=>setDirect(false)}>返回聊天，保留输入</button><button className="primary" disabled={busy||savingDirect||!text.trim()} onClick={()=>void saveDirect()}>{savingDirect?'正在保存本次需求…':'保存输入并进入草稿核对 →'}</button></div>:<div ref={composer} className="chat-composer-actions"><button className="chat-sources-link" disabled={busy||sending} onClick={onSources}>查看项目资料</button><button className="chat-send" disabled={busy||sending||!!blocked||(!text.trim()&&!attachments.length)} onClick={()=>void send()}>{receipt.current?'核对并恢复这次提交':sending?'提交中…':pendingImages>0||attachments.some(a=>a.type.startsWith('image/'))?'识别图片并发送':'发送'}</button></div>}
    </div>
    <div className="chat-secondary-actions" hidden={direct}>
    <details className="chat-consent"><summary>发送范围：{model.bound_origin||'尚未配置接收端'} · {model.model||'尚未配置模型'}</summary><small>发送至 {model.bound_origin||'尚未配置接收端'} · {model.model||'尚未配置模型'}，包含本项目聊天、需求草稿和 {activeSourceIds?`${scopedSourceIds.size} 份本次拟纳入资料`:`当前有效项目资料${attachedSourceIds.size?`及本轮已保存附件 ${attachedSourceIds.size} 个`:''}`}{unsavedAttachmentCount?`，另有 ${unsavedAttachmentCount} 个本轮附件待保存`:''}。发送前仍需核对任务中的实际资料范围；附件不会因添加而自动发送模型。</small></details>
    {pendingImages>0&&<p className="notice" role="status">还有 {pendingImages} 张本次有效资料图片待识别。点击“识别图片并发送”一次，会先识别尚未读取的图片，再继续对话；已识别的图片不会重复识别。发送范围始终可在上方查看，没有额外授权步骤。</p>}
    {blocked&&<p className="notice">{blocked}</p>}
    <BenchmarkIntake p={p} busy={busy||sending||!!blocked} text={text} onSources={onReferenceSources} onImport={onReferenceImport} onPrepare={onBenchmark}/>
    <div className="chat-next">{onContinue?<><button className={hasAnalysis?'secondary':'primary'} disabled={busy||sending||continuing} onClick={onContinue}>{continuing?'正在整理…':hasAnalysis?'让 AI 更新草稿':'整理并进入完善需求'}</button><small>已整理过且信息未变时直接进入；新结果由你确认。</small></>:<><button disabled={busy||sending||!!blocked||!!text.trim()||!p.sources.some(s=>!s.excluded)} onClick={onOrganize}>AI 整理需求草稿 →</button>{hasAnalysis&&p.product_flow?.[1]?.available&&<button disabled={busy||sending} onClick={()=>go(1)}>查看已有草稿</button>}</>}</div>
    <small>AI 整理完成后自动进入草稿审核页，由你修改和确认。</small>
    {!!text.trim()&&<small>可以发送给助手讨论，或选择上方“直接填写”。当前文字会保留，进入填写页后再保存到项目。</small>}
    </div>
  </article>;
}
