import { OutboundMessage } from '../contracts/builders';
import { Channel, SendResult, Transport } from './types';

/**
 * Standalone mock transport (PRD Step 10 standalone simulator).
 *
 * Lets the whole app run end-to-end with no backend: it records every outbound
 * contract message it "delivers" so the UI / tests can inspect the exact wire
 * JSON. Its reachability is controlled by the dev connectivity toggle so the
 * offline-queue + Iridium-fallback path is fully demonstrable in a simulator.
 */
export class MockTransport implements Transport {
  readonly channel: Channel = 'terrestrial';

  /** Every message this transport has accepted, in delivery order. */
  readonly delivered: OutboundMessage[] = [];

  constructor(private online = true) {}

  setOnline(value: boolean): void {
    this.online = value;
  }

  async isReachable(): Promise<boolean> {
    return this.online;
  }

  async send(msg: OutboundMessage): Promise<SendResult> {
    if (!this.online) {
      return { ok: false, channel: this.channel, detail: 'mock terrestrial offline' };
    }
    this.delivered.push(msg);
    return { ok: true, channel: this.channel, detail: 'mock delivered' };
  }
}
