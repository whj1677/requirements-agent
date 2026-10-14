import React from 'react';
import { detailPropertyLabels, detailRows, detailSectionLabels, type DetailContract } from './detail-contract';

type Change = { path:string; label:string; before:unknown; after:unknown };
const same=(a:unknown,b:unknown)=>JSON.stringify(a)===JSON.stringify(b);
const display=(value:unknown):string=>value===undefined?'未设置':typeof value==='string'?value:typeof value==='boolean'?(value?'是':'否'):Array.isArray(value)?value.map(display).join('、'):JSON.stringify(value);

export function detailContractChanges(before?:DetailContract,after?:DetailContract):Change[]{
  if(same(before,after))return [];
  const changes:Change[]=[];
  for(const section of ['fields','interactions','flow.nodes','flow.edges','state_transitions']){
    const title=section==='flow.nodes'?'流程步骤':section==='flow.edges'?'流程分支':detailSectionLabels[section as keyof typeof detailSectionLabels];
    const oldRows=detailRows(before,section),newRows=detailRows(after,section);
    for(const id of new Set([...oldRows,...newRows].map(row=>row.id))){
      const old=oldRows.find(row=>row.id===id),next=newRows.find(row=>row.id===id);
      for(const key of new Set([...Object.keys(old||{}),...Object.keys(next||{})])){
        if(key==='id'||same(old?.[key],next?.[key]))continue;
        changes.push({path:section+'.'+id+'.'+key,label:title+' · '+(next?.name||next?.label||old?.name||old?.label||id)+' · '+(key==='evidence'?'内容依据':detailPropertyLabels[key]||key),before:old?.[key],after:next?.[key]});
      }
    }
  }
  for(const key of ['schema_version','applicability','depends_on'] as const)if(!same(before?.[key],after?.[key]))changes.push({path:key,label:({schema_version:'详细定义版本',applicability:'适用范围与不适用原因',depends_on:'依赖的需求'})[key],before:before?.[key],after:after?.[key]});
  return changes;
}

export function DetailContractChanges({before,after}:{before?:DetailContract;after?:DetailContract}){
  const changes=detailContractChanges(before,after);
  if(!changes.length)return null;
  return <section className="answer-detail-changes" aria-label="详细定义修改前后"><h5>字段、交互、流程与状态的关联修改 · {changes.length} 处</h5>
    <div className="prewrite-table-scroll"><table><thead><tr><th scope="col">修改位置</th><th scope="col">修改前</th><th scope="col">确认后</th></tr></thead><tbody>{changes.map(change=><tr key={change.path}><th scope="row">{change.label}</th><td>{display(change.before)}</td><td>{display(change.after)}</td></tr>)}</tbody></table></div>
  </section>;
}
