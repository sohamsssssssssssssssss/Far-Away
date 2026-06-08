import { promptLLM } from './llm';

export interface BriefingState {
  eventName: string;           // e.g. "Cyclone Remal"
  activeZones: string[];       // e.g. ["Zone 7", "Zone 4"]
  agentDecisionsSince: string[]; // last N agent decisions as plain strings
  resourcesSummary: string;    // e.g. "15 boats, 5 helicopters deployed"
  populationAtRisk: number;    // e.g. 84000
  projectedNextHours: string;  // e.g. "Inundation expected Zone 7 in 4.2 hours"
  pendingEscalations: number;  // count
}

export interface SituationBriefing {
  text: string;           // 200-word plain language briefing
  generatedAt: string;    // ISO timestamp
  provider: string;       // which LLM generated it
}

export async function generateSituationBriefing(
  state: BriefingState
): Promise<SituationBriefing> {
  const systemPrompt = `You are DisasterMind's briefing officer. Generate concise,
factual situation briefings for senior government officials and NDRF commanders.
Write in plain language. Maximum 200 words. No bullet points — flowing paragraphs.
Use specific numbers. Be direct about risks. Never use jargon.`;

  const userMessage = `Generate a situation briefing for the following state:

Event: ${state.eventName}
Active Zones: ${state.activeZones.join(', ')}
Population at Risk: ${state.populationAtRisk.toLocaleString()}
Resources Deployed: ${state.resourcesSummary}
Projection: ${state.projectedNextHours}
Pending Escalations: ${state.pendingEscalations}

Recent autonomous decisions:
${state.agentDecisionsSince.slice(0, 5).map((d, i) => `${i + 1}. ${d}`).join('\n')}

Cover: what has happened, what the system has done autonomously,
current resource deployment, projection for next 2 hours,
any escalations requiring commander attention.`;

  const text = await promptLLM(systemPrompt, userMessage);

  return {
    text,
    generatedAt: new Date().toISOString(),
    provider: 'llm',
  };
}
