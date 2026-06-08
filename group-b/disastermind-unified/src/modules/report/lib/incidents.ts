export type Incident = {
  id: string
  title: string
  disaster: string
  location: string
  month: string
  durationHours: number
  zones: number
  teamsDeployed: number
  autonomousDecisions: number
  humanOverrides: number
  civiliansReached: number
  civiliansAtRisk: number
}

export const incidents: Incident[] = [
  {
    id: 'remal-odisha-2026',
    title: 'Cyclone Remal - Odisha, May 2026',
    disaster: 'Cyclone Remal',
    location: 'Odisha',
    month: 'May 2026',
    durationHours: 72,
    zones: 8,
    teamsDeployed: 24,
    autonomousDecisions: 847,
    humanOverrides: 12,
    civiliansReached: 94200,
    civiliansAtRisk: 102000,
  },
  {
    id: 'assam-floods-2026',
    title: 'Floods - Assam Valley, June 2026',
    disaster: 'Floods',
    location: 'Assam Valley',
    month: 'June 2026',
    durationHours: 48,
    zones: 5,
    teamsDeployed: 16,
    autonomousDecisions: 412,
    humanOverrides: 8,
    civiliansReached: 31500,
    civiliansAtRisk: 37800,
  },
  {
    id: 'manipur-earthquake-2026',
    title: 'Earthquake - Manipur, April 2026',
    disaster: 'Earthquake',
    location: 'Manipur',
    month: 'April 2026',
    durationHours: 96,
    zones: 12,
    teamsDeployed: 31,
    autonomousDecisions: 1203,
    humanOverrides: 19,
    civiliansReached: 58700,
    civiliansAtRisk: 64800,
  },
]

export const reportSections = [
  'Event Timeline',
  'Autonomous Decision Log',
  'Human Override Analysis',
  'Resource Utilisation Summary',
  'Population Outcomes',
  'Model Performance Metrics',
  'Recommendations',
]

export const audiences = [
  {
    id: 'NDMA',
    label: 'NDMA',
    detail: 'National Disaster Management Authority',
  },
  {
    id: 'SDMA',
    label: 'SDMA',
    detail: 'State Disaster Management Authority',
  },
  {
    id: 'Review Board',
    label: 'Review Board',
    detail: 'full technical detail',
  },
  {
    id: 'Academic / Research',
    label: 'Academic / Research',
    detail: 'anonymised',
  },
]
