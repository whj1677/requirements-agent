import React,{useState} from 'react';
import type {Project,JsonObject as Obj} from '../api';
import type {Sheet} from './refinement-sheet-state';
import {scopeLabels} from '../scope-draft';
import {detailRows} from './detail-contract';
import {issueLocation,businessText} from './prewrite-business-text';

export function BatchMissingReview({project,sheet,issues,pendingIssues,disabled,onChange,onDraft,onClose}:{project:Project;sheet:Sheet;issues:Obj[];pendingIssues:Obj[];disabled:boolean;onChange:(s:Sheet)=>void;onDraft:()=>void;onClose:()=>void}){
  const [textValues,setTextValues]=useState<Record<string,string>>({});
  function detail(issue:Obj,text:string){
    const {section,object,property}=issueLocation(project,sheet,issue);if(!object)return;
    const row=sheet.rows[issue.item_id],contract=row.detail_contract!;
    const rows=detailRows(contract,section).map(o=>o.id===object.id?{...o,[property]:property==='enum_values'?text.split(/[\n，,；;]/).map(s=>s.trim()).filter(Boolean):text,
      evidence:{...o.evidence,[property]:{status:'proposal' as const,reason:'人工补全，等待整表确认'}}}:o);
    const next=section.startsWith('flow.')?{...contract,flow:{...contract.flow,[section.slice(5)]:rows}}:{...contract,[section]:rows};
    onChange({...sheet,rows:{...sheet.rows,[issue.item_id]:{...row,detail_contract:next}}});
  }
  return <section className="batch-missing-review" aria-label="本表待补内容">
    <header><strong>{pendingIssues.length?'还需补齐 '+pendingIssues.length+' 项':'缺项已补齐，请统一确认'}</strong><button type="button" disabled={disabled||!pendingIssues.length} onClick={onDraft}>让 AI 一次补齐这些缺项</button><button type="button" onClick={onClose}>返回整表</button></header>
    <p>已填内容保留，下面只显示本次卡住的内容。填好后仍点击下方统一确认按钮。</p>
    <div className="batch-missing-fields">
    {issues.map((issue,n)=>{const loc=issueLocation(project,sheet,issue),row=sheet.rows[issue.item_id];const label=issue.kind==='context'?scopeLabels[issue.field]:issue.kind==='question'?'业务确认回答':loc.label;
      let editor:React.ReactNode;
      if(loc.object){const value=loc.object[loc.property],key=issue.item_id+'-'+issue.path;editor=<textarea autoFocus={n===0} rows={2} aria-label={label} value={textValues[key]??(Array.isArray(value)?value.join('\n'):value||'')} placeholder={loc.property==='enum_values'?'填写实际可选值，用逗号或换行分隔；未提供的厂家名称不要编造。':'填写这项业务说明'} disabled={disabled} onChange={e=>{setTextValues({...textValues,[key]:e.target.value});detail(issue,e.target.value);}}/>;}
      else if(issue.kind==='context')editor=<textarea rows={2} aria-label={label} disabled={disabled} value={sheet.values[issue.field]||''} onChange={e=>onChange({...sheet,values:{...sheet.values,[issue.field]:e.target.value},states:{...sheet.states,[issue.field]:{...sheet.states[issue.field],status:e.target.value.trim()?'known':'unknown'}}})}/>;
      else if(issue.kind==='question')editor=<textarea rows={2} aria-label={label} disabled={disabled} value={sheet.answers[issue.question_id]||''} onChange={e=>onChange({...sheet,answers:{...sheet.answers,[issue.question_id]:e.target.value}})}/>;
      else if(row&&issue.field==='change_type')editor=<select aria-label={label+' 本次性质'} disabled={disabled} value={row.change_type} onChange={e=>onChange({...sheet,rows:{...sheet.rows,[issue.item_id]:{...row,change_type:e.target.value}}})}><option value="unspecified">请选择本次性质</option><option value="new">新增功能</option><option value="modified">修改已有功能</option><option value="preserved">保持已有功能</option></select>;
      else if(row&&issue.field==='statement')editor=<textarea rows={2} aria-label={label} disabled={disabled} value={row.statement} onChange={e=>onChange({...sheet,rows:{...sheet.rows,[issue.item_id]:{...row,statement:e.target.value}}})}/>;
      const pending=pendingIssues.some(p=>p.item_id===issue.item_id&&p.question_id===issue.question_id&&p.path===issue.path&&p.field===issue.field);
      return <div className={'batch-missing-field'+(pending?'':' is-complete')} key={[issue.item_id,issue.question_id,issue.path,issue.field,n].join('-')}><label><b>{label}</b><span>{pending?businessText(project,issue.message):'已补齐，确认后统一保存。'}</span>{editor||<p>请在本表对应条款中核对，或使用上方 AI 补齐按钮。</p>}</label></div>;
    })}</div>
  </section>;
}
