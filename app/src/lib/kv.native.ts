// Small saved values on the phone (expo-sqlite's key-value store, in Expo
// Go): what the ASHA saves must still be there when she reopens a profile or
// the app. Same interface as kv.ts (web: localStorage).
//
// Reads are synchronous (stores load once, at start); writes are coalesced
// per key and run a moment later, so typing or ticking never waits on disk.

type Storage = { getItemSync(k: string): string | null; setItemSync(k: string, v: string): void; removeItemSync(k: string): boolean };

let storage: Storage | null = null;
try {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  storage = require('expo-sqlite/kv-store').default as Storage;
} catch {
  storage = null; // no native module (tests): values live for the session only
}

const pending = new Map<string, unknown>();
let timer: ReturnType<typeof setTimeout> | null = null;

function flush() {
  timer = null;
  for (const [key, value] of pending) {
    try {
      if (value === undefined) storage?.removeItemSync(key);
      else storage?.setItemSync(key, JSON.stringify(value));
    } catch {
      // a full disk must not break the screen; the value stays in memory
    }
  }
  pending.clear();
}

export const kv = {
  get<T>(key: string): T | null {
    if (pending.has(key)) return (pending.get(key) as T) ?? null;
    try {
      const raw = storage?.getItemSync(key);
      return raw ? (JSON.parse(raw) as T) : null;
    } catch {
      return null;
    }
  },
  set(key: string, value: unknown) {
    pending.set(key, value);
    timer ??= setTimeout(flush, 250);
  },
  remove(key: string) {
    kv.set(key, undefined);
  },
};
