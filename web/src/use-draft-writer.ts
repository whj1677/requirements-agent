import { useEffect, useMemo, useRef } from 'react';
import { createDraftWriter } from './draft-storage';

export function useDraftWriter(key: string, onFailure: () => void) {
  const failure = useRef(onFailure); failure.current = onFailure;
  const writer = useMemo(() => createDraftWriter(() => failure.current()), [key]);
  useEffect(() => {
    const flush = () => { writer.flush(); };
    const hide = () => { if (document.visibilityState === 'hidden') flush(); };
    window.addEventListener('pagehide',flush);
    document.addEventListener('visibilitychange',hide);
    return () => {
      flush(); window.removeEventListener('pagehide',flush);
      document.removeEventListener('visibilitychange',hide);
    };
  }, [writer]);
  return writer;
}
