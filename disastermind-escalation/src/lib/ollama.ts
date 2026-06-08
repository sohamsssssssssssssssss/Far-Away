export type EscalationMemoFields = {
  situation: string
  recommended: string
  riskIfYes: string
  riskIfNo: string
}

const OLLAMA_BASE_URL = 'http://localhost:11434'

const SYSTEM_PROMPT = `You are DisasterMind's escalation intelligence system.
You generate structured escalation memos for Indian government disaster
commanders. Always respond with exactly 4 lines in this format:
SITUATION: [2 sentences max - what is happening and why it is urgent]
RECOMMENDED: [1 sentence - the specific action being requested]
RISK IF YES: [1 sentence - the main risk of approving]
RISK IF NO: [1 sentence - the main risk of not approving]
Be specific, factual, and urgent. Use real Indian geography and agencies.`

type OllamaTagResponse = {
  models?: Array<{ name: string }>
}

type OllamaChatResponse = {
  message?: {
    content?: string
  }
  error?: string
}

export function parseEscalationMemo(text: string): EscalationMemoFields {
  const fields: EscalationMemoFields = {
    situation: '',
    recommended: '',
    riskIfYes: '',
    riskIfNo: '',
  }

  for (const rawLine of text.split('\n')) {
    const line = rawLine.trim().replace(/^[-*]\s*/, '')
    if (line.toUpperCase().startsWith('SITUATION:')) {
      fields.situation = line.slice('SITUATION:'.length).trim()
    }
    if (line.toUpperCase().startsWith('RECOMMENDED:')) {
      fields.recommended = line.slice('RECOMMENDED:'.length).trim()
    }
    if (line.toUpperCase().startsWith('RISK IF YES:')) {
      fields.riskIfYes = line.slice('RISK IF YES:'.length).trim()
    }
    if (line.toUpperCase().startsWith('RISK IF NO:')) {
      fields.riskIfNo = line.slice('RISK IF NO:'.length).trim()
    }
  }

  const missing = Object.entries(fields)
    .filter(([, value]) => !value)
    .map(([key]) => key)

  if (missing.length > 0) {
    throw new Error(`Memo response missing required fields: ${missing.join(', ')}`)
  }

  return fields
}

async function selectOllamaModel(): Promise<string> {
  const response = await fetch(`${OLLAMA_BASE_URL}/api/tags`)
  if (!response.ok) {
    throw new Error(`Ollama model list failed with status ${response.status}. Confirm Ollama is running at ${OLLAMA_BASE_URL}.`)
  }

  const payload = (await response.json()) as OllamaTagResponse
  const modelNames = payload.models?.map((model) => model.name) ?? []
  const preferred = modelNames.find((name) => name === 'llama3.2' || name.startsWith('llama3.2:'))

  if (preferred) {
    return preferred
  }

  if (modelNames[0]) {
    return modelNames[0]
  }

  throw new Error('No Ollama models are pulled. Run `ollama pull llama3.2` or use any available local chat model.')
}

export async function generateEscalationMemo(prompt: string): Promise<EscalationMemoFields> {
  const model = await selectOllamaModel()

  const response = await fetch(`${OLLAMA_BASE_URL}/api/chat`, {
    method: 'POST',
    headers: {
      'content-type': 'application/json',
    },
    body: JSON.stringify({
      model,
      stream: false,
      options: {
        temperature: 0.2,
        num_predict: 300,
      },
      messages: [
        {
          role: 'system',
          content: SYSTEM_PROMPT,
        },
        {
          role: 'user',
          content: prompt,
        },
      ],
    }),
  })

  const payload = (await response.json()) as OllamaChatResponse

  if (!response.ok) {
    throw new Error(payload.error || `Ollama chat request failed with status ${response.status}.`)
  }

  const text = payload.message?.content
  if (!text) {
    throw new Error('Ollama returned no memo content.')
  }

  return parseEscalationMemo(text)
}
