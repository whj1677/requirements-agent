import React, { useEffect, useRef, useState } from 'react';
import type { Project, JsonObject } from '../api';
import { ConflictReview, DraftConflictError, useDirty, useNavigation } from '../editing';

type Props = {project:Project;busy:boolean;mutate:(path:string,body:JsonObject)=>Promise<any>;onCreate?:()=>void;focusNonce?:number};
const savedIds=(project:Project):string[]=>[...(project.product_context?.scope_ids||[])];
export const scopeRequirementOptions=(project:Project)=>project.items.filter(item=>item.kind==='requirement'&&
  item.applies_to==='to_be'&&!item.target_item_id&&!item.superseded_by&&!['rejected','deferred','excluded'].includes(item.selection_status));

/** Scope is an independent choice. It never approves clauses or AI suggestions. */
export function RequirementScope({project,busy,mutate,onCreate,focusNonce}:Props){
  const [ids,setIds]=useState(()=>savedIds(project)),[dirty,setDirty]=useState(false),[open,setOpen]=useState(false);
  const [saving,setSaving]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
  const [conflict,setConflict]=useState<DraftConflictError|null>(null);
  const before=useRef(savedIds(project)),revision=useRef(project.revision),lock=useRef(false),panel=useRef<HTMLDetailsElement>(null);
  const navigate=useNavigation(),options=scopeRequirementOptions(project),visibleIds=new Set(options.map(item=>item.id));
  const retained=ids.filter(id=>!visibleIds.has(id)),disabled=busy||saving;
  useEffect(()=>{if(!dirty){setIds(savedIds(project));before.current=savedIds(project);revision.current=project.revision;}},[project,dirty]);
  useEffect(()=>{if(!focusNonce)return;setOpen(true);const frame=requestAnimationFrame(()=>{panel.current?.scrollIntoView({block:'start'});panel.current?.querySelector('summary')?.focus({preventScroll:true});});return()=>cancelAnimationFrame(frame);},[focusNonce]);
  function discard(){setIds(savedIds(project));before.current=savedIds(project);revision.current=project.revision;setDirty(false);setConflict(null);setError('');setNotice('');}
  function toggle(id:string,checked:boolean){
    if(!visibleIds.has(id))return;
    const next=checked?[...new Set([...ids,id])]:ids.filter(value=>value!==id);
    setIds(next);setDirty(JSON.stringify([...next].sort())!==JSON.stringify([...before.current].sort()));setError('');setNotice('');
  }
  async function save(){
    if(lock.current)throw Error('本期范围正在保存，请等待。');
    if(conflict)throw Error('请先核对本期范围的修改冲突。');
    lock.current=true;setSaving(true);setError('');
    try{
      const result=await mutate('/product-context',{expected_revision:revision.current,values:{},scope_ids:ids,
        __edit:{kind:'scope',before:before.current,mine:ids}});
      const written=result?.project||result;
      const saved=written?.product_context?.scope_ids||ids;
      setIds([...saved]);before.current=[...saved];revision.current=written?.revision??project.revision;
      setDirty(false);setConflict(null);setNotice('本期范围已保存。请在需求表格中核对并确认内容。');
    }catch(e){if(e instanceof DraftConflictError)setConflict(e);setError((e as Error).message);throw e;}
    finally{lock.current=false;setSaving(false);}
  }
  function keepMine(){
    const latest=Array.isArray(conflict?.latest)?conflict.latest:savedIds(project);
    // These entries have no checkbox here; keeping a visible edit must not
    // silently delete scope entries created elsewhere while this form was open.
    const next=[...new Set([...ids,...latest.filter(id=>!visibleIds.has(id))])];
    before.current=[...latest];revision.current=project.revision;setIds(next);
    setDirty(JSON.stringify([...next].sort())!==JSON.stringify([...latest].sort()));setConflict(null);setError('');
  }
  useDirty(project.id+'-requirement-scope',dirty,'本期需求范围',save,discard);
  return <details ref={panel} className="requirement-scope" open={open} onToggle={event=>setOpen(event.currentTarget.open)}>
    <summary>调整本期范围</summary>
    <p>勾选本期要做的需求，再保存范围。需求内容仍需在表格中确认。</p>
    <div role="group" aria-label="本期需求范围" className="requirement-scope-options">
      {options.map(item=><label className="requirement-scope-option" key={item.id}><input type="checkbox" aria-label={'纳入本期 '+item.title} checked={ids.includes(item.id)} disabled={disabled} onChange={event=>toggle(item.id,event.target.checked)}/><span>{item.title}</span></label>)}
      {!options.length&&<p>还没有可选择的需求，可先补充需求或让助手整理资料。</p>}
    </div>
    {!!retained.length&&<p className="muted">另有 {retained.length} 项已保存范围内容，本次保留：{retained.map(id=>project.items.find(item=>item.id===id)?.title||id).join('、')}。</p>}
    <ConflictReview conflict={conflict} onReviewed={keepMine} onDiscard={discard}/>
    {error&&!conflict&&<p role="alert" className="connected-error">{error}</p>}{notice&&<p role="status">{notice}</p>}
    <div className="connected-actions">{dirty&&<><button type="button" className="primary" disabled={disabled||!!conflict} onClick={()=>void save().catch(()=>{})}>{saving?'正在保存范围…':'保存本期范围'}</button><button type="button" disabled={disabled} onClick={discard}>放弃范围修改</button></>}{onCreate&&<button type="button" disabled={disabled} onClick={()=>navigate(onCreate)}>补充需求</button>}</div>
  </details>;
}
