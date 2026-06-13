import { describe, it, expect, vi, beforeEach } from 'vitest'

// Mock the LLM layer so this is a pure, offline test of prompt construction and
// the structured return — no network, no model. vi.hoisted keeps the spy defined
// before the hoisted vi.mock factory runs, and types it (string, string) so the
// recorded calls are correctly typed below.
const { promptLLM } = vi.hoisted(() => ({
  promptLLM: vi.fn(async (_system: string, _user: string) => 'GENERATED BRIEFING'),
}))
vi.mock('./llm', () => ({ promptLLM }))

import { generateSituationBriefing } from './situationBriefing'

describe('generateSituationBriefing', () => {
  beforeEach(() => promptLLM.mockClear())

  it('returns the model text plus a timestamp and the context echoed back', async () => {
    const ctx = { topRiskZone: 'Zone 9', topRiskProbability: 0.81 }
    const res = await generateSituationBriefing(ctx)
    expect(res.text).toBe('GENERATED BRIEFING')
    expect(res.context).toEqual(ctx)
    expect(() => new Date(res.generatedAt).toISOString()).not.toThrow()
  })

  it('weaves the context values into the user prompt', async () => {
    await generateSituationBriefing({
      activeZones: ['Zone A', 'Zone B'],
      topRiskZone: 'Zone A',
      topRiskProbability: 0.92,
      deployedBoats: 7,
    })
    const [, userMessage] = promptLLM.mock.calls[0]
    expect(userMessage).toContain('Zone A, Zone B')
    expect(userMessage).toContain('7 boats')
    // probability rendered as a rounded percentage
    expect(userMessage).toContain('92%')
  })

  it('uses sensible defaults when called with no context', async () => {
    const res = await generateSituationBriefing()
    expect(promptLLM).toHaveBeenCalledOnce()
    const [systemPrompt] = promptLLM.mock.calls[0]
    expect(systemPrompt.toLowerCase()).toContain('briefing')
    expect(res.text).toBe('GENERATED BRIEFING')
  })
})
