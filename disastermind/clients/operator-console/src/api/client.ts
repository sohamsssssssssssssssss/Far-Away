// Typed HTTP/WS client for the DisasterMind Commander Dashboard API.
//
// By default every URL is RELATIVE (e.g. "/topics"), so the app works behind
// any host/port exactly like the reference vanilla-JS dashboard, and the Vite
// dev-server proxy forwards to uvicorn. To point at a remote backend, set:
//   VITE_API_BASE = https://api.example.com      (HTTP base, no trailing slash)
//   VITE_WS_BASE  = wss://api.example.com         (WS base, optional)
// If VITE_WS_BASE is unset it is derived from VITE_API_BASE (http->ws,
// https->wss); if both are unset, the WS URL is derived from window.location.

import type {
  ApproveResult,
  Escalation,
  Health,
  Message,
  RejectResult,
  TopicCounts,
} from "./types";

const API_BASE = (import.meta.env.VITE_API_BASE ?? "").replace(/\/+$/, "");
const WS_BASE = (import.meta.env.VITE_WS_BASE ?? "").replace(/\/+$/, "");

function url(path: string): string {
  return API_BASE ? `${API_BASE}${path}` : path;
}

/** Resolve the absolute ws:// or wss:// URL for the /ws stream. */
export function wsUrl(path = "/ws"): string {
  if (WS_BASE) return `${WS_BASE}${path}`;
  if (API_BASE) {
    // Derive ws scheme from the HTTP base.
    return API_BASE.replace(/^http(s?):/, (_m, s) => `ws${s}:`) + path;
  }
  // Relative: mirror the reference UI (wss when the page is https).
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}${path}`;
}

async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const resp = await fetch(url(path), { signal });
  if (!resp.ok) throw new Error(`GET ${path} -> HTTP ${resp.status}`);
  return (await resp.json()) as T;
}

async function postJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const resp = await fetch(url(path), { method: "POST", signal });
  if (!resp.ok) throw new Error(`POST ${path} -> HTTP ${resp.status}`);
  return (await resp.json()) as T;
}

export const api = {
  health: (signal?: AbortSignal) => getJson<Health>("/health", signal),

  topics: (signal?: AbortSignal) => getJson<TopicCounts>("/topics", signal),

  /** Recent bus MESSAGES (NOT grouped incidents — see service.recent()). */
  incidents: (limit = 100, signal?: AbortSignal) =>
    getJson<Message[]>(`/incidents?limit=${encodeURIComponent(limit)}`, signal),

  escalations: (signal?: AbortSignal) =>
    getJson<Escalation[]>("/escalations", signal),

  approve: (id: string, approver: string, signal?: AbortSignal) =>
    postJson<ApproveResult>(
      `/escalations/${encodeURIComponent(id)}/approve?approver=${encodeURIComponent(
        approver,
      )}`,
      signal,
    ),

  reject: (id: string, approver: string, note: string, signal?: AbortSignal) =>
    postJson<RejectResult>(
      `/escalations/${encodeURIComponent(id)}/reject?approver=${encodeURIComponent(
        approver,
      )}&note=${encodeURIComponent(note)}`,
      signal,
    ),
};
