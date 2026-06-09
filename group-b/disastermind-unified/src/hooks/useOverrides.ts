import { useState } from 'react'
import type { OverrideRecord } from '../lib/mapTypes'

export function useOverrides() {
  const [overrides, setOverrides] = useState<OverrideRecord[]>([])

  function submitOverride(
    agentDecisionId: string,
    agentType: string,
    originalAction: string,
    reason: string,
  ): OverrideRecord {
    // Determine which agents get propagation notice based on agent type
    const propagationMap: Record<string, string[]> = {
      'FLOOD-AI':     ['RESOURCE-AI', 'ROUTING-AI', 'FIELD-COORD-AI'],
      'RESOURCE-AI':  ['ROUTING-AI', 'FIELD-COORD-AI'],
      'ROUTING-AI':   ['FIELD-COORD-AI'],
      'COMMANDER-AI': ['FLOOD-AI', 'RESOURCE-AI', 'ROUTING-AI', 'FIELD-COORD-AI'],
      'SHELTER-AI':   ['RESOURCE-AI', 'FIELD-COORD-AI'],
    }

    const record: OverrideRecord = {
      id: `override-${Date.now()}`,
      agentDecisionId,
      agentType,
      originalAction,
      overrideReason: reason,
      commanderId: 'CDR-SOHAM',
      timestamp: Date.now(),
      propagatedTo: propagationMap[agentType] ?? ['COMMANDER-AI'],
    }

    setOverrides(prev => [record, ...prev])
    return record
  }

  return { overrides, submitOverride }
}
