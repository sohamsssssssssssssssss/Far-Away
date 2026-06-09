import { promptLLM } from './llm'

export interface BriefingContext {
  activeZones?: string[]
  agentDecisionCount?: number
  deployedBoats?: number
  deployedHelicopters?: number
  shelterUtilisation?: number   // 0-100
  topRiskZone?: string
  topRiskProbability?: number   // 0-1
  lastEscalation?: string
  minutesSinceLastBriefing?: number
}

export interface SituationBriefing {
  text: string
  generatedAt: string
  context: BriefingContext
}

export async function generateSituationBriefing(
  context: BriefingContext = {}
): Promise<SituationBriefing> {
  const {
    activeZones = ['Zone 6', 'Zone 7'],
    agentDecisionCount = 12,
    deployedBoats = 12,
    deployedHelicopters = 4,
    shelterUtilisation = 73,
    topRiskZone = 'Zone 7',
    topRiskProbability = 0.92,
    lastEscalation = 'Mandatory evacuation — Zone 7',
    minutesSinceLastBriefing = 15,
  } = context

  const systemPrompt = 'You are a professional disaster management AI briefing system. Be concise, factual, and authoritative.'

  const userMessage = `You are the AI briefing system for DisasterMind, an autonomous disaster response platform deployed during Cyclone Remal hitting the Odisha coast of India.

Generate a situation briefing for state disaster management officials. Write in plain, professional language. Maximum 200 words. No bullet points — flowing prose only.

Current system state:
- Active high-risk zones: ${activeZones.join(', ')}
- Autonomous agent decisions in last ${minutesSinceLastBriefing} minutes: ${agentDecisionCount}
- Resources deployed: ${deployedBoats} boats, ${deployedHelicopters} helicopters
- Average shelter utilisation: ${shelterUtilisation}%
- Highest risk zone: ${topRiskZone} at ${Math.round(topRiskProbability * 100)}% inundation probability
- Last escalation sent to commanders: ${lastEscalation}
- Time since last briefing: ${minutesSinceLastBriefing} minutes

The briefing must cover:
1. What has happened since the last briefing
2. What the system has done autonomously
3. Current resource deployment status
4. What is projected in the next 2 hours
5. Any escalations requiring commander attention

Begin directly with the briefing. No preamble.`

  const text = await promptLLM(systemPrompt, userMessage)

  return {
    text,
    generatedAt: new Date().toISOString(),
    context,
  }
}
