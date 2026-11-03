import { describe, expect, it, jest } from '@jest/globals';

import {
  createSosStore,
  demoLocation,
  etaMinutes,
  GridIndex,
  haversineKm,
  planWaves,
  rankResponders,
  RockerDetector,
  ROSTER,
  topK,
  ZONE_GEO,
  type GeoPoint,
  type Incident,
  type Responder,
  type SosClock,
  type SosResponder,
} from '../sos';

// SOS: the dispatch algorithm (grid index, haversine, scoring, top-k,
// waves), the rocker trigger and the incident state machine.

const asha = (id: string, at: GeoPoint, extra: Partial<Responder> = {}): Responder => ({
  id,
  name: id,
  role: 'asha',
  phone: '0',
  at,
  zoneIds: [],
  onDuty: true,
  activeSos: 0,
  travel: 'scooter',
  ...extra,
});

/** A seeded pseudo-random generator: the "random" tests are reproducible. */
function rng(seed: number) {
  let s = seed >>> 0;
  return () => {
    s = (s * 1664525 + 1013904223) >>> 0;
    return s / 2 ** 32;
  };
}

describe('haversineKm', () => {
  it('is 0 for the same point and symmetric', () => {
    const a = ZONE_GEO.kamthana;
    const b = ZONE_GEO.janawada;
    expect(haversineKm(a, a)).toBe(0);
    expect(haversineKm(a, b)).toBeCloseTo(haversineKm(b, a), 9);
  });

  it('matches known distances', () => {
    // one degree of latitude ~ 111.2 km
    expect(haversineKm({ lat: 17, lng: 77 }, { lat: 18, lng: 77 })).toBeCloseTo(111.2, 0);
    // Bengaluru -> Mysuru, ~128 km in a straight line
    expect(haversineKm({ lat: 12.9716, lng: 77.5946 }, { lat: 12.2958, lng: 76.6394 })).toBeGreaterThan(125);
    expect(haversineKm({ lat: 12.9716, lng: 77.5946 }, { lat: 12.2958, lng: 76.6394 })).toBeLessThan(131);
  });
});

describe('etaMinutes', () => {
  it('walking is slower than a scooter, never below a minute', () => {
    expect(etaMinutes(2, 'walk')).toBeGreaterThan(etaMinutes(2, 'scooter'));
    expect(etaMinutes(0, 'vehicle')).toBe(1);
  });
});

describe('GridIndex', () => {
  it('ring 0 is the own cell, ring 1 the 8 cells around it', () => {
    const p = { lat: 17.875, lng: 77.575 };
    const same = asha('same', { lat: 17.876, lng: 77.576 });
    const next = asha('next', { lat: 17.925, lng: 77.575 }); // one cell north
    const far = asha('far', { lat: 18.2, lng: 77.575 });
    const g = new GridIndex([same, next, far]);
    expect(g.ring(p, 0)).toEqual([same]);
    expect(g.ring(p, 1)).toEqual([next]);
    expect(g.around(p, 1).map((x) => x.id).sort()).toEqual(['next', 'same']); // stops after one extra ring
  });

  it('finds the same nearest responders as a full scan (1,000 random cases)', () => {
    const rand = rng(7);
    for (let n = 0; n < 1000; n++) {
      const roster = Array.from({ length: 40 }, (_, i) => asha(`a${i}`, { lat: 17.7 + rand() * 0.5, lng: 77.3 + rand() * 0.5 }));
      const origin = { lat: 17.7 + rand() * 0.5, lng: 77.3 + rand() * 0.5 };
      const k = 3;
      const brute = [...roster].sort((a, b) => haversineKm(origin, a.at) - haversineKm(origin, b.at)).slice(0, k);
      const viaGrid = topK(new GridIndex(roster).around(origin, k, 12), k, (r) => haversineKm(origin, r.at));
      // The grid may return extra candidates, but the k nearest must be among them, in order.
      expect(viaGrid.map((r) => r.id)).toEqual(brute.map((r) => r.id));
    }
  });
});

describe('topK', () => {
  it('equals sort + slice for any k', () => {
    const rand = rng(3);
    const xs = Array.from({ length: 200 }, () => Math.floor(rand() * 1000));
    for (const k of [0, 1, 5, 50, 200, 500]) {
      expect(topK(xs, k, (x) => x)).toEqual([...xs].sort((a, b) => a - b).slice(0, k));
    }
  });
});

describe('rankResponders', () => {
  const here = { lat: 17.86, lng: 77.6 };

  it('never alerts someone off duty or too far away', () => {
    const roster = [asha('near-off', { lat: 17.861, lng: 77.601 }, { onDuty: false }), asha('ok', { lat: 17.87, lng: 77.61 }), asha('far', { lat: 18.5, lng: 77.6 })];
    expect(rankResponders(here, roster, {}).map((x) => x.r.id)).toEqual(['ok']);
  });

  it('prefers her own ASHA unless she is much further', () => {
    const own = asha('own', { lat: 17.875, lng: 77.6 }, { zoneIds: ['z'] }); // ~1.7 km
    const closer = asha('closer', { lat: 17.865, lng: 77.6 }); // ~0.6 km
    const ranked = rankResponders(here, [closer, own], { zoneId: 'z' });
    expect(ranked[0].r.id).toBe('own');
    expect(ranked[0].own).toBe(true);
    // ...but not when she is 15 km away and someone is next door
    const ownFar = asha('ownFar', { lat: 17.995, lng: 77.6 }, { zoneIds: ['z'] });
    expect(rankResponders(here, [closer, ownFar], { zoneId: 'z' })[0].r.id).toBe('closer');
  });

  it('includes her own ASHA even outside the searched rings', () => {
    const crowd = Array.from({ length: 10 }, (_, i) => asha(`c${i}`, { lat: 17.86 + i * 0.001, lng: 77.6 }));
    const own = asha('own', { lat: 17.98, lng: 77.6 }, { zoneIds: ['z'] }); // ~13 km, several rings out
    expect(rankResponders(here, [...crowd, own], { zoneId: 'z', k: 12 }).some((x) => x.r.id === 'own')).toBe(true);
  });

  it('spreads load: someone already on an SOS drops down', () => {
    const busy = asha('busy', { lat: 17.861, lng: 77.6 }, { activeSos: 2 });
    const free = asha('free', { lat: 17.875, lng: 77.6 });
    expect(rankResponders(here, [busy, free], {})[0].r.id).toBe('free');
  });

  it('the PHC is never in the ASHA ranking', () => {
    expect(rankResponders(ZONE_GEO.kamthana, ROSTER, { zoneId: 'kamthana' }).every((x) => x.r.role === 'asha')).toBe(true);
  });
});

describe('planWaves', () => {
  it('wave 1: own ASHA + next two; wave 2: next three; wave 3: the PHC', () => {
    const ranked = rankResponders(demoLocation('lakshmi', 'kamthana'), ROSTER, { zoneId: 'kamthana' });
    const phc = ROSTER.find((r) => r.role === 'phc');
    const waves = planWaves(ranked, phc);
    expect(waves[0].ids[0]).toBe('sunita'); // her own ASHA first
    expect(waves[0].ids).toHaveLength(3);
    expect(waves[waves.length - 1].ids).toEqual(['phc-hosahalli']);
    const all = waves.flatMap((w) => w.ids);
    expect(new Set(all).size).toBe(all.length); // nobody alerted twice
    expect(all).not.toContain('lalitha'); // off duty
  });

  it('no own ASHA: the three best go first', () => {
    const ranked = rankResponders(ZONE_GEO.chitta, ROSTER.filter((r) => r.id !== 'sunita'), {});
    expect(planWaves(ranked, undefined)[0].ids).toHaveLength(3);
  });
});

describe('RockerDetector', () => {
  it('up, down, up, down inside 2.5 s triggers', () => {
    const d = new RockerDetector();
    expect(d.push('up', 0)).toBe(false);
    expect(d.push('down', 300)).toBe(false);
    expect(d.push('up', 600)).toBe(false);
    expect(d.push('down', 900)).toBe(true);
  });

  it('holding one key (turning the volume up) never triggers', () => {
    const d = new RockerDetector();
    for (let i = 0; i < 10; i++) expect(d.push('up', i * 150)).toBe(false);
  });

  it('too slow does not trigger', () => {
    const d = new RockerDetector();
    ['up', 'down', 'up', 'down'].forEach((k, i) => expect(d.push(k as 'up' | 'down', i * 1200)).toBe(false));
  });

  it('rests after a trigger (one press burst = one SOS)', () => {
    const d = new RockerDetector();
    ['up', 'down', 'up'].forEach((k, i) => d.push(k as 'up' | 'down', i * 200));
    expect(d.push('down', 600)).toBe(true);
    ['up', 'down', 'up', 'down'].forEach((k, i) => expect(d.push(k as 'up' | 'down', 800 + i * 200)).toBe(false));
  });
});

// ---- the incident ---------------------------------------------------------------

function fakeClock() {
  let now = 1_000_000;
  let id = 0;
  const timers = new Map<number, { at: number; fn: () => void }>();
  const clock: SosClock = {
    now: () => now,
    setTimeout: (fn, ms) => {
      timers.set(++id, { at: now + ms, fn });
      return id;
    },
    clearTimeout: (h) => void timers.delete(h as number),
  };
  const advance = (ms: number) => {
    const until = now + ms;
    for (;;) {
      const next = [...timers.entries()].filter(([, t]) => t.at <= until).sort((a, b) => a[1].at - b[1].at)[0];
      if (!next) break;
      timers.delete(next[0]);
      now = next[1].at;
      next[1].fn();
    }
    now = until;
  };
  return { clock, advance, pending: () => timers.size };
}

function setup(simulate = false) {
  const { clock, advance, pending } = fakeClock();
  const sent: { to: string; kind: 'alert' | 'cancelled' }[] = [];
  const notify = jest.fn((to: SosResponder, _i: Incident, kind: 'alert' | 'cancelled') => void sent.push({ to: to.id, kind }));
  const store = createSosStore({ roster: ROSTER, notify, clock, simulate, countdownMs: 3000, escalateMs: 12_000 });
  const fire = () => store.trigger({ motherId: 'lakshmi', motherName: 'Lakshmi D.', zoneId: 'kamthana', at: demoLocation('lakshmi', 'kamthana'), source: 'rocker' });
  return { store, advance, pending, sent, fire };
}

describe('SOS incident', () => {
  it('counts down 3 s, then alerts wave 1 (her own ASHA first)', () => {
    const { store, advance, sent, fire } = setup();
    expect(fire()).toBeTruthy();
    expect(store.snapshot()?.stage).toBe('countdown');
    advance(2999);
    expect(sent).toHaveLength(0);
    advance(1);
    expect(store.snapshot()?.stage).toBe('alerting');
    expect(sent[0]).toEqual({ to: 'sunita', kind: 'alert' });
    expect(sent).toHaveLength(store.snapshot()!.waves[0].ids.length);
  });

  it('cancel during the countdown alerts nobody', () => {
    const { store, advance, sent, fire, pending } = setup();
    fire();
    advance(1000);
    store.cancel();
    advance(60_000);
    expect(sent).toHaveLength(0);
    expect(store.snapshot()?.stage).toBe('cancelled');
    expect(pending()).toBe(0); // no timer left behind
  });

  it('escalates wave by wave while nobody is on the way', () => {
    const { store, advance, sent, fire } = setup();
    fire();
    advance(3000);
    const w1 = sent.length;
    advance(12_000);
    expect(store.snapshot()?.wave).toBe(2);
    expect(sent.length).toBeGreaterThan(w1);
    advance(12_000);
    expect(sent.some((s) => s.to === 'phc-hosahalli')).toBe(true);
  });

  it('an accept stops escalation; cancelling then tells everyone alerted', () => {
    const { store, advance, sent, fire } = setup();
    fire();
    advance(3000);
    store.accept('sunita');
    expect(store.snapshot()?.stage).toBe('coming');
    const before = sent.length;
    advance(60_000);
    expect(sent.length).toBe(before); // no further waves
    store.cancel();
    const allClear = sent.filter((s) => s.kind === 'cancelled').map((s) => s.to);
    expect(allClear.sort()).toEqual(store.snapshot()!.waves[0].ids.slice().sort());
  });

  it('a second trigger while one runs is ignored', () => {
    const { store, fire } = setup();
    const first = fire();
    expect(fire()).toBeNull();
    expect(store.snapshot()?.id).toBe(first);
  });

  it('send now skips the countdown', () => {
    const { store, sent, fire } = setup();
    fire();
    store.sendNow();
    expect(store.snapshot()?.stage).toBe('alerting');
    expect(sent.length).toBeGreaterThan(0);
  });

  it('demo: responders see it and her ASHA accepts by herself', () => {
    const { store, advance, fire } = setup(true);
    fire();
    advance(3000 + 6000);
    const s = store.snapshot()!;
    expect(s.stage).toBe('coming');
    expect(s.acceptedBy).toBe('sunita');
    expect(s.responders.find((r) => r.id === 'sunita')?.state).toBe('coming');
  });

  it('dismiss forgets only a finished incident', () => {
    const { store, fire } = setup();
    fire();
    store.dismiss();
    expect(store.snapshot()).not.toBeNull();
    store.cancel();
    store.dismiss();
    expect(store.snapshot()).toBeNull();
    expect(fire()).toBeTruthy(); // a new SOS can start
  });
});
