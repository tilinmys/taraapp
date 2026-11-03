import { useSyncExternalStore } from 'react';

// SOS for a pregnant woman: who to alert, in what order, and what happens
// next. Pure logic + a small store; the screens are components/mother/sos.tsx.
//
// WHO (the dispatch algorithm)
//   1 where she is     her GPS fix (in this demo: her village's centre with a
//                      small offset; production: expo-location)
//   2 who is near      a uniform GRID index over the responders' last known
//                      places (cells of GRID_DEG, ~5.5 km). Query her cell
//                      and the ring around it, widening ring by ring until
//                      there are enough candidates or MAX_RING is reached:
//                      O(cells touched + candidates), never a scan of every
//                      ASHA in the district
//   3 how far          great-circle distance (haversine), and an ETA from
//                      each responder's way of travelling
//   4 who first        score = ETA - OWN_ASHA_BONUS (her own ASHA knows her
//                      history and is preferred unless she is much further)
//                      + LOAD_PENALTY per SOS she is already on. Off-duty
//                      responders and anyone beyond MAX_KM are never alerted
//   5 best k           a bounded max-heap keeps the k best: O(n log k)
//   6 waves            wave 1 at once: her own ASHA + the next best two;
//                      wave 2 after ESCALATE_MS with nobody on the way: the
//                      next three; wave 3: the PHC (doctor on duty). 108 is
//                      offered to her from the first second
//
// WHAT NEXT (the incident, a small state machine)
//   countdown --3 s--> alerting --someone accepts--> coming --> resolved
//       |                  |
//       +----> cancelled <-+   (she taps "I'm safe": everyone alerted is told)
// A second trigger while one is running is ignored (one incident at a time).
//
// TRIGGER (the rocker pattern): volume up / down / up / down within 2.5 s.
// Expo Go cannot read the physical keys (no native module); the screen
// feeds the detector from on-screen side keys, the web's volume keys, and,
// in a development build, react-native-volume-manager's listener.

export type GeoPoint = { lat: number; lng: number };
export type Travel = 'walk' | 'scooter' | 'vehicle';
export type Responder = {
  id: string;
  name: string;
  role: 'asha' | 'phc';
  phone: string;
  /** Last known place (home / last check-in). */
  at: GeoPoint;
  /** Villages she is the ASHA of. */
  zoneIds: string[];
  onDuty: boolean;
  /** SOS incidents she is already handling. */
  activeSos: number;
  travel: Travel;
};
export type Ranked = { r: Responder; km: number; etaMin: number; score: number; own: boolean };

export const GRID_DEG = 0.05; // ~5.5 km of latitude
export const MAX_RING = 4; // search at most ~25 km around her
export const MAX_KM = 20;
export const OWN_ASHA_BONUS_MIN = 10;
export const LOAD_PENALTY_MIN = 8;
const SPEED_KMH: Record<Travel, number> = { walk: 4.5, scooter: 22, vehicle: 35 };
/** Roads wind: straight-line distance x this ~ road distance. */
const ROAD_FACTOR = 1.3;

// ---- geometry ---------------------------------------------------------------

const R_EARTH_KM = 6371.0088;
const rad = (d: number) => (d * Math.PI) / 180;

/** Great-circle distance in km. */
export function haversineKm(a: GeoPoint, b: GeoPoint) {
  const dLat = rad(b.lat - a.lat);
  const dLng = rad(b.lng - a.lng);
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(rad(a.lat)) * Math.cos(rad(b.lat)) * Math.sin(dLng / 2) ** 2;
  return 2 * R_EARTH_KM * Math.asin(Math.min(1, Math.sqrt(h)));
}

export function etaMinutes(km: number, travel: Travel) {
  return Math.max(1, Math.round(((km * ROAD_FACTOR) / SPEED_KMH[travel]) * 60));
}

// ---- spatial index --------------------------------------------------------------

/** A uniform grid of cells (GRID_DEG square): place -> the responders there. */
export class GridIndex<T extends { at: GeoPoint }> {
  private cells = new Map<string, T[]>();
  constructor(
    items: T[] = [],
    private readonly deg = GRID_DEG,
  ) {
    items.forEach((x) => this.insert(x));
  }

  private cellOf(p: GeoPoint): [number, number] {
    return [Math.floor(p.lat / this.deg), Math.floor(p.lng / this.deg)];
  }

  insert(x: T) {
    const [i, j] = this.cellOf(x.at);
    const key = `${i}:${j}`;
    const list = this.cells.get(key);
    if (list) list.push(x);
    else this.cells.set(key, [x]);
  }

  /** Items in the cells exactly `ring` steps away (ring 0 = her own cell). */
  ring(p: GeoPoint, ring: number): T[] {
    const [ci, cj] = this.cellOf(p);
    const out: T[] = [];
    for (let i = ci - ring; i <= ci + ring; i++) {
      for (let j = cj - ring; j <= cj + ring; j++) {
        if (Math.max(Math.abs(i - ci), Math.abs(j - cj)) !== ring) continue; // the ring's border only
        const list = this.cells.get(`${i}:${j}`);
        if (list) out.push(...list);
      }
    }
    return out;
  }

  /**
   * Candidates around `p`, ring by ring, until at least `want` are found
   * (plus one more ring: a point near a cell edge may have closer
   * neighbours in the next ring) or `maxRing` is reached.
   */
  around(p: GeoPoint, want: number, maxRing = MAX_RING): T[] {
    const out: T[] = [];
    let last = maxRing;
    for (let r = 0; r <= last; r++) {
      out.push(...this.ring(p, r));
      // Enough: take the next ring too, then stop.
      if (last === maxRing && out.length >= want) last = Math.min(maxRing, r + 1);
    }
    return out;
  }
}

// ---- top-k ---------------------------------------------------------------------------

/** The k smallest by `score`, in ascending order, with a bounded max-heap: O(n log k). */
export function topK<T>(items: Iterable<T>, k: number, score: (x: T) => number): T[] {
  if (k <= 0) return [];
  const heap: { x: T; s: number }[] = []; // max-heap on s
  const up = (i: number) => {
    while (i > 0) {
      const p = (i - 1) >> 1;
      if (heap[p].s >= heap[i].s) break;
      [heap[p], heap[i]] = [heap[i], heap[p]];
      i = p;
    }
  };
  const down = (i: number) => {
    for (;;) {
      const l = 2 * i + 1;
      const r = l + 1;
      let m = i;
      if (l < heap.length && heap[l].s > heap[m].s) m = l;
      if (r < heap.length && heap[r].s > heap[m].s) m = r;
      if (m === i) return;
      [heap[m], heap[i]] = [heap[i], heap[m]];
      i = m;
    }
  };
  for (const x of items) {
    const s = score(x);
    if (heap.length < k) {
      heap.push({ x, s });
      up(heap.length - 1);
    } else if (s < heap[0].s) {
      heap[0] = { x, s };
      down(0);
    }
  }
  return heap.sort((a, b) => a.s - b.s).map((e) => e.x);
}

// ---- ranking + waves --------------------------------------------------------------------

/** The best `k` responders for an SOS at `origin` (see the header). */
export function rankResponders(origin: GeoPoint, roster: Responder[], opts: { zoneId?: string; k?: number; maxKm?: number; index?: GridIndex<Responder> }): Ranked[] {
  const k = opts.k ?? 8;
  const maxKm = opts.maxKm ?? MAX_KM;
  const index = opts.index ?? new GridIndex(roster);
  const candidates = index.around(origin, k);
  // Her own ASHA is always considered, even outside the searched rings.
  const own = roster.find((r) => r.role === 'asha' && !!opts.zoneId && r.zoneIds.includes(opts.zoneId));
  if (own && !candidates.includes(own)) candidates.push(own);
  const ranked: Ranked[] = [];
  for (const r of candidates) {
    if (!r.onDuty || r.role !== 'asha') continue;
    const km = haversineKm(origin, r.at);
    if (km > maxKm) continue;
    const etaMin = etaMinutes(km, r.travel);
    const isOwn = r === own;
    ranked.push({ r, km, etaMin, own: isOwn, score: etaMin - (isOwn ? OWN_ASHA_BONUS_MIN : 0) + r.activeSos * LOAD_PENALTY_MIN });
  }
  return topK(ranked, k, (x) => x.score);
}

export type Wave = { n: number; ids: string[] };

/** Wave 1: her own ASHA + the next best two; wave 2: the next three; wave 3: the PHC. */
export function planWaves(ranked: Ranked[], phc: Responder | undefined): Wave[] {
  const own = ranked.find((x) => x.own);
  const others = ranked.filter((x) => x !== own);
  const first = [...(own ? [own] : []), ...others.slice(0, own ? 2 : 3)];
  const second = others.slice(own ? 2 : 3, own ? 5 : 6);
  const waves: Wave[] = [{ n: 1, ids: first.map((x) => x.r.id) }];
  if (second.length) waves.push({ n: waves.length + 1, ids: second.map((x) => x.r.id) });
  if (phc) waves.push({ n: waves.length + 1, ids: [phc.id] });
  return waves.filter((w) => w.ids.length);
}

// ---- the trigger -----------------------------------------------------------------------------

export type RockerKey = 'up' | 'down';

/**
 * Volume up / down pressed alternately: at least `presses` presses with
 * `turns` changes of direction inside `windowMs`. Holding one key (raising
 * the volume) never triggers it. After a trigger it rests for `cooldownMs`.
 */
export class RockerDetector {
  private keys: { key: RockerKey; t: number }[] = [];
  private restUntil = 0;
  constructor(private readonly opts = { windowMs: 2500, presses: 4, turns: 3, cooldownMs: 5000 }) {}

  push(key: RockerKey, t: number): boolean {
    if (t < this.restUntil) return false;
    this.keys = [...this.keys.filter((k) => t - k.t <= this.opts.windowMs), { key, t }];
    if (this.keys.length < this.opts.presses) return false;
    let turns = 0;
    for (let i = 1; i < this.keys.length; i++) if (this.keys[i].key !== this.keys[i - 1].key) turns++;
    if (turns < this.opts.turns) return false;
    this.keys = [];
    this.restUntil = t + this.opts.cooldownMs;
    return true;
  }

  reset() {
    this.keys = [];
    this.restUntil = 0;
  }
}

// ---- demo geography ----------------------------------------------------------------------------

/** Village centres (Bidar taluk, approximate). */
export const ZONE_GEO: Record<string, GeoPoint> = {
  kamthana: { lat: 17.859, lng: 77.606 },
  chitta: { lat: 17.942, lng: 77.457 },
  aliyabad: { lat: 17.983, lng: 77.541 },
  janawada: { lat: 17.948, lng: 77.398 },
};

/** The responders this demo knows (the real list: the ASHA roster table). */
export const ROSTER: Responder[] = [
  { id: 'sunita', name: 'Sunita', role: 'asha', phone: '+910000000001', at: { lat: 17.862, lng: 77.601 }, zoneIds: ['kamthana', 'chitta', 'aliyabad'], onDuty: true, activeSos: 0, travel: 'scooter' },
  { id: 'parvati', name: 'Parvati', role: 'asha', phone: '+910000000003', at: { lat: 17.951, lng: 77.402 }, zoneIds: ['janawada'], onDuty: true, activeSos: 0, travel: 'scooter' },
  { id: 'kavya', name: 'Kavya', role: 'asha', phone: '+910000000005', at: { lat: 17.871, lng: 77.588 }, zoneIds: [], onDuty: true, activeSos: 0, travel: 'walk' },
  { id: 'rehana', name: 'Rehana', role: 'asha', phone: '+910000000006', at: { lat: 17.846, lng: 77.631 }, zoneIds: [], onDuty: true, activeSos: 0, travel: 'scooter' },
  { id: 'lalitha', name: 'Lalitha', role: 'asha', phone: '+910000000007', at: { lat: 17.866, lng: 77.612 }, zoneIds: [], onDuty: false, activeSos: 0, travel: 'walk' },
  { id: 'shobha', name: 'Shobha', role: 'asha', phone: '+910000000008', at: { lat: 17.905, lng: 77.55 }, zoneIds: [], onDuty: true, activeSos: 0, travel: 'scooter' },
  { id: 'meera', name: 'Meera', role: 'asha', phone: '+910000000009', at: { lat: 17.94, lng: 77.47 }, zoneIds: [], onDuty: true, activeSos: 1, travel: 'scooter' },
  { id: 'phc-hosahalli', name: 'Hosahalli PHC', role: 'phc', phone: '108', at: { lat: 17.913, lng: 77.519 }, zoneIds: [], onDuty: true, activeSos: 0, travel: 'vehicle' },
];

/** Her place for the demo: the village centre, nudged a little (stable per mother). */
export function demoLocation(motherId: string, zoneId: string): GeoPoint {
  const c = ZONE_GEO[zoneId] ?? ZONE_GEO.kamthana;
  let h = 0;
  for (let i = 0; i < motherId.length; i++) h = (h * 31 + motherId.charCodeAt(i)) | 0;
  return { lat: c.lat + ((h % 100) / 100) * 0.006, lng: c.lng + (((h >> 7) % 100) / 100) * 0.006 };
}

// ---- the incident ----------------------------------------------------------------------------------

export type SosStage = 'countdown' | 'alerting' | 'coming' | 'cancelled' | 'resolved';
export type ResponderState = 'queued' | 'alerting' | 'alerted' | 'seen' | 'coming';
export type SosResponder = { id: string; name: string; phone: string; km: number; etaMin: number; own: boolean; wave: number; state: ResponderState };
export type Incident = {
  id: string;
  motherId: string;
  motherName: string;
  zoneId: string;
  source: 'button' | 'rocker' | 'keys';
  at: GeoPoint;
  startedAt: number;
  stage: SosStage;
  countdownEndsAt: number;
  wave: number;
  waves: Wave[];
  responders: SosResponder[];
  acceptedBy: string | null;
};

export type SosClock = {
  now: () => number;
  setTimeout: (fn: () => void, ms: number) => unknown;
  clearTimeout: (h: unknown) => void;
};

export type SosDeps = {
  roster: Responder[];
  /** Deliver an alert (or the all-clear) to a responder. */
  notify: (to: SosResponder, incident: Incident, kind: 'alert' | 'cancelled') => void;
  clock?: SosClock;
  /** Demo: responders "see" and one accepts by themselves. */
  simulate?: boolean;
  countdownMs?: number;
  escalateMs?: number;
};

export const COUNTDOWN_MS = 3000;
export const ESCALATE_MS = 12_000;

const realClock: SosClock = { now: () => Date.now(), setTimeout: (fn, ms) => setTimeout(fn, ms), clearTimeout: (h) => clearTimeout(h as ReturnType<typeof setTimeout>) };

export function createSosStore(deps: SosDeps) {
  const clock = deps.clock ?? realClock;
  const countdownMs = deps.countdownMs ?? COUNTDOWN_MS;
  const escalateMs = deps.escalateMs ?? ESCALATE_MS;
  let incident: Incident | null = null;
  let seq = 0;
  let timers: unknown[] = [];
  const listeners = new Set<() => void>();
  const emit = () => listeners.forEach((l) => l());
  const set = (next: Incident | null) => {
    incident = next;
    emit();
  };
  const later = (ms: number, fn: () => void) => {
    timers.push(clock.setTimeout(fn, ms));
  };
  const clearAll = () => {
    timers.forEach((h) => clock.clearTimeout(h));
    timers = [];
  };
  const patchResponder = (id: string, patch: Partial<SosResponder>) => {
    if (!incident) return;
    set({ ...incident, responders: incident.responders.map((r) => (r.id === id ? { ...r, ...patch } : r)) });
  };
  const live = (id: string) => incident?.id === id && (incident.stage === 'alerting' || incident.stage === 'coming');

  function sendWave(n: number) {
    if (!incident || incident.stage !== 'alerting') return;
    const wave = incident.waves.find((w) => w.n === n);
    if (!wave) return;
    const id = incident.id;
    set({ ...incident, wave: n, responders: incident.responders.map((r) => (wave.ids.includes(r.id) ? { ...r, state: 'alerting' } : r)) });
    for (const rid of wave.ids) {
      const r = incident.responders.find((x) => x.id === rid);
      if (!r) continue;
      deps.notify(r, incident, 'alert');
      later(600, () => live(id) && incident?.responders.find((x) => x.id === rid)?.state === 'alerting' && patchResponder(rid, { state: 'alerted' }));
    }
    if (deps.simulate) simulateWave(id, wave);
    // Nobody on the way yet: the next wave.
    later(escalateMs, () => {
      if (incident?.id === id && incident.stage === 'alerting') sendWave(n + 1);
    });
  }

  /** Demo: the alerted ASHAs see it; the best one says she is coming. */
  function simulateWave(id: string, wave: Wave) {
    wave.ids.forEach((rid, i) => later(1800 + i * 900, () => live(id) && patchResponder(rid, { state: 'seen' })));
    if (wave.n === 1 && wave.ids[0]) later(5200, () => live(id) && api.accept(wave.ids[0]));
  }

  const api = {
    subscribe(l: () => void) {
      listeners.add(l);
      return () => void listeners.delete(l);
    },
    snapshot: () => incident,

    /** Start an SOS (ignored while one is already running). Returns its id. */
    trigger(input: { motherId: string; motherName: string; zoneId: string; at: GeoPoint; source: Incident['source'] }): string | null {
      if (incident && (incident.stage === 'countdown' || incident.stage === 'alerting' || incident.stage === 'coming')) return null;
      clearAll();
      const now = clock.now();
      const ranked = rankResponders(input.at, deps.roster, { zoneId: input.zoneId });
      const phc = deps.roster.find((r) => r.role === 'phc' && r.onDuty);
      const waves = planWaves(ranked, phc);
      const responders: SosResponder[] = [];
      for (const w of waves) {
        for (const rid of w.ids) {
          const hit = ranked.find((x) => x.r.id === rid);
          const r = hit?.r ?? deps.roster.find((x) => x.id === rid);
          if (!r) continue;
          const km = hit?.km ?? haversineKm(input.at, r.at);
          responders.push({ id: r.id, name: r.name, phone: r.phone, km, etaMin: hit?.etaMin ?? etaMinutes(km, r.travel), own: !!hit?.own, wave: w.n, state: 'queued' });
        }
      }
      const id = `sos-${now.toString(36)}-${++seq}`;
      set({ ...input, id, startedAt: now, stage: 'countdown', countdownEndsAt: now + countdownMs, wave: 0, waves, responders, acceptedBy: null });
      later(countdownMs, () => {
        if (incident?.id !== id || incident.stage !== 'countdown') return;
        set({ ...incident, stage: 'alerting' });
        sendWave(1);
      });
      return id;
    },

    /** Skip the countdown (she taps "Send now"). */
    sendNow() {
      if (incident?.stage !== 'countdown') return;
      clearAll();
      set({ ...incident, stage: 'alerting', countdownEndsAt: clock.now() });
      sendWave(1);
    },

    /** A responder is on her way (the ASHA tapped the alert, or the demo). */
    accept(responderId: string) {
      if (!incident || incident.stage !== 'alerting') return;
      const r = incident.responders.find((x) => x.id === responderId);
      if (!r) return;
      set({ ...incident, stage: 'coming', acceptedBy: responderId, responders: incident.responders.map((x) => (x.id === responderId ? { ...x, state: 'coming' } : x)) });
    },

    /** She is safe: stop, and tell everyone already alerted. */
    cancel() {
      if (!incident) return;
      const was = incident.stage;
      clearAll();
      if (was === 'alerting' || was === 'coming') {
        incident.responders.filter((r) => r.state !== 'queued').forEach((r) => incident && deps.notify(r, incident, 'cancelled'));
      }
      set({ ...incident, stage: 'cancelled' });
    },

    /** Help arrived: close the incident. */
    resolve() {
      if (!incident) return;
      clearAll();
      set({ ...incident, stage: 'resolved' });
    },

    /** Forget a finished incident (the overlay closes). */
    dismiss() {
      if (incident && (incident.stage === 'cancelled' || incident.stage === 'resolved')) set(null);
    },
  };
  return api;
}

export type SosStore = ReturnType<typeof createSosStore>;

// The app's store: alerts go into the care network's notifications.
let notifyImpl: SosDeps['notify'] = () => {};
/** Wired once by the care network (avoids an import cycle). */
export function setSosNotifier(fn: SosDeps['notify']) {
  notifyImpl = fn;
}

export const sosStore = createSosStore({ roster: ROSTER, notify: (to, inc, kind) => notifyImpl(to, inc, kind), simulate: true });

export function useSos() {
  return useSyncExternalStore(sosStore.subscribe, sosStore.snapshot, sosStore.snapshot);
}
