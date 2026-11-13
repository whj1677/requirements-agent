import type {Project} from '../api';
import {scopeDraft,scopeLabels,fieldStatus,defaultScopeProfile,ScopeProfile,FieldState,hasGenerationRun} from '../scope-draft';
import {requirementBehaviorLabels as fields} from './requirement-fields';
import {directReviewTargets} from './answer-actions';
import {validImplementationRefs, implementationsComplete, type AnswerImplementations, type AnswerImplementationRef} from '../answer-application-review';
import {mergeDetailContract,detailPathValue,type DetailContract,type DetailConflict} from './detail-contract';
type Obj=Record<string,any>;
export type Acceptance={id?:string;statement:string;candidate_id?:string;ai_suggested?:boolean;applied_candidate_ids?:string[];candidate_ai_suggested?:Record<string,boolean>;invalid_candidate?:{id:string;latest_statement:string;ai_suggested?:boolean};conflict_statement?:string;suggestions?:{candidate_id:string;statement:string}[]};
export type Row={requirement_id:string;statement:string;change_type:string;behavior:Obj;acceptance_items:Acceptance[];candidate_id?:string;detail_contract?:DetailContract;detail_conflicts?:DetailConflict[];detail_suggestion?:{contract:DetailContract;candidate_id:string}};
export type LinkedClause={item_id:string;statement:string;behavior:Record<string,string>};
export type Sheet={revision:number;values:Obj;states:Record<string,FieldState>;profile:ScopeProfile;scope:string[];scope_excluded_ids?:string[];rows:Record<string,Row>;answers:Record<string,string>;answer_provenance?:Record<string,{saved_answer:string;ai_answer?:string}>;answer_implementations?:AnswerImplementations;linked_clauses?:Record<string,LinkedClause>;merge_conflicts?:string[];labels?:Record<string,string>};
export const equal=(a:unknown,b:unknown)=>JSON.stringify(a)===JSON.stringify(b);
export const activeItem=(i:Obj)=>!i.superseded_by&&!['rejected','deferred','excluded'].includes(i.selection_status);
export const requirements=(p:Project)=>p.items.filter(i=>i.kind==='requirement'&&i.applies_to==='to_be'&&!i.target_item_id&&activeItem(i));
const acceptance=(p:Project,id:string)=>p.items.filter(i=>i.kind==='acceptance'&&i.applies_to==='to_be'&&!i.target_item_id&&activeItem(i)&&((i.related_refs||[]).includes(id)||(p.items.find(r=>r.id===id)?.related_refs||[]).includes(i.id)));
const acceptanceDraft=(p:Project,ac:Obj):Acceptance=>{
  const revisions=p.items.filter(i=>i.kind==='acceptance'&&i.action==='revise'&&i.target_item_id===ac.id&&i.applies_to==='to_be');
  return {
    id:ac.id,statement:ac.statement,
    applied_candidate_ids:(ac.confirmed_revisions||[]).map((review:Obj)=>review.candidate_id),
    ai_suggested:ac.selection_status==='candidate'&&hasGenerationRun(ac),
    candidate_ai_suggested:Object.fromEntries(revisions.filter(i=>i.id).map(i=>[i.id,hasGenerationRun(i)])),
    suggestions:revisions.filter(i=>i.selection_status==='candidate'&&!i.superseded_by).map(i=>({candidate_id:i.id,statement:i.statement})),
  };
};
export const uncertain=(text:string)=>/^(?:待确认|待明确|待定|待补充|未知|不确定|尚未明确|尚未确定|暂不确定|有冲突)(?:[。！!，,：:].*)?$/.test(text.trim());
export function inScopeQuestion(p:Project,q:Obj,scope:string[]){
  const roots=requirements(p),rootIds=new Set(roots.map(r=>r.id)),refs=q.related_refs||[];
  if(!refs.length)return true;
  const mapped=refs.map((id:string)=>{
    if(rootIds.has(id))return [id];
    const ac=p.items.find(i=>i.id===id&&i.kind==='acceptance'&&i.applies_to==='to_be'&&!i.target_item_id);
    if(!ac||(ac.related_refs||[]).some((ref:string)=>!rootIds.has(ref)))return [];
    return roots.filter(root=>(ac.related_refs||[]).includes(root.id)||(root.related_refs||[]).includes(id)).map(root=>root.id);
  });
  return mapped.some((ids:string[])=>!ids.length||ids.some(id=>scope.includes(id)));
}
export function sheetQuestions(p:Project){return p.questions.filter(q=>q.prewrite_resolution_current!==true&&!q.superseded_by&&q.kind!=='parent'&&(!q.out_of_scope_reason||q.scope_exclusion)&&(!['deferred','out_of_scope'].includes(q.status)||q.scope_exclusion));}
/** Answer text and original linked clauses remain separate until explicit table confirmation. */
export const clearAnswer=(text:string)=>!!text.trim()&&!/^\s*(?:暂时?|暂且|目前|还|尚|仍)?(?:不确定|不知道|不清楚|未确定|未明确|未知|未定|待定|待确认|待核对|待补充|待澄清|待讨论|待决策|后续再说)[。！!\s]*$/.test(text);
export function linkedClauseItems(p:Project,questions:Obj[]=sheetQuestions(p)){
  const refs=new Set(questions.flatMap(q=>q.related_refs||[]));
  return p.items.filter(i=>refs.has(i.id)&&['rule','constraint','goal'].includes(i.kind)&&i.applies_to==='to_be'&&!i.target_item_id&&!i.superseded_by&&['candidate','selected'].includes(i.selection_status));
}
export function answeredLinkedClauses(p:Project,sheet:Sheet){
  return linkedClauseItems(p,sheetQuestions(p).filter(q=>inScopeQuestion(p,q,sheet.scope)&&clearAnswer(sheet.answers[q.id]||'')));
}
export const linkedClausePayload=(p:Project,sheet:Sheet)=>answeredLinkedClauses(p,sheet).map(item=>{
  const row=sheet.linked_clauses?.[item.id];
  return {item_id:item.id,statement:row?.statement??item.statement??'',behavior:Object.fromEntries(Object.keys(fields).map(f=>[f,row?.behavior[f]??item.behavior?.[f]??'']))};
});

/** Project the table's final visible text without saving or adopting it. */
export function sheetReviewProject(p:Project,sheet:Sheet):Project{
  const clauses=sheet.linked_clauses||{},acs=new Map(Object.values(sheet.rows).flatMap(row=>row.acceptance_items.filter(ac=>ac.id).map(ac=>[ac.id!,ac])));
  return {...p,items:p.items.map(item=>{
    const row=sheet.rows[item.id];if(row&&sheet.scope.includes(item.id))return {...item,statement:row.statement,behavior:row.behavior,detail_contract:row.detail_contract,selection_status:'selected'};
    const ac=acs.get(item.id);if(ac)return {...item,statement:ac.statement,selection_status:'selected'};
    const clause=clauses[item.id];return clause?{...item,statement:clause.statement,behavior:clause.behavior,selection_status:'selected'}:item;
  })};
}
export const sheetImplementationTargets=(p:Project,sheet:Sheet,q:Obj)=>directReviewTargets(sheetReviewProject(p,sheet),q);

function localImplementationTargets(sheet:Sheet):Obj[]{
  return [...Object.values(sheet.rows).flatMap(row=>[
    {id:row.requirement_id,kind:'requirement',statement:row.statement,behavior:row.behavior,detail_contract:row.detail_contract},
    ...row.acceptance_items.filter(ac=>ac.id).map(ac=>({id:ac.id,kind:'acceptance',statement:ac.statement})),
  ]),...Object.values(sheet.linked_clauses||{}).map(row=>({id:row.item_id,kind:'rule',statement:row.statement,behavior:row.behavior}))];
}
/** Answer edits clear their witnesses; clause edits never silently refresh quotes. */
export function retainSheetImplementations(sheet:Sheet,previous?:Sheet|null):Sheet{
  const targets=localImplementationTargets(sheet);
  return {...sheet,answer_implementations:Object.fromEntries(Object.entries(sheet.answer_implementations||{}).flatMap(([qid,refs])=>{
    if(previous&&sheet.answers[qid]!==previous.answers[qid])return [];
    const valid=validImplementationRefs(refs,targets);return valid.length?[[qid,valid]]:[];
  }))};
}
export function sheetAnswerImplementations(p:Project,sheet:Sheet):AnswerImplementations{
  return Object.fromEntries(sheetQuestions(p).filter(q=>inScopeQuestion(p,q,sheet.scope)&&clearAnswer(sheet.answers[q.id]||'')).map(q=>[
    q.id,validImplementationRefs(sheet.answer_implementations?.[q.id]||[],sheetImplementationTargets(p,sheet,q)),
  ]));
}
export const pendingSheetAnswers=(p:Project,sheet:Sheet)=>sheetQuestions(p).filter(q=>inScopeQuestion(p,q,sheet.scope)&&clearAnswer(sheet.answers[q.id]||'')&&
  !implementationsComplete(sheet.answer_implementations?.[q.id]||[],sheetImplementationTargets(p,sheet,q)));

function savedImplementations(q:Obj):AnswerImplementationRef[]{
  if(q.answer_implementation_contract!==2||q.understanding_status!=='applied')return [];
  const current=new Set(q.applied_item_ids||[]);
  return Object.entries(q.answer_applications||{}).flatMap(([item_id,application])=>{
    const record=application as Obj;
    return current.has(item_id)&&record.contract_version===2?(record.witnesses||[]).map((w:Obj)=>({item_id,field:w.field,quote:w.quote})):[];
  });
}
export function initial(p:Project,states:Obj[]):Sheet{
  const completion=(p as Obj).refinement_completion||{},bulk=completion.mode==='all'||completion.mode==='required';
  const reviewed=(p as Obj).context_review_statuses||{};
  const savedProfile=(p as Obj).scope_profile||defaultScopeProfile;
  const values=scopeDraft(p),profile=bulk&&completion.profile?{
    scenario:savedProfile.scenario==='unspecified'?completion.profile.scenario:savedProfile.scenario,
    dependencies:savedProfile.dependencies==='unknown'?completion.profile.dependencies:savedProfile.dependencies,
  }:savedProfile;
  if(bulk)for(const key of Object.keys(scopeLabels))if(reviewed[key]!==true&&!['unknown','none','not_applicable'].includes((p as Obj).context_field_states?.[key]?.status)&&!String(values[key]||'').trim()&&p.product_context_proposal?.[key])values[key]=p.product_context_proposal[key];
  const fieldStates=Object.fromEntries(Object.keys(scopeLabels).map(key=>{
    const saved=(p as Obj).context_field_states?.[key],evidence=(p as Obj).product_context_proposal_evidence?.[key];
    const proposalOnly=!String(p.product_context?.[key]||'').trim()&&!!String(values[key]||'').trim();
    const status=['unknown','none','not_applicable'].includes(saved?.status)?saved?.status:reviewed[key]===true?saved?.status||fieldStatus(key,p.product_context||{},(p as Obj).context_field_states||{},profile):bulk&&values[key]===p.product_context_proposal?.[key]&&String(values[key]||'').trim()?'known':proposalOnly?(evidence?.uncertain||uncertain(values[key])?'unknown':'known'):fieldStatus(key,p.product_context||{},(p as Obj).context_field_states||{},profile);
    return [key,{...saved,status}];
  }));
  const rows=Object.fromEntries(requirements(p).map(item=>{
    // Wait for the server's content fingerprints before choosing an AI version.
    const confirmed=states.find(row=>row.requirement_id===item.id)?.current;
    const completionOffer=bulk?p.items.filter(i=>completion.candidate_ids?.includes(i.id)&&i.target_item_id===item.id&&i.kind==='requirement'&&i.selection_status==='candidate'&&!i.superseded_by).at(-1):undefined;
    const offered=completionOffer||(confirmed?item:p.items.filter(i=>i.target_item_id===item.id&&i.kind==='requirement'&&i.selection_status==='candidate'&&!i.superseded_by).at(-1)||item);
    const detailSuggestion=p.items.filter(i=>i.target_item_id===item.id&&i.kind==='requirement'&&i.selection_status==='candidate'&&!i.superseded_by&&i.detail_contract).at(-1);
    return [item.id,{requirement_id:item.id,statement:offered.statement||'',change_type:offered.change_type&&offered.change_type!=='unspecified'?offered.change_type:item.change_type&&item.change_type!=='unspecified'?item.change_type:bulk?(completion.change_types?.[item.id]||'unspecified'):'unspecified',
      behavior:{...Object.fromEntries(Object.keys(fields).map(key=>[key,''])),...item.behavior,...offered.behavior},
      ...(offered.detail_contract||item.detail_contract?{detail_contract:structuredClone(offered.detail_contract||item.detail_contract)}:{}),
      ...(detailSuggestion&&offered.id!==detailSuggestion.id?{detail_suggestion:{contract:structuredClone(detailSuggestion.detail_contract),candidate_id:detailSuggestion.id}}:{}),
      acceptance_items:acceptance(p,item.id).map(ac=>acceptanceDraft(p,ac)),candidate_id:offered.target_item_id?offered.id:undefined}];
  }));
  return {revision:p.revision,values,states:fieldStates,profile,labels:Object.fromEntries([...p.items.map(item=>[item.id,item.title]),...p.questions.map(q=>[q.id,q.question])]),scope:p.product_context?.scope_ids?.length?p.product_context.scope_ids:bulk?(completion.scope_ids||[]):requirements(p).filter(i=>i.selection_status==='selected').map(i=>i.id),scope_excluded_ids:[],rows,
    answers:Object.fromEntries(sheetQuestions(p).map(q=>[q.id,q.answer||completion.answers?.[q.id]?.text||''])),
    answer_provenance:Object.fromEntries(sheetQuestions(p).map(q=>{
      const draft=q.answer?undefined:completion.answers?.[q.id]?.text;
      return [q.id,{saved_answer:q.answer||'',...(typeof draft==='string'?{ai_answer:draft}:{})}];
    })),
    answer_implementations:Object.fromEntries(sheetQuestions(p).map(q=>[q.id,savedImplementations(q)])),
    linked_clauses:Object.fromEntries(linkedClauseItems(p).map(item=>[item.id,{item_id:item.id,statement:item.statement||'',behavior:Object.fromEntries(Object.keys(fields).map(f=>[f,item.behavior?.[f]||'']))}]))};
}

/** Merge each stable acceptance identity, so one edited cell cannot hide new AI rows. */
export function mergeAcceptanceItems(mine:Acceptance[],base:Acceptance[]|undefined,latest:Acceptance[],conflicts:string[],label:string):Acceptance[]{
  const byId=(rows:Acceptance[])=>new Map(rows.filter(a=>a.id).map(a=>[a.id!,a]));
  const local=byId(mine),original=byId(base||[]),remote=byId(latest),result:Acceptance[]=[];
  for(const row of latest){
    const previous=row.id?local.get(row.id):undefined,before=row.id?original.get(row.id):undefined;
    // A row present in the base but removed locally stays removed.
    if(before&&!previous)continue;
    if(!previous){result.push(row);continue;}
    // Classification belongs to the selected revision, not necessarily its base row.
    // This metadata is deliberately excluded from the edit/conflict comparison below.
    const current={...row,ai_suggested:previous.candidate_id?row.candidate_ai_suggested?.[previous.candidate_id]===true:row.ai_suggested===true};
    const unavailable=!!previous.candidate_id&&!row.suggestions?.some(s=>s.candidate_id===previous.candidate_id);
    // A recorded application of this exact candidate is safe to reuse. A merely
    // deferred/rejected/removed candidate must never be relabelled as human text.
    if(unavailable&&row.applied_candidate_ids?.includes(previous.candidate_id!)&&row.statement===previous.statement){result.push(row);continue;}
    const invalidCandidate=unavailable?{id:previous.candidate_id!,latest_statement:row.statement,ai_suggested:row.ai_suggested===true}:undefined;
    const edited=!before||previous.statement!==before.statement||previous.candidate_id!==before.candidate_id;
    const conflict=edited&&previous.statement!==row.statement&&(!before||before.statement!==row.statement);
    if(conflict)conflicts.push(label+'的验收内容在其他位置也已变化，已保留'+(base?'你的修改':'当前草稿')+'；最新内容在验收单元格内对照展示。');
    result.push(edited?{...current,statement:previous.statement,...(previous.candidate_id?{candidate_id:previous.candidate_id}:{}),...(invalidCandidate?{invalid_candidate:invalidCandidate}:{}),...(conflict?{conflict_statement:row.statement}:previous.conflict_statement?{conflict_statement:previous.conflict_statement}:{})}:current);
  }
  for(const row of mine){
    if(!row.id){if(row.statement.trim()||!result.length)result.push({...row,ai_suggested:false});continue;}
    if(remote.has(row.id))continue;
    const before=original.get(row.id);
    if(!before||row.statement!==before.statement||row.candidate_id!==before.candidate_id){
      result.push({...row,ai_suggested:false});
      if(before)conflicts.push(label+'的验收已在其他位置移除，已保留'+(base?'你的本地修改':'当前草稿')+'，请核对。');
    }
  }
  return result;
}
const detailEvidenceAt=(contract:DetailContract|undefined,path:string)=>{
  const parts=path.split('.'),key=parts.pop()!;
  return detailPathValue(contract,parts.join('.'))?.evidence?.[key];
};
export function mergeSuggestions(mine:Sheet,base:Sheet|null,latest:Sheet):Sheet{
  const conflicts:string[]=(mine.merge_conflicts||[]).filter(message=>!message.includes('的详细定义有 '));
  const keep=(local:any,before:any,remote:any,label:string)=>{
    if(equal(local,before)||equal(local,remote))return remote;
    if(!base&&(local===undefined||local===''||local===null))return remote;
    if(!equal(remote,before))conflicts.push(label+'在其他位置也已变化，已保留'+(base?'你的修改':'当前草稿')+'。');
    return local;
  };
  const rows={...latest.rows};
  for(const [id,row] of Object.entries(rows)){
    const previous=mine.rows[id],original=base?.rows[id];if(!previous)continue;
    const label='“'+(latest.labels?.[id]||id)+'”';
    const detail=mergeDetailContract(previous.detail_contract,original?.detail_contract,row.detail_contract);
    const currentDetails=new Map<string,DetailConflict>(detail.conflicts.map(conflict=>[conflict.path,conflict]));
    for(const conflict of previous.detail_conflicts||[]){
      if(currentDetails.has(conflict.path)||conflict.path.includes('.evidence.')||conflict.path.endsWith('.evidence'))continue;
      const local=detailPathValue(detail.value,conflict.path),remote=detailPathValue(row.detail_contract,conflict.path);
      if(equal(local,remote))continue;
      const localEvidence=detailEvidenceAt(detail.value,conflict.path),remoteEvidence=detailEvidenceAt(row.detail_contract,conflict.path);
      currentDetails.set(conflict.path,{path:conflict.path,local:structuredClone(local),remote:structuredClone(remote),
        ...(localEvidence===undefined?{}:{localEvidence:structuredClone(localEvidence)}),
        ...(remoteEvidence===undefined?{}:{remoteEvidence:structuredClone(remoteEvidence)})});
    }
    const detailConflicts=[...currentDetails.values()];
    if(detailConflicts.length)conflicts.push(label+'的详细定义有 '+detailConflicts.length+' 处冲突，请展开详细定义比较并选择；'+(base?'你的修改':'当前草稿')+'已保留。');
    rows[id]={...row,
      statement:keep(previous.statement,original?.statement,row.statement,label+'需求正文'),
      change_type:keep(previous.change_type,original?.change_type,row.change_type,label+'改动性质'),
      behavior:Object.fromEntries(Object.keys(fields).map(f=>[f,keep(previous.behavior[f],original?.behavior[f],row.behavior[f],label+fields[f])])),
      detail_contract:detail.value,detail_conflicts:detailConflicts,
      acceptance_items:mergeAcceptanceItems(previous.acceptance_items,original?.acceptance_items,row.acceptance_items,conflicts,label)};
    if(previous.candidate_id&&rows[id].statement===previous.statement)rows[id].candidate_id=previous.candidate_id;
  }
  const removed=Object.keys(mine.rows).filter(id=>!latest.rows[id]&&base?.rows[id]&&!equal(mine.rows[id],base.rows[id]));
  if(removed.length)conflicts.push('有需求已在其他位置移除；其本地修改保存在上次草稿副本。');
  const linked_clauses={...latest.linked_clauses};
  for(const [id,row] of Object.entries(linked_clauses)){
    const previous=mine.linked_clauses?.[id],original=base?.linked_clauses?.[id];if(!previous)continue;
    const label='“'+(latest.labels?.[id]||id)+'”关联规则';
    linked_clauses[id]={...row,statement:keep(previous.statement,original?.statement,row.statement,label+'正文'),
      behavior:Object.fromEntries(Object.keys(fields).map(f=>[f,keep(previous.behavior[f],original?.behavior[f],row.behavior[f],label+fields[f])||'']))};
  }
  const removedClauses=Object.keys(mine.linked_clauses||{}).filter(id=>!latest.linked_clauses?.[id]&&base?.linked_clauses?.[id]&&!equal(mine.linked_clauses![id],base.linked_clauses[id]));
  if(removedClauses.length)conflicts.push('有关联规则已在其他位置移除；其本地修改保存在上次草稿副本。');
  const mergeAnswer=(q:string)=>{
    const local=mine.answers[q],before=base?.answers[q],remote=latest.answers[q];
    const original=base?.answer_provenance?.[q],source=latest.answer_provenance?.[q];
    const aiOnly=!!original&&!!source&&
      typeof original.saved_answer==='string'&&typeof source.saved_answer==='string'&&
      original.saved_answer===source.saved_answer&&
      typeof source.ai_answer==='string'&&remote===source.ai_answer;
    // Only an exact fresh AI projection with an unchanged saved answer is AI-only.
    if(aiOnly&&!equal(local,before)&&!equal(local,remote))return local||'';
    return keep(local,before,remote,'“'+(latest.labels?.[q]||q)+'”回答')||'';
  };
  const result:Sheet={...latest,rows,linked_clauses,
    profile:keep(mine.profile,base?.profile,latest.profile,'需求类型'),
    scope:(keep(mine.scope,base?.scope,latest.scope,'本期范围')||[]).filter((id:string)=>!!latest.rows[id]),
    scope_excluded_ids:(keep(mine.scope_excluded_ids||[],base?.scope_excluded_ids||[],latest.scope_excluded_ids||[],'暂不纳入本期')||[]).filter((id:string)=>!!latest.rows[id]),
    values:Object.fromEntries(Object.keys(scopeLabels).map(f=>[f,keep(mine.values[f],base?.values[f],latest.values[f],scopeLabels[f])||''])),
    states:Object.fromEntries(Object.keys(scopeLabels).map(f=>[f,keep(mine.states[f],base?.states[f],latest.states[f],scopeLabels[f]+'状态')])),
    answers:Object.fromEntries(Object.keys(latest.answers).map(q=>[q,mergeAnswer(q)])),
    answer_implementations:Object.fromEntries(Object.keys(latest.answers).map(q=>[q,
      equal(mine.answer_implementations?.[q]||[],base?.answer_implementations?.[q]||[])?latest.answer_implementations?.[q]||[]:
      mine.answers[q]===latest.answers[q]||mine.answers[q]!==base?.answers[q]?mine.answer_implementations?.[q]||[]:latest.answer_implementations?.[q]||[]])),
    merge_conflicts:[...new Set(conflicts)]};
  return retainSheetImplementations(result);
}
/** Strip UI-only advice metadata from the explicit confirmation request. */
export const acceptancePayload=(rows:Acceptance[])=>rows.filter(a=>a.statement.trim()).map(a=>({...(a.id?{id:a.id}:{}),statement:a.statement,...(a.candidate_id?{candidate_id:a.candidate_id}:{})}));
export const requirementPayload=(row:Row)=>{const {detail_conflicts,detail_suggestion,...content}=row;return {...content,acceptance_items:acceptancePayload(row.acceptance_items)};};

export const restoreSheet=(saved:Sheet,base:Sheet|null,latest:Sheet)=>mergeSuggestions(saved,base,latest);

/** An explicit local decision for expired advice; final confirmation remains required. */
export function resolveExpiredAcceptance(row:Acceptance,choice:'saved'|'manual'):Acceptance{
  const {candidate_id,invalid_candidate,conflict_statement,...rest}=row;
  return {...rest,statement:choice==='saved'?(invalid_candidate?.latest_statement??row.statement):row.statement,ai_suggested:choice==='saved'?invalid_candidate?.ai_suggested===true:false};
}
