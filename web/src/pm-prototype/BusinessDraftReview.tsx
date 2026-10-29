import React from 'react';
import { requirementBehaviorLabels } from './requirement-fields';

type Obj = Record<string, any>;
const basisLabel: Record<string,string> = {
  fact:'引用资料整理 · 待核对', recommendation:'AI 推荐 · 待确认', unknown:'资料未明确 · 待决定',
};

export function latestDraftReview(project:Obj, requirementId?:string):Obj|undefined {
  return [...(project.business_draft_reviews||[])].reverse().find((review:Obj)=>
    !requirementId || [...(review.accepted||[]),...(review.rejected||[])].some((row:Obj)=>row.requirement_id===requirementId));
}

export function DraftPackageStatus({project,onLocate,onCreate}:{project:Obj;onLocate:(id:string)=>void;onCreate:()=>void}) {
  const review=latestDraftReview(project);
  if(!review)return null;
  const rejected:Obj[]=review.rejected||[], summaryRejected:Obj[]=review.summary_rejected||[], questionRejected:Obj[]=review.question_rejected||[];
  const fieldRejected:Obj[]=review.field_rejected||[];
  const completion=review.completion;
  const structureRejected:Obj[]=(review.structure_rejected||[]).filter((row:Obj)=>JSON.stringify(row.path)!=='["questions"]');
  const history:Obj[]=(project.business_draft_reviews||[]).filter((r:Obj)=>r!==review).flatMap((r:Obj)=>(r.rejected||[]).filter((row:Obj)=>!row.requirement_id));
  return <section className="table-callout business-draft-status" aria-label="AI 草稿整理结果">
    <strong>最近一次 AI 整理：{review.accepted?.length||0} 条草稿可核对{rejected.length?`，${rejected.length} 条未载入`:''}</strong>
    <p>资料引用已检查；内容是否准确、建议是否采用，仍由你确认。</p>
    {completion&&<p>已起草 {Object.keys(completion.answers||{}).length} 道问题的回答，直接填入表格草稿。{completion.missing_answer_ids?.length?`仍有 ${completion.missing_answer_ids.length} 道未生成可用回答，原有内容保留。`:''}</p>}
    {!!completion?.rejected?.length&&<details><summary>查看未载入回答的原因</summary>{completion.rejected.map((row:Obj,n:number)=><p key={n}>{row.reason}</p>)}</details>}
    {!!rejected.length&&<details><summary>查看未载入条目与处理入口</summary>{rejected.map((row,n)=><div key={n} className="business-draft-rejected">
      <strong>{typeof row.raw?.title==='string'?row.raw.title:'未能识别的需求条目'}</strong><p>{row.reason}</p>
      {row.requirement_id?<button type="button" onClick={()=>onLocate(row.requirement_id)}>到这条需求处理</button>:<button type="button" onClick={onCreate}>补充需求</button>}
      <details><summary>查看未载入的 AI 原稿</summary><pre>{JSON.stringify(row.raw,null,2)}</pre></details>
    </div>)}</details>}
    {!!summaryRejected.length&&<p>另有 {summaryRejected.length} 项项目摘要未载入：{summaryRejected.map(row=>row.reason).join('；')}。可在项目摘要中核对。</p>}
    {!!fieldRejected.length&&<details><summary>{fieldRejected.length} 个字段未载入，已有内容保留</summary>{fieldRejected.map((row,n)=><div className="business-draft-rejected" key={n}><p>{row.path?.at(-1)==='detail_contract'?'新详细定义未通过校验；已有详细定义保留，未用错误内容替换。':row.reason}</p><small>{row.reason}</small>{row.requirement_id&&<button type="button" onClick={()=>onLocate(row.requirement_id)}>到相关需求处理</button>}<details><summary>查看未载入字段的原稿</summary><pre>{JSON.stringify(row.raw,null,2)}</pre></details></div>)}</details>}
    {!!questionRejected.length&&<details><summary>{questionRejected.length} 个问题需要重新整理，其他有效草稿已保留</summary>{questionRejected.map((row,n)=><div className="business-draft-rejected" key={n}><p>{typeof row.raw?.text==='string'?row.raw.text:typeof row.raw?.question==='string'?row.raw.question:'未能读取的问题'}</p><small>{row.reason}</small>{row.requirement_id?<button type="button" onClick={()=>onLocate(row.requirement_id)}>到相关需求处理</button>:<p>尚未关联到需求，不代表问题已解决。请对照原稿核对。</p>}<details><summary>查看这条问题的原稿</summary><pre>{JSON.stringify(row.raw,null,2)}</pre></details></div>)}</details>}
    {!!structureRejected.length&&<details><summary>{structureRejected.length} 处格式差异已单独保留</summary><p>有效需求可继续核对，以下内容尚未作为需求或业务决定写入。</p>{structureRejected.map((row,n)=><div className="business-draft-rejected" key={n}><small>{row.reason}</small>{row.requirement_id&&<button type="button" onClick={()=>onLocate(row.requirement_id)}>到相关需求处理</button>}<pre>{JSON.stringify(row.raw,null,2)}</pre></div>)}</details>}
    {!!history.length&&<details><summary>此前未载入的新需求记录 · {history.length} 条</summary><p>历史记录保留供核对；后续已补充的内容无需重复处理。</p>{history.map((row,n)=><div className="business-draft-rejected" key={n}><strong>{typeof row.raw?.title==='string'?row.raw.title:'未能识别的需求条目'}</strong><p>{row.reason}</p><details><summary>查看该次 AI 原稿</summary><pre>{JSON.stringify(row.raw,null,2)}</pre></details><button type="button" onClick={onCreate}>补充需求</button></div>)}</details>}
  </section>;
}

export function DraftRowReview({project,requirementId,candidateId,onRetry,onSources,disabled}:{project:Obj;requirementId:string;candidateId?:string;onRetry:()=>void;onSources:(refs:Obj[])=>void;disabled:boolean}) {
  const review=latestDraftReview(project,requirementId);
  if(!review)return null;
  const rejected=(review.rejected||[]).find((row:Obj)=>row.requirement_id===requirementId);
  const questionRejected=(review.question_rejected||[]).filter((row:Obj)=>row.requirement_id===requirementId);
  const fieldRejected=(review.field_rejected||[]).filter((row:Obj)=>row.requirement_id===requirementId);
  const row=candidateId?[...(project.business_draft_reviews||[])].reverse().flatMap((r:Obj)=>r.accepted||[]).find((r:Obj)=>r.requirement_id===requirementId&&r.candidate_temp_id===candidateId):undefined;
  const evidence=row?.field_evidence;
  const entries:[string,Obj][] = evidence ? [
    ['需求内容',evidence.statement],
    ...Object.entries(requirementBehaviorLabels).map(([key,label])=>[label,evidence.behavior?.[key]] as [string,Obj]),
    ...(evidence.acceptance||[]).map((value:Obj,n:number)=>['验收建议 '+(n+1),value] as [string,Obj]),
  ].filter((entry):entry is [string,Obj]=>!!entry[1]) : [];
  return <div className="business-draft-row-review">
    {rejected&&<div className="table-callout"><strong>本条新建议未载入，已有内容保留</strong><p>{rejected.reason}</p><button type="button" disabled={disabled} onClick={onRetry}>只重新整理这条需求</button></div>}
    {!!fieldRejected.length&&<div className="table-callout"><strong>{fieldRejected.length} 个新字段未载入，已有内容保留</strong>{fieldRejected.map((row:Obj,n:number)=><p key={n}>{row.path?.at(-1)==='detail_contract'?'新详细定义未通过校验，已有详细定义保留；可先核对本次有效的正文和验收建议。':row.reason}</p>)}<button type="button" disabled={disabled} onClick={onRetry}>让 AI 调整未载入字段</button></div>}
    {!!questionRejected.length&&<div className="table-callout"><strong>需求草稿已保留，{questionRejected.length} 个问题待重新整理</strong>{questionRejected.map((q:Obj,n:number)=><p key={n}>{typeof q.raw?.text==='string'?q.raw.text:q.reason}</p>)}<button type="button" disabled={disabled} onClick={onRetry}>让 AI 调整本条问题与建议</button></div>}
    {!!entries.length&&<details><summary>核对本版 AI 草稿的依据与待定内容</summary><p>以下是 AI 原稿的标注。引用存在不代表结论已经正确；修改或确认前请对照来源。</p>
      {entries.map(([label,value],n)=><div className="business-draft-evidence" key={n}><strong>{label}</strong><span>{basisLabel[value.basis]||'待核对'}</span><p>{value.text||'尚未明确'}</p>{value.source_relationship==='context_only'&&<small>以下来源只说明需求背景，不代表资料已规定这条建议。</small>}{!!value.source_refs?.length&&<button type="button" onClick={()=>onSources(value.source_refs)}>{value.source_relationship==='context_only'?'查看背景来源':'查看这项来源'}</button>}</div>)}
    </details>}
  </div>;
}
