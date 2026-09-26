import React, { useEffect, useRef, useState } from 'react';

type Obj = Record<string, any>;

export function useDialog(open: boolean, onClose: () => void) {
  const close = useRef(onClose);
  close.current = onClose;
  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement as HTMLElement | null;
    const own=Array.from(document.querySelectorAll<HTMLElement>('[role="dialog"]')).filter(el=>el.offsetParent!==null).at(-1);
    const first=own?.querySelector<HTMLElement>('input,textarea,button');
    first?.focus();
    const escape = (event: KeyboardEvent) => {
      const top=Array.from(document.querySelectorAll<HTMLElement>('[role="dialog"]')).filter(el=>el.offsetParent!==null).at(-1);
      if(top!==own)return;
      if (event.key === 'Escape') {event.preventDefault();close.current();}
      if (event.key === 'Tab') {
        const dialogs = Array.from(document.querySelectorAll<HTMLElement>('[role="dialog"]')).filter(el => el.offsetParent !== null);
        const dialog = dialogs[dialogs.length - 1];
        const targets = Array.from(dialog?.querySelectorAll<HTMLElement>('button:not(:disabled),input:not(:disabled),textarea,select,a[href],summary,[tabindex="0"]') || []).filter(el => el.offsetParent !== null);
        const first = targets[0], last = targets[targets.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
      }
    };
    document.addEventListener('keydown', escape);
    return () => {
      document.removeEventListener('keydown', escape);
      requestAnimationFrame(() => { if (previous?.isConnected) previous.focus(); });
    };
  }, [open]);
}

export function AppShell({ sidebar, children }: { sidebar: React.ReactNode; children: React.ReactNode }) {
  return <div className="layout">{sidebar}<main>{children}</main></div>;
}

export function ProjectSwitcher({ projects, currentId, name, setName, busy, onCreate, onSwitch }: { projects: Obj[]; currentId?: string; name: string; setName: (s: string) => void; busy: boolean; onCreate: () => void; onSwitch: (id: string) => void }) {
  const [creating,setCreating]=useState(false); useDialog(creating,()=>setCreating(false));
  useEffect(()=>{if(!name&&!busy)setCreating(false);},[name,busy]);
  return <div className="project-switcher"><button onClick={()=>setCreating(true)}>＋ 新建需求项目</button><p className="sidebar-caption">当前项目</p><div className="project-list">{projects.map(project => <button key={project.id} className={currentId === project.id ? 'active' : ''} onClick={() => onSwitch(project.id)}><span aria-hidden="true">▤</span><span>{project.name}</span></button>)}</div>{creating&&<div className="modal-backdrop"><form className="modal" role="dialog" aria-modal="true" aria-label="新建需求项目" onSubmit={e=>{e.preventDefault();onCreate();}}><h2>新建需求项目</h2><label>项目名称<input aria-label="新项目名称" required value={name} onChange={e=>setName(e.target.value)} placeholder="给本次改动起一个可辨识的名称"/></label><div className="toolbar"><button type="button" disabled={busy} onClick={()=>setCreating(false)}>取消</button><button className="primary" disabled={busy||!name.trim()}>创建项目</button></div></form></div>}</div>;
}
