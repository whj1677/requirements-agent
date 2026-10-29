import type { BlockerAction, Project } from '../api';
import { blockerDestination } from '../blocker-navigation';

export type ConnectedDestination = { step: 'information'|'refine'|'delivery'; itemId?:string; questionId?:string; field?:string; scope?:boolean; review?:boolean; task?:boolean; prewrite?:boolean; findingId?:string; nonce:number };

/** Everyday editing has one destination, including links from formal documents. */
export function refinementDestination(project:Project, action:BlockerAction):Omit<ConnectedDestination,'nonce'>|null {
  if(action.kind==='prewrite')return {step:'refine',prewrite:true};
  if(action.kind==='source'||action.kind==='document'||action.kind==='review')return null;
  const resolved=blockerDestination(project,action.kind==='item'?{...action,kind:'requirement'}:action);
  if(action.kind==='stage')return resolved.phase>2?null:{step:resolved.phase===0?'information':'refine',review:resolved.phase>0};
  return {step:'refine',field:action.kind==='context'?action.field:action.kind==='answer'?'implementation':undefined,
    itemId:resolved.itemIds?.[0]||action.item_id,questionId:action.question_id,scope:action.kind==='scope'};
}
