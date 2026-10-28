import React, { createContext, useCallback, useContext, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { useDialog } from './shell';
import { activityNotice, hasPendingActivity } from './activity';

type Entry = { label:string; save:()=>Promise<void>; discard:()=>void };
export async function saveRegisteredDrafts(entries:Map<string,Entry>,except?:string,only?:string[]){
  for(const [key,entry] of Array.from(entries)){
    if(key===except||(only&&!only.includes(key)))continue;
    await entry.save();entries.delete(key);
  }
}
const Context = createContext<{dirtyKeys:string[];register:(key:string,entry:Entry|null)=>void; navigate:(next:()=>void)=>void;saveDrafts:(except?:string,only?:string[])=>Promise<void>}>({dirtyKeys:[],register:()=>{},navigate:next=>next(),saveDrafts:async()=>{}});
export function DraftProvider({children}:{children:React.ReactNode}) {
  const entries=useRef(new Map<string,Entry>()), pending=useRef<(()=>void)|null>(null);
  const [open,setOpen]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState('');
  const [dirtyKeys,setDirtyKeys]=useState<string[]>([]);
  const register=useCallback((key:string,entry:Entry|null)=>{
    if(entry)entries.current.set(key,entry);else entries.current.delete(key);
    const next=[...entries.current.keys()];
    setDirtyKeys(previous=>previous.length===next.length&&previous.every((value,index)=>value===next[index])?previous:next);
  },[]);
  useDialog(open,()=>{if(!busy)setOpen(false);});
  useEffect(()=>{const fn=(e:BeforeUnloadEvent)=>{if(entries.current.size||hasPendingActivity()){e.preventDefault();e.returnValue='';}};window.addEventListener('beforeunload',fn);return()=>window.removeEventListener('beforeunload',fn);},[]);
  function navigate(next:()=>void){if(hasPendingActivity()){activityNotice('当前操作仍在处理中，请等待结果后再切换页面。');return;}if(!entries.current.size){next();return;}pending.current=next;setError('');setOpen(true);}
  function leave(){setOpen(false);pending.current?.();pending.current=null;}
  async function saveDrafts(except?:string,only?:string[]){await saveRegisteredDrafts(entries.current,except,only);}
  return <Context.Provider value={{dirtyKeys,register,navigate,saveDrafts}}>{children}{open&&<div className="modal-backdrop"><section className="modal" role="dialog" aria-modal="true" aria-label="未保存的修改"><h2>有尚未保存的修改</h2><p>{Array.from(entries.current.values()).map(x=>x.label).join('、')}</p><p>保存只记录本次编辑，不调用模型。保存失败时保留输入。</p>{error&&<p className="error" role="alert">{error}</p>}<div className="toolbar"><button disabled={busy} onClick={()=>setOpen(false)}>留在当前页</button><button disabled={busy} onClick={()=>{for(const x of entries.current.values())x.discard();entries.current.clear();leave();}}>放弃修改</button><button className="primary" disabled={busy} onClick={async()=>{setBusy(true);try{await saveDrafts();leave();}catch(e){setError((e as Error).message);}finally{setBusy(false);}}}>保存后离开</button></div></section></div>}</Context.Provider>;
}
export const useNavigation=()=>useContext(Context).navigate;
export const useDraftKeys=()=>useContext(Context).dirtyKeys;
export const useSaveDrafts=()=>useContext(Context).saveDrafts;
export type EditProof = { kind: 'answers'|'behavior'|'item'|'context'|'intake'|'scope'|'question_note'; id?: string; before: unknown; mine: unknown };
export class DraftConflictError extends Error {
  constructor(public before: unknown, public latest: unknown, public mine: unknown) { super('同一对象已被其他修改更新。请核对旧值、最新值和我的修改后再继续。'); }
}
export function ConflictReview({ conflict, onReviewed, onDiscard }: { conflict: DraftConflictError|null; onReviewed: ()=>void; onDiscard: ()=>void }) {
  if (!conflict) return null;
  const show=(value:unknown)=>typeof value==='string'?value:JSON.stringify(value,null,2);
  return <div className="error" role="alert"><p>{conflict.message}</p><details open><summary>核对编辑冲突</summary><p>旧值</p><pre>{show(conflict.before)}</pre><p>最新值</p><pre>{show(conflict.latest)}</pre><p>我的修改</p><pre>{show(conflict.mine)}</pre><p>保留修改后，请再次点击原位置的保存按钮，才会写入最新版本。</p><div className="toolbar"><button type="button" onClick={onReviewed}>已核对，保留我的修改</button><button type="button" onClick={onDiscard}>放弃我的修改，使用最新值</button></div></details></div>;
}
export function useDirty(key:string,dirty:boolean,label:string,save:()=>Promise<void>,discard:()=>void) {
  const context=useContext(Context),latest=useRef({save,discard});latest.current={save,discard};
  useLayoutEffect(()=>{context.register(key,dirty?{label,save:()=>latest.current.save(),discard:()=>latest.current.discard()}:null);return()=>context.register(key,null);},[key,dirty]);
  return ()=>context.register(key,null);
}
