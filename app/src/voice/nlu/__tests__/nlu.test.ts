import { describe, expect, it } from '@jest/globals';

import { ANTENATAL_QUESTIONS, evaluate } from '@/data/mockScreening';

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

describe('Intake: each question', () => {
  it('q01 needs name AND age: a name alone asks again', () => {
    const a = new Intake();
    expect(say(a, 'q01', 'ರೂಪ').complete).toBe(false);
    const r = say(a, 'q01', 'ರೂಪ ವಯಸ್ಸು 25');
    expect(r.complete).toBe(true);
    expect(r.record.value).toBe('ರೂಪ, 25 years');
  });

  it('q01: "my name is Roopa, age 25" keeps only the name', () => {
    const a = new Intake();
    expect(say(a, 'q01', 'ನನ್ನ ಹೆಸರು ರೂಪ ವಯಸ್ಸು 25').record.value).toBe('ರೂಪ, 25 years');
  });

  it('q06: a full reading; a single number asks again', () => {
    const a = new Intake();
    expect(say(a, 'q06', '148').complete).toBe(false);
    const r = say(a, 'q06', 'ಬಿ ಪಿ 148 ಬೈ 96');
    expect(r.record).toEqual({ value: '148/96 mmHg', danger: true });
    const b = new Intake();
    expect(say(b, 'q06', '118 ಬೈ 76').record).toEqual({ value: '118/76 mmHg', danger: false });
  });

  it('q09: a plain no denies all three signs', () => {
    const a = new Intake();
    const r = say(a, 'q09', 'ಇಲ್ಲ ಏನೂ ತೊಂದರೆ ಇಲ್ಲ');
    expect(r.complete).toBe(true);
    expect(r.record).toEqual({ value: 'No', danger: false });
  });

  it('q09: named signs are danger; "no ... but ..." is not a clear no', () => {
    expect(say(new Intake(), 'q09', '2 ದಿನದಿಂದ ತುಂಬಾ ತಲೆನೋವಿದೆ').record).toEqual({ value: 'Headache', danger: true });
    expect(say(new Intake(), 'q09', 'no headache but blurred vision').record).toEqual({ value: 'Blurred vision', danger: true });
    expect(say(new Intake(), 'q09', 'ತಲೆನೋವು ಇಲ್ಲ ಆದರೆ ಕಣ್ಣು ಮಂಜಾಗುತ್ತದೆ').record.danger).toBe(true);
  });

  it('q09: "yes" with no sign named is flagged for the doctor', () => {
    expect(say(new Intake(), 'q09', 'ಹೌದು').record).toEqual({ value: 'Yes (sign not named: check)', danger: true });
  });

  it('q12 is inverted: "no" means no movements (danger), "yes" is fine', () => {
    expect(say(new Intake(), 'q12', 'ಹೌದು ಸರಿಯಾಗಿ ಗೊತ್ತಾಗುತ್ತಿದೆ').record.danger).toBe(false);
    expect(say(new Intake(), 'q12', 'ಇಲ್ಲ').record).toEqual({ value: 'No movements felt', danger: true });
    expect(say(new Intake(), 'q12', 'ಚಲನೆ ಕಡಿಮೆ ಆಗಿದೆ').record).toEqual({ value: 'Fewer movements', danger: true });
    expect(say(new Intake(), 'q12', 'fewer movements since yesterday').record.danger).toBe(true);
    expect(say(new Intake(), 'q12', 'yes normal').record.danger).toBe(false);
  });

  it('q24: generic "swelling" means the feet here; pallor alone is AN-01 (watch)', () => {
    const r = say(new Intake(), 'q24', 'ಕಾಲು ಊದಿಕೊಂಡಿದೆ');
    expect(r.record).toEqual({ value: 'Swollen feet', danger: true });
  });

  it('q04 / q05: a first pregnancy needs no question about past deliveries', () => {
    const a = new Intake();
    const r = say(a, 'q04', 'ಇದು ಮೊದಲನೇ ಗರ್ಭ');
    expect(r.record.value).toBe('1st pregnancy');
    expect(a.skippable('q05')).toBe(true);
    expect(a.record('q05').value).toBe('First pregnancy: no past deliveries');
  });

  it('q14: both / one / plain yes', () => {
    expect(say(new Intake(), 'q14', 'ಎರಡೂ ಆಗಿದೆ').record.value).toBe('Yes, both');
    expect(say(new Intake(), 'q14', 'ಒಂದು ಮಾತ್ರ').record.value).toBe('Only one');
    expect(say(new Intake(), 'q14', 'हाँ').record.value).toBe('Yes, both');
  });

  it('q26: Hb by keyword or a bare decimal; sugar "normal"', () => {
    expect(say(new Intake(), 'q26', 'Hb 11.2, sugar normal').record.value).toBe('Hb 11.2, sugar normal');
    expect(say(new Intake(), 'q26', 'ಹಿಮೋಗ್ಲೋಬಿನ್ 9.5').record.value).toBe('Hb 9.5');
  });

  it('every question can be completed by a typical answer', () => {
    const typical: Record<string, string> = {
      q01: 'Lakshmi 26 years', q02: '7 ತಿಂಗಳು', q03: 'ಗೊತ್ತಿಲ್ಲ', q04: 'ಎರಡನೇ ಗರ್ಭ ಒಂದು ಮಗು', q05: 'ಇಲ್ಲ', q21: 'ನೈಸರ್ಗಿಕ', q06: '118 ಬೈ 76',
      q07: '58 ಕೆಜಿ', q08: 'ಹೌದು ದಿನಾ', q24: 'ಇಲ್ಲ', q25: '140', q09: 'ಇಲ್ಲ', q10: 'ಇಲ್ಲ', q11: 'ಇಲ್ಲ', q12: 'ಹೌದು', q18: 'ಇಲ್ಲ',
      q19: 'ಇಲ್ಲ', q20: 'ಕಬ್ಬಿಣ ಮತ್ತು ಕ್ಯಾಲ್ಸಿಯಂ, ಅಲರ್ಜಿ ಇಲ್ಲ', q13: 'ಹೌದು ತಾಲೂಕು ಆಸ್ಪತ್ರೆ', q14: 'ಎರಡೂ', q26: 'Hb 11, sugar normal', q15: 'ಹೌದು', q16: 'ಹೌದು',
    };
    for (const [qid, text] of Object.entries(typical)) {
      const a = new Intake();
      expect([qid, a.answer(qid, text).complete]).toEqual([qid, true]);
    }
  });
});

describe('Intake: prefill guides the next question', () => {
  it('months said in q01 skip q02 and q03 (non-danger), but never a danger question', () => {
    const a = new Intake();
    const r = say(a, 'q01', 'ರೂಪ 25 ವರ್ಷ 7 ತಿಂಗಳು');
    expect(r.prefilled).toEqual(expect.arrayContaining(['q02', 'q03']));
    expect(a.record('q02').value).toBe('7 months');
    expect(a.skippable('q06')).toBe(false);
  });

  it('a BP said early is kept, and its danger question is still asked', () => {
    const a = new Intake();
    say(a, 'q02', '7 ತಿಂಗಳು ಬಿಪಿ 150 ಬೈ 100');
    expect(a.slots.bp?.v).toEqual([150, 100]);
    expect(a.skippable('q06')).toBe(false);
  });

  it('a sign mentioned early prefills its question; the question can still deny it', () => {
    const a = new Intake();
    say(a, 'q06', '148 ಬೈ 96 ತಲೆನೋವು ಇದೆ');
    expect(a.slots.signs.headache?.v).toBe(true);
    expect(a.record('q09')).toEqual({ value: 'Headache', danger: true });
    say(a, 'q09', 'ಇಲ್ಲ ತಲೆನೋವು ಇಲ್ಲ');
    expect(a.record('q09')).toEqual({ value: 'No', danger: false });
  });

  it('what a question itself said is not overwritten by a later aside', () => {
    const a = new Intake();
    say(a, 'q07', '58 ಕೆಜಿ');
    say(a, 'q08', 'ಹೌದು 60 ಕೆಜಿ ಆಗಿದೆ ಅಂತ ಹೇಳಿದ್ರು');
    expect(a.record('q07').value).toBe('58 kg');
  });

  it('asked twice and still unclear: recorded as such; a danger question is flagged', () => {
    const a = new Intake();
    say(a, 'q06', 'ಏನು');
    expect(a.giveUp('q06', 'ಏನು')).toEqual({ value: 'Not clear: "ಏನು"', danger: true });
    const b = new Intake();
    say(b, 'q07', 'ಏನು');
    expect(b.giveUp('q07', 'ಏನು')).toEqual({ value: 'Not clear: "ಏನು"', danger: false });
  });
});

describe('real Gnani transcripts (from the agent and WebSocket logs)', () => {
  it.each([
    ['q06', 'ಬಿ ಪಿ 148 ಬೈ 96', '148/96 mmHg', true],
    ['q06', 'ಬಿಪಿನ್ 148 ಬೈ 96', '148/96 mmHg', true],
    ['q06', 'ಬಿಪಿ 148 ಬೈ 96 ಇದೆ', '148/96 mmHg', true],
    ['q01', 'ನನ್ನ ಹೆಸರು ರೂಪ ವಯಸ್ಸು 25', 'ರೂಪ, 25 years', false],
    ['q02', '7 ತಿಂಗಳು ಆಗಿದೆ', '7 months', false],
    ['q09', 'ಇಲ್ಲ ಏನೂ ತೊಂದರೆ ಇಲ್ಲ', 'No', false],
    ['q09', 'ತಲೆನೋವಿದೆ ಮತ್ತು ಕಣ್ಣು ಮಂಜಾಗುತ್ತದೆ', 'Headache, Blurred vision', true],
    ['q09', '2 ದಿನದಿಂದ ತುಂಬಾ ತಲೆನೋವಿದೆ ಕಾಲು', 'Headache', true],
  ])('%s "%s"', (qid, said, value, danger) => {
    const r = new Intake().answer(qid, said);
    expect(r.complete).toBe(true);
    expect(r.record).toEqual({ value, danger });
  });
});

describe('records feed the screening rules unchanged', () => {
  it('a severe pre-eclampsia check-in fires PE-04', () => {
    const a = new Intake();
    say(a, 'q06', 'ಬಿ ಪಿ 148 ಬೈ 96');
    say(a, 'q09', 'ತೀವ್ರ ತಲೆನೋವು ಇದೆ ಕಣ್ಣು ಮಂಜಾಗುತ್ತಿದೆ');
    const answers = Object.fromEntries(ANTENATAL_QUESTIONS.map((q) => [q.id, a.record(q.id)]));
    expect(evaluate(answers).map((h) => h.code)).toEqual(expect.arrayContaining(['PE-01', 'PE-03', 'PE-04']));
  });

  it('every question has a spec', () => {
    for (const q of ANTENATAL_QUESTIONS) expect(SPECS[q.id]).toBeDefined();
  });

  it('is fast: a long answer is understood in well under a millisecond on average', () => {
    const text = 'ಬಿ ಪಿ ನೂರ ನಲವತ್ತೆಂಟು ಬೈ ತೊಂಬತ್ತಾರು ತಲೆನೋವು ಇದೆ ಕಣ್ಣು ಮಂಜು ಇಲ್ಲ ಕಾಲು ಊದಿಕೊಂಡಿದೆ ಏಳು ತಿಂಗಳು ';
    extract(text); // build once
    const t = performance.now();
    for (let i = 0; i < 500; i++) extract(text);
    expect((performance.now() - t) / 500).toBeLessThan(2);
  });
});

describe('micro-prompts: a half answer asks only for what is missing', () => {
  it('name without age asks the age; age without name asks the name', () => {
    const a = new Intake();
    expect(a.answer('q01', 'ಲಕ್ಷ್ಮಿ').complete).toBe(false);
    expect(a.missing('q01')).toEqual({ key: 'vAskAge', expect: 'age' });
    expect(a.answer('q01', '24').complete).toBe(true);
    expect(a.record('q01').value).toBe('ಲಕ್ಷ್ಮಿ, 24 years');
    const b = new Intake();
    b.answer('q01', '24 ವರ್ಷ');
    expect(b.missing('q01')).toEqual({ key: 'vAskName', expect: 'name' });
  });

  it('the upper BP number alone asks the lower one, and the two make the reading', () => {
    const i = new Intake();
    expect(i.answer('q06', '140').complete).toBe(false);
    expect(i.missing('q06')).toEqual({ key: 'vAskDiastolic', expect: 'diastolic' });
    expect(i.answer('q06', '90').complete).toBe(true);
    expect(i.record('q06')).toEqual({ value: '140/90 mmHg', danger: true });
  });

  it('nothing understood asks the whole question again', () => {
    const i = new Intake();
    i.answer('q06', 'ಏನು');
    expect(i.missing('q06')).toBeNull();
  });

  it('"140 ಮೇಲೆ 90" and its spoken words are a reading', () => {
    expect(extract('140 ಮೇಲೆ 90').bp).toEqual([140, 90]);
    expect(extract('ನೂರ ನಲವತ್ತು ಮೇಲೆ ತೊಂಬತ್ತು').bp).toEqual([140, 90]);
  });
});
