import { ANTENATAL_QUESTIONS, type AnswerRecord } from '@/data/mockScreening';
import type { StringKey } from '@/i18n/strings';

import { extract, type Facts, type Polarity } from './extract';
import type { Concept } from './lexicon';

// The check-in as SLOT FILLING, with no language model in the loop.
//
// Every answer is turned into facts (extract.ts) and offered to EVERY
// question: the one just asked reads it fully (bare numbers, a plain yes /
// no); the others take only what was said explicitly ("7 ತಿಂಗಳು", "ಬಿಪಿ
// 148 ಬೈ 96", "ಮೊದಲನೇ ಗರ್ಭ"). That is the PREFILL.
//
// What happens next is decided from the slots alone:
//   next question   the first one, in order, that is not yet answered and
//                   not skippable. Skippable = NOT a danger question and its
//                   slots already filled by earlier answers (or not
//                   applicable: no past deliveries in a first pregnancy, no
//                   period date needed once months are known). A danger
//                   question is ALWAYS asked, even when an earlier answer
//                   mentioned the sign: a denial or a sign must come from
//                   that question
//   follow-up       a PARTLY answered question asks only for what is
//                   missing (Spec.follow): "Lakshmi" -> "and her age?",
//                   "140" -> "and the lower number?", "no headache" -> "and
//                   blurred vision or swelling?". A danger question with
//                   signs only DENIED is done when every sign was answered
//                   (or a plain no / don't know covered them): a sign she
//                   never answered is never recorded as "No". A sign that is
//                   PRESENT completes it (the doctor examines her anyway). A bare number given to a
//                   follow-up goes to the slot it asked for (answer's expect).
//   same question   asked again ONCE when the answer did not fill it (no
//                   number for the BP, no age, nothing understood); after
//                   that it is recorded as "Not clear" and the check-in
//                   moves on. A danger question that stays unclear is
//                   flagged for the doctor: a missed sign costs more than a
//                   second look
// Each question also writes the English record the doctor and the rules
// read (AnswerRecord), so the screening rules work unchanged.

type Src = { q: string; stated: boolean };
type Slot<T> = { v: T } & Src;

export type Slots = {
  name?: Slot<string>;
  age?: Slot<number>;
  gaMonths?: Slot<number>;
  gaWeeks?: Slot<number>;
  lmp?: Slot<string>;
  gravida?: Slot<number>;
  para?: Slot<number>;
  past?: Slot<string[]>;
  conception?: Slot<'natural' | 'ivf'>;
  bp?: Slot<[number, number]>;
  /** The upper BP number alone, waiting for the lower one ("ಬಿಪಿಯ ಕೆಳಗಿನ ಸಂಖ್ಯೆ?"). */
  bpSys?: Slot<number>;
  weight?: Slot<number>;
  iron?: Slot<'daily' | 'irregular' | 'no'>;
  fhr?: Slot<number | 'checked' | 'not checked'>;
  scan?: Slot<boolean>;
  scanPlace?: Slot<string>;
  tt?: Slot<0 | 1 | 2>;
  hb?: Slot<number>;
  sugar?: Slot<number | 'normal'>;
  history?: Slot<string[]>;
  meds?: Slot<string[]>;
  allergy?: Slot<boolean>;
  staff?: Slot<boolean>;
  ambulance?: Slot<boolean>;
  movement?: Slot<'normal' | 'reduced' | 'absent'>;
  /** Danger signs: true = present, false = denied. */
  signs: Partial<Record<Sign, Slot<boolean>>>;
  /** Questions answered "don't know". */
  unknown: Set<string>;
  /** A danger question answered "yes" with no sign named. */
  vagueYes: Set<string>;
};

export type Sign = 'headache' | 'vision' | 'swellFace' | 'swellFeet' | 'pallor' | 'abdPain' | 'bleeding' | 'leaking' | 'fits' | 'fever' | 'vomiting' | 'breathless';

const SIGN_OF: Partial<Record<Concept, Sign>> = {
  HEADACHE: 'headache', VISION: 'vision', SWELL_FACE: 'swellFace', SWELL_FEET: 'swellFeet', PALLOR: 'pallor', ABD_PAIN: 'abdPain',
  BLEEDING: 'bleeding', LEAKING: 'leaking', FITS: 'fits', FEVER: 'fever', VOMITING: 'vomiting', BREATHLESS: 'breathless',
};
const SIGN_LABEL: Record<Sign, string> = {
  headache: 'Headache', vision: 'Blurred vision', swellFace: 'Swelling of face / hands', swellFeet: 'Swollen feet', pallor: 'Pallor',
  abdPain: 'Stomach pain', bleeding: 'Bleeding', leaking: 'Leaking fluid', fits: 'Fits', fever: 'Fever', vomiting: 'Vomiting', breathless: 'Breathlessness',
};

type Ctx = { f: Facts; s: Slots; q: string; current: boolean; expect?: Expect };

/** The one slot a follow-up asked for: a bare number in the reply goes there. */
export type Expect = 'name' | 'age' | 'diastolic' | 'para' | 'allergy' | 'meds' | 'hb' | 'sugar';
/**
 * What a partly answered question still needs, as the line that asks for
 * it: a fixed line (`key`), or the danger signs she has not answered yet
 * (voice/nlu/followup.ts words them in her language).
 */
export type FollowUp = { key: StringKey; expect: Expect } | { signs: Sign[] };

type Spec = {
  /** Danger questions are never skipped. */
  danger: boolean;
  /** Say-only (the closing line): never listens. */
  sayOnly?: boolean;
  fill(c: Ctx): void;
  done(s: Slots): boolean;
  /** Partly answered: what is still missing (null = nothing to follow up). */
  follow?(s: Slots): FollowUp | null;
  /** Not applicable given other answers. */
  notApplicable?(s: Slots): boolean;
  record(s: Slots): AnswerRecord;
};

const set = <K extends keyof Slots>(c: Ctx, key: K, v: NonNullable<Slots[K]> extends Slot<infer T> ? T : never) => {
  const cur = c.s[key] as Slot<unknown> | undefined;
  // What a question itself said is never overwritten by an aside in another answer.
  if (cur && cur.stated && !c.current) return;
  (c.s as Record<string, unknown>)[key as string] = { v, q: c.q, stated: c.current };
};
const pol = (c: Ctx, k: Concept): Polarity | undefined => c.f.said.get(k);
const numberWith = (f: Facts, units: Concept[], lo: number, hi: number) =>
  f.numbers.find((n) => (n.unit && units.includes(n.unit)) || (n.marker && units.includes(n.marker)))?.value ?? numberIn(f, lo, hi, units, true);
function numberIn(f: Facts, lo: number, hi: number, units: Concept[] = [], markedOnly = false) {
  const n = f.numbers.find((x) => x.value >= lo && x.value <= hi && (!markedOnly || (x.unit && units.includes(x.unit)) || (x.marker && units.includes(x.marker))));
  return n?.value;
}
const bare = (f: Facts) => f.numbers.filter((n) => !n.unit && !n.marker);

/**
 * Signs of a danger question. Each danger question owns its signs: a sign
 * mentioned in ANY answer prefills its owner; only the asked question can
 * deny all of them with a plain "no", or leave a vague "yes".
 */
function fillSigns(c: Ctx, signs: Sign[], generic?: { concept: Concept; sign: Sign }) {
  const named = new Set<Sign>();
  for (const [concept, sign] of Object.entries(SIGN_OF) as [Concept, Sign][]) {
    const p = pol(c, concept);
    if (p === undefined || !signs.includes(sign)) continue;
    setSign(c, sign, p === 1);
    named.add(sign);
  }
  // "swelling" alone means this question's swelling (face in q09, feet in q24)
  const g = generic && c.current ? pol(c, generic.concept) : undefined;
  if (generic && g !== undefined && !named.has(generic.sign)) {
    setSign(c, generic.sign, g === 1);
    named.add(generic.sign);
  }
  if (!c.current) return;
  // A plain "no" denies every sign of this question not named now AND not
  // answered before: "no" to the follow-up "and vision or swelling?" must
  // not undo the headache she reported a moment ago.
  // The "no" must be about the whole question: alone, "nothing", or leading
  // ("ಇಲ್ಲ, ..."). "ತಲೆನೋವು ಇಲ್ಲ" (no headache) denies the headache only.
  const plainNo = c.f.global === 'no' && (!named.size || c.f.seen.has('NOTHING') || LEADING_NO.test(c.f.text));
  if (plainNo) signs.filter((sg) => !named.has(sg) && !answeredHere(c.s, c.q, sg)).forEach((sg) => setSign(c, sg, false));
  else if (c.f.global === 'yes' && !named.size) c.s.vagueYes.add(c.q);
  else if (c.f.global === 'unknown' && !named.size) c.s.unknown.add(c.q);
}

function setSign(c: Ctx, sign: Sign, present: boolean) {
  const cur = c.s.signs[sign];
  if (cur && cur.stated && !c.current) return;
  // a sign once present stays present unless the question itself denies it
  if (cur?.v === true && !present && !c.current) return;
  c.s.signs[sign] = { v: present, q: c.q, stated: c.current };
}
/** An answer that opens with a plain no ("ಇಲ್ಲ, ...", "no, ...", "नहीं, ..."). */
const LEADING_NO = /^(?:ಇಲ್ಲ|ಇಲ್ಲಾ|ಇಲ್ರೀ|no|nahi|nahin|नहीं|नही)(?:\s|$)/;
/** Did THIS question's own answer settle this sign (yes or no)? */
const answeredHere = (s: Slots, q: string, sg: Sign) => s.signs[sg]?.q === q && !!s.signs[sg]?.stated;
/**
 * Done when a sign is PRESENT (already a danger case: the doctor examines
 * her; asking the rest would only slow the visit), when every sign was
 * answered, or on "don't know" / a vague "yes" (flagged). NOT done when she
 * only denied some: "no headache" says nothing about vision or swelling.
 */
const signsAnswered = (s: Slots, q: string, signs: Sign[]) =>
  s.unknown.has(q) || s.vagueYes.has(q) || signs.some((sg) => answeredHere(s, q, sg) && s.signs[sg]?.v === true) || signs.every((sg) => answeredHere(s, q, sg));
/** Some signs denied, the rest not answered: ask the rest (and only the rest). */
function signsFollow(s: Slots, q: string, signs: Sign[]): FollowUp | null {
  if (signsAnswered(s, q, signs)) return null;
  const rest = signs.filter((sg) => !answeredHere(s, q, sg));
  return rest.length < signs.length ? { signs: rest } : null;
}
function signRecord(s: Slots, q: string, signs: Sign[]): AnswerRecord {
  const present = signs.filter((sg) => s.signs[sg]?.v === true);
  // A sign she was asked about but never answered is "not answered", never "No".
  if (present.length) return { value: present.map((sg) => SIGN_LABEL[sg]).join(', '), danger: true };
  const unanswered = s.unknown.has(q) || s.vagueYes.has(q) ? [] : signs.filter((sg) => !answeredHere(s, q, sg));
  const rest = unanswered.length ? ` · Not answered: ${unanswered.map((sg) => SIGN_LABEL[sg]).join(', ')} (check)` : '';
  if (s.vagueYes.has(q)) return { value: 'Yes (sign not named: check)', danger: true };
  if (s.unknown.has(q)) return { value: 'Not known (check)', danger: true };
  if (unanswered.length) {
    const denied = signs.filter((sg) => answeredHere(s, q, sg));
    return { value: `${denied.length ? `No ${denied.map((sg) => SIGN_LABEL[sg].toLowerCase()).join(', ')}` : 'No answer'}${rest}`, danger: true };
  }
  return { value: 'No', danger: false };
}

/** The signs each multi-sign danger question asks about, in its own order. */
export const QUESTION_SIGNS: Record<string, Sign[]> = {
  q09: ['headache', 'vision', 'swellFace'],
  q10: ['abdPain', 'bleeding', 'leaking'],
  q18: ['fever', 'vomiting', 'breathless'],
  q24: ['pallor', 'swellFeet'],
};
const yesNo = (c: Ctx, concepts: Concept[]): boolean | undefined => {
  for (const k of concepts) {
    const p = pol(c, k);
    if (p !== undefined) return p === 1;
  }
  if (!c.current) return undefined;
  if (c.f.global === 'yes' || c.f.seen.has('NORMAL')) return true;
  if (c.f.global === 'no') return false;
  return undefined;
};

const ORD = ['', '1st', '2nd', '3rd', '4th', '5th'];
/** Words around a name that are not the name ("my name is", "her age"). */
const NAME_FILLER = new Set([
  'ನನ್ನ', 'ಅವರ', 'ಅವಳ', 'ಅವರು', 'ಅವಳು', 'ಇವರ', 'ಇವಳ', 'ತಾಯಿ', 'ತಾಯಿಯ', 'ಅಮ್ಮ', 'ಅವರದು', 'ಇದು', 'ಆಗಿದೆ', 'ವರ್ಷದ', 'ವರ್ಷಗಳು',
  'मेरा', 'मेरी', 'उनका', 'उनकी', 'इनका', 'इनकी', 'माता', 'मां', 'का', 'की', 'के', 'है', 'हैं', 'उसका', 'उसकी',
  'my', 'her', 'the', 'mother', 'mothers', 'is', 'and', 'old', 'she', 'name', 'age', 'years',
]);

export const SPECS: Record<string, Spec> = {
  q01: {
    danger: false,
    fill(c) {
      const age = numberWith(c.f, ['YEAR', 'AGE'], 14, 55) ?? (c.current ? numberIn(c.f, 14, 55) : undefined);
      if (age !== undefined) set(c, 'age', age);
      if (c.current) {
        // letters AND marks: Kannada / Devanagari vowel signs are marks (ರೂಪ = ರ + ೂ + ಪ)
        const name = c.f.rest.filter((w) => /[\p{L}\p{M}]{2,}/u.test(w) && !NAME_FILLER.has(w)).join(' ').trim();
        if (name) set(c, 'name', titleCase(name));
      }
    },
    done: (s) => !!s.name && !!s.age,
    follow: (s) => (s.name && !s.age ? { key: 'vAskAge', expect: 'age' } : s.age && !s.name ? { key: 'vAskName', expect: 'name' } : null),
    record: (s) => ({ value: [s.name?.v, s.age ? `${s.age.v} years` : null].filter(Boolean).join(', ') || 'Not clear', danger: false }),
  },
  q02: {
    danger: false,
    fill(c) {
      const months = numberWith(c.f, ['MONTH'], 1, 10);
      const weeks = numberWith(c.f, ['WEEK'], 4, 42);
      if (months !== undefined) set(c, 'gaMonths', months);
      if (weeks !== undefined) set(c, 'gaWeeks', weeks);
      if (c.current && months === undefined && weeks === undefined) {
        const n = bare(c.f)[0]?.value;
        if (n !== undefined && n >= 1 && n <= 10) set(c, 'gaMonths', n);
        else if (n !== undefined && n >= 11 && n <= 42) set(c, 'gaWeeks', n);
      }
      if (c.current && c.f.date) set(c, 'lmp', c.f.date);
    },
    done: (s) => !!(s.gaMonths || s.gaWeeks || s.lmp),
    record: (s) => ({ value: [s.gaMonths && `${s.gaMonths.v} months`, s.gaWeeks && `${s.gaWeeks.v} weeks`, s.lmp && `LMP/EDD ${s.lmp.v}`].filter(Boolean).join(', ') || 'Not clear', danger: false }),
  },
  q03: {
    danger: false,
    fill(c) {
      if (c.current && c.f.date) set(c, 'lmp', c.f.date);
      if (c.current && c.f.global === 'unknown') c.s.unknown.add('q03');
    },
    done: (s) => !!s.lmp || s.unknown.has('q03'),
    notApplicable: (s) => !!(s.gaMonths || s.gaWeeks || s.lmp),
    record: (s) => ({ value: s.lmp ? `LMP ${s.lmp.v}` : s.gaMonths || s.gaWeeks ? 'Not needed: months known' : 'Not known', danger: false }),
  },
  q04: {
    danger: false,
    fill(c) {
      const ord = c.f.ordinal;
      if (ord && (c.current || c.f.seen.has('PREGNANCY'))) set(c, 'gravida', ord);
      const kids = c.f.numbers.find((n) => n.unit === 'CHILD' || n.marker === 'CHILD');
      if (kids) set(c, 'para', kids.value);
      if (pol(c, 'CHILD') === -1 && c.current) set(c, 'para', 0);
      // (not in reply to "how many children before?": that number is the count)
      if (c.current && !ord && c.expect !== 'para') {
        const nums = bare(c.f).filter((n) => n.value <= 12);
        if (nums.length) set(c, 'gravida', nums[0].value);
        if (nums.length > 1 && !kids) set(c, 'para', nums[1].value);
      }
      // The follow-up "how many children before?": a bare number is the count.
      if (c.current && c.expect === 'para' && !kids) {
        const n = bare(c.f).find((x) => x.value <= 12)?.value;
        if (n !== undefined) set(c, 'para', n);
        else if (c.f.global === 'no') set(c, 'para', 0);
      }
      if (c.s.gravida?.v === 1 && !c.s.para) set(c, 'para', 0);
    },
    done: (s) => !!s.gravida && (s.gravida.v === 1 || !!s.para),
    follow: (s) => (s.gravida && s.gravida.v > 1 && !s.para ? { key: 'vAskPara', expect: 'para' } : null),
    record: (s) => ({
      value:
        [
          s.gravida && `${ORD[s.gravida.v] ?? `${s.gravida.v}th`} pregnancy`,
          s.para && s.gravida?.v !== 1 && (s.para.v === 0 ? 'no children before' : `${s.para.v} child${s.para.v === 1 ? '' : 'ren'} before`),
        ]
          .filter(Boolean)
          .join(', ') || 'Not clear',
      danger: false,
    }),
  },
  q05: {
    danger: false,
    fill(c) {
      const found: string[] = [];
      if (pol(c, 'CSECTION') === 1) found.push('C-section');
      if (c.current && pol(c, 'BLEEDING') === 1) found.push('Heavy bleeding');
      if (c.current && pol(c, 'FITS') === 1) found.push('Fits');
      if (found.length) set(c, 'past', found);
      else if (c.current && (c.f.global === 'no' || pol(c, 'CSECTION') === -1)) set(c, 'past', []);
      else if (c.current && c.f.global === 'yes') set(c, 'past', ['Yes (not named)']);
    },
    done: (s) => !!s.past,
    notApplicable: (s) => s.gravida?.v === 1,
    record: (s) => ({ value: s.gravida?.v === 1 && !s.past ? 'First pregnancy: no past deliveries' : s.past?.v.length ? s.past.v.join(', ') : 'No problems', danger: false }),
  },
  q21: {
    danger: false,
    fill(c) {
      if (pol(c, 'IVF') === 1) set(c, 'conception', 'ivf');
      else if (pol(c, 'NATURAL') === 1 || (c.current && (pol(c, 'IVF') === -1 || c.f.global === 'no' || c.f.seen.has('NORMAL')))) set(c, 'conception', 'natural');
    },
    done: (s) => !!s.conception,
    record: (s) => ({ value: s.conception?.v === 'ivf' ? 'After IVF treatment' : 'Natural', danger: false }),
  },
  q06: {
    danger: true,
    fill(c) {
      if (c.f.bp && (c.current || c.f.seen.has('BP') || c.f.seen.has('HTN'))) set(c, 'bp', c.f.bp);
      else if (c.current) {
        const nums = c.f.numbers.filter((n) => n.value >= 40 && n.value <= 260);
        if (nums.length >= 2 && nums[0].value > nums[1].value && nums[0].value >= 70 && nums[1].value <= 160) set(c, 'bp', [nums[0].value, nums[1].value]);
        else if (nums.length === 1) {
          // One number: the lower one when the upper is already heard, else the upper.
          const n = nums[0].value;
          const sys = c.s.bpSys?.v;
          if (sys !== undefined && n < sys && n <= 160) set(c, 'bp', [sys, n]);
          else if (n >= 70) set(c, 'bpSys', n);
        }
      }
      if (c.current && c.f.global === 'unknown') c.s.unknown.add('q06');
    },
    done: (s) => !!s.bp || s.unknown.has('q06'),
    follow: (s) => (s.bpSys && !s.bp ? { key: 'vAskDiastolic', expect: 'diastolic' } : null),
    record: (s) =>
      s.bp ? { value: `${s.bp.v[0]}/${s.bp.v[1]} mmHg`, danger: s.bp.v[0] >= 140 || s.bp.v[1] >= 90 } : { value: 'Not measured (check)', danger: true },
  },
  q07: {
    danger: false,
    fill(c) {
      const kg = numberWith(c.f, ['KG'], 30, 150) ?? (c.current ? numberIn(c.f, 30, 150) : undefined);
      if (kg !== undefined) set(c, 'weight', kg);
      if (c.current && c.f.global === 'unknown') c.s.unknown.add('q07');
    },
    done: (s) => !!s.weight || s.unknown.has('q07'),
    record: (s) => ({ value: s.weight ? `${s.weight.v} kg` : 'Not known', danger: false }),
  },
  q08: {
    danger: false,
    fill(c) {
      if (!c.current) return;
      if (pol(c, 'SOMETIMES') === 1 || pol(c, 'DAILY') === -1) set(c, 'iron', 'irregular');
      else if (pol(c, 'DAILY') === 1 || pol(c, 'IRON') === 1 || c.f.global === 'yes') set(c, 'iron', 'daily');
      else if (pol(c, 'IRON') === -1 || c.f.global === 'no') set(c, 'iron', 'no');
    },
    done: (s) => !!s.iron,
    record: (s) => ({ value: s.iron?.v === 'daily' ? 'Yes, every day' : s.iron?.v === 'irregular' ? 'Not regularly' : 'No', danger: false }),
  },
  q24: {
    danger: true,
    fill: (c) => fillSigns(c, ['pallor', 'swellFeet'], { concept: 'SWELLING', sign: 'swellFeet' }),
    done: (s) => signsAnswered(s, 'q24', ['pallor', 'swellFeet']),
    follow: (s) => signsFollow(s, 'q24', ['pallor', 'swellFeet']),
    record: (s) => signRecord(s, 'q24', ['pallor', 'swellFeet']),
  },
  q25: {
    danger: false,
    fill(c) {
      const rate = numberWith(c.f, ['FHR'], 80, 220) ?? (c.current ? numberIn(c.f, 80, 220) : undefined);
      if (rate !== undefined) set(c, 'fhr', rate);
      else if (c.current && c.f.global === 'yes') set(c, 'fhr', 'checked');
      else if (c.current && c.f.global === 'no') set(c, 'fhr', 'not checked');
      if (c.current && c.f.global === 'unknown') c.s.unknown.add('q25');
    },
    done: (s) => !!s.fhr || s.unknown.has('q25'),
    record: (s) => ({ value: typeof s.fhr?.v === 'number' ? `${s.fhr.v} per minute` : s.fhr?.v === 'checked' ? 'Checked' : s.fhr ? 'Not checked' : 'Not known', danger: false }),
  },
  q09: {
    danger: true,
    fill: (c) => fillSigns(c, ['headache', 'vision', 'swellFace'], { concept: 'SWELLING', sign: 'swellFace' }),
    done: (s) => signsAnswered(s, 'q09', ['headache', 'vision', 'swellFace']),
    follow: (s) => signsFollow(s, 'q09', ['headache', 'vision', 'swellFace']),
    record: (s) => signRecord(s, 'q09', ['headache', 'vision', 'swellFace']),
  },
  q10: {
    danger: true,
    fill: (c) => fillSigns(c, ['abdPain', 'bleeding', 'leaking']),
    done: (s) => signsAnswered(s, 'q10', ['abdPain', 'bleeding', 'leaking']),
    follow: (s) => signsFollow(s, 'q10', ['abdPain', 'bleeding', 'leaking']),
    record: (s) => signRecord(s, 'q10', ['abdPain', 'bleeding', 'leaking']),
  },
  q11: {
    danger: true,
    fill: (c) => fillSigns(c, ['fits']),
    done: (s) => signsAnswered(s, 'q11', ['fits']),
    record(s) {
      const r = signRecord(s, 'q11', ['fits']);
      return s.vagueYes.has('q11') ? { value: 'Yes, fits', danger: true } : r;
    },
  },
  q12: {
    danger: true,
    fill(c) {
      if (!c.current) {
        if (pol(c, 'MOVEMENT') !== undefined && pol(c, 'REDUCED') === 1) set(c, 'movement', 'reduced');
        return;
      }
      const reduced = pol(c, 'REDUCED');
      const mv = pol(c, 'MOVEMENT');
      if (reduced === 1) set(c, 'movement', 'reduced');
      else if (mv === -1 || (mv === undefined && c.f.global === 'no' && !c.f.seen.has('NORMAL'))) set(c, 'movement', 'absent');
      else if (mv === 1 || c.f.seen.has('NORMAL') || c.f.global === 'yes' || reduced === -1) set(c, 'movement', 'normal');
      else if (c.f.global === 'unknown') c.s.unknown.add('q12');
    },
    done: (s) => (!!s.movement && s.movement.q === 'q12') || s.unknown.has('q12'),
    record: (s) =>
      s.movement?.v === 'normal'
        ? { value: 'Yes, moving normally', danger: false }
        : s.movement?.v === 'reduced'
          ? { value: 'Fewer movements', danger: true }
          : s.movement?.v === 'absent'
            ? { value: 'No movements felt', danger: true }
            : { value: 'Not known (check)', danger: true },
  },
  q18: {
    danger: true,
    fill: (c) => fillSigns(c, ['fever', 'vomiting', 'breathless']),
    done: (s) => signsAnswered(s, 'q18', ['fever', 'vomiting', 'breathless']),
    follow: (s) => signsFollow(s, 'q18', ['fever', 'vomiting', 'breathless']),
    record: (s) => signRecord(s, 'q18', ['fever', 'vomiting', 'breathless']),
  },
  q19: {
    danger: false,
    fill(c) {
      if (!c.current) return;
      const found: string[] = [];
      if (pol(c, 'DIABETES') === 1) found.push('Diabetes');
      if (pol(c, 'HTN') === 1) found.push('High BP');
      if (pol(c, 'THYROID') === 1) found.push('Thyroid');
      if (pol(c, 'FITS') === 1) found.push('Fits');
      if (found.length) set(c, 'history', found);
      else if (c.f.global === 'no' || [...c.f.said.values()].some((p) => p === -1)) set(c, 'history', []);
      else if (c.f.global === 'yes') set(c, 'history', ['Yes (not named)']);
    },
    done: (s) => !!s.history,
    record: (s) => ({ value: s.history?.v.length ? s.history.v.join(', ') : 'No', danger: false }),
  },
  q20: {
    danger: false,
    fill(c) {
      if (pol(c, 'ALLERGY') !== undefined) set(c, 'allergy', pol(c, 'ALLERGY') === 1);
      if (!c.current) return;
      const meds: string[] = [];
      if (pol(c, 'IRON') === 1) meds.push('Iron');
      if (/calcium|ಕ್ಯಾಲ್ಸಿ|कैल्शियम/.test(c.f.text)) meds.push('Calcium');
      if (/folic|ಫೋಲಿಕ್|फोलिक/.test(c.f.text)) meds.push('Folic acid');
      if (!meds.length && pol(c, 'MEDICINE') === 1) meds.push('Yes (not named)');
      if (meds.length) set(c, 'meds', meds);
      else if (c.f.global === 'no' || pol(c, 'MEDICINE') === -1) set(c, 'meds', []);
      if (c.f.global === 'no' && pol(c, 'ALLERGY') === undefined && !meds.length) set(c, 'allergy', false);
      // Replies to a follow-up: a plain yes / no is about what it asked.
      if (c.expect === 'allergy' && pol(c, 'ALLERGY') === undefined && (c.f.global === 'yes' || c.f.global === 'no')) set(c, 'allergy', c.f.global === 'yes');
      if (c.expect === 'meds' && !meds.length && c.f.global === 'no') set(c, 'meds', []);
    },
    done: (s) => !!s.meds && !!s.allergy,
    follow: (s) => (s.meds && !s.allergy ? { key: 'vAskAllergy', expect: 'allergy' } : s.allergy && !s.meds ? { key: 'vAskMeds', expect: 'meds' } : null),
    record: (s) => ({
      value: [s.meds ? (s.meds.v.length ? s.meds.v.join(', ') : 'No medicines') : null, s.allergy ? (s.allergy.v ? 'Allergy: yes (check)' : 'No allergies') : null].filter(Boolean).join('. '),
      danger: false,
    }),
  },
  q13: {
    danger: false,
    fill(c) {
      const scan = pol(c, 'SCAN');
      if (scan !== undefined && (c.current || c.f.said.size)) set(c, 'scan', scan === 1);
      if (c.current) {
        const place = /brims|ಬ್ರಿಮ್ಸ್|ಬಿಆರ್ಐಎಂಎಸ್|ब्रिम्स/.test(c.f.text) ? 'BRIMS' : /taluk|ತಾಲೂಕು|ತಾಲ್ಲೂಕು|तालुक/.test(c.f.text) ? 'taluk hospital' : /private|ಖಾಸಗಿ|ಪ್ರೈವೇಟ್|प्राइवेट/.test(c.f.text) ? 'private' : null;
        if (place) {
          set(c, 'scanPlace', place);
          set(c, 'scan', true);
        } else if (scan === undefined && c.f.global) set(c, 'scan', c.f.global === 'yes');
      }
    },
    done: (s) => !!s.scan,
    record: (s) => ({ value: s.scan?.v ? `Yes${s.scanPlace ? `, at ${s.scanPlace.v}` : ''}` : 'Not yet', danger: false }),
  },
  q14: {
    danger: false,
    fill(c) {
      if (pol(c, 'BOTH') === 1) set(c, 'tt', 2);
      else if (pol(c, 'ONE_ONLY') === 1) set(c, 'tt', 1);
      else if (c.current) {
        const n = bare(c.f).find((x) => x.value <= 2)?.value;
        if (n !== undefined) set(c, 'tt', n as 0 | 1 | 2);
        else if (c.f.global === 'yes') set(c, 'tt', 2);
        else if (c.f.global === 'no') set(c, 'tt', 0);
      }
    },
    done: (s) => !!s.tt,
    record: (s) => ({ value: s.tt?.v === 2 ? 'Yes, both' : s.tt?.v === 1 ? 'Only one' : 'No', danger: false }),
  },
  q26: {
    danger: false,
    fill(c) {
      const hb = numberWith(c.f, ['HB'], 4, 18) ?? (c.current ? c.f.numbers.find((n) => n.value >= 4 && n.value <= 18 && (n.value % 1 !== 0 || !n.unit))?.value : undefined);
      if (hb !== undefined) set(c, 'hb', hb);
      const sugar = c.f.numbers.find((n) => (n.unit === 'SUGAR' || n.marker === 'SUGAR') && n.value >= 40 && n.value <= 500)?.value;
      if (sugar !== undefined) set(c, 'sugar', sugar);
      else if (c.current && c.f.seen.has('SUGAR') && c.f.seen.has('NORMAL')) set(c, 'sugar', 'normal');
      // Replies to "and the sugar?" / "and the Hb?": the bare number or "normal".
      if (c.current && c.expect === 'sugar' && sugar === undefined) {
        const n = bare(c.f).find((x) => x.value >= 40 && x.value <= 500)?.value;
        if (n !== undefined) set(c, 'sugar', n);
        else if (c.f.seen.has('NORMAL')) set(c, 'sugar', 'normal');
      }
      if (c.current && c.f.global === 'unknown') c.s.unknown.add('q26');
    },
    done: (s) => (!!s.hb && !!s.sugar) || s.unknown.has('q26'),
    follow: (s) => (s.unknown.has('q26') ? null : s.hb && !s.sugar ? { key: 'vAskSugar', expect: 'sugar' } : s.sugar && !s.hb ? { key: 'vAskHb', expect: 'hb' } : null),
    record: (s) => ({ value: [s.hb && `Hb ${s.hb.v}`, s.sugar && (s.sugar.v === 'normal' ? 'sugar normal' : `sugar ${s.sugar.v}`)].filter(Boolean).join(', ') || 'Not known', danger: false }),
  },
  q15: {
    danger: false,
    fill(c) {
      const v = yesNo(c, ['DOCTOR', 'NURSE']);
      if (c.current && v !== undefined) set(c, 'staff', v);
    },
    done: (s) => !!s.staff,
    record: (s) => ({ value: s.staff?.v ? 'Yes' : 'No (check the birth plan)', danger: false }),
  },
  q16: {
    danger: false,
    fill(c) {
      const v = yesNo(c, ['AMBULANCE']);
      if (c.current && v !== undefined) set(c, 'ambulance', v);
    },
    done: (s) => !!s.ambulance,
    record: (s) => ({ value: s.ambulance?.v ? 'Yes' : 'No (arrange transport)', danger: false }),
  },
  q17: { danger: false, sayOnly: true, fill() {}, done: () => true, record: () => ({ value: 'Summary sent', danger: false }) },
};

function titleCase(s: string) {
  return s.replace(/\b[a-z]/g, (m) => m.toUpperCase());
}

export type TurnOutcome = {
  /** The question now has what it needs. */
  complete: boolean;
  /** Its record so far (what the doctor sees). */
  record: AnswerRecord;
  /** Later questions this answer completed (they will be skipped). */
  prefilled: string[];
};

/**
 * One check-in's understanding. The engine calls answer() after each
 * transcript and next() to know which question comes now.
 */
export class Intake {
  readonly slots: Slots = { signs: {}, unknown: new Set(), vagueYes: new Set() };
  private attempts = new Map<string, number>();
  private answered = new Set<string>();

  /**
   * Feed one transcript for question `qid`. `expect`: the slot a follow-up
   * just asked for (its bare number / yes / no goes there).
   */
  answer(qid: string, transcript: string, expect?: Expect): TurnOutcome {
    const f = extract(transcript);
    const before = new Set(ANTENATAL_QUESTIONS.filter((q) => this.skippable(q.id)).map((q) => q.id));
    // the asked question reads it fully; every other question takes explicit facts
    for (const q of ANTENATAL_QUESTIONS) SPECS[q.id]?.fill({ f, s: this.slots, q: q.id, current: q.id === qid, expect: q.id === qid ? expect : undefined });
    this.attempts.set(qid, (this.attempts.get(qid) ?? 0) + 1);
    const spec = SPECS[qid];
    const complete = spec ? spec.done(this.slots) : true;
    if (complete) this.answered.add(qid);
    const prefilled = ANTENATAL_QUESTIONS.filter((q) => q.id !== qid && !before.has(q.id) && !this.answered.has(q.id) && this.skippable(q.id)).map((q) => q.id);
    return { complete, record: this.record(qid, transcript), prefilled };
  }

  /** Asked twice and still not filled: record it as it stands and move on. */
  giveUp(qid: string, transcript: string): AnswerRecord {
    this.answered.add(qid);
    const r = this.record(qid, transcript);
    const spec = SPECS[qid];
    if (spec && !spec.done(this.slots)) {
      // Part of it was answered: keep that part, say what is missing.
      if (spec.follow?.(this.slots)) return { value: `${r.value} (part not answered: check)`, danger: spec.danger || r.danger };
      const said = transcript.trim();
      return { value: said ? `Not clear: "${said}"` : 'No answer', danger: spec.danger };
    }
    return r;
  }

  attemptsOf(qid: string) {
    return this.attempts.get(qid) ?? 0;
  }

  /** Can question `qid` be skipped (answered earlier, or not applicable)? Never a danger question. */
  skippable(qid: string): boolean {
    const spec = SPECS[qid];
    if (!spec || spec.danger || spec.sayOnly) return false;
    return !!spec.notApplicable?.(this.slots) || spec.done(this.slots);
  }

  /**
   * What a partly answered question still lacks, as its follow-up; null =
   * complete, or nothing understood yet (then it is asked again whole).
   */
  missing(qid: string): FollowUp | null {
    const spec = SPECS[qid];
    if (!spec || spec.done(this.slots)) return null;
    return spec.follow?.(this.slots) ?? null;
  }

  isSayOnly(qid: string) {
    return !!SPECS[qid]?.sayOnly;
  }

  /** The record for a question (asked or skipped). */
  record(qid: string, transcript = ''): AnswerRecord {
    const spec = SPECS[qid];
    if (!spec) return { value: transcript, danger: false };
    return spec.record(this.slots);
  }
}
