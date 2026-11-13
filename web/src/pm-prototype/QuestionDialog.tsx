import React, {useEffect, useRef, useState} from 'react';
import {createPortal} from 'react-dom';
import type {Project} from '../api';
import {session} from '../draft-storage';
import {useDialog} from '../shell';
import {questionGroups} from './refine-context';
import './question-dialog.css';

type Draft={choice:string;text:string;before:string;revision:number};
type Obj=Record<string,any>;

/** Structured, current questions only. Opening a project never calls the model. */
export function QuestionDialog({project,active,disabled,mutate,onContinue}:{
  project:Project;active:boolean;disabled:boolean;
  mutate:(path:string,body:Obj)=>Promise<any>;onContinue:()=>void;
}){
  const storageKey='ra-question-dialog-'+project.id;
  const [drafts,setDrafts]=useState<Record<string,Draft>>(()=>{
    try{
      const saved=JSON.parse(session.getItem(storageKey)||'{}');
      if(!saved||typeof saved!=='object'||Array.isArray(saved))return {};
      return Object.fromEntries(Object.entries(saved).filter((entry):entry is [string,Draft]=>{
        const value=entry[1] as Draft;
        return !!value&&typeof value.choice==='string'&&typeof value.text==='string'&&typeof value.before==='string'&&Number.isInteger(value.revision);
      }));
    }catch{return {};}
  });
  const [open,setOpen]=useState(false),[saving,setSaving]=useState(false);
  const [error,setError]=useState(''),[storageWarning,setStorageWarning]=useState(false);
  const [submitted,setSubmitted]=useState<string[]>([]);
  const lock=useRef(false);
  // The canonical open set also contains saved answers awaiting clause review.
  // They belong in the review table, not another blank answer prompt.
  const questions=questionGroups(project).open.filter(q=>q.status!=='answered'||!String(q.answer||'').trim());
  const ids=questions.map(q=>q.id as string),signature=JSON.stringify(ids);
  const seen=useRef(new Set(ids));
  const remaining=questions.filter(q=>!submitted.includes(q.id)),question=remaining[0];
  const current=question && drafts[question.id];
  const draft:Draft=current||{choice:'',text:'',before:question?.answer||'',revision:project.revision};
  const options:string[]=Array.isArray(question?.options)?question.options.filter((x:unknown)=>typeof x==='string'&&x.trim()):[];
  const answer=[draft.choice,draft.text.trim()&&((draft.choice?'补充：':'')+draft.text.trim())].filter(Boolean).join('\n');
  const locked=disabled||saving;
  const close=()=>{if(!lock.current)setOpen(false);};
  useDialog(open&&active,close);
  useEffect(()=>{
    const added=ids.some(id=>!seen.current.has(id));
    if(disabled)return; // Wait for the task and its final project refresh.
    seen.current=new Set(ids);
    if(added&&active&&!document.querySelector('[role="dialog"][aria-modal="true"]')){
      setSubmitted([]);setError('');setOpen(true);
    }
  },[signature,active,disabled]);
  useEffect(()=>{
    if(open&&active&&!saving)document.getElementById('question-dialog-title-'+project.id)?.focus();
  },[question?.id,open,active,saving]);
  function update(value:Partial<Draft>){
    if(!question)return;
    const next={...drafts,[question.id]:{...draft,...value}};
    setDrafts(next);setStorageWarning(!session.setItem(storageKey,JSON.stringify(next)));setError('');
  }
  async function submit(){
    if(lock.current||locked||!question||!answer.trim())return;
    lock.current=true;setSaving(true);setError('');
    const qid=question.id;
    try{
      await mutate('/answers',{answers:{[qid]:answer},expected_revision:draft.revision,
        __edit:{kind:'answers',before:{[qid]:draft.before},mine:{[qid]:answer}}});
      const next={...drafts};delete next[qid];setDrafts(next);
      session.setItem(storageKey,JSON.stringify(next));
      setSubmitted(value=>[...value,qid]);
    }catch(e){setError((e as Error).message+'；输入已保留，请核对后重试。');}
    finally{lock.current=false;setSaving(false);}
  }
  return <>
    {!!questions.length&&<div className="question-dialog-entry">
      <span><strong>{questions.length} 个问题待回答</strong><small>逐题选择，也可以直接写下你的判断</small></span>
      <button type="button" disabled={disabled} onClick={()=>{setSubmitted([]);setError('');setOpen(true);}}>逐题回答</button>
    </div>}
    {open&&active&&createPortal(<div className="question-dialog-backdrop">
      <section className="question-dialog" role="dialog" aria-modal="true" aria-labelledby={'question-dialog-title-'+project.id}>
        <header><span>{question?`需求澄清 · 已答 ${submitted.length} / ${submitted.length+remaining.length}`:'本轮回答已保存'}</span><button type="button" disabled={saving} onClick={close} aria-label="关闭提问，保留输入">关闭</button></header>
        <div className="question-dialog-body">
          <h2 id={'question-dialog-title-'+project.id} tabIndex={-1}>{question?question.question:'回答完成，继续核对需求'}</h2>
          {question?<>
            {question.why&&<p className="question-dialog-why">{question.why}</p>}
            {!!options.length&&<fieldset disabled={locked}><legend>选择一项建议，或直接在下方填写</legend>
              {options.map((option,index)=><label key={index} className={draft.choice===option?'selected':''}>
                <input type="radio" name={'question-choice-'+project.id} checked={draft.choice===option} onChange={()=>update({choice:option})}/><span>{option}</span>
              </label>)}
              {draft.choice&&<button type="button" onClick={()=>update({choice:''})}>清除选项，自己填写</button>}
            </fieldset>}
            <label className="question-dialog-text">{draft.choice?'补充说明（可选）':'你的回答'}
              <textarea rows={3} disabled={locked} value={draft.text} onChange={e=>update({text:e.target.value})} placeholder={draft.choice?'补充适用条件、例外或具体数值':'可以选择上方建议，也可以直接输入自己的答案'}/>
            </label>
            {storageWarning&&<p role="status">浏览器暂时无法持久保存，输入保留在当前页面，请勿刷新。</p>}
          </>:<p>回答已保存。接下来核对它们对应的需求正文和验收条件。</p>}
          {error&&<p className="question-dialog-error" role="alert">{error}</p>}
        </div>
        <footer><small>{question?'暂时关闭会保留未提交输入':'保存回答不会自动确认需求'}</small>
          {question?<button type="button" className="primary" disabled={locked||!answer.trim()} onClick={()=>void submit()}>{saving?'正在保存…':remaining.length>1?'提交并继续':'提交回答'}</button>
            :<button type="button" className="primary" disabled={disabled} onClick={()=>{setOpen(false);onContinue();}}>继续核对草稿</button>}
        </footer>
      </section>
    </div>,document.body)}
  </>;
}
