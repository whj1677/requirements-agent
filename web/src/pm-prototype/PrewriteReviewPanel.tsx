import React, { useEffect, useRef, useState } from 'react';
import { type JsonObject as Obj, type Project,type ActionInput,type UserTask } from '../api';
import { useNavigation } from '../editing';
import { prewriteCanDraft, prewriteFindingLabel, prewriteLabel, prewritePassed, prewriteQuoteLabel } from '../prewrite-review';
import {PrewriteBatchTable} from './PrewriteBatchTable';
import {pendingChecks} from './prewrite-batch-state';
import './prewrite-review.css';

type Props={
  project:Project;busy:boolean;running:boolean;blockReason:string;
  draftTask?:UserTask;
  mutate:(path:string,body:Obj,method?:string)=>Promise<any>;
  onRefresh:()=>void;onRecheck:()=>void;onGenerate:(allowReviewDraft?:boolean)=>void;
  onDraftAll:(instruction:string,request?:ActionInput['draft_request'])=>void;
  onClarify:(questionId:string)=>void;onSources:(refs?:Obj[])=>void;
  onRequirement:(itemId?:string)=>void;focusFindingId?:string;
};

/** Same main area as the requirement sheet. Saved text and selected card survive
 * task polling and navigation; only an explicit action calls the model. */
export function PrewriteReviewPanel(props:Props){
  const {project,busy,running,onRecheck}=props,report=project.prewrite_review;
  const findings:Obj[]=report?.findings||[],navigate=useNavigation();
  const selectionKey='ra-prewrite-card-'+project.id;
  const [selected,setSelected]=useState(()=>sessionStorage.getItem(selectionKey)||'');
  const [individualOpen,setIndividualOpen]=useState(false);
  const first=findings.find(finding=>finding.status!=='resolved')||findings[0];
  const finding=findings.find(value=>value.id===selected)||first;
  const heading=useRef<HTMLHeadingElement>(null);
  useEffect(()=>{if(finding&&selected!==finding.id){setSelected(finding.id);sessionStorage.setItem(selectionKey,finding.id);}},[finding?.id]);
  useEffect(()=>{if(props.focusFindingId&&findings.some(value=>value.id===props.focusFindingId)){setSelected(props.focusFindingId);setIndividualOpen(true);}},[props.focusFindingId]);
  function choose(id:string){navigate(()=>{setSelected(id);sessionStorage.setItem(selectionKey,id);requestAnimationFrame(()=>heading.current?.focus({preventScroll:true}));});}
  const waiting=busy||running,passed=prewritePassed(report),canDraft=prewriteCanDraft(report);
  const batch=!passed&&pendingChecks(project).length>0;
  return <section className="prewrite-review" aria-label="成文检查">
    <header className="prewrite-header"><div><p className="connected-kicker">成文前核对</p><h2 tabIndex={-1} ref={heading}>{prewriteLabel(report)}</h2>
      <p>{batch?'所有确认点集中在下表，统一保存后再整批复检。':'核对需求、字段、交互、流程、状态和验收是否一致。'}</p></div>
      <div className="prewrite-actions">{!batch&&<button type="button" className="primary" disabled={waiting||!!props.blockReason} onClick={()=>navigate(onRecheck)}>{running?'任务正在处理…':report?.status==='failed'?'重新进行成文检查':report?.id?'复检当前需求':'开始成文检查'}</button>}
      <button type="button" disabled={waiting} onClick={()=>props.onRequirement()}>返回需求列表</button></div>
    </header>
    {props.blockReason&&<p role="status" className="notice">{props.blockReason}</p>}
    {report?.status==='failed'?<div role="alert" className="prewrite-notice prewrite-failure-summary"><strong>检查未完成，回答已保留。</strong><span>可直接重试检查；下方未确认的修改仍可继续核对。</span>{batch&&<button type="button" disabled={waiting||!!props.blockReason} onClick={()=>navigate(onRecheck)}>重试本次检查</button>}<details><summary>查看失败详情</summary><p>{report.message}</p></details></div>:!batch&&report?.message&&<p role="status" className="prewrite-notice">{report.message}</p>}
    {!batch&&report?.id&&!report.current&&report.status!=='running'&&<p className="prewrite-notice">下方保留上次检查及处理记录，不能代表当前内容已通过。可以继续落实回答，然后复检。</p>}
    {passed&&<div className="prewrite-success" role="status"><strong>可以继续生成 PRD。</strong><p>本次通过只对应已检查的需求内容。生成后的文档还需要核对，原版本确认不会自动延续。</p><button type="button" className="primary" disabled={waiting} onClick={()=>navigate(()=>props.onGenerate(false))}>返回并继续生成 PRD</button></div>}
    {batch&&<PrewriteBatchTable project={project} draftTask={props.draftTask} busy={busy} running={running} mutate={props.mutate} onDraftAll={props.onDraftAll} onContinue={onRecheck} onReviewDraft={canDraft?()=>navigate(()=>props.onGenerate(true)):undefined} onRefresh={props.onRefresh} onSources={props.onSources}/>}
    {!!findings.length&&<details className="prewrite-optional-details" open={individualOpen} onToggle={e=>setIndividualOpen(e.currentTarget.open)}><summary>查看逐项依据与处理记录（可选）</summary>{individualOpen&&<>
      <div className="prewrite-card-navigation"><label>定位到检查项<select aria-label="定位到成文检查项" value={finding?.id||''} onChange={event=>choose(event.target.value)}>{findings.map((value,index)=><option key={value.id} value={value.id}>{index+1}. {value.saved_correction?'已有修改待确认：'+(project.items.find(item=>item.kind==='requirement'&&(value.related_refs||[]).includes(item.id))?.title||'关联需求'):value.title||value.message} · {prewriteFindingLabel(value)}</option>)}</select></label><span>{report?.blocking_count??findings.filter(value=>value.severity==='blocker'&&value.status!=='resolved').length} 项待处理 · {report?.resolved_count??findings.filter(value=>value.status==='resolved').length} 项已解决</span></div>
      {finding&&<PrewriteFinding key={finding.id} {...props} report={report!} finding={finding}/>}</>}</details>}
    {!findings.length&&!passed&&report?.status!=='running'&&<p className="prewrite-empty">{report?.status==='failed'?'本次检查没有完整结束，请重新检查。已有需求和旧文档保持可用。':'点击“开始成文检查”，结果会在这里显示；进入此页面不会调用模型。'}</p>}
    {canDraft&&!batch&&<div className="prewrite-draft-option"><strong>需要先讨论未决项？</strong><p>可生成明确标注未决问题的评审稿；这不会关闭检查项，也不能确认为研发交付版本。</p><button type="button" disabled={waiting} onClick={()=>navigate(()=>props.onGenerate(true))}>带未决项生成 PRD 评审稿</button></div>}
  </section>;
}

function PrewriteFinding(props:Props&{report:Obj;finding:Obj}){
  const {project,finding}=props,question=project.questions.find(q=>q.id===finding.question_id);
  return <article className="prewrite-finding" data-finding-id={finding.id}>
    <h3>{finding.title||'检查依据'}</h3><p className="preserve-lines">{finding.message}</p>
    {question&&<p><b>已保存回答：</b>{question.answer||'尚未确认，请在上方表格核对。'}</p>}
    {(finding.quotes||[]).map((quote:Obj,index:number)=><blockquote key={index}><b>{prewriteQuoteLabel(project,quote)}</b><p>{quote.text}</p></blockquote>)}
    {finding.suggested_resolution&&<p><b>建议：</b>{finding.suggested_resolution}</p>}
    {!!finding.source_refs?.length&&<button type="button" onClick={()=>props.onSources(finding.source_refs)}>查看来源依据</button>}
    {finding.response?.reason&&<p><b>已保存处理说明：</b>{finding.response.reason}</p>}
  </article>;
}
