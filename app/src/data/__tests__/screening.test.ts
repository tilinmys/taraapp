import { describe, expect, it } from '@jest/globals';

import { evaluate, isHighRisk, type AnswerRecord } from '../mockScreening';
import { careStatus, riskOf, sortByRisk, store } from '../store';

// Regression cases for the deterministic screening rules: the doctor's
// queue, the ASHA's "needs care" and the PDF all depend on them.

const a = (value: string, danger = false): AnswerRecord => ({ value, danger });
const codes = (answers: Record<string, AnswerRecord>) => evaluate(answers).map((h) => h.code);

describe('screening rules', () => {
  it('normal answers fire nothing', () => {
    expect(codes({ q06: a('118/76 mmHg'), q09: a('No'), q12: a('Yes') })).toEqual([]);
  });

  it('BP 140/90 is PE-01; 160/110 is PE-02 (not both)', () => {
    expect(codes({ q06: a('140/88 mmHg') })).toEqual(['PE-01']);
    expect(codes({ q06: a('138/90 mmHg') })).toEqual(['PE-01']);
    expect(codes({ q06: a('139/89 mmHg') })).toEqual([]);
    expect(codes({ q06: a('162/100 mmHg') })).toEqual(['PE-02']);
  });

  it('high BP + headache/vision is severe pre-eclampsia (PE-04)', () => {
    const hits = codes({ q06: a('148/96 mmHg', true), q09: a('Severe headache, blurred vision', true) });
    expect(hits).toEqual(expect.arrayContaining(['PE-01', 'PE-03', 'PE-04']));
    expect(isHighRisk(evaluate({ q06: a('148/96 mmHg', true), q09: a('x', true) }))).toBe(true);
  });

  it('each danger question fires its own rule', () => {
    expect(codes({ q11: a('Yes, one fit', true) })).toEqual(['EC-01']);
    expect(codes({ q10: a('Spotting', true) })).toEqual(['HM-01']);
    expect(codes({ q12: a('Fewer movements', true) })).toEqual(['FM-01']);
    expect(codes({ q18: a('Fever', true) })).toEqual(['IN-01']);
  });

  it('pallor / swelling alone is WATCH, not high risk', () => {
    const hits = evaluate({ q24: a('Both feet swollen', true) });
    expect(hits.map((h) => h.code)).toEqual(['AN-01']);
    expect(isHighRisk(hits)).toBe(false);
  });
});

describe('store: risk, status and check-ins', () => {
  it('the seeded queue puts high risk first', () => {
    store.resetDemo();
    const sorted = sortByRisk(store.get());
    const firstNormal = sorted.findIndex((m) => !riskOf(m).high);
    expect(sorted.slice(0, firstNormal).every((m) => riskOf(m).high)).toBe(true);
    expect(sorted.slice(firstNormal).every((m) => !riskOf(m).high)).toBe(true);
  });

  it('riskOf is cached per answers object and refreshed when answers change', () => {
    store.resetDemo();
    const m = store.get().find((x) => x.id === 'kavitha')!;
    expect(riskOf(m).hits).toBe(riskOf(m).hits); // same object: cached
    store.startCheckIn('kavitha');
    store.recordAnswer('kavitha', 'q11', a('Yes, one fit', true));
    store.completeCheckIn('kavitha', 'r1');
    const after = store.get().find((x) => x.id === 'kavitha')!;
    expect(riskOf(after).high).toBe(true); // new answers, new result
  });

  it("the ASHA's own flag overrides the computed status", () => {
    store.resetDemo();
    store.setFlag('kavitha', 'needs_care');
    const m = store.get().find((x) => x.id === 'kavitha')!;
    expect(careStatus(m)).toBe('alert');
    expect(riskOf(m).high).toBe(true);
  });

  it('a check-in is filed once, even if completed twice (re-render, reload)', () => {
    store.resetDemo();
    store.startCheckIn('roopa');
    store.recordAnswer('roopa', 'q01', a('Roopa K., 21'));
    store.completeCheckIn('roopa', 'rep-1');
    store.completeCheckIn('roopa', 'rep-1');
    const m = store.get().find((x) => x.id === 'roopa')!;
    expect(m.reports.filter((r) => r.id === 'rep-1')).toHaveLength(1);
    expect(m.draft).toBeNull();
  });

  it('an abandoned check-in leaves the record exactly as it was', () => {
    store.resetDemo();
    const before = store.get().find((x) => x.id === 'meena')!;
    store.startCheckIn('meena');
    store.recordAnswer('meena', 'q11', a('Yes', true));
    store.discardCheckIn('meena');
    const after = store.get().find((x) => x.id === 'meena')!;
    expect(after.answers).toBe(before.answers);
    expect(after.reports).toBe(before.reports);
    expect(after.draft).toBeNull();
  });

  it('a new report needs a fresh referral', () => {
    store.resetDemo();
    store.confirmReferral('lakshmi', ['Come today']);
    store.startCheckIn('lakshmi');
    store.completeCheckIn('lakshmi', 'again');
    expect(store.get().find((x) => x.id === 'lakshmi')!.referred).toBe(false);
  });
});
