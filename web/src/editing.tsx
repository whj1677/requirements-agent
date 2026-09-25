import React, { createContext, useContext, useEffect, useRef, useState } from 'react';
import { useDialog } from './shell';

type Entry = { label:string; save:()=>Promise<void>; discard:()=>void };
const Context = createContext<{register:(key:string,entry:Entry|null)=>void; navigate:(next:()=>void)=>void}>({register:()=>{},navigate:next=>next()});
export function DraftProvider({children}:{children:React.ReactNode}) {
  const entries=useRef(new Map<string,Entry>()), pending=useRef<(()=>void)|null>(null);
  const [open,setOpen]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState('');
  useDialog(open,()=>{if(!busy)setOpen(false);});
  useEffect(()=>{const fn=(e:BeforeUnloadEvent)=>{if(entries.current.size){e.preventDefault();e.returnValue='';}};window.addEventListener('beforeunload',fn);return()=>window.removeEventListener('beforeunload',fn);},[]);
  function navigate(next:()=>void){if(!entries.current.size){next();return;}pending.current=next;setError('');setOpen(true);}
  function leave(){setOpen(false);pending.current?.();pending.current=null;}
  return <Context.Provider value={{register:(key,entry)=>{if(entry)entries.current.set(key,entry);else entries.current.delete(key);},navigate}}>{children}{open&&<div className="modal-backdrop"><section className="modal" role="dialog" aria-modal="true" aria-label="未保存的修改"><h2>有尚未保存的修改</h2><p>{Array.from(entries.current.values()).map(x=>x.label).join('、')}</p><p>保存只记录本次编辑，不调用模型。保存失败时保留输入。</p>{error&&<p className="error" role="alert">{error}</p>}<div className="toolbar"><button disabled={busy} onClick={()=>setOpen(false)}>留在当前页</button><button disabled={busy} onClick={()=>{for(const x of entries.current.values())x.discard();entries.current.clear();leave();}}>放弃修改</button><button className="primary" disabled={busy} onClick={async()=>{setBusy(true);try{for(const [key,x] of Array.from(entries.current)){await x.save();entries.current.delete(key);}leave();}catch(e){setError((e as Error).message);}finally{setBusy(false);}}}>保存后离开</button></div></section></div>}</Context.Provider>;
}
export const useNavigation=()=>useContext(Context).navigate;
export function useDirty(key:string,dirty:boolean,label:string,save:()=>Promise<void>,discard:()=>void) {
  const context=useContext(Context),latest=useRef({save,discard});latest.current={save,discard};
  useEffect(()=>{context.register(key,dirty?{label,save:()=>latest.current.save(),discard:()=>latest.current.discard()}:null);return()=>context.register(key,null);},[key,dirty]);
}
