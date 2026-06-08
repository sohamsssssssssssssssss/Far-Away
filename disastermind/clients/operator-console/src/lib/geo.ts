// Defensive geo extraction for the map view.
//
// The backend's dispatch/field-order payloads are not strictly typed; an order
// may carry `waypoints: [{lat, lon}]` plus a `site`/`target_cell`, OR a plain
// lat/lon somewhere in the payload. We dig through whatever we get and surface
// anything plottable, so the operator always sees what we DO know.

import type { Message } from "../api/types";
import { PRIORITY_LABEL } from "../api/types";

export interface LatLon {
  lat: number;
  lon: number;
}

export interface PlottedOrder {
  /** Stable key for React lists. */
  key: string;
  /** Source message id. */
  messageId: string;
  topic: string;
  priority: number;
  priorityLabel: string;
  incidentId: string | null;
  /** Free-text site / target cell label if present. */
  site: string | null;
  team: string | null;
  reason: string | null;
  /** Ordered route waypoints (>=1). The last point is the destination. */
  waypoints: LatLon[];
}

function num(v: unknown): number | null {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string" && v.trim() !== "") {
    const n = Number(v);
    if (Number.isFinite(n)) return n;
  }
  return null;
}

function plausible(p: LatLon): boolean {
  return (
    p.lat >= -90 &&
    p.lat <= 90 &&
    p.lon >= -180 &&
    p.lon <= 180 &&
    // reject the (0,0) null-island artifact that often means "missing"
    !(p.lat === 0 && p.lon === 0)
  );
}

/** Pull a {lat, lon} out of an object that may use lat/lon, latitude/longitude, or lng. */
function asLatLon(obj: unknown): LatLon | null {
  if (!obj || typeof obj !== "object") return null;
  const o = obj as Record<string, unknown>;
  const lat = num(o.lat ?? o.latitude);
  const lon = num(o.lon ?? o.lng ?? o.longitude);
  if (lat === null || lon === null) return null;
  const p = { lat, lon };
  return plausible(p) ? p : null;
}

/** Extract waypoints from anything array-like that holds lat/lon points. */
function asWaypoints(value: unknown): LatLon[] {
  if (!Array.isArray(value)) return [];
  const out: LatLon[] = [];
  for (const item of value) {
    const p = asLatLon(item);
    if (p) out.push(p);
  }
  return out;
}

function str(v: unknown): string | null {
  return typeof v === "string" && v.length ? v : null;
}

/**
 * Turn one bus message into zero or more plottable orders.
 *
 * Handles, in priority order:
 *   1. field_order payloads: payload.orders[] each with waypoints[]/site.
 *   2. a single payload.order with waypoints[].
 *   3. payload.waypoints / payload.route at the top level.
 *   4. any single lat/lon found on the payload (or payload.location/centroid/site).
 */
export function ordersFromMessage(m: Message): PlottedOrder[] {
  const payload = (m.payload ?? {}) as Record<string, unknown>;
  const priorityLabel = PRIORITY_LABEL[m.priority] ?? String(m.priority);
  const base = {
    messageId: m.id,
    topic: m.topic,
    priority: m.priority,
    priorityLabel,
    incidentId: m.incident_id,
  };
  const out: PlottedOrder[] = [];

  const pushOrder = (o: Record<string, unknown>, idx: number) => {
    let wps = asWaypoints(o.waypoints ?? o.route ?? o.path);
    const single = asLatLon(o.location ?? o.centroid ?? o.site ?? o);
    if (!wps.length && single) wps = [single];
    if (!wps.length) return;
    out.push({
      ...base,
      key: `${m.id}:${idx}`,
      site:
        str(o.site) ?? str(o.target_cell) ?? str((o as { name?: unknown }).name),
      team: str(o.team_id) ?? str((o as { team?: unknown }).team),
      reason: str(o.reason),
      waypoints: wps,
    });
  };

  // 1. field_order: payload.orders[]
  const orders = payload.orders;
  if (Array.isArray(orders)) {
    orders.forEach((o, i) => {
      if (o && typeof o === "object") pushOrder(o as Record<string, unknown>, i);
    });
  }

  // 2. single payload.order
  if (payload.order && typeof payload.order === "object") {
    pushOrder(payload.order as Record<string, unknown>, out.length);
  }

  // 3 / 4. top-level waypoints / single point on the payload itself.
  if (!out.length) {
    pushOrder(payload, 0);
  }

  return out;
}

/** Aggregate all plottable orders from a list of messages (newest first kept). */
export function ordersFromMessages(messages: Message[]): PlottedOrder[] {
  const out: PlottedOrder[] = [];
  for (const m of messages) out.push(...ordersFromMessage(m));
  return out;
}

/** A single representative point for an order (its destination). */
export function orderAnchor(o: PlottedOrder): LatLon {
  return o.waypoints[o.waypoints.length - 1];
}
