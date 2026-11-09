import { useSyncExternalStore } from 'react';

import { evaluate, isHighRisk, questionsFor, RULES, type AnswerRecord, type RuleCode, type RuleHit, type Sign, type Visit } from '@/data/mockScreening';
import { ME_ASHA, ME_DOCTOR, dayStart, network, zonesOf } from '@/data/care-network';
import { clinicalFacts, type ClinicalEntry } from '@/data/clinical';
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
  /** Which visit's questionnaire it was (data/mockScreening.ts VISIT_QUESTIONS). */
  visit?: Visit;
  /** When it was filed (ms). */
  at?: number;
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

/**
 * A blood-pressure recheck the ASHA set for her after a raised reading
 * (>= 140/90, rule PE-01: re-check, and refer to the PHC today). Saved with her
 * record and shown until her next check-in records a new reading.
 */
export type BpRecheck = { dueAt: number; at: number; by: string; bp: string };
export const BP_RECHECK_HOURS = 6;

/**
 * A danger sign she reported during a check-in, and what the ASHA did about
 * it when Sakhi paused (the MCP danger-sign protocol): referred her for an
 * urgent check, or noted it and carried on with the questions.
 */
export type Referral = { code: RuleCode; signs: Sign[]; text: string; status: 'referred' | 'noted'; at: number; by: string };

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
  /** A BP recheck set after a raised reading (see BpRecheck). */
  bpRecheck?: BpRecheck;
  /** What the ASHA / health worker entered per visit (measurements, examination, tests), newest first. */
  clinical?: ClinicalEntry[];
  /** Danger signs reported in check-ins and what was done (see Referral), newest first. */
  referrals?: Referral[];
  /**
   * Her first-visit answers (registration: history, conditions), kept for
   * good: later visits only ask what changed.
   */
  profile?: Record<string, AnswerRecord>;
  /** The visit of the check-in in progress. */
  draftVisit?: Visit;
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
  /**
   * The ASHA's own call ("How is she?"): Well, Recheck in 6 h (bpRecheck is
   * set with it) or Needs care. Cleared ('well' / 'recheck') when new
   * findings arrive (a check-in, a new BP), so an old call never hides them.
   */
  ashaFlag?: AshaFlag;
  /** Uploaded lab reports, newest first (the Lab report card shows their history). */
  labReports?: LabUpload[];
};

/** What the ASHA fills in to add a pregnant woman (everything else comes from Sakhi). */
export type AshaFlag = 'well' | 'recheck' | 'needs_care';

/** An uploaded lab report: what the ASHA named it, and the photo / file kept on the phone. */
export type LabUpload = { id: string; name: string; uri: string; kind: 'image' | 'pdf' | 'file'; at: number; by: string };

export type NewMother = { name: string; months: number; language: Lang; zoneId: string; phone?: string };
/** Her details the ASHA can correct later (the same fields as adding her). */
export type MotherDetails = NewMother;
export type Escalation = { note: string; at: number; by: string };

const weeksFor = (months: number) => `${Math.round(months * 4.35)}w 0d`;

/** A readable transcript rebuilt from saved answers (seed data, old visits). */
export function transcriptFrom(answers: Record<string, AnswerRecord>, name: string, visit: Visit): TranscriptLine[] {
  const lines: TranscriptLine[] = [{ from: 'sakhi', text: `Namaskara! I will ask a few questions about ${name}.` }];
  for (const q of questionsFor(visit)) {
    const a = answers[q.id];
    if (!a) continue;
    lines.push({ from: 'sakhi', text: q.en }, { from: 'asha', text: a.value, danger: a.danger });
  }
  return lines;
}

function seedReport(id: string, when: string, answers: Record<string, AnswerRecord>, name: string, asha: string, visit: Visit, clinical?: ClinicalEntry[]): CheckInReport {
  const hits = evaluate(answers, clinicalFacts(clinical));
  return {
    id: `${id}-seed`,
    when,
    asha,
    high: isHighRisk(hits),
    rules: hits.map((h) => h.code),
    answers,
    transcript: transcriptFrom(answers, name, visit),
    visit,
  };
}

/** A visit's answers, all typical, with these overrides. */
function fullAnswers(visit: Visit, overrides: Record<string, AnswerRecord>): Record<string, AnswerRecord> {
  const base: Record<string, AnswerRecord> = {};
  for (const q of questionsFor(visit)) base[q.id] = { value: q.sampleNormal, danger: false };
  return { ...base, ...overrides };
}

/** A seeded clinical entry: her latest vitals, measured by the ASHA `daysAgo`. */
function seedClinical(v: Vitals, visit: Visit, daysAgo: number, extra: Record<string, string> = {}): ClinicalEntry[] {
  const at = dayStart(-daysAgo) + 10 * 3_600_000;
  const last = (a: number[]) => a[a.length - 1];
  const entry = (value: string, source: 'measured' | 'examination' = 'measured') => ({ value, by: ME_ASHA, at, source });
  const fields: ClinicalEntry['fields'] = {
    bp: entry(`${last(v.sys)}/${last(v.dia)}`),
    weight: entry(String(last(v.weight))),
    pallor: entry('no', 'examination'),
    oedema: entry('no', 'examination'),
  };
  for (const [k, val] of Object.entries(extra)) fields[k] = entry(val, k === 'pallor' || k === 'oedema' ? 'examination' : 'measured');
  return [{ visit, at, by: ME_ASHA, fields, tests: { hb: { status: 'done', result: String(last(v.hb)), by: ME_ASHA, at } }, added: { sys: true, dia: true, weight: true, hb: true } }];
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
    answers: fullAnswers(3, { warning: { value: 'Headache, Blurred vision', danger: true, signs: ['headache', 'vision'] } }),
    advice: [
      'Come to the PHC today so the doctor can check your blood pressure again.',
      'Rest lying on your left side and avoid heavy work until you are seen.',
      'Call 108 at once if the headache gets worse, your vision gets worse, or you have a fit.',
    ],
    nextVisit: 'Today, 14:00',
    dueSoon: false,
    vitals: { sys: [118, 126, 138, 148], dia: [76, 82, 90, 96], hb: [11.2, 10.6, 10.1, 9.6], sugar: [96, 104, 108, 112], weight: [55.4, 57.0, 58.9, 61.2] },
    clinical: seedClinical({ sys: [148], dia: [96], hb: [9.6], sugar: [], weight: [61.2] }, 3, 0, { oedema: 'yes' }),
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
    answers: fullAnswers(3, { movement: { value: 'Fewer movements', danger: true, signs: ['reducedMovement'] } }),
    advice: null,
    nextVisit: 'Today, 16:30',
    dueSoon: true,
    vitals: { sys: [112, 114, 116, 118], dia: [72, 74, 74, 76], hb: [10.9, 10.8, 10.6, 10.6], sugar: [92, 95, 98, 98], weight: [54.1, 55.8, 56.9, 58.0] },
    clinical: seedClinical({ sys: [118], dia: [76], hb: [10.6], sugar: [], weight: [58.0] }, 3, 1),
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
    answers: fullAnswers(2, {}),
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
    answers: fullAnswers(1, { name: { value: 'Roopa K.', danger: false }, age: { value: '21 years', danger: false }, village: { value: 'Aliyabad', danger: false }, firstPregnancy: { value: 'Yes, first pregnancy', danger: false } }),
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
    answers: fullAnswers(2, {}),
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
    answers: fullAnswers(2, {}),
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
const seeded: MotherRecord[] = initial.map((m) => {
  const visit = m.answers.name ? 1 : m.answers.movement ? 3 : 2;
  return {
    ...m,
    draft: null,
    reports: [seedReport(m.id, m.lastCheck, m.answers, m.name, m.asha, visit, m.clinical)],
    ...(visit === 1 ? { profile: m.answers } : {}),
    ...(m.advice ? { adviceAt: dayStart(-1) + 17 * 3_600_000, adviceBy: ME_DOCTOR } : {}),
  };
});

// --- Saved on the phone --------------------------------------------------
// Seeded women keep only the fields the ASHA / doctor changed; women she
// added are kept whole (without a check-in draft).
const SAVE_KEY = 'tara.mothers.v1';
const SAVED_FIELDS = ['name', 'months', 'weeksLabel', 'phone', 'language', 'zoneId', 'ashaFlag', 'escalation', 'consent', 'bpRecheck', 'labReports', 'clinical', 'referrals', 'advice', 'adviceAt', 'adviceBy', 'referred'] as const;
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
/** Today's report (filed before the measurements / lab results) is re-checked with them. */
function recheckToday(reports: CheckInReport[], clinical: ClinicalEntry[], now: number) {
  const facts = clinicalFacts(clinical);
  return reports.map((r, k) => {
    if (k !== 0 || (r.at ?? 0) < dayStart(0, now)) return r;
    const hits = evaluate(r.answers, facts);
    return { ...r, high: isHighRisk(hits), rules: hits.map((h) => h.code) };
  });
}

// An answer the outbox may fill in later: nothing real was recorded for it.
const LATE_FILLABLE = /^(no answer|not clear|skipped)/i;
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
  /** A new check-in (of `visit`) starts from a blank draft, so rules only see today's answers. */
  startCheckIn(id: string, visit: Visit = 1) {
    update(id, (m) => ({ ...m, draft: {}, draftVisit: visit }));
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
   * An answer recorded offline was transcribed later (voice/outbox-worker.ts).
   * It fills that question ONLY where nothing real is there yet (no answer, or
   * a "not clear / no answer" placeholder), never over an answer the ASHA
   * corrected. Goes to the draft while the check-in is still open, else to its
   * filed report (rules re-run, the same deterministic rules), and to her
   * current answers when it is her latest report. The "will be written when
   * online" line in the conversation becomes her words. Returns whether
   * anything changed.
   */
  updateTurnTranscript(
    id: string,
    reportId: string,
    turn: { qid: string; question: string; text: string; record: AnswerRecord; placeholders: string[] },
  ): boolean {
    const m = state.find((x) => x.id === id);
    if (!m) return false;
    const open = (a?: AnswerRecord) => !a || (!a.corrected && LATE_FILLABLE.test(a.value));
    const fixLines = (lines: TranscriptLine[]) => {
      const at = lines.findIndex((l) => l.from === 'sakhi' && l.text === turn.question);
      const from = at < 0 ? 0 : at + 1;
      const j = lines.findIndex((l, k) => k >= from && l.from === 'asha' && turn.placeholders.includes(l.text));
      if (j < 0) return lines;
      const sub = turn.record.value === turn.text ? undefined : turn.record.value;
      return lines.map((l, k) => (k === j ? { ...l, text: turn.text, sub, danger: turn.record.danger } : l));
    };
    const report = m.reports.find((r) => r.id === reportId);
    if (!report) {
      // Still in the check-in (or its review): the draft and the pending conversation.
      if (!m.draft) return false;
      const pending = transcripts.get(id);
      if (pending?.reportId === reportId) transcripts.set(id, { reportId, lines: fixLines(pending.lines) });
      if (!open(m.draft[turn.qid])) return false;
      update(id, (cur) => ({ ...cur, draft: { ...(cur.draft ?? {}), [turn.qid]: turn.record } }));
      return true;
    }
    if (!open(report.answers[turn.qid])) return false;
    const latest = m.reports[0]?.id === reportId;
    update(id, (cur) => {
      const reports = cur.reports.map((r) => {
        if (r.id !== reportId) return r;
        const answers = { ...r.answers, [turn.qid]: turn.record };
        const hits = evaluate(answers, clinicalFacts(cur.clinical));
        return { ...r, answers, high: isHighRisk(hits), rules: hits.map((h) => h.code), transcript: fixLines(r.transcript) };
      });
      const filed = reports.find((r) => r.id === reportId) as CheckInReport;
      if (!latest) return { ...cur, reports };
      const hits = evaluate(filed.answers, clinicalFacts(cur.clinical));
      const note = hits.length ? hits.map((h) => RULES[h.code].title).join(', ') : 'All answers normal';
      return {
        ...cur,
        reports,
        answers: filed.answers,
        ...(filed.visit === 1 ? { profile: filed.answers } : {}),
        history: cur.history.length ? [{ ...cur.history[0], high: filed.high, note }, ...cur.history.slice(1)] : cur.history,
      };
    });
    persist();
    return true;
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
      const visit = cur.draftVisit ?? 1;
      const hits = evaluate(answers, clinicalFacts(cur.clinical));
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
        visit,
        at: Date.now(),
      };
      return {
        ...cur,
        answers,
        // The first visit is her registration: kept for good.
        ...(visit === 1 ? { profile: answers } : {}),
        draft: null,
        draftVisit: undefined,
        lastCheck: 'Just now',
        referred: false, // new findings need a fresh review
        history: [{ when: 'Just now', high, note }, ...cur.history],
        reports: [report, ...cur.reports],
        isNew: false,
        bpRecheck: undefined, // the new reading supersedes a pending recheck
        // New findings need a fresh call: "Well" / "Recheck" were about the old ones.
        ashaFlag: cur.ashaFlag === 'needs_care' ? 'needs_care' : undefined,
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
  /**
   * "How is she?": ONE choice, its side effects kept in step.
   *   well        she is fine: a pending recheck is cancelled (the ASHA's call)
   *   recheck     a BP recheck BP_RECHECK_HOURS from now (kept if one is already set)
   *   needs_care  escalated (telling the doctor is a separate, confirmed step):
   *               the recheck is superseded by the doctor's review
   */
  setFlag(id: string, flag: AshaFlag, by: string = ME_ASHA) {
    const at = Date.now();
    update(id, (m) => {
      if (flag !== 'recheck') return { ...m, ashaFlag: flag, bpRecheck: undefined };
      const last = (a: number[]) => a[a.length - 1];
      const bp = m.vitals.sys.length && m.vitals.dia.length ? `${last(m.vitals.sys)}/${last(m.vitals.dia)}` : '';
      return { ...m, ashaFlag: 'recheck', bpRecheck: m.bpRecheck ?? { dueAt: at + BP_RECHECK_HOURS * 3_600_000, at, by, bp } };
    });
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
  /** Raised BP: the ASHA sets a recheck BP_RECHECK_HOURS from now (rest, then measure again). */
  scheduleBpRecheck(id: string, input: { by: string; bp: string }) {
    const at = Date.now();
    // The same state as choosing "Recheck in 6 h" in How is she?
    update(id, (m) => ({ ...m, ashaFlag: m.ashaFlag === 'needs_care' ? 'needs_care' : 'recheck', bpRecheck: { dueAt: at + BP_RECHECK_HOURS * 3_600_000, at, by: input.by, bp: input.bp } }));
    persist();
  },
  /**
   * The ASHA saves this visit's measurements and examination. One entry per
   * day: saving again the same day updates it (and its point on the vitals
   * trend) instead of adding a visit. Lab results entered the same day are
   * kept (they are the Lab report's, see saveLab). A new BP is the recheck:
   * it clears a pending one, and a "Well" / "Recheck" call made on the old
   * reading. Her risk and today's report are re-checked with the new values.
   */
  saveClinical(id: string, input: { visit: Visit; fields: ClinicalEntry['fields']; by: string }) {
    const now = Date.now();
    update(id, (m) => {
      const prev = m.clinical?.[0];
      const sameDay = !!prev && prev.at >= dayStart(0, now);
      const v = m.vitals;
      const num = (x?: string) => (x !== undefined && x !== '' && Number.isFinite(Number(x)) ? Number(x) : undefined);
      const bp = input.fields.bp?.value.match(/^(\d{2,3})\/(\d{2,3})$/);
      const point = { sys: bp ? Number(bp[1]) : undefined, dia: bp ? Number(bp[2]) : undefined, weight: num(input.fields.weight?.value) };
      const added: NonNullable<ClinicalEntry['added']> = sameDay && prev?.added?.hb ? { hb: true } : {};
      const vitals = { ...v };
      for (const k of ['sys', 'dia', 'weight'] as const) {
        const replaceLast = sameDay && prev?.added?.[k];
        const series = replaceLast ? v[k].slice(0, -1) : v[k];
        vitals[k] = point[k] === undefined ? series : [...series, point[k] as number];
        if (point[k] !== undefined) added[k] = true;
      }
      const entry: ClinicalEntry = { visit: input.visit, at: sameDay ? prev.at : now, by: input.by, fields: input.fields, tests: sameDay ? prev.tests : {}, added };
      const clinical = [entry, ...(sameDay ? (m.clinical ?? []).slice(1) : (m.clinical ?? []))];
      const newBp = point.sys !== undefined;
      return {
        ...m,
        clinical,
        vitals,
        reports: recheckToday(m.reports, clinical, now),
        bpRecheck: newBp ? undefined : m.bpRecheck,
        ashaFlag: newBp && m.ashaFlag !== 'needs_care' ? undefined : m.ashaFlag,
      };
    });
    persist();
  },
  /**
   * The Lab report: test results (Hb, urine, blood group, HIV / syphilis /
   * HBsAg, glucose, scan), each Done (with its result), Pending, Not
   * available or Not needed. Saved with her latest visit's entry (a new one
   * when she has none), where the rules and the PDF read them; Hb also adds
   * (or corrects) its point on the Hb trend.
   */
  saveLab(id: string, input: { tests: ClinicalEntry['tests']; by: string; visit: Visit }) {
    const now = Date.now();
    update(id, (m) => {
      const prev = m.clinical?.[0];
      const base: ClinicalEntry = prev ?? { visit: input.visit, at: now, by: input.by, fields: {}, tests: {}, added: {} };
      const tests = { ...base.tests, ...input.tests };
      const num = (x?: string) => (x !== undefined && x !== '' && Number.isFinite(Number(x)) ? Number(x) : undefined);
      const hb = tests.hb?.status === 'done' ? num(tests.hb.result) : undefined;
      const series = base.added?.hb ? m.vitals.hb.slice(0, -1) : m.vitals.hb;
      const vitals = { ...m.vitals, hb: hb === undefined ? series : [...series, hb] };
      const added: NonNullable<ClinicalEntry['added']> = { ...(base.added ?? {}) };
      if (hb === undefined) delete added.hb;
      else added.hb = true;
      const entry: ClinicalEntry = { ...base, tests, added };
      const clinical = [entry, ...(prev ? (m.clinical ?? []).slice(1) : (m.clinical ?? []))];
      return { ...m, clinical, vitals, reports: recheckToday(m.reports, clinical, now) };
    });
    persist();
  },
  /** The ASHA uploaded a lab report (named first): it goes on top of her report history. */
  addLabReport(id: string, upload: Omit<LabUpload, 'at'> & { at?: number }) {
    update(id, (m) => ({ ...m, labReports: [{ ...upload, at: upload.at ?? Date.now() }, ...(m.labReports ?? [])] }));
    persist();
  },
  /** Removed by the ASHA (a wrong photo): gone from her history. */
  removeLabReport(id: string, uploadId: string) {
    update(id, (m) => ({ ...m, labReports: (m.labReports ?? []).filter((u) => u.id !== uploadId) }));
    persist();
  },
  /** A danger sign during a check-in: what the ASHA did (referred / noted). */
  recordReferral(id: string, r: Omit<Referral, 'at'>) {
    update(id, (m) => ({ ...m, referrals: [{ ...r, at: Date.now() }, ...(m.referrals ?? [])] }));
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

// Rule hits per answers object (and clinical entry). Neither is ever mutated
// (every change makes a new object), so identity is a safe key; a WeakMap
// lets old answers be collected. Lists sort and filter by risk many times per render.
const hitsCache = new WeakMap<Record<string, AnswerRecord>, { clinical?: ClinicalEntry[]; hits: RuleHit[] }>();

/** Derived risk for a mother: deterministic rule hits (answers + measurements) + high-risk flag. */
export function riskOf(m: MotherRecord): { hits: RuleHit[]; high: boolean } {
  const cached = hitsCache.get(m.answers);
  let hits = cached && cached.clinical === m.clinical ? cached.hits : undefined;
  if (!hits) {
    hits = evaluate(m.answers, clinicalFacts(m.clinical));
    hitsCache.set(m.answers, { clinical: m.clinical, hits });
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
  // Her own call wins, except "Recheck": that is pending, the findings decide.
  if (m.ashaFlag === 'needs_care') return 'alert';
  if (m.ashaFlag === 'well') return 'ok';
  if (m.isNew) return 'new';
  return riskOf(m).high ? 'alert' : 'ok';
}
