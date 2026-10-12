import type { Blocker, BlockerAction, Project } from './api';
import { scopeLabels } from './scope-draft';
import { canStartDirect, questionPhase } from './workflow-access';

export type BlockerFocus = {action:BlockerAction;nonce:number};
export function stageBlockers(p:Project,phase:number):Blocker[] {
  if(phase===5)return p.confirmation_blockers||[];
  const state=p.product_flow?.[phase];
  if(phase===0&&canStartDirect(p))return [];
  // Intake has a direct-save path: legacy context fields are not four separate
  // chores, and saving literal input also creates its readable source.
  if(phase===0&&state&&!state.complete)return [{key:'save-intake',code:'intake_pending',title:'保存本次需求',
    message:'直接填写并保存已有信息，即可进入草稿核对；也可以先添加资料，再整理当前信息。',
    actions:[{kind:'context',field:'intent',label:'直接填写并保存输入'},{kind:'source',label:'添加或核对资料'}]}];
  const rows=state?.blockers||state?.missing.map((message,n)=>({key:'legacy-'+n,code:'legacy',title:'当前阶段缺项',message,actions:[]}))||[];
  const prior=state&&!state.available&&!(phase===1&&canStartDirect(p))?p.product_flow?.slice(0,phase).find(s=>!s.retired&&!s.complete):undefined;
  return prior?[{key:'prerequisite-'+prior.step,code:'prerequisite_stage',title:prior.title,
    message:'请先完成第 '+prior.display_step+' 步「'+prior.title+'」核对，再继续当前阶段。',
    actions:[{kind:'stage',label:'去完成前序核对',step:prior.step}]},...rows]:rows;
}
export function acceptanceTargets(p:Project, requirementId:string) {
  const req=p.items.find(i=>i.id===requirementId&&i.kind==='requirement');
  if(!req)return [];
  return p.items.filter(i=>i.kind==='acceptance'&&i.applies_to==='to_be'&&i.selection_status==='candidate'&&
    ((i.related_refs||[]).includes(req.id)||(req.related_refs||[]).includes(i.id)));
}
/** Resolve identities from the actual project, never from error-message wording. */
export function blockerDestination(p:Project, action:BlockerAction):{phase:number;itemIds?:string[]} {
  const item=action.item_id?p.items.find(i=>i.id===action.item_id):undefined;
  switch(action.kind){
    case 'context': if(!action.field||!scopeLabels[action.field])break;return {phase:1};
    case 'scope':case 'candidate':return {phase:1};
    case 'requirement':if(!item)break;return {phase:item.kind==='requirement'?1:2};
    case 'add_acceptance':{
      if(!action.item_id)return {phase:p.items.some(i=>i.kind==='requirement'&&i.applies_to==='to_be'&&p.product_context?.scope_ids?.includes(i.id))?2:1};
      if(!item||item.kind!=='requirement'||item.applies_to!=='to_be'||!p.product_context?.scope_ids?.includes(item.id))break;return {phase:2};
    }
    case 'behavior':
      if(!item||item.kind!=='requirement'||!p.product_context?.scope_ids?.includes(item.id))break;
      return {phase:2};
    case 'acceptance':{
      const ids=acceptanceTargets(p,action.item_id||'').map(i=>i.id);
      if(!ids.length)break;return {phase:2,itemIds:ids};
    }
    case 'question':{const q=p.questions.find(q=>q.id===action.question_id&&!q.superseded_by);if(!q)break;return {phase:questionPhase(q)};}
    case 'answer':if(!p.questions.some(q=>q.id===action.question_id&&!q.superseded_by))break;return {phase:2};
    case 'source':return {phase:0};
    case 'document':if(!['mrd','prd'].includes(action.document||''))break;return {phase:4};
    case 'review':return {phase:4};
    case 'stage':if(![1,2,3,5,6].includes(action.step||0))break;return {phase:action.step!-1};
  }
  throw Error('处理位置已变化，请重新载入当前项目后再定位。');
}
export function blockerGroups(rows:Blocker[]) {
  const groups=new Map<string,{key:string;title:string;rows:Blocker[]}>();
  for(const row of rows){
    const identity=row.actions[0]?.item_id||row.actions[0]?.field||row.title;
    const key=row.title+'\u0000'+identity;
    if(!groups.has(key))groups.set(key,{key,title:row.title,rows:[]});
    groups.get(key)!.rows.push(row);
  }
  return Array.from(groups.values());
}
export function focusBlockerElement(id:string,selector?:string) {
  const el=document.getElementById(id);if(!el)return false;
  let parent:HTMLElement|null=el;
  while(parent){if(parent instanceof HTMLDetailsElement)parent.open=true;parent=parent.parentElement;}
  document.querySelectorAll('.blocker-target').forEach(node=>node.classList.remove('blocker-target'));
  el.classList.add('blocker-target');
  const target=(selector?el.querySelector<HTMLElement>(selector):null)||el;
  if(!['INPUT','SELECT','TEXTAREA','BUTTON','A','SUMMARY'].includes(target.tagName)&&!target.hasAttribute('tabindex'))target.tabIndex=-1;
  target.scrollIntoView({block:'center'});target.focus({preventScroll:true});
  return true;
}
