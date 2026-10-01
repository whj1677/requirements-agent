import React, { useEffect, useRef, useState } from 'react';
import { api, ApiError, Project, UserTask } from './api';
import { useDirty } from './editing';
import { ChatReceipt, sendInterview } from './interview-request';

type Props={p:Project;model:Record<string,any>;busy:boolean;blocked:string;tasks:UserTask[];status:React.ReactNode;
  onStarted:(task:UserTask)=>Promise<void>;onRefresh:()=>void;onSources:()=>void;onOrganize:()=>void;go:(step:number)=>void};
export function IntakeChat({p,model,busy,blocked,tasks,status,onStarted,onRefresh,onSources,onOrganize,go}:Props){
  const draftKey='ra-chat-draft-'+p.id,receiptKey='ra-chat-receipt-'+p.id;
  const [text,setText]=useState(()=>sessionStorage.getItem(draftKey)||''),[dirty,setDirty]=useState(false);
  const [sending,setSending]=useState(false),[error,setError]=useState('');
  const receipt=useRef<ChatReceipt|undefined>(undefined),locked=useRef(false),tail=useRef<HTMLDivElement|null>(null);
  useEffect(()=>{try{receipt.current=JSON.parse(sessionStorage.getItem(receiptKey)||'null')||undefined;}catch{sessionStorage.removeItem(receiptKey);}},[]);
  const saveDraft=async()=>{sessionStorage.setItem(draftKey,text);setDirty(false);};
  const clear=()=>{sessionStorage.removeItem(draftKey);sessionStorage.removeItem(receiptKey);receipt.current=undefined;setText('');setDirty(false);setError('');};
  useDirty(p.id+'-chat',dirty,'聊天输入（保存为本浏览器草稿，不调用模型）',saveDraft,clear);
  useEffect(()=>{
    const key=receipt.current?.idempotency_key;
    if(key&&tasks.some(t=>(t as any).idempotency_key===key))clear();
  },[tasks]);
  const messages=p.messages.filter(m=>m.stage==='interview');
  useEffect(()=>{tail.current?.scrollIntoView({block:'nearest'});},[messages.length,sending]);
  async function send(){
    if(locked.current||busy||blocked||!text.trim())return;
    locked.current=true;setSending(true);setError('');
    try{
      const task=await sendInterview(api,'/projects/'+p.id,{action:'interview',expected_revision:p.revision,message:text,document_type:'prd',max_calls:null},
        {origin:String(model.bound_origin||''),model:String(model.model||'')},p.sources.filter(s=>!s.excluded).map(s=>s.id),
        r=>{receipt.current=r;sessionStorage.setItem(receiptKey,JSON.stringify(r));sessionStorage.setItem(draftKey,text);},receipt.current);
      clear();await onStarted(task);
    }catch(e){
      setError((e as Error).message);
      if(e instanceof ApiError&&e.status>0&&e.kind==='json'){receipt.current=undefined;sessionStorage.removeItem(receiptKey);}
      onRefresh();
    }finally{locked.current=false;setSending(false);}
  }
  const hasAnalysis=p.messages.some(m=>m.stage==='ingest'&&m.role==='assistant')||p.items.length>0;
  return <article className="card intake-chat" aria-label="需求聊天">
    <div className="chat-intro"><h2>先聊聊你想做什么</h2><p>一句想法、一个困扰或一份参考资料都可以。不知道的地方，我们一起弄清楚。</p></div>
    <div className="chat-messages" role="log" aria-label="聊天记录" aria-live="polite">
      {!messages.length&&<p className="chat-invitation">比如：“每天整理客户资料很麻烦，但我还不知道该怎么改。”</p>}
      {!!(p as any).intake?.text&&!messages.some(m=>m.text===(p as any).intake.text)&&<details><summary>此前保存的输入</summary><p>{(p as any).intake.text}</p></details>}
      {messages.map((m,n)=><section className={'chat-message '+m.role} key={m.user_task_id+'-'+m.role+'-'+n}><b>{m.role==='user'?'你':'需求助手'}</b><p>{m.text}</p></section>)}
      {sending&&<p role="status">正在保存消息并提交…</p>}<div ref={tail}/>
    </div>
    {error&&<p className="error" role="alert">{error}</p>}
    {status}
    <label>你的想法<textarea aria-label="你的想法" rows={3} value={text} disabled={sending||!!receipt.current}
      placeholder="随便说说，不需要专业术语…" onChange={e=>{setText(e.target.value);setDirty(true);sessionStorage.setItem(draftKey,e.target.value);}}/></label>
    <div className="chat-composer-actions"><button disabled={busy||sending} onClick={onSources}>＋ 添加资料</button><button className="primary" disabled={busy||sending||!!blocked||!text.trim()} onClick={()=>void send()}>{receipt.current?'核对并恢复这次提交':sending?'提交中…':'发送'}</button></div>
    <small className="chat-consent">发送至 {model.bound_origin||'尚未配置接收端'} · {model.model||'尚未配置模型'}，包含本项目聊天、需求草稿和 {p.sources.filter(s=>!s.excluded).length} 份项目资料。点击发送即授权本次处理。图片未读取时会如实提示。</small>
    {blocked&&<p className="notice">{blocked}</p>}
    <div className="chat-next"><button disabled={busy||sending||!!blocked||!!text.trim()||!p.sources.some(s=>!s.excluded)} onClick={onOrganize}>整理需求草稿 →</button>{hasAnalysis&&<button onClick={()=>go(1)}>查看已有草稿</button>}<button className="link-button" onClick={()=>go(1)}>已有明确需求，直接整理</button></div>
    {!!text.trim()&&<small>先发送当前输入，再整理草稿；也可以保存输入后进入直接填写。</small>}
  </article>;
}
