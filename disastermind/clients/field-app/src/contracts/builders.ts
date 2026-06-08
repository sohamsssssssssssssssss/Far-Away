/**
 * Builder helpers that produce the EXACT wire JSON the backbone consumes.
 *
 * Each builder mirrors a Python dataclass constructor + (where applicable) its
 * `to_reading()` method from `disastermind/fieldapp/contracts.py`. The
 * `*Reading` builders reproduce the IoT-telemetry reading shapes the Tier-2
 * field coordinator ingests; `buildOrderAck` reproduces the OrderAck published
 * on the `fieldapp.order_ack` topic.
 */
import {
  AckStatus,
  AssetType,
  DeploymentOrderMsg,
  FIELDAPP_ACK,
  LatLon,
  OrderAck,
  SiteOverCapacityReading,
  SiteOverCapacityReport,
  Status,
  TeamStatusReading,
  TeamStatusUpdate,
} from './types';

/** ISO 8601 timestamp in UTC — mirrors `core.contracts.utcnow_iso()`. */
export function utcnowIso(): string {
  return new Date().toISOString();
}

// --------------------------------------------------------------- DeploymentOrderMsg

/**
 * Project a raw dispatch/field-order dict onto the device contract.
 * Mirrors `DeploymentOrderMsg.from_payload`.
 */
export function deploymentOrderFromPayload(
  order: Record<string, unknown>,
  incidentId?: string | null,
): DeploymentOrderMsg {
  const num = (v: unknown, dflt: number): number => {
    const n = Number(v);
    return Number.isFinite(n) && n !== 0 ? n : v == null || v === '' ? dflt : n || dflt;
  };
  const waypointsRaw = Array.isArray(order.waypoints) ? order.waypoints : [];
  return {
    order_id: String(order.order_id ?? order.id ?? 'unknown'),
    team_id: String(order.team_id ?? 'unassigned'),
    site: String(order.site ?? order.target_cell ?? 'unknown'),
    priority: num(order.priority, 3),
    reason: String(order.reason ?? ''),
    waypoints: waypointsRaw.filter(
      (w): w is LatLon =>
        typeof w === 'object' &&
        w !== null &&
        'lat' in (w as object) &&
        'lon' in (w as object),
    ),
    channel: String(order.channel ?? 'terrestrial'),
    incident_id: (incidentId ?? (order.incident_id as string | undefined)) ?? null,
  };
}

// ----------------------------------------------------------------- TeamStatusUpdate

export interface BeaconArgs {
  teamId: string;
  assetType: AssetType;
  location: LatLon;
  status?: Status;
  assignment?: string | null;
  ts?: string;
}

/** Construct a `TeamStatusUpdate` (mirrors the dataclass). */
export function buildTeamStatusUpdate(args: BeaconArgs): TeamStatusUpdate {
  return {
    team_id: args.teamId,
    asset_type: args.assetType,
    location: args.location,
    status: args.status ?? 'idle',
    assignment: args.assignment ?? null,
    ts: args.ts ?? utcnowIso(),
  };
}

/** Render the beacon reading — mirrors `TeamStatusUpdate.to_reading()`. */
export function teamStatusToReading(u: TeamStatusUpdate): TeamStatusReading {
  return {
    team_id: u.team_id,
    asset_type: u.asset_type,
    location: { lat: u.location.lat, lon: u.location.lon },
    status: u.status,
    assignment: u.assignment ?? null,
    ts: u.ts,
  };
}

// -------------------------------------------------------------------------- OrderAck

export interface OrderAckArgs {
  orderId: string;
  teamId: string;
  status: AckStatus;
  note?: string;
  incidentId?: string | null;
  ts?: string;
}

/** Construct an `OrderAck` (mirrors the dataclass). */
export function buildOrderAck(args: OrderAckArgs): OrderAck {
  return {
    order_id: args.orderId,
    team_id: args.teamId,
    status: args.status,
    note: args.note ?? '',
    incident_id: args.incidentId ?? null,
    ts: args.ts ?? utcnowIso(),
  };
}

// ------------------------------------------------------------- SiteOverCapacityReport

export interface OverCapacityArgs {
  teamId: string;
  site: string;
  shortfall?: number;
  note?: string;
  incidentId?: string | null;
  ts?: string;
}

/** Construct a `SiteOverCapacityReport` (mirrors the dataclass). */
export function buildSiteOverCapacityReport(
  args: OverCapacityArgs,
): SiteOverCapacityReport {
  return {
    team_id: args.teamId,
    site: args.site,
    shortfall: args.shortfall ?? 1,
    note: args.note ?? 'site over capacity',
    incident_id: args.incidentId ?? null,
    ts: args.ts ?? utcnowIso(),
  };
}

/**
 * Render the over-capacity reading — mirrors
 * `SiteOverCapacityReport.to_reading(location, asset_type)`.
 */
export function siteOverCapacityToReading(
  r: SiteOverCapacityReport,
  location: LatLon,
  assetType: AssetType,
): SiteOverCapacityReading {
  return {
    team_id: r.team_id,
    asset_type: assetType,
    location: { lat: location.lat, lon: location.lon },
    status: 'onsite',
    site: r.site,
    site_over_capacity: true,
    shortfall: r.shortfall,
    note: r.note,
    ts: r.ts,
  };
}

// ----------------------------------------------------------------- outbound envelopes

/**
 * The kinds of outbound messages the device emits. Each maps to a backbone
 * topic + the exact contract JSON payload the backbone expects.
 */
export type OutboundKind = 'order_ack' | 'gps_beacon' | 'over_capacity';

/**
 * A transport-agnostic outbound envelope. `topic` selects the backbone topic;
 * `body` is the exact contract JSON. For the two beacon kinds we wrap the
 * reading in the `{kind:"gps_beacon", readings:[...]}` telemetry frame the
 * coordinator's `_on_telemetry` consumes (see client.py).
 */
export interface OutboundMessage {
  /** Stable id for dedupe / FIFO ordering in the outbox. */
  id: string;
  kind: OutboundKind;
  /** Backbone topic this would be published on. */
  topic: string;
  /** The exact contract JSON body. */
  body: Record<string, unknown>;
  /** ISO timestamp the message was enqueued. */
  enqueuedAt: string;
}

let _seq = 0;
function nextId(prefix: string): string {
  _seq += 1;
  return `${prefix}-${Date.now().toString(36)}-${_seq}`;
}

/** Wrap an OrderAck for transport on the `fieldapp.order_ack` topic. */
export function orderAckEnvelope(ack: OrderAck): OutboundMessage {
  return {
    id: nextId('ack'),
    kind: 'order_ack',
    topic: FIELDAPP_ACK,
    body: {
      kind: 'order_ack',
      order_id: ack.order_id,
      team_id: ack.team_id,
      status: ack.status,
      note: ack.note,
      incident_id: ack.incident_id ?? null,
      ts: ack.ts,
    },
    enqueuedAt: utcnowIso(),
  };
}

/** Wrap a GPS beacon reading in the IoT telemetry frame for transport. */
export function gpsBeaconEnvelope(
  reading: TeamStatusReading,
): OutboundMessage {
  return {
    id: nextId('beacon'),
    kind: 'gps_beacon',
    topic: 'iot.telemetry',
    body: { kind: 'gps_beacon', readings: [reading] },
    enqueuedAt: utcnowIso(),
  };
}

/** Wrap an over-capacity reading in the IoT telemetry frame for transport. */
export function overCapacityEnvelope(
  reading: SiteOverCapacityReading,
): OutboundMessage {
  return {
    id: nextId('overcap'),
    kind: 'over_capacity',
    topic: 'iot.telemetry',
    body: { kind: 'gps_beacon', readings: [reading] },
    enqueuedAt: utcnowIso(),
  };
}
