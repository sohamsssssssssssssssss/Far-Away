import { config } from './config'

export const OLLAMA_BASE = config.ollama.url
export const OLLAMA_MODEL = config.ollama.model

type OllamaChatResponse = {
  message?: {
    content?: string
  }
  error?: string
}

export async function ollamaChat(systemPrompt: string, userMessage: string): Promise<string> {
  const res = await fetch(`${OLLAMA_BASE}/api/chat`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      model: OLLAMA_MODEL,
      stream: false,
      messages: [
        { role: 'system', content: systemPrompt },
        { role: 'user', content: userMessage },
      ],
    }),
  })

  const data = (await res.json()) as OllamaChatResponse

  if (!res.ok) {
    throw new Error(data.error || `Ollama chat request failed with status ${res.status}.`)
  }

  const content = data.message?.content?.trim()
  if (!content) {
    throw new Error('Ollama returned no content.')
  }

  return content
}
