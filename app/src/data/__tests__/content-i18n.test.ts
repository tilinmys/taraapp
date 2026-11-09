import { describe, expect, it, jest } from '@jest/globals';

import { CONTENT, tx } from '@/i18n/content';
import { BABY_SAYS_I18N, FRUIT_I18N } from '@/services/week-i18n';

import { PLAN_PRESETS } from '../care-plan';
import { MEDICATIONS, TOPICS } from '../pregnancy';
import { store } from '../store';
import { SUGGESTED, taraAnswer } from '../tara-replies';

// Every line a mother reads is in her language: a pregnant woman who chose
// Kannada or Hindi must never meet an English sentence. A content line added
// in English without its Kannada and Hindi fails here.

jest.mock('expo-router', () => ({ router: { canGoBack: jest.fn(), back: jest.fn(), replace: jest.fn(), push: jest.fn() } }));
jest.mock('@/services/canva-media-engine', () => ({}));

const both = (en: string) => {
  const row = CONTENT[en];
  return [en, !!row && row[0].length > 0 && row[1].length > 0 && row[0] !== en && row[1] !== en];
};

describe('mother-facing content in Kannada and Hindi', () => {
  it('health topics: titles, blurbs, cards and every tip', () => {
    for (const t of Object.values(TOPICS)) {
      expect(both(t.title)).toEqual([t.title, true]);
      expect(both(t.blurb)).toEqual([t.blurb, true]);
      for (const c of t.cards) {
        expect(both(c.title)).toEqual([c.title, true]);
        for (const tip of c.tips) expect(both(tip)).toEqual([tip, true]);
      }
    }
  });

  it('medicines, the daily checklist presets, Tara’s questions and the seeded advice', () => {
    for (const m of MEDICATIONS) for (const s of [m.name, m.time]) expect(both(s)).toEqual([s, true]);
    for (const p of PLAN_PRESETS) expect(both(p.label)).toEqual([p.label, true]);
    for (const q of SUGGESTED) expect(both(q)).toEqual([q, true]);
    store.resetDemo();
    for (const m of store.get()) for (const a of m.advice ?? []) expect(both(a)).toEqual([a, true]);
  });

  it('the baby says something every week (4 to 40), in both languages; every fruit has a name', () => {
    for (let w = 4; w <= 40; w++) {
      expect([w, !!BABY_SAYS_I18N.kn[w]]).toEqual([w, true]);
      expect([w, !!BABY_SAYS_I18N.hi[w]]).toEqual([w, true]);
    }
    expect(Object.values(FRUIT_I18N).every((f) => f.kn && f.hi)).toBe(true);
  });

  it('Tara understands the suggested questions in her language, and answers in it', () => {
    for (const lang of ['kn', 'hi'] as const) {
      for (const q of SUGGESTED) {
        const r = taraAnswer(tx(q, lang), 'Sunita');
        expect([lang, q, r.keys.length > 0]).toEqual([lang, q, true]);
        expect(both(r.answer)).toEqual([r.answer, true]);
      }
    }
  });

  it('a line not in the table stays as written (a doctor’s own words)', () => {
    expect(tx('Come back on Friday.', 'kn')).toBe('Come back on Friday.');
    expect(tx('Iron + Folic acid', 'en')).toBe('Iron + Folic acid');
  });
});
