import type {Project} from '../api';
import {scopeLabels} from '../scope-draft';
import {requirementBehaviorLabels} from './requirement-fields';
import {acceptancePayload,initial,linkedClausePayload,type Sheet} from './refinement-sheet-state';

type Obj=Record<string,any>;
export type RefinementReturnState={projectId:string;revision:number;canReturn:boolean;pendingAnswerCount?:number;missingCount?:number};
const behavior=(value:Obj)=>Object.fromEntries(Object.keys(requirementBehaviorLabels).map(key=>[key,value[key]||'']));
const ordered=(value:any):any=>Array.isArray(value)?value.map(ordered):value&&typeof value==='object'
  ?Object.fromEntries(Object.keys(value).sort().map(key=>[key,ordered(value[key])])):value;

/** Compare editable content, independently of saving a browser draft or UI hints. */
export function refinementContent(sheet:Sheet){
  return JSON.stringify(ordered({
    values:Object.fromEntries(Object.keys(scopeLabels).map(key=>[key,sheet.values[key]||''])),
    states:Object.fromEntries(Object.keys(scopeLabels).map(key=>[key,sheet.states[key]||{status:'unknown'}])),
    profile:sheet.profile,
    scope:[...sheet.scope].sort(),
    rows:Object.keys(sheet.rows).sort().map(id=>{const row=sheet.rows[id];return {
      requirement_id:id,statement:row.statement,change_type:row.change_type,behavior:behavior(row.behavior),
      acceptance_items:acceptancePayload(row.acceptance_items),candidate_id:row.candidate_id||null,
      detail_contract:row.detail_contract||null,
    };}),
    answers:Object.keys(sheet.answers).sort().map(id=>[id,sheet.answers[id]]),
    linked_clauses:Object.keys(sheet.linked_clauses||{}).sort().map(id=>{const row=sheet.linked_clauses![id];return {item_id:id,statement:row.statement,behavior:behavior(row.behavior)};}),
  }));
}

/** Navigation reuses human review only for the exact persisted table content. */
export function canReturnToDelivery(project:Project,sheet:Sheet|null,states:Obj[],loaded:number,stale=false){
  if(!sheet||stale||sheet.merge_conflicts?.length||loaded!==project.revision||sheet.revision!==project.revision||!sheet.scope.length)return false;
  if(sheet.scope.some(id=>!!sheet.rows[id]?.detail_conflicts?.length))return false;
  // complete also includes unrelated blockers. Compare the original checkpoint
  // identities here; the delivery screen continues to present its own blockers.
  const checks=(project as Obj).stage_checks||{};
  if(![1,2,3].every(step=>{
    const check=checks[String(step)],current=project.product_flow?.find(row=>row.step===step);
    return check?.actor==='authenticated_local_user'&&!!current?.content_hash&&check.content_hash===current.content_hash;
  }))return false;
  if(!sheet.scope.every(id=>states.find(row=>row.requirement_id===id)?.current===true))return false;
  const fresh=initial(project,states);
  if(refinementContent(sheet)!==refinementContent(fresh))return false;
  // initial() can fill blank cells with AI proposals even when the saved
  // requirement's fingerprint remains current. Those cells still need review.
  if(Object.keys(scopeLabels).some(key=>(sheet.values[key]||'')!==(project.product_context?.[key]||'')))return false;
  if(sheet.scope.some(id=>{
    const row=sheet.rows[id],item=project.items.find(item=>item.id===id);
    return !row||item?.selection_status!=='selected'||!!row.candidate_id||row.acceptance_items.some(ac=>
      !!ac.candidate_id||!!ac.invalid_candidate||!!ac.ai_suggested||!ac.id||project.items.find(item=>item.id===ac.id)?.selection_status!=='selected');
  }))return false;
  return linkedClausePayload(project,sheet).every(row=>project.items.find(item=>item.id===row.item_id)?.selection_status==='selected');
}
