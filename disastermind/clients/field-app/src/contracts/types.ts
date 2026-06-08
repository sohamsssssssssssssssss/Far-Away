/**
 * TypeScript mirror of the Python device contracts in
 * `disastermind/fieldapp/contracts.py`. These shapes are the wire format
 * between a field device and the coordination backbone (PRD Step 6 / 8).
 *
 * IMPORTANT: these types mirror the Python dataclasses AS-IS. Do not change
 * field names, defaults, or the `to_reading()` output shapes — the backbone
 * consumes them verbatim.
 */

/** Topic the device publishes explicit order acknowledgements on. */
export const FIELDAPP_ACK = 'fieldapp.order_ack' as const;

/** Status lifecycle a device walks through while servicing an order. */
export const STATUS_FLOW = ['idle', 'enroute', 'onsite', 'returning'] as const;
export type Status = (typeof STATUS_FLOW)[number];

/** Mirror of `disastermind.models.domain.AssetType` (str Enum values). */
export const ASSET_TYPES = [
  'boat',
  'helicopter',
  'ndrf_team',
  'sdrf_team',
  'medical_unit',
  'fire_engine',
  'usar_team',
] as const;
export type AssetType = (typeof ASSET_TYPES)[number];

/** Mirror of `disastermind.models.geo.LatLon` (the {lat, lon} payload form). */
export interface LatLon {
  lat: number;
  lon: number;
}

/** OrderAck.status values. */
export type AckStatus = 'accepted' | 'rejected' | 'completed';

/**
 * Backbone -> device deployment order.
 * Mirrors `DeploymentOrderMsg`.
 */
export interface DeploymentOrderMsg {
  order_id: string;
  team_id: string;
  site: string;
  priority: number; // default 3
  reason: string; // default ""
  waypoints: LatLon[]; // list[dict[str, float]]
  channel: string; // default "terrestrial"
  incident_id?: string | null;
}

/**
 * Device -> backbone GPS beacon (60s cadence).
 * Mirrors `TeamStatusUpdate`.
 */
export interface TeamStatusUpdate {
  team_id: string;
  asset_type: AssetType;
  location: LatLon;
  status: Status; // default "idle"
  assignment?: string | null;
  ts: string; // ISO 8601
}

/** The exact reading shape `TeamStatusUpdate.to_reading()` emits. */
export interface TeamStatusReading {
  team_id: string;
  asset_type: string;
  location: LatLon;
  status: Status;
  assignment: string | null;
  ts: string;
}

/**
 * Device -> backbone explicit order receipt.
 * Mirrors `OrderAck`.
 */
export interface OrderAck {
  order_id: string;
  team_id: string;
  status: AckStatus;
  note: string; // default ""
  incident_id?: string | null;
  ts: string; // ISO 8601
}

/**
 * Device -> backbone over-capacity report.
 * Mirrors `SiteOverCapacityReport`.
 */
export interface SiteOverCapacityReport {
  team_id: string;
  site: string;
  shortfall: number; // default 1
  note: string; // default "site over capacity"
  incident_id?: string | null;
  ts: string; // ISO 8601
}

/** The exact reading shape `SiteOverCapacityReport.to_reading()` emits. */
export interface SiteOverCapacityReading {
  team_id: string;
  asset_type: string;
  location: LatLon;
  status: 'onsite';
  site: string;
  site_over_capacity: true;
  shortfall: number;
  note: string;
  ts: string;
}
