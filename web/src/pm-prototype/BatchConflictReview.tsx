import React from 'react';
import type {Project} from '../api';
import type {Sheet} from './refinement-sheet-state';
import {businessText} from './prewrite-business-text';
import {batchConflicts,resolveBatchConflict} from './prewrite-conflicts';
import {detailPropertyLabels,detailSectionLabels} from './detail-contract';

export function BatchConflictReview({project,sheet,fresh,disabled,onChange,onClose}:{project:Project;sheet:Sheet;fresh:Sheet|null;disabled:boolean;onChange:(value:Sheet)=>void;onClose:()=>void}){
  const entries=batchConflicts(project,sheet,fresh);
  function describe(value:any):string{
    if(value===undefined)return '这一版已移除此内容';
    if(typeof value==='string')return businessText(project,({known:'已明确',unknown:'尚未明确',not_applicable:'不适用',new:'新增功能',modified:'修改已有功能',preserved:'保持已有功能'} as Record<string,string>)[value]||value);
    if(Array.isArray(value))return value.map(describe).join('\n');
    if(value&&typeof value==='object')return Object.entries(value).filter(([key])=>!['id','evidence','source_refs'].includes(key)).map(([key,child])=>(detailPropertyLabels[key]||(detailSectionLabels as any)[key]||({status:'填写状态',reason:'说明',scenario:'需求类型',dependencies:'关联情况'} as any)[key]||'业务内容')+'：'+describe(child)).join('\n');
    return String(value??'');
  }
  return <section className="batch-conflict-review" aria-label="比较并处理冲突"><header><strong>{entries.length} 处修改需要你选择</strong><button type="button" onClick={onClose}>返回整表</button></header><p>自动保留了你的修改。逐项选择后再统一保存；这里不会调用模型。</p>
    {entries.map(entry=><article key={entry.key}><h4>{businessText(project,entry.label)}</h4><div className="batch-conflict-values"><div><b>我的修改</b><p>{describe(entry.local)}</p><button type="button" disabled={disabled} onClick={()=>onChange(resolveBatchConflict(project,sheet,entry,false,fresh))}>保留我的修改</button></div><div><b>AI 新建议</b><p>{describe(entry.remote)}</p><button type="button" disabled={disabled} onClick={()=>onChange(resolveBatchConflict(project,sheet,entry,true,fresh))}>采用 AI 建议</button></div></div></article>)}
    {!entries.length&&(sheet.merge_conflicts?.length?<div><p>{businessText(project,sheet.merge_conflicts.join('；'))}</p><button type="button" disabled={disabled} onClick={()=>onChange({...sheet,merge_conflicts:[]})}>已核对，保留当前表格内容</button></div>:<p role="status">已完成逐项选择，请点击下方“确认全部修改并复检”。</p>)}
  </section>;
}
