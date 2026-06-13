import { config } from './config';

export interface LLMMessage {
  role: 'system' | 'user' | 'assistant';
  content: string;
}

export interface LLMResponse {
  text: string;
  provider: 'ollama' | 'anthropic' | 'gemini';
  model: string;
  durationMs: number;
}

// ─── OLLAMA ───────────────────────────────────────────────────────────────────

async function callOllama(messages: LLMMessage[]): Promise<LLMResponse> {
  const start = Date.now();
  const response = await fetch(`${config.ollama.url}/api/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      model: config.ollama.model,
      messages,
      stream: false,
    }),
  });

  if (!response.ok) {
    throw new Error(`Ollama error: ${response.status}`);
  }

  const data = await response.json();
  return {
    text: data.message?.content ?? '',
    provider: 'ollama',
    model: config.ollama.model,
    durationMs: Date.now() - start,
  };
}

// ─── BACKEND LLM PROXY ─────────────────────────────────────────────────────────
//
// The browser must NOT call Anthropic/Gemini directly — that ships the API key in
// the JS bundle to every user (and Anthropic blocks browser calls without the
// dangerous-direct-browser-access header). The DisasterMind backend exposes
// POST /llm/generate which holds the key SERVER-SIDE and uses claude-opus-4-8.
// When no key is configured the backend returns 503 and we fall back to Ollama
// (and then to an empty response), so the UI never crashes and never fakes prose.

async function callBackend(messages: LLMMessage[]): Promise<LLMResponse> {
  const start = Date.now();
  const response = await fetch(`${config.api.baseUrl}/llm/generate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ messages }),
  });

  if (!response.ok) {
    // 503 = no server-side key (use local fallback); other codes = upstream error.
    throw new Error(`Backend LLM proxy error: ${response.status}`);
  }

  const data = await response.json();
  return {
    text: data.text ?? '',
    provider: 'anthropic',
    model: 'claude-opus-4-8',
    durationMs: Date.now() - start,
  };
}

// ─── UNIFIED ENTRY POINT ──────────────────────────────────────────────────────

/**
 * Call the LLM via the DisasterMind backend proxy (server-side key, claude-opus-4-8).
 * Falls back to a local Ollama instance if the backend is unreachable or has no key,
 * then to an empty response so the UI never crashes.
 *
 * Usage:
 *   const res = await callLLM([
 *     { role: 'system', content: 'You are a disaster response AI.' },
 *     { role: 'user', content: 'Generate an escalation memo for...' },
 *   ]);
 *   console.log(res.text);
 */
export async function callLLM(messages: LLMMessage[]): Promise<LLMResponse> {
  try {
    return await callBackend(messages);
  } catch (err) {
    console.warn('[LLM] backend proxy unavailable, falling back to Ollama:', err);
    try {
      return await callOllama(messages);
    } catch (ollamaErr) {
      console.error('[LLM] Ollama fallback also failed:', ollamaErr);
      // Last resort: empty response so UI never crashes (callers render their
      // own deterministic fallback, e.g. Report.tsx -> buildFallbackReport).
      return {
        text: '',
        provider: 'ollama',
        model: config.ollama.model,
        durationMs: 0,
      };
    }
  }
}

/**
 * Convenience wrapper for single-turn prompts.
 * Usage:
 *   const text = await promptLLM('system prompt here', 'user message here');
 */
export async function promptLLM(
  systemPrompt: string,
  userMessage: string
): Promise<string> {
  const res = await callLLM([
    { role: 'system', content: systemPrompt },
    { role: 'user', content: userMessage },
  ]);
  return res.text;
}
