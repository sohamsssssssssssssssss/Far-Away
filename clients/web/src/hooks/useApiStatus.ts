import { useState, useEffect, useCallback } from 'react';
import { disasterApi } from '../lib/disasterApi';
import type { WSConnectionState } from '../lib/disasterApi';

export interface ApiStatus {
  backendOnline: boolean;
  wsState: WSConnectionState;
  lastChecked: Date | null;
  lastMessageAt: Date | null;
  agentsActive: number;
}

export function useApiStatus() {
  const [status, setStatus] = useState<ApiStatus>({
    backendOnline: false,
    wsState: 'connecting',
    lastChecked: null,
    lastMessageAt: null,
    agentsActive: 0,
  });

  const checkHealth = useCallback(async () => {
    const health = await disasterApi.health();
    setStatus(prev => ({
      ...prev,
      backendOnline: health !== null && health.status !== 'down',
      lastChecked: new Date(),
      agentsActive: health?.agents_active ?? 0,
    }));
  }, []);

  useEffect(() => {
    checkHealth();
    const interval = setInterval(checkHealth, 30_000);
    return () => clearInterval(interval);
  }, [checkHealth]);

  const setWsState = useCallback((wsState: WSConnectionState) => {
    setStatus(prev => ({ ...prev, wsState }));
  }, []);

  const recordMessage = useCallback(() => {
    setStatus(prev => ({ ...prev, lastMessageAt: new Date() }));
  }, []);

  return { status, setWsState, recordMessage };
}
