import React, { useEffect, useRef, useState } from 'react';
import { type JsonObject as Obj, type Project,type ActionInput,type UserTask } from '../api';
import { useNavigation } from '../editing';
import {session} from '../draft-storage';
import {useDraftWriter} from '../use-draft-writer';
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
  const reviewFact=passed?'当前需求已通过本次成文检查。':batch?'还有 '+pendingChecks(project).length+' 个确认点需要集中核对。':report?.status==='running'?'成文检查正在进行，尚未形成新的通过结论。':report?.id&&!report.current?'需求内容已有变化，上次检查不能代表当前内容已通过。':report?.status==='failed'?'本次检查未完整结束，已有回答与修改仍保留。':report?.blocking_count?'还有 '+report.blocking_count+' 项检查问题待处理。':'尚未形成当前内容的通过结论。';
  const nextStep=passed?'生成 PRD，随后继续核对文档；复检仅作为次级操作。':batch?'先在批量表中保存确认点，再整批复检。':report?.status==='running'?'等待本次检查结束，不重复发起任务。':'先处理未通过或已失效的检查项，再复检当前需求。';
  return <section className="prewrite-review" aria-label="成文检查">
    <header className="prewrite-header"><div><p className="connected-kicker">成文前核对</p><h2 tabIndex={-1} ref={heading}>{prewriteLabel(report)}</h2>
      <p>{reviewFact}</p><p className="stage-next-step"><strong>下一步：</strong>{nextStep}</p></div>
      <div className="prewrite-actions">{!batch&&<button type="button" className={passed?undefined:'primary'} disabled={waiting||!!props.blockReason} onClick={()=>navigate(onRecheck)}>{running?'任务正在处理…':report?.status==='failed'?'重新进行成文检查':report?.id?'复检当前需求':'开始成文检查'}</button>}
      <button type="button" disabled={waiting} onClick={()=>props.onRequirement()}>返回需求列表</button></div>
    </header>
    {props.blockReason&&<p role="status" className="notice">{props.blockReason}</p>}
    {report?.status==='failed'?<div role="alert" className="prewrite-notice prewrite-failure-summary"><strong>检查未完成，回答已保留。</strong><span>可直接重试检查；下方未确认的修改仍可继续核对。</span>{batch&&<button type="button" disabled={waiting||!!props.blockReason} onClick={()=>navigate(onRecheck)}>重试本次检查</button>}<details><summary>查看失败详情</summary><p>{report.message}</p></details></div>:!batch&&report?.message&&<p role="status" className="prewrite-notice">{report.message}</p>}
    {!batch&&report?.id&&!report.current&&report.status!=='running'&&<p className="prewrite-notice">下方保留上次检查及处理记录，不能代表当前内容已通过。可以继续落实回答，然后复检。</p>}
    {passed&&canDraft&&<div className="prewrite-success" role="status"><strong>可以继续生成 PRD。</strong><p>本次通过只对应已检查的需求内容。生成后的文档还需要核对，原版本确认不会自动延续。</p><button type="button" className="primary" disabled={waiting} onClick={()=>navigate(()=>props.onGenerate(true))}>生成 PRD</button><button type="button" disabled={waiting} onClick={()=>navigate(()=>props.onGenerate(false))}>使用 AI 生成含流程图的 PRD</button></div>}
    {batch&&<PrewriteBatchTable project={project} draftTask={props.draftTask} busy={busy} running={running} mutate={props.mutate} onDraftAll={props.onDraftAll} onContinue={onRecheck} onReviewDraft={canDraft&&!passed?()=>navigate(()=>props.onGenerate(true)):undefined} onRefresh={props.onRefresh} onSources={props.onSources}/>}
    {!!findings.length&&<details className="prewrite-optional-details" open={individualOpen} onToggle={e=>setIndividualOpen(e.currentTarget.open)}><summary>查看逐项依据与处理记录（可选）</summary>{individualOpen&&<>
      <div className="prewrite-card-navigation"><label>定位到检查项<select aria-label="定位到成文检查项" value={finding?.id||''} onChange={event=>choose(event.target.value)}>{findings.map((value,index)=><option key={value.id} value={value.id}>{index+1}. {value.saved_correction?'已有修改待确认：'+(project.items.find(item=>item.kind==='requirement'&&(value.related_refs||[]).includes(item.id))?.title||'关联需求'):value.title||value.message} · {prewriteFindingLabel(value)}</option>)}</select></label><span>{report?.blocking_count??findings.filter(value=>value.severity==='blocker'&&value.status!=='resolved').length} 项待处理 · {report?.resolved_count??findings.filter(value=>value.status==='resolved').length} 项已解决</span></div>
      {finding&&<PrewriteFinding key={project.id+':'+finding.id} {...props} report={report!} finding={finding}/>}</>}</details>}
    {!findings.length&&!passed&&report?.status!=='running'&&<p className="prewrite-empty">{report?.status==='failed'?'本次检查没有完整结束，请重新检查。已有需求和旧文档保持可用。':'点击“开始成文检查”，结果会在这里显示；进入此页面不会调用模型。'}</p>}
    {canDraft&&!passed&&!batch&&<div className="prewrite-draft-option"><strong>需要先讨论未决项？</strong><p>可生成明确标注未决问题的评审稿；这不会关闭检查项，也不能确认为研发交付版本。</p><button type="button" disabled={waiting} onClick={()=>navigate(()=>props.onGenerate(true))}>带未决项生成 PRD 评审稿</button></div>}
  </section>;
}

function PrewriteFinding(props:Props&{report:Obj;finding:Obj}){
  const {project,finding}=props,question=project.questions.find(q=>q.id===finding.question_id),navigate=useNavigation();
  const draftKey='ra-prewrite-response-'+project.id+'-'+finding.id;
  const serverReason=typeof finding.response?.reason==='string'?finding.response.reason.trim():'';
  const [reason,setReason]=useState(()=>{
    const stored=session.getItem(draftKey);if(stored===null)return serverReason;
    try{const value=JSON.parse(stored);return typeof value==='string'?value:'';}catch{return '';}
  });
  const [saving,setSaving]=useState(false),[saved,setSaved]=useState(false),[error,setError]=useState('');
  const [persistedReason,setPersistedReason]=useState(serverReason);
  const writer=useDraftWriter(draftKey,()=>setError('浏览器未能保存草稿，输入仍保留在本页。'));
  const waiting=props.busy||props.running||saving,canRecheck=!!persistedReason&&reason.trim()===persistedReason;
  function edit(value:string){setReason(value);setSaved(false);writer.schedule(()=>({[draftKey]:value}));}
  async function save(){
    const value=reason.trim();
    if(!value){setError('请填写核对说明后再保存。');return;}
    if(value.length>6000){setError('核对说明不能超过 6000 字，请精简后再保存。');return;}
    if(!writer.flush())return;
    setSaving(true);setError('');
    try{
      await props.mutate('/prewrite-review/findings/'+finding.id+'/response',{
        expected_revision:project.revision,review_id:props.report.id,input_hash:props.report.input_hash,
        mode:'dispute',reason:value,
      });
      setPersistedReason(value);
      setSaved(true);
    }catch(e){setError(e instanceof Error?e.message:'核对说明保存失败，输入已保留，请稍后重试。');}
    finally{setSaving(false);}
  }
  return <article className="prewrite-finding" data-finding-id={finding.id}>
    <h3>{finding.title||'检查依据'}</h3><p className="preserve-lines">{finding.message}</p>
    {question&&<p><b>已保存回答：</b>{question.answer||'尚未确认，请在上方表格核对。'}</p>}
    {(finding.quotes||[]).map((quote:Obj,index:number)=><blockquote key={index}><b>{prewriteQuoteLabel(project,quote)}</b><p>{quote.text}</p></blockquote>)}
    {finding.suggested_resolution&&<p><b>建议：</b>{finding.suggested_resolution}</p>}
    {!!finding.source_refs?.length&&<button type="button" onClick={()=>props.onSources(finding.source_refs)}>查看来源依据</button>}
    {finding.response?.reason&&<p><b>已保存处理说明：</b>{finding.response.reason}</p>}
    {finding.status!=='resolved'&&<div className="prewrite-response">
      <label htmlFor={'prewrite-response-'+finding.id}><b>我的核对说明</b></label>
      <textarea id={'prewrite-response-'+finding.id} rows={5} value={reason} disabled={waiting}
        placeholder="请说明核对事实和依据，以及这条提醒为什么不适用或需要调整。"
        onChange={event=>edit(event.target.value)}/>
      <p>最多 6000 字。保存异议不会关闭这条提醒，也不代表正式检查通过；保存后仍需明确重新检查当前内容。</p>
      {error&&<p className="error" role="alert">{error}</p>}
      {saved&&<p role="status">核对说明已保存，提醒仍未关闭。现在可以带说明重新检查当前内容。</p>}
      <div className="prewrite-actions">
        <button type="button" disabled={waiting} onClick={save}>{saving?'正在保存…':'保存异议说明'}</button>
        {canRecheck&&<button type="button" className={prewritePassed(props.report)?undefined:'primary'} disabled={waiting||!!props.blockReason}
          onClick={()=>navigate(props.onRecheck)}>带说明重新检查</button>}
      </div>
    </div>}
  </article>;
}
