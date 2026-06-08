/**
 * Durable key/value storage abstraction for the offline outbox (PRD Step 10).
 *
 * The app uses AsyncStorage at runtime; tests inject an in-memory store. Both
 * satisfy the same minimal async string K/V interface.
 */
export interface KeyValueStore {
  getItem(key: string): Promise<string | null>;
  setItem(key: string, value: string): Promise<void>;
  removeItem(key: string): Promise<void>;
}

/** Simple in-memory store for tests / standalone fallback. */
export class MemoryStore implements KeyValueStore {
  private readonly map = new Map<string, string>();

  async getItem(key: string): Promise<string | null> {
    return this.map.has(key) ? (this.map.get(key) as string) : null;
  }

  async setItem(key: string, value: string): Promise<void> {
    this.map.set(key, value);
  }

  async removeItem(key: string): Promise<void> {
    this.map.delete(key);
  }
}

/**
 * Resolve the runtime store. Lazily requires AsyncStorage so the module is
 * importable under plain ts-jest (node) without the native package installed.
 */
export function createDefaultStore(): KeyValueStore {
  try {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const AsyncStorage =
      require('@react-native-async-storage/async-storage').default;
    return {
      getItem: (k: string) => AsyncStorage.getItem(k),
      setItem: (k: string, v: string) => AsyncStorage.setItem(k, v),
      removeItem: (k: string) => AsyncStorage.removeItem(k),
    };
  } catch {
    return new MemoryStore();
  }
}
