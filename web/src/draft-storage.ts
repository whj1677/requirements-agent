/** Input stays in memory when browser storage is blocked or full. */
const fallback = new Map<string, string>();
export const session = {
  getItem(key: string): string | null {
    if (fallback.has(key)) return fallback.get(key)!;
    try { return window.sessionStorage.getItem(key); } catch { return null; }
  },
  setItem(key: string, value: string): boolean {
    try { window.sessionStorage.setItem(key, value); fallback.delete(key); return true; }
    catch { fallback.set(key, value); return false; }
  },
  removeItem(key: string): void {
    fallback.delete(key);
    try { window.sessionStorage.removeItem(key); } catch { /* Editing remains available. */ }
  },
};

/** Serialization happens once after a pause, never once per input event. */
export function createDraftWriter(onFailure: () => void, delay = 400) {
  let timer: ReturnType<typeof setTimeout> | undefined;
  let pending: (() => Record<string, unknown>) | undefined;
  function flush(): boolean {
    clearTimeout(timer); timer = undefined;
    if (!pending) return true;
    const read = pending; pending = undefined;
    try {
      const entries = Object.entries(read()).map(([key,value]) => [key,JSON.stringify(value)] as const);
      let saved = true;
      for (const [key,value] of entries) if (!session.setItem(key,value)) saved = false;
      if (!saved) onFailure();
      return saved;
    } catch { pending = read; onFailure(); return false; }
  }
  return {
    schedule(read: () => Record<string, unknown>) {
      pending = read; clearTimeout(timer); timer = setTimeout(flush,delay);
    },
    flush,
    cancel() { clearTimeout(timer); timer = undefined; pending = undefined; },
  };
}

export function retainedWorkspaces(visited: string[], active: string, pinned: Set<string>, limit = 2): string[] {
  const ordered = [...visited.filter(id => id !== active), ...(active ? [active] : [])];
  const ordinary = ordered.filter(id => !pinned.has(id)).slice(-limit);
  return ordered.filter(id => pinned.has(id) || ordinary.includes(id));
}

export function taskFingerprint(tasks: {id:string;status:string;completed_steps?:number;calls?:number;message?:string;step_states?:{stage:string;status:string}[]}[]): string {
  return tasks.map(task => [task.id,task.status,task.completed_steps,task.calls,task.message,
    task.step_states?.map(step=>step.stage+':'+step.status).join(',')].join(':')).join('|');
}
