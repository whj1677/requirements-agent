import React, { useEffect, useRef, useState } from 'react';
import { api, Project } from '../api';
import { useNavigation, useSaveDrafts } from '../editing';
import { requirementBehaviorLabels } from './requirement-fields';

type Obj = Record<string, any>;
export type TableDraft = {statement:string;behavior:Obj;change_type?:string;acceptance_items:Obj[];candidate_id?:string;confirmed_question_ids:string[];revision:number;instruction?:string};
export type DraftEntry = {draft:TableDraft;baseStamp:string;dirty:boolean};
type Props = {
  project:Project; active:boolean; busy:boolean; selected:string[]; drafts:Record<string,DraftEntry>;
  eligible:string[]; rowStamp:(p:Project,id:string)=>string;
  onSelect:(ids:string[])=>void; onEdit:(id:string)=>void; onSources:(refs:Obj[])=>void;
  onReviewState:(open:boolean)=>void;
  mutate:(path:string,body:Obj)=>Promise<any>; onConfirmed:(ids:string[])=>void;
};
const changeLabels:Record<string,string>={new:'新增功能',modified:'修改已有功能',preserved:'保持已有功能',unspecified:'尚未确定'};

export function RequirementBatchReview(props:Props){
  const {project,selected}=props;
  const [preview,setPreview]=useState<Obj|null>(null),[working,setWorking]=useState(false),[error,setError]=useState('');
  const lock=useRef(false),intent=useRef(''),panel=useRef<HTMLElement>(null),request=useRef(0);
  const navigate=useNavigation(),saveDrafts=useSaveDrafts(),prepareLatest=useRef(()=>{});
  const inputSignature=JSON.stringify([project.id,project.revision,props.active,selected.map(id=>[id,props.drafts[id]?.draft,props.drafts[id]?.baseStamp])]);
  const latestInput=useRef(inputSignature);latestInput.current=inputSignature;
  useEffect(()=>()=>{request.current++;},[]);
  useEffect(()=>{props.onReviewState(props.active&&(working||!!preview));return()=>props.onReviewState(false);},[props.active,working,!!preview,props.onReviewState]);
  useEffect(()=>{setPreview(null);setError('');},[project.id,project.revision,inputSignature]);
  useEffect(()=>{if(!props.active){setPreview(null);setError('');}},[props.active]);
  useEffect(()=>{if(preview){panel.current?.scrollIntoView({block:'start'});panel.current?.focus({preventScroll:true});}},[preview?.preview_hash]);
  async function prepare(){
    if(lock.current||!selected.length||!props.active)return;
    const requestId=++request.current,capturedInput=inputSignature;
    lock.current=true;setWorking(true);setError('');
    try{
      const latest=await api<Project>('/projects/'+project.id);
      if(latest.revision!==project.revision)throw Error('项目有新修改，请刷新并查看最新内容；你的选择和草稿仍保留。');
      const rows=selected.map(id=>{
        const entry=props.drafts[id],item=project.items.find(i=>i.id===id);
        if(!entry||entry.baseStamp!==props.rowStamp(latest,id))throw Error('“'+(item?.title||'所选需求')+'”已有新内容，请展开核对后再加入本次确认。');
        const draft=entry.draft;
        if(draft.instruction?.trim())throw Error('“'+item?.title+'”还有给 AI 的修改意见尚未处理，请先调整本行，或清空不再需要的意见。');
        return {requirement_id:id,statement:draft.statement,behavior:draft.behavior,
          change_type:draft.change_type||'unspecified',candidate_id:draft.candidate_id,
          acceptance_items:draft.acceptance_items.filter(a=>a.statement.trim()),confirmed_question_ids:draft.confirmed_question_ids};
      });
      const result=await api<Obj>('/projects/'+project.id+'/requirement-confirmation/batch/preview','POST',{expected_revision:project.revision,rows});
      if(request.current!==requestId||latestInput.current!==capturedInput)return;
      intent.current=crypto.randomUUID();setPreview({...result,input_signature:capturedInput});
    }catch(e){if(request.current===requestId&&latestInput.current===capturedInput)setError((e as Error).message);}finally{lock.current=false;setWorking(false);}
  }
  prepareLatest.current=()=>{void prepare();};
  async function reviewSelection(){
    try{
      await saveDrafts(undefined,selected.map(id=>project.id+'-table-draft-'+id));
      navigate(()=>requestAnimationFrame(()=>prepareLatest.current()));
    }catch(e){setError((e as Error).message);}
  }
  async function commit(){
    if(!preview||lock.current)return;
    if(preview.input_signature!==latestInput.current){setPreview(null);setError('内容已有修改，请重新查看确认预览。');return;}
    lock.current=true;setWorking(true);setError('');
    try{
      await props.mutate('/requirement-confirmation/batch/commit',{expected_revision:preview.expected_revision,
        preview_id:preview.preview_id,preview_hash:preview.preview_hash,idempotency_key:intent.current,review_confirmed:true});
      const ids=preview.rows.map((row:Obj)=>row.requirement_id);
      setPreview(null);props.onConfirmed(ids);
    }catch(e){setError((e as Error).message);}finally{lock.current=false;setWorking(false);}
  }
  const disabled=props.busy||working;
  return <section className="table-batch" aria-label="批量确认需求">
    <div className="table-batch-toolbar"><div><strong>本次选择 {selected.length} 条</strong><p>阅读所选需求及验收后，一次确认；勾选只是临时选择，离开本页会清空。</p></div>
      <div><button type="button" disabled={disabled||!props.eligible.length} onClick={()=>props.onSelect(props.eligible)}>选择待确认需求</button>
        <button type="button" disabled={disabled||!selected.length} onClick={()=>props.onSelect([])}>清空选择</button>
        <button type="button" className="primary" disabled={disabled||!selected.length} onClick={()=>void reviewSelection()}>{working?'正在处理…':`查看并确认所选（${selected.length}）`}</button></div></div>
    {error&&<p role="alert" className="connected-error">{error}</p>}
    {preview&&preview.input_signature===inputSignature&&<section ref={panel} tabIndex={-1} className="table-batch-preview" aria-label="批量确认预览">
      <header><h3>确认以下 {preview.rows.length} 条需求及验收</h3><p>可直接确认这些内容，也可返回修改。未解决的业务决定单独保留。</p></header>
      {preview.rows.map((row:Obj)=><article key={row.requirement_id} className="table-batch-row">
        <header><h4>{row.after.title}</h4><button type="button" disabled={disabled} onClick={()=>{setPreview(null);props.onEdit(row.requirement_id);}}>修改这条</button></header>
        {row.before.statement!==row.after.statement&&<details><summary>对照修改前的需求</summary><p>{row.before.statement}</p></details>}
        <p className="table-batch-statement">{row.after.statement}</p><small>本次改动：{changeLabels[row.after.change_type]||'尚未确定'}</small>
        <div className="table-behavior-readonly">{Object.entries(requirementBehaviorLabels).map(([field,label])=><div key={field}><b>{label}</b><p>{row.after.behavior?.[field]||'未说明，本次不确认为业务条件'}</p>{row.before.behavior?.[field]&&row.before.behavior[field]!==row.after.behavior?.[field]&&<details><summary>修改前</summary><p>{row.before.behavior[field]}</p></details>}</div>)}</div>
        <h4>同时确认的验收</h4><ul>{row.acceptance_items.map((ac:Obj,n:number)=><li key={ac.id||n}>{ac.after.statement}{ac.before&&ac.before.statement!==ac.after.statement&&<details><summary>修改前</summary><p>{ac.before.statement}</p></details>}</li>)}</ul>
        <button type="button" onClick={()=>props.onSources(row.after.source_refs||[])}>查看本条来源</button>
      </article>)}
      {!!preview.question_results?.length&&<section className="table-question-results" aria-label="本次关联的业务决定"><h4>关联的业务决定</h4>{preview.question_results.map((q:Obj)=><div key={q.question_id}><b>{q.question}</b><p>{q.answer||'尚未回答'}<br/><small>{q.confirm?'本次确认：以上需求已表达这条回答':q.already_confirmed?'已有确认仍有效':q.blocking===false?'可选，继续保留':q.reason||'仍需决定，不随本次确认关闭'}</small></p></div>)}</section>}
      <div className="table-confirm-actions"><button type="button" disabled={disabled} onClick={()=>setPreview(null)}>返回表格修改</button><button type="button" className="primary" disabled={disabled||preview.expected_revision!==project.revision} onClick={()=>void commit()}>{working?'正在确认…':`确认以上 ${preview.rows.length} 条需求`}</button><small>一次保存以上需求、业务说明和验收；不会确认未展示的内容。</small></div>
    </section>}
  </section>;
}
