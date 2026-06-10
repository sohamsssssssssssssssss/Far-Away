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

// ─── ANTHROPIC ────────────────────────────────────────────────────────────────

async function callAnthropic(messages: LLMMessage[]): Promise<LLMResponse> {
  const start = Date.now();

  // Separate system message from conversation
  const systemMsg = messages.find(m => m.role === 'system')?.content ?? '';
  const conversation = messages.filter(m => m.role !== 'system');

  const response = await fetch('https://api.anthropic.com/v1/messages', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'x-api-key': config.llm.anthropicKey,
      'anthropic-version': '2023-06-01',
    },
    body: JSON.stringify({
      model: 'claude-haiku-4-5-20251001',
      max_tokens: 1024,
      system: systemMsg,
      messages: conversation,
    }),
  });

  if (!response.ok) {
    throw new Error(`Anthropic error: ${response.status}`);
  }

  const data = await response.json();
  return {
    text: data.content?.[0]?.text ?? '',
    provider: 'anthropic',
    model: 'claude-haiku-4-5-20251001',
    durationMs: Date.now() - start,
  };
}

// ─── GEMINI ───────────────────────────────────────────────────────────────────

async function callGemini(messages: LLMMessage[]): Promise<LLMResponse> {
  const start = Date.now();

  // Convert to Gemini format
  const contents = messages
    .filter(m => m.role !== 'system')
    .map(m => ({
      role: m.role === 'assistant' ? 'model' : 'user',
      parts: [{ text: m.content }],
    }));

  const systemInstruction = messages.find(m => m.role === 'system')?.content;

  const body: Record<string, unknown> = { contents };
  if (systemInstruction) {
    body.systemInstruction = { parts: [{ text: systemInstruction }] };
  }

  const response = await fetch(
    `https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key=${config.llm.geminiKey}`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }
  );

  if (!response.ok) {
    throw new Error(`Gemini error: ${response.status}`);
  }

  const data = await response.json();
  return {
    text: data.candidates?.[0]?.content?.parts?.[0]?.text ?? '',
    provider: 'gemini',
    model: 'gemini-2.0-flash',
    durationMs: Date.now() - start,
  };
}

// ─── UNIFIED ENTRY POINT ──────────────────────────────────────────────────────

/**
 * Call the configured LLM provider.
 * Falls back to Ollama if the primary provider fails.
 *
 * Usage:
 *   const res = await callLLM([
 *     { role: 'system', content: 'You are a disaster response AI.' },
 *     { role: 'user', content: 'Generate an escalation memo for...' },
 *   ]);
 *   console.log(res.text);
 */
export async function callLLM(messages: LLMMessage[]): Promise<LLMResponse> {
  const provider = config.llm.provider;

  try {
    if (provider === 'anthropic' && config.llm.anthropicKey) {
      return await callAnthropic(messages);
    }
    if (provider === 'gemini' && config.llm.geminiKey) {
      return await callGemini(messages);
    }
    // Default: Ollama
    return await callOllama(messages);
  } catch (err) {
    console.warn(`[LLM] ${provider} failed, falling back to Ollama:`, err);
    // Fallback to Ollama
    try {
      return await callOllama(messages);
    } catch (ollamaErr) {
      console.error('[LLM] Ollama fallback also failed:', ollamaErr);
      // Last resort: return empty response so UI never crashes
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
