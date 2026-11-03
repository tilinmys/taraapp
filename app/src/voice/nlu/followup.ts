import { format, STRINGS, type Lang, type StringKey } from '@/i18n/strings';

import { QUESTION_SIGNS, type FollowUp, type Sign } from './intake';

// The words of a follow-up, in the language Sakhi speaks to her.
// Danger signs she has not answered yet are asked together, in the
// question's own order: "ಮತ್ತು ಕಣ್ಣು ಮಂಜಾಗುವುದು ಅಥವಾ ಮುಖ ಅಥವಾ ಕೈ ಊತ ಇದೆಯಾ?".
// Every possible line is listed by allFollowUps(), so all of them are
// recorded in Sakhi's voice (scripts/prerecord_sakhi.py builds the same
// texts; voice/__tests__/prerecorded.test.ts fails if one is missing).

const SIGN_WORD: Record<Sign, StringKey> = {
  headache: 'sgHeadache',
  vision: 'sgVision',
  swellFace: 'sgSwellFace',
  swellFeet: 'sgSwellFeet',
  pallor: 'sgPallor',
  abdPain: 'sgAbdPain',
  bleeding: 'sgBleeding',
  leaking: 'sgLeaking',
  fits: 'sgFits',
  fever: 'sgFever',
  vomiting: 'sgVomiting',
  breathless: 'sgBreathless',
};

export function signsLine(lang: Lang, signs: Sign[]) {
  const s = STRINGS[lang];
  return format(s.vAlsoSigns, { signs: signs.map((sg) => s[SIGN_WORD[sg]]).join(s.vOr) });
}

export function followUpText(lang: Lang, f: FollowUp) {
  return 'key' in f ? STRINGS[lang][f.key] : signsLine(lang, f.signs);
}

/** Every proper, non-empty subset of a question's signs (what can be left unanswered). */
function leftovers(signs: Sign[]): Sign[][] {
  const out: Sign[][] = [];
  for (let mask = 1; mask < (1 << signs.length) - 1; mask++) out.push(signs.filter((_, i) => mask & (1 << i)));
  return out;
}

/** All follow-up lines a check-in can say, in this language. */
export function allFollowUps(lang: Lang): string[] {
  const keys: StringKey[] = ['vAskName', 'vAskAge', 'vAskDiastolic', 'vAskPara', 'vAskAllergy', 'vAskMeds', 'vAskHb', 'vAskSugar'];
  const lines = keys.map((k) => STRINGS[lang][k]);
  for (const signs of Object.values(QUESTION_SIGNS)) for (const rest of leftovers(signs)) lines.push(signsLine(lang, rest));
  return [...new Set(lines)];
}
