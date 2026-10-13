import React, { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { api, Project, Blocker, BlockerAction } from '../api';
import { useDirty, useNavigation } from '../editing';
import { scopeLabels, isScopeFieldRequired, ScopeProfile, FieldState } from '../scope-draft';
import { requirementBehaviorLabels as fields } from './requirement-fields';
import './refinement-sheet.css';
import type {PreparationStatus} from '../refinement-preparation';
import {canReturnToDelivery,type RefinementReturnState} from './refinement-return';
import {Row,Sheet,LinkedClause,equal,requirements,uncertain,inScopeQuestion,sheetQuestions,initial,mergeSuggestions,requirementPayload,restoreSheet,resolveExpiredAcceptance,clearAnswer,linkedClauseItems,answeredLinkedClauses,linkedClausePayload,retainSheetImplementations,sheetAnswerImplementations,sheetImplementationTargets,pendingSheetAnswers} from './refinement-sheet-state';
import {AnswerImplementation} from './AnswerImplementation';
import {nextPendingAnswer, refinementActionCopy} from './refinement-navigation';
import {DetailContractEditor} from './DetailContractEditor';
import {detailEditingIssues} from './detail-contract';

type Obj = Record<string, any>;
type Props = {
  project:Project; busy:boolean; active:boolean; mutate:(path:string,body:Obj)=>Promise<any>;
  onSuggest:(instruction:string)=>void; onRefresh:()=>void; onSources:(refs?:Obj[])=>void; onRecommend:(id:string,instruction:string)=>void;
  onCreate:()=>void; onResolve:(action:BlockerAction,row:Blocker)=>void;
  onQuestionReview:(id:string)=>void; onStageConfirmed:()=>void;
  onReturnAvailabilityChange?:(state:RefinementReturnState)=>void;
  focus?:{itemId?:string;questionId?:string;field?:string;scope?:boolean;nonce:number};
};
type Issue = {kind:string;field?:string;path?:string;item_id?:string;question_id?:string;message:string};
const changeLabels={unspecified:'请选择本次改动',new:'新增功能',modified:'修改已有功能',preserved:'保持已有功能'};
const fieldHelp:Record<string,string>={actor:'例如操作人员或系统角色。资料已说明时，直接核对即可。',entry:'说明从哪里进入，以及操作前需要什么状态；不相关可留空。',flow:'按实际操作顺序说明关键步骤；不用写代码实现。',data:'说明要查看、填写或处理的数据。未涉及可留空。',permissions:'说明谁可以查看或操作；不要凭行业惯例假定权限。',result:'说明操作完成后，用户会看到什么或数据如何变化。',exceptions:'说明已知的失败、空数据或边界情况；没有依据可留空。'};
function allowedStates(key:string,profile:ScopeProfile){
  const result=['known','unknown'];
  if(['value','priority','out_of_scope'].includes(key))result.push('not_applicable','none');
  if(['current_state','preserve_scope'].includes(key)&&!isScopeFieldRequired(key,profile))result.push('not_applicable');
  if(key==='preserve_scope')result.push('none');return result;
}
export function aiInstructions(purpose:string,sheet:Sheet,base:Sheet|null){
  const edits={summary:Object.fromEntries(Object.entries(sheet.values).filter(([k,v])=>!base||!equal(v,base.values[k]))),
    requirements:Object.values(sheet.rows).filter(row=>!base||!equal(row,base.rows[row.requirement_id])),
    answers:Object.fromEntries(Object.entries(sheet.answers).filter(([k,v])=>!base||!equal(v,base.answers[k]))),
    linked_clauses:Object.values(sheet.linked_clauses||{}).filter(row=>!base||!equal(row,base.linked_clauses?.[row.item_id]))};
  if(!purpose&&!Object.keys(edits.summary).length&&!edits.requirements.length&&!Object.keys(edits.answers).length&&!edits.linked_clauses.length)return '';
  const text=JSON.stringify(edits);
  return purpose+(text.length<10500?' 本表未提交的人工修改（仅供起草参考，仍待确认）：'+text:' 本表较长的人工修改保留在浏览器，未附带本次请求；请依据项目已保存聊天和资料提供建议，由用户对照修改。');
}
function issueKey(issue:Issue){return issue.kind==='context'?'context-'+issue.field:issue.kind==='question'?'question-'+issue.question_id:issue.kind==='clause'?'clause-'+issue.item_id:issue.kind==='requirement'?'requirement-'+issue.item_id+'-'+(issue.path||issue.field||'statement'):issue.kind;}
function requiredIssues(sheet:Sheet,questions:Obj[],project:Project):Issue[]{
  const result:Issue[]=[];
  for(const key of Object.keys(scopeLabels)){
    const state=sheet.states[key]?.status||'unknown',value=String(sheet.values[key]||'').trim();
    if(!allowedStates(key,sheet.profile).includes(state)||state==='known'&&!value||isScopeFieldRequired(key,sheet.profile)&&(state==='unknown'||state==='known'&&uncertain(value)))
      result.push({kind:'context',field:key,message:'请明确'+scopeLabels[key]+'；确实不适用时请选择允许的适用状态。'});
  }
  if(!sheet.scope.length)result.push({kind:'scope',message:'请至少将一条需求列为“本期包含”。'});
  for(const id of sheet.scope){const row=sheet.rows[id];if(!row)continue;
    if(!row.statement.trim())result.push({kind:'requirement',item_id:id,field:'statement',message:'请填写需求内容。'});
    if(!['new','modified','preserved'].includes(row.change_type))result.push({kind:'requirement',item_id:id,field:'change_type',message:'请选择新增、修改或保持，以本次实际需求为准。'});
    if(!row.acceptance_items.some(a=>a.statement.trim()))result.push({kind:'requirement',item_id:id,field:'acceptance',message:'请填写至少一条可验证的验收条件，也可以让 AI 先起草。'});
    if(row.acceptance_items.some(a=>a.invalid_candidate))result.push({kind:'requirement',item_id:id,field:'acceptance',message:'已使用的 AI 验收建议发生变化或不再可用。请在此格采用已保存内容、明确保留为人工修改，或使用新的建议。'});
    if(project.items.find(item=>item.id===id)?.detail_required&&!row.detail_contract)result.push({kind:'requirement',item_id:id,path:'detail_contract',message:'本需求需要详细定义，请先使用 AI 草稿或启用字段、交互与流程表。'});
    result.push(...detailEditingIssues(row.detail_contract).map(issue=>({...issue,kind:'requirement',item_id:id})));
    result.push(...(row.detail_conflicts||[]).map(conflict=>({kind:'requirement',item_id:id,path:conflict.path,message:'详细定义有版本冲突，请比较你的修改和最新内容后选择。'})));
  }
  for(const q of questions)if(q.blocking!==false&&(!q.out_of_scope_reason||q.scope_exclusion)&&(!sheet.answers[q.id]?.trim()||uncertain(sheet.answers[q.id])))
    result.push({kind:'question',question_id:q.id,message:'这项业务决定尚未明确，请填写或修改推荐回答。'});
  for(const row of linkedClausePayload(project,sheet))if(!row.statement.trim())result.push({kind:'clause',item_id:row.item_id,message:'请填写关联业务规则正文，或清空尚不确定的可选回答。'});
  return result;
}

export function DraftPreparationNotice({missing,preparation}:{missing:{id:string;title:string}[];preparation:PreparationStatus|null}){
  if(!missing.length)return null;
  const coverage=preparation?.draft_coverage;
  const missingFromResult=!coverage||missing.some(row=>coverage.missing_acceptance_ids.includes(row.id));
  const attempted=missingFromResult&&(preparation?.status==='ready_with_limits'||preparation?.status==='failed');
  return <details className="sheet-notice sheet-preparation-notice"><summary><strong>{missing.length} 条需求待补充验收内容</strong><span>查看说明</span></summary><div><p>{attempted?'本次已尝试起草，部分验收仍未形成可用建议；已有草稿和人工修改已保留。':missingFromResult?'这些内容还没有形成可核对的验收草稿，可以用上方“AI 补充建议”一次起草整表。':'这些单元格当前为空，你的本地修改已保留；请补充内容或重新核对已有建议。'}</p>{preparation?.reason&&missingFromResult&&<p>{preparation.reason}</p>}<p>待补充：{missing.slice(0,3).map(row=>row.title).join('、')}{missing.length>3?'等 '+missing.length+' 条':''}。可让 AI 一次补充后再核对，也可以自行填写；系统不会自动重复调用。</p></div></details>;
}

export function LinkedClauseEditor({item,row,disabled,onChange}:{item:Obj;row:LinkedClause;disabled:boolean;onChange:(patch:Partial<LinkedClause>)=>void}){
  const [visibleAtOpen]=useState(()=>Object.keys(fields).filter(f=>row.behavior[f]?.trim()||item.behavior?.[f]?.trim()));
  const visible=Object.keys(fields).filter(f=>visibleAtOpen.includes(f)||item.behavior?.[f]?.trim());
  const remaining=Object.keys(fields).filter(f=>!visible.includes(f));
  const editor=(f:string)=><label key={f} style={{display:'block',marginTop:8}}>{fields[f]}<textarea rows={2} aria-label={item.title+' 关联规则 '+fields[f]} disabled={disabled} value={row.behavior[f]||''} placeholder="选填；原资料未说明时无需补造" onChange={e=>onChange({behavior:{...row.behavior,[f]:e.target.value}})}/></label>;
  return <><label>规则正文<textarea rows={3} aria-label={item.title+' 关联规则正文'} disabled={disabled} value={row.statement} onChange={e=>onChange({statement:e.target.value})}/></label>{visible.map(editor)}{!!remaining.length&&<details><summary>其他业务说明（选填）</summary>{remaining.map(editor)}</details>}</>;
}

export function RequirementTable(props:Props){
  const {project}=props,key=project.id+'-refinement-sheet';
  const [sheet,setSheet]=useState<Sheet|null>(null),[states,setStates]=useState<Obj[]>([]),[loaded,setLoaded]=useState(-1);
  const [preparationInfo,setPreparationInfo]=useState<PreparationStatus|null>(null);
  const [working,setWorking]=useState(false),[loadError,setLoadError]=useState(''),[error,setError]=useState(''),[notice,setNotice]=useState(''),[issues,setIssues]=useState<Issue[]>([]),[dirty,setDirty]=useState(false);
  const [stale,setStale]=useState(false),[reload,setReload]=useState(0),[archived,setArchived]=useState(()=>!!sessionStorage.getItem(key+'-previous'));
  const [focusedAnswer,setFocusedAnswer]=useState(''),[expandedBehavior,setExpandedBehavior]=useState<Record<string,boolean>>({}),[expandedDetail,setExpandedDetail]=useState<Record<string,boolean>>({});
  const [focusMode,setFocusMode]=useState(false),[jumpRequirement,setJumpRequirement]=useState('');
  const lock=useRef(false),root=useRef<HTMLElement>(null),current=useRef<Sheet|null>(null),baseline=useRef<Sheet|null>(null),intent=useRef({signature:'',key:''}),navigate=useNavigation();
  current.current=sheet;
  useEffect(()=>{let live=true;void api<PreparationStatus>('/projects/'+project.id+'/refinement-preparation').then(value=>{if(live)setPreparationInfo(value);}).catch(()=>{if(live)setPreparationInfo(null);});return()=>{live=false;};},[project.id,project.revision,reload]);
  useEffect(()=>{let live=true;setLoadError('');
    void api<Obj>('/projects/'+project.id+'/requirement-confirmation/state').then(response=>{
      if(!live)return;if(response.revision!==project.revision){setLoadError('项目正在更新，请刷新后读取最新内容。');return;}
      setStates(response.rows);setLoaded(response.revision);
      const fresh=initial(project,response.rows),previous=current.current;
      let saved:Sheet|null=previous,base=baseline.current;
      if(!saved){try{saved=JSON.parse(sessionStorage.getItem(key)||'null');base=JSON.parse(sessionStorage.getItem(key+'-base')||'null');}catch{}}
      if(previous?.revision===project.revision)return;
      if(saved?.rows&&saved?.values){
        const next=restoreSheet(saved,base,fresh);
        if(saved.revision!==project.revision){sessionStorage.setItem(key+'-previous',JSON.stringify(saved));setArchived(true);}
        baseline.current=fresh;setSheet(next);setStale(!!next.merge_conflicts?.length);
        sessionStorage.setItem(key,JSON.stringify(next));sessionStorage.setItem(key+'-base',JSON.stringify(fresh));
        setNotice(next.merge_conflicts?.length?'新建议已补入未修改的内容；有冲突的单元格保留你的修改，请核对下方提示。':saved.revision===project.revision?'已恢复当前浏览器中未提交的表格；尚未正式确认。':'AI 新建议已补入表格，你的手工修改已保留；核对后可一次确认。');
      }else{baseline.current=fresh;setSheet(fresh);}
    }).catch(e=>{if(live)setLoadError(e.message);});return()=>{live=false;};
  },[project.id,project.revision,reload]);
  function replace(value:Sheet){const next=retainSheetImplementations(value,current.current);if(baseline.current)sessionStorage.setItem(key+'-base',JSON.stringify(baseline.current));setSheet(next);setDirty(true);setError('');sessionStorage.setItem(key,JSON.stringify(next));}
  async function saveDraft(){if(current.current)sessionStorage.setItem(key,JSON.stringify(current.current));setDirty(false);markSaved();setNotice('表格草稿已保存在当前浏览器，尚未确认。');}
  function discard(){sessionStorage.removeItem(key);setSheet(baseline.current);setDirty(false);setStale(false);setIssues([]);setError('');}
  const markSaved=useDirty(key,dirty,'需求确认表',saveDraft,discard);
  const canReturn=canReturnToDelivery(project,sheet,states,loaded,stale)&&!loadError&&!!sheet&&!pendingSheetAnswers(project,sheet).length;
  const pendingCount=sheet?pendingSheetAnswers(project,sheet).length:0;
  const missingCount=sheet?requiredIssues(sheet,sheetQuestions(project).filter(q=>inScopeQuestion(project,q,sheet.scope)),project).length:0;
  const actionCopy=refinementActionCopy(canReturn,pendingCount,missingCount);
  useLayoutEffect(()=>{props.onReturnAvailabilityChange?.({projectId:project.id,revision:project.revision,canReturn,pendingAnswerCount:pendingCount,missingCount});return()=>props.onReturnAvailabilityChange?.({projectId:project.id,revision:project.revision,canReturn:false});},[project.id,project.revision,canReturn,pendingCount,missingCount,props.onReturnAvailabilityChange]);
  function locate(issue:Issue){
    if(issue.kind==='question')setFocusedAnswer(issue.question_id||'');
    if(issue.kind==='requirement'&&issue.item_id&&issue.field&&Object.keys(fields).includes(issue.field))setExpandedBehavior(value=>({...value,[issue.item_id!]:true}));
    if(issue.kind==='requirement'&&issue.item_id&&(issue.path||issue.field||'').startsWith('detail_contract'))setExpandedDetail(value=>({...value,[issue.item_id!]:true}));
    requestAnimationFrame(()=>{const row=root.current?.querySelector<HTMLElement>('[data-cell-key="'+CSS.escape(issueKey(issue))+'"]')||root.current?.querySelector<HTMLElement>('[data-requirement-id="'+CSS.escape(issue.item_id||'')+'"]');let parent=row;while(parent&&parent!==root.current){if(parent instanceof HTMLDetailsElement)parent.open=true;parent=parent.parentElement;}const input=row?.querySelector<HTMLElement>('textarea,select,input')||row?.querySelector<HTMLElement>('button')||(issue.kind==='scope'?root.current?.querySelector<HTMLElement>('.sheet-requirement select'):null);(input||row)?.scrollIntoView({block:'center',behavior:'instant'});input?.focus({preventScroll:true});});
  }
  function locateSection(key:string){const row=root.current?.querySelector<HTMLElement>('[data-section="'+key+'"]');row?.scrollIntoView({block:'start',behavior:'instant'});row?.focus({preventScroll:true});}
  function locatePending(direction:1|-1=1){const id=sheet&&nextPendingAnswer(pendingSheetAnswers(project,sheet).map(q=>q.id),focusedAnswer,direction,sheetQuestions(project).map(q=>q.id));if(id)locate({kind:'question',question_id:id,message:''});}
  useEffect(()=>{if(!sheet||!props.focus)return;const focus=props.focus;locate(focus.questionId?{kind:'question',question_id:focus.questionId,message:''}:focus.field?{kind:'context',field:focus.field,message:''}:focus.itemId?{kind:linkedClauseItems(project).some(i=>i.id===focus.itemId)?'clause':'requirement',item_id:focus.itemId,message:''}:{kind:'scope',message:''});},[props.focus?.nonce,!!sheet]);
  function loadLatest(){if(sheet){sessionStorage.setItem(key+'-previous',JSON.stringify(sheet));setArchived(true);}const fresh=initial(project,states),next=sheet?mergeSuggestions(sheet,baseline.current,fresh):fresh;baseline.current=fresh;next.merge_conflicts=[];setSheet(next);setStale(false);setDirty(false);markSaved();sessionStorage.removeItem(key);setIssues([]);setError('');sessionStorage.setItem(key,JSON.stringify(next));sessionStorage.setItem(key+'-base',JSON.stringify(fresh));setNotice('已更新建议，保留你修改过的单元格；请核对后确认。原表格也保留为副本。');}
  async function confirm(){
    if(lock.current||!sheet||props.busy||loaded!==project.revision)return;
    if(canReturn){props.onStageConfirmed();return;}
    const missing=requiredIssues(sheet,sheetQuestions(project).filter(q=>inScopeQuestion(project,q,sheet.scope)),project);setIssues(missing);setError('');
    if(missing.length){setError('还有 '+missing.length+' 项必填信息未完成，请处理表格中标红的内容。');locate(missing[0]);return;}
    if(stale){setError('项目版本已变化，请先载入最新内容并核对；当前草稿仍保留。');return;}
    const payload={expected_revision:sheet.revision,review_confirmed:true,
      context:{values:sheet.values,field_states:sheet.states,scope_profile:sheet.profile,scope_ids:sheet.scope},
      rows:sheet.scope.map(id=>({...requirementPayload(sheet.rows[id]),
        confirmed_question_ids:sheetQuestions(project).filter(q=>inScopeQuestion(project,q,sheet.scope)&&(q.related_refs||[]).includes(id)&&!!sheet.answers[q.id]?.trim()).map(q=>q.id)})),
      answers:sheetQuestions(project).filter(q=>inScopeQuestion(project,q,sheet.scope)).map(q=>({question_id:q.id,answer:sheet.answers[q.id]||''})),linked_clauses:linkedClausePayload(project,sheet),
      answer_implementations:sheetAnswerImplementations(project,sheet)};
    const signature=JSON.stringify(payload);if(intent.current.signature!==signature)intent.current={signature,key:crypto.randomUUID()};
    lock.current=true;setWorking(true);
    try{const result=await props.mutate('/refinement-confirmation',{...payload,idempotency_key:intent.current.key});
      if(!result.confirmed){setIssues(result.issues||[]);setError(result.message||'请核对表格中提示的内容，本次没有写入。');if(result.issues?.length)locate(result.issues[0]);return;}
      sessionStorage.removeItem(key);sheet.scope.forEach(id=>sessionStorage.removeItem(project.id+'-table-draft-'+id));
      const written=result.project as Project;const confirmedStates=sheet.scope.map(id=>({requirement_id:id,current:true}));const accepted=initial(written,confirmedStates);baseline.current=accepted;setSheet(accepted);setStates(confirmedStates);setLoaded(written.revision);setStale(false);setDirty(false);markSaved();setIssues([]);
      const pending=result.implementation_pending_ids||pendingSheetAnswers(written,accepted).map(q=>q.id);
      if(pending.length||result.destination==='refinement'){
        sessionStorage.setItem(key,JSON.stringify(accepted));sessionStorage.setItem(key+'-base',JSON.stringify(accepted));
        setNotice('回答和条款已保存，'+(pending.length?pending.length+' 项回答':'仍有回答')+'待落实到正文。可以用“AI 补充建议”一次起草，再核对原文位置；当前表格已保留。');
        return;
      }
      props.onStageConfirmed();
    }catch(e){setError((e as Error).message);if((e as Obj).code==='STALE_REVISION'){setStale(true);props.onRefresh();}}
    finally{lock.current=false;setWorking(false);}
  }
  const disabled=props.busy||working||loaded!==project.revision;
  const reqs=requirements(project),questions=sheetQuestions(project);
  function renderRow(category:string,label:string,cellKey:string,editor:React.ReactNode,required=false,hint:React.ReactNode=null){
    const errors=issues.filter(i=>issueKey(i)===cellKey);
    return <tr key={cellKey} data-cell-key={cellKey} className={errors.length?'sheet-invalid':undefined}><th scope="row"><small>{category}</small><strong>{label}</strong>{required&&<span className="sheet-required">必填</span>}</th><td>{editor}</td><td>{errors.map((e,n)=><p role="alert" className="sheet-error" key={n}>{e.message}</p>)}{hint}</td></tr>;
  }
  if(!sheet)return <section className="refinement-sheet" aria-label="表格完善需求"><h2>完善需求</h2><p role="status">正在读取已保存内容与确认状态…</p>{loadError&&<p role="alert">{loadError}<button type="button" onClick={()=>setReload(x=>x+1)}>重新读取</button></p>}</section>;
  const updateRow=(id:string,patch:Partial<Row>)=>replace({...sheet,rows:{...sheet.rows,[id]:{...sheet.rows[id],...patch}}});
  const missing=requiredIssues(sheet,questions.filter(q=>inScopeQuestion(project,q,sheet.scope)),project);
  const clauseItems=answeredLinkedClauses(project,sheet),clauseRows=linkedClausePayload(project,sheet);
  const updateClause=(id:string,patch:Partial<LinkedClause>)=>replace({...sheet,linked_clauses:{...sheet.linked_clauses,[id]:{...clauseRows.find(row=>row.item_id===id)!,...patch}}});
  const missingAcceptance=reqs.filter(item=>sheet.scope.includes(item.id)&&!sheet.rows[item.id]?.acceptance_items.some(ac=>ac.statement.trim()));
  const pendingAnswers=pendingSheetAnswers(project,sheet);
  return <section ref={root} className="refinement-sheet" data-focus-mode={focusMode} aria-label="表格完善需求" onFocusCapture={event=>{
      const key=(event.target as HTMLElement).closest('[data-cell-key^="question-"]')?.getAttribute('data-cell-key');
      if(key)setFocusedAnswer(key.slice('question-'.length));
    }}>
    <header className="refinement-sheet-heading"><div><h2>完善需求</h2><p>核对表格与答案对应的原文位置。可以先保存；回答已落实后进入交付。</p></div><div className="sheet-view-actions"><span>{sheet.scope.length} 条本期需求 · {missing.length?'还有 '+missing.length+' 项待补充':pendingAnswers.length?pendingAnswers.length+' 项回答待落实':'必填信息已填入'}</span><button type="button" className="primary" title="AI 可一次起草需求、验收和答案对应的正文；你核对后确认。" disabled={disabled} onClick={()=>void saveDraft().then(()=>navigate(()=>props.onSuggest(aiInstructions(pendingAnswers.length?'请将已填写答案起草到直接关联需求的正文、业务说明和必要验收；移除已解决的待明确占位，不替用户新增决定。':'',sheet,baseline.current))))}>AI 补充建议</button><button type="button" disabled={disabled} onClick={()=>void saveDraft().then(()=>navigate(props.onCreate))}>补充需求</button><button type="button" aria-pressed={focusMode} onClick={()=>setFocusMode(value=>!value)}>{focusMode?'退出专注核对':'专注核对'}</button></div></header>
    <nav className="sheet-navigation" aria-label="需求表定位">
      <button type="button" onClick={()=>locateSection('context')}>项目摘要</button>
      <button type="button" onClick={()=>locateSection('requirements')}>需求与验收</button>
      <button type="button" className="sheet-pending-jump" onClick={()=>pendingAnswers.length?locatePending():locateSection('answers')}>待落实回答{pendingAnswers.length?' · '+pendingAnswers.length:''}</button>
      {!!clauseItems.length&&<button type="button" onClick={()=>locateSection('clauses')}>关联规则</button>}
      {!!reqs.length&&<label className="sheet-requirement-jump"><span>定位需求</span><select aria-label="定位到需求" value={jumpRequirement} onChange={event=>{const id=event.target.value;setJumpRequirement(id);if(id)locate({kind:'requirement',item_id:id,message:''});}}><option value="">选择需求…</option>{reqs.map((item,index)=><option key={item.id} value={item.id}>{index+1}. {item.title}{sheet.scope.includes(item.id)?'':'（本期不包含）'}</option>)}</select></label>}
      <span className="sheet-pending-controls"><button type="button" disabled={!pendingAnswers.length} onClick={()=>locatePending(-1)}>上一项待落实</button><button type="button" disabled={!pendingAnswers.length} onClick={()=>locatePending(1)}>下一项待落实</button></span>
    </nav>
    <DraftPreparationNotice missing={missingAcceptance.map(item=>({id:item.id,title:item.title}))} preparation={preparationInfo}/>
    {(error||loadError)&&<div role="alert" className="sheet-banner-error">{error||loadError}{!!issues.length&&<button type="button" onClick={()=>locate(issues[0])}>定位第一处</button>}{loadError&&<button type="button" onClick={()=>setReload(x=>x+1)}>重新读取</button>}</div>}
    {stale&&<div className="sheet-banner-error">{(sheet.merge_conflicts?.length?sheet.merge_conflicts:['项目有新内容，本地填写已保留。']).map((message,n)=><p key={n}>{message}</p>)}<button type="button" disabled={disabled} onClick={loadLatest}>保留我的修改，继续核对</button></div>}
    {notice&&<p className="sheet-notice" role="status">{notice}</p>}
    <div className="refinement-sheet-scroll" tabIndex={0} aria-label="滚动核对全部信息"><table aria-label="需求确认表"><colgroup><col style={{width:'20%'}}/><col style={{width:'59%'}}/><col style={{width:'21%'}}/></colgroup><thead><tr><th>需确认的信息</th><th>当前内容 · 可直接修改</th><th>填写提示与来源</th></tr></thead><tbody>
      <tr className="sheet-group" data-section="context" tabIndex={-1}><th colSpan={3}>1. 项目摘要 <small>已有聊天和资料整理的内容直接展示，确认前可修改</small></th></tr>
      {renderRow('适用场景','这次属于哪种需求','profile',<div className="sheet-inline"><select aria-label="需求类型" value={sheet.profile.scenario} disabled={disabled} onChange={e=>{const profile={...sheet.profile,scenario:e.target.value as ScopeProfile['scenario']};const states={...sheet.states};for(const k of ['current_state','preserve_scope'])if(!sheet.values[k]?.trim())states[k]={status:isScopeFieldRequired(k,profile)?'unknown':'not_applicable'};replace({...sheet,profile,states});}}><option value="unspecified">尚未确定</option><option value="new_product">全新产品</option><option value="new_module">新增模块</option><option value="modify_existing">修改已有功能</option></select>{sheet.profile.scenario==='new_module'&&<select aria-label="模块关联" disabled={disabled} value={sheet.profile.dependencies} onChange={e=>{const profile={...sheet.profile,dependencies:e.target.value as ScopeProfile['dependencies']};const states={...sheet.states};for(const k of ['current_state','preserve_scope'])if(!sheet.values[k]?.trim())states[k]={status:isScopeFieldRequired(k,profile)?'unknown':'not_applicable'};replace({...sheet,profile,states});}}><option value="unknown">关联尚未确定</option><option value="independent">独立模块</option><option value="shared">涉及其他模块</option></select>}</div>,false,'新产品和独立模块不要求填写现状与保持项。')}
      {Object.entries(scopeLabels).map(([field,label])=>{const state=sheet.states[field]||{status:'unknown'},optional=!isScopeFieldRequired(field,sheet.profile),proposal=project.product_context_proposal?.[field],fromAI=!String(project.product_context?.[field]||'').trim()&&!!proposal;
        return renderRow('项目摘要',label,'context-'+field,<><textarea rows={2} aria-label={label} disabled={disabled} value={sheet.values[field]||''} placeholder={optional?'选填；没有相关内容可留空':'请核对或补充'+label} onChange={e=>replace({...sheet,values:{...sheet.values,[field]:e.target.value},states:{...sheet.states,[field]:{...state,status:e.target.value.trim()?'known':'unknown'}}})}/><select className="sheet-status-select" aria-label={label+' 状态'} value={state.status} disabled={disabled} onChange={e=>replace({...sheet,states:{...sheet.states,[field]:{...state,status:e.target.value as FieldState['status']}}})}>{allowedStates(field,sheet.profile).map(s=><option key={s} value={s}>{{known:'内容已明确，随本次确认',unknown:optional?'选填，暂未说明':'尚待明确',not_applicable:'本需求不适用',none:'已确认没有额外约束'}[s]}</option>)}</select></>,!optional,<>{fromAI?<span className="sheet-origin">AI 整理 · 待你确认</span>:<span className="sheet-origin">{sheet.values[field]?'已保存内容':'暂无内容'}</span>}{optional&&<p>选填，不影响进入交付。</p>}{fromAI&&(project as Obj).product_context_proposal_evidence?.[field]?.source_refs?.length>0&&<button type="button" onClick={()=>props.onSources((project as Obj).product_context_proposal_evidence[field].source_refs)}>查看来源</button>}</>);
      })}
      <tr className="sheet-group" data-cell-key="scope" data-section="requirements" tabIndex={-1}><th colSpan={3}>2. 本期需求与验收 <small>范围只是本期包含与否，无需逐条勾选、采纳或再次确认</small>{issues.filter(i=>i.kind==='scope').map((i,n)=><p role="alert" key={n} className="sheet-error">{i.message}</p>)}</th></tr>
      {!reqs.length&&<tr><td colSpan={3}>尚无需求草稿。<button type="button" disabled={disabled} onClick={props.onCreate}>补充需求</button></td></tr>}
      {reqs.map(item=>{const row=sheet.rows[item.id];if(!row)return null;const included=sheet.scope.includes(item.id),confirmed=states.find(s=>s.requirement_id===item.id)?.current;return <React.Fragment key={item.id}>
        <tr className="sheet-requirement" data-requirement-id={item.id}><th scope="row">{item.title}</th><td><label className="sheet-inline">本期范围<select aria-label={item.title+' 本期范围'} disabled={disabled} value={included?'included':'excluded'} onChange={e=>replace({...sheet,scope:e.target.value==='included'?[...sheet.scope,item.id]:sheet.scope.filter(id=>id!==item.id)})}><option value="included">本期包含</option><option value="excluded">暂不纳入本期</option></select></label></td><td><span className="sheet-origin">{confirmed?'已确认内容':row.candidate_id?'AI 修订建议':'待确认草稿'}</span><button type="button" onClick={()=>props.onSources((project.items.find(i=>i.id===row.candidate_id)||item).source_refs||[])}>查看来源</button></td></tr>
        {included?<>
          {renderRow(item.title,'需求内容','requirement-'+item.id+'-statement',<textarea rows={4} aria-label={item.title+' 需求内容'} disabled={disabled} value={row.statement} onChange={e=>updateRow(item.id,{statement:e.target.value})}/>,true)}
          {renderRow(item.title,'本次改动','requirement-'+item.id+'-change_type',<select aria-label={item.title+' 本次改动'} disabled={disabled} value={row.change_type} onChange={e=>updateRow(item.id,{change_type:e.target.value})}>{Object.entries(changeLabels).map(([v,l])=><option key={v} value={v}>{l}</option>)}</select>,true)}
          {renderRow(item.title,'业务说明','requirement-'+item.id+'-behavior-summary',<>
            <button type="button" aria-expanded={!!expandedBehavior[item.id]} aria-label={(expandedBehavior[item.id]?'收起':'展开')+'业务说明：'+item.title} onClick={()=>setExpandedBehavior(value=>({...value,[item.id]:!value[item.id]}))}>{expandedBehavior[item.id]?'收起业务说明':'展开业务说明'}</button>
            <small>已填写 {Object.keys(fields).filter(field=>row.behavior[field]?.trim()).length} / {Object.keys(fields).length} 项</small>
            <p className="sheet-behavior-summary">{['actor','flow','permissions'].filter(field=>row.behavior[field]?.trim()).map(field=>fields[field]+'：'+row.behavior[field].slice(0,70)+(row.behavior[field].length>70?'…':'')).join(' · ')||'暂无业务说明，需要时可展开填写。'}</p>
          </>,false,'根据当前资料查看或修改；未说明的可选内容保留为空。')}
          {expandedBehavior[item.id]&&Object.entries(fields).map(([field,label])=>renderRow(item.title,label,'requirement-'+item.id+'-'+field,<textarea rows={2} aria-label={item.title+' '+label} disabled={disabled} value={row.behavior[field]||''} placeholder="资料未说明时可保留为空；不确定的规则不要编造" onChange={e=>updateRow(item.id,{behavior:{...row.behavior,[field]:e.target.value}})}/>,false,<><p>{fieldHelp[field]}</p>{row.candidate_id&&<span className="sheet-origin">AI 草稿 · 请核对后确认</span>}</>))}
          <tr data-cell-key={'requirement-'+item.id+'-detail_contract'}><th scope="row"><small>{item.title}</small><strong>详细定义</strong><span className="sheet-origin">字段 · 交互 · 流程 · 状态</span></th><td colSpan={2}>
            <button type="button" aria-expanded={expandedDetail[item.id]??!!row.detail_contract} onClick={()=>setExpandedDetail(value=>({...value,[item.id]:!(value[item.id]??!!row.detail_contract)}))}>{(expandedDetail[item.id]??!!row.detail_contract)?'收起详细定义':'展开详细定义'}</button>
            {issues.filter(issue=>issue.item_id===item.id&&(issue.path||issue.field)==='detail_contract').map((issue,index)=><p role="alert" className="sheet-error" key={index}>{issue.message}</p>)}
            {!row.detail_contract&&<p className="sheet-origin">{item.detail_required?'本需求需要补充详细定义，随整表确认。':'历史需求保留原有内容；需要时启用详细定义。'}{row.detail_suggestion?'已有 AI 详细定义草稿可供核对。':''}</p>}
            {(expandedDetail[item.id]??!!row.detail_contract)&&<DetailContractEditor requirementId={item.id} title={item.title} value={row.detail_contract} base={item.detail_contract} candidate={row.detail_suggestion} disabled={disabled} conflicts={row.detail_conflicts} issues={issues.filter(issue=>issue.item_id===item.id&&(issue.path||issue.field||'').startsWith('detail_contract')).map(issue=>({path:issue.path||issue.field!,message:issue.message}))} references={project.items.filter(ref=>!ref.target_item_id&&!ref.superseded_by&&!['rejected','deferred','excluded'].includes(ref.selection_status)).map(ref=>({id:ref.id,title:ref.title||ref.statement,kind:ref.kind}))} onSources={props.onSources} onChange={(detail_contract,detail_conflicts)=>updateRow(item.id,{detail_contract,...(detail_conflicts?{detail_conflicts}:{})})}/>}
          </td></tr>
          {renderRow(item.title,'验收条件','requirement-'+item.id+'-acceptance',<div className="sheet-acceptances">{!row.acceptance_items.some(ac=>ac.statement.trim())&&<p className="sheet-origin">本格验收内容为空。可用表头“AI 补充建议”一次起草整表；你只需核对或修改，不必逐格发起。</p>}{(row.acceptance_items.length?row.acceptance_items:[{statement:''}]).map((ac,n)=><label key={ac.id||n}>验收 {n+1}{(ac.ai_suggested||ac.candidate_id)&&<span className="sheet-origin">AI 建议 · 随本次确认</span>}<textarea rows={3} aria-label={item.title+' 验收建议 '+(n+1)} disabled={disabled} value={ac.statement} onChange={e=>{const items=[...(row.acceptance_items.length?row.acceptance_items:[ac])];items[n]={...ac,statement:e.target.value};updateRow(item.id,{acceptance_items:items});}}/>{ac.invalid_candidate&&<div role="alert" className="sheet-group-reference"><strong>先核对这条失效的 AI 建议</strong><p>原建议已被更新、暂缓或拒绝。你的文字已保留，需要明确处理后才能确认。</p><button type="button" disabled={disabled} onClick={()=>{const items=[...row.acceptance_items];items[n]=resolveExpiredAcceptance(ac,'saved');updateRow(item.id,{acceptance_items:items});}}>采用当前已保存内容</button><button type="button" disabled={disabled} onClick={()=>{const items=[...row.acceptance_items];items[n]=resolveExpiredAcceptance(ac,'manual');updateRow(item.id,{acceptance_items:items});}}>我已核对，保留为人工修改</button></div>}{ac.conflict_statement&&<div className="sheet-group-reference"><strong>最新已保存内容（与你的修改不同）</strong><p>{ac.conflict_statement}</p></div>}{(ac.suggestions||[]).filter(s=>s.candidate_id!==ac.candidate_id).map(suggestion=><div className="sheet-group-reference" key={suggestion.candidate_id}><span className="sheet-origin">AI 修订建议 · 尚未使用</span><p>{suggestion.statement}</p><button type="button" disabled={disabled} onClick={()=>{const items=[...row.acceptance_items];items[n]={...ac,statement:suggestion.statement,candidate_id:suggestion.candidate_id,invalid_candidate:undefined,conflict_statement:undefined};updateRow(item.id,{acceptance_items:items});}}>使用此验收建议</button></div>)}{!(ac.id&&project.items.find(i=>i.id===ac.id)?.selection_status==='selected')&&row.acceptance_items.length>1&&<button type="button" disabled={disabled} onClick={()=>updateRow(item.id,{acceptance_items:row.acceptance_items.filter((_,k)=>k!==n)})}>移除此条验收</button>}</label>)}<button type="button" disabled={disabled} onClick={()=>updateRow(item.id,{acceptance_items:[...row.acceptance_items,{statement:''}]})}>添加验收</button></div>,true,<><p>至少一条：执行什么操作，应看到什么结果。</p><button type="button" disabled={disabled} onClick={()=>void saveDraft().then(()=>navigate(()=>props.onRecommend(item.id,aiInstructions('请根据本条已保存资料补充或修正业务说明和验收，未知关键决定保留待确认。', {...sheet,rows:{[item.id]:row}},baseline.current))))}>请 AI 完善此需求</button></>)}
        </>:<tr><td colSpan={3} className="sheet-excluded">{row.statement} <small>本期不提交此条，原有内容和确认历史保留。</small></td></tr>}
      </React.Fragment>;})}
      <tr className="sheet-group" data-section="answers" tabIndex={-1}><th colSpan={3}>3. 回答与落实位置 <small>回答可先保存；落实需要核对直接关联条款中的原文位置</small></th></tr>
      {questions.map(q=>!inScopeQuestion(project,q,sheet.scope)?renderRow('本期以外',q.question,'question-'+q.id,<p>{sheet.answers[q.id]||'尚未回答'}</p>,false,'关联需求已暂不纳入本期，本次保留原话，不确认回答。'):renderRow('业务决定',q.question,'question-'+q.id,<>{q.decision_group_id&&!q.answer&&<p className="sheet-group-reference">本组原话（仅供参考，请分别回答本题）：{[...((project as Obj).decision_answers||[])].reverse().find((a:Obj)=>a.question_id===q.decision_group_id)?.text||'尚无原话'}</p>}{!!q.options?.length&&<span className="sheet-origin">AI 推荐回答 · 点击填入后可修改</span>}<div className="sheet-answer-options">{(q.options||[]).map((option:string,n:number)=><button type="button" key={n} disabled={disabled} onClick={()=>replace({...sheet,answers:{...sheet.answers,[q.id]:option}})}>{option}</button>)}</div><textarea rows={3} aria-label={q.question+' 我的回答'} disabled={disabled} value={sheet.answers[q.id]||''} onChange={e=>replace({...sheet,answers:{...sheet.answers,[q.id]:e.target.value}})}/>{clearAnswer(sheet.answers[q.id]||'')&&<AnswerImplementation questionId={q.id} questionText={q.question} answer={sheet.answers[q.id]} targets={sheetImplementationTargets(project,sheet,q)} value={sheet.answer_implementations?.[q.id]||[]} disabled={disabled} onChange={refs=>replace({...sheet,answer_implementations:{...sheet.answer_implementations,[q.id]:refs}})}/>}</>,q.blocking!==false,<><p>{q.blocking===false?'可选，暂不确定可留空。':'这项决定需要明确回答。请同时核对上方关联需求。'}</p>{!q.options?.length&&<button type="button" disabled={disabled} onClick={()=>void saveDraft().then(()=>navigate(()=>props.onSuggest('请根据已有资料为这个问题提供推荐回答及依据，不要替用户作最终决定：'+q.question+'。当前原话：'+(sheet.answers[q.id]||'尚未填写'))))}>请 AI 提供建议</button>}<p>关联：{(q.related_refs||[]).map((id:string)=>project.items.find(i=>i.id===id)?.title||id).join('、')||'项目范围'}</p>{clearAnswer(sheet.answers[q.id]||'')&&linkedClauseItems(project,[q]).map(item=><p className="sheet-group-reference" key={item.id}>这条回答还关联「{item.title}」。请核对下方原规则；回答不会自动改写规则。<button type="button" onClick={()=>locate({kind:'clause',item_id:item.id,message:''})}>核对关联规则：{item.title}</button></p>)}{!!q.options?.length&&<span className="sheet-origin">选项为 AI 推荐，点击填入后仍可修改。</span>}</>))}
      {!questions.length&&<tr><td colSpan={3}>没有需要额外回答的问题。</td></tr>}
      {!!clauseItems.length&&<tr className="sheet-group" data-section="clauses" tabIndex={-1}><th colSpan={3}>4. 关联业务规则 <small>你填写的回答还关联以下规则。请核对原文是否与回答一致；这些规则将随整表一起确认，推荐回答不会自动改写规则。</small></th></tr>}
      {clauseItems.map(item=>{const row=clauseRows.find(clause=>clause.item_id===item.id)!;return renderRow('关联业务规则',item.title,'clause-'+item.id,
        <LinkedClauseEditor item={item} row={row} disabled={disabled} onChange={patch=>updateClause(item.id,patch)}/>,true,
        <><span className="sheet-origin">{item.selection_status==='candidate'?'待采纳规则 · 随本次确认采纳':'已保存规则 · 随本次重新核对'}</span><p>请确认条款与已填写回答一致；不同业务范围的规则分别保留。</p><button type="button" onClick={()=>props.onSources(item.source_refs||[])}>查看规则来源：{item.title}</button>{questions.filter(q=>inScopeQuestion(project,q,sheet.scope)&&clearAnswer(sheet.answers[q.id]||'')&&(q.related_refs||[]).includes(item.id)).map(q=><p key={q.id}>关联回答：{q.question}<br/>{sheet.answers[q.id]}<button type="button" onClick={()=>locate({kind:'question',question_id:q.id,message:''})}>返回这条回答</button></p>)}</>);})}
    </tbody></table></div>
    <footer className="refinement-sheet-footer"><div><strong>{working?'正在检查并确认…':actionCopy.status}</strong><small>{actionCopy.detail}</small></div><button type="button" disabled={disabled} onClick={()=>void saveDraft()}>保存草稿</button><button type="button" className="primary" disabled={disabled||!!loadError} onClick={()=>void confirm()}>{working?'正在确认…':actionCopy.label}</button></footer>
    {archived&&<button type="button" className="sheet-restore" disabled={disabled} onClick={()=>{try{const old=JSON.parse(sessionStorage.getItem(key+'-previous')||'null');if(old){replace(old);setStale(old.revision!==project.revision);setNotice('已恢复上次草稿。');}}catch{}}}>恢复上次表格草稿</button>}
  </section>;
}
