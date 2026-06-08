import { OutboundMessage } from '../contracts/builders';

/** Which physical channel a message went out on (PRD Step 8). */
export type Channel = 'terrestrial' | 'iridium';

/** Result of a single send attempt. */
export interface SendResult {
  ok: boolean;
  channel: Channel | null;
  /** Diagnostic detail (HTTP status, simulated reason, etc.). */
  detail?: string;
}

/**
 * A transport delivers a single outbound contract message to the backbone.
 * Implementations: TerrestrialTransport (real fetch), IridiumTransport
 * (simulated satellite fallback), MockTransport (standalone simulator).
 */
export interface Transport {
  readonly channel: Channel;
  /** True when this channel is currently reachable. */
  isReachable(): Promise<boolean>;
  /** Attempt to deliver one message. Throws on hard failure. */
  send(msg: OutboundMessage): Promise<SendResult>;
}
