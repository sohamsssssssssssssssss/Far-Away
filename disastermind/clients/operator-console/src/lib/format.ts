// Small formatting helpers shared across panels.

/** Human countdown to a unix-epoch (seconds) deadline. Negative => overdue. */
export function countdown(deadlineEpoch: number, nowMs = Date.now()): string {
  const remainingMs = deadlineEpoch * 1000 - nowMs;
  const overdue = remainingMs < 0;
  const total = Math.floor(Math.abs(remainingMs) / 1000);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const parts = h > 0 ? [h, m, s] : [m, s];
  const body = parts.map((n) => String(n).padStart(2, "0")).join(":");
  return overdue ? `overdue ${body}` : body;
}

export function isOverdue(deadlineEpoch: number, nowMs = Date.now()): boolean {
  return deadlineEpoch * 1000 - nowMs < 0;
}

/** HH:MM:SS from an ISO timestamp (falls back to the raw string). */
export function clock(iso: string | undefined | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleTimeString([], { hour12: false });
}

/** A short, readable label for an escalation trigger constant. */
export function triggerLabel(trigger: string | null | undefined): string {
  if (!trigger) return "escalation";
  return trigger.replace(/_/g, " ");
}

export function titleCase(s: string): string {
  return s.replace(/\b\w/g, (c) => c.toUpperCase());
}
