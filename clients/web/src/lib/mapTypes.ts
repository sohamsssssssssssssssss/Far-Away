// ============================================================
// Group A WebSocket contract — map-relevant payload shapes
// Source: disastermind/api/FRONTEND_CONTRACT.md
// ============================================================

export interface GpsReading {
  location: { lat: number; lon: number }
  team_id: string
  status: 'active' | 'staged' | 'distress' | 'offline'
  timestamp: string
}

export interface RiskCell {
  centroid: { lat: number; lon: number }
  probability: number      // 0.0 – 1.0
  cell_id: string
  zone?: string
}

export interface Waypoint {
  lat: number
  lon: number
}

export interface DispatchRoute {
  team_id: string
  waypoints: Waypoint[]
  route_type: 'evacuation' | 'rescue' | 'supply'
}

export interface MapState {
  teams: Record<string, GpsReading>        // keyed by team_id
  riskCells: RiskCell[]
  routes: DispatchRoute[]
}

// ── SHAP feature explanations ─────────────────────────────────────────────────
export interface ShapFeature {
  label: string          // e.g. "River gauge 94%"
  value: number          // raw SHAP value (positive = increases risk)
  direction: 'up' | 'down'  // up = increases risk/urgency, down = decreases
}

export interface AgentDecisionShap {
  topFeatures: ShapFeature[]   // top 3 features only
  modelConfidence: number      // 0-1
}

// Synthetic SHAP data per agent type — used when Group A is offline
export const SYNTHETIC_SHAP: Record<string, AgentDecisionShap> = {
  'FLOOD-AI': {
    modelConfidence: 0.92,
    topFeatures: [
      { label: 'River gauge 94%', value: 0.38, direction: 'up' },
      { label: 'Rainfall 187mm/48h', value: 0.29, direction: 'up' },
      { label: 'Elevation 1.2m', value: 0.21, direction: 'up' },
    ],
  },
  'RESOURCE-AI': {
    modelConfidence: 0.87,
    topFeatures: [
      { label: 'Shelter at 88%', value: 0.31, direction: 'up' },
      { label: 'Boats available 6', value: 0.24, direction: 'down' },
      { label: 'ETA 14 min', value: 0.19, direction: 'up' },
    ],
  },
  'EVAC-AI': {
    modelConfidence: 0.89,
    topFeatures: [
      { label: 'Population at risk 14.2k', value: 0.42, direction: 'up' },
      { label: 'Route capacity 85%', value: 0.27, direction: 'down' },
      { label: 'Storm surge 2.1m', value: 0.33, direction: 'up' },
    ],
  },
  'COORD-AI': {
    modelConfidence: 0.84,
    topFeatures: [
      { label: 'Team proximity 2.1km', value: 0.28, direction: 'down' },
      { label: 'Zone 7 priority HIGH', value: 0.35, direction: 'up' },
      { label: 'Comms signal 72%', value: 0.18, direction: 'down' },
    ],
  },
  'ALERT-AI': {
    modelConfidence: 0.91,
    topFeatures: [
      { label: 'IMD alert Category 3', value: 0.44, direction: 'up' },
      { label: 'Landfall T-6h', value: 0.38, direction: 'up' },
      { label: 'Pop density HIGH', value: 0.29, direction: 'up' },
    ],
  },
}

// Fallback for unknown agent types
export const DEFAULT_SHAP: AgentDecisionShap = {
  modelConfidence: 0.80,
  topFeatures: [
    { label: 'Risk score 0.87', value: 0.35, direction: 'up' },
    { label: 'Confidence HIGH', value: 0.28, direction: 'up' },
    { label: 'Priority CRITICAL', value: 0.22, direction: 'up' },
  ],
}

// ── Escalation types ───────────────────────────────────────────────────────────
export type EscalationTrigger =
  | 'CROSS_STATE_RESOURCE'
  | 'MILITARY_ASSET'
  | 'MANDATORY_EVACUATION'
  | 'REQUISITION_INFRASTRUCTURE'
  | 'MEDIA_BROADCAST'
  | 'INTERNATIONAL_AID'
  | 'STATE_OF_EMERGENCY'
  | 'ARMED_FORCES'
  | 'CRITICAL_INFRASTRUCTURE';

// Human-only triggers — no auto-execute, no timeout
export const HUMAN_ONLY_TRIGGERS: EscalationTrigger[] = [
  'INTERNATIONAL_AID',
  'STATE_OF_EMERGENCY',
  'ARMED_FORCES',
  'CRITICAL_INFRASTRUCTURE',
];

export interface EscalationMemo {
  situation: string;      // 2-sentence summary
  recommended: string;    // 1-sentence action
  riskIfYes: string;      // 1-sentence consequence
  riskIfNo: string;       // 1-sentence consequence
}

export interface EscalationItem {
  id: string;
  trigger: EscalationTrigger;
  zone: string;
  priority: 'CRITICAL' | 'HIGH' | 'MEDIUM';
  memo: EscalationMemo;
  createdAt: number;       // Date.now() timestamp
  timeoutMs: number;       // default 300000 (5 min); Infinity for HUMAN_ONLY
  status: 'PENDING' | 'APPROVED' | 'OVERRIDDEN' | 'AUTO_EXECUTED';
  overrideReason?: string;
  resolvedAt?: number;
}

// ── EvacRoute shelter (from WebSocket tier2.routing_plan payload) ───────────
export interface EvacRouteShelter {
  shelter_id: string
  name: string
  location: { lat: number; lon: number }
  capacity: number
  current_occupancy?: number
  status: 'open' | 'full' | 'closed'
}

// ── Shelter database ──────────────────────────────────────────────────────────
export interface Shelter {
  id: string
  name: string
  district: string
  lat: number
  lon: number
  capacity: number
  occupied: number
  status: 'OPEN' | 'FULL' | 'CLOSED'
  facilities: string[]   // e.g. ['medical', 'food', 'water']
}

// ── IMD Alert zone ────────────────────────────────────────────────────────────
export interface IMDAlert {
  id: string
  type: 'CYCLONE' | 'FLOOD' | 'STORM_SURGE' | 'HEAVY_RAIN'
  severity: 'RED' | 'ORANGE' | 'YELLOW'
  district: string
  lat: number
  lon: number
  radiusKm: number
  headline: string
  issuedAt: string   // ISO timestamp
  validUntil: string
}

// ── Static Odisha shelter data (IDRN-style) ───────────────────────────────────
// Replace with: fetch('https://far-away-production.up.railway.app/api/shelters')
// when Group A confirms the endpoint
export const ODISHA_SHELTERS: Shelter[] = [
  {
    id: 'SH-001',
    name: 'Paradip Cyclone Shelter',
    district: 'Jagatsinghpur',
    lat: 20.316, lon: 86.611,
    capacity: 2500, occupied: 2180,
    status: 'OPEN',
    facilities: ['medical', 'food', 'water', 'power'],
  },
  {
    id: 'SH-002',
    name: 'Kendrapara Multipurpose Shelter',
    district: 'Kendrapara',
    lat: 20.501, lon: 86.421,
    capacity: 1800, occupied: 1800,
    status: 'FULL',
    facilities: ['food', 'water'],
  },
  {
    id: 'SH-003',
    name: 'Ersama Block Shelter',
    district: 'Jagatsinghpur',
    lat: 20.196, lon: 86.372,
    capacity: 1200, occupied: 640,
    status: 'OPEN',
    facilities: ['medical', 'food', 'water'],
  },
  {
    id: 'SH-004',
    name: 'Mahakalapara Coastal Shelter',
    district: 'Kendrapara',
    lat: 20.393, lon: 86.913,
    capacity: 900, occupied: 720,
    status: 'OPEN',
    facilities: ['food', 'water'],
  },
  {
    id: 'SH-005',
    name: 'Rajnagar Mangrove Shelter',
    district: 'Kendrapara',
    lat: 20.447, lon: 86.778,
    capacity: 600, occupied: 0,
    status: 'CLOSED',
    facilities: ['water'],
  },
  {
    id: 'SH-006',
    name: 'Astarang Panchayat Shelter',
    district: 'Puri',
    lat: 19.937, lon: 86.148,
    capacity: 1500, occupied: 890,
    status: 'OPEN',
    facilities: ['medical', 'food', 'water', 'power'],
  },
  {
    id: 'SH-007',
    name: 'Pentha Coastal Shelter',
    district: 'Jagatsinghpur',
    lat: 20.142, lon: 86.703,
    capacity: 800, occupied: 560,
    status: 'OPEN',
    facilities: ['food', 'water'],
  },
  {
    id: 'SH-008',
    name: 'Bhubaneswar Relief Camp A',
    district: 'Khordha',
    lat: 20.296, lon: 85.824,
    capacity: 3000, occupied: 1200,
    status: 'OPEN',
    facilities: ['medical', 'food', 'water', 'power', 'comms'],
  },
]

// ── Static IMD alert zones ────────────────────────────────────────────────────
// Replace with: fetch('https://far-away-production.up.railway.app/api/alerts/imd')
// when Group A confirms the endpoint
export const IMD_ALERTS: IMDAlert[] = [
  {
    id: 'IMD-001',
    type: 'CYCLONE',
    severity: 'RED',
    district: 'Jagatsinghpur',
    lat: 20.27, lon: 86.55,
    radiusKm: 45,
    headline: 'Cyclone Remal — Landfall imminent, T-6h. Winds 165 kmph.',
    issuedAt: new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString(),
    validUntil: new Date(Date.now() + 12 * 60 * 60 * 1000).toISOString(),
  },
  {
    id: 'IMD-002',
    type: 'STORM_SURGE',
    severity: 'RED',
    district: 'Kendrapara',
    lat: 20.50, lon: 86.72,
    radiusKm: 30,
    headline: 'Storm surge warning — 2.1m above normal tide. Evacuate coastal belt immediately.',
    issuedAt: new Date(Date.now() - 1 * 60 * 60 * 1000).toISOString(),
    validUntil: new Date(Date.now() + 10 * 60 * 60 * 1000).toISOString(),
  },
  {
    id: 'IMD-003',
    type: 'FLOOD',
    severity: 'ORANGE',
    district: 'Cuttack',
    lat: 20.462, lon: 85.883,
    radiusKm: 25,
    headline: 'Mahanadi in spate — gauge at 91.2%. Flash flood risk HIGH.',
    issuedAt: new Date(Date.now() - 3 * 60 * 60 * 1000).toISOString(),
    validUntil: new Date(Date.now() + 18 * 60 * 60 * 1000).toISOString(),
  },
  {
    id: 'IMD-004',
    type: 'HEAVY_RAIN',
    severity: 'YELLOW',
    district: 'Puri',
    lat: 19.81, lon: 85.83,
    radiusKm: 20,
    headline: 'Extremely heavy rainfall expected — 187mm/24h. Low-lying areas at risk.',
    issuedAt: new Date(Date.now() - 4 * 60 * 60 * 1000).toISOString(),
    validUntil: new Date(Date.now() + 24 * 60 * 60 * 1000).toISOString(),
  },
]

// ── Override Records ─────────────────────────────────────────────────────────
export interface OverrideRecord {
  id: string;                  // override-[timestamp]
  agentDecisionId: string;     // id of the AgentDecision being overridden
  agentType: string;           // e.g. 'FLOOD-AI'
  originalAction: string;      // the decision text that was overridden
  overrideReason: string;      // commander's entered reason
  commanderId: string;         // hardcoded 'CDR-SOHAM' for demo
  timestamp: number;           // Date.now()
  propagatedTo: string[];      // list of dependent agent types notified
}

// Synthetic fallback data — Odisha coast (Puri / Balasore / Cuttack)
export const SYNTHETIC_MAP_STATE: MapState = {
  teams: {
    'UNIT-A1': {
      team_id: 'UNIT-A1',
      location: { lat: 19.8135, lon: 85.8312 },
      status: 'active',
      timestamp: new Date().toISOString(),
    },
    'UNIT-A2': {
      team_id: 'UNIT-A2',
      location: { lat: 19.7950, lon: 85.8150 },
      status: 'active',
      timestamp: new Date().toISOString(),
    },
    'UNIT-B1': {
      team_id: 'UNIT-B1',
      location: { lat: 21.4942, lon: 86.9304 },
      status: 'staged',
      timestamp: new Date().toISOString(),
    },
    'UNIT-B2': {
      team_id: 'UNIT-B2',
      location: { lat: 21.5200, lon: 86.9100 },
      status: 'active',
      timestamp: new Date().toISOString(),
    },
    'UNIT-C1': {
      team_id: 'UNIT-C1',
      location: { lat: 20.4625, lon: 85.8828 },
      status: 'active',
      timestamp: new Date().toISOString(),
    },
  },
  riskCells: [
    { centroid: { lat: 19.82, lon: 85.83 }, probability: 0.92, cell_id: 'puri-1', zone: 'Zone 1' },
    { centroid: { lat: 19.79, lon: 85.80 }, probability: 0.85, cell_id: 'puri-2', zone: 'Zone 2' },
    { centroid: { lat: 19.75, lon: 85.77 }, probability: 0.78, cell_id: 'puri-3', zone: 'Zone 3' },
    { centroid: { lat: 21.50, lon: 86.93 }, probability: 0.71, cell_id: 'balasore-1', zone: 'Zone 4' },
    { centroid: { lat: 21.48, lon: 86.91 }, probability: 0.65, cell_id: 'balasore-2', zone: 'Zone 5' },
    { centroid: { lat: 20.45, lon: 85.88 }, probability: 0.55, cell_id: 'cuttack-1', zone: 'Zone 6' },
    { centroid: { lat: 20.47, lon: 85.90 }, probability: 0.88, cell_id: 'cuttack-2', zone: 'Zone 7' },
  ],
  routes: [
    {
      team_id: 'UNIT-A1',
      route_type: 'evacuation',
      waypoints: [
        { lat: 19.8135, lon: 85.8312 },
        { lat: 19.8200, lon: 85.8400 },
        { lat: 19.8300, lon: 85.8500 },
      ],
    },
    {
      team_id: 'UNIT-B2',
      route_type: 'rescue',
      waypoints: [
        { lat: 21.5200, lon: 86.9100 },
        { lat: 21.5100, lon: 86.9200 },
        { lat: 21.5000, lon: 86.9300 },
      ],
    },
  ],
}
