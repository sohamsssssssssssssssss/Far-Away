export interface AuditEntry {
  id: string
  timestamp: number
  agentId: string
  action: 'APPROVE' | 'REJECT' | 'OVERRIDE' | 'ESCALATE' | 'AUTO_EXECUTED'
  payload?: Record<string, unknown>
  operatorId?: string
  note?: string
}

export interface TimelineEntry {
  time: string
  event: string
  actor: string
  outcome: string
}

export interface CriticalDecision {
  decision: string
  rationale: string
  impact: string
}

export interface IncidentReport {
  incidentId: string
  generatedAt: string
  summary: string
  timeline: TimelineEntry[]
  agentsDeployed: string[]
  escalationsRaised: number
  escalationsApproved: number
  escalationsRejected: number
  overridesIssued: number
  criticalDecisions: CriticalDecision[]
  lessonsLearned: string[]
  recommendations: string[]
}

const SYSTEM_PROMPT = `You are an emergency operations analyst for the National Disaster Management Authority (NDMA), India. You have been given a structured audit log of a disaster response operation managed by the DisasterMind AI coordination system. Your task is to synthesise a formal post-incident report.

Return ONLY a valid JSON object. No markdown, no code fences, no preamble, no explanation. The JSON must conform exactly to this schema:
{
  "incidentId": "INC-YYYYMMDD-XXXX where XXXX is a random 4-digit number",
  "generatedAt": "ISO 8601 timestamp",
  "summary": "2-3 sentence executive summary of the incident and response",
  "timeline": [{ "time": "HH:MM", "event": "description", "actor": "agent or operator", "outcome": "result" }],
  "agentsDeployed": ["agent-id-1", "agent-id-2"],
  "escalationsRaised": 0,
  "escalationsApproved": 0,
  "escalationsRejected": 0,
  "overridesIssued": 0,
  "criticalDecisions": [{ "decision": "...", "rationale": "...", "impact": "..." }],
  "lessonsLearned": ["...", "..."],
  "recommendations": ["...", "..."]
}

If the audit log is minimal or contains only mock data, generate a plausible but realistic report appropriate for an Indian disaster response context (cyclone, flood, or earthquake scenario).`

export async function generateReport(auditLog: AuditEntry[]): Promise<IncidentReport> {
  const userMessage = `Audit log for analysis (JSON array):
${JSON.stringify(auditLog, null, 2)}

Current timestamp: ${new Date().toISOString()}
Generate the post-incident report now.`

  const response = await fetch('https://api.anthropic.com/v1/messages', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      model: 'claude-sonnet-4-20250514',
      max_tokens: 1500,
      system: SYSTEM_PROMPT,
      messages: [{ role: 'user', content: userMessage }],
    }),
  })

  if (!response.ok) {
    const body = await response.text().catch(() => '')
    throw new Error(`Anthropic API error: ${response.status}${body ? ` — ${body}` : ''}`)
  }

  const data = await response.json()
  const rawText: string = data.content?.[0]?.text ?? ''

  // Strip accidental markdown code fences
  const cleaned = rawText
    .replace(/^```(?:json)?\s*/i, '')
    .replace(/\s*```$/i, '')
    .trim()

  let report: IncidentReport
  try {
    report = JSON.parse(cleaned) as IncidentReport
  } catch {
    throw new Error('LLM returned malformed JSON')
  }

  return report
}
