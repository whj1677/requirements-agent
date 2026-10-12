import React from 'react';
import { JsonObject, Project } from './api';

export function BenchmarkReport({p,onSource,onItem}:{p:Project;onSource:(refs:JsonObject[])=>void;onItem:(id:string)=>void}){
  const report=p.benchmark_analysis;
  if(!report)return null;
  const stale=report.source_versions?.some((version:JsonObject)=>{const source=p.sources.find(s=>s.id===version.id);return !source||source.excluded||source.version!==version.version||source.sha256!==version.sha256;});
  return <article className="card benchmark-report" aria-label="对标分析结果"><h2>对标参考 · {report.request?.target}</h2>
    <p>{report.summary}</p><p className="muted">以下借鉴内容是待审核建议。对应需求、行为和验收已进入草稿候选，由你修改、采纳。</p>
    {report.request?.focus&&<p>参考范围：{report.request.focus}</p>}
    {stale&&<p className="notice">参考资料已变化，以下保留的是此前对标记录。请重新分析后核对当前草稿。</p>}
    {(report.limitations||[]).map((text:string,n:number)=><p className="notice" key={n}>{text}</p>)}
    {!!report.observations?.length&&<div className="benchmark-table"><table><thead><tr><th>参考内容与出处</th><th>与当前需求的差异</th><th>可借鉴建议</th></tr></thead><tbody>{report.observations.map((row:JsonObject,n:number)=>{
      const source=p.sources.find(s=>s.id===row.source_ref.source_id);
      const candidates=(row.proposal_refs||[]).map((id:string)=>p.items.find(i=>i.id===id)).filter(Boolean);
      return <tr key={n}><td><strong>{row.title}</strong><p>{source?.title||'历史参考资料'}</p><details><summary>{row.evidence_kind==='visual_observation'?'查看视觉观察（待人工核对）':'查看参考原文'}</summary><blockquote>{row.reference_quote}</blockquote><button type="button" onClick={()=>onSource([row.source_ref])}>定位资料原文</button></details></td>
        <td>{row.difference||'当前资料不足，差异待核对'}</td><td><p>{row.suggested_application||'是否借鉴待决定'}</p>{candidates.map((item:JsonObject)=><button type="button" key={item.id} onClick={()=>onItem(item.id)}>核对候选：{item.title}</button>)}</td></tr>;
    })}</tbody></table></div>}
  </article>;
}
