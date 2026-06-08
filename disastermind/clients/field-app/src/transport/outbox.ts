/**
 * OutboxQueue — durable offline send queue with Iridium satellite fallback
 * (PRD Step 8 channels + Step 10 offline / satellite fallback).
 *
 * Behaviour:
 *   - enqueue(msg): persist the message durably (FIFO) and attempt a flush.
 *   - flush(): walk the queue in FIFO order; for each message try the
 *     terrestrial transport first, then fall back to Iridium. A message is
 *     removed from the queue only once a transport reports ok. Flushing stops
 *     at the first message that cannot be delivered on any channel so ordering
 *     is preserved and nothing is dropped.
 *   - The queue survives restarts because it is persisted to a KeyValueStore
 *     (AsyncStorage in the app) after every mutation.
 */
import { OutboundMessage } from '../contracts/builders';
import { KeyValueStore } from './storage';
import { Channel, Transport } from './types';

const STORAGE_KEY = 'disastermind.field.outbox.v1';

export interface FlushOutcome {
  /** Number of messages delivered in this flush pass. */
  sent: number;
  /** Number still queued after the pass. */
  remaining: number;
  /** The channel the last successful send used, if any. */
  lastChannel: Channel | null;
  /** True if Iridium was used for any delivery in this pass. */
  usedIridium: boolean;
}

export interface OutboxSnapshot {
  depth: number;
  messages: OutboundMessage[];
  flushing: boolean;
}

export type OutboxListener = (snapshot: OutboxSnapshot) => void;

export class OutboxQueue {
  private queue: OutboundMessage[] = [];
  private loaded = false;
  private flushing = false;
  private readonly listeners = new Set<OutboxListener>();

  constructor(
    private readonly store: KeyValueStore,
    private readonly terrestrial: Transport,
    private readonly iridium: Transport,
  ) {}

  /** Load the persisted queue from durable storage (idempotent). */
  async load(): Promise<void> {
    if (this.loaded) {
      return;
    }
    const raw = await this.store.getItem(STORAGE_KEY);
    if (raw) {
      try {
        const parsed = JSON.parse(raw);
        if (Array.isArray(parsed)) {
          this.queue = parsed as OutboundMessage[];
        }
      } catch {
        // Corrupt persisted state — start clean rather than crash the device.
        this.queue = [];
      }
    }
    this.loaded = true;
    this.notify();
  }

  /** Current queue depth. */
  depth(): number {
    return this.queue.length;
  }

  snapshot(): OutboxSnapshot {
    return {
      depth: this.queue.length,
      messages: [...this.queue],
      flushing: this.flushing,
    };
  }

  subscribe(listener: OutboxListener): () => void {
    this.listeners.add(listener);
    listener(this.snapshot());
    return () => {
      this.listeners.delete(listener);
    };
  }

  /**
   * Enqueue a message durably, then attempt to flush. Returns the flush
   * outcome so callers can surface "sent now" vs "queued offline" in the UI.
   */
  async enqueue(msg: OutboundMessage): Promise<FlushOutcome> {
    await this.load();
    this.queue.push(msg);
    await this.persist();
    this.notify();
    return this.flush();
  }

  /**
   * Flush the queue FIFO. Tries terrestrial first, then Iridium fallback.
   * Stops at the first message that no channel can deliver (preserves order
   * and durability — nothing is dropped).
   */
  async flush(): Promise<FlushOutcome> {
    await this.load();
    if (this.flushing) {
      return {
        sent: 0,
        remaining: this.queue.length,
        lastChannel: null,
        usedIridium: false,
      };
    }
    this.flushing = true;
    this.notify();

    let sent = 0;
    let lastChannel: Channel | null = null;
    let usedIridium = false;

    try {
      while (this.queue.length > 0) {
        const next = this.queue[0];
        const channel = await this.deliver(next);
        if (channel === null) {
          // No channel could deliver — stop to preserve FIFO + durability.
          break;
        }
        // Delivered: pop the head and persist immediately so a crash mid-flush
        // never re-sends an already-delivered message.
        this.queue.shift();
        await this.persist();
        sent += 1;
        lastChannel = channel;
        if (channel === 'iridium') {
          usedIridium = true;
        }
        this.notify();
      }
    } finally {
      this.flushing = false;
      this.notify();
    }

    return { sent, remaining: this.queue.length, lastChannel, usedIridium };
  }

  /** Try terrestrial, then Iridium. Returns the channel used or null. */
  private async deliver(msg: OutboundMessage): Promise<Channel | null> {
    try {
      if (await this.terrestrial.isReachable()) {
        const res = await this.terrestrial.send(msg);
        if (res.ok) {
          return this.terrestrial.channel;
        }
      }
    } catch {
      // fall through to satellite fallback
    }
    try {
      if (await this.iridium.isReachable()) {
        const res = await this.iridium.send(msg);
        if (res.ok) {
          return this.iridium.channel;
        }
      }
    } catch {
      // both channels down — caller will leave the message queued
    }
    return null;
  }

  /** Drop all queued messages (durably). For dev/testing. */
  async clear(): Promise<void> {
    await this.load();
    this.queue = [];
    await this.persist();
    this.notify();
  }

  private async persist(): Promise<void> {
    await this.store.setItem(STORAGE_KEY, JSON.stringify(this.queue));
  }

  private notify(): void {
    const snap = this.snapshot();
    for (const l of this.listeners) {
      l(snap);
    }
  }
}

export { STORAGE_KEY as OUTBOX_STORAGE_KEY };
