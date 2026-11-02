import {session} from '../draft-storage';
import {useDraftWriter} from '../use-draft-writer';
import React, { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { api, Project, Blocker, BlockerAction, type ActionInput } from '../api';
import {beginActivity} from '../activity';
import { useDirty, useNavigation } from '../editing';
import { scopeLabels, isScopeFieldRequired, ScopeProfile, FieldState, hasGenerationRun } from '../scope-draft';
import { requirementBehaviorLabels as fields } from './requirement-fields';
import './refinement-sheet.css';
import type {PreparationStatus} from '../refinement-preparation';
import {canReturnToDelivery,type RefinementReturnState} from './refinement-return';
import {Row,Sheet,LinkedClause,equal,requirements,uncertain,inScopeQuestion,sheetQuestions,initial,mergeSuggestions,requirementPayload,restoreSheet,resolveExpiredAcceptance,clearAnswer,linkedClauseItems,answeredLinkedClauses,linkedClausePayload,retainSheetImplementations,sheetAnswerImplementations,sheetImplementationTargets,pendingSheetAnswers} from './refinement-sheet-state';
import {AnswerImplementation} from './AnswerImplementation';
import {nextPendingAnswer, refinementActionCopy} from './refinement-navigation';
import {DetailContractEditor} from './DetailContractEditor';
import {detailEditingIssues,detailUnknownScopeProperties} from './detail-contract';
import {DraftPackageStatus,DraftRowReview,latestDraftReview} from './BusinessDraftReview';
import {duplicateGroupCandidates,questionGroups} from './refine-context';

type Obj = Record<string, any>;
type Props = {
  project:Project; busy:boolean; active:boolean; mutate:(path:string,body:Obj)=>Promise<any>;
  onSuggest:(instruction:string,request?:ActionInput['draft_request'])=>void; onRefresh:()=>void; onSources:(refs?:Obj[])=>void; onRecommend:(id:string,instruction:string,requestDetails?:boolean)=>void;
  onCreate:()=>void; onResolve:(action:BlockerAction,row:Blocker)=>void;
  onQuestionReview:(id:string)=>void; onStageConfirmed:()=>void;
  onReturnAvailabilityChange?:(state:RefinementReturnState)=>void;
  validationRequest?:number;
  focus?:{itemId?:string;questionId?:string;field?:string;scope?:boolean;nonce:number};
};
type Issue = {kind:string;field?:string;path?:string;item_id?:string;question_id?:string;message:string;client?:boolean};
type QuestionEntry={key:string;kind:'single'|'duplicate'|'related';representative:Obj;questions:Obj[];children?:QuestionEntry[]};
const changeLabels={unspecified:'请选择本次改动',new:'新增功能',modified:'修改已有功能',preserved:'保持已有功能'};
const fieldHelp:Record<string,string>={actor:'例如操作人员或系统角色。资料已说明时，直接核对即可。',entry:'说明从哪里进入，以及操作前需要什么状态；不相关可留空。',flow:'按实际操作顺序说明关键步骤；不用写代码实现。',data:'说明要查看、填写或处理的数据。未涉及可留空。',permissions:'说明谁可以查看或操作；不要凭行业惯例假定权限。',result:'说明操作完成后，用户会看到什么或数据如何变化。',exceptions:'说明已知的失败、空数据或边界情况；没有依据可留空。'};
function allowedStates(key:string,profile:ScopeProfile){
  const result=['known','unknown'];
  if(['value','priority','out_of_scope'].includes(key))result.push('not_applicable','none');
  if(['current_state','preserve_scope'].includes(key)&&!isScopeFieldRequired(key,profile))result.push('not_applicable');
  if(key==='preserve_scope')result.push('none');return result;
}
export function detailDraftRequested(item:Obj,row:Row){return !!(row.detail_contract||item.detail_required||item.detail_contract);}
export function withDetailDraftIntent(project:Project,sheet:Sheet,request:NonNullable<ActionInput['draft_request']>){
  if(request.mode!=='all'&&request.mode!=='required')return request;
  const detailRequiredIds=requirements(project).filter(item=>{
    const row=sheet.rows[item.id];
    return sheet.scope.includes(item.id)&&!!row&&detailDraftRequested(item,row);
  }).map(item=>item.id);
  return detailRequiredIds.length?{...request,detail_required_ids:detailRequiredIds}:request;
}
function editableRequirementContent(row:Row|undefined){
  return row&&{requirement_id:row.requirement_id,statement:row.statement,change_type:row.change_type,behavior:row.behavior,
    acceptance_items:row.acceptance_items.map(({id,statement})=>({id,statement})),detail_contract:row.detail_contract};
}
function latestRequirementRevision(project:Project,item:Obj){
  return project.items.filter(candidate=>candidate.kind==='requirement'&&candidate.action==='revise'&&
    candidate.selection_status==='candidate'&&candidate.applies_to==='to_be'&&candidate.target_item_id===item.id&&
    !candidate.superseded_by&&(!candidate.recommendation_requirement_id||candidate.recommendation_requirement_id===item.id)
  ).at(-1) as Obj|undefined;
}
function latestDetailCandidate(project:Project,item:Obj){
  const candidate=project.items.filter(candidate=>candidate.target_item_id===item.id&&candidate.kind==='requirement'&&
    candidate.action==='revise'&&candidate.selection_status==='candidate'&&candidate.applies_to==='to_be'&&
    !candidate.superseded_by&&(!candidate.recommendation_requirement_id||candidate.recommendation_requirement_id===item.id)&&candidate.detail_contract).at(-1);
  return candidate&&typeof candidate.id==='string'&&candidate.id&&candidate.detail_contract?
    {contract:candidate.detail_contract,candidate_id:candidate.id}:undefined;
}
export function aiInstructions(purpose:string,sheet:Sheet,base:Sheet|null){
  type BlockKind='scope_profile'|'answers'|'summary'|'candidate'|'requirements'|'linked_clauses';
  type Block={kind:BlockKind;id:string;value?:any;reference?:string};
  const messageBudget=11500;
  const identity=(value:string)=>JSON.stringify(value);
  const blocks:Block[]=[];
  if(sheet.profile&&(!base||!equal(sheet.profile,base.profile)))blocks.push({kind:'scope_profile',id:'scope_profile',value:sheet.profile});
  for(const [id,value] of Object.entries(sheet.answers))if(!base||!equal(value,base.answers[id]))blocks.push({kind:'answers',id,value});
  for(const [id,value] of Object.entries(sheet.values))if(!base||!equal(value,base.values[id]))blocks.push({kind:'summary',id,value});
  const selectedCandidate=(value:unknown)=>typeof value==='string'&&value?value:undefined;
  const addCandidateReference=(kind:'需求'|'验收',entityId:string,currentValue:unknown,previousValue:unknown)=>{
    const current=selectedCandidate(currentValue),previous=selectedCandidate(previousValue);
    if(current===previous)return;
    blocks.push({kind:'candidate',id:kind+' '+identity(entityId)+'→'+(current?identity(current):'取消'),
      reference:kind+' '+identity(entityId)+'：'+(current?'版本/选择引用 '+identity(current):'明确取消版本/选择引用')});
  };
  for(const row of Object.values(sheet.rows)){
    const baseRow=base?.rows[row.requirement_id];
    addCandidateReference('需求',row.requirement_id,row.candidate_id,baseRow?.candidate_id);
    for(const [index,acceptance] of row.acceptance_items.entries()){
      const previous=acceptance.id?baseRow?.acceptance_items.find(item=>item.id===acceptance.id):
        baseRow?.acceptance_items[index]?.id?undefined:baseRow?.acceptance_items[index];
      addCandidateReference('验收',acceptance.id||row.requirement_id+' 当前本地验收位置 '+(index+1),
        acceptance.candidate_id,previous?.candidate_id);
    }
  }
  for(const row of Object.values(sheet.rows))if(!base||!equal(editableRequirementContent(row),editableRequirementContent(base.rows[row.requirement_id])))
    blocks.push({kind:'requirements',id:row.requirement_id,value:requirementPayload(row)});
  for(const row of Object.values(sheet.linked_clauses||{}))if(!base||!equal(row,base.linked_clauses?.[row.item_id]))
    blocks.push({kind:'linked_clauses',id:row.item_id,value:row});
  if(!blocks.length)return purpose;
  const omissionNotice=(omitted:Block[])=>{
    const labels:Record<BlockKind,string>={scope_profile:'范围场景',answers:'人工答案',summary:'范围字段',candidate:'版本/选择引用',requirements:'需求',linked_clauses:'关联条款'};
    const groups:Partial<Record<BlockKind,string[]>>={};
    for(const block of omitted)(groups[block.kind]||(groups[block.kind]=[])).push(block.id);
    const parts=(Object.entries(groups) as [BlockKind,string[]][]).map(([kind,ids])=>{
      if(kind==='scope_profile')return labels[kind];
      let shown='',count=0;
      for(const id of ids){
        const next=shown+(shown?'、':'')+id;
        if((labels[kind]+'：'+next+'等共 '+ids.length+' 项').length>180)break;
        shown=next;count++;
      }
      if(count<ids.length)return labels[kind]+(shown?'：'+shown+'等':'：')+'共 '+ids.length+' 项';
      return labels[kind]+'：'+shown;
    });
    return ' 未附带：'+parts.join('；')+'；完整草稿仍保留在浏览器，未附带内容未被模型读取。';
  };
  const render=(selected:Set<number>)=>{
    const chosen=blocks.filter((_,index)=>selected.has(index));
    const omitted=blocks.filter((_,index)=>!selected.has(index));
    let text=purpose;
    const references=chosen.filter(block=>block.kind==='candidate');
    if(references.length)text+=' 本表未提交的版本/选择引用（仅用于定位候选版本，不代表新增人工业务事实）：'+references.map(block=>block.reference).join('；');
    if(omitted.length)text+=omissionNotice(omitted);
    const business=chosen.filter(block=>block.kind!=='candidate');
    if(business.length){
      const edits:Obj={summary:{},requirements:[],answers:{},linked_clauses:[],scope_profile:undefined};
      for(const block of business){
        if(block.kind==='scope_profile')edits.scope_profile=block.value;
        else if(block.kind==='answers')edits.answers[block.id]=block.value;
        else if(block.kind==='summary')edits.summary[block.id]=block.value;
        else if(block.kind==='requirements')edits.requirements.push(block.value);
        else if(block.kind==='linked_clauses')edits.linked_clauses.push(block.value);
      }
      text+=' 本表未提交的人工修改（仅供起草参考，仍待确认）：'+JSON.stringify(edits);
    }
    return text;
  };
  let included=new Set<number>();
  for(let index=0;index<blocks.length;index++){
    const attempt=new Set(included);attempt.add(index);
    if(render(attempt).length<=messageBudget)included=attempt;
  }
  const result=render(included);
  if(result.length<=messageBudget)return result;
  const fallback=' 本表修改未附带，完整草稿仍保留在浏览器，未被模型读取。';
  return purpose.length+fallback.length<=messageBudget?purpose+fallback:purpose;
}
function issueKey(issue:Issue){return issue.kind==='context'?'context-'+issue.field:issue.kind==='question'?'question-'+issue.question_id:issue.kind==='clause'?'clause-'+issue.item_id:issue.kind==='requirement'?'requirement-'+issue.item_id+'-'+(issue.path||issue.field||'statement'):issue.kind;}
function currentDetailIssues(project:Project,requirementId:string,contract:Row['detail_contract']){
  return detailEditingIssues(contract,detailUnknownScopeProperties(project,requirementId,contract));
}
function scopeFieldRequired(key:string,sheet:Sheet,project:Project){
  const draftProject={...project,items:project.items.map(item=>({...item,change_type:sheet.rows[item.id]?.change_type??item.change_type}))};
  return isScopeFieldRequired(key,sheet.profile,sheet.scope,draftProject);
}
export function requiredIssues(sheet:Sheet,questions:Obj[],project:Project):Issue[]{
  const result:Issue[]=[];
  for(const key of Object.keys(scopeLabels)){
    const state=sheet.states[key]?.status||'unknown',value=String(sheet.values[key]||'').trim();
    if(!allowedStates(key,sheet.profile).includes(state)||state==='known'&&!value||scopeFieldRequired(key,sheet,project)&&(state==='unknown'||state==='known'&&uncertain(value)))
      result.push({kind:'context',field:key,message:'请明确'+scopeLabels[key]+'；确实不适用时请选择允许的适用状态。'});
  }
  if(!sheet.scope.length)result.push({kind:'scope',message:'请至少将一条需求列为“本期包含”。'});
  for(const id of sheet.scope){const row=sheet.rows[id];if(!row)continue;
    if(!row.statement.trim())result.push({kind:'requirement',item_id:id,field:'statement',message:'请填写需求内容。'});
    if(!['new','modified','preserved'].includes(row.change_type))result.push({kind:'requirement',item_id:id,field:'change_type',message:'请选择新增、修改或保持，以本次实际需求为准。'});
    if(!row.acceptance_items.some(a=>a.statement.trim()))result.push({kind:'requirement',item_id:id,field:'acceptance',message:'请填写至少一条可验证的验收条件，也可以让 AI 先起草。'});
    if(row.acceptance_items.some(a=>a.invalid_candidate))result.push({kind:'requirement',item_id:id,field:'acceptance',message:'已使用的 AI 验收建议发生变化或不再可用。请在此格采用已保存内容、明确保留为人工修改，或使用新的建议。'});
    if(project.items.find(item=>item.id===id)?.detail_required&&!row.detail_contract)result.push({kind:'requirement',item_id:id,path:'detail_contract',message:'本需求需要详细定义，请先使用 AI 草稿或启用字段、交互与流程表。'});
    result.push(...currentDetailIssues(project,id,row.detail_contract).map(issue=>({...issue,kind:'requirement',item_id:id})));
    result.push(...(row.detail_conflicts||[]).map(conflict=>({kind:'requirement',item_id:id,path:conflict.path,message:'详细定义有版本冲突，请比较你的修改和最新内容后选择。'})));
  }
  for(const q of questions)if(q.blocking!==false&&(!q.out_of_scope_reason||q.scope_exclusion)&&(!clearAnswer(sheet.answers[q.id]||'')||uncertain(sheet.answers[q.id]||'')))
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
  const questionState=questionGroups(project),currentQuestionIds=new Set(questionState.current.map(q=>q.id));
  const writer=useDraftWriter(key,()=>setError('浏览器未能保存草稿，输入仍保留在本页。可以继续编辑并确认保存到项目；离开前请保留当前页面。'));
  const [sheet,setSheet]=useState<Sheet|null>(null),[states,setStates]=useState<Obj[]>([]),[loaded,setLoaded]=useState(-1);
  const [preparationInfo,setPreparationInfo]=useState<PreparationStatus|null>(null);
  const [working,setWorking]=useState(false),[loadError,setLoadError]=useState(''),[error,setError]=useState(''),[notice,setNotice]=useState(''),[reportedIssues,setIssues]=useState<Issue[]>([]),[dirty,setDirty]=useState(false);
  const [stale,setStale]=useState(false),[reload,setReload]=useState(0),[archived,setArchived]=useState(()=>!!session.getItem(key+'-previous'));
  const [focusedAnswer,setFocusedAnswer]=useState(''),[expandedBehavior,setExpandedBehavior]=useState<Record<string,boolean>>({}),[expandedDetail,setExpandedDetail]=useState<Record<string,boolean>>({}),[expandedRelatedGroups,setExpandedRelatedGroups]=useState<Record<string,boolean>>({});
  const [focusMode,setFocusMode]=useState(false);
  const validationSeen=useRef<number|undefined>(undefined);
  const pendingLocate=useRef<Issue|null>(null);
  const lock=useRef(false),root=useRef<HTMLElement>(null),current=useRef<Sheet|null>(null),baseline=useRef<Sheet|null>(null),intent=useRef({signature:'',key:''}),navigate=useNavigation();
  const aiAnchor=useRef<{key:string;offset:number;top:number;outer:number;run?:string}|null>(null);
  const validationQuestions=(scope:string[])=>sheetQuestions(project).filter(q=>currentQuestionIds.has(q.id)&&inScopeQuestion(project,q,scope));
  const currentPendingAnswers=()=>sheet?pendingSheetAnswers(project,sheet).filter(q=>currentQuestionIds.has(q.id)):[];
  function requestAI(request:NonNullable<ActionInput['draft_request']>,purpose:string){
    if(!sheet||props.busy||working)return;
    const draftRequest=withDetailDraftIntent(project,sheet,request);
    const scroll=root.current?.querySelector<HTMLElement>('.refinement-sheet-scroll');
    const target=request.question_id?root.current?.querySelector<HTMLElement>('[data-cell-key="question-'+CSS.escape(request.question_id)+'"]'):
      Array.from(scroll?.querySelectorAll<HTMLElement>('[data-cell-key]')||[]).find(row=>row.getBoundingClientRect().bottom>(scroll?.getBoundingClientRect().top||0)+45);
    aiAnchor.current={key:target?.dataset.cellKey||'',offset:(target?.getBoundingClientRect().top||0)-(scroll?.getBoundingClientRect().top||0),top:scroll?.scrollTop||0,
      outer:root.current?.closest<HTMLElement>('.connected-workspace')?.scrollTop||0,run:(project as Obj).refinement_completion?.run_id};
    void saveDraft().then(()=>navigate(()=>props.onSuggest(aiInstructions(purpose,sheet,baseline.current),draftRequest))).catch(e=>setError((e as Error).message));
  }
  useLayoutEffect(()=>{
    const anchor=aiAnchor.current,completion=(project as Obj).refinement_completion;
    if(!anchor||!completion?.run_id||completion.run_id===anchor.run||!sheet||loaded!==project.revision||props.busy||working)return;
    const scroll=root.current?.querySelector<HTMLElement>('.refinement-sheet-scroll'),target=anchor.key?root.current?.querySelector<HTMLElement>('[data-cell-key="'+CSS.escape(anchor.key)+'"]'):null;
    if(scroll){scroll.scrollTop=target?scroll.scrollTop+target.getBoundingClientRect().top-scroll.getBoundingClientRect().top-anchor.offset:anchor.top;}
    const outer=root.current?.closest<HTMLElement>('.connected-workspace');if(outer)outer.scrollTop=anchor.outer;
    aiAnchor.current=null;
    const left=requiredIssues(sheet,validationQuestions(sheet.scope),project).length;
    setNotice('本次整理结果仍需核对；已有草稿和手工修改已保留'+(left?'，仍有 '+left+' 项待补充':'')+'。请查看本次结果与未载入原因。');
  },[sheet,loaded,project.revision,props.busy,working]);
  current.current=sheet;
  useEffect(()=>{let live=true;void api<PreparationStatus>('/projects/'+project.id+'/refinement-preparation').then(value=>{if(live)setPreparationInfo(value);}).catch(()=>{if(live)setPreparationInfo(null);});return()=>{live=false;};},[project.id,project.revision,reload]);
  useEffect(()=>{let live=true;setLoadError('');
    void api<Obj>('/projects/'+project.id+'/requirement-confirmation/state').then(response=>{
      if(!live)return;if(response.revision!==project.revision){setLoadError('项目正在更新，请刷新后读取最新内容。');return;}
      setStates(response.rows);setLoaded(response.revision);
      const fresh=initial(project,response.rows),previous=current.current;
      let saved:Sheet|null=previous,base=baseline.current;
      if(!saved){try{saved=JSON.parse(session.getItem(key)||'null');base=JSON.parse(session.getItem(key+'-base')||'null');}catch{}}
      if(previous?.revision===project.revision)return;
      if(saved?.rows&&saved?.values){
        const next=restoreSheet(saved,base,fresh);
        if(saved.revision!==project.revision){session.setItem(key+'-previous',JSON.stringify(saved));setArchived(true);}
        baseline.current=fresh;setSheet(next);setStale(!!next.merge_conflicts?.length);
        session.setItem(key,JSON.stringify(next));session.setItem(key+'-base',JSON.stringify(fresh));
        setNotice(next.merge_conflicts?.length?'有合并冲突，你的修改已保留；请核对下方提示、本次结果与未载入原因。':saved.revision===project.revision?'已恢复当前浏览器中未提交的表格；尚未正式确认。':'项目已更新，已有草稿和手工修改已保留；请核对本次结果与未载入原因。');
      }else{baseline.current=fresh;setSheet(fresh);}
    }).catch(e=>{if(live)setLoadError(e.message);});return()=>{live=false;};
  },[project.id,project.revision,reload]);
  function replace(value:Sheet){const next=retainSheetImplementations(value,current.current);current.current=next;setSheet(next);setDirty(true);setError('');writer.schedule(()=>({[key]:current.current,[key+'-base']:baseline.current}));}
  async function saveDraft(){writer.schedule(()=>({[key]:current.current,[key+'-base']:baseline.current}));if(!writer.flush())throw Error('浏览器未能保存草稿，输入仍在本页。请先确认保存到项目，或保留当前页面。');setDirty(false);markSaved();setNotice('表格草稿已保存在当前浏览器，尚未确认。');}
  function discard(){writer.cancel();session.removeItem(key);setSheet(baseline.current);setDirty(false);setStale(false);setIssues([]);setError('');}
  const markSaved=useDirty(key,dirty,'需求确认表',saveDraft,discard);
  const canReturn=canReturnToDelivery(project,sheet,states,loaded,stale)&&!loadError&&!!sheet&&!currentPendingAnswers().length;
  const pendingCount=currentPendingAnswers().length;
  const currentMissing=sheet?requiredIssues(sheet,validationQuestions(sheet.scope),project):[];
  const missingCount=currentMissing.length;
  // Show gaps before submission and clear them as the current draft is completed.
  // Server-only validation issues still require an explicit recheck.
  const issues=[...currentMissing,...reportedIssues.filter(issue=>!issue.client)].filter((issue,index,rows)=>rows.findIndex(row=>issueKey(row)===issueKey(issue)&&row.message===issue.message)===index);
  const actionCopy=refinementActionCopy(canReturn,pendingCount,missingCount);
  useLayoutEffect(()=>{props.onReturnAvailabilityChange?.({projectId:project.id,revision:project.revision,canReturn,pendingAnswerCount:pendingCount,missingCount});return()=>props.onReturnAvailabilityChange?.({projectId:project.id,revision:project.revision,canReturn:false});},[project.id,project.revision,canReturn,pendingCount,missingCount,props.onReturnAvailabilityChange]);
  function locate(issue:Issue){
    if(issue.kind==='question'&&issue.question_id){
      const requestedId=issue.question_id,relatedId=relatedGroupByQuestion.get(requestedId);
      if(relatedId)setExpandedRelatedGroups(value=>({...value,[relatedId]:true}));
      const targetId=questionRowIds.get(requestedId)||requestedId;
      issue={...issue,question_id:targetId};
      setFocusedAnswer(targetId);
    }
    pendingLocate.current=issue;
    if(issue.kind==='requirement'&&issue.item_id&&issue.field&&Object.keys(fields).includes(issue.field))setExpandedBehavior(value=>({...value,[issue.item_id!]:true}));
    if(issue.kind==='requirement'&&issue.item_id&&(issue.path||issue.field||'').startsWith('detail_contract'))setExpandedDetail(value=>({...value,[issue.item_id!]:true}));
    requestAnimationFrame(()=>{if(pendingLocate.current!==issue)return;const row=root.current?.querySelector<HTMLElement>('[data-cell-key="'+CSS.escape(issueKey(issue))+'"]')||root.current?.querySelector<HTMLElement>('[data-requirement-id="'+CSS.escape(issue.item_id||'')+'"]');let parent=row;while(parent&&parent!==root.current){if(parent instanceof HTMLDetailsElement)parent.open=true;parent=parent.parentElement;}row?.querySelectorAll<HTMLDetailsElement>('.sheet-question-history,.sheet-question-records').forEach(details=>{details.open=true;});const review=issue.field==='implementation'?row?.querySelector<HTMLElement>('.answer-implementation input[type="checkbox"]:not(:checked),.answer-implementation select'):null;const input=review||row?.querySelector<HTMLElement>('textarea,select,input')||row?.querySelector<HTMLElement>('button')||(issue.kind==='scope'?root.current?.querySelector<HTMLElement>('.sheet-requirement select'):null);if(input?.matches(':disabled'))return;const target=input||row;target?.scrollIntoView({block:'center',behavior:'instant'});
      // Centering alone ignores the sticky table heading and can clip tall editors.
      const scroll=target?.closest<HTMLElement>('.refinement-sheet-scroll');
      if(target&&scroll){const rect=target.getBoundingClientRect(),bounds=scroll.getBoundingClientRect(),heading=scroll.querySelector('thead')?.getBoundingClientRect().height||0,top=bounds.top+heading+8,bottom=bounds.bottom-8;const delta=rect.top<top?rect.top-top:rect.bottom>bottom?rect.bottom-bottom:0;if(delta)scroll.scrollTop+=delta;}
      input?.focus({preventScroll:true});pendingLocate.current=null;});
  }
  useLayoutEffect(()=>{if(sheet&&!props.busy&&!working&&loaded===project.revision&&pendingLocate.current)locate(pendingLocate.current);},[sheet,props.busy,working,loaded,project.revision]);
  function locateSection(key:string){const row=root.current?.querySelector<HTMLElement>('[data-section="'+key+'"]');row?.scrollIntoView({block:'start',behavior:'instant'});row?.focus({preventScroll:true});}
  function locatePending(direction:1|-1=1){const id=sheet&&nextPendingAnswer(currentPendingAnswers().map(q=>q.id),focusedAnswer,direction,sheetQuestions(project).map(q=>q.id));if(id)locate({kind:'question',question_id:id,message:''});}
  useEffect(()=>{if(!sheet||!props.focus)return;const focus=props.focus;locate(focus.questionId?{kind:'question',question_id:focus.questionId,field:focus.field,message:''}:focus.field?{kind:'context',field:focus.field,message:''}:focus.itemId?{kind:linkedClauseItems(project).some(i=>i.id===focus.itemId)?'clause':'requirement',item_id:focus.itemId,message:''}:{kind:'scope',message:''});},[props.focus?.nonce,!!sheet]);
  useEffect(()=>{
    if(!props.active||!sheet||loaded!==project.revision||props.validationRequest===undefined||validationSeen.current===props.validationRequest)return;
    validationSeen.current=props.validationRequest;
    const missing=requiredIssues(sheet,validationQuestions(sheet.scope),project);
    if(missing.length){setIssues(missing.map(issue=>({...issue,client:true})));setError('还有 '+missing.length+' 项必填信息未完成，已定位第一处。');locate(missing[0]);return;}
    const pending=currentPendingAnswers();
    if(pending.length){setNotice('已定位尚未落实到正文的回答，请核对原文位置后继续。');locate({kind:'question',question_id:pending[0].id,field:'implementation',message:''});return;}
    requestAnimationFrame(()=>{const button=root.current?.querySelector<HTMLButtonElement>('.refinement-sheet-footer .primary');button?.scrollIntoView({block:'center'});button?.focus({preventScroll:true});});
  },[props.validationRequest,props.active,!!sheet,loaded,project.revision]);
  function loadLatest(){if(sheet){session.setItem(key+'-previous',JSON.stringify(sheet));setArchived(true);}const fresh=initial(project,states),next=sheet?mergeSuggestions(sheet,baseline.current,fresh):fresh;baseline.current=fresh;next.merge_conflicts=[];setSheet(next);setStale(false);setDirty(false);markSaved();session.removeItem(key);setIssues([]);setError('');session.setItem(key,JSON.stringify(next));session.setItem(key+'-base',JSON.stringify(fresh));setNotice('已更新建议，保留你修改过的单元格；请核对后确认。原表格也保留为副本。');}
  async function confirm(){
    if(lock.current||!sheet||props.busy||loaded!==project.revision)return;
    if(canReturn){props.onStageConfirmed();return;}
    const missing=requiredIssues(sheet,validationQuestions(sheet.scope),project);setIssues(missing.map(issue=>({...issue,client:true})));setError('');
    if(missing.length){setError('还有 '+missing.length+' 项必填信息未完成，请处理表格中标红的内容。');locate(missing[0]);return;}
    if(stale){setError('项目版本已变化，请先载入最新内容并核对；当前草稿仍保留。');return;}
    const payload={expected_revision:sheet.revision,review_confirmed:true,
      context:{values:sheet.values,field_states:sheet.states,scope_profile:sheet.profile,scope_ids:sheet.scope},
      rows:sheet.scope.map(id=>({...requirementPayload(sheet.rows[id]),
        confirmed_question_ids:sheetQuestions(project).filter(q=>inScopeQuestion(project,q,sheet.scope)&&(q.related_refs||[]).includes(id)&&!!sheet.answers[q.id]?.trim()).map(q=>q.id)})),
      answers:sheetQuestions(project).filter(q=>inScopeQuestion(project,q,sheet.scope)).map(q=>({question_id:q.id,answer:sheet.answers[q.id]||''})),linked_clauses:linkedClausePayload(project,sheet),
      answer_implementations:sheetAnswerImplementations(project,sheet)};
    const signature=JSON.stringify(payload);if(intent.current.signature!==signature)intent.current={signature,key:crypto.randomUUID()};
    const idempotencyKey=intent.current.key;const projectId=project.id;
    lock.current=true;setWorking(true);
    try{
      let result:Obj;
      let recoveredAccepted:Sheet|undefined,recoveredPending:string[]|undefined;
      try{result=await props.mutate('/refinement-confirmation',{...payload,idempotency_key:idempotencyKey});
      }catch(e){
        const code=(e as Obj).code;
        if(code!=='REQUEST_TIMEOUT'&&code!=='NETWORK')throw e;
        const originalError=(e as Error).message;
        const finishReceiptCheck=beginActivity('正在核对本次确认结果');
        let recovered:Obj;
        try{recovered=await api<Obj>(`/projects/${encodeURIComponent(projectId)}/refinement-confirmation/receipts/${encodeURIComponent(idempotencyKey)}`);
        }catch{
          finishReceiptCheck(originalError,true);setError(originalError);return;
        }
        const receipt=recovered?.receipt;const written=recovered?.project;
        if(recovered?.confirmed!==true||!receipt||receipt.idempotency_key!==idempotencyKey||!written||written.id!==projectId||
          typeof written.revision!=='number'||!Number.isFinite(written.revision)||typeof receipt.result_revision!=='number'||!Number.isFinite(receipt.result_revision)||written.revision!==receipt.result_revision||
          !Array.isArray(written.items)||written.items.length===0||!sheet.scope.every(id=>written.items.some((item:Obj)=>item?.id===id))||
          (recovered.destination!=='delivery'&&recovered.destination!=='refinement')){
          finishReceiptCheck(originalError,true);setError(originalError);return;
        }
        try{
          const confirmedStates=sheet.scope.map(id=>({requirement_id:id,current:true}));
          const accepted=initial(written as Project,confirmedStates);
          const pending=recovered.implementation_pending_ids||pendingSheetAnswers(written as Project,accepted).map(q=>q.id);
          recoveredAccepted=accepted;recoveredPending=pending;
        }catch{
          finishReceiptCheck(originalError,true);setError(originalError);return;
        }
        result=recovered;finishReceiptCheck();props.onRefresh();
      }
      if(!result.confirmed){setIssues(result.issues||[]);setError(result.message||'请核对表格中提示的内容，本次没有写入。');if(result.issues?.length)locate(result.issues[0]);return;}
      const written=result.project as Project;const confirmedStates=sheet.scope.map(id=>({requirement_id:id,current:true}));const accepted=recoveredAccepted||initial(written,confirmedStates);
      writer.cancel();session.removeItem(key);sheet.scope.forEach(id=>session.removeItem(project.id+'-table-draft-'+id));
      baseline.current=accepted;setSheet(accepted);setStates(confirmedStates);setLoaded(written.revision);setStale(false);setDirty(false);markSaved();setIssues([]);
      current.current=accepted;
      const pending=recoveredPending||result.implementation_pending_ids||pendingSheetAnswers(written,accepted).map(q=>q.id);
      if(pending.length||result.destination==='refinement'){
        session.setItem(key,JSON.stringify(accepted));session.setItem(key+'-base',JSON.stringify(accepted));
        setNotice('回答和条款已保存，'+(pending.length?pending.length+' 项回答':'仍有回答')+'待落实到正文。可以用“AI 补充建议”一次起草，再核对原文位置；当前表格已保留。');
        if(pending.length)locate({kind:'question',question_id:pending[0],field:'implementation',message:''});
        return;
      }
      props.onStageConfirmed();
    }catch(e){setError((e as Error).message);if((e as Obj).code==='STALE_REVISION'){setStale(true);props.onRefresh();}}
    finally{lock.current=false;setWorking(false);}
  }
  if(!sheet)return <section className="refinement-sheet" aria-label="表格完善需求"><h2>完善需求</h2><p role="status">正在读取已保存内容与确认状态…</p>{loadError&&<p role="alert">{loadError}<button type="button" onClick={()=>setReload(x=>x+1)}>重新读取</button></p>}</section>;
  const currentSheet=sheet;
  const disabled=props.busy||working||loaded!==project.revision;
  const reqs=requirements(project),allQuestions=sheetQuestions(project);
  const pendingAnswers=currentPendingAnswers(),pendingAnswerIds=new Set(pendingAnswers.map(q=>q.id));
  const openQuestionIds=new Set(questionState.open.map(q=>q.id));
  const questions=allQuestions.filter(q=>currentQuestionIds.has(q.id)&&(openQuestionIds.has(q.id)||pendingAnswerIds.has(q.id)||!clearAnswer(currentSheet.answers[q.id]||'')));
  const settledQuestions=allQuestions.filter(q=>currentQuestionIds.has(q.id)&&!questions.includes(q));
  const historyQuestions=questionState.history;
  const rejectedEmptyDrafts:Obj[]=reqs.length?[]:(latestDraftReview(project)?.rejected||[]);
  const rejectedDraftDiagnostics=rejectedEmptyDrafts.map(row=>(typeof row.raw?.title==='string'&&row.raw.title.trim()?row.raw.title.trim():'未能识别的需求条目')+'：'+String(row.reason||'未通过需求草稿校验')).join('；').slice(0,1800);
  function renderRow(category:string,label:string,cellKey:string,editor:React.ReactNode,required=false,hint:React.ReactNode=null){
    const errors=issues.filter(i=>issueKey(i)===cellKey);
    return <tr key={cellKey} data-cell-key={cellKey} aria-invalid={errors.length?true:undefined} className={errors.length?'sheet-invalid':undefined}><th scope="row"><small>{category}</small><strong>{label}</strong>{errors.length?<span className="sheet-missing-label">待补充</span>:required&&<span className="sheet-required">必填</span>}</th><td>{editor}</td><td>{errors.map((e,n)=><p role={reportedIssues.some(reported=>issueKey(reported)===cellKey&&reported.message===e.message)?'alert':undefined} className="sheet-error" key={n}>{e.message}</p>)}{hint}</td></tr>;
  }
  const updateRow=(id:string,patch:Partial<Row>)=>replace({...sheet,rows:{...sheet.rows,[id]:{...sheet.rows[id],...patch}}});
  const pendingScopeQuestion=(q:Obj)=>reqs.some(item=>!sheet.scope.includes(item.id)&&!sheet.scope_excluded_ids?.includes(item.id)&&inScopeQuestion(project,q,[item.id]));
  const applyRevision=(item:Obj,revision:Obj)=>{
    const row=sheet.rows[item.id];
    const revised:Row={...row,
      statement:revision.statement||'',
      change_type:revision.change_type&&revision.change_type!=='unspecified'?revision.change_type:row.change_type,
      behavior:Object.fromEntries(Object.keys(fields).map(field=>[field,revision.behavior?.[field]??''])),
      detail_contract:revision.detail_contract?structuredClone(revision.detail_contract):row.detail_contract,
      detail_suggestion:undefined,
      candidate_id:revision.id};
    // Treat the chosen revision as the remote side of the existing three-way merge.
    // All other rows, acceptance items, answers and field states remain the current draft.
    const merged=mergeSuggestions(sheet,baseline.current,{...sheet,rows:{...sheet.rows,[item.id]:revised}});
    const next={...merged,rows:{...merged.rows,[item.id]:{...merged.rows[item.id],candidate_id:revision.id}}};
    replace(next);
    setNotice(next.merge_conflicts?.length?
      'AI 修订已并入当前浏览器草稿；人工改动和已有冲突均已保留，请核对提示后再确认。':
      'AI 修订已带入当前浏览器草稿，尚未确认；现有验收条件和其他需求保持不变。');
  };
  const missing=requiredIssues(sheet,questions.filter(q=>inScopeQuestion(project,q,sheet.scope)),project);
  const clauseItems=answeredLinkedClauses(project,sheet),clauseRows=linkedClausePayload(project,sheet);
  const updateClause=(id:string,patch:Partial<LinkedClause>)=>replace({...sheet,linked_clauses:{...sheet.linked_clauses,[id]:{...clauseRows.find(row=>row.item_id===id)!,...patch}}});
  const missingAcceptance=reqs.filter(item=>sheet.scope.includes(item.id)&&!sheet.rows[item.id]?.acceptance_items.some(ac=>ac.statement.trim()));
  const duplicateByQuestion=new Map<string,QuestionEntry>();
  for(const group of duplicateGroupCandidates(project)){
    const members=group.question_ids.map(id=>allQuestions.find(q=>q.id===id)).filter((q):q is Obj=>!!q);
    const representative=members.find(q=>q.id===group.representative_id);
    if(!representative||members.length!==group.question_ids.length||members.some(q=>!currentQuestionIds.has(q.id)||!openQuestionIds.has(q.id)))continue;
    if(!members.every(q=>inScopeQuestion(project,q,currentSheet.scope)))continue;
    const targets=sheetImplementationTargets(project,currentSheet,representative),answer=currentSheet.answers[representative.id]||'',implementations=currentSheet.answer_implementations?.[representative.id]||[];
    if(members.some(q=>!equal(sheetImplementationTargets(project,currentSheet,q),targets)||(currentSheet.answers[q.id]||'')!==answer||!equal(currentSheet.answer_implementations?.[q.id]||[],implementations)))continue;
    const entry:QuestionEntry={key:'duplicate-'+representative.id,kind:'duplicate',representative,questions:members};
    members.forEach(q=>duplicateByQuestion.set(q.id,entry));
  }
  const relatedByQuestion=new Map<string,QuestionEntry>(),relatedClaimed=new Set<string>();
  for(const group of project.question_view?.open_groups||[]){
    const ids=new Set(group.question_ids||[]),members=questions.filter(q=>openQuestionIds.has(q.id)&&ids.has(q.id)&&!relatedClaimed.has(q.id));
    if(members.length<2)continue;
    const memberIds=new Set(members.map(q=>q.id)),children:QuestionEntry[]=[],added=new Set<string>();
    let crossesGroup=false;
    for(const q of members){
      const duplicate=duplicateByQuestion.get(q.id);
      if(duplicate){
        if(duplicate.questions.some(member=>!memberIds.has(member.id))){crossesGroup=true;break;}
        if(!added.has(duplicate.key)){children.push(duplicate);added.add(duplicate.key);}
      }else children.push({key:'single-'+q.id,kind:'single',representative:q,questions:[q]});
    }
    if(crossesGroup||children.length<2)continue;
    const representative=members.find(q=>q.id===group.representative_id)||members[0];
    const entry:QuestionEntry={key:'related-'+representative.id,kind:'related',representative,questions:members,children};
    members.forEach(q=>{relatedClaimed.add(q.id);relatedByQuestion.set(q.id,entry);});
  }
  const questionEntries:QuestionEntry[]=[],addedEntries=new Set<string>();
  for(const q of questions){
    const entry=relatedByQuestion.get(q.id)||duplicateByQuestion.get(q.id)||{key:'single-'+q.id,kind:'single' as const,representative:q,questions:[q]};
    if(!addedEntries.has(entry.key)){questionEntries.push(entry);addedEntries.add(entry.key);}
  }
  const questionRowIds=new Map<string,string>(),relatedGroupByQuestion=new Map<string,string>();
  for(const entry of questionEntries){
    const children=entry.kind==='related'?entry.children||[]:[entry];
    for(const child of children)for(const q of child.questions)questionRowIds.set(q.id,child.representative.id);
    if(entry.kind==='related')entry.questions.forEach(q=>relatedGroupByQuestion.set(q.id,entry.key));
  }
  const updateAnswers=(ids:string[],value:string)=>replace({...currentSheet,answers:{...currentSheet.answers,...Object.fromEntries(ids.map(id=>[id,value]))}});
  const updateAnswerImplementations=(ids:string[],refs:NonNullable<Sheet['answer_implementations']>[string])=>replace({...currentSheet,answer_implementations:{...currentSheet.answer_implementations,...Object.fromEntries(ids.map(id=>[id,refs]))}});
  const renderFoldedQuestion=(q:Obj,historical=false)=>{
    const answer=currentSheet.answers[q.id]??q.answer??'';
    return <tr key={(historical?'history-':'settled-')+q.id} data-cell-key={'question-'+q.id}><td colSpan={3}><details className="sheet-question-history">
      <summary><span>{q.question}</span><small>{historical?'历史原题 · 仅查看，不计入本期待办':'本期回答已落实 · 展开可查看或修改'}</small></summary>
      <div className="sheet-history-answer">
        {historical?<p className="preserve-lines"><strong>原回答：</strong>{answer||'原题未保存回答'}</p>:<>
          <label>已保存回答<textarea rows={3} aria-label={q.question+' 我的回答'} disabled={disabled} value={answer} onChange={e=>replace({...currentSheet,answers:{...currentSheet.answers,[q.id]:e.target.value}})}/></label>
          {clearAnswer(answer)&&<AnswerImplementation questionId={q.id} questionText={q.question} answer={answer} targets={sheetImplementationTargets(project,currentSheet,q)} suggestions={q.answer_location_suggestions} value={currentSheet.answer_implementations?.[q.id]||[]} disabled={disabled} onChange={refs=>replace({...currentSheet,answer_implementations:{...currentSheet.answer_implementations,[q.id]:refs}})}/>}
          <p>修改回答后需要重新核对落实位置；整表确认按钮仍只在页面底部。</p>
        </>}
        <p>关联：{(q.related_refs||[]).map((id:string)=>project.items.find(i=>i.id===id)?.title||id).join('、')||'项目范围'}</p>
        {!!q.source_refs?.length&&<button type="button" onClick={()=>props.onSources(q.source_refs)}>查看原题来源</button>}
      </div>
    </details></td></tr>;
  };
  const renderQuestion=(q:Obj,sharedIds:string[]=[q.id])=>!inScopeQuestion(project,q,currentSheet.scope)?renderRow(pendingScopeQuestion(q)?'范围待核对':'本期以外',q.question,'question-'+q.id,<p>{currentSheet.answers[q.id]||'尚未回答'}</p>,false,pendingScopeQuestion(q)?'关联需求范围尚未核对，本次保留原话，不确认回答。':'关联需求已暂不纳入本期，本次保留原话，不确认回答。'):renderRow('业务决定',q.question,'question-'+q.id,<>{sharedIds.length>1&&<p className="sheet-group-reference">相同问题 {sharedIds.length} 条记录，本次回答用于这些记录。</p>}{q.decision_group_id&&!q.answer&&<p className="sheet-group-reference">本组原话（仅供参考，请分别回答本题）：{[...((project as Obj).decision_answers||[])].reverse().find((a:Obj)=>a.question_id===q.decision_group_id)?.text||'尚无原话'}</p>}{!!q.options?.length&&<span className="sheet-origin">AI 推荐回答 · 点击填入后可修改</span>}<div className="sheet-answer-options">{(q.options||[]).map((option:string,n:number)=><button type="button" key={n} disabled={disabled} onClick={()=>updateAnswers(sharedIds,option)}>{option}</button>)}</div>{!q.answer&&(project as Obj).refinement_completion?.answers?.[q.id]?.text===currentSheet.answers[q.id]&&<span className="sheet-origin">AI 起草 · 待你确认，可直接修改</span>}{(project as Obj).refinement_completion?.answers?.[q.id]?.text&&(project as Obj).refinement_completion.answers[q.id].text!==currentSheet.answers[q.id]&&<div className="sheet-group-reference"><span className="sheet-origin">AI 新草稿 · 已保留你的内容</span><p>{(project as Obj).refinement_completion.answers[q.id].text}</p><button type="button" disabled={disabled} onClick={()=>updateAnswers(sharedIds,(project as Obj).refinement_completion.answers[q.id].text)}>使用这份建议</button></div>}<textarea rows={3} aria-label={q.question+' 我的回答'} disabled={disabled} value={currentSheet.answers[q.id]||''} onChange={e=>updateAnswers(sharedIds,e.target.value)}/>{clearAnswer(currentSheet.answers[q.id]||'')&&<AnswerImplementation questionId={q.id} questionText={q.question} answer={currentSheet.answers[q.id]} targets={sheetImplementationTargets(project,currentSheet,q)} suggestions={q.answer_location_suggestions} value={currentSheet.answer_implementations?.[q.id]||[]} disabled={disabled} onChange={refs=>updateAnswerImplementations(sharedIds,refs)}/>}</>,q.blocking!==false,<><p>{q.blocking===false?'可选，暂不确定可留空。':'这项决定需要明确回答。请同时核对上方关联需求。'}</p>{!q.options?.length&&<button type="button" disabled={disabled} onClick={()=>requestAI({mode:'question',question_id:q.id},'请只为这道问题起草一份具体可直接确认的回答及依据，不改其他内容。问题：'+q.question+'。当前草稿：'+(currentSheet.answers[q.id]||'尚未填写'))}>请 AI 提供建议</button>}<p>关联：{(q.related_refs||[]).map((id:string)=>project.items.find(i=>i.id===id)?.title||id).join('、')||'项目范围'}</p><small>原记录：{q.id}</small><button type="button" onClick={()=>props.onSources(q.source_refs||[])}>查看原题来源</button>{sharedIds.length>1&&<details className="sheet-question-records"><summary>查看 {sharedIds.length} 条原记录与来源</summary>{sharedIds.map(id=>{const original=allQuestions.find(item=>item.id===id)||q;return <div className="sheet-question-record" data-original-question-id={id} key={id}><p><strong>原 ID：</strong>{id}</p><p>{original.question}</p><p>关联：{(original.related_refs||[]).map((ref:string)=>project.items.find(item=>item.id===ref)?.title||ref).join('、')||'项目范围'}</p><button type="button" onClick={()=>props.onSources(original.source_refs||[])}>查看这条原题来源</button></div>;})}</details>}{clearAnswer(currentSheet.answers[q.id]||'')&&linkedClauseItems(project,[q]).map(item=><p className="sheet-group-reference" key={item.id}>这条回答还关联「{item.title}」。请核对下方原规则；回答不会自动改写规则。<button type="button" onClick={()=>locate({kind:'clause',item_id:item.id,message:''})}>核对关联规则：{item.title}</button></p>)}{!!q.options?.length&&<span className="sheet-origin">选项为 AI 推荐，点击填入后仍可修改。</span>}</>);
  const renderQuestionEntry=(entry:QuestionEntry):React.ReactNode=>{
    if(entry.kind==='related')return <tr key={entry.key}><td colSpan={3}><details className="sheet-related-questions" open={!!expandedRelatedGroups[entry.key]} onToggle={event=>{const open=event.currentTarget.open;setExpandedRelatedGroups(value=>({...value,[entry.key]:open}));}}><summary>同一需求的相关问题 · {entry.questions.length}条</summary><p>这些原题相关，但不是同一问题；请逐条回答、逐条落实，不会复制答案或批量已答。</p><table aria-label="同一需求的相关问题"><colgroup><col style={{width:'20%'}}/><col style={{width:'59%'}}/><col style={{width:'21%'}}/></colgroup><tbody>{entry.children?.map(renderQuestionEntry)}</tbody></table></details></td></tr>;
    return renderQuestion(entry.representative,entry.kind==='duplicate'?entry.questions.map(q=>q.id):[entry.representative.id]);
  };
  return <section ref={root} className="refinement-sheet" data-focus-mode={focusMode} aria-label="表格完善需求" onFocusCapture={event=>{
      const key=(event.target as HTMLElement).closest('[data-cell-key^="question-"]')?.getAttribute('data-cell-key');
      if(key)setFocusedAnswer(key.slice('question-'.length));
    }}>
    <header className="refinement-sheet-heading"><div className="sheet-heading-copy"><h2>需求确认表</h2><span className="sheet-heading-summary">{sheet.scope.length} 条本期需求 · {missing.length?'还有 '+missing.length+' 项待补充':pendingAnswers.length?pendingAnswers.length+' 项回答待落实':'必填信息已填入'}</span></div><div className="sheet-view-actions"><button type="button" className="secondary" title="AI 可一次起草需求、验收和答案对应的正文；你核对后确认。" disabled={disabled} onClick={()=>requestAI({mode:'all'},'请完整起草本表内容和每道未答问题的推荐回答，直接填入可核对的草稿，不要求用户逐格打字。已有人工内容保留；未明确决定给具体建议并标待确认。'+(pendingAnswers.length?'同时把已填写答案起草到直接关联正文与验收。':''))}>AI 补充建议</button><details className="sheet-more-actions"><summary>更多操作</summary><button type="button" disabled={disabled||!missing.length} onClick={()=>requestAI({mode:'required'},'请一次补全所有必填未填项，给出具体可确认草稿；缺少已定依据时提供标为推荐的方案，不以未知占位。当前缺项：'+JSON.stringify(missing.map(({kind,field,path,item_id,question_id})=>({kind,field,path,item_id,question_id}))).slice(0,1800))}>AI 补全必填项</button><button type="button" disabled={disabled} onClick={()=>void saveDraft().then(()=>navigate(props.onCreate))}>补充需求</button><button type="button" aria-pressed={focusMode} onClick={()=>setFocusMode(value=>!value)}>{focusMode?'退出专注核对':'专注核对'}</button></details></div></header>
    <p className="stage-next-step"><strong>下一步：</strong>{missing.length?'先用“待补充”定位第一处，完成当前有效需求与业务决定。':pendingAnswers.length?'先用“待落实回答”定位第一项，核对正文或验收中的落实位置。':'当前必填项已填入；核对整表后，使用底部唯一确认按钮提交。'}</p>
    {rejectedEmptyDrafts.length>0&&<div className="table-callout table-empty-draft-callout" role="alert">
      <strong>本次 AI 需求草稿未载入</strong>
      <p>聊天、项目资料与摘要、浏览器草稿均保留；可重新生成或继续补充需求。</p>
      {rejectedEmptyDrafts.map((row,n)=><div key={n} className="business-draft-rejected"><strong>{typeof row.raw?.title==='string'&&row.raw.title.trim()?row.raw.title:'未能识别的需求条目'}</strong><p>{row.reason||'未通过需求草稿校验'}</p></div>)}
      <button type="button" className="primary" disabled={disabled} onClick={()=>requestAI({mode:'all'},'请根据项目中已有的用户原话生成完整、可核对的需求草稿，并纠正以下校验问题。已答决定不重问，仍保留人工值。本次校验问题：'+rejectedDraftDiagnostics)}>重新生成需求草稿</button>
      <button type="button" disabled={disabled} onClick={props.onCreate}>补充需求</button>
    </div>}
    <nav className="sheet-navigation" aria-label="需求表定位">
      {!!missing.length&&<button type="button" className="sheet-missing-jump" onClick={()=>locate(missing[0])}>待补充 · {missing.length}</button>}
      <button type="button" onClick={()=>locateSection('context')}>项目摘要</button>
      <button type="button" onClick={()=>locateSection('requirements')}>需求与验收</button>
      <button type="button" className="sheet-pending-jump" onClick={()=>pendingAnswers.length?locatePending():locateSection('answers')}>待落实回答{pendingAnswers.length?' · '+pendingAnswers.length:''}</button>
      {!!clauseItems.length&&<button type="button" onClick={()=>locateSection('clauses')}>关联规则</button>}
      {!!(settledQuestions.length+historyQuestions.length)&&<button type="button" onClick={()=>locateSection('answer-history')}>已落实与历史 · {settledQuestions.length+historyQuestions.length}</button>}
      {!!reqs.length&&<label className="sheet-requirement-jump"><span>定位需求</span><select aria-label="定位到需求" value="" onChange={event=>{const id=event.target.value;if(id)locate({kind:'requirement',item_id:id,message:''});}}><option value="">选择需求…</option>{reqs.map((item,index)=><option key={item.id} value={item.id}>{index+1}. {item.title}{sheet.scope.includes(item.id)?'':sheet.scope_excluded_ids?.includes(item.id)?'（暂不纳入本期）':'（待核对范围）'}</option>)}</select></label>}
      {!!pendingAnswers.length&&<span className="sheet-pending-controls"><button type="button" disabled={!pendingAnswers.length} onClick={()=>locatePending(-1)}>上一项待落实</button><button type="button" disabled={!pendingAnswers.length} onClick={()=>locatePending(1)}>下一项待落实</button></span>}
    </nav>
    <DraftPreparationNotice missing={missingAcceptance.map(item=>({id:item.id,title:item.title}))} preparation={preparationInfo}/>
    {(error||loadError)&&<div role="alert" className="sheet-banner-error">{error||loadError}{!!issues.length&&<button type="button" onClick={()=>locate(issues[0])}>定位第一处</button>}{loadError&&<button type="button" onClick={()=>setReload(x=>x+1)}>重新读取</button>}</div>}
    {stale&&<div className="sheet-banner-error">{(sheet.merge_conflicts?.length?sheet.merge_conflicts:['项目有新内容，本地填写已保留。']).map((message,n)=><p key={n}>{message}</p>)}<button type="button" disabled={disabled} onClick={loadLatest}>保留我的修改，继续核对</button></div>}
    {!stale&&!!sheet.merge_conflicts?.length&&<div className="sheet-banner-error" role="alert">{sheet.merge_conflicts.map((message,n)=><p key={n}>{message}</p>)}<p>你的修改已保留；请核对冲突后再明确点击整表确认。</p></div>}
    <div className="refinement-sheet-scroll" onWheel={()=>{aiAnchor.current=null;}} onTouchStart={()=>{aiAnchor.current=null;}} tabIndex={0} aria-label="滚动核对全部信息"><table aria-label="需求确认表"><colgroup><col style={{width:'20%'}}/><col style={{width:'59%'}}/><col style={{width:'21%'}}/></colgroup><thead><tr><th>需确认的信息</th><th>当前内容 · 可直接修改</th><th>填写提示与来源</th></tr></thead><tbody>
      {latestDraftReview(project)&&<tr><td colSpan={3}><details className="sheet-draft-review"><summary>查看 AI 整理结果与未载入内容</summary><DraftPackageStatus project={project} onLocate={id=>locate({kind:'requirement',item_id:id,message:''})} onCreate={()=>void saveDraft().then(()=>navigate(props.onCreate))}/></details></td></tr>}
      <tr className="sheet-group" data-section="context" tabIndex={-1}><th colSpan={3}>1. 项目摘要 <small>已有聊天和资料整理的内容直接展示，确认前可修改</small></th></tr>
      {renderRow('适用场景','这次属于哪种需求','profile',<div className="sheet-inline"><select aria-label="需求类型" value={sheet.profile.scenario} disabled={disabled} onChange={e=>{const profile={...sheet.profile,scenario:e.target.value as ScopeProfile['scenario']};const states={...sheet.states};for(const k of ['current_state','preserve_scope'])if(!sheet.values[k]?.trim())states[k]={status:isScopeFieldRequired(k,profile)?'unknown':'not_applicable'};replace({...sheet,profile,states});}}><option value="unspecified">尚未确定</option><option value="new_product">全新产品</option><option value="new_module">新增模块</option><option value="modify_existing">修改已有功能</option></select>{sheet.profile.scenario==='new_module'&&<select aria-label="模块关联" disabled={disabled} value={sheet.profile.dependencies} onChange={e=>{const profile={...sheet.profile,dependencies:e.target.value as ScopeProfile['dependencies']};const states={...sheet.states};for(const k of ['current_state','preserve_scope'])if(!sheet.values[k]?.trim())states[k]={status:isScopeFieldRequired(k,profile)?'unknown':'not_applicable'};replace({...sheet,profile,states});}}><option value="unknown">关联尚未确定</option><option value="independent">独立模块</option><option value="shared">涉及其他模块</option></select>}</div>,false,'新产品和独立模块不要求填写现状与保持项。')}
      {Object.entries(scopeLabels).map(([field,label])=>{const state=sheet.states[field]||{status:'unknown'},optional=!scopeFieldRequired(field,sheet,project),proposal=project.product_context_proposal?.[field],fromAI=!String(project.product_context?.[field]||'').trim()&&!!proposal&&sheet.values[field]===proposal&&(project as Obj).context_review_statuses?.[field]!==true&&!['unknown','none','not_applicable'].includes((project as Obj).context_field_states?.[field]?.status)&&typeof (project as Obj).product_context_proposal_evidence?.[field]?.run_id==='string'&&!!(project as Obj).product_context_proposal_evidence[field].run_id.trim();
        return renderRow('项目摘要',label,'context-'+field,<><textarea rows={2} aria-label={label} disabled={disabled} value={sheet.values[field]||''} placeholder={optional?'选填；没有相关内容可留空':'请核对或补充'+label} onChange={e=>replace({...sheet,values:{...sheet.values,[field]:e.target.value},states:{...sheet.states,[field]:{...state,status:e.target.value.trim()?'known':'unknown'}}})}/><select className="sheet-status-select" aria-label={label+' 状态'} value={state.status} disabled={disabled} onChange={e=>replace({...sheet,states:{...sheet.states,[field]:{...state,status:e.target.value as FieldState['status']}}})}>{allowedStates(field,sheet.profile).map(s=><option key={s} value={s}>{{known:'内容已明确，随本次确认',unknown:optional?'选填，暂未说明':'尚待明确',not_applicable:'本需求不适用',none:'已确认没有额外约束'}[s]}</option>)}</select></>,!optional,<>{fromAI?<span className="sheet-origin">AI 整理 · 待你确认</span>:<span className="sheet-origin">{sheet.values[field]?'已保存内容':'暂无内容'}</span>}{optional&&<p>选填，不影响进入交付。</p>}{fromAI&&(project as Obj).product_context_proposal_evidence?.[field]?.source_refs?.length>0&&<button type="button" onClick={()=>props.onSources((project as Obj).product_context_proposal_evidence[field].source_refs)}>查看来源</button>}</>);
      })}
      <tr className={'sheet-group'+(issues.some(i=>i.kind==='scope')?' sheet-invalid':'')} data-cell-key="scope" data-section="requirements" tabIndex={-1}><th colSpan={3}>2. 本期需求与验收 <small>范围只是本期包含与否，无需逐条勾选、采纳或再次确认</small>{issues.filter(i=>i.kind==='scope').map((i,n)=><p key={n} className="sheet-error">{i.message}</p>)}</th></tr>
      {!reqs.length&&<tr><td colSpan={3}>尚无需求草稿。<button type="button" disabled={disabled} onClick={props.onCreate}>补充需求</button></td></tr>}
      {reqs.map(item=>{const row=sheet.rows[item.id];if(!row)return null;const included=sheet.scope.includes(item.id),excluded=sheet.scope_excluded_ids?.includes(item.id),confirmed=states.find(s=>s.requirement_id===item.id)?.current,revision=latestRequirementRevision(project,item),revisionSelected=revision?.id===row.candidate_id,revisionFromAI=hasGenerationRun(revision),selectedRevisionFromAI=hasGenerationRun(project.items.find(i=>i.id===row.candidate_id));return <React.Fragment key={item.id}>
        <tr className="sheet-requirement" data-requirement-id={item.id}><th scope="row">{item.title}</th><td><label className="sheet-inline">本期范围<select aria-label={item.title+' 本期范围'} disabled={disabled} value={included?'included':excluded?'excluded':'pending'} onChange={e=>{const value=e.target.value;replace({...sheet,scope:value==='included'?[...new Set([...sheet.scope,item.id])]:sheet.scope.filter(id=>id!==item.id),scope_excluded_ids:value==='excluded'?[...new Set([...(sheet.scope_excluded_ids||[]),item.id])]:(sheet.scope_excluded_ids||[]).filter(id=>id!==item.id)});}}><option value="included">本期包含</option><option value="pending">待核对范围</option><option value="excluded">暂不纳入本期</option></select></label></td><td><span className="sheet-origin">{selectedRevisionFromAI?'AI 修订草稿·待确认':confirmed?'已确认内容':'待确认草稿'}</span><button type="button" onClick={()=>props.onSources((project.items.find(i=>i.id===row.candidate_id)||item).source_refs||[])}>查看来源</button></td></tr>
        {revision&&<tr><td colSpan={3}><details className="sheet-group-reference">
          <summary><strong>{revisionFromAI?'有一版 AI 修订待核对':'有一版修订待核对'}</strong> <span className="sheet-origin">仅草稿 · 尚未确认</span></summary>
          <p><strong>修订后的需求正文：</strong>{revision.statement||'此版未填写需求正文。'}</p>
          <p>业务说明：已填写 {Object.keys(fields).filter(field=>revision.behavior?.[field]?.trim()).length} / {Object.keys(fields).length} 项；详细定义：{revision.detail_contract?'此版包含新的详细定义，将随此版一起带入':'此版没有新的详细定义，带入时保留当前详细定义'}。</p>
          <details><summary>阅读此版的完整业务说明</summary><dl>{Object.entries(fields).map(([field,label])=><div key={field}><dt>{label}</dt><dd>{revision.behavior?.[field]||'未填写'}</dd></div>)}</dl></details>
          <details><summary>对照原正式内容</summary>
            <p><strong>原需求正文：</strong>{item.statement||'原正式内容为空。'}</p>
            <p>原详细定义：{item.detail_contract?'有':'无'}。以下原业务说明仅供对照，不会在核对修订时自动覆盖。</p>
            <dl>{Object.entries(fields).map(([field,label])=><div key={field}><dt>{label}</dt><dd>{item.behavior?.[field]||'未填写'}</dd></div>)}</dl>
          </details>
          {revisionSelected?<p className="sheet-origin">此版已在当前草稿中，仍需随整表明确确认；原正式内容可在上方对照。</p>:
            <button type="button" className="primary" disabled={disabled} onClick={()=>applyRevision(item,revision)}>将此修订带入草稿</button>}
          <p><small>带入只修改当前浏览器中的本条草稿，不会调用 AI 或保存正式内容；现有验收条件和其他需求保留，人工改动冲突会继续提示核对。</small></p>
        </details></td></tr>}
        {included?<>
          {renderRow(item.title,'需求内容','requirement-'+item.id+'-statement',<textarea rows={4} aria-label={item.title+' 需求内容'} disabled={disabled} value={row.statement} onChange={e=>updateRow(item.id,{statement:e.target.value})}/>,true,<DraftRowReview project={project} requirementId={item.id} candidateId={row.candidate_id} disabled={disabled} onSources={props.onSources} onRetry={()=>void saveDraft().then(()=>navigate(()=>props.onRecommend(item.id,aiInstructions(detailDraftRequested(item,row)?'请重新整理本条业务说明、验收及字段、交互、业务流程图和状态的完整详细定义草稿；保留已保存原话和人工内容，未知保持建议或待确认，核对后再采用。':'请只重新整理这条需求，保留已有资料和人工内容，未知决定保持待确认。',{...sheet,rows:{[item.id]:row}},baseline.current),detailDraftRequested(item,row))))}/>)}
          {renderRow(item.title,'本次改动','requirement-'+item.id+'-change_type',<select aria-label={item.title+' 本次改动'} disabled={disabled} value={row.change_type} onChange={e=>updateRow(item.id,{change_type:e.target.value})}>{Object.entries(changeLabels).map(([v,l])=><option key={v} value={v}>{l}</option>)}</select>,true)}
          {renderRow(item.title,'业务说明','requirement-'+item.id+'-behavior-summary',<>
            <button type="button" aria-expanded={!!expandedBehavior[item.id]} aria-label={(expandedBehavior[item.id]?'收起':'展开')+'业务说明：'+item.title} onClick={()=>setExpandedBehavior(value=>({...value,[item.id]:!value[item.id]}))}>{expandedBehavior[item.id]?'收起业务说明':'展开业务说明'}</button>
            <small>已填写 {Object.keys(fields).filter(field=>row.behavior[field]?.trim()).length} / {Object.keys(fields).length} 项</small>
            <p className="sheet-behavior-summary">{['actor','flow','permissions'].filter(field=>row.behavior[field]?.trim()).map(field=>fields[field]+'：'+row.behavior[field].slice(0,70)+(row.behavior[field].length>70?'…':'')).join(' · ')||'暂无业务说明，需要时可展开填写。'}</p>
          </>,false,'根据当前资料查看或修改；未说明的可选内容保留为空。')}
          {expandedBehavior[item.id]&&Object.entries(fields).map(([field,label])=>renderRow(item.title,label,'requirement-'+item.id+'-'+field,<textarea rows={2} aria-label={item.title+' '+label} disabled={disabled} value={row.behavior[field]||''} placeholder="资料未说明时可保留为空；不确定的规则不要编造" onChange={e=>updateRow(item.id,{behavior:{...row.behavior,[field]:e.target.value}})}/>,false,<><p>{fieldHelp[field]}</p>{selectedRevisionFromAI&&<span className="sheet-origin">AI 草稿 · 请核对后确认</span>}</>))}
          <tr className={issues.some(issue=>issue.item_id===item.id&&(issue.path||issue.field||'').startsWith('detail_contract'))?'sheet-invalid':undefined} data-cell-key={'requirement-'+item.id+'-detail_contract'}><th scope="row"><small>{item.title}</small><strong>详细定义</strong><span className="sheet-origin">字段 · 交互 · 流程 · 状态</span>{issues.some(issue=>issue.item_id===item.id&&(issue.path||issue.field||'').startsWith('detail_contract'))&&<span className="sheet-missing-label">待补充 · {issues.filter(issue=>issue.item_id===item.id&&(issue.path||issue.field||'').startsWith('detail_contract')).length}</span>}</th><td colSpan={2}>
            <button type="button" aria-expanded={expandedDetail[item.id]??!!row.detail_contract} onClick={()=>setExpandedDetail(value=>({...value,[item.id]:!(value[item.id]??!!row.detail_contract)}))}>{(expandedDetail[item.id]??!!row.detail_contract)?'收起详细定义':'展开详细定义'}</button>
            {issues.filter(issue=>issue.item_id===item.id&&(issue.path||issue.field)==='detail_contract').map((issue,index)=><p role="alert" className="sheet-error" key={index}>{issue.message}</p>)}
            {!row.detail_contract&&<p className="sheet-origin">{item.detail_required?'本需求需要补充详细定义，随整表确认。':'历史需求保留原有内容；需要时启用详细定义。'}{latestDetailCandidate(project,item)?'已有 AI 详细定义草稿可供核对。':''}</p>}
            {(expandedDetail[item.id]??!!row.detail_contract)&&<DetailContractEditor requirementId={item.id} title={item.title} value={row.detail_contract} base={item.detail_contract} candidate={latestDetailCandidate(project,item)} disabled={disabled} conflicts={row.detail_conflicts} issues={issues.filter(issue=>issue.item_id===item.id&&(issue.path||issue.field||'').startsWith('detail_contract')).map(issue=>({path:issue.path||issue.field!,message:issue.message}))} technicalScopeProperties={detailUnknownScopeProperties(project,item.id,row.detail_contract)} references={project.items.filter(ref=>!ref.target_item_id&&!ref.superseded_by&&!['rejected','deferred','excluded'].includes(ref.selection_status)).map(ref=>({id:ref.id,title:ref.title||ref.statement,kind:ref.kind}))} onSources={props.onSources} onChange={(detail_contract,detail_conflicts)=>updateRow(item.id,{detail_contract,...(detail_conflicts?{detail_conflicts}:{})})}/>}
          </td></tr>
          {renderRow(item.title,'验收条件','requirement-'+item.id+'-acceptance',<div className="sheet-acceptances">{!row.acceptance_items.some(ac=>ac.statement.trim())&&<p className="sheet-origin">本格验收内容为空。可用表头“AI 补充建议”一次起草整表；你只需核对或修改，不必逐格发起。</p>}{(row.acceptance_items.length?row.acceptance_items:[{statement:''}]).map((ac,n)=><label key={ac.id||n}>验收 {n+1}{ac.ai_suggested&&<span className="sheet-origin">AI 建议 · 随本次确认</span>}<textarea rows={3} aria-label={item.title+' 验收建议 '+(n+1)} disabled={disabled} value={ac.statement} onChange={e=>{const items=[...(row.acceptance_items.length?row.acceptance_items:[ac])];items[n]={...ac,statement:e.target.value};updateRow(item.id,{acceptance_items:items});}}/>{ac.invalid_candidate&&<div role="alert" className="sheet-group-reference"><strong>{ac.ai_suggested?'先核对这条失效的 AI 建议':'先核对这条失效的修订建议'}</strong><p>原建议已被更新、暂缓或拒绝。你的文字已保留，需要明确处理后才能确认。</p><button type="button" disabled={disabled} onClick={()=>{const items=[...row.acceptance_items];items[n]=resolveExpiredAcceptance(ac,'saved');updateRow(item.id,{acceptance_items:items});}}>采用当前已保存内容</button><button type="button" disabled={disabled} onClick={()=>{const items=[...row.acceptance_items];items[n]=resolveExpiredAcceptance(ac,'manual');updateRow(item.id,{acceptance_items:items});}}>我已核对，保留为人工修改</button></div>}{ac.conflict_statement&&<div className="sheet-group-reference"><strong>最新已保存内容（与你的修改不同）</strong><p>{ac.conflict_statement}</p></div>}{(ac.suggestions||[]).filter(s=>s.candidate_id!==ac.candidate_id).map(suggestion=><div className="sheet-group-reference" key={suggestion.candidate_id}><span className="sheet-origin">{ac.candidate_ai_suggested?.[suggestion.candidate_id]?'AI 修订建议 · 尚未使用':'修订建议 · 尚未使用'}</span><p>{suggestion.statement}</p><button type="button" disabled={disabled} onClick={()=>{const items=[...row.acceptance_items];items[n]={...ac,statement:suggestion.statement,candidate_id:suggestion.candidate_id,ai_suggested:ac.candidate_ai_suggested?.[suggestion.candidate_id]===true,invalid_candidate:undefined,conflict_statement:undefined};updateRow(item.id,{acceptance_items:items});}}>使用此验收建议</button></div>)}{!(ac.id&&project.items.find(i=>i.id===ac.id)?.selection_status==='selected')&&row.acceptance_items.length>1&&<button type="button" disabled={disabled} onClick={()=>updateRow(item.id,{acceptance_items:row.acceptance_items.filter((_,k)=>k!==n)})}>移除此条验收</button>}</label>)}<button type="button" disabled={disabled} onClick={()=>updateRow(item.id,{acceptance_items:[...row.acceptance_items,{statement:''}]})}>添加验收</button></div>,true,<><p>至少一条：执行什么操作，应看到什么结果。</p><button type="button" disabled={disabled} onClick={()=>void saveDraft().then(()=>navigate(()=>props.onRecommend(item.id,aiInstructions(detailDraftRequested(item,row)?'请根据本条已保存资料生成或修正字段、交互、业务流程图和状态的完整详细定义草稿，同时核对业务说明和验收；保留人工编辑，未知保持建议或待确认，生成后人工核对采用。':'请根据本条已保存资料补充或修正业务说明和验收，未知关键决定保留待确认。', {...sheet,rows:{[item.id]:row}},baseline.current),detailDraftRequested(item,row))))}>请 AI 完善此需求</button></>)}
        </>:<tr><td colSpan={3} className="sheet-excluded">{row.statement} <small>{excluded?'暂不纳入本期，本次不提交；原有内容和确认历史保留。':'尚未核对是否纳入，本次未确认；原有内容和确认历史保留。'}</small></td></tr>}
      </React.Fragment>;})}
      <tr className="sheet-group" data-section="answers" tabIndex={-1}><th colSpan={3}>3. 本期待决定与待落实 <small>只突出当前有效且仍需处理的问题；回答可先保存，落实需要核对直接关联条款中的原文位置</small></th></tr>
      {questionEntries.map(renderQuestionEntry)}
      {!questionEntries.length&&<tr><td colSpan={3}>本期暂无待决定或待落实的问题。</td></tr>}
      {!!settledQuestions.length&&<tr className="sheet-group" data-section="answer-history" tabIndex={-1}><th colSpan={3}>本期已落实回答 <small>默认收起；展开后可修改，修改后重新核对落实位置</small></th></tr>}
      {settledQuestions.map(q=>renderFoldedQuestion(q))}
      {!!historyQuestions.length&&<tr className="sheet-group" data-section={settledQuestions.length?undefined:'answer-history'} tabIndex={-1}><th colSpan={3}>历史问题 <small>拆分、移出或已完成审查的原题，仅供追溯，不冒充本期待答</small></th></tr>}
      {historyQuestions.map(q=>renderFoldedQuestion(q,true))}
      {!!clauseItems.length&&<tr className="sheet-group" data-section="clauses" tabIndex={-1}><th colSpan={3}>4. 关联业务规则 <small>你填写的回答还关联以下规则。请核对原文是否与回答一致；这些规则将随整表一起确认，推荐回答不会自动改写规则。</small></th></tr>}
      {clauseItems.map(item=>{const row=clauseRows.find(clause=>clause.item_id===item.id)!;return renderRow('关联业务规则',item.title,'clause-'+item.id,
        <LinkedClauseEditor item={item} row={row} disabled={disabled} onChange={patch=>updateClause(item.id,patch)}/>,true,
        <><span className="sheet-origin">{item.selection_status==='candidate'?'待采纳规则 · 随本次确认采纳':'已保存规则 · 随本次重新核对'}</span><p>请确认条款与已填写回答一致；不同业务范围的规则分别保留。</p><button type="button" onClick={()=>props.onSources(item.source_refs||[])}>查看规则来源：{item.title}</button>{questions.filter(q=>inScopeQuestion(project,q,sheet.scope)&&clearAnswer(sheet.answers[q.id]||'')&&(q.related_refs||[]).includes(item.id)).map(q=><p key={q.id}>关联回答：{q.question}<br/>{sheet.answers[q.id]}<button type="button" onClick={()=>locate({kind:'question',question_id:q.id,message:''})}>返回这条回答</button></p>)}</>);})}
    </tbody></table></div>
    <footer className="refinement-sheet-footer"><div title={actionCopy.detail}><strong role={notice&&!working?'status':undefined}>{working?'正在检查并确认…':notice||actionCopy.status}</strong><small>{actionCopy.detail}</small></div><button type="button" disabled={disabled} onClick={()=>void saveDraft()}>保存草稿</button><button type="button" className="primary" disabled={disabled||!!loadError} onClick={()=>void confirm()}>{working?'正在确认…':actionCopy.label}</button></footer>
    {archived&&<button type="button" className="sheet-restore" disabled={disabled} onClick={()=>{try{const old=JSON.parse(session.getItem(key+'-previous')||'null');if(old){replace(old);setStale(old.revision!==project.revision);setNotice('已恢复上次草稿。');}}catch{}}}>恢复上次表格草稿</button>}
  </section>;
}
