import type { EscalationItem } from '../lib/mapTypes'

export const MOCK_ESCALATIONS: EscalationItem[] = [
  {
    id: 'ESC-001',
    trigger: 'MANDATORY_EVACUATION',
    zone: 'Zone 7 — Kendrapara Coast',
    priority: 'CRITICAL',
    memo: {
      situation:
        'River gauge at 94% danger level with 187mm rainfall in 48h. Inundation model projects Zone 7 flooding within 90 minutes at 92% confidence.',
      recommended:
        'Immediately issue mandatory evacuation order for 14,200 residents in Zone 7 low-lying areas.',
      riskIfYes:
        'Traffic congestion on NH-16 may slow evacuation; nearest shelter at 8.2km has 73% capacity.',
      riskIfNo:
        'Projected 2.1m inundation in 90 minutes places 14,200 residents at direct life risk.',
    },
    createdAt: Date.now() - 60000,
    timeoutMs: 300000,
    status: 'PENDING',
  },
  {
    id: 'ESC-002',
    trigger: 'CROSS_STATE_RESOURCE',
    zone: 'Zone 3 — Jagatsinghpur District',
    priority: 'HIGH',
    memo: {
      situation:
        'All 12 ODRAF boats deployed. Zone 3 requires 4 additional rescue boats for 340 stranded residents across 6 villages.',
      recommended:
        'Request 4 NDRF boats from Andhra Pradesh standby pool via cross-state mutual aid protocol.',
      riskIfYes:
        'AP boats have 3-hour ETA; interim gap must be covered by helicopter sorties.',
      riskIfNo:
        '340 residents remain stranded with water levels rising at 12cm/hour.',
    },
    createdAt: Date.now() - 120000,
    timeoutMs: 300000,
    status: 'PENDING',
  },
  {
    id: 'ESC-003',
    trigger: 'REQUISITION_INFRASTRUCTURE',
    zone: 'Zone 5 — Puri Urban',
    priority: 'HIGH',
    memo: {
      situation:
        'Government shelter capacity exhausted at 98%. 1,840 displaced persons require immediate shelter placement.',
      recommended:
        'Requisition Hotel Grand Puri (420 rooms) and DAV School (capacity 800) as temporary relief centres.',
      riskIfYes:
        'Compensation claims from hotel owner likely; school requires 6-hour setup before occupancy.',
      riskIfNo:
        '1,840 displaced persons have no shelter assignment with night temperatures dropping to 22°C.',
    },
    createdAt: Date.now() - 30000,
    timeoutMs: 300000,
    status: 'PENDING',
  },
  {
    id: 'ESC-004',
    trigger: 'STATE_OF_EMERGENCY',
    zone: 'Odisha — State Level',
    priority: 'CRITICAL',
    memo: {
      situation:
        'Cyclone Remal has made landfall. 4 districts affected, 38,000 residents at risk, central government NDRF activation threshold met.',
      recommended:
        'Declare State of Emergency under Disaster Management Act 2005 to unlock central government funding and NDRF battalions.',
      riskIfYes:
        'Declaration triggers mandatory media reporting and may cause public panic if not accompanied by clear communication.',
      riskIfNo:
        'Central government funding and additional NDRF battalions cannot be activated without formal emergency declaration.',
    },
    createdAt: Date.now() - 180000,
    timeoutMs: Infinity,  // HUMAN_ONLY — never auto-executes
    status: 'PENDING',
  },
]
