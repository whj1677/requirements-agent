type Activity = { id: number; label: string; started: number };
type Snapshot = { active: Activity[]; message: string; error: boolean };
let snapshot: Snapshot = { active: [], message: '', error: false };
let sequence = 0;
const listeners = new Set<() => void>();
function publish(next: Snapshot) { snapshot = next; listeners.forEach(fn => fn()); }
export const activitySnapshot = () => snapshot;
export const subscribeActivity = (fn: () => void) => { listeners.add(fn); return () => { listeners.delete(fn); }; };
export const hasPendingActivity = () => snapshot.active.length > 0;
export function activityNotice(message: string, error = false) { publish({ ...snapshot, message, error }); }
export function beginActivity(label: string) {
  const id = ++sequence;
  publish({ active: [...snapshot.active, { id, label, started: Date.now() }], message: '', error: false });
  let finished = false;
  return (message = '', error = false) => {
    if (finished) return;
    finished = true;
    publish({ active: snapshot.active.filter(x => x.id !== id), message: message || snapshot.message, error: message ? error : snapshot.error });
  };
}
