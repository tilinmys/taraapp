// Small saved values in the browser (localStorage). Same interface as
// kv.native.ts (phones: expo-sqlite key-value store).

function ls(): globalThis.Storage | null {
  try {
    return typeof localStorage === 'undefined' ? null : localStorage;
  } catch {
    return null; // blocked storage (private mode): values live for the session only
  }
}

export const kv = {
  get<T>(key: string): T | null {
    try {
      const raw = ls()?.getItem(key);
      return raw ? (JSON.parse(raw) as T) : null;
    } catch {
      return null;
    }
  },
  set(key: string, value: unknown) {
    try {
      if (value === undefined) ls()?.removeItem(key);
      else ls()?.setItem(key, JSON.stringify(value));
    } catch {
      // quota exceeded: the value stays in memory for this session
    }
  },
  remove(key: string) {
    kv.set(key, undefined);
  },
};
