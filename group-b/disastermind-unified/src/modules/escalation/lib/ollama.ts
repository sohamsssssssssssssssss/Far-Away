import { callLLM } from '../../../lib/llm'

export type EscalationMemoFields = {
  situation: string
  recommended: string
  riskIfYes: string
  riskIfNo: string
}

const SYSTEM_PROMPT = `You are DisasterMind's escalation intelligence system.
You generate structured escalation memos for Indian government disaster
commanders. Always respond with exactly 4 lines in this format:
SITUATION: [2 sentences max - what is happening and why it is urgent]
RECOMMENDED: [1 sentence - the specific action being requested]
RISK IF YES: [1 sentence - the main risk of approving]
RISK IF NO: [1 sentence - the main risk of not approving]
Be specific, factual, and urgent. Use real Indian geography and agencies.`

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

export async function generateEscalationMemo(prompt: string): Promise<EscalationMemoFields> {
  const result = await callLLM([
    { role: 'system', content: SYSTEM_PROMPT },
    { role: 'user', content: prompt },
  ])
  return parseEscalationMemo(result.text)
}
