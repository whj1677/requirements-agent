import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { api, setCsrf, ProjectSummary, SessionInfo } from './api';
import { AppShell, ProjectSwitcher } from './shell';
import { ModelSettings } from './model-settings';
import { Workspace } from './workspace';
import type { Compat } from './workspace';
import { DraftProvider, useNavigation } from './editing';
import './style.css';
import './workflow.css';

const REQUIRED_CAPABILITIES = ['actions', 'five-step-workflow'];
function compatOf(info?: SessionInfo['backend']): Compat {
  if (!info) return { status: 'unverified', missing: [] };
  const missing = REQUIRED_CAPABILITIES.filter(c => !(info.capabilities || []).includes(c));
  return missing.length ? { status: 'incompatible', missing } : { status: 'ok', missing: [] };
}

function App() {
  const guard=useNavigation();
  const [logged, setLogged] = useState(false), [token, setToken] = useState(''), [error, setError] = useState(''), [busy, setBusy] = useState(false);
  const [projects, setProjects] = useState<ProjectSummary[]>([]), [models, setModels] = useState<Record<string, any>>({});
  const [active, setActive] = useState(''), [visited, setVisited] = useState<string[]>([]), [name, setName] = useState(''), [settings, setSettings] = useState(false);
  const [compat, setCompat] = useState<Compat>({ status: 'unverified', missing: [] });
  async function refresh() { const [list, config] = await Promise.all([api<ProjectSummary[]>('/projects'), api('/models')]); setProjects(list); setModels(config); }
  async function act(fn: () => Promise<unknown>) { setError(''); setBusy(true); try { await fn(); } catch(e) { setError((e as Error).message); } finally { setBusy(false); } }
  function select(id: string) { guard(()=>{setActive(id); setVisited(ids => ids.includes(id) ? ids : [...ids, id]); setSettings(false);}); }
  async function loadSession() { const x = await api<SessionInfo>('/session'); setCsrf(x.csrf); setCompat(compatOf(x.backend)); }
  useEffect(() => { loadSession().then(() => { setLogged(true); return refresh(); }).catch(e => setError((e as Error).message)); }, []);
  if (!logged) return <div className="login"><div className="brand-mark">需</div><h1>需求工作区</h1><p>从零散信息到可评审的需求讨论稿。</p><form onSubmit={e => { e.preventDefault(); act(async () => { await api('/session/login', 'POST', { key: token }); await loadSession(); setLogged(true); setToken(''); await refresh(); }); }}><label>本机访问令牌<input type="password" autoComplete="off" value={token} onChange={e => setToken(e.target.value)} /></label><button className="primary" disabled={busy}>进入工作台</button></form>{error && <p role="alert">{error}</p>}</div>;
  return <AppShell sidebar={<aside className="sidebar"><div className="brand"><span className="brand-mark">需</span><strong>需求 Agent</strong></div><ProjectSwitcher projects={projects} currentId={active} name={name} setName={setName} busy={busy} onCreate={() => act(async () => { const p = await api('/projects', 'POST', { name }); setName(''); await refresh(); select(p.id); })} onSwitch={select} /><div className="sidebar-bottom"><button title="模型设置" onClick={() => guard(()=>setSettings(true))}>⚙<span className="nav-label">模型设置</span></button></div></aside>}>
    <div className="compact-projects"><select aria-label="切换项目" value={active} onChange={e => select(e.target.value)}><option value="">选择项目</option>{projects.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select><form onSubmit={e => { e.preventDefault(); act(async () => { const p = await api('/projects', 'POST', { name }); setName(''); await refresh(); select(p.id); }); }}><input aria-label="窄屏新项目名称" value={name} onChange={e => setName(e.target.value)} placeholder="新项目名称" required /><button disabled={busy}>新建</button></form></div>
    {error && <p className="error" role="alert">{error}</p>}
    {settings && <div className="settings-surface"><button onClick={() => setSettings(false)}>返回当前任务</button><ModelSettings models={models} busy={busy} act={act} refresh={refresh} /></div>}
    {!settings && !active && <div className="welcome"><h1>选择或新建需求项目</h1><p>进入后可直接描述目标、添加资料并整理当前信息。</p></div>}
    {visited.map(id => <Workspace key={id} id={id} active={!settings && active === id} models={models} onProjectsChanged={refresh} compat={compat} onExit={() => setActive('')} onReLogin={() => { setActive(''); setLogged(false); }} />)}
  </AppShell>;
}
createRoot(document.getElementById('root')!).render(<DraftProvider><App /></DraftProvider>);
