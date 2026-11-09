import { beforeEach, describe, expect, it, jest } from '@jest/globals';

import { bpState, canRecheck, pendingRecheck, referNow } from '@/lib/bp';
import { kv } from '@/lib/kv';

import { ME_ASHA } from '../care-network';
import { BP_RECHECK_HOURS, store, type MotherRecord } from '../store';

// High BP on her page follows the app's rules (PE-01..PE-04): raised BP is
// referred today AND rechecked after 6 hours; severe BP, or raised BP with a
// danger sign, is referred now with no waiting. The recheck is saved with her
// record and a new check-in replaces it.

jest.mock('expo-router', () => ({ router: { canGoBack: jest.fn(), back: jest.fn(), replace: jest.fn(), push: jest.fn() } }));

const get = (id: string) => store.get().find((m) => m.id === id)!;
// A well woman (no rule hits) with her latest reading replaced.
const withBp = (sys: number, dia: number): MotherRecord => {
  const m = get('roopa');
  return { ...m, vitals: { ...m.vitals, sys: [...m.vitals.sys, sys], dia: [...m.vitals.dia, dia] } };
};

beforeEach(() => store.resetDemo());

describe('bpState', () => {
  it('normal BP shows nothing', () => {
    expect(bpState(withBp(130, 85))).toBeNull();
  });

  it('>= 140 systolic or >= 90 diastolic is raised (recheck applies)', () => {
    expect(bpState(withBp(140, 80))?.level).toBe('raised');
    expect(bpState(withBp(128, 90))?.level).toBe('raised');
  });

  it('>= 160/110 is severe (refer now)', () => {
    expect(bpState(withBp(162, 100))?.level).toBe('severe');
    expect(bpState(withBp(150, 110))?.level).toBe('severe');
  });

  it('below 90/60 is low (recheck applies)', () => {
    expect(bpState(withBp(86, 64))?.level).toBe('low');
    expect(bpState(withBp(100, 58))?.level).toBe('low');
    expect(bpState(withBp(90, 60))).toBeNull(); // the edge is normal
  });

  it('the 6-hour recheck is offered for every out-of-range BP; severe also says refer now', () => {
    expect(canRecheck(bpState(withBp(146, 92)))).toBe(true);
    expect(canRecheck(bpState(withBp(86, 56)))).toBe(true);
    expect(canRecheck(bpState(withBp(165, 112)))).toBe(true);
    expect(referNow(bpState(withBp(165, 112)))).toBe(true);
    expect(referNow(bpState(withBp(146, 92)))).toBe(false);
    expect(canRecheck(bpState(withBp(120, 80)))).toBe(false);
  });

  it('no reading, no card', () => {
    const m = get('roopa');
    expect(bpState({ ...m, vitals: { ...m.vitals, sys: [], dia: [] } })).toBeNull();
  });
});

describe('6-hour BP recheck', () => {
  it('is saved with her record, due 6 hours later', () => {
    const before = Date.now();
    store.scheduleBpRecheck('lakshmi', { by: ME_ASHA, bp: '148/96' });
    const r = get('lakshmi').bpRecheck!;
    expect(r).toMatchObject({ by: ME_ASHA, bp: '148/96' });
    expect(r.dueAt - r.at).toBe(BP_RECHECK_HOURS * 3_600_000);
    expect(r.at).toBeGreaterThanOrEqual(before);
    const saved = kv.get<{ patches: Record<string, { bpRecheck?: unknown }> }>('tara.mothers.v1');
    expect(saved?.patches.lakshmi?.bpRecheck).toMatchObject({ bp: '148/96' });
  });

  it('is pending until due, then overdue', () => {
    store.scheduleBpRecheck('lakshmi', { by: ME_ASHA, bp: '148/96' });
    const m = get('lakshmi');
    expect(pendingRecheck(m)?.overdue).toBe(false);
    expect(pendingRecheck(m, m.bpRecheck!.dueAt)?.overdue).toBe(true);
    expect(pendingRecheck(get('roopa'))).toBeNull();
  });

  it('a completed check-in (the new reading) clears it', () => {
    store.scheduleBpRecheck('lakshmi', { by: ME_ASHA, bp: '148/96' });
    store.startCheckIn('lakshmi');
    store.completeCheckIn('lakshmi', 'recheck-report');
    expect(get('lakshmi').bpRecheck).toBeUndefined();
  });
});
