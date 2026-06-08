import { OutboundMessage } from '../contracts/builders';
import { Channel, SendResult, Transport } from './types';

/**
 * Simulated Iridium satellite transport (PRD Step 8 fallback channel / Step 10
 * satellite fallback).
 *
 * Iridium is the high-latency, low-bandwidth fallback used when the terrestrial
 * channel is unreachable. In this standalone build it is *simulated*: it adds a
 * latency budget and (optionally) a small failure rate to model real satellite
 * behaviour, but always logs the exact contract JSON it "transmits". In a real
 * deployment this would marshal the body into an SBD (Short Burst Data) message.
 *
 * It is deliberately always considered "reachable" once enabled — a satellite
 * device has line-of-sight to the constellation almost everywhere — which is
 * why it serves as the fallback when terrestrial fails.
 */
export class IridiumTransport implements Transport {
  readonly channel: Channel = 'iridium';

  constructor(
    /** Simulated round-trip latency for an SBD burst. */
    private readonly latencyMs = 1200,
    /** Whether the satellite link is enabled at all (e.g. modem powered). */
    private enabled = true,
    /** Simulated transmit failure probability [0,1] (e.g. obstructed sky). */
    private readonly failureRate = 0,
  ) {}

  setEnabled(value: boolean): void {
    this.enabled = value;
  }

  async isReachable(): Promise<boolean> {
    return this.enabled;
  }

  async send(msg: OutboundMessage): Promise<SendResult> {
    if (!this.enabled) {
      return { ok: false, channel: this.channel, detail: 'iridium modem disabled' };
    }
    await this.delay(this.latencyMs);
    if (this.failureRate > 0 && Math.random() < this.failureRate) {
      return { ok: false, channel: this.channel, detail: 'sbd burst failed (no sky)' };
    }
    // Simulated SBD transmit — the body IS the exact contract JSON.
    // eslint-disable-next-line no-console
    console.log(
      `[iridium] SBD burst topic=${msg.topic} kind=${msg.kind} bytes=${
        JSON.stringify(msg.body).length
      }`,
    );
    return { ok: true, channel: this.channel, detail: 'sbd burst delivered (simulated)' };
  }

  private delay(ms: number): Promise<void> {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }
}
