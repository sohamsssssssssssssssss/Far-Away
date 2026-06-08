// Generic 3s polling hook (POLL_MS=3000, matching the reference dashboard).
//
// Fetches immediately, then on an interval. Aborts in-flight requests on
// unmount and ignores transient errors (the next tick retries), but surfaces
// the last error so panels can show an "unreachable" state. `refresh()` lets a
// caller force an out-of-band poll (e.g. right after an approve/reject).

import { useCallback, useEffect, useRef, useState } from "react";

export const POLL_MS = 3000;

export interface Polling<T> {
  data: T | undefined;
  error: Error | null;
  loading: boolean;
  refresh: () => void;
}

export function usePolling<T>(
  fetcher: (signal: AbortSignal) => Promise<T>,
  intervalMs: number = POLL_MS,
): Polling<T> {
  const [data, setData] = useState<T | undefined>(undefined);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(true);

  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;
  // Bumping this forces the effect to re-run an immediate fetch.
  const [tick, setTick] = useState(0);
  const refresh = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();

    const run = async () => {
      try {
        const result = await fetcherRef.current(controller.signal);
        if (cancelled) return;
        setData(result);
        setError(null);
      } catch (e) {
        if (cancelled || controller.signal.aborted) return;
        setError(e instanceof Error ? e : new Error(String(e)));
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    run();
    const id = setInterval(run, intervalMs);
    return () => {
      cancelled = true;
      controller.abort();
      clearInterval(id);
    };
  }, [intervalMs, tick]);

  return { data, error, loading, refresh };
}
