import React, { useEffect, useRef } from 'react';

type Obj = Record<string, any>;

export function useDialog(open: boolean, onClose: () => void) {
  const close = useRef(onClose);
  close.current = onClose;
  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement as HTMLElement | null;
    const first = document.querySelector<HTMLElement>('[role="dialog"] input, [role="dialog"] textarea, [role="dialog"] button');
    first?.focus();
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') close.current();
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
  return <div className="project-switcher"><p className="sidebar-caption">当前项目</p><form onSubmit={e => { e.preventDefault(); onCreate(); }} className="new-project"><input aria-label="新项目名称" value={name} onChange={e => setName(e.target.value)} placeholder="新项目名称" required /><button aria-label="创建项目" disabled={busy}>＋</button></form><div className="project-list">{projects.map(project => <button key={project.id} className={currentId === project.id ? 'active' : ''} onClick={() => onSwitch(project.id)}><span aria-hidden="true">▤</span><span>{project.name}</span></button>)}</div></div>;
}
