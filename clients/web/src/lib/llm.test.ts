import { describe, it, expect, vi, beforeEach } from 'vitest'
import { callLLM, promptLLM } from './llm'

// The LLM client is security-critical: it must call the BACKEND proxy (which holds
// the key server-side) and never the provider directly, and it must degrade
// cleanly (backend -> ollama -> empty) so the UI never crashes or fakes prose.

function mockFetchOnce(impl: (url: string, init: RequestInit) => Partial<Response> & { json?: () => unknown }) {
  return vi.fn(async (url: string, init: RequestInit) => {
    const r = impl(String(url), init)
    return {
      ok: r.ok ?? true,
      status: r.status ?? 200,
      json: async () => (r.json ? r.json() : {}),
    } as Response
  })
}

describe('callLLM', () => {
  beforeEach(() => vi.restoreAllMocks())

  it('calls the backend proxy at /llm/generate — never a provider directly', async () => {
    const calls: string[] = []
    vi.stubGlobal('fetch', mockFetchOnce((url) => {
      calls.push(url)
      return { ok: true, json: () => ({ text: 'hello from backend' }) }
    }))

    const res = await callLLM([{ role: 'user', content: 'hi' }])

    expect(calls).toHaveLength(1)
    expect(calls[0]).toContain('/llm/generate')
    // Critically: no call to api.anthropic.com / generativelanguage from the browser.
    expect(calls.some((u) => u.includes('anthropic.com') || u.includes('googleapis'))).toBe(false)
    expect(res.text).toBe('hello from backend')
    expect(res.model).toBe('claude-opus-4-8')
    expect(res.provider).toBe('anthropic')
  })

  it('sends the messages array as the POST body', async () => {
    let body: unknown
    vi.stubGlobal('fetch', mockFetchOnce((_url, init) => {
      body = JSON.parse(String(init.body))
      return { ok: true, json: () => ({ text: 'ok' }) }
    }))
    const messages = [
      { role: 'system' as const, content: 'sys' },
      { role: 'user' as const, content: 'usr' },
    ]
    await callLLM(messages)
    expect(body).toEqual({ messages })
  })

  it('falls back to Ollama when the backend returns 503 (no server key)', async () => {
    const urls: string[] = []
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      urls.push(String(url))
      if (String(url).includes('/llm/generate')) {
        return { ok: false, status: 503, json: async () => ({}) } as Response
      }
      // Ollama endpoint
      return { ok: true, status: 200, json: async () => ({ message: { content: 'from ollama' } }) } as Response
    }))

    const res = await callLLM([{ role: 'user', content: 'hi' }])
    expect(urls[0]).toContain('/llm/generate')
    expect(urls[1]).toContain('/api/chat') // ollama
    expect(res.text).toBe('from ollama')
    expect(res.provider).toBe('ollama')
  })

  it('returns an empty (non-crashing) response when backend AND ollama both fail', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => {
      throw new Error('network down')
    }))
    const res = await callLLM([{ role: 'user', content: 'hi' }])
    expect(res.text).toBe('') // caller renders its own deterministic fallback
    expect(res.durationMs).toBe(0)
  })

  it('never throws to the caller even on total failure', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => {
      throw new Error('boom')
    }))
    await expect(callLLM([{ role: 'user', content: 'x' }])).resolves.toBeDefined()
  })
})

describe('promptLLM', () => {
  it('wraps a system+user turn and returns just the text', async () => {
    let body: { messages: Array<{ role: string; content: string }> } | undefined
    vi.stubGlobal('fetch', mockFetchOnce((_url, init) => {
      body = JSON.parse(String(init.body))
      return { ok: true, json: () => ({ text: 'briefing text' }) }
    }))
    const text = await promptLLM('be concise', 'brief me')
    expect(text).toBe('briefing text')
    expect(body!.messages).toEqual([
      { role: 'system', content: 'be concise' },
      { role: 'user', content: 'brief me' },
    ])
  })
})
