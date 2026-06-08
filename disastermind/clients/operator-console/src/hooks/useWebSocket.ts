// Auto-reconnecting WebSocket hook for the /ws live stream.
//
// Mirrors the reference vanilla-JS dashboard:
//   * derives wss:// when the page is https (via api/client.wsUrl)
//   * reconnects 2s after a close
//   * the first frame is { kind: "snapshot", topics } — surfaced separately
//
// Frames are delivered through stable refs so changing handlers never tears
// down the live socket (which would drop messages on every render).

import { useEffect, useRef, useState } from "react";
import { wsUrl } from "../api/client";
import { isSnapshot, type Message, type TopicCounts, type WsFrame } from "../api/types";

const RECONNECT_MS = 2000;

export type WsStatus = "connecting" | "live" | "reconnecting";

export interface UseWebSocketArgs {
  /** Called with every streamed bus message (snapshot frames excluded). */
  onMessage: (msg: Message) => void;
  /** Called once per (re)connection with the initial topic snapshot. */
  onSnapshot?: (topics: TopicCounts) => void;
}

export function useWebSocket({ onMessage, onSnapshot }: UseWebSocketArgs): WsStatus {
  const [status, setStatus] = useState<WsStatus>("connecting");

  const onMessageRef = useRef(onMessage);
  const onSnapshotRef = useRef(onSnapshot);
  onMessageRef.current = onMessage;
  onSnapshotRef.current = onSnapshot;

  useEffect(() => {
    let ws: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let closed = false; // component unmounted — stop reconnecting

    const connect = () => {
      if (closed) return;
      setStatus((prev) => (prev === "live" ? "reconnecting" : prev));
      try {
        ws = new WebSocket(wsUrl("/ws"));
      } catch {
        scheduleReconnect();
        return;
      }

      ws.onopen = () => {
        if (!closed) setStatus("live");
      };
      ws.onmessage = (ev: MessageEvent) => {
        let frame: WsFrame;
        try {
          frame = JSON.parse(ev.data as string) as WsFrame;
        } catch {
          return;
        }
        if (isSnapshot(frame)) {
          onSnapshotRef.current?.(frame.topics);
          return;
        }
        onMessageRef.current(frame);
      };
      ws.onerror = () => {
        try {
          ws?.close();
        } catch {
          /* ignore */
        }
      };
      ws.onclose = () => {
        if (closed) return;
        setStatus("reconnecting");
        scheduleReconnect();
      };
    };

    const scheduleReconnect = () => {
      if (closed) return;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      reconnectTimer = setTimeout(connect, RECONNECT_MS);
    };

    connect();

    return () => {
      closed = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      if (ws) {
        ws.onclose = null; // prevent reconnect on intentional teardown
        try {
          ws.close();
        } catch {
          /* ignore */
        }
      }
    };
  }, []);

  return status;
}
