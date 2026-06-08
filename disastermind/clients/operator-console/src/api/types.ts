// Wire types mirrored from the DisasterMind backend.
//
// Source of truth (read-only reference — never edited by this client):
//   * disastermind/core/contracts.py  -> Message.to_dict(), Topic, enums
//   * disastermind/api/service.py      -> health(), topic_counts(), recent()
//   * disastermind/tier1/commander/agent.py -> pending_reports() rows
//
// The backend is intentionally permissive about payload shapes, so the map view
// is defensive: it plots ANY message whose payload exposes lat/lon or waypoints.

/** Priority 1 (CRITICAL) .. 5 (INFO) — see core.contracts.Priority. */
export type Priority = 1 | 2 | 3 | 4 | 5;

export const PRIORITY_LABEL: Record<number, string> = {
  1: "CRITICAL",
  2: "HIGH",
  3: "MEDIUM",
  4: "LOW",
  5: "INFO",
};

/** Domain module a message belongs to — core.contracts.Module. */
export type Module = "A" | "B" | "C" | "ALL";

export const MODULE_LABEL: Record<string, string> = {
  A: "Cyclone/Flood",
  B: "Earthquake",
  C: "Fire/Collapse",
  ALL: "All",
};

/** Message.type — core.contracts.MessageType. */
export type MessageType =
  | "alert"
  | "instruction"
  | "query"
  | "acknowledgement"
  | "escalation";

/** Canonical inter-agent envelope — Message.to_dict(). */
export interface Message {
  sender: string;
  recipient: string;
  type: MessageType | string;
  priority: number; // 1..5
  payload: Record<string, unknown>;
  reasoning: string[];
  ttl_seconds: number;
  topic: string;
  incident_id: string | null;
  module: Module | string;
  escalation_trigger: string | null;
  timestamp: string; // ISO 8601
  id: string;
}

/** GET /health */
export interface Health {
  status: string;
  commander: string | null;
  messages_seen: number;
  pending_escalations: number;
}

/** GET /topics — { [topic]: count }. */
export type TopicCounts = Record<string, number>;

/** GET /escalations row — commander.pending_reports(). */
export interface Escalation {
  report_id: string;
  trigger: string | null;
  human_only: boolean;
  deadline_epoch: number; // unix seconds
  status: string;
  incident_id: string | null;
}

/** POST /escalations/{id}/approve result. */
export interface ApproveResult {
  report_id: string;
  action: "approve";
  approver: string;
  ok: boolean;
  dispatched: Message[];
}

/** POST /escalations/{id}/reject result. */
export interface RejectResult {
  report_id: string;
  action: "reject";
  approver: string;
  note: string;
  ok: boolean;
  acks: Message[];
}

/** First /ws frame — initial topic snapshot. */
export interface WsSnapshot {
  kind: "snapshot";
  topics: TopicCounts;
}

/** A /ws frame is either the snapshot or a streamed Message. */
export type WsFrame = WsSnapshot | Message;

export function isSnapshot(frame: WsFrame): frame is WsSnapshot {
  return (frame as WsSnapshot).kind === "snapshot";
}

// ---- Well-known topics — core.contracts.Topic ------------------------------
export const Topic = {
  RAW_FEED: "tier3.raw_feed",
  IOT_TELEMETRY: "tier3.iot_telemetry",
  PREDICTION: "tier2.prediction",
  CASCADE: "tier2.cascade",
  RESOURCE_PLAN: "tier2.resource_plan",
  ROUTING_PLAN: "tier2.routing_plan",
  FIELD_ORDER: "tier2.field_order",
  COMMANDER_REVIEW: "tier1.commander_review",
  ESCALATION: "tier1.escalation",
  DISPATCH: "tier3.dispatch",
} as const;

/** Topics whose traffic likely changes pending-escalation state. */
export function isEscalationish(topic: string | null | undefined): boolean {
  return !!topic && /escalation|dispatch|field/i.test(topic);
}
