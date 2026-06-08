import { OutboundMessage } from '../contracts/builders';
import { Channel, SendResult, Transport } from './types';

/**
 * Real terrestrial transport (PRD Step 8: terrestrial channel).
 *
 * POSTs the exact contract JSON to a configurable backend ingest endpoint
 * derived from BACKEND_URL. The Python backend currently ships no device-ingest
 * REST route, so this is structured to hit one when it exists:
 *
 *   POST {backendUrl}/ingest/{topic}
 *   body: OutboundMessage.body  (the exact contract JSON)
 *
 * Reachability is probed with a short HEAD/GET to {backendUrl}/health. When the
 * backendUrl is empty this transport reports itself unreachable so the app
 * cleanly degrades to the offline outbox + Iridium fallback path.
 */
export class TerrestrialTransport implements Transport {
  readonly channel: Channel = 'terrestrial';

  constructor(
    private readonly backendUrl: string,
    private readonly timeoutMs = 4000,
    /** Test seam: a forced-offline flag for the dev connectivity toggle. */
    private forcedOffline = false,
  ) {}

  setForcedOffline(value: boolean): void {
    this.forcedOffline = value;
  }

  hasBackend(): boolean {
    return this.backendUrl.trim().length > 0;
  }

  async isReachable(): Promise<boolean> {
    if (this.forcedOffline || !this.hasBackend()) {
      return false;
    }
    try {
      const res = await this.withTimeout(
        fetch(this.joinUrl('/health'), { method: 'GET' }),
      );
      return res.ok;
    } catch {
      return false;
    }
  }

  async send(msg: OutboundMessage): Promise<SendResult> {
    if (this.forcedOffline) {
      return { ok: false, channel: this.channel, detail: 'forced offline (dev)' };
    }
    if (!this.hasBackend()) {
      return { ok: false, channel: this.channel, detail: 'no BACKEND_URL configured' };
    }
    try {
      const res = await this.withTimeout(
        fetch(this.joinUrl(`/ingest/${encodeURIComponent(msg.topic)}`), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(msg.body),
        }),
      );
      return {
        ok: res.ok,
        channel: this.channel,
        detail: `HTTP ${res.status}`,
      };
    } catch (err) {
      return {
        ok: false,
        channel: this.channel,
        detail: err instanceof Error ? err.message : 'network error',
      };
    }
  }

  private joinUrl(path: string): string {
    const base = this.backendUrl.replace(/\/+$/, '');
    return `${base}${path}`;
  }

  private withTimeout<T>(p: Promise<T>): Promise<T> {
    return new Promise<T>((resolve, reject) => {
      const timer = setTimeout(
        () => reject(new Error(`timeout after ${this.timeoutMs}ms`)),
        this.timeoutMs,
      );
      p.then(
        (v) => {
          clearTimeout(timer);
          resolve(v);
        },
        (e) => {
          clearTimeout(timer);
          reject(e);
        },
      );
    });
  }
}
