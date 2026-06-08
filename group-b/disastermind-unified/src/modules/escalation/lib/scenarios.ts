export type Scenario = {
  id: string
  label: string
  type: string
  context: string
  prompt: string
}

export const scenarios: Scenario[] = [
  {
    id: 'zone-7-evacuation',
    label: 'MANDATORY EVACUATION - ZONE 7',
    type: 'MANDATORY EVACUATION ORDER',
    context: '14,200 residents, high inundation, 2hr window',
    prompt:
      'Generate an escalation memo for: Mandatory evacuation order for Zone 7, Odisha. 14,200 residents at risk. Inundation projected within 2 hours. River gauge at 94% danger level. FLOOD-AI confidence: 91%.',
  },
  {
    id: 'cross-state-boats',
    label: 'CROSS-STATE RESOURCE REQUEST',
    type: 'CROSS-STATE RESOURCE REQUEST',
    context: '8-boat deficit, request to Andhra Pradesh NDRF',
    prompt:
      'Generate an escalation memo for: Cross-state resource request from Odisha to Andhra Pradesh NDRF. DisasterMind projects an 8-boat deficit within 2 hours across Puri and Ganjam sectors. Current NDRF boat utilization is 89%, evacuation demand is rising, and road access is partially blocked.',
  },
  {
    id: 'military-helicopter',
    label: 'MILITARY ASSET DEPLOYMENT',
    type: 'MILITARY ASSET DEPLOYMENT',
    context: 'IAF helicopter request for cliff rescue, Uttarakhand',
    prompt:
      'Generate an escalation memo for: Military asset deployment request in Uttarakhand. 23 civilians are stranded above a landslide-cut road near Joshimath, cliff rescue access is unsafe, and NDRF requests IAF helicopter lift support before nightfall.',
  },
  {
    id: 'puri-hotels',
    label: 'PRIVATE INFRASTRUCTURE REQUISITION',
    type: 'PRIVATE INFRASTRUCTURE REQUISITION',
    context: '3 hotels in Puri to be converted to emergency shelters',
    prompt:
      'Generate an escalation memo for: Private infrastructure requisition in Puri, Odisha. Three hotels near Grand Road can provide 1,100 temporary shelter beds, but conversion requires state authority approval and police support as public shelters are projected to exceed 92% capacity.',
  },
  {
    id: 'odisha-broadcast',
    label: 'MEDIA BROADCAST ORDER',
    type: 'MEDIA BROADCAST ORDER',
    context: 'Emergency alert to 2.3 million mobile users, Odisha',
    prompt:
      'Generate an escalation memo for: Emergency media broadcast order for Odisha coastal districts. A cell broadcast alert must reach 2.3 million mobile users in Puri, Balasore, Bhadrak, and Kendrapara. Cyclone landfall guidance changed and evacuation timing has moved forward by 90 minutes.',
  },
]
