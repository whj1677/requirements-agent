import React,{useEffect,useRef,useState} from 'react';
import {api,type Project,type JsonObject as Obj,type ActionInput,type UserTask} from '../api';
import {inlinePrewriteFailure} from '../task-progress-state';
import {useDirty} from '../editing';
import {requiredIssues,aiInstructions} from './RequirementTable';
import {mergeSuggestions,sheetQuestions,inScopeQuestion,equal,linkedClausePayload,type Sheet} from './refinement-sheet-state';
import {initialBatch,pendingChecks,checkRoots,batchCompletion,batchImplementations,confirmationPayload,answerOffer,refreshBatchCandidates} from './prewrite-batch-state';
import {requirementBehaviorLabels as fields} from './requirement-fields';
import {DetailContractEditor} from './DetailContractEditor';
import './prewrite-batch.css';
import {businessText} from './prewrite-business-text';
import {BatchMissingReview} from './BatchMissingReview';
import {BatchConflictReview} from './BatchConflictReview';
import {batchConflicts,resolveBatchConflict} from './prewrite-conflicts';

type Props={project:Project;draftTask?:UserTask;busy:boolean;running:boolean;mutate:(path:string,body:Obj,method?:string)=>Promise<any>;
  onDraftAll:(instruction:string,request?:ActionInput['draft_request'])=>void;onContinue:()=>void;onReviewDraft?:()=>void;onRefresh:()=>void;onSources:(refs?:Obj[])=>void};
function BatchText({project,value,label,rows=3,disabled,onChange}:{project:Project;value:string;label:string;rows?:number;disabled:boolean;onChange:(value:string)=>void}){
  const [editing,setEditing]=useState(false),readable=businessText(project,value);
  return !editing&&readable!==value?<div className="batch-readable"><p>{readable}</p><button type="button" aria-label={'修改'+label} disabled={disabled} onClick={()=>setEditing(true)}>修改</button></div>:<textarea rows={rows} aria-label={label} disabled={disabled} value={value} onChange={e=>onChange(e.target.value)}/>;
}

export function PrewriteBatchTable(props:Props){
  const {project}=props,key=project.id+'-prewrite-batch';
  const [sheet,setSheet]=useState<Sheet|null>(null),[loaded,setLoaded]=useState(-1),[error,setError]=useState(''),[notice,setNotice]=useState('');
  const [working,setWorking]=useState(false),[dirty,setDirty]=useState(false),[savedRevision,setSavedRevision]=useState<number|null>(()=>Number(sessionStorage.getItem(key+'-confirmed'))||null);
  const [showAllFields,setShowAllFields]=useState(false);
  const [issueReview,setIssueReview]=useState<Obj[]|null>(null);
  const [conflictReview,setConflictReview]=useState(false);
  const clean=(text:string)=>businessText(project,text);
  const saved=savedRevision===project.revision;
  const baseline=useRef<Sheet|null>(null),current=useRef<Sheet|null>(null),lock=useRef(false),intent=useRef({body:'',key:''});
  current.current=sheet;
  const markSaved=useDirty(key,dirty,'成文检查确认表',async()=>{persist();setDirty(false);},()=>{
    if(baseline.current)setSheet(structuredClone(baseline.current));sessionStorage.removeItem(key);setDirty(false);
  });
  function persist(value=current.current){if(value)sessionStorage.setItem(key,JSON.stringify({sheet:value,base:baseline.current}));}
  useEffect(()=>{let live=true;
    void api<Obj>('/projects/'+project.id+'/requirement-confirmation/state').then(state=>{
      if(!live)return;if(state.revision!==project.revision){setError('项目正在更新，请稍候读取最新内容。');return;}
      const fresh=initialBatch(project,state.rows);let mine=current.current,base=baseline.current;
      if(!mine)try{const local=JSON.parse(sessionStorage.getItem(key)||'null');mine=local?.sheet||null;base=local?.base||null;}catch{}
      const next=refreshBatchCandidates(project,mine?mergeSuggestions(mine,base,fresh):fresh,fresh);
      baseline.current=fresh;setSheet(next);setLoaded(project.revision);setError('');persist(next);
    }).catch(e=>{if(live)setError((e as Error).message);});return()=>{live=false;};
  },[project.id,project.revision]);
  function change(next:Sheet){setSheet(next);persist(next);setDirty(true);setSavedRevision(null);sessionStorage.removeItem(key+'-confirmed');setError('');}
  const disabled=props.busy||props.running||working||loaded!==project.revision;
  if(!sheet)return <section className="prewrite-batch" aria-label="成文检查确认表"><p>正在读取全部确认点和已有草稿…</p>{error&&<p role="alert">{error}</p>}</section>;
  const qs=sheetQuestions(project).filter(q=>inScopeQuestion(project,q,sheet.scope)),checks=pendingChecks(project),completion=batchCompletion(project);
  const witnesses=batchImplementations(project,sheet);
  const missing=requiredIssues(sheet,qs,project);
  const conflicts=sheet.merge_conflicts||[];
  const conflictEntries=batchConflicts(project,sheet,baseline.current);
  const offered=new Set(checks.flatMap((f:Obj)=>f.saved_correction?.candidate_ids||[]));
  const offeredRoots=new Set(project.items.filter(i=>offered.has(i.id)&&i.kind==='requirement').map(i=>i.target_item_id));
  const rootChanged=(id:string)=>{
    const row=sheet.rows[id],original=project.items.find(i=>i.id===id);
    return !!row&&!!original&&(!equal(row.statement,original.statement)||Object.keys(fields).some(field=>(row.behavior[field]||'')!==(original.behavior?.[field]||''))||!!row.candidate_id||offeredRoots.has(id)||
      row.acceptance_items.some(ac=>!!ac.candidate_id||ac.statement!==project.items.find(i=>i.id===ac.id)?.statement));
  };
  const ready=(finding:Obj)=>{
    const roots=checkRoots(project,finding);
    return saved||!!roots.length&&roots.every(id=>rootChanged(id)||(completion?.complete&&completion.covered_requirement_ids?.includes(id)));
  };
  const left=checks.filter((f:Obj)=>!ready(f));
  const checkedAcceptance=new Set((project.prewrite_review?.findings||[]).filter((f:Obj)=>f.status!=='resolved').flatMap((f:Obj)=>f.related_refs||[]));
  const draftReport=(project as Obj).business_draft_reviews?.slice().reverse().find((r:Obj)=>r.run_id===completion?.run_id);
  const draftProblems=completion&&!completion.complete?[...(draftReport?.rejected||[]),...(draftReport?.field_rejected||[])]:[];
  async function draftAll(){
    if(lock.current||disabled)return;
    persist();setDirty(false);markSaved();setError('');
    setIssueReview(null);setConflictReview(false);
    const instruction=aiInstructions('请一次处理 prewrite_confirmation_points 中全部待确认点，并把每个已保存回答落实到所有直接关联需求、业务说明与验收。已有明确回答原文保留，不重问、不发散新问题。给出具体可直接确认的修改；没有既定规则的地方写成具体推荐方案。已有人工修改保留，同一条款只返回一份合并后的修改，避免逐项覆盖。当前表内校验缺项：'+JSON.stringify(missing)+'。逐项补齐详细定义，枚举须列出实际可选值；资料没有实际名单时明确给出待确认建议，不能编造厂家名称，也不能假称已补全。',sheet!,baseline.current);
    props.onDraftAll(instruction,{mode:'prewrite'});
  }
  async function confirmAll(){
    if(lock.current||disabled)return;setError('');
    if(conflictEntries.length||conflicts.length){setIssueReview(null);setConflictReview(true);return;}
    if(missing.length){setIssueReview(missing);return;}
    if(left.length||conflicts.length){setError(left.length?'还有 '+left.length+' 个检查点缺少可确认的修改，可用上方 AI 一次起草全部。':'手工修改已保留，请核对按钮上方的合并提示。');return;}
    setIssueReview(null);setConflictReview(false);
    const payload=confirmationPayload(project,sheet!);const signature=JSON.stringify(payload);
    if(intent.current.body!==signature)intent.current={body:signature,key:crypto.randomUUID()};
    lock.current=true;setWorking(true);
    try{
      const result=await props.mutate('/refinement-confirmation',{...payload,idempotency_key:intent.current.key});
      if(!result.confirmed||result.implementation_pending_ids?.length){
        const issues=(result.issues||[]).filter((r:Obj)=>r.item_id||r.question_id||r.field);
        if(issues.length)setIssueReview(issues.map((r:Obj)=>({...r,kind:r.kind||(r.item_id?'requirement':'question')})));
        setError(clean(result.message||result.issues?.[0]?.message||'部分回答尚未落实，已保留整张表，请核对提示。'));return;
      }
      sessionStorage.removeItem(key);setDirty(false);markSaved();setSavedRevision(result.project.revision);sessionStorage.setItem(key+'-confirmed',String(result.project.revision));setNotice('本表已一次确认保存。接下来只对整批内容复检，不逐项调用模型。');
      props.onContinue();
    }catch(e){setError((e as Error).message);}finally{lock.current=false;setWorking(false);}
  }
  function setField(id:string,field:string,value:string){const row=sheet!.rows[id];change({...sheet!,rows:{...sheet!.rows,[id]:field==='statement'?{...row,statement:value}:{...row,behavior:{...row.behavior,[field]:value}}}});}
  return <section className="prewrite-batch" aria-label="成文检查确认表">
    <header className="prewrite-batch-heading"><div><h3>集中核对，一次确认</h3><p>查看、编辑、确认均不调用 AI。缺少修改草稿时批量起草，确认后只整批复检一次。</p></div>
      <button type="button" className="primary" disabled={disabled||saved} onClick={()=>void draftAll()}>AI 批量起草全部修改</button></header>
    {props.draftTask&&inlinePrewriteFailure(props.draftTask)&&<div role="alert" className="prewrite-batch-warning batch-draft-failure"><div><strong>本次 AI 起草未完成，原稿和修改已保留。</strong><button type="button" disabled={disabled} onClick={()=>void draftAll()}>重新起草全部修改</button></div><details><summary>查看失败原因</summary><p>{clean(props.draftTask.message)}</p></details></div>}
    {props.onReviewDraft&&<div className="prewrite-batch-warning"><strong>可以先生成完整的 PRD 评审稿</strong><p>直接使用已保存内容，不调用 AI、不改写需求。未决问题随文档列出，后续仍可继续核对。</p><button type="button" disabled={disabled} onClick={props.onReviewDraft}>先生成待确认评审稿</button></div>}
    <div className="prewrite-batch-scroll" hidden={!!issueReview||conflictReview}>
      {!!draftProblems.length&&<details className="prewrite-batch-warning"><summary>上次批量起草有 {draftProblems.length} 处内容未形成可用修改；已有回答和原条款已保留</summary><p>可直接核对下方已生成内容。缺少的修改可整批重新起草，不需要逐项操作。</p>{draftProblems.map((r:Obj,n:number)=><p key={n}>{clean(r.reason||'该部分未通过格式与来源核对。')}</p>)}</details>}
      <table aria-label="全部成文检查确认点"><thead><tr><th>需要确认的点</th><th>已保存回答 / 推荐方案</th><th>建议与对应修改</th><th>当前状态</th></tr></thead><tbody>
        {checks.map((finding:Obj,index:number)=>{const q=qs.find(q=>q.id===finding.question_id),ids=checkRoots(project,finding),offer=answerOffer(project,finding),hasText=!!sheet.answers[q?.id||'']?.trim();const labels=[...new Set((finding.quotes||[]).map((v:Obj)=>v.path==='statement'?'需求正文':fields[v.path?.replace('behavior.','') as keyof typeof fields]).filter(Boolean))].join('、');return <tr key={finding.id} data-check-id={finding.id}>
          <th scope="row"><span>{index+1}. {finding.saved_correction?'已有需求与验收修改':q?.question?.startsWith('请核对是否按已引用依据')?labels+'需统一核对':clean(q?.question||finding.title||finding.message)}</span><small>{ids.map(id=>project.items.find(i=>i.id===id)?.title).join('、')}</small></th>
          <td>{q&&offer.kind==='missing'&&!hasText&&<div className="batch-answer-missing"><b>本轮检查尚未生成回答草稿</b><p>检查给出的修改建议：{clean(finding.suggested_resolution||finding.message)}</p><small>点击上方“AI 批量起草全部修改”，一次生成全部回答及条款修改。</small></div>}
            {q?<BatchText project={project} label={'检查点 '+(index+1)+' 的回答'} disabled={disabled} value={sheet.answers[q.id]||''} onChange={value=>change({...sheet,answers:{...sheet.answers,[q.id]:value}})}/>:<p>在下方条款中核对本项修改。</p>}
            {q&&hasText&&<small>{sheet.answers[q.id]!==offer.text?'当前填写的回答草稿，整表确认后才保存。':offer.kind==='saved'?'已保存的原话，直接沿用；不满意可改。':offer.kind==='draft'?'AI 推荐回答已填入，核对后可整表确认；不满意可改。':'当前填写的回答草稿，整表确认后才保存。'}</small>}</td>
          <td><p className="batch-check-suggestion">{clean(finding.suggested_resolution||finding.message)}</p><button type="button" onClick={()=>document.getElementById(key+'-'+ids[0])?.scrollIntoView({block:'start',behavior:'smooth'})}>查看下方对应条款</button>
            <details><summary>原检查依据</summary><p>{finding.message}</p>{(finding.quotes||[]).map((quote:Obj,n:number)=><blockquote key={n}>{quote.text}</blockquote>)}</details></td>
          <td><span className={ready(finding)?'batch-ready':'batch-missing'}>{saved?'已保存':ready(finding)?'待统一确认':'待起草修改'}</span></td>
        </tr>})}
      </tbody></table>
      <h4>本次写入的需求与验收 · 共享条款只编辑一次</h4>
      <label className="batch-field-toggle"><input type="checkbox" checked={showAllFields} onChange={e=>setShowAllFields(e.target.checked)}/> 展开全部业务字段（默认仅显示修改或检查涉及的字段）</label>
      <table aria-label="本次统一确认的条款修改"><thead><tr><th>条款</th><th>当前内容</th><th>确认后写入 · 可直接修改</th></tr></thead><tbody>
        {sheet.scope.map(id=>{const row=sheet.rows[id],original=project.items.find(i=>i.id===id);if(!row||!original)return null;return <React.Fragment key={id}>
          <tr id={key+'-'+id}><th scope="row">{original.title}<label>本次性质<select aria-label={original.title+' 检查后性质'} disabled={disabled} value={row.change_type} onChange={e=>change({...sheet,rows:{...sheet.rows,[id]:{...row,change_type:e.target.value}}})}>
            <option value="unspecified">待明确</option><option value="new">新增功能</option><option value="modified">修改已有功能</option><option value="preserved">保持已有功能</option></select></label></th><td className="preserve-lines">{clean(original.statement)}</td><td><BatchText project={project} rows={4} label={original.title+' 确认后正文'} disabled={disabled} value={row.statement} onChange={value=>setField(id,'statement',value)}/>
            <button type="button" onClick={()=>props.onSources(project.items.find(i=>i.id===row.candidate_id)?.source_refs||original.source_refs)}>核对来源</button></td></tr>
          {Object.entries(fields).filter(([field])=>showAllFields||(row.behavior[field]||'')!==(original.behavior?.[field]||'')||checks.some((f:Obj)=>checkRoots(project,f).includes(id)&&(f.quotes||[]).some((q:Obj)=>q.object_id===id&&q.path==='behavior.'+field))).map(([field,label])=><tr key={field}><th scope="row">{original.title} · {label}</th><td className="preserve-lines">{clean(original.behavior?.[field]||'未填写')}</td><td><BatchText project={project} rows={2} label={original.title+' 确认后'+label} disabled={disabled} value={row.behavior[field]||''} onChange={value=>setField(id,field,value)}/></td></tr>)}
          {row.acceptance_items.map((ac,n)=>showAllFields||checkedAcceptance.has(ac.id)||ac.candidate_id||ac.ai_suggested||ac.statement!==project.items.find(i=>i.id===ac.id)?.statement?<tr key={ac.id||n}><th scope="row">{original.title} · 验收条件{checkedAcceptance.has(ac.id)&&<small>本轮检查涉及，请核对</small>}</th><td className="preserve-lines">{clean(project.items.find(i=>i.id===ac.id)?.statement||'新增验收建议')}</td><td><BatchText project={project} rows={3} label={original.title+' 确认后验收 '+(n+1)} disabled={disabled} value={ac.statement} onChange={value=>change({...sheet,rows:{...sheet.rows,[id]:{...row,acceptance_items:row.acceptance_items.map((v,i)=>i===n?{...v,statement:value}:v)}}})}/></td></tr>:null)}
          {(row.detail_contract||original.detail_required)&&<tr><th scope="row">{original.title} · 详细定义</th><td colSpan={2}><details><summary>字段、交互、流程与状态</summary><DetailContractEditor requirementId={id} title={original.title} value={row.detail_contract} base={original.detail_contract} conflicts={row.detail_conflicts||[]} disabled={disabled} references={project.items.map(i=>({id:i.id,title:i.title||i.id,kind:i.kind}))} onSources={props.onSources} onChange={(detail_contract,detail_conflicts)=>change({...sheet,rows:{...sheet.rows,[id]:{...row,detail_contract,detail_conflicts:detail_conflicts??row.detail_conflicts}}})}/></details></td></tr>}
        </React.Fragment>;})}
        {linkedClausePayload(project,sheet).map(clause=><tr key={clause.item_id}><th scope="row">{project.items.find(i=>i.id===clause.item_id)?.title||'关联规则'}</th><td>{clean(project.items.find(i=>i.id===clause.item_id)?.statement||'')}</td><td><BatchText project={project} rows={3} label={'关联规则 '+(project.items.find(i=>i.id===clause.item_id)?.title||'业务说明')} disabled={disabled} value={clause.statement} onChange={value=>change({...sheet,linked_clauses:{...sheet.linked_clauses,[clause.item_id]:{...clause,statement:value}}})}/></td></tr>)}
      </tbody></table>
      <details className="batch-context"><summary>沿用的项目摘要、范围与回答落实原文</summary><p>本次范围：{sheet.scope.map(id=>project.items.find(i=>i.id===id)?.title).join('、')}</p>
        {Object.entries(sheet.values).map(([field,value])=><p key={field}>{String(value||'未填写')}</p>)}
        {qs.map(q=><div key={q.id}><b>{clean(q.question)}</b><p>{sheet.answers[q.id]||'尚未填写'}</p>{(witnesses[q.id]||[]).map((r:Obj,n:number)=><blockquote key={n}>{r.quote}</blockquote>)}</div>)}</details>
    </div>
    {issueReview&&<BatchMissingReview project={project} sheet={sheet} issues={issueReview} pendingIssues={missing} disabled={disabled} onChange={change} onDraft={()=>void draftAll()} onClose={()=>setIssueReview(null)}/>}
    {conflictReview&&<BatchConflictReview project={project} sheet={sheet} fresh={baseline.current} disabled={disabled} onChange={change} onClose={()=>setConflictReview(false)}/>}
    {(!!conflicts.length||!!conflictEntries.length)&&!conflictReview&&<div role="alert" className="prewrite-batch-warning">新建议与手工修改有差异，你的内容已保留。<button type="button" onClick={()=>{setIssueReview(null);setConflictReview(true);}}>比较并处理冲突</button><button type="button" onClick={()=>{let next=sheet;for(const entry of conflictEntries)next=resolveBatchConflict(project,next,entry,false,baseline.current);change({...next,merge_conflicts:[]});}}>保留全部手工修改，继续核对</button></div>}
    {error&&<p role="alert" className="connected-error">{error}</p>}{notice&&<p role="status">{notice}</p>}
    <footer className="prewrite-batch-footer"><span>{checks.length} 个确认点 · {saved?'整批修改已保存':conflictEntries.length||conflicts.length?'请核对冲突内容':missing.length?<button type="button" onClick={()=>{setConflictReview(false);setIssueReview(missing);}}>{missing.length} 项需要补齐，点击在此处理</button>:left.length?left.length+' 项待起草':'已有草稿可统一确认'}</span>
      <div className="batch-footer-actions"><button type="button" className="primary" disabled={disabled} onClick={()=>saved?props.onContinue():void confirmAll()}>{working?'正在统一保存…':saved?'继续整批复检':'确认全部修改并复检'}</button></div></footer>
  </section>;
}
