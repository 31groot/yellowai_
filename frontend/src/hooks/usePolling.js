import { useCallback, useEffect, useRef, useState } from "react";

// Calls fetcher now and every `ms`. Returns {data, error, refresh}.
// Pauses while the tab is hidden; ignores stale responses after deps change.
export function usePolling(fetcher, deps, ms = 3000) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const gen = useRef(0);

  const refresh = useCallback(async () => {
    const mine = gen.current;
    try {
      const d = await fetcher();
      if (mine === gen.current) { setData(d); setError(null); }
    } catch (e) {
      if (mine === gen.current) setError(e);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  useEffect(() => {
    gen.current += 1;
    setData(null);
    refresh();
    const t = setInterval(() => { if (!document.hidden) refresh(); }, ms);
    return () => clearInterval(t);
  }, [refresh, ms]);

  return { data, error, refresh };
}
