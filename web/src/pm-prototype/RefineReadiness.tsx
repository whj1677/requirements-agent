import React, { useRef, useState, useEffect } from 'react';
import { api, Project, Blocker, BlockerAction, JsonObject } from '../api';
import { useNavigation } from '../editing';
import { confirmRefinement, refinementBlockers } from './refine-readiness';

export function RefineReadiness({project,busy,mutate,onResolve,focus,onConfirmed,additionalBlockers=[],tableMode=false}:{project:Project;busy:boolean;mutate:(path:string,body:JsonObject)=>Promise<any>;onResolve:(action:BlockerAction,row:Blocker)=>void;focus?:{review?:boolean;nonce:number};onConfirmed?:()=>void;additionalBlockers?:Blocker[];tableMode?:boolean}){
  const [expanded,setExpanded]=useState(false),[saving,setSaving]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
  const lock=useRef(false),panel=useRef<HTMLElement>(null),navigate=useNavigation();
  const groupedCodes=['behavior_fields_missing','behavior_review_pending','acceptance_missing','requirement_confirmation_stale','scope_unadopted','selected_requirement_missing'];
  const base=refinementBlockers(project).filter(row=>!(tableMode&&additionalBlockers.length&&groupedCodes.includes(row.code)));
  const rows=[...base,...additionalBlockers],complete=!rows.length&&project.product_flow?.slice(0,3).length===3&&project.product_flow.slice(0,3).every(state=>state.complete);
  useEffect(()=>{if(focus?.review){setExpanded(true);requestAnimationFrame(()=>panel.current?.scrollIntoView({block:'nearest'}));}},[focus?.nonce]);
  function confirm(){
    if(rows.length){setExpanded(true);setError('请先处理以下事项，再确认需求核对。');return;}
    navigate(()=>{if(lock.current)return;lock.current=true;setSaving(true);setError('');setNotice('');
      void confirmRefinement(project,()=>api<Project>('/projects/'+project.id),(step,revision,hash)=>mutate('/stage-checks/'+step,{expected_revision:revision,expected_hash:hash}))
        .then(()=>{setNotice('需求核对已记录，可查看交付准备情况。');onConfirmed?.();})
        .catch(e=>setError((e as Error).message)).finally(()=>{lock.current=false;setSaving(false);});
    });
  }
  return <section ref={panel} className="refine-readiness" aria-label="需求核对准备">
    <div className="connected-actions"><strong>{rows.length?`还有 ${rows.length} 项必要检查未完成`:complete?'完善需求已完成，可以进入交付':'必要内容已补齐，请确认后进入交付'}</strong>{!!rows[0]?.actions[0]&&<button type="button" className="primary" disabled={busy||saving} onClick={()=>onResolve(rows[0].actions[0],rows[0])}>处理第一项</button>}<button type="button" onClick={()=>setExpanded(!expanded)}>{expanded?'收起核对清单':'查看核对清单'}</button></div>
    {expanded&&<div className="refine-readiness-list">{rows.map(row=><div key={row.key} className="connected-blocker-row"><b>{row.title}</b><p>{row.detail||row.message}</p><div className="connected-actions">{row.actions.map((action,n)=><button key={n} type="button" disabled={busy||saving} onClick={()=>onResolve(action,row)}>{action.label||'去处理'} →</button>)}</div></div>)}{!rows.length&&<p>{complete?'当前已保存内容的需求核对已记录，后续修改会重新检查。':'来源、业务摘要、本期范围、条款及必要问题已通过当前内容检查。请核对后明确确认。'}</p>}</div>}
    <p className="muted">这里只列出进入交付前必须处理的检查。可选建议不会因为未填写而阻止进入交付。</p>
    {!complete&&!rows.length&&<button type="button" className="primary" disabled={busy||saving} onClick={confirm}>{saving?'正在记录核对…':'确认需求核对并进入交付'}</button>}
    {complete&&onConfirmed&&<button type="button" className="primary" disabled={busy||saving} onClick={()=>navigate(()=>onConfirmed())}>进入交付 →</button>}
    {error&&<p role="alert" className="connected-error">{error}</p>}{notice&&<p role="status">{notice}</p>}
  </section>;
}
