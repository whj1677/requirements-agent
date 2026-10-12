import type { Project } from '../api';

type Item = Project['items'][number];
type Location = {itemId?:string;questionId?:string};
const currentItem = (item:Item) => item.applies_to==='to_be'&&!item.superseded_by&&
  !['rejected','deferred','excluded'].includes(item.selection_status);
const currentQuestion = (question:Item) => !question.superseded_by&&question.kind!=='parent'&&
  !['deferred','out_of_scope'].includes(question.status)&&!question.out_of_scope_reason;
const refs = (value:unknown):string[] => Array.isArray(value)?value.filter((id):id is string=>typeof id==='string'):[];

/** Resolve only one visible table row. Shared or stale links remain in their
 * own review context; selecting the first related requirement would hide work. */
export function singleRequirementRoot(project:Project, location:Location):string|undefined {
  const byId=new Map(project.items.map(item=>[item.id,item]));
  function lineage(id:string):Item[]|undefined {
    const chain:Item[]=[],visited=new Set<string>();
    let item=byId.get(id);
    if(!item)return;
    const kind=item.kind;
    while(item){
      if(visited.has(item.id)||!currentItem(item)||item.kind!==kind)return;
      visited.add(item.id);chain.push(item);
      if(!item.target_item_id)return chain;
      item=byId.get(item.target_item_id);
    }
  }
  function addRequirement(result:Set<string>, id:string) {
    const chain=lineage(id), root=chain?.at(-1);
    if(root?.kind==='requirement')result.add(root.id);
  }
  function itemRoots(id:string):Set<string> {
    const result=new Set<string>(),chain=lineage(id);
    if(!chain)return result;
    const root=chain.at(-1)!;
    if(root.kind==='requirement'){result.add(root.id);return result;}
    const linkedIds=new Set(chain.map(item=>item.id));
    for(const item of chain)for(const ref of refs(item.related_refs))addRequirement(result,ref);
    // Some existing acceptance clauses are linked only from the requirement.
    for(const item of project.items){
      if(item.kind==='requirement'&&refs(item.related_refs).some(ref=>linkedIds.has(ref)))
        addRequirement(result,item.id);
    }
    return result;
  }
  let result:Set<string>|undefined;
  if(location.itemId){
    result=itemRoots(location.itemId);
    if(result.size!==1)return;
  }
  if(location.questionId){
    const question=project.questions.find(q=>q.id===location.questionId&&currentQuestion(q));
    if(!question)return;
    const questions=question.decision_group_id
      ?project.questions.filter(q=>q.decision_group_id===question.decision_group_id&&currentQuestion(q)):[question];
    const questionIds=new Set(questions.map(q=>q.id)), related=new Set<string>();
    for(const q of questions){
      for(const id of refs(q.related_refs))for(const root of itemRoots(id))related.add(root);
      for(const affected of Array.isArray(q.affected_requirements)?q.affected_requirements:[])
        if(typeof affected?.id==='string')for(const root of itemRoots(affected.id))related.add(root);
    }
    for(const item of project.items){
      if(refs(item.answer_refs).some(id=>questionIds.has(id)))
        for(const root of itemRoots(item.id))related.add(root);
    }
    if(related.size!==1||result&&[...related][0]!==[...result][0])return;
    result=related;
  }
  return result?.size===1?[...result][0]:undefined;
}
