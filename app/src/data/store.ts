import { useSyncExternalStore } from 'react';

import { ANTENATAL_QUESTIONS, evaluate, isHighRisk, RULES, type AnswerRecord, type RuleHit } from '@/data/mockScreening';
import { ME_DOCTOR, dayStart, network, zonesOf } from '@/data/care-network';
import type { Lang } from '@/i18n/strings';
import { kv } from '@/lib/kv';

// Tiny reactive demo store (no dependencies). Changes made in one screen,
// such as an ASHA escalating a mother or a doctor sending advice, show up
// everywhere immediately. What the ASHA and the doctor SAVE (her details,
// the condition flag, an escalation note, the doctor's advice, women she
// added) is kept on the phone (lib/kv) and is there when the app reopens;
// check-in drafts stay in memory.

/** One line of the Sakhi conversation, as spoken (and translated). */
export type TranscriptLine = { from: 'sakhi' | 'asha'; text: string; sub?: string; danger?: boolean };

/** A finished voice check-in: what the doctor and the PDF report read. */
export type CheckInReport = {
  id: string;
  when: string;
  asha: string;
  high: boolean;
  rules: string[]; // fired rule codes, most severe first
  answers: Record<string, AnswerRecord>;
  transcript: TranscriptLine[];
};

/** Vitals over the last visits, oldest first (latest = last item). */
export type Vitals = {
  sys: number[];
  dia: number[];
  hb: number[]; // g/dL
  sugar: number[]; // mg/dL, random
  weight: number[]; // kg
};

/**
 * Her consent to the check-in (DPDP Act 2023 s.6): given to the ASHA after
 * the notice was read to her, in her language. Affirmative only: there is no
 * default, and a check-in cannot start without it. `notice` is the version of
 * the notice she heard (same versions as sakhi.patient_consents).
 */
export type Consent = { granted: boolean; at: number; by: string; notice: string; lang: string };
export const CONSENT_NOTICE_VERSION = 'v1.0';

export type MotherRecord = {
  id: string;
  name: string; // display name, e.g. "Lakshmi D."
  months: number;
  weeksLabel: string; // e.g. "30w 2d"
  village: string; // her PHC, for display
  /** Her village (zone): decides which ASHA she sees and who sees her. */
  zoneId: string;
  asha: string;
  lastCheck: string;
  /** Answers of the last COMPLETED check-in: what risk, the doctor and the mother see. */
  answers: Record<string, AnswerRecord>;
  /**
   * The check-in in progress (null when none). Kept apart from `answers` so
   * a half-finished or abandoned check-in never changes her risk, the
   * doctor's queue or her record; it is committed only by completeCheckIn.
   */
  draft: Record<string, AnswerRecord> | null;
  /** Doctor's advice once sent (numbered steps). */
  advice: string[] | null;
  /** When the doctor last sent (or updated) the advice, and who (staff id). */
  adviceAt?: number;
  adviceBy?: string;
  /** The ASHA told the doctor she needs care: what she wrote, and when. */
  escalation?: Escalation;
  /** Her consent to voice check-ins (see Consent). */
  consent?: Consent;
  referred: boolean;
  /** Past check-ins, newest first. */
  history: { when: string; high: boolean; note: string }[];
  nextVisit: string;
  /** True when the next visit falls within 7 days. */
  dueSoon: boolean;
  vitals: Vitals;
  /** Finished voice check-ins, newest first. */
  reports: CheckInReport[];
  /** The language she speaks; Sakhi talks to her in it (else the app language). */
  language?: Lang;
  /** Mobile number, if the ASHA added one (10 digits). */
  phone?: string;
  /** Added by the ASHA in this session: shown with a NEW badge until her first check-in. */
  isNew?: boolean;
  /**
   * The ASHA's own call on how she is (the condition dropdown in her village
   * list). Overrides the computed status on the ASHA's screens; "needs_care"
   * also puts her with the high-risk cases in the doctor's queue.
   */
  ashaFlag?: 'well' | 'needs_care';
};

/** What the ASHA fills in to add a pregnant woman (everything else comes from Sakhi). */
export type NewMother = { name: string; months: number; language: Lang; zoneId: string; phone?: string };
/** Her details the ASHA can correct later (the same fields as adding her). */
export type MotherDetails = NewMother;
export type Escalation = { note: string; at: number; by: string };

const weeksFor = (months: number) => `${Math.round(months * 4.35)}w 0d`;

/** A readable transcript rebuilt from saved answers (seed data, old visits). */
export function transcriptFrom(answers: Record<string, AnswerRecord>, name: string): TranscriptLine[] {
  const lines: TranscriptLine[] = [
    { from: 'sakhi', text: `Namaskara! Please answer my 24 questions about ${name} by speaking.` },
  ];
  for (const q of ANTENATAL_QUESTIONS) {
    const a = answers[q.id];
    if (!a) continue;
    lines.push({ from: 'sakhi', text: q.en }, { from: 'asha', text: a.value, danger: a.danger });
  }
  return lines;
}

/**
 * Today's reading from the spoken answers (BP q06, weight q07, Hb and sugar
 * q26), appended to the trend. Anything not stated carries the last value
 * forward, so a chart never jumps to zero.
 */
export function vitalsAfter(v: Vitals, answers: Record<string, AnswerRecord>): Vitals {
  const last = (a: number[]) => a[a.length - 1];
  const bp = answers.q06?.value.match(/(\d{2,3})\s*\/\s*(\d{2,3})/);
  const kg = answers.q07?.value.match(/(\d{2,3}(?:\.\d)?)\s*kg/i);
  const hb = answers.q26?.value.match(/hb\s*(\d{1,2}(?:\.\d)?)/i);
  const sugar = answers.q26?.value.match(/sugar\s*(\d{2,3})/i);
  // A newly added mother has no history: a value that is neither stated nor
  // known yet is simply not added (never an empty / NaN point).
  const add = (a: number[], v: number | undefined) => (v === undefined || Number.isNaN(v) ? a : [...a, v]);
  return {
    sys: add(v.sys, bp ? Number(bp[1]) : last(v.sys)),
    dia: add(v.dia, bp ? Number(bp[2]) : last(v.dia)),
    weight: add(v.weight, kg ? Number(kg[1]) : last(v.weight)),
    hb: add(v.hb, hb ? Number(hb[1]) : last(v.hb)),
    sugar: add(v.sugar, sugar ? Number(sugar[1]) : last(v.sugar)),
  };
}

function seedReport(id: string, when: string, answers: Record<string, AnswerRecord>, name: string, asha: string): CheckInReport {
  const hits = evaluate(answers);
  return {
    id: `${id}-seed`,
    when,
    asha,
    high: isHighRisk(hits),
    rules: hits.map((h) => h.code),
    answers,
    transcript: transcriptFrom(answers, name),
  };
}

function fullAnswers(overrides: Record<string, AnswerRecord>, name: string): Record<string, AnswerRecord> {
  const base: Record<string, AnswerRecord> = {};
  for (const q of ANTENATAL_QUESTIONS) base[q.id] = { value: q.sampleNormal, danger: false };
  base.q01 = { value: name, danger: false };
  return { ...base, ...overrides };
}

const initial: Omit<MotherRecord, 'draft'>[] = [
  {
    id: 'lakshmi',
    zoneId: 'kamthana',
    name: 'Lakshmi D.',
    months: 7,
    weeksLabel: '30w 2d',
    village: 'Hosahalli PHC',
    asha: 'Sunita',
    lastCheck: 'Today, 10:42',
    answers: fullAnswers(
      {
        q06: { value: '148/96 mmHg', danger: true },
        q09: { value: 'Severe headache for 2 days, blurred vision', danger: true },
        q24: { value: 'Both feet swollen since last week', danger: true },
      },
      'Lakshmi D., 26',
    ),
    advice: [
      'Come to the PHC today so the doctor can check your blood pressure again.',
      'Rest lying on your left side and avoid heavy work until you are seen.',
      'Call 108 at once if the headache gets worse, your vision gets worse, or you have a fit.',
    ],
    nextVisit: 'Today, 14:00',
    dueSoon: false,
    vitals: { sys: [118, 126, 138, 148], dia: [76, 82, 90, 96], hb: [11.2, 10.6, 10.1, 9.6], sugar: [96, 104, 108, 112], weight: [55.4, 57.0, 58.9, 61.2] },
    reports: [],
    referred: false,
    history: [
      { when: 'Today, 10:42', high: true, note: 'BP 148/96, headache, blurred vision' },
      { when: '3 weeks ago', high: false, note: 'All answers normal' },
      { when: '7 weeks ago', high: false, note: 'All answers normal' },
    ],
  },
  {
    id: 'meena',
    zoneId: 'chitta',
    name: 'Meena S.',
    months: 8,
    weeksLabel: '34w 0d',
    village: 'Kadur PHC',
    asha: 'Sunita',
    lastCheck: 'Yesterday, 16:10',
    answers: fullAnswers({ q12: { value: 'Fewer movements since yesterday', danger: true } }, 'Meena S., 23'),
    advice: null,
    nextVisit: 'Today, 16:30',
    dueSoon: true,
    vitals: { sys: [112, 114, 116, 118], dia: [72, 74, 74, 76], hb: [10.9, 10.8, 10.6, 10.6], sugar: [92, 95, 98, 98], weight: [54.1, 55.8, 56.9, 58.0] },
    reports: [],
    referred: false,
    history: [
      { when: 'Yesterday, 16:10', high: true, note: 'Fewer baby movements' },
      { when: '4 weeks ago', high: false, note: 'All answers normal' },
    ],
  },
  {
    id: 'kavitha',
    zoneId: 'kamthana',
    name: 'Kavitha R.',
    months: 5,
    weeksLabel: '21w 4d',
    village: 'Hosahalli PHC',
    asha: 'Sunita',
    lastCheck: '4 days ago',
    answers: fullAnswers({}, 'Kavitha R., 24'),
    advice: [
      'Keep taking your iron and calcium tablets every day.',
      'Eat one extra meal a day with greens, dal and milk.',
      'Your next check-in with Sunita is in 2 weeks.',
    ],
    nextVisit: 'In 2 weeks',
    dueSoon: false,
    vitals: { sys: [110, 112, 110, 112], dia: [70, 72, 70, 72], hb: [11.4, 11.6, 11.7, 11.8], sugar: [88, 90, 92, 90], weight: [50.2, 51.0, 52.1, 53.0] },
    reports: [],
    referred: false,
    history: [
      { when: '4 days ago', high: false, note: 'All answers normal' },
      { when: '5 weeks ago', high: false, note: 'All answers normal' },
    ],
  },
  {
    id: 'roopa',
    zoneId: 'aliyabad',
    name: 'Roopa K.',
    months: 3,
    weeksLabel: '13w 1d',
    village: 'Kadur PHC',
    asha: 'Sunita',
    lastCheck: '9 days ago',
    answers: fullAnswers({}, 'Roopa K., 21'),
    advice: null,
    nextVisit: 'In 3 days',
    dueSoon: true,
    vitals: { sys: [108, 110], dia: [68, 70], hb: [11.0, 11.2], sugar: [86, 89], weight: [47.0, 47.6] },
    reports: [],
    referred: false,
    history: [{ when: '9 days ago', high: false, note: 'First check-in, all normal' }],
  },
  {
    id: 'shabana',
    zoneId: 'chitta',
    name: 'Shabana B.',
    months: 6,
    weeksLabel: '25w 3d',
    village: 'Hosahalli PHC',
    asha: 'Sunita',
    lastCheck: '6 days ago',
    answers: fullAnswers({}, 'Shabana B., 24'),
    advice: null,
    nextVisit: 'In 2 weeks',
    dueSoon: false,
    vitals: { sys: [112, 114], dia: [72, 74], hb: [10.8, 11.0], sugar: [90, 94], weight: [52.0, 53.4] },
    reports: [],
    referred: false,
    language: 'hi',
    history: [{ when: '6 days ago', high: false, note: 'All answers normal' }],
  },
  {
    // Parvati's zone: never shown to Sunita (location rule).
    id: 'renuka',
    zoneId: 'janawada',
    name: 'Renuka M.',
    months: 4,
    weeksLabel: '17w 2d',
    village: 'Hosahalli PHC',
    asha: 'Parvati',
    lastCheck: '2 weeks ago',
    answers: fullAnswers({}, 'Renuka M., 22'),
    advice: null,
    nextVisit: 'In 1 week',
    dueSoon: false,
    vitals: { sys: [110], dia: [70], hb: [11.4], sugar: [88], weight: [49.5] },
    reports: [],
    referred: false,
    history: [{ when: '2 weeks ago', high: false, note: 'All answers normal' }],
  },
];

// Every seeded mother already has her latest visit as a voice report; seeded
// advice was sent by Dr. Anand yesterday.
const seeded: MotherRecord[] = initial.map((m) => ({
  ...m,
  draft: null,
  reports: [seedReport(m.id, m.lastCheck, m.answers, m.name, m.asha)],
  ...(m.advice ? { adviceAt: dayStart(-1) + 17 * 3_600_000, adviceBy: ME_DOCTOR } : {}),
}));

// --- Saved on the phone --------------------------------------------------
// Seeded women keep only the fields the ASHA / doctor changed; women she
// added are kept whole (without a check-in draft).
const SAVE_KEY = 'tara.mothers.v1';
const SAVED_FIELDS = ['name', 'months', 'weeksLabel', 'phone', 'language', 'zoneId', 'ashaFlag', 'escalation', 'consent', 'advice', 'adviceAt', 'adviceBy', 'referred'] as const;
type Saved = { added: MotherRecord[]; patches: Record<string, Partial<MotherRecord>> };

function load(): MotherRecord[] {
  const saved = kv.get<Saved>(SAVE_KEY);
  if (!saved) return seeded;
  const base = seeded.map((m) => ({ ...m, ...(saved.patches?.[m.id] ?? {}) }));
  return [...(saved.added ?? []).map((m) => ({ ...m, draft: null })), ...base];
}

function persist() {
  const seedById = new Map(seeded.map((m) => [m.id, m]));
  const patches: Saved['patches'] = {};
  const added: MotherRecord[] = [];
  for (const m of state) {
    const seed = seedById.get(m.id);
    if (!seed) {
      added.push({ ...m, draft: null });
      continue;
    }
    const patch: Partial<MotherRecord> = {};
    for (const f of SAVED_FIELDS) if (m[f] !== seed[f]) (patch as Record<string, unknown>)[f] = m[f];
    if (Object.keys(patch).length) patches[m.id] = patch;
  }
  kv.set(SAVE_KEY, { added, patches } satisfies Saved);
}

let state: MotherRecord[] = load();
const listeners = new Set<() => void>();
// Conversation captured in the session, waiting for the summary to file it
// under the report id the session chose (so the summary shows exactly that report).
const transcripts = new Map<string, { reportId: string; lines: TranscriptLine[] }>();
const emit = () => listeners.forEach((l) => l());
/** A saved change: everyone sees it now, and it is kept on the phone. */
const commit = () => {
  emit();
  persist();
};

function update(id: string, fn: (m: MotherRecord) => MotherRecord) {
  state = state.map((m) => (m.id === id ? fn(m) : m));
  emit();
}

export const store = {
  subscribe(l: () => void) {
    listeners.add(l);
    return () => listeners.delete(l);
  },
  get: () => state,
  /** A new check-in starts from a blank draft, so rules only see today's answers. */
  startCheckIn(id: string) {
    update(id, (m) => ({ ...m, draft: {} }));
  },
  recordAnswer(id: string, qid: string, answer: AnswerRecord) {
    update(id, (m) => ({ ...m, draft: { ...(m.draft ?? {}), [qid]: answer } }));
  },
  /** Undo: restore the previous value, or remove the answer entirely. */
  clearAnswer(id: string, qid: string, prev?: AnswerRecord) {
    update(id, (m) => {
      const draft = { ...(m.draft ?? {}) };
      if (prev) draft[qid] = prev;
      else delete draft[qid];
      return { ...m, draft };
    });
  },
  /** Exit without finishing: the record stays exactly as it was. */
  discardCheckIn(id: string) {
    transcripts.delete(id);
    update(id, (m) => (m.draft ? { ...m, draft: null } : m));
  },
  /** The session hands its transcript (and the report id to use) to the summary. */
  saveDraftTranscript(id: string, reportId: string, lines: TranscriptLine[]) {
    transcripts.set(id, { reportId, lines });
  },
  /**
   * Commits the draft as the mother's latest check-in and files the report.
   * Idempotent: does nothing without a draft or if that report already exists,
   * so a re-render, remount or reload can never file a check-in twice.
   */
  completeCheckIn(id: string, reportId: string) {
    const m = state.find((x) => x.id === id);
    if (!m?.draft || m.reports.some((r) => r.id === reportId)) return;
    const pending = transcripts.get(id);
    const transcript = pending?.reportId === reportId ? pending.lines : [];
    transcripts.delete(id);
    update(id, (cur) => {
      const answers = cur.draft ?? cur.answers;
      const hits = evaluate(answers);
      const high = isHighRisk(hits);
      const note = hits.length ? hits.map((h) => RULES[h.code].title).join(', ') : 'All answers normal';
      const report: CheckInReport = {
        id: reportId,
        when: 'Just now',
        asha: cur.asha,
        high,
        rules: hits.map((h) => h.code),
        answers,
        transcript,
      };
      return {
        ...cur,
        answers,
        draft: null,
        lastCheck: 'Just now',
        referred: false, // new findings need a fresh review
        history: [{ when: 'Just now', high, note }, ...cur.history],
        reports: [report, ...cur.reports],
        vitals: vitalsAfter(cur.vitals, answers),
        isNew: false,
      };
    });
    persist();
  },
  /**
   * The ASHA adds a pregnant woman. She goes to the top of the list (NEW,
   * not checked yet, normal until Sakhi's first check-in says otherwise).
   * Returns her id so the caller can open the conversation straight away.
   */
  addMother(input: NewMother) {
    const name = input.name.trim().replace(/\s+/g, ' ');
    const base = name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'mother';
    let id = base;
    for (let n = 2; state.some((m) => m.id === id); n++) id = `${base}-${n}`;
    const mother: MotherRecord = {
      id,
      name,
      months: input.months,
      weeksLabel: weeksFor(input.months),
      village: 'Hosahalli PHC',
      zoneId: input.zoneId,
      asha: 'Sunita',
      lastCheck: 'Not checked yet',
      answers: {},
      draft: null,
      advice: null,
      referred: false,
      history: [],
      nextVisit: 'Today',
      dueSoon: input.months >= 8,
      vitals: { sys: [], dia: [], hb: [], sugar: [], weight: [] },
      reports: [],
      language: input.language,
      phone: input.phone,
      isNew: true,
    };
    state = [mother, ...state];
    commit();
    return id;
  },
  /** Back to the original sample data (Profile -> Reset demo data). */
  resetDemo() {
    transcripts.clear();
    state = seeded;
    network.reset();
    kv.remove(SAVE_KEY);
    emit();
  },
  /** The ASHA marks her Well or Needs care (telling the doctor is a separate, confirmed step). */
  setFlag(id: string, flag: 'well' | 'needs_care') {
    update(id, (m) => ({ ...m, ashaFlag: flag }));
    persist();
  },
  /**
   * The ASHA corrects her details (name, months, language, village, phone).
   * Returns false when nothing valid was given (the form says why).
   */
  updateDetails(id: string, input: MotherDetails) {
    const name = input.name.trim().replace(/\s+/g, ' ');
    if (name.length < 2 || input.months < 1 || input.months > 9 || !input.zoneId) return false;
    update(id, (m) => ({
      ...m,
      name,
      months: input.months,
      weeksLabel: m.months === input.months ? m.weeksLabel : weeksFor(input.months),
      language: input.language,
      zoneId: input.zoneId,
      phone: input.phone,
    }));
    persist();
    return true;
  },
  /** The ASHA told the doctor (with what happened): the doctor's case shows her note. */
  setEscalation(id: string, note: string, by: string) {
    update(id, (m) => ({ ...m, ashaFlag: 'needs_care', escalation: { note: note.trim(), at: Date.now(), by } }));
    persist();
  },
  /** She heard the notice and agreed (or refused): recorded by the ASHA, with the notice version. */
  recordConsent(id: string, input: { granted: boolean; by: string; lang: string }) {
    update(id, (m) => ({ ...m, consent: { granted: input.granted, at: Date.now(), by: input.by, notice: CONSENT_NOTICE_VERSION, lang: input.lang } }));
    persist();
  },
  /** The doctor sends (or updates) the advice: the ASHA and the mother see it at once. */
  sendAdvice(id: string, advice: string[], by: string = ME_DOCTOR) {
    update(id, (m) => ({ ...m, advice, adviceAt: Date.now(), adviceBy: by }));
    persist();
  },
  confirmReferral(id: string, advice: string[], by: string = ME_DOCTOR) {
    update(id, (m) => ({ ...m, referred: true, advice, adviceAt: Date.now(), adviceBy: by }));
    persist();
  },
};

export function useMothers() {
  return useSyncExternalStore(store.subscribe, store.get, store.get);
}

export function useMother(id: string | undefined) {
  const all = useMothers();
  return all.find((m) => m.id === id);
}

// Rule hits per answers object. Answers are never mutated (every change
// makes a new object), so identity is a safe key; a WeakMap lets old
// answers be collected. Lists sort and filter by risk many times per render.
const hitsCache = new WeakMap<Record<string, AnswerRecord>, RuleHit[]>();

/** Derived risk for a mother: deterministic rule hits + high-risk flag. */
export function riskOf(m: MotherRecord): { hits: RuleHit[]; high: boolean } {
  let hits = hitsCache.get(m.answers);
  if (!hits) {
    hits = evaluate(m.answers);
    hitsCache.set(m.answers, hits);
  }
  return { hits, high: isHighRisk(hits) || m.ashaFlag === 'needs_care' };
}

/** High-risk mothers first, then by name. */
export function sortByRisk(list: MotherRecord[]) {
  return [...list].sort((a, b) => Number(riskOf(b).high) - Number(riskOf(a).high) || a.name.localeCompare(b.name));
}

/** Location rule: an ASHA sees only the women of her own villages (zones). */
export function useAshaMothers(ashaId: string) {
  const all = useMothers();
  const mine = new Set(zonesOf(ashaId).map((z) => z.id));
  return all.filter((m) => mine.has(m.zoneId));
}

/** One word for the ASHA: needs care (red), not checked yet (plum), well (green). */
export type CareStatus = 'alert' | 'new' | 'ok';
export function careStatus(m: MotherRecord): CareStatus {
  if (m.ashaFlag) return m.ashaFlag === 'needs_care' ? 'alert' : 'ok';
  if (m.isNew) return 'new';
  return riskOf(m).high ? 'alert' : 'ok';
}
