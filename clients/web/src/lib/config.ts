// Central config — all environment variables accessed through here
// Never import import.meta.env directly in components

export const config = {
  api: {
    baseUrl: import.meta.env.VITE_API_URL || 'http://localhost:8000',
    wsUrl: import.meta.env.VITE_WS_URL || 'ws://localhost:8000/ws',
  },
  ollama: {
    url: import.meta.env.VITE_OLLAMA_URL || 'http://localhost:11434',
    model: import.meta.env.VITE_OLLAMA_MODEL || 'llama3.2',
  },
  llm: {
    anthropicKey: import.meta.env.VITE_ANTHROPIC_API_KEY || '',
    geminiKey: import.meta.env.VITE_GEMINI_API_KEY || '',
    // provider: 'ollama' | 'anthropic' | 'gemini'
    provider: import.meta.env.VITE_ANTHROPIC_API_KEY ? 'anthropic'
              : import.meta.env.VITE_GEMINI_API_KEY ? 'gemini'
              : 'ollama',
  },
  map: {
    mapboxToken: import.meta.env.VITE_MAPBOX_TOKEN || '',
    // useRealMap: true only when Mapbox token is present (Phase 1+)
    useRealMap: !!import.meta.env.VITE_MAPBOX_TOKEN,
  },
  env: import.meta.env.VITE_ENV || 'development',
  isDev: import.meta.env.VITE_ENV !== 'production',
} as const;
