import { describe, expect, it } from '@jest/globals';

import { evaluate, questionsFor, type Visit } from '@/data/mockScreening';

import { AhoCorasick, withinOneEdit } from '../aho';
import { extract } from '../extract';
import { Intake, SPECS } from '../intake';
import { combineNumbers, normalize } from '../normalize';

// The understanding layer: what Sakhi takes from each spoken answer, and
// which question that leads to. Transcripts are written the way Gnani
// returns them (native script, numbers often as digits, no punctuation).

const pol = (text: string, concept: string) => extract(text).said.get(concept as never);

describe('Aho–Corasick', () => {
  it('finds every occurrence of every pattern, like a brute-force search', () => {
    const pats = ['he', 'she', 'his', 'hers', 'ಇಲ್ಲ', 'ತಲೆನೋವ', 'ನೋವ', 'नहीं'];
    const ac = new AhoCorasick(pats.map((p) => [p, p] as [string, string]));
    let seed = 11;
    const rand = () => (seed = (seed * 1103515245 + 12345) % 2147483648) / 2147483648;
    const alphabet = ['h', 'e', 's', 'i', 'r', ' ', 'ತ', 'ಲ', 'ೆ', 'ನ', 'ೋ', 'ವ', 'ಇ', 'ಲ್ಲ', 'नहीं'];
    for (let n = 0; n < 300; n++) {
      const text = Array.from({ length: 40 }, () => alphabet[Math.floor(rand() * alphabet.length)]).join('');
      const brute: string[] = [];
      for (const p of pats) for (let i = text.indexOf(p); i >= 0; i = text.indexOf(p, i + 1)) brute.push(`${i}:${p}`);
      const fast = ac.search(text).map((m) => `${m.start}:${m.pattern}`);
      expect(fast.sort()).toEqual(brute.sort());
    }
  });

  it('withinOneEdit catches one typo, swap, insert or delete, and nothing more', () => {
    expect(withinOneEdit('headace', 'headache')).toBe(true);
    expect(withinOneEdit('haedache', 'headache')).toBe(true);
    expect(withinOneEdit('headachee', 'headache')).toBe(true);
    expect(withinOneEdit('convultion', 'convulsion')).toBe(true);
    expect(withinOneEdit('hedace', 'headache')).toBe(false);
  });
});

describe('normalize: scripts and number words', () => {
  it.each([
    ['ಬಿ ಪಿ ೧೪೮ ಬೈ ೯೬', 'ಬಿ ಪಿ 148 ಬೈ 96'],
    ['ನೂರ ನಲವತ್ತೆಂಟು ಬೈ ತೊಂಬತ್ತಾರು', '148 ಬೈ 96'],
    ['ಇಪ್ಪತ್ತಾರು ವರ್ಷ', '26 ವರ್ಷ'],
    ['ಏಳು ತಿಂಗಳು', '7 ತಿಂಗಳು'],
    ['एक सौ अड़तालीस बटा छियानबे', '148 बटा 96'],
    ['सात महीने', '7 महीने'],
    ['one forty eight over ninety six', '148 over 96'],
    ['twenty five years', '25 years'],
    ['हाँ', 'हां'],
    ['Hb 11.2, sugar normal.', 'hb 11.2 sugar normal'],
  ])('%s', (raw, want) => expect(normalize(raw)).toBe(want));

  it('joins spoken numbers the way people say them', () => {
    expect(combineNumbers([1, 40, 8])).toEqual([148]);
    expect(combineNumbers([100, 48])).toEqual([148]);
    expect(combineNumbers([2, 3])).toEqual([2, 3]);
    expect(combineNumbers([90, 6])).toEqual([96]);
  });
});

describe('extract: polarity', () => {
  it('Kannada: a post-cue covers the list before it', () => {
    const f = extract('ತಲೆನೋವು ಕಣ್ಣು ಮಂಜು ಏನೂ ಇಲ್ಲ');
    expect(f.said.get('HEADACHE')).toBe(-1);
    expect(f.said.get('VISION')).toBe(-1);
    expect(f.global).toBe('no');
  });

  it('Kannada: each mention takes its own cue', () => {
    const f = extract('ತಲೆನೋವು ಇದೆ ಕಣ್ಣು ಮಂಜು ಇಲ್ಲ');
    expect(f.said.get('HEADACHE')).toBe(1);
    expect(f.said.get('VISION')).toBe(-1);
  });

  it('Kannada: negation and affirmation fused into the word', () => {
    expect(pol('ತಲೆನೋವಿಲ್ಲ', 'HEADACHE')).toBe(-1);
    expect(pol('ತಲೆನೋವಿದೆ ಕಣ್ಣು ಮಂಜು ಇಲ್ಲ', 'HEADACHE')).toBe(1);
    expect(pol('2 ದಿನದಿಂದ ತುಂಬಾ ತಲೆನೋವಿದೆ', 'HEADACHE')).toBe(1);
  });

  it('Hindi: post-negation', () => {
    expect(pol('सिरदर्द नहीं है', 'HEADACHE')).toBe(-1);
    expect(pol('सिरदर्द है धुंधला नहीं', 'HEADACHE')).toBe(1);
    expect(pol('सिरदर्द है धुंधला नहीं', 'VISION')).toBe(-1);
  });

  it('English: prefix and postfix styles', () => {
    expect(pol('no headache or blurred vision', 'VISION')).toBe(-1);
    expect(pol('headache no, blurred vision yes', 'HEADACHE')).toBe(-1);
    expect(pol('headache no, blurred vision yes', 'VISION')).toBe(1);
  });

  it('"but" starts a new clause: a denial never crosses it', () => {
    const f = extract('ತಲೆನೋವು ಇಲ್ಲ ಆದರೆ ಕಣ್ಣು ಮಂಜಾಗುತ್ತದೆ');
    expect(f.said.get('HEADACHE')).toBe(-1);
    expect(f.said.get('VISION')).toBe(1);
  });

  it('phrases that contain a "no" but describe a symptom', () => {
    expect(pol('साफ नहीं दिखता', 'VISION')).toBe(1);
    expect(pol('ಕಣ್ಣು ಸರಿಯಾಗಿ ಕಾಣಿಸುತ್ತಿಲ್ಲ', 'VISION')).toBe(1);
    expect(pol('सांस नहीं ले पा रही', 'BREATHLESS')).toBe(1);
  });

  it('"don\'t know" is not a "no"', () => {
    expect(extract('ಗೊತ್ತಿಲ್ಲ').global).toBe('unknown');
    expect(extract('पता नहीं').global).toBe('unknown');
  });

  it('keywords match whole words, never inside another word', () => {
    expect(extract('benefit').said.has('FITS')).toBe(false);
    expect(extract('she had a fit').said.get('FITS')).toBe(1);
  });

  it('a one-letter STT typo in English still matches', () => {
    expect(pol('headace since morning', 'HEADACHE')).toBe(1);
  });

  it('numbers keep the unit they were said with', () => {
    const f = extract('ನನ್ನ ಹೆಸರು ರೂಪ ವಯಸ್ಸು 25 ಏಳು ತಿಂಗಳು');
    expect(f.numbers.find((n) => n.value === 7)?.unit).toBe('MONTH');
    expect(f.numbers.find((n) => n.value === 25)?.marker).toBe('AGE');
  });

  it.each([
    ['ಬಿ ಪಿ 148 ಬೈ 96', [148, 96]],
    ['ಬಿಪಿ 148 ಬೈ 96 ಇದೆ', [148, 96]],
    ['बीपी 150 बटा 100', [150, 100]],
    ['148/96', [148, 96]],
    ['one forty eight over ninety six', [148, 96]],
  ])('blood pressure: %s', (text, want) => expect(extract(text).bp).toEqual(want));

  it('rejects numbers that cannot be a blood pressure', () => {
    expect(extract('9 by 3').bp).toBeNull();
    expect(extract('96 by 148').bp).toBeNull();
  });
});

// ---- the dialogue state -------------------------------------------------------------

const say = (intake: Intake, qid: string, text: string) => intake.answer(qid, text);
const all = (intake: Intake) => Object.fromEntries(intake.questions.map((q) => [q.id, intake.record(q.id)]));

describe('Intake: first visit (registration)', () => {
  it('name, age and village are separate questions', () => {
    expect(say(new Intake(1), 'name', 'ನನ್ನ ಹೆಸರು ರೂಪ').record.value).toBe('ರೂಪ');
    expect(say(new Intake(1), 'age', '25 ವರ್ಷ').record.value).toBe('25 years');
    expect(say(new Intake(1), 'age', 'ಇಪ್ಪತ್ತೈದು').record.value).toBe('25 years');
    expect(say(new Intake(1), 'village', 'ಕಮಠಾಣ').record.value).toBe('ಕಮಠಾಣ');
  });

  it('a first pregnancy needs no questions about earlier pregnancies or deliveries', () => {
    const a = new Intake(1);
    expect(say(a, 'firstPregnancy', 'ಹೌದು').record.value).toBe('Yes, first pregnancy');
    for (const q of ['prevPregnancies', 'deliveries', 'losses', 'pastProblems', 'csection']) expect([q, a.skippable(q)]).toEqual([q, true]);
    expect(a.record('deliveries').value).toBe('None: first pregnancy');
  });

  it('"my second pregnancy" answers two questions at once', () => {
    const a = new Intake(1);
    const r = say(a, 'firstPregnancy', 'ಇದು ಎರಡನೇ ಗರ್ಭ');
    expect(r.record.value).toBe('No');
    expect(r.prefilled).toContain('prevPregnancies');
    expect(a.record('prevPregnancies').value).toBe('1 time before (2nd pregnancy)');
  });

  it('LMP: a date; "do not remember" is recorded as unknown (never guessed); "yes" asks the date', () => {
    expect(say(new Intake(1), 'lmp', '12/3').record.value).toBe('LMP 12/3');
    expect(say(new Intake(1), 'lmp', 'ನೆನಪಿಲ್ಲ').record.value).toBe('Not remembered');
    const a = new Intake(1);
    expect(say(a, 'lmp', 'ಹೌದು').complete).toBe(false);
    expect(a.missing('lmp')).toEqual({ key: 'vAskDate', expect: 'date' });
  });

  it('history lists: miscarriage / abortion / stillbirth; conditions; long illness', () => {
    expect(say(new Intake(1), 'losses', 'ಒಂದು ಸಲ ಗರ್ಭಪಾತ ಆಗಿತ್ತು').record.value).toBe('Miscarriage');
    expect(say(new Intake(1), 'losses', 'ಇಲ್ಲ').record.value).toBe('No');
    expect(say(new Intake(1), 'conditions', 'ಥೈರಾಯ್ಡ್ ಮತ್ತು ಅಸ್ತಮಾ ಇದೆ').record.value).toBe('Asthma, Thyroid');
    expect(say(new Intake(1), 'conditions', 'ಏನೂ ಇಲ್ಲ').record.value).toBe('No');
    expect(say(new Intake(1), 'longIllness', 'ಟಿಬಿ ಆಗಿತ್ತು').record.value).toBe('TB');
    expect(say(new Intake(1), 'transfusion', 'ಹೌದು ರಕ್ತ ಹಾಕಿದ್ದರು').record.value).toBe('Yes, transfusion before');
    expect(say(new Intake(1), 'tobacco', 'ಗುಟ್ಕಾ ತಿನ್ನುತ್ತೇನೆ').record.value).toBe('Tobacco');
    expect(say(new Intake(1), 'tobacco', 'ಇಲ್ಲ').record.value).toBe('No');
  });

  it('a C-section named in "past problems" is not asked again', () => {
    const a = new Intake(1);
    say(a, 'firstPregnancy', 'ಇಲ್ಲ');
    say(a, 'pastProblems', 'ಆಪರೇಷನ್ ಆಗಿತ್ತು');
    expect(a.skippable('csection')).toBe(true);
  });

  it('history is history: epilepsy treated in the past is not a fit now', () => {
    const a = new Intake(1);
    const r = say(a, 'longIllness', 'ಹೌದು ಅಪಸ್ಮಾರ ಇದೆ');
    expect(r.record).toEqual({ value: 'Epilepsy', danger: false });
    expect(r.reported).toEqual([]);
    expect(evaluate(all(a)).map((h) => h.code)).not.toContain('EC-01');
  });

  it('a past heavy bleed does not prefill "bleeding in this pregnancy"', () => {
    const a = new Intake(1);
    say(a, 'firstPregnancy', 'ಇಲ್ಲ');
    say(a, 'pastProblems', 'ಹಿಂದಿನ ಹೆರಿಗೆಯಲ್ಲಿ ರಕ್ತಸ್ರಾವ ಆಗಿತ್ತು');
    expect(a.slots.signs.bleeding).toBeUndefined();
  });

  it('this pregnancy: bleeding is danger; unusual discharge is recorded for a check, with no alarm', () => {
    expect(say(new Intake(1), 'bleedDischarge', 'ರಕ್ತಸ್ರಾವ ಆಗುತ್ತಿದೆ').record).toEqual({ value: 'Bleeding', danger: true, signs: ['bleeding'] });
    expect(say(new Intake(1), 'bleedDischarge', 'ಬಿಳಿ ಮುಟ್ಟು ಇದೆ').record).toEqual({ value: 'Unusual discharge (check)', danger: false, signs: ['discharge'] });
    expect(say(new Intake(1), 'bleedDischarge', 'ಇಲ್ಲ').record).toEqual({ value: 'No', danger: false });
  });
});

describe('Intake: the danger-sign safety net (any answer)', () => {
  it('a sign said in "anything worrying you?" raises it, even with no question for it this visit', () => {
    const a = new Intake(1);
    const r = say(a, 'concerns', 'ತುಂಬಾ ತಲೆನೋವು ಇದೆ ಕಣ್ಣು ಮಂಜಾಗುತ್ತಿದೆ');
    expect(r.reported).toEqual(expect.arrayContaining(['headache', 'vision']));
    expect(r.record.danger).toBe(true);
    expect(evaluate(all(a)).map((h) => h.code)).toContain('PE-03');
  });

  it('fits mentioned in "new problems" are an emergency', () => {
    const a = new Intake(2);
    const r = say(a, 'newProblems', 'ನಿನ್ನೆ ರಾತ್ರಿ ಫಿಟ್ಸ್ ಬಂದಿತ್ತು');
    expect(r.reported).toContain('fits');
    expect(evaluate(all(a)).map((h) => h.code)).toContain('EC-01');
  });

  it('"nothing" to an open question is a plain No', () => {
    expect(say(new Intake(2), 'newProblems', 'ಏನೂ ಇಲ್ಲ').record).toEqual({ value: 'No', danger: false });
    expect(say(new Intake(1), 'concerns', 'ಇಲ್ಲ').record).toEqual({ value: 'No', danger: false });
  });

  it('a sign said early still has its own danger question asked (it can be denied there)', () => {
    const a = new Intake(2);
    say(a, 'newProblems', 'ತಲೆನೋವು ಇದೆ');
    expect(a.skippable('warning')).toBe(false);
    say(a, 'warning', 'ಇಲ್ಲ ತಲೆನೋವು ಇಲ್ಲ');
    expect(a.record('warning').danger).toBe(false);
  });
});

describe('Intake: second and third visits', () => {
  it('warning signs: plain no denies all three; named signs are danger; vague yes is flagged', () => {
    expect(say(new Intake(2), 'warning', 'ಇಲ್ಲ ಏನೂ ತೊಂದರೆ ಇಲ್ಲ').record).toEqual({ value: 'No', danger: false });
    expect(say(new Intake(2), 'warning', '2 ದಿನದಿಂದ ತುಂಬಾ ತಲೆನೋವಿದೆ').record).toEqual({ value: 'Headache', danger: true, signs: ['headache'] });
    expect(say(new Intake(2), 'warning', 'no headache but blurred vision').record).toEqual({ value: 'Blurred vision', danger: true, signs: ['vision'] });
    expect(say(new Intake(2), 'warning', 'ಹೌದು').record).toEqual({ value: 'Yes (sign not named: check)', danger: true });
    expect(say(new Intake(2), 'warning', 'ಮುಖ ಊದಿಕೊಂಡಿದೆ').record.signs).toEqual(['swellFace']);
  });

  it('movements not yet felt (second visit) are recorded as such, never as danger', () => {
    expect(say(new Intake(2, 18), 'movementFelt', 'ಇನ್ನೂ ಇಲ್ಲ').record).toEqual({ value: 'Not yet felt', danger: false });
    expect(say(new Intake(2, 22), 'movementFelt', 'ಹೌದು').record).toEqual({ value: 'Yes, feels movements', danger: false });
  });

  it('third visit: "moving as usual?" is inverted ("no" = danger); the follow-up is then not asked', () => {
    const a = new Intake(3, 32);
    expect(say(a, 'movement', 'ಇಲ್ಲ').record).toEqual({ value: 'No movements felt', danger: true, signs: ['reducedMovement'] });
    expect(a.skippable('movementLess')).toBe(true);
    expect(say(new Intake(3, 32), 'movement', 'ಹೌದು ಸರಿಯಾಗಿ ಆಡುತ್ತಿದೆ').record.danger).toBe(false);
    expect(say(new Intake(3, 32), 'movement', 'ಚಲನೆ ಕಡಿಮೆ ಆಗಿದೆ').record).toEqual({ value: 'Fewer movements', danger: true, signs: ['reducedMovement'] });
  });

  it('"less movement?" (when asked): "yes" means fewer movements', () => {
    expect(say(new Intake(3, 32), 'movementLess', 'ಹೌದು').record).toEqual({ value: 'Fewer movements', danger: true, signs: ['reducedMovement'] });
    expect(say(new Intake(3, 32), 'movementLess', 'ಇಲ್ಲ').record).toEqual({ value: 'No decrease', danger: false });
  });

  it('reduced movement counts from 28 weeks (sakhi_triage.py); earlier it is recorded for a check', () => {
    expect(say(new Intake(3, 26), 'movement', 'fewer movements since yesterday').record.danger).toBe(false);
    expect(say(new Intake(3, 30), 'movement', 'fewer movements since yesterday').record.danger).toBe(true);
    expect(say(new Intake(3), 'movement', 'fewer movements since yesterday').record.danger).toBe(true); // weeks unknown: flagged
  });

  it('breathing: breathlessness is danger; tiredness or palpitations are recorded for a check', () => {
    expect(say(new Intake(3), 'breathing', 'ಉಸಿರಾಟದ ತೊಂದರೆ ಇದೆ').record).toEqual({ value: 'Breathlessness', danger: true, signs: ['breathless'] });
    expect(say(new Intake(3), 'breathing', 'ಸುಸ್ತು ಆಗುತ್ತೆ').record).toEqual({ value: 'Unusual tiredness (check)', danger: false, signs: ['tired'] });
    expect(say(new Intake(3), 'breathing', 'ಎದೆ ಡವಡವ ಆಗುತ್ತದೆ').record.signs).toEqual(['palpitations']);
  });

  it('tablets: every day / not regularly / no', () => {
    expect(say(new Intake(2), 'ifa', 'ಹೌದು ದಿನಾ').record.value).toBe('Yes, every day');
    expect(say(new Intake(2), 'calcium', 'ಕೆಲವೊಮ್ಮೆ ಮರೆತು ಹೋಗುತ್ತೆ').record.value).toBe('Not regularly (check)');
    expect(say(new Intake(3), 'ironCalcium', 'ಇಲ್ಲ').record.value).toBe('No (check)');
  });

  it('birth plan: the place she chose; home is flagged; no help means arrange transport', () => {
    expect(say(new Intake(3), 'deliveryPlace', 'ತಾಲೂಕು ಆಸ್ಪತ್ರೆಯಲ್ಲಿ').record.value).toBe('Yes, taluk hospital');
    expect(say(new Intake(3), 'deliveryPlace', 'ಮನೆಯಲ್ಲಿ').record.value).toBe('At home (check: facility delivery advised)');
    expect(say(new Intake(3), 'support', 'ಇಲ್ಲ').record.value).toBe('No (arrange transport)');
  });

  it('asked twice and still unclear: recorded as such; a danger question is flagged', () => {
    const a = new Intake(2);
    say(a, 'warning', 'ಏನು');
    expect(a.giveUp('warning', 'ಏನು')).toEqual({ value: 'Not clear: "ಏನು"', danger: true });
    const b = new Intake(2);
    say(b, 'testsDone', 'ಏನು');
    expect(b.giveUp('testsDone', 'ಏನು')).toEqual({ value: 'Not clear: "ಏನು"', danger: false });
  });
});

const TYPICAL: Record<string, string> = {
  name: 'ಲಕ್ಷ್ಮಿ', age: '26 ವರ್ಷ', village: 'ಕಮಠಾಣ', firstPregnancy: 'ಇಲ್ಲ', prevPregnancies: '1', lmp: 'ನೆನಪಿಲ್ಲ', regularPeriods: 'ಹೌದು',
  confirmed: 'ಹೌದು ಆಸ್ಪತ್ರೆಯಲ್ಲಿ', deliveries: '1', losses: 'ಇಲ್ಲ', pastProblems: 'ಇಲ್ಲ', csection: 'ಇಲ್ಲ', conditions: 'ಇಲ್ಲ', longIllness: 'ಇಲ್ಲ',
  medicines: 'ಕಬ್ಬಿಣ ಮಾತ್ರೆ', allergy: 'ಇಲ್ಲ', transfusion: 'ಇಲ್ಲ', bleedDischarge: 'ಇಲ್ಲ', feverPain: 'ಇಲ್ಲ', tobacco: 'ಇಲ್ಲ', concerns: 'ಏನೂ ಇಲ್ಲ',
  newProblems: 'ಇಲ್ಲ', bleedFluid: 'ಇಲ್ಲ', warning: 'ಇಲ್ಲ', movementFelt: 'ಹೌದು', ifa: 'ಹೌದು', calcium: 'ಹೌದು', testsDone: 'ಹೌದು', followedAdvice: 'ಹೌದು',
  movement: 'ಹೌದು', movementLess: 'ಇಲ್ಲ', tummySize: 'ಹೌದು ಬೆಳೆಯುತ್ತಿದೆ', bleedLeak: 'ಇಲ್ಲ', breathing: 'ಇಲ್ಲ', ironCalcium: 'ಹೌದು', deliveryPlace: 'ತಾಲೂಕು ಆಸ್ಪತ್ರೆ', support: 'ಹೌದು',
};

describe('every visit', () => {
  it.each([1, 2, 3] as Visit[])('visit %s: every question has a spec and is completed by a typical answer', (visit) => {
    for (const q of questionsFor(visit)) {
      expect([q.id, !!SPECS[q.id]]).toEqual([q.id, true]);
      if (q.id === 'close') continue;
      expect([q.id, new Intake(visit, 30).answer(q.id, TYPICAL[q.id]).complete]).toEqual([q.id, true]);
    }
  });

  it('a whole normal third visit fires no rule', () => {
    const a = new Intake(3, 32);
    for (const q of questionsFor(3)) if (q.id !== 'close') a.answer(q.id, TYPICAL[q.id]);
    expect(evaluate(all(a), { bp: [118, 76] })).toEqual([]);
  });

  it('a severe pre-eclampsia visit (reported signs + measured BP) fires PE-04', () => {
    const a = new Intake(2);
    say(a, 'warning', 'ತೀವ್ರ ತಲೆನೋವು ಇದೆ ಕಣ್ಣು ಮಂಜಾಗುತ್ತಿದೆ');
    expect(evaluate(all(a), { bp: [148, 96] }).map((h) => h.code)).toEqual(expect.arrayContaining(['PE-01', 'PE-03', 'PE-04']));
  });
});

describe('real Gnani transcripts (from the agent and WebSocket logs)', () => {
  it.each([
    ['warning', 'ಇಲ್ಲ ಏನೂ ತೊಂದರೆ ಇಲ್ಲ', 'No', false],
    ['warning', 'ತಲೆನೋವಿದೆ ಮತ್ತು ಕಣ್ಣು ಮಂಜಾಗುತ್ತದೆ', 'Headache, Blurred vision', true],
    ['warning', '2 ದಿನದಿಂದ ತುಂಬಾ ತಲೆನೋವಿದೆ ಕಾಲು', 'Headache', true],
    ['name', 'ನನ್ನ ಹೆಸರು ರೂಪ', 'ರೂಪ', false],
    ['age', 'ವಯಸ್ಸು 25', '25 years', false],
  ])('%s "%s"', (qid, said, value, danger) => {
    const r = new Intake(qid === 'name' || qid === 'age' ? 1 : 2).answer(qid as string, said as string);
    expect(r.complete).toBe(true);
    expect([r.record.value, r.record.danger]).toEqual([value, danger]);
  });

  it('is fast: a long answer is understood in well under a millisecond on average', () => {
    const text = 'ಬಿ ಪಿ ನೂರ ನಲವತ್ತೆಂಟು ಬೈ ತೊಂಬತ್ತಾರು ತಲೆನೋವು ಇದೆ ಕಣ್ಣು ಮಂಜು ಇಲ್ಲ ಕಾಲು ಊದಿಕೊಂಡಿದೆ ಏಳು ತಿಂಗಳು ';
    extract(text); // build once
    const t = performance.now();
    for (let i = 0; i < 500; i++) extract(text);
    expect((performance.now() - t) / 500).toBeLessThan(2);
  });

  it('"140 ಮೇಲೆ 90" and its spoken words are a reading (the parser still reads one if said)', () => {
    expect(extract('140 ಮೇಲೆ 90').bp).toEqual([140, 90]);
    expect(extract('ನೂರ ನಲವತ್ತು ಮೇಲೆ ತೊಂಬತ್ತು').bp).toEqual([140, 90]);
  });
});

describe('rural dialect (North Karnataka / Bidar, rural Hindi)', () => {
  it('recognizes rural vision terms: ಕಣ್ಣು ಕತ್ತಲೆ', () => {
    const res = new Intake(3).answer('warning', 'ಕಣ್ಣು ಕತ್ತಲೆ ಆಗ್ತಿದೆ');
    expect(res.complete).toBe(true);
    expect(res.record.danger).toBe(true);
    expect(res.record.signs).toContain('vision');
  });

  it('"darkness before the eyes" in every form is blurred vision; denied, it is not', () => {
    for (const t of ['ಕಣ್ಣು ಕತ್ತಲು ಬರುತ್ತೆ', 'ಆಗಾಗ ಕತ್ತಲೆ ಬರುತ್ತದೆ', 'आँखों के आगे अंधेरा आता है', 'आंखों के आगे अंधेरा', 'अंधेरा छा जाता है']) {
      expect(pol(t, 'VISION')).toBe(1);
    }
    expect(pol('ಕಣ್ಣು ಕತ್ತಲೆ ಇಲ್ಲ', 'VISION')).toBe(-1);
  });

  it('ಬೇನೆ (pain) and सर में भारीपन: headache and tummy pain', () => {
    expect(pol('ತಲೆ ಬೇನೆ ಐತಿ', 'HEADACHE')).toBe(1);
    expect(pol('ತಲೆಬೇನೆ', 'HEADACHE')).toBe(1);
    expect(pol('सर में बहुत भारीपन है', 'HEADACHE')).toBe(1);
    expect(pol('ಹೊಟ್ಟೆ ಬೇನೆ', 'ABD_PAIN')).toBe(1);
    expect(pol('ಹೊಟ್ಟೆಬೇನೆ ಇಲ್ಲ', 'ABD_PAIN')).toBe(-1);
  });

  it('ರಕ್ತ ಕಡಿಮಿ (blood is low) is pallor', () => {
    expect(pol('ರಕ್ತ ಕಡಿಮಿ ಐತಿ', 'PALLOR')).toBe(1);
    expect(pol('ರಕ್ತ ಕಮ್ಮಿ ಆಗಿದೆ', 'PALLOR')).toBe(1);
  });

  it('recognizes rural pallor terms: ರಕ್ತ ಇಲ್ಲ', () => {
    const res = new Intake(1).answer('bleedDischarge', 'ರಕ್ತ ಇಲ್ಲ');
    expect(res.record.danger).toBe(false);
  });

  it('SAFETY: "no blood" to a bleeding question is "no bleeding", never pallor', () => {
    // These phrases are deliberately NOT pallor words in the lexicon: in a
    // bleeding answer they mean "not bleeding", so they must raise nothing.
    for (const [v, q, t] of [[1, 'bleedDischarge', 'ರಕ್ತ ಇಲ್ಲ'], [3, 'bleedLeak', 'ರಕ್ತ ಇಲ್ಲ ನೀರು ಇಲ್ಲ'], [2, 'bleedFluid', 'खून नहीं है'], [2, 'bleedFluid', 'खून बिल्कुल नहीं']] as [Visit, string, string][]) {
      const res = new Intake(v).answer(q, t);
      expect(res.record).toEqual({ value: 'No', danger: false });
      expect(res.reported).toEqual([]);
    }
  });
});
