import { useEffect, useRef, useState } from 'react';
import { api, ApiError, categorizeError, type ErrorCategory, type JsonObject, type Project, type Run, type UserTask } from './api';
import { taskFingerprint } from './draft-storage';

export const workspaceErrorText: Record<ErrorCategory, string> = {
  'not-found': '项目不存在或已被删除', 'route-missing': '所需接口缺失，前后端版本可能不兼容',
  network: '无法连接服务', 'non-json': '服务返回了无法识别的内容', session: '页面连接状态已失效，请重新加载',
  forbidden: '访问被拒绝', conflict: '内容版本已变化', server: '暂时无法加载此项目', unknown: '暂时无法加载此项目',
};

/** Read and poll one project's saved state; navigation and edits belong to Workspace. */
export function useWorkspaceData(id: string, active: boolean, onProjectSnapshot?: (project: Project) => void) {
  const root = '/projects/' + id;
  const [p, setP] = useState<Project | null>(null);
  const [runs, setRuns] = useState<Run[]>([]), [tasks, setTasks] = useState<UserTask[]>([]);
  const [artifacts, setArtifacts] = useState<Record<string, JsonObject[]>>({ document_artifact: [] });
  const [fatal, setFatal] = useState<ApiError | null>(null), [stale, setStale] = useState<string[]>([]);
  const [staleNotice, setStaleNotice] = useState(''), [tasksLoaded, setTasksLoaded] = useState(false);
  const projectRef = useRef<Project | null>(null);
  const refreshTail = useRef<Promise<void>>(Promise.resolve());

  function refresh(): Promise<void> {
    // A mutation-triggered read must follow any older poll already in flight.
    const run = refreshTail.current.then(async () => {
      const records = Promise.allSettled([
        api<Run[]>(root + '/runs'), api<UserTask[]>(root + '/actions'),
        api<Record<string, JsonObject[]>>(root + '/artifacts'),
      ]);
      let project: Project;
      try { project = await api<Project>(root); }
      catch (error) { setFatal(error as ApiError); throw error; }
      // A read may have started before a confirmed write updated the local state.
      if (!projectRef.current || project.revision >= projectRef.current.revision) {
        projectRef.current = project; setP(project); onProjectSnapshot?.(project);
      }
      setFatal(null); setStaleNotice('');
      const responses = await records;
      const failed: string[] = [];
      responses.forEach((result, index) => {
        if (result.status === 'rejected') { failed.push(['运行记录', '任务列表', '产物记录'][index]); return; }
        if (index === 0) setRuns(result.value as Run[]);
        if (index === 1) { setTasks(result.value as UserTask[]); setTasksLoaded(true); }
        if (index === 2) setArtifacts(result.value as Record<string, JsonObject[]>);
      });
      setStale(failed);
    });
    refreshTail.current = run.then(() => {}, () => {});
    return run;
  }

  function onRefreshFail(error: unknown) { setStaleNotice(workspaceErrorText[categorizeError(error)]); }
  useEffect(() => { if (active) refresh().catch(onRefreshFail); }, [active]);

  const running = tasks.some(task => ['queued', 'running'].includes(task.status))
    || runs.some(run => ['queued', 'running'].includes(run.status));
  const pollingSnapshot = useRef({ tasks, runs }); pollingSnapshot.current = { tasks, runs };
  useEffect(() => {
    if (!running) return;
    let stopped = false, timer: ReturnType<typeof setTimeout>, inFlight = false;
    async function poll() {
      if (stopped || inFlight) return;
      clearTimeout(timer); inFlight = true;
      try {
        if (document.visibilityState === 'hidden') return;
        const next = await api<UserTask[]>(root + '/actions');
        if (stopped) return;
        const old = pollingSnapshot.current;
        const legacyRunning = old.runs.some(run => ['queued', 'running'].includes(run.status)
          && !old.tasks.some(task => task.run_ids.includes(run.id) && ['queued', 'running'].includes(task.status)));
        if (legacyRunning || taskFingerprint(next) !== taskFingerprint(old.tasks)) await refresh();
      } catch (error) { if (!stopped) onRefreshFail(error); }
      finally { inFlight = false; if (!stopped) timer = setTimeout(poll, document.visibilityState === 'hidden' ? 10000 : 1600); }
    }
    function visible() { if (document.visibilityState === 'visible') void poll(); }
    document.addEventListener('visibilitychange', visible); timer = setTimeout(poll, 1600);
    return () => { stopped = true; clearTimeout(timer); document.removeEventListener('visibilitychange', visible); };
  }, [running, root]);

  return { root, p, setP, projectRef, runs, tasks, setTasks, artifacts, fatal, stale,
    staleNotice, setStaleNotice, tasksLoaded, setTasksLoaded, running, refresh, onRefreshFail };
}
