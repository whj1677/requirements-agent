import React, { useEffect, useState } from 'react';
import { Project, JsonObject as Obj } from './api';

const intake: Obj = {product:'现有产品',module:'相关页面或模块',intent:'这次希望改什么'};
const scope: Obj = {current_state:'目前页面与流程（区分可见、陈述与推断）',users:'目标使用者',value:'问题与改动价值',change_scope:'本期新增或修改',preserve_scope:'保持不变',out_of_scope:'暂不涉及',priority:'优先级与依据'};
const behavior: Obj = {actor:'谁操作',entry:'入口及前置条件',flow:'主要流程',data:'业务数据',permissions:'权限约束',result:'操作结果',exceptions:'边界和异常'};
type Props={p:Project;phase:number;busy:boolean;mutate:(path:string,body:Obj)=>Promise<unknown>;go:(n:number)=>void};

export function ProductCheckpoint({p,phase,busy,mutate,go}:Props) {
  const [values,setValues]=useState<Obj>({}),[ids,setIds]=useState<string[]>([]),[dirty,setDirty]=useState(false),[error,setError]=useState('');
  const state=p.product_flow?.[phase];
  useEffect(()=>{if(dirty)return; setValues(phase===3 ? (p.sketch_review || {applicable:true}) : {...p.product_context_proposal,...p.product_context});setIds(p.product_context?.scope_ids || []);},[p,phase,dirty]);
  const fields=phase===0?intake:phase===1?scope:phase===3 ? (values.applicable===false?{reason:'本次无界面变化的原因'}:{changes:'新增或修改区域',preserved:'原有保持部分',behavior:'入口、操作、结果与重要未决项'}):{};
  const edit=(key:string,value:unknown)=>{setValues(v=>({...v,[key]:value}));setDirty(true);};
  async function save(){try{setError('');await mutate(phase===3?'/sketch-review':'/product-context',phase===3?{applicable:values.applicable!==false,reason:values.reason||'',changes:values.changes||'',preserved:values.preserved||'',behavior:values.behavior||''}:{values:Object.fromEntries(Object.keys(fields).map(k=>[k,values[k]||''])),...(phase===1?{scope_ids:ids}:{})});setDirty(false);}catch(e){setError((e as Error).message);}}
  async function check(){try{setError('');await mutate('/stage-checks/'+(phase+1),{expected_hash:state?.content_hash});go(phase+1);}catch(e){setError((e as Error).message);}}
  if(phase===5)return null;
  return <article className="card checkpoint"><div className="section-heading"><h3>本步核对</h3><span>{dirty?'尚未保存':state?.complete?'已核对':state?.needs_recheck?'内容变化，需重新核对':'待核对'}</span></div>
    {!state && <p role="alert">阶段状态未取得，不能提交核对。已有内容仍可查看。</p>}
    {phase<2 && <p>助手建议可直接修改；空缺保留待定。保存说明不等于正式批准。</p>}
    {phase===3 && <><p>核对的是功能表达，不是最终配色或控件位置。静态草图即可；业务规则仍须遵守。</p><label><input type="checkbox" checked={values.applicable!==false} onChange={e=>edit('applicable',e.target.checked)}/>本次涉及界面或交互变化</label></>}
    {Object.entries(fields).map(([key,label])=><label key={key}>{String(label)}<textarea rows={2} value={values[key]||''} onChange={e=>edit(key,e.target.value)}/></label>)}
    {phase===1 && <fieldset><legend>本期独立需求（选择范围，不会自动采纳）</legend>{p.items.filter(i=>i.kind==='requirement'&&i.applies_to==='to_be').map(i=><label key={i.id}><input type="checkbox" checked={ids.includes(i.id)} onChange={e=>{setIds(e.target.checked?[...ids,i.id]:ids.filter(id=>id!==i.id));setDirty(true);}}/>{i.id}｜{i.title} · v{i.content_version||1}</label>)}{!p.items.some(i=>i.kind==='requirement')&&<p>先整理材料形成候选需求；背景或目标不能替代需求。</p>}</fieldset>}
    {phase===1 && p.product_context && <details><summary>业务范围讨论摘要 · MRD 的早期依据</summary><p>这是已保存核对内容的整理，尚非完整 MRD 评审稿。</p>{Object.entries(scope).map(([k,label])=><p key={k}><b>{String(label)}：</b>{p.product_context?.[k]||'待核对'}</p>)}<p>需求：{p.product_context.scope_ids?.join('、')||'尚未确定'}</p></details>}
    {phase===2 && <><p>逐条核对下面的行为与验收。没有答案时保留空缺，不用通用描述代替具体规则。</p>{p.items.filter(i=>i.kind==='requirement'&&p.product_context?.scope_ids?.includes(i.id)).map(i=><BehaviorEditor key={i.id} item={i} mutate={mutate} busy={busy}/>)}
      {p.questions.filter(q=>q.status!=='answered').map(q=><QuestionScope key={q.id} question={q} mutate={mutate}/>)}<RequirementRelations p={p} mutate={mutate}/></>}
    {phase===4 && <><p>默认同时交付 MRD 与 PRD，引用同一需求版本。切换查看不会调用模型。</p><div className="document-pair">{['mrd','prd'].map(k=><div key={k}><strong>{k.toUpperCase()}</strong><p>{p.documents[k]?`文档 v${p.documents[k].document_version||'旧版未记录'} · 底稿 v${p.documents[k].draft_revision}`:'尚未生成'}</p><p>{p.document_export_readiness?.[k]?.ready?'当前可导出':'待更新或澄清，详见文档区'}</p></div>)}</div></>}
    {error && <p role="alert" className="error">{error}</p>}
    {(phase<2||phase===3)&&<button disabled={busy} onClick={save}>保存核对说明</button>}
    {state && !state.available && <p className="notice">可查看本步内容；执行前请完成前一步。<button onClick={()=>go(Math.max(0,phase-1))}>返回前一步</button></p>}
    {state && state.missing.length>0 && <details open><summary>继续前还需要</summary><ul>{state.missing.map(x=><li key={x}>{x}</li>)}</ul></details>}
    <button className="primary" disabled={busy||dirty||!state?.available||!!state?.missing.length} onClick={check}>核对当前内容并继续</button>
  </article>;
}

function RequirementRelations({p,mutate}:{p:Project;mutate:Props['mutate']}){
  const [source,setSource]=useState(''),[target,setTarget]=useState(''),[relation,setRelation]=useState('refines'),[error,setError]=useState('');
  const reqs=p.items.filter(i=>i.kind==='requirement');
  return <details><summary>需求的父子、细化与依赖关系</summary><p>拆分或取代时保留原编号，各子需求有自己的编号。关系不代表实现或测试通过。</p>{(p.requirement_relations||[]).map((r:Obj,n:number)=><p key={n}>{r.source.id} v{r.source.version} → {r.type} → {r.target.id} v{r.target.version}</p>)}{reqs.length>1&&<><label>起始需求<select value={source} onChange={e=>setSource(e.target.value)}><option value="">选择需求</option>{reqs.map(i=><option key={i.id} value={i.id}>{i.id}｜{i.title}</option>)}</select></label><label>关系<select value={relation} onChange={e=>setRelation(e.target.value)}>{[['parent_of','是其父需求'],['refines','细化'],['replaces','取代'],['depends_on','依赖']].map(([k,n])=><option key={k} value={k}>{n}</option>)}</select></label><label>目标需求<select value={target} onChange={e=>setTarget(e.target.value)}><option value="">选择需求</option>{reqs.map(i=><option key={i.id} value={i.id}>{i.id}｜{i.title}</option>)}</select></label><button disabled={!source||!target||source===target} onClick={async()=>{try{await mutate('/requirement-relations',{source_id:source,target_id:target,relation});setError('');}catch(e){setError((e as Error).message);}}}>记录关系</button></>}{error&&<p role="alert">{error}</p>}<p><a href={'/api/projects/'+p.id+'/requirements-exchange'} download={p.name+'-需求交换.json'}>导出结构化需求与关系</a>（当前草稿；正式交接使用确认基线）</p></details>;
}

function BehaviorEditor({item,mutate,busy}:{item:Obj;mutate:Props['mutate'];busy:boolean}){
  const [values,setValues]=useState<Obj>(item.behavior||{}),[kind,setKind]=useState(item.change_type||'modified'),[dirty,setDirty]=useState(false),[error,setError]=useState('');
  useEffect(()=>{if(!dirty){setValues(item.behavior||{});setKind(item.change_type||'modified');}},[item,dirty]);
  return <details><summary>{item.id}｜{item.title} · v{item.content_version||1}{dirty?' · 未保存':''}</summary><p>{item.statement}</p><label>本次性质<select value={kind} onChange={e=>{setKind(e.target.value);setDirty(true);}}>{[['new','新增'],['modified','修改'],['preserved','保持约束'],['existing','现状']].map(([v,t])=><option key={v} value={v}>{t}</option>)}</select></label>{Object.entries(behavior).map(([key,label])=><label key={key}>{String(label)}<textarea value={values[key]||''} onChange={e=>{setValues(v=>({...v,[key]:e.target.value}));setDirty(true);}}/></label>)}{error&&<p role="alert">{error}</p>}<button disabled={busy||!dirty} onClick={async()=>{try{await mutate('/items/'+item.id+'/behavior',{values,change_type:kind});setDirty(false);setError('');}catch(e){setError((e as Error).message);}}}>保存此需求行为</button></details>;
}
function QuestionScope({question:q,mutate}:{question:Obj;mutate:Props['mutate']}){
  const [reason,setReason]=useState(q.out_of_scope_reason||''),[error,setError]=useState('');
  return <details><summary>{q.question}{q.out_of_scope_reason?' · 已移出本期，仍未知':''}</summary><p>影响需求：{q.affected_requirements?.map((r:Obj)=>r.id).join('、')||q.related_refs?.join('、')||'尚待关联范围'}</p><p>{q.why} · 影响第{q.blocking_stage||3}步</p><label>明确移出本期的理由（清空可恢复）<textarea value={reason} onChange={e=>setReason(e.target.value)}/></label><p>不改变原问题、答案或条目采纳状态；本期仍依赖的规则不得移出。</p><button onClick={async()=>{try{await mutate('/questions/'+q.id+'/scope',{out_of_scope_reason:reason});}catch(e){setError((e as Error).message);}}}>保存范围决定</button>{error&&<p role="alert">{error}</p>}</details>;
}
