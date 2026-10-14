import React, { useEffect, useRef, useState } from 'react';
import { api, type JsonObject as Obj, type Project } from '../api';
import { useNavigation } from '../editing';
import { useDiscussionDraft } from '../use-discussion-draft';
import { documentDiscussionAnswer } from '../document-discussion-context';
import { prewriteCanDraft, prewriteFindingLabel, prewriteLabel, prewritePassed, prewriteQuoteLabel } from '../prewrite-review';
import { AnswerConfirmation } from './AnswerConfirmation';
import './prewrite-review.css';

type Props={
  project:Project;busy:boolean;running:boolean;blockReason:string;
  mutate:(path:string,body:Obj,method?:string)=>Promise<any>;
  onRefresh:()=>void;onRecheck:()=>void;onGenerate:(allowReviewDraft?:boolean)=>void;
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
  const first=findings.find(finding=>finding.status!=='resolved')||findings[0];
  const finding=findings.find(value=>value.id===selected)||first;
  const heading=useRef<HTMLHeadingElement>(null);
  useEffect(()=>{if(finding&&selected!==finding.id){setSelected(finding.id);sessionStorage.setItem(selectionKey,finding.id);}},[finding?.id]);
  useEffect(()=>{if(props.focusFindingId&&findings.some(value=>value.id===props.focusFindingId))setSelected(props.focusFindingId);},[props.focusFindingId]);
  function choose(id:string){navigate(()=>{setSelected(id);sessionStorage.setItem(selectionKey,id);requestAnimationFrame(()=>heading.current?.focus({preventScroll:true}));});}
  const waiting=busy||running,passed=prewritePassed(report),canDraft=prewriteCanDraft(report);
  return <section className="prewrite-review" aria-label="成文检查">
    <header className="prewrite-header"><div><p className="connected-kicker">成文前核对</p><h2 tabIndex={-1} ref={heading}>{prewriteLabel(report)}</h2>
      <p>核对需求、字段、交互、流程、状态和验收是否一致。保存回答后，关联修改仍需确认并复检。</p></div>
      <div className="prewrite-actions"><button type="button" className="primary" disabled={waiting||!!props.blockReason} onClick={()=>navigate(onRecheck)}>{running?'任务正在处理…':report?.status==='failed'?'重新进行成文检查':report?.id?'复检当前需求':'开始成文检查'}</button>
      <button type="button" disabled={waiting} onClick={()=>props.onRequirement()}>返回需求列表</button></div>
    </header>
    {props.blockReason&&<p role="status" className="notice">{props.blockReason}</p>}
    {report?.message&&<p role={report.status==='failed'?'alert':'status'} className={report.status==='failed'?'connected-error':'prewrite-notice'}>{report.message}</p>}
    {report?.id&&!report.current&&report.status!=='running'&&<p className="prewrite-notice">下方保留上次检查及处理记录，不能代表当前内容已通过。可以继续落实回答，然后复检。</p>}
    {passed&&<div className="prewrite-success" role="status"><strong>可以继续生成 PRD。</strong><p>本次通过只对应已检查的需求内容。生成后的文档还需要核对，原版本确认不会自动延续。</p><button type="button" className="primary" disabled={waiting} onClick={()=>navigate(()=>props.onGenerate(false))}>返回并继续生成 PRD</button></div>}
    {!!findings.length&&<>
      <div className="prewrite-card-navigation"><label>定位到检查项<select aria-label="定位到成文检查项" value={finding?.id||''} onChange={event=>choose(event.target.value)}>{findings.map((value,index)=><option key={value.id} value={value.id}>{index+1}. {value.title||value.message} · {prewriteFindingLabel(value)}</option>)}</select></label><span>{report?.blocking_count??findings.filter(value=>value.severity==='blocker'&&value.status!=='resolved').length} 项待处理 · {report?.resolved_count??findings.filter(value=>value.status==='resolved').length} 项已解决</span></div>
      {finding&&<PrewriteFinding key={finding.id} {...props} report={report!} finding={finding}/>}</>}
    {!findings.length&&!passed&&report?.status!=='running'&&<p className="prewrite-empty">{report?.status==='failed'?'本次检查没有完整结束，请重新检查。已有需求和旧文档保持可用。':'点击“开始成文检查”，结果会在这里显示；进入此页面不会调用模型。'}</p>}
    {canDraft&&<div className="prewrite-draft-option"><strong>需要先讨论未决项？</strong><p>可生成明确标注未决问题的评审稿；这不会关闭检查项，也不能确认为研发交付版本。</p><button type="button" disabled={waiting} onClick={()=>navigate(()=>props.onGenerate(true))}>带未决项生成 PRD 评审稿</button></div>}
  </section>;
}

function PrewriteFinding(props:Props&{report:Obj;finding:Obj}){
  const {project,finding,report,busy,running,mutate,onRefresh}=props;
  const question=project.questions.find(value=>value.id===finding.question_id);
  const [noteMode,setNoteMode]=useState<'defer'|'dispute'|null>(null);
  const responseDraft=useDiscussionDraft(project.id,'prewrite-note','',finding.id,finding.response?.reason||'');
  const [working,setWorking]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
  const lock=useRef(false);
  async function saveNote(){
    if(lock.current||!noteMode)return;
    if(!responseDraft.text.trim()){setError(noteMode==='dispute'?'请说明为什么不是冲突，或补充适用条件。':'请记录尚未确定的内容，便于下次继续。');return;}
    lock.current=true;setWorking(true);setError('');
    try{
      await mutate('/prewrite-review/findings/'+encodeURIComponent(finding.id)+'/response',{review_id:report.id,input_hash:report.input_hash,mode:noteMode,reason:responseDraft.text});
      responseDraft.reset(responseDraft.text);setNoteMode(null);setNotice(noteMode==='dispute'?'异议理由已保存，复检会核对这项解释；当前检查项仍保留。':'已记录暂未确定，关键问题继续保留。');onRefresh();
    }catch(e){setError((e as Error).message);}finally{lock.current=false;setWorking(false);}
  }
  const disabled=busy||running||working,related: string[]=finding.related_refs||[];
  return <article className="prewrite-finding" data-finding-id={finding.id}>
    <div className="prewrite-finding-heading"><h3>{finding.title||'需要核对的业务问题'}</h3><span>{prewriteFindingLabel(finding)}</span></div>
    <p className="preserve-lines">{finding.message}</p>
    {!!finding.quotes?.length&&<div className="prewrite-quotes" aria-label="需要核对的原文">{finding.quotes.map((quote:Obj,index:number)=><blockquote key={quote.object_id+':'+quote.path+':'+index}><b>{prewriteQuoteLabel(project,quote)}</b><p>{quote.text}</p>{(quote.path||quote.object_id)&&<details><summary>定位信息</summary><small>{quote.object_id}{quote.path&&' / '+quote.path}</small></details>}</blockquote>)}</div>}
    {finding.impact&&<p><b>对实现的影响：</b>{finding.impact}</p>}
    {finding.suggested_resolution&&<p className="prewrite-suggestion"><b>待核对建议：</b>{finding.suggested_resolution}</p>}
    {!!related.length&&<div className="prewrite-related"><span>关联内容</span>{related.map(id=><button type="button" key={id} onClick={()=>props.onRequirement(id)}>{project.items.find(item=>item.id===id)?.title||id}</button>)}</div>}
    {!!finding.source_refs?.length&&<button type="button" onClick={()=>props.onSources(finding.source_refs)}>查看来源依据</button>}
    {finding.status!=='resolved'&&question&&<PrewriteAnswer key={question.decision_group_id||question.id} {...props} question={question} disabled={working} onNotice={setNotice}/>}
    {finding.status!=='resolved'&&!question&&<p className="prewrite-notice">{finding.severity!=='blocker'?'这项建议不阻塞成文。若需要修改，可以核对关联需求。':finding.kind==='structure'?'请进入关联需求修正结构或引用，再复检。':'本项尚无可确认的业务问题，请先核对关联内容，再复检。'}<button type="button" disabled={disabled} onClick={()=>props.onRequirement(related[0])}>核对关联需求</button></p>}
    {finding.status==='resolved'&&<p className="prewrite-success">这项问题已在复检中解决；记录保留供追溯。</p>}
    {finding.status!=='resolved'&&<div className="prewrite-note-actions"><button type="button" disabled={disabled} aria-expanded={noteMode==='defer'} onClick={()=>{setNoteMode(noteMode==='defer'?null:'defer');setError('');}}>暂未确定</button>
      {finding.kind!=='structure'&&<button type="button" disabled={disabled} aria-expanded={noteMode==='dispute'} onClick={()=>{setNoteMode(noteMode==='dispute'?null:'dispute');setError('');}}>这不是冲突，补充理由</button>}</div>}
    {noteMode&&<div className="prewrite-note-editor"><label>{noteMode==='dispute'?'适用条件或异议理由':'暂未确定的内容'}<textarea rows={3} disabled={disabled} value={responseDraft.text} onChange={event=>responseDraft.change(event.target.value)}/></label><p>这条记录不会自动回答业务问题，也不会直接关闭检查项。</p><button type="button" disabled={disabled||!responseDraft.text.trim()} onClick={()=>void saveNote()}>{working?'正在保存…':'保存说明'}</button><button type="button" disabled={disabled} onClick={()=>setNoteMode(null)}>收起，保留草稿</button></div>}
    {finding.response?.reason&&<details><summary>已保存的处理说明</summary><p>{finding.response.reason}</p></details>}
    {notice&&<p role="status" className="prewrite-notice">{notice}</p>}
    {(error||responseDraft.error)&&<p role="alert" className="connected-error">{error||responseDraft.error}</p>}
  </article>;
}

function PrewriteAnswer(props:Props&{finding:Obj;question:Obj;disabled:boolean;onNotice:(message:string)=>void}){
  const {project,question,finding,mutate,onRefresh}=props,qid=question.decision_group_id||question.id;
  const saved=documentDiscussionAnswer(project,question);
  const draft=useDiscussionDraft(project.id,'prewrite-answer','',qid,saved);
  const dirty=draft.text!==draft.base,[working,setWorking]=useState(false),[error,setError]=useState('');
  const lock=useRef(false);
  useEffect(()=>{if(!dirty)draft.reset(saved);},[saved]);
  const decisions=question.decision_group_id?project.questions.filter(value=>value.decision_group_id===question.decision_group_id&&!value.superseded_by):[question];
  const options:Obj[]=(finding.options?.length?finding.options:question.options||[]).map((option:Obj|string)=>typeof option==='string'?{label:option,value:option}:{...option,label:option.label||option.title||option.text,value:option.value||option.text||option.label||option.title});
  async function saveAnswer(){
    if(lock.current)throw Error('回答正在保存，请稍后继续。');
    if(!draft.text.trim())throw Error('请先填写已经确定的规则。');
    lock.current=true;setWorking(true);setError('');
    try{
      const latest=await api<Project>('/projects/'+project.id),current=latest.questions.find(value=>value.id===question.id);
      if(!current||current.superseded_by)throw Error('此问题已变化，你的输入仍保留，请刷新核对。');
      if(documentDiscussionAnswer(latest,current)!==draft.base)throw Error('这条回答已经在别处更新，你的输入仍保留，请先核对最新内容。');
      const result=await mutate('/decision-review/answer',{expected_revision:latest.revision,question_id:qid,answer:draft.text});
      // mutate already refreshes the project. Starting another workspace action
      // here would occupy its lock and swallow the following clarify request.
      draft.reset(draft.text);return result;
    }catch(e){setError((e as Error).message);throw e;}finally{lock.current=false;setWorking(false);}
  }
  return <div className="prewrite-answer">
    {!!options.length&&<fieldset className="prewrite-options"><legend>可选方案（不会默认替你决定）</legend>{options.filter(option=>option.label&&option.value).map((option,index)=><button key={index} type="button" disabled={props.busy||props.running||props.disabled||working} aria-pressed={draft.text===option.value} onClick={()=>draft.change(option.value)}>{option.label}{option.description&&<small>{option.description}</small>}</button>)}<p>点击后仅填入回答草稿；也可直接填写自己的规则。</p></fieldset>}
    <AnswerConfirmation project={project} question={question} decisions={decisions} answer={draft.text} answerDirty={dirty}
      onAnswerChange={draft.change} onSaveAnswer={saveAnswer} busy={props.busy||props.disabled||working} running={props.running} blockReason={props.blockReason}
      proposalCurrent={question.decision_review_proposal_current===true&&!dirty} onOrganize={()=>props.onClarify(question.id)} mutate={mutate}
      onSaved={(_latest,message,pending)=>{props.onNotice(message+(pending?'请继续落实关联条款。':'正在准备复检当前需求。'));if(pending)onRefresh();else props.onRecheck();}}
      confirmLabel="确认修改并复检" onSources={props.onSources} onScope={()=>props.onRequirement()} onRefresh={onRefresh}/>
    {(error||draft.error)&&<p className="connected-error" role="alert">{error||draft.error}</p>}
    {dirty&&<small>回答草稿已在浏览器保留；查看关联修改时才保存为依据。</small>}
  </div>;
}
