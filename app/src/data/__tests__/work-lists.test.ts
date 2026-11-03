import { beforeEach, describe, expect, it, jest } from '@jest/globals';

import { dayPlanStore, reasonFor, suggest } from '../day-plan';
import { byTriage, doctorReview, reviewOf, triageOf } from '../doctor-review';
import { store, type MotherRecord } from '../store';

// The ASHA's day plan and the doctor's review memory: small stores the
// screens trust completely, so their rules are pinned down here.

jest.mock('expo-router', () => ({ router: { canGoBack: jest.fn(), back: jest.fn(), replace: jest.fn() } }));

const women = () => store.get().filter((m) => m.asha === 'Sunita');
const plan = () => dayPlanStore.snapshot().sunita;

beforeEach(() => {
  store.resetDemo();
  dayPlanStore.reset();
  doctorReview.reset();
});

describe('day plan', () => {
  it('suggests needs-care first, then not seen for a week; nobody without a reason', () => {
    const ids = suggest(women());
    const reasons = ids.map((id) => reasonFor(women().find((m) => m.id === id)!, undefined));
    expect(reasons.every(Boolean)).toBe(true);
    const rank = { needs_care: 0, not_checked: 1, doctor_soon: 2, overdue: 3 };
    for (let i = 1; i < reasons.length; i++) expect(rank[reasons[i]!]).toBeGreaterThanOrEqual(rank[reasons[i - 1]!]);
    expect(ids).toContain('roopa'); // 9 days without a check-in
  });

  it('move swaps neighbours and stops at the ends', () => {
    const w = women();
    const first = suggest(w)[0];
    dayPlanStore.move('sunita', w, first, -1); // already first: no change
    expect(plan().order[0]).toBe(first);
    dayPlanStore.move('sunita', w, first, 1);
    expect(plan().order[1]).toBe(first);
  });

  it('markVisited is idempotent and never undoes a ticked visit', () => {
    const w = women();
    dayPlanStore.markVisited('sunita', w, 'roopa');
    const at = plan().visited.roopa;
    dayPlanStore.markVisited('sunita', w, 'roopa');
    expect(plan().visited.roopa).toBe(at);
    // a woman not on today's list is added by a finished check-in
    dayPlanStore.markVisited('sunita', w, 'shabana');
    expect(plan().order).toContain('shabana');
    expect(plan().visited.shabana).toBeTruthy();
  });

  it('toggleVisited is the ASHA undo', () => {
    const w = women();
    dayPlanStore.toggleVisited('sunita', w, 'meena');
    expect(plan().visited.meena).toBeTruthy();
    dayPlanStore.toggleVisited('sunita', w, 'meena');
    expect(plan().visited.meena).toBeUndefined();
  });
});

describe('doctor review memory', () => {
  const m = (id: string) => store.get().find((x) => x.id === id) as MotherRecord;

  it('urgent cases come first; normal old reports start as reviewed', () => {
    const order = byTriage(store.get()).map((x) => triageOf(x));
    expect(order[0]).toBe('urgent');
    expect(triageOf(m('kavitha'))).toBe('done');
  });

  it('a referral + review moves an urgent case to "referred"', () => {
    store.confirmReferral('lakshmi', ['Come today']);
    doctorReview.markReviewed(m('lakshmi'));
    expect(triageOf(m('lakshmi'))).toBe('referred');
  });

  it('a NEWER report makes a reviewed case "new" again', () => {
    doctorReview.markReviewed(m('kavitha'));
    expect(reviewOf(m('kavitha'))).not.toBeNull();
    store.startCheckIn('kavitha');
    store.completeCheckIn('kavitha', 'fresh-report');
    expect(reviewOf(m('kavitha'))).toBeNull();
    expect(triageOf(m('kavitha'))).toBe('new');
  });

  it('recently opened keeps the last 3, most recent first, no duplicates', () => {
    ['a', 'b', 'c', 'b', 'd'].forEach((id) => doctorReview.opened(id));
    expect(doctorReview.snapshot().recent).toEqual(['d', 'b', 'c']);
  });

  it('undo forgets the review', () => {
    doctorReview.markReviewed(m('meena'));
    doctorReview.undoReviewed('meena');
    expect(reviewOf(m('meena'))).toBeNull();
  });
});

describe('goBack', () => {
  it('goes back when there is history, else to the role home (never a dead button)', () => {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const { goBack } = require('@/lib/nav') as typeof import('@/lib/nav');
    const { router } = jest.requireMock('expo-router') as { router: { canGoBack: jest.Mock<() => boolean>; back: jest.Mock; replace: jest.Mock } };
    router.canGoBack.mockReturnValue(true);
    goBack('/asha');
    expect(router.back).toHaveBeenCalledTimes(1);
    router.canGoBack.mockReturnValue(false);
    goBack('/asha');
    expect(router.replace).toHaveBeenCalledWith('/asha');
  });
});
