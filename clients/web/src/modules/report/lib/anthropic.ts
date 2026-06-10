import { callLLM } from '../../../lib/llm'
import type { Incident } from './incidents'

export type ReportSection = {
  title: string
  body: string
}

export type GenerateReportInput = {
  incident: Incident
  sections: string[]
  audience: string
}

const systemPrompt = `You are DisasterMind's post-incident analysis AI. You generate structured government incident reports for Indian disaster management authorities.
Write in formal, official language suitable for NDMA/SDMA review.
Use specific numbers, percentages, and concrete facts.
Reference real Indian agencies, geography, and protocols.
Structure your response with clear section headers in ALL CAPS followed by a colon.
Keep each section concise but substantive, 3-6 sentences.
Do not use markdown formatting - plain text only.`

export const requiredSectionOrder = [
  'EXECUTIVE SUMMARY',
  'EVENT TIMELINE',
  'AUTONOMOUS DECISIONS SUMMARY',
  'HUMAN OVERRIDE LOG',
  'RESOURCE UTILISATION',
  'POPULATION OUTCOMES',
  'MODEL PERFORMANCE',
  'RECOMMENDATIONS',
]

export function buildUserPrompt({ incident, sections, audience }: GenerateReportInput) {
  const outcomePercent = ((incident.civiliansReached / incident.civiliansAtRisk) * 100).toFixed(1)

  return `Generate a post-incident report for: ${incident.disaster}, ${incident.location}, ${incident.month}.
${incident.durationHours}-hour event. ${incident.zones} zones. ${incident.teamsDeployed} NDRF teams deployed. ${incident.autonomousDecisions.toLocaleString('en-IN')} autonomous decisions made by AI agents. ${incident.humanOverrides} human overrides by commanders. ${incident.civiliansReached.toLocaleString('en-IN')} civilians reached out of ${incident.civiliansAtRisk.toLocaleString('en-IN')} at risk (${outcomePercent}%).
Include sections: Executive Summary, ${sections.join(', ')}.
Audience: ${audience}.
Return exactly these section headers where applicable: ${requiredSectionOrder.join(', ')}.`
}

export async function generateReport(input: GenerateReportInput): Promise<string> {
  const result = await callLLM([
    { role: 'system', content: systemPrompt },
    { role: 'user', content: buildUserPrompt(input) },
  ])
  return result.text
}

export function parseReport(text: string): ReportSection[] {
  const sections: ReportSection[] = []
  const matches = [...text.matchAll(/(^|\n)([A-Z][A-Z\s/&-]{3,}):\s*/g)]

  if (matches.length === 0) {
    return [{ title: 'EXECUTIVE SUMMARY', body: text.trim() }]
  }

  matches.forEach((match, index) => {
    const title = match[2].trim()
    const start = (match.index ?? 0) + match[0].length
    const end = index + 1 < matches.length ? matches[index + 1].index ?? text.length : text.length
    const body = text.slice(start, end).trim()

    if (body) {
      sections.push({ title, body })
    }
  })

  return sections
}

export function buildFallbackReport({ incident, audience }: GenerateReportInput) {
  const reachedPercent = ((incident.civiliansReached / incident.civiliansAtRisk) * 100).toFixed(1)

  return `EXECUTIVE SUMMARY:
The ${incident.disaster} response in ${incident.location} during ${incident.month} was managed as a ${incident.durationHours}-hour multi-agency operation under state disaster management protocols. DisasterMind supported command staff across ${incident.zones} operational zones with ${incident.teamsDeployed} NDRF teams deployed for evacuation, relief routing, and field coordination. The system recorded ${incident.autonomousDecisions.toLocaleString('en-IN')} autonomous recommendations with ${incident.humanOverrides} commander overrides, indicating high automation utility with retained human command authority. A total of ${incident.civiliansReached.toLocaleString('en-IN')} civilians were reached from an estimated ${incident.civiliansAtRisk.toLocaleString('en-IN')} at-risk population, representing ${reachedPercent}% coverage for the ${audience} review.

EVENT TIMELINE:
The event was monitored from initial IMD and state control room alerts through escalation, field deployment, and stabilisation. The first phase prioritised vulnerable gram panchayats, low-lying habitations, and transport chokepoints identified by the district emergency operations centre. During peak impact, NDRF field teams, Odisha Disaster Rapid Action Force units, and district magistrate offices received prioritised tasking from the coordination layer. The final phase focused on shelter occupancy, medical triage, restoration of access routes, and closure of high-risk rescue calls.

AUTONOMOUS DECISIONS SUMMARY:
DisasterMind issued ${incident.autonomousDecisions.toLocaleString('en-IN')} autonomous decisions across flood-risk estimation, resource allocation, evacuation routing, and inter-team coordination. The highest-volume recommendations involved routing relief convoys around blocked roads and reassigning teams to higher-risk census clusters. Agent decisions were constrained by commander approval thresholds for evacuation orders, medical priority changes, and cross-district resource movements. The decision log indicates that automated prioritisation reduced manual queueing during the most active response windows.

HUMAN OVERRIDE LOG:
Commanders registered ${incident.humanOverrides} human overrides, primarily where ground intelligence contradicted remote sensing or where local administrative constraints required adjustment. Overrides included redirecting assets to newly reported breach points, delaying movement through unstable road corridors, and prioritising elderly residents in mixed-risk shelters. Each override was logged with timestamp, authorising officer, operational rationale, and downstream effect. The low override ratio indicates acceptable alignment between AI recommendations and field command judgement.

RESOURCE UTILISATION:
The response deployed ${incident.teamsDeployed} NDRF teams across ${incident.zones} zones with supporting boats, medical units, high-clearance vehicles, and district logistics staff. DisasterMind tracked asset saturation, response time, and handoff status between staging areas and field sectors. Resource utilisation was strongest in zones with pre-mapped shelter routes and weakest where communications degraded during peak weather. Future planning should increase reserve transport and satellite communication kits for prolonged night operations.

POPULATION OUTCOMES:
The operation reached ${incident.civiliansReached.toLocaleString('en-IN')} civilians from ${incident.civiliansAtRisk.toLocaleString('en-IN')} assessed at risk, achieving ${reachedPercent}% coverage. The remaining gap was concentrated in dispersed settlements, cut-off hamlets, and locations with delayed road access. Shelter reporting showed improved intake visibility and faster escalation for medical cases compared with manual-only reporting. No autonomous action bypassed civil authority; evacuation and shelter decisions remained aligned with district administration protocols.

MODEL PERFORMANCE:
Model performance was strongest in resource triage, route-risk scoring, and detection of high-density vulnerable areas. False positives were primarily associated with outdated road availability and duplicated field reports during communication recovery periods. Human override patterns show that commander feedback improved agent recommendations during later phases of the incident. Continued calibration against IMD warnings, CWC river gauge data, and district-level incident logs is recommended.

RECOMMENDATIONS:
NDMA and SDMA stakeholders should retain human approval gates for evacuation directives while expanding AI-assisted queue prioritisation for resource allocation. District control rooms should maintain cleaner baseline data for shelters, boat launch points, vulnerable household lists, and road closure feeds. The audit trail should be reviewed within 30 days by the state emergency operations centre and shared with authorised training teams. Future deployments should include periodic offline-mode exercises and additional satellite-backed communication capacity.`
}
