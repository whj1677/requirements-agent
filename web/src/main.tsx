import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { api, setCsrf, ProjectSummary, SessionInfo } from './api';
import { AppShell, ProjectSwitcher } from './shell';
import { ModelSettings } from './model-settings';
import { Workspace } from './workspace';
import type { Compat } from './workspace';
import { DraftProvider, useNavigation } from './editing';
import { activityNotice, beginActivity } from './activity';
import { RequestFeedback } from './feedback';
import './style.css';
import './workflow.css';
import './source-preview.css';

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
  const [distribution, setDistribution] = useState<string>();
  const [initializing, setInitializing] = useState(true), busyRef = useRef(false);
  async function refresh() { const [list, config] = await Promise.allSettled([api<ProjectSummary[]>('/projects'), api('/models')]); if(list.status==='fulfilled')setProjects(list.value);if(config.status==='fulfilled')setModels(config.value);if(list.status==='rejected')throw list.reason;if(config.status==='rejected')throw config.reason; }
  async function act(fn: () => Promise<unknown>) { if(busyRef.current)return;busyRef.current=true;const finish=beginActivity('正在处理当前操作');setError('');setBusy(true);try { await fn();finish(); }catch(e){const message=(e as Error).message;setError(message);finish(message,true);}finally{busyRef.current=false;setBusy(false);} }
  function select(id: string) { guard(()=>{setActive(id); setVisited(ids => ids.includes(id) ? ids : [...ids, id]); setSettings(false);}); }
  async function loadSession() { const x = await api<SessionInfo>('/session'); setCsrf(x.csrf); setCompat(compatOf(x.backend)); setDistribution(x.backend?.distribution); }
  async function initialize(){setInitializing(true);setError('');try{await loadSession();setLogged(true);await refresh();}catch(e){setError((e as Error).message);}finally{setInitializing(false);}}
  useEffect(() => { void initialize(); }, []);
  async function createProject(){const p=await api<ProjectSummary>('/projects','POST',{name});setName('');setProjects(list=>[...list.filter(x=>x.id!==p.id),p]);setActive(p.id);setVisited(ids=>ids.includes(p.id)?ids:[...ids,p.id]);setSettings(false);await refresh().catch(e=>{setError('项目已创建，但列表更新失败。'+(e as Error).message);activityNotice('项目已创建，请勿重复创建；可重试刷新项目列表。',true);});}
  if(initializing&&!logged)return <div className="login"><p role="status">正在连接工作台并读取项目列表…</p></div>;
  if (!logged) return <div className="login"><div className="brand-mark">需</div><h1>需求工作区</h1><p>从零散信息到可评审的需求讨论稿。</p><form onSubmit={e => { e.preventDefault(); act(async () => { await api('/session/login', 'POST', { key: token }); await loadSession(); setLogged(true); setToken(''); await refresh(); }); }}><label>本机访问令牌<input type="password" autoComplete="off" value={token} onChange={e => setToken(e.target.value)} /></label><button className="primary" disabled={busy}>{busy?'正在连接…':'进入工作台'}</button></form>{error && <p role="alert">{error}</p>}</div>;
  return <AppShell sidebar={<aside className="sidebar"><div className="brand"><span className="brand-mark">需</span><strong>需求 Agent</strong></div><ProjectSwitcher projects={projects} currentId={active} name={name} setName={setName} busy={busy} onCreate={() => act(createProject)} onSwitch={select} /><div className="sidebar-bottom"><button title="模型设置" onClick={() => guard(()=>setSettings(true))}>⚙<span className="nav-label">模型设置</span></button>{distribution === 'requirements-agent-windows' && <button title="授权与退出" disabled={busy} onClick={() => guard(() => window.location.assign('/activation/'))}><span aria-hidden="true">↗</span><span className="nav-label">授权与退出</span></button>}</div></aside>}>
    <div className="compact-projects"><select aria-label="切换项目" value={active} onChange={e => select(e.target.value)}><option value="">选择项目</option>{projects.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select><form onSubmit={e => { e.preventDefault(); act(createProject); }}><input aria-label="窄屏新项目名称" disabled={busy} value={name} onChange={e => setName(e.target.value)} placeholder="新项目名称" required /><button disabled={busy}>{busy?'正在创建…':'新建'}</button></form></div>
    {initializing&&<p role="status">正在读取项目列表与配置…</p>}
    {error && <p className="error" role="alert">{error}<button disabled={busy} onClick={()=>act(refresh)}>重试加载列表与配置</button></p>}
    {settings && <div className="settings-surface"><button disabled={busy} onClick={() => guard(()=>setSettings(false))}>返回当前任务</button><fieldset className="operation-fields" disabled={busy}><ModelSettings models={models} busy={busy} act={act} refresh={refresh} /></fieldset></div>}
    {!settings && !active && <div className="welcome"><h1>选择或新建需求项目</h1><p>进入后可直接描述目标、添加资料并整理当前信息。</p></div>}
    {visited.map(id => <Workspace key={id} id={id} active={!settings && active === id} models={models} onProjectsChanged={refresh} compat={compat} onExit={() => setActive('')} onReLogin={() => { setActive(''); setLogged(false); }} />)}
  </AppShell>;
}
createRoot(document.getElementById('root')!).render(<DraftProvider><App /><RequestFeedback /></DraftProvider>);
