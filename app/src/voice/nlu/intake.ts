import { questionsFor, URGENT_SIGNS, WATCH_SIGNS, type AnswerRecord, type Question, type Sign, type Visit } from '@/data/mockScreening';
import type { StringKey } from '@/i18n/strings';

import { extract, type Facts, type Polarity } from './extract';
import type { Concept } from './lexicon';

export type { Sign } from '@/data/mockScreening';

// The check-in as SLOT FILLING, with no language model in the loop.
//
// Every answer is turned into facts (extract.ts) and offered to EVERY
// question of the visit: the one just asked reads it fully (bare numbers, a
// plain yes / no); the others take only what was said explicitly ("ಇದು
// ಮೊದಲನೇ ಗರ್ಭ", "ಹೊಟ್ಟೆ ನೋವು ಇದೆ"). That is the PREFILL.
//
// What happens next is decided from the slots alone:
//   next question   the first one, in order, that is not yet answered and
//                   not skippable. Skippable = NOT a danger question and its
//                   slots already filled by earlier answers (or not
//                   applicable: no past deliveries in a first pregnancy, no
//                   "less movement?" once she said the baby moves as usual).
//                   A danger question is ALWAYS asked, even when an earlier
//                   answer mentioned the sign: a denial or a sign must come
//                   from that question
//   follow-up       a PARTLY answered question asks only for what is
//                   missing: "yes" to "previous deliveries?" -> "how many?",
//                   "no headache" -> "and blurred vision or swelling?". A
//                   danger question with signs only DENIED is done when every
//                   sign was answered (or a plain no / don't know covered
//                   them): a sign she never answered is never recorded as
//                   "No". A sign that is PRESENT completes it
//   same question   asked again ONCE when the answer did not fill it; after
//                   that it is recorded as "Not clear" and the check-in moves
//                   on. A danger question that stays unclear is flagged for
//                   the doctor: a missed sign costs more than a second look
// DANGER SIGNS ARE HEARD IN ANY ANSWER (the MCP danger-sign protocol): a
// sign said in "anything worrying you?" or "new problems?" is recorded with
// that answer (`reported`) and raises its alert at once, even when this
// visit has no question about it. History questions are the exception: "I
// was treated for fits" (epilepsy, in the past) is history, not a fit now.
// Each question writes the English record the doctor and the rules read.

type Src = { q: string; stated: boolean };
type Slot<T> = { v: T } & Src;
type YN = Slot<boolean>;

export type Slots = {
  name?: Slot<string>;
  age?: Slot<number>;
  village?: Slot<string>;
  firstPregnancy?: YN;
  prevPregnancies?: Slot<number>;
  lmp?: Slot<string>;
  gaMonths?: Slot<number>;
  regular?: Slot<'regular' | 'irregular'>;
  confirmed?: Slot<string | false>;
  deliveries?: Slot<number>;
  /** "Yes" to previous deliveries, with no number yet. */
  deliveriesYes?: YN;
  losses?: Slot<string[]>;
  pastProblems?: Slot<string[]>;
  csection?: YN;
  conditions?: Slot<string[]>;
  longIllness?: Slot<string[]>;
  medicines?: Slot<string[]>;
  allergy?: YN;
  transfusion?: YN;
  tobacco?: Slot<string[]>;
  concerns?: Slot<string>;
  newProblems?: Slot<string>;
  tummySize?: Slot<string>;
  movementFelt?: YN;
  ifa?: Slot<Adherence>;
  calcium?: Slot<Adherence>;
  ironCalcium?: Slot<Adherence>;
  testsDone?: YN;
  followedAdvice?: YN;
  movement?: Slot<'normal' | 'reduced' | 'absent'>;
  fewer?: Slot<'yes' | 'no' | 'absent'>;
  deliveryPlace?: Slot<string | false>;
  support?: YN;
  /** Danger signs: true = present, false = denied. */
  signs: Partial<Record<Sign, Slot<boolean>>>;
  /** Signs heard in a question's answer that are not that question's own (see DANGER SIGNS above). */
  reported: Map<string, Sign[]>;
  /** Questions answered "don't know". */
  unknown: Set<string>;
  /** A danger question answered "yes" with no sign named. */
  vagueYes: Set<string>;
};
type Adherence = 'daily' | 'irregular' | 'no';

const SIGN_OF: Partial<Record<Concept, Sign>> = {
  HEADACHE: 'headache', VISION: 'vision', SWELL_FACE: 'swellFace', SWELL_FEET: 'swellFeet', PALLOR: 'pallor', ABD_PAIN: 'abdPain',
  BLEEDING: 'bleeding', LEAKING: 'leaking', FITS: 'fits', FEVER: 'fever', VOMITING: 'vomiting', BREATHLESS: 'breathless',
  DISCHARGE: 'discharge', TIRED: 'tired', PALPITATIONS: 'palpitations',
};
export const SIGN_LABEL: Record<Sign, string> = {
  headache: 'Headache', vision: 'Blurred vision', swellFace: 'Swelling of face / hands', swellFeet: 'Swollen feet', pallor: 'Pallor',
  abdPain: 'Abdominal pain', bleeding: 'Bleeding', leaking: 'Leaking fluid', fits: 'Fits', fever: 'Fever', vomiting: 'Vomiting',
  breathless: 'Breathlessness', discharge: 'Unusual discharge', tired: 'Unusual tiredness', palpitations: 'Palpitations', reducedMovement: 'Reduced baby movements',
};

/** Reduced movement is a red flag from 28 weeks (sakhi_triage.py THIRD_TRIMESTER_WEEKS); unknown gestation counts. */
export const MOVEMENT_FLAG_WEEKS = 28;

type Ctx = { f: Facts; s: Slots; q: string; current: boolean; expect?: Expect; weeks?: number };

/** The one slot a follow-up asked for: a bare number in the reply goes there. */
export type Expect = 'count' | 'date';
/**
 * What a partly answered question still needs, as the line that asks for
 * it: a fixed line (`key`), or the danger signs she has not answered yet
 * (voice/nlu/followup.ts words them in her language).
 */
export type FollowUp = { key: StringKey; expect: Expect } | { signs: Sign[] };

type Spec = {
  /** Danger questions are never skipped. */
  danger: boolean;
  /** About the past: the signs it mentions are history, never a danger sign now. */
  history?: boolean;
  /** Say-only (the closing line): never listens. */
  sayOnly?: boolean;
  fill(c: Ctx): void;
  done(s: Slots): boolean;
  /** Partly answered: what is still missing (null = nothing to follow up). */
  follow?(s: Slots): FollowUp | null;
  /** Not applicable given other answers. */
  notApplicable?(s: Slots): boolean;
  record(s: Slots, weeks?: number): AnswerRecord;
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
const plainNoOf = (c: Ctx) => c.f.global === 'no' || c.f.seen.has('NOTHING');

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
  // "swelling" alone means this question's swelling (face / hands here)
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
/** A watch sign (discharge, tiredness...) is recorded for the doctor but raises no alarm. */
const alarming = (signs: Sign[]) => signs.some((sg) => !WATCH_SIGNS.has(sg));
function signRecord(s: Slots, q: string, signs: Sign[]): AnswerRecord {
  const present = signs.filter((sg) => s.signs[sg]?.v === true);
  // A sign she was asked about but never answered is "not answered", never "No".
  if (present.length) return { value: present.map((sg) => SIGN_LABEL[sg] + (WATCH_SIGNS.has(sg) ? ' (check)' : '')).join(', '), danger: alarming(present), signs: present };
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
  bleedDischarge: ['bleeding', 'discharge'],
  feverPain: ['fever', 'vomiting', 'abdPain'],
  bleedFluid: ['bleeding', 'leaking'],
  bleedLeak: ['bleeding', 'leaking'],
  warning: ['headache', 'vision', 'swellFace'],
  breathing: ['breathless', 'tired', 'palpitations'],
};
const yesNo = (c: Ctx, concepts: Concept[]): boolean | undefined => {
  for (const k of concepts) {
    const p = pol(c, k);
    if (p !== undefined) return p === 1;
  }
  if (!c.current) return undefined;
  if (c.f.global === 'yes' || c.f.seen.has('NORMAL')) return true;
  if (c.f.global === 'no' || c.f.seen.has('NOTHING')) return false;
  return undefined;
};
/** A plain yes / no question (what it asks, and how each answer reads for the doctor). */
function yesNoSpec(key: keyof Slots, concepts: Concept[], yes: string, no: string, opts: { history?: boolean; notApplicable?: (s: Slots) => boolean } = {}): Spec {
  return {
    danger: false,
    history: opts.history,
    notApplicable: opts.notApplicable,
    fill(c) {
      const v = yesNo(c, concepts);
      if (v !== undefined && (c.current || concepts.some((k) => pol(c, k) !== undefined))) set(c, key, v as never);
      if (c.current && v === undefined && c.f.global === 'unknown') c.s.unknown.add(c.q);
    },
    done: (s) => !!s[key] || s.unknown.has(String(key)),
    record: (s) => {
      const v = (s[key] as YN | undefined)?.v;
      return { value: v === true ? yes : v === false ? no : s.unknown.has(String(key)) ? 'Not known' : 'Not clear', danger: false };
    },
  };
}
/** A medicine she should take every day: daily / not regularly / no. */
function adherenceSpec(key: 'ifa' | 'calcium' | 'ironCalcium', concepts: Concept[]): Spec {
  return {
    danger: false,
    fill(c) {
      if (!c.current) return;
      if (pol(c, 'SOMETIMES') === 1 || pol(c, 'DAILY') === -1) set(c, key, 'irregular');
      else if (pol(c, 'DAILY') === 1 || concepts.some((k) => pol(c, k) === 1) || c.f.global === 'yes') set(c, key, 'daily');
      else if (concepts.some((k) => pol(c, k) === -1) || c.f.global === 'no') set(c, key, 'no');
      else if (c.f.global === 'unknown') c.s.unknown.add(key);
    },
    done: (s) => !!s[key] || s.unknown.has(key),
    record: (s) => {
      const v = (s[key] as Slot<Adherence> | undefined)?.v;
      return { value: v === 'daily' ? 'Yes, every day' : v === 'irregular' ? 'Not regularly (check)' : v === 'no' ? 'No (check)' : 'Not known', danger: false };
    },
  };
}
/** Named items of a list question ("which conditions?"), or none, or "yes" with none named. */
function listSpec(key: 'losses' | 'pastProblems' | 'conditions' | 'longIllness' | 'tobacco', items: [Concept, string][], opts: { history?: boolean; notApplicable?: (s: Slots) => boolean; extra?: (c: Ctx) => string[] } = {}): Spec {
  return {
    danger: false,
    history: opts.history,
    notApplicable: opts.notApplicable,
    fill(c) {
      const found = items.filter(([k]) => pol(c, k) === 1).map(([, label]) => label);
      if (c.current) found.push(...(opts.extra?.(c) ?? []));
      if (found.length && (c.current || key === 'tobacco')) set(c, key, [...new Set(found)]);
      else if (c.current && (plainNoOf(c) || items.some(([k]) => pol(c, k) === -1))) set(c, key, []);
      else if (c.current && c.f.global === 'yes') set(c, key, ['Yes (not named: check)']);
      else if (c.current && c.f.global === 'unknown') c.s.unknown.add(key);
    },
    done: (s) => !!s[key] || s.unknown.has(key),
    record: (s) => {
      const v = (s[key] as Slot<string[]> | undefined)?.v;
      return { value: v?.length ? v.join(', ') : v ? 'No' : s.unknown.has(key) ? 'Not known' : 'Not clear', danger: false };
    },
  };
}
/** An open question ("anything worrying you?"): her own words, or "No". */
function openSpec(key: 'concerns' | 'newProblems' | 'tummySize'): Spec {
  return {
    danger: false,
    fill(c) {
      if (!c.current) return;
      if (plainNoOf(c) && !c.f.said.size) set(c, key, '');
      else if (plainNoOf(c) && [...c.f.said.values()].every((p) => p === -1)) set(c, key, '');
      else if (c.f.global === 'unknown') c.s.unknown.add(key);
      else if (c.f.raw.trim()) set(c, key, c.f.raw.trim());
    },
    done: (s) => !!s[key] || s.unknown.has(key),
    record: (s) => {
      const v = s[key]?.v;
      return { value: v ? `Reported: "${v}" (check)` : v === '' ? 'No' : s.unknown.has(key) ? 'Not sure' : 'Not clear', danger: false };
    },
  };
}

const ORD = ['', '1st', '2nd', '3rd', '4th', '5th'];
/** Words around a name that are not the name ("my name is", "her age"). */
const NAME_FILLER = new Set([
  'ನನ್ನ', 'ಅವರ', 'ಅವಳ', 'ಅವರು', 'ಅವಳು', 'ಇವರ', 'ಇವಳ', 'ತಾಯಿ', 'ತಾಯಿಯ', 'ಅಮ್ಮ', 'ಅವರದು', 'ಇದು', 'ಆಗಿದೆ', 'ವರ್ಷದ', 'ವರ್ಷಗಳು', 'ನಾನು', 'ಊರು', 'ಹಳ್ಳಿ', 'ಗ್ರಾಮ', 'ನಮ್ಮ', 'ಇರುವುದು', 'ವಾಸ',
  'मेरा', 'मेरी', 'उनका', 'उनकी', 'इनका', 'इनकी', 'माता', 'मां', 'का', 'की', 'के', 'है', 'हैं', 'उसका', 'उसकी', 'मैं', 'गाँव', 'गांव', 'में', 'रहती', 'हूँ', 'हूं', 'हमारा',
  'my', 'her', 'the', 'mother', 'mothers', 'is', 'and', 'old', 'she', 'name', 'age', 'years', 'i', 'live', 'in', 'village', 'from', 'am', 'at',
]);
const wordsOf = (c: Ctx) => c.f.rest.filter((w) => /[\p{L}\p{M}]{2,}/u.test(w) && !NAME_FILLER.has(w)).join(' ').trim();
const firstPreg = (s: Slots) => s.firstPregnancy?.v === true;

export const SPECS: Record<string, Spec> = {
  // ---- first visit: who she is --------------------------------------------------
  name: {
    danger: false,
    fill(c) {
      // letters AND marks: Kannada / Devanagari vowel signs are marks (ರೂಪ = ರ + ೂ + ಪ)
      if (c.current && wordsOf(c)) set(c, 'name', titleCase(wordsOf(c)));
    },
    done: (s) => !!s.name,
    record: (s) => ({ value: s.name?.v ?? 'Not clear', danger: false }),
  },
  age: {
    danger: false,
    fill(c) {
      const age = numberWith(c.f, ['YEAR', 'AGE'], 14, 55) ?? (c.current ? numberIn(c.f, 14, 55) : undefined);
      if (age !== undefined) set(c, 'age', age);
    },
    done: (s) => !!s.age,
    record: (s) => ({ value: s.age ? `${s.age.v} years` : 'Not clear', danger: false }),
  },
  village: {
    danger: false,
    fill(c) {
      if (c.current && wordsOf(c)) set(c, 'village', titleCase(wordsOf(c)));
    },
    done: (s) => !!s.village,
    record: (s) => ({ value: s.village?.v ?? 'Not clear', danger: false }),
  },
  firstPregnancy: {
    danger: false,
    history: true,
    fill(c) {
      const ord = c.f.ordinal;
      if (ord && (c.current || c.f.seen.has('PREGNANCY'))) {
        set(c, 'firstPregnancy', ord === 1);
        if (ord > 1) set(c, 'prevPregnancies', ord - 1);
      } else if (c.current) {
        const v = yesNo(c, []);
        if (v !== undefined) set(c, 'firstPregnancy', v);
      }
    },
    done: (s) => !!s.firstPregnancy,
    record: (s) => ({ value: s.firstPregnancy?.v ? 'Yes, first pregnancy' : s.firstPregnancy ? 'No' : 'Not clear', danger: false }),
  },
  prevPregnancies: {
    danger: false,
    history: true,
    fill(c) {
      if (!c.current) return;
      const n = numberWith(c.f, ['PREGNANCY', 'CHILD'], 0, 15) ?? bare(c.f).find((x) => x.value <= 15)?.value;
      if (n !== undefined) set(c, 'prevPregnancies', n);
      else if (pol(c, 'ONE_ONLY') === 1) set(c, 'prevPregnancies', 1);
      else if (pol(c, 'BOTH') === 1) set(c, 'prevPregnancies', 2);
      else if (c.f.global === 'unknown') c.s.unknown.add('prevPregnancies');
    },
    done: (s) => !!s.prevPregnancies || s.unknown.has('prevPregnancies'),
    notApplicable: firstPreg,
    record: (s) =>
      firstPreg(s) && !s.prevPregnancies
        ? { value: 'None: first pregnancy', danger: false }
        : { value: s.prevPregnancies ? `${s.prevPregnancies.v} time${s.prevPregnancies.v === 1 ? '' : 's'} before (${ORD[s.prevPregnancies.v + 1] ?? `${s.prevPregnancies.v + 1}th`} pregnancy)` : 'Not known', danger: false },
  },
  // ---- first visit: pregnancy history ----------------------------------------------
  lmp: {
    danger: false,
    history: true,
    fill(c) {
      const months = numberWith(c.f, ['MONTH'], 1, 10);
      if (months !== undefined) set(c, 'gaMonths', months);
      if (!c.current) return;
      if (c.f.date) set(c, 'lmp', c.f.date);
      else if (c.f.global === 'unknown' || c.f.global === 'no') set(c, 'lmp', 'unknown');
      else if (c.f.global === 'yes') c.s.vagueYes.add('lmp');
    },
    done: (s) => !!s.lmp,
    follow: (s) => (!s.lmp && s.vagueYes.has('lmp') ? { key: 'vAskDate', expect: 'date' } : null),
    record: (s) => ({
      value: s.lmp && s.lmp.v !== 'unknown' ? `LMP ${s.lmp.v}` : `Not remembered${s.gaMonths ? ` (about ${s.gaMonths.v} months pregnant)` : ''}`,
      danger: false,
    }),
  },
  regularPeriods: {
    danger: false,
    history: true,
    fill(c) {
      if (!c.current) return;
      if (pol(c, 'SOMETIMES') === 1 || pol(c, 'DAILY') === -1 || pol(c, 'NORMAL') === -1) set(c, 'regular', 'irregular');
      else if (pol(c, 'DAILY') === 1 || c.f.seen.has('NORMAL') || c.f.global === 'yes') set(c, 'regular', 'regular');
      else if (c.f.global === 'no') set(c, 'regular', 'irregular');
      else if (c.f.global === 'unknown') c.s.unknown.add('regularPeriods');
    },
    done: (s) => !!s.regular || s.unknown.has('regularPeriods'),
    record: (s) => ({ value: s.regular?.v === 'regular' ? 'Yes, regular' : s.regular ? 'Not regular' : 'Not known', danger: false }),
  },
  confirmed: {
    danger: false,
    history: true,
    fill(c) {
      if (!c.current) return;
      const how = [pol(c, 'PREG_TEST') === 1 && 'pregnancy test', (pol(c, 'HEALTH_CENTRE') === 1 || pol(c, 'PLACE') === 1) && 'health centre'].filter(Boolean) as string[];
      if (how.length) set(c, 'confirmed', how.join(' and '));
      else if (c.f.global === 'yes') set(c, 'confirmed', 'yes');
      else if (plainNoOf(c)) set(c, 'confirmed', false);
      else if (c.f.global === 'unknown') c.s.unknown.add('confirmed');
    },
    done: (s) => !!s.confirmed || s.unknown.has('confirmed'),
    record: (s) => ({ value: s.confirmed?.v === false ? 'Not yet confirmed (check)' : s.confirmed?.v === 'yes' ? 'Yes' : s.confirmed ? `Yes, ${s.confirmed.v}` : 'Not known', danger: false }),
  },
  deliveries: {
    danger: false,
    history: true,
    fill(c) {
      if (!c.current) return;
      const n = numberWith(c.f, ['CHILD', 'PREGNANCY'], 0, 12) ?? bare(c.f).find((x) => x.value <= 12)?.value;
      if (n !== undefined) set(c, 'deliveries', n);
      else if (pol(c, 'ONE_ONLY') === 1) set(c, 'deliveries', 1);
      else if (plainNoOf(c)) set(c, 'deliveries', 0);
      else if (c.f.global === 'yes') set(c, 'deliveriesYes', true);
      else if (c.f.global === 'unknown') c.s.unknown.add('deliveries');
    },
    done: (s) => !!s.deliveries || s.unknown.has('deliveries'),
    follow: (s) => (!s.deliveries && s.deliveriesYes ? { key: 'vAskHowMany', expect: 'count' } : null),
    notApplicable: firstPreg,
    record: (s) =>
      firstPreg(s) && !s.deliveries
        ? { value: 'None: first pregnancy', danger: false }
        : { value: s.deliveries ? (s.deliveries.v === 0 ? 'No previous deliveries' : `${s.deliveries.v} previous deliver${s.deliveries.v === 1 ? 'y' : 'ies'}`) : 'Not known', danger: false },
  },
  losses: listSpec('losses', [['MISCARRIAGE', 'Miscarriage'], ['ABORTION', 'Abortion'], ['STILLBIRTH', 'Stillbirth']], { history: true, notApplicable: firstPreg }),
  pastProblems: listSpec('pastProblems', [['CSECTION', 'C-section'], ['BLEEDING', 'Heavy bleeding'], ['FITS', 'Fits'], ['HTN', 'High BP'], ['DIABETES', 'Sugar'], ['STILLBIRTH', 'Stillbirth']], {
    history: true,
    notApplicable: firstPreg,
  }),
  csection: yesNoSpec('csection', ['CSECTION'], 'Yes, C-section before (check)', 'No', {
    history: true,
    notApplicable: (s) => firstPreg(s) || s.deliveries?.v === 0 || (s.pastProblems?.v.includes('C-section') ?? false),
  }),
  // ---- first visit: existing health conditions -----------------------------------------
  conditions: listSpec('conditions', [['HTN', 'High BP'], ['DIABETES', 'Diabetes'], ['HEART', 'Heart disease'], ['ASTHMA', 'Asthma'], ['THYROID', 'Thyroid']], { history: true }),
  longIllness: listSpec('longIllness', [['TB', 'TB'], ['FITS', 'Epilepsy']], { history: true }),
  medicines: {
    danger: false,
    history: true,
    fill(c) {
      if (!c.current) return;
      const meds: string[] = [];
      if (pol(c, 'IRON') === 1) meds.push('Iron');
      if (/calcium|ಕ್ಯಾಲ್ಸಿ|कैल्शियम/.test(c.f.text)) meds.push('Calcium');
      if (/folic|ಫೋಲಿಕ್|फोलिक/.test(c.f.text)) meds.push('Folic acid');
      if (pol(c, 'THYROID') === 1) meds.push('Thyroid');
      if (pol(c, 'DIABETES') === 1) meds.push('Sugar');
      if (pol(c, 'HTN') === 1) meds.push('BP');
      if (!meds.length && (pol(c, 'MEDICINE') === 1 || c.f.global === 'yes')) meds.push('Yes (not named: check)');
      if (meds.length) set(c, 'medicines', meds);
      else if (plainNoOf(c) || pol(c, 'MEDICINE') === -1) set(c, 'medicines', []);
      else if (c.f.global === 'unknown') c.s.unknown.add('medicines');
    },
    done: (s) => !!s.medicines || s.unknown.has('medicines'),
    record: (s) => ({ value: s.medicines?.v.length ? s.medicines.v.join(', ') : s.medicines ? 'No medicines' : 'Not known', danger: false }),
  },
  allergy: yesNoSpec('allergy', ['ALLERGY'], 'Yes, allergy (check)', 'No allergies', { history: true }),
  transfusion: yesNoSpec('transfusion', ['TRANSFUSION'], 'Yes, transfusion before', 'No', { history: true }),
  // ---- first visit: this pregnancy ---------------------------------------------------------
  bleedDischarge: dangerSigns('bleedDischarge'),
  feverPain: dangerSigns('feverPain'),
  tobacco: listSpec('tobacco', [['TOBACCO', 'Tobacco'], ['ALCOHOL', 'Alcohol']]),
  concerns: openSpec('concerns'),
  // ---- second and third visits ---------------------------------------------------------------
  newProblems: openSpec('newProblems'),
  bleedFluid: dangerSigns('bleedFluid'),
  bleedLeak: dangerSigns('bleedLeak'),
  warning: dangerSigns('warning', { concept: 'SWELLING', sign: 'swellFace' }),
  breathing: dangerSigns('breathing'),
  // Her own words about her tummy (the ASHA examines fundal height): recorded for a check.
  tummySize: openSpec('tummySize'),
  movementFelt: {
    danger: false,
    fill(c) {
      if (!c.current) return;
      const mv = pol(c, 'MOVEMENT');
      if (mv === 1 || c.f.global === 'yes' || c.f.seen.has('NORMAL')) set(c, 'movementFelt', true);
      else if (mv === -1 || c.f.global === 'no') set(c, 'movementFelt', false);
      else if (c.f.global === 'unknown') c.s.unknown.add('movementFelt');
    },
    done: (s) => !!s.movementFelt || s.unknown.has('movementFelt'),
    // Not yet felt is recorded as such, never as a danger sign: it depends on the weeks.
    record: (s) => ({ value: s.movementFelt?.v ? 'Yes, feels movements' : s.movementFelt ? 'Not yet felt' : 'Not sure', danger: false }),
  },
  ifa: adherenceSpec('ifa', ['IRON']),
  calcium: adherenceSpec('calcium', ['IRON', 'MEDICINE']),
  ironCalcium: adherenceSpec('ironCalcium', ['IRON', 'MEDICINE']),
  testsDone: yesNoSpec('testsDone', ['TEST'], 'Yes, done', 'Not yet (check)'),
  followedAdvice: yesNoSpec('followedAdvice', ['DOCTOR', 'HEALTH_CENTRE'], 'Yes', 'No (check)'),
  movement: {
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
      else if (c.f.global === 'unknown') c.s.unknown.add('movement');
    },
    done: (s) => (!!s.movement && s.movement.q === 'movement') || s.unknown.has('movement'),
    record: (s, weeks) => movementRecord(s.movement?.v, s.unknown.has('movement'), weeks),
  },
  movementLess: {
    // Asked only when "moving as usual?" did not settle it (the inverse question: "yes" = fewer).
    danger: false,
    fill(c) {
      if (!c.current) return;
      if (pol(c, 'MOVEMENT') === -1 && pol(c, 'REDUCED') !== 1) set(c, 'fewer', 'absent');
      else if (pol(c, 'REDUCED') === 1 || c.f.global === 'yes') set(c, 'fewer', 'yes');
      else if (c.f.global === 'no' || c.f.seen.has('NORMAL') || c.f.seen.has('NOTHING')) set(c, 'fewer', 'no');
      else if (c.f.global === 'unknown') c.s.unknown.add('movementLess');
    },
    done: (s) => !!s.fewer || s.unknown.has('movementLess'),
    notApplicable: (s) => !!s.movement && s.movement.q === 'movement',
    record(s, weeks) {
      // Settled by "moving as usual?": that answer carries any flag.
      if (s.movement && s.movement.q === 'movement' && !s.fewer) return { value: s.movement.v === 'normal' ? 'No decrease' : 'See the previous answer', danger: false };
      const v = s.fewer?.v;
      return v === 'no' ? { value: 'No decrease', danger: false } : movementRecord(v === 'absent' ? 'absent' : v === 'yes' ? 'reduced' : undefined, s.unknown.has('movementLess') || !v, weeks);
    },
  },
  deliveryPlace: {
    danger: false,
    fill(c) {
      if (!c.current) return;
      const place = /brims|ಬ್ರಿಮ್ಸ್|ಬಿಆರ್ಐಎಂಎಸ್|ब्रिम्स/.test(c.f.text)
        ? 'BRIMS'
        : /taluk|ತಾಲೂಕು|ತಾಲ್ಲೂಕು|तालुक/.test(c.f.text)
          ? 'taluk hospital'
          : /district|ಜಿಲ್ಲಾ|जिला/.test(c.f.text)
            ? 'district hospital'
            : /private|ಖಾಸಗಿ|ಪ್ರೈವೇಟ್|प्राइवेट/.test(c.f.text)
              ? 'private hospital'
              : pol(c, 'HOME') === 1
                ? 'home'
                : pol(c, 'HEALTH_CENTRE') === 1
                  ? 'health centre'
                  : null;
      if (place) set(c, 'deliveryPlace', place);
      else if (c.f.global === 'yes') set(c, 'deliveryPlace', 'yes');
      else if (plainNoOf(c) || c.f.global === 'unknown') set(c, 'deliveryPlace', false);
    },
    done: (s) => !!s.deliveryPlace,
    record: (s) => {
      const v = s.deliveryPlace?.v;
      return {
        value: v === 'home' ? 'At home (check: facility delivery advised)' : v === 'yes' ? 'Yes (place not named)' : v ? `Yes, ${v}` : 'Not decided yet (check)',
        danger: false,
      };
    },
  },
  support: yesNoSpec('support', ['AMBULANCE'], 'Yes', 'No (arrange transport)'),
  close: { danger: false, sayOnly: true, fill() {}, done: () => true, record: () => ({ value: 'Summary sent', danger: false }) },
};

function dangerSigns(q: keyof typeof QUESTION_SIGNS, generic?: { concept: Concept; sign: Sign }): Spec {
  const signs = QUESTION_SIGNS[q];
  return {
    danger: true,
    fill: (c) => fillSigns(c, signs, generic),
    done: (s) => signsAnswered(s, q, signs),
    follow: (s) => signsFollow(s, q, signs),
    record: (s) => signRecord(s, q, signs),
  };
}

/** Baby's movements: reduced / absent is a danger sign from 28 weeks (or when the weeks are not known). */
function movementRecord(v: 'normal' | 'reduced' | 'absent' | undefined, unknown: boolean, weeks?: number): AnswerRecord {
  if (v === 'normal') return { value: 'Yes, moving normally', danger: false };
  const flag = weeks === undefined || weeks >= MOVEMENT_FLAG_WEEKS;
  if (v === 'reduced' || v === 'absent') {
    const value = v === 'reduced' ? 'Fewer movements' : 'No movements felt';
    return flag ? { value, danger: true, signs: ['reducedMovement'] } : { value: `${value} (before ${MOVEMENT_FLAG_WEEKS} weeks: check)`, danger: false };
  }
  return unknown ? { value: 'Not known (check)', danger: flag } : { value: 'Not clear', danger: flag };
}

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
  /** Danger signs heard in this answer that are not this question's own (raise their alert now). */
  reported: Sign[];
};

/** Signs present in an answer (any concept), and reduced movement when it counts. */
function presentSigns(f: Facts, weeks?: number): Sign[] {
  const out = (Object.entries(SIGN_OF) as [Concept, Sign][]).filter(([k]) => f.said.get(k) === 1).map(([, sg]) => sg);
  const lessMovement = f.said.has('MOVEMENT') && (f.said.get('REDUCED') === 1 || f.said.get('MOVEMENT') === -1);
  if (lessMovement && (weeks === undefined || weeks >= MOVEMENT_FLAG_WEEKS)) out.push('reducedMovement');
  return out;
}

/**
 * One check-in's understanding. The engine calls answer() after each
 * transcript and next() to know which question comes now.
 */
export class Intake {
  readonly slots: Slots = { signs: {}, reported: new Map(), unknown: new Set(), vagueYes: new Set() };
  readonly questions: Question[];
  private attempts = new Map<string, number>();
  private answered = new Set<string>();

  /** `weeks`: her gestation (reduced movement counts from 28 weeks; undefined = not known). */
  constructor(
    visit: Visit = 1,
    private readonly weeks?: number,
  ) {
    this.questions = questionsFor(visit);
  }

  /**
   * Feed one transcript for question `qid`. `expect`: the slot a follow-up
   * just asked for (its bare number / yes / no goes there).
   */
  answer(qid: string, transcript: string, expect?: Expect): TurnOutcome {
    const f = extract(transcript);
    const before = new Set(this.questions.filter((q) => this.skippable(q.id)).map((q) => q.id));
    const spec = SPECS[qid];
    // the asked question reads it fully; every other question takes explicit
    // facts (never from a history answer: a past bleed is not a bleed now)
    for (const q of this.questions) {
      const current = q.id === qid;
      if (!current && spec?.history && SPECS[q.id]?.danger) continue;
      SPECS[q.id]?.fill({ f, s: this.slots, q: q.id, current, expect: current ? expect : undefined, weeks: this.weeks });
    }
    // Danger signs said in this answer that this question does not own.
    if (spec && !spec.history && !spec.sayOnly) {
      const own = new Set(QUESTION_SIGNS[qid] ?? (qid === 'movement' || qid === 'movementLess' ? ['reducedMovement'] : []));
      const extra = presentSigns(f, this.weeks).filter((sg) => !own.has(sg));
      if (extra.length) this.slots.reported.set(qid, [...new Set([...(this.slots.reported.get(qid) ?? []), ...extra])]);
    }
    this.attempts.set(qid, (this.attempts.get(qid) ?? 0) + 1);
    const complete = spec ? spec.done(this.slots) : true;
    if (complete) this.answered.add(qid);
    const prefilled = this.questions.filter((q) => q.id !== qid && !before.has(q.id) && !this.answered.has(q.id) && this.skippable(q.id)).map((q) => q.id);
    return { complete, record: this.record(qid, transcript), prefilled, reported: this.slots.reported.get(qid) ?? [] };
  }

  /** Asked twice and still not filled: record it as it stands and move on. */
  giveUp(qid: string, transcript: string): AnswerRecord {
    this.answered.add(qid);
    const r = this.record(qid, transcript);
    const spec = SPECS[qid];
    if (spec && !spec.done(this.slots)) {
      // Part of it was answered: keep that part, say what is missing.
      if (spec.follow?.(this.slots)) return { ...r, value: `${r.value} (part not answered: check)`, danger: spec.danger || r.danger };
      const said = transcript.trim();
      return { ...r, value: said ? `Not clear: "${said}"` : 'No answer', danger: spec.danger || r.danger };
    }
    return r;
  }

  attemptsOf(qid: string) {
    return this.attempts.get(qid) ?? 0;
  }

  /** Was this question answered (its own answer, or given up on)? */
  isAnswered(qid: string) {
    return this.answered.has(qid);
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

  /** The record for a question (asked or skipped), with any danger sign reported in its answer. */
  record(qid: string, transcript = ''): AnswerRecord {
    const spec = SPECS[qid];
    if (!spec) return { value: transcript, danger: false };
    const r = spec.record(this.slots, this.weeks);
    const extra = this.slots.reported.get(qid);
    if (!extra?.length) return r;
    const signs = [...new Set([...(r.signs ?? []), ...extra])];
    return {
      value: `${r.value} · Reported: ${extra.map((sg) => SIGN_LABEL[sg]).join(', ')}`,
      danger: r.danger || extra.some((sg) => !WATCH_SIGNS.has(sg)),
      signs,
    };
  }
}

/** Is this an urgent sign (the questionnaire stops for the ASHA)? */
export const isUrgent = (sg: Sign) => URGENT_SIGNS.has(sg);
