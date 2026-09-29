import React, { useEffect, useRef, useState } from 'react';
import { activityNotice, hasPendingActivity } from './activity';

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
      if (event.key === 'Escape') {event.preventDefault();if(hasPendingActivity()){activityNotice('当前操作仍在处理中，请等待结果后再关闭。');return;}close.current();}
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

function dateLabel(value: unknown): string {
  if (typeof value !== 'string' || !value) return '';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
}

export function ProjectSwitcher({ projects, currentId, name, setName, busy, onCreate, onSwitch }: { projects: Obj[]; currentId?: string; name: string; setName: (s: string) => void; busy: boolean; onCreate: () => void; onSwitch: (id: string) => void }) {
  const [creating, setCreating] = useState(false);
  const [query, setQuery] = useState('');
  useDialog(creating, () => { if (!busy) setCreating(false); });
  useEffect(() => { if (!name && !busy) setCreating(false); }, [name, busy]);
  const matches = projects.filter(project => [project.name, project.id, project.flow_current_title].some(value => String(value || '').toLocaleLowerCase().includes(query.trim().toLocaleLowerCase())));
  matches.sort((a, b) => String(b.updated_at || b.created || '').localeCompare(String(a.updated_at || a.created || '')));
  const duplicateNames = new Set(projects.filter((project, index) => projects.some((other, otherIndex) => otherIndex !== index && other.name === project.name)).map(project => project.name));

  return <div className="project-switcher">
    <button onClick={() => setCreating(true)}>＋ 新建需求项目</button>
    <label className="project-search">查找项目<input type="search" value={query} onChange={event => setQuery(event.target.value)} placeholder="搜索名称、阶段或编号" /></label>
    <p className="sidebar-caption">需求项目 · {matches.length}{query.trim() ? ' 个匹配' : ' 个'}</p>
    <div className="project-list">{matches.map(project => {
      const updated = dateLabel(project.updated_at);
      const created = dateLabel(project.created);
      return <button key={project.id} className={currentId === project.id ? 'active' : ''} aria-current={currentId === project.id ? 'page' : undefined} onClick={() => onSwitch(project.id)} title={project.name}>
        <span aria-hidden="true">▤</span><span className="project-list-text"><strong>{project.name}</strong><small>{project.flow_current_title || '阶段未提供'}{updated ? ' · 更新于 ' + updated : created ? ' · 创建于 ' + created : ''}{duplicateNames.has(project.name) ? ' · ' + project.id.slice(-6) : ''}</small></span>
      </button>;
    })}{!matches.length && <p className="project-empty">{query.trim() ? '没有匹配的项目，请换个关键词。' : '还没有项目，先新建一个。'}</p>}</div>
    {creating && <div className="modal-backdrop"><form className="modal" role="dialog" aria-modal="true" aria-label="新建需求项目" onSubmit={event => { event.preventDefault(); onCreate(); }}><h2>新建需求项目</h2><label>项目名称<input aria-label="新项目名称" disabled={busy} required value={name} onChange={event => setName(event.target.value)} placeholder="给本次改动起一个可辨识的名称" /></label><div className="toolbar"><button type="button" disabled={busy} onClick={() => setCreating(false)}>取消</button><button className="primary" disabled={busy || !name.trim()}>{busy ? '正在创建…' : '创建项目'}</button></div></form></div>}
  </div>;
}
