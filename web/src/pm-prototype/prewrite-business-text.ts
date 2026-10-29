import type {Project,JsonObject as Obj} from '../api';
import {detailPropertyLabels,detailRows} from './detail-contract';
import {requirementBehaviorLabels} from './requirement-fields';

export function businessText(project:Project,text:string){
  return String(text||'').replace(/\b(?:REQ|AC|Q|RULE|SRC|EX)-[A-Za-z0-9_-]+\b/g,id=>{
    const item=project.items.find(i=>i.id===id);
    if(item)return '「'+(item.title||'相关条款')+'」';
    if(id.startsWith('Q-'))return '已保存的业务回答';
    const source=project.sources.find(s=>s.id===id);
    return source?'「'+(source.name||source.filename||'来源资料')+'」':'所引用资料';
  }).replace(/\b(?:behavior\.)?(actor|entry|flow|data|permissions|result|exceptions)\b/g,(all,key)=>requirementBehaviorLabels[key as keyof typeof requirementBehaviorLabels]||all)
    .replace(/product_context\.scope_ids/g,'本期范围').replace(/\bstatement\b/g,'需求正文').replace(/\bscope_ids\b/g,'本期范围');
}

export function issueLocation(project:Project,sheet:Obj,issue:Obj){
  const item=project.items.find(i=>i.id===issue.item_id),contract=sheet.rows[issue.item_id]?.detail_contract;
  const match=issue.path?.match(/^detail_contract\.(fields|interactions|state_transitions|flow\.nodes|flow\.edges)\.([^.]+)\.([^.]+)$/);
  if(match){
    const object=detailRows(contract,match[1]).find(row=>row.id===match[2]);
    return {label:[item?.title||'需求内容',object?.name||object?.label||'业务定义',detailPropertyLabels[match[3]]||'待补内容'].join(' · '),section:match[1],object,property:match[3]};
  }
  return {label:item?.title||'待补内容',section:'',object:undefined,property:''};
}
