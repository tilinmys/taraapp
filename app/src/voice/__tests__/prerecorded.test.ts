import { describe, expect, it } from '@jest/globals';

import { ANTENATAL_QUESTIONS, questionText, SAKHI_REPLIES } from '@/data/mockScreening';
import { STRINGS } from '@/i18n/strings';

import { allFollowUps } from '../nlu/followup';
import { prerecorded } from '../prerecorded';
import { PRERECORDED } from '../prerecorded.generated';

// Every line Sakhi says in a check-in must be recorded in her voice, in
// both languages. A line reworded without re-running
// scripts/prerecord_sakhi.py fails here (instead of quietly costing a TTS
// request on every check-in).

describe('prerecorded lines', () => {
  for (const lang of ['kn', 'hi'] as const) {
    it(`${lang}: all 24 questions and every fixed line are recorded`, () => {
      const lines = [
        STRINGS[lang].vGreetingVoice,
        STRINGS[lang].vReask,
        STRINGS[lang].vReprompt,
        STRINGS[lang].vWaiting,
        ...allFollowUps(lang),
        SAKHI_REPLIES[lang].danger,
        SAKHI_REPLIES[lang].done,
        ...ANTENATAL_QUESTIONS.map((q) => questionText(q, lang)),
      ];
      const missing = lines.filter((t) => !prerecorded(lang, t));
      expect(missing).toEqual([]);
    });

    it(`${lang}: recordings are real audio of a sensible length`, () => {
      for (const [key, line] of Object.entries(PRERECORDED[lang])) {
        expect([key, line.ms > 800 && line.ms < 20_000]).toEqual([key, true]);
      }
    });
  }

  it('English has no recordings: it falls back to TTS, never to the wrong words', () => {
    expect(prerecorded('en', ANTENATAL_QUESTIONS[0].en)).toBeNull();
    expect(prerecorded('kn', 'a line that was never recorded')).toBeNull();
  });
});
