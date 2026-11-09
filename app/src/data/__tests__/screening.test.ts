import { beforeEach, describe, expect, it, jest } from '@jest/globals';

import { kv } from '@/lib/kv';

import { ME_ASHA } from '../care-network';
import { clinicalFacts, groupsFor, parseBp, testsFor, validField } from '../clinical';
import { evaluate, isHighRisk, QUESTION_BANK, questionCount, questionsFor, triggersOf, VISIT_QUESTIONS, visitFor, type AnswerRecord, type Sign } from '../mockScreening';
import { careStatus, riskOf, sortByRisk, store } from '../store';

// Regression cases for the deterministic screening rules (the doctor's
// queue, the ASHA's "needs care" and the PDF all depend on them), for the
// visit questionnaires (MCP card / ICMR schedule) and for the clinical entry
// the ASHA makes (measurements are entered, never asked by voice).

jest.mock('expo-router', () => ({ router: { canGoBack: jest.fn(), back: jest.fn(), replace: jest.fn(), push: jest.fn() } }));

const a = (value: string, danger = false, signs?: Sign[]): AnswerRecord => ({ value, danger, ...(signs ? { signs } : {}) });
const codes = (answers: Record<string, AnswerRecord>, bp?: [number, number]) => evaluate(answers, bp ? { bp } : undefined).map((h) => h.code);
const get = (id: string) => store.get().find((m) => m.id === id)!;

beforeEach(() => store.resetDemo());

describe('the questionnaires', () => {
  it('first visit asks 21 questions; second 9; third 11 (each ends with the closing line)', () => {
    expect(questionCount(1)).toBe(21);
    expect(questionCount(2)).toBe(9);
    expect(questionCount(3)).toBe(11);
    for (const v of [1, 2, 3] as const) expect(VISIT_QUESTIONS[v][VISIT_QUESTIONS[v].length - 1]).toBe('close');
  });

  it('no voice question asks for a measurement (BP, weight, Hb, fundal height, FHR)', () => {
    const asked = QUESTION_BANK.map((q) => q.en.toLowerCase()).join(' | ');
    for (const word of ['blood pressure reading', 'weight', 'haemoglobin', 'heart rate', 'kg']) expect(asked).not.toContain(word);
  });

  it('every question of every visit exists, with Kannada and Hindi wording', () => {
    for (const v of [1, 2, 3] as const)
      for (const q of questionsFor(v)) {
        expect(q).toBeDefined();
        expect(q.questionKn.length).toBeGreaterThan(5);
        expect(q.questionHi.length).toBeGreaterThan(5);
      }
  });

  it('the visit: first until she has a check-in; then second up to 27 weeks, third from 28', () => {
    expect(visitFor({ reports: [], months: 7, weeksLabel: '30w 0d' })).toBe(1);
    expect(visitFor({ reports: [{}], months: 5, weeksLabel: '21w 4d' })).toBe(2);
    expect(visitFor({ reports: [{}], months: 6, weeksLabel: '27w 6d' })).toBe(2);
    expect(visitFor({ reports: [{}], months: 7, weeksLabel: '28w 0d' })).toBe(3);
  });
});

describe('screening rules', () => {
  it('normal answers and a normal BP fire nothing', () => {
    expect(codes({ warning: a('No'), movement: a('Yes, moving normally') }, [118, 76])).toEqual([]);
  });

  it('BP comes from the measurement: 140/90 is PE-01; 160/110 is PE-02 (not both)', () => {
    expect(codes({}, [140, 88])).toEqual(['PE-01']);
    expect(codes({}, [138, 90])).toEqual(['PE-01']);
    expect(codes({}, [139, 89])).toEqual([]);
    expect(codes({}, [162, 100])).toEqual(['PE-02']);
  });

  it('measured high BP + reported headache / vision is severe pre-eclampsia (PE-04)', () => {
    const answers = { warning: a('Headache, Blurred vision', true, ['headache', 'vision']) };
    expect(codes(answers, [148, 96])).toEqual(expect.arrayContaining(['PE-01', 'PE-03', 'PE-04']));
    expect(isHighRisk(evaluate(answers, { bp: [148, 96] }))).toBe(true);
  });

  it('rules follow the SIGN, whichever question it was said in', () => {
    expect(codes({ concerns: a('Reported: fits', true, ['fits']) })).toEqual(['EC-01']);
    expect(codes({ bleedLeak: a('Bleeding', true, ['bleeding']) })).toEqual(['HM-01']);
    expect(codes({ movement: a('Fewer movements', true, ['reducedMovement']) })).toEqual(['FM-01']);
    expect(codes({ newProblems: a('Fever', true, ['fever']) })).toEqual(['IN-01']);
    expect(triggersOf('EC-01', { concerns: a('Reported: fits', true, ['fits']) })).toEqual(['concerns']);
  });

  it('a flagged answer with no sign named raises its question\'s rule', () => {
    expect(codes({ warning: a('Yes (sign not named: check)', true) })).toEqual(['PE-03']);
  });

  it('tiredness, palpitations, discharge and examined pallor / oedema are WATCH, not high risk', () => {
    const hits = evaluate({ breathing: a('Unusual tiredness (check)', false, ['tired']) }, { oedema: true });
    expect(hits.map((h) => h.code)).toEqual(['AN-01']);
    expect(isHighRisk(hits)).toBe(false);
    expect(codes({ bleedDischarge: a('Unusual discharge (check)', false, ['discharge']) })).toEqual([]);
  });
});

describe('clinical entry (what the ASHA enters)', () => {
  it('BP must be a real reading', () => {
    expect(parseBp('148/96')).toEqual([148, 96]);
    expect(parseBp('96/148')).toBeNull();
    expect(parseBp('148')).toBeNull();
    expect(validField({ kind: 'number', unit: 'kg', min: 25, max: 150, decimals: true }, '61.5')).toBe(true);
    expect(validField({ kind: 'number', unit: 'kg', min: 25, max: 150 }, '400')).toBe(false);
  });

  it('the fields follow the MCP card per visit', () => {
    const ids = (v: 1 | 2 | 3) => groupsFor(v).flatMap((g) => g.fields.map((f) => f.id));
    expect(ids(1)).toEqual(expect.arrayContaining(['rchId', 'mcpNo', 'bp', 'weight', 'height', 'pallor', 'oedema', 'jaundice']));
    expect(ids(2)).toEqual(expect.arrayContaining(['bp', 'weight', 'fundalHeight', 'fhr']));
    expect(ids(3)).toEqual(expect.arrayContaining(['girth', 'lie', 'facility', 'transport']));
    expect(testsFor(1).map((t) => t.id)).toEqual(expect.arrayContaining(['hb', 'bloodGroup', 'rh', 'urineAlbumin', 'hiv', 'syphilis', 'hbsag']));
  });

  it('saving traces who entered what, from which source, and re-checks her risk', () => {
    store.startCheckIn('kavitha', 2);
    store.recordAnswer('kavitha', 'warning', a('Headache', true, ['headache']));
    store.completeCheckIn('kavitha', 'r-today');
    store.saveClinical('kavitha', {
      visit: 2,
      by: ME_ASHA,
      fields: { bp: { value: '150/100', by: ME_ASHA, at: Date.now(), source: 'measured' } },
    });
    store.saveLab('kavitha', { visit: 2, by: ME_ASHA, tests: { hb: { status: 'pending', by: ME_ASHA, at: Date.now() } } });
    const m = get('kavitha');
    expect(m.clinical?.[0].fields.bp).toMatchObject({ value: '150/100', by: ME_ASHA, source: 'measured' });
    expect(m.clinical?.[0].tests.hb.status).toBe('pending'); // never read as normal
    expect(clinicalFacts(m.clinical)?.hb).toBeUndefined();
    expect(riskOf(m).hits.map((h) => h.code)).toEqual(expect.arrayContaining(['PE-01', 'PE-03', 'PE-04']));
    // today's report (filed before the BP) now carries it
    expect(m.reports[0].rules).toContain('PE-04');
    expect(m.reports[0].high).toBe(true);
  });

  it('a second save the same day corrects the entry and its point on the trend (never two visits)', () => {
    const before = get('kavitha').vitals.sys.length;
    const save = (bp: string) => store.saveClinical('kavitha', { visit: 2, by: ME_ASHA, fields: { bp: { value: bp, by: ME_ASHA, at: Date.now(), source: 'measured' } } });
    save('120/80');
    save('124/82');
    const m = get('kavitha');
    expect(m.vitals.sys).toHaveLength(before + 1);
    expect(m.vitals.sys[m.vitals.sys.length - 1]).toBe(124);
    expect(m.clinical?.filter((e) => e.at >= m.clinical![0].at)).toHaveLength(1);
  });

  it('is saved on the phone', () => {
    store.saveClinical('kavitha', { visit: 2, by: ME_ASHA, fields: { weight: { value: '54', by: ME_ASHA, at: Date.now(), source: 'measured' } } });
    const saved = kv.get<{ patches: Record<string, { clinical?: unknown[] }> }>('tara.mothers.v1');
    expect(saved?.patches.kavitha?.clinical).toHaveLength(1);
  });
});

describe('store: lab report', () => {
  const hb = (result: string) => ({ hb: { status: 'done' as const, result, by: ME_ASHA, at: Date.now() } });
  const bp = (value: string) => ({ bp: { value, by: ME_ASHA, at: Date.now(), source: 'measured' as const } });

  it('medicines and vaccines are not lab tests (not in the lab report)', () => {
    const ids = [1, 2, 3].flatMap((v) => testsFor(v as 1 | 2 | 3).map((t) => t.id));
    expect(ids).not.toEqual(expect.arrayContaining(['ifaGiven']));
    expect(ids).not.toContain('calciumGiven');
    expect(ids).not.toContain('td');
  });

  it('a lab Hb feeds the rules and the Hb trend; correcting it replaces its point', () => {
    store.saveClinical('kavitha', { visit: 2, by: ME_ASHA, fields: bp('118/76') });
    const before = get('kavitha').vitals.hb.length;
    store.saveLab('kavitha', { visit: 2, by: ME_ASHA, tests: hb('8.4') });
    expect(clinicalFacts(get('kavitha').clinical)?.hb).toBe(8.4);
    expect(get('kavitha').vitals.hb).toHaveLength(before + 1);
    store.saveLab('kavitha', { visit: 2, by: ME_ASHA, tests: hb('8.9') });
    const m = get('kavitha');
    expect(m.vitals.hb).toHaveLength(before + 1);
    expect(m.vitals.hb[m.vitals.hb.length - 1]).toBe(8.9);
  });

  it('saving measurements again the same day never wipes the lab results', () => {
    store.saveClinical('kavitha', { visit: 2, by: ME_ASHA, fields: bp('118/76') });
    store.saveLab('kavitha', { visit: 2, by: ME_ASHA, tests: hb('9.2') });
    store.saveClinical('kavitha', { visit: 2, by: ME_ASHA, fields: bp('120/78') });
    const m = get('kavitha');
    expect(m.clinical?.[0].tests.hb).toMatchObject({ status: 'done', result: '9.2' });
    expect(m.clinical?.[0].added?.hb).toBe(true);
  });

  it('a lab report for a mother with no visit entry yet starts one', () => {
    const id = store.addMother({ name: 'Test Mother', months: 4, language: 'kn', zoneId: 'kamthana' });
    store.saveLab(id, { visit: 1, by: ME_ASHA, tests: hb('11.0') });
    expect(get(id).clinical?.[0]).toMatchObject({ visit: 1, fields: {} });
    expect(get(id).vitals.hb).toEqual([11]);
  });
});

describe('store: uploaded lab reports (named, newest first, kept)', () => {
  const up = (id: string, name: string, at: number) => ({ id, name, uri: `file://${id}.jpg`, kind: 'image' as const, by: ME_ASHA, at });

  it('each upload keeps its name; the history is newest first and saved on the phone', () => {
    store.addLabReport('kavitha', up('a', 'Hb test', 1000));
    store.addLabReport('kavitha', up('b', 'Scan', 2000));
    expect(get('kavitha').labReports?.map((u) => u.name)).toEqual(['Scan', 'Hb test']);
    const saved = kv.get<{ patches: Record<string, { labReports?: unknown[] }> }>('tara.mothers.v1');
    expect(saved?.patches.kavitha?.labReports).toHaveLength(2);
  });

  it('a wrong upload can be deleted; the others stay', () => {
    store.addLabReport('kavitha', up('a', 'Hb test', 1000));
    store.addLabReport('kavitha', up('b', 'Scan', 2000));
    store.removeLabReport('kavitha', 'b');
    expect(get('kavitha').labReports?.map((u) => u.id)).toEqual(['a']);
  });
});

describe('store: "How is she?" (one choice, its side effects in step)', () => {
  const bp = (value: string) => ({ bp: { value, by: ME_ASHA, at: Date.now(), source: 'measured' as const } });

  it('Recheck sets the 6-hour recheck; Well and Needs care cancel it', () => {
    store.setFlag('lakshmi', 'recheck');
    let m = get('lakshmi');
    expect(m.ashaFlag).toBe('recheck');
    expect(m.bpRecheck?.bp).toBe('148/96');
    expect(m.bpRecheck!.dueAt - m.bpRecheck!.at).toBe(6 * 3_600_000);
    store.setFlag('lakshmi', 'well');
    m = get('lakshmi');
    expect(m.ashaFlag).toBe('well');
    expect(m.bpRecheck).toBeUndefined(); // no recheck nagging after "Well"
    store.setFlag('lakshmi', 'recheck');
    store.setFlag('lakshmi', 'needs_care');
    expect(get('lakshmi').bpRecheck).toBeUndefined();
  });

  it('choosing Recheck again keeps the time already set', () => {
    store.setFlag('lakshmi', 'recheck');
    const first = get('lakshmi').bpRecheck!.dueAt;
    store.setFlag('lakshmi', 'recheck');
    expect(get('lakshmi').bpRecheck!.dueAt).toBe(first);
  });

  it('the recheck button on her card is the same state as choosing Recheck', () => {
    store.scheduleBpRecheck('lakshmi', { by: ME_ASHA, bp: '148/96' });
    expect(get('lakshmi').ashaFlag).toBe('recheck');
  });

  it('a new BP is the recheck: it clears the recheck and an old Well / Recheck call', () => {
    store.setFlag('lakshmi', 'recheck');
    store.saveClinical('lakshmi', { visit: 3, by: ME_ASHA, fields: bp('150/100') });
    expect(get('lakshmi').ashaFlag).toBeUndefined();
    expect(get('lakshmi').bpRecheck).toBeUndefined();
    store.setFlag('lakshmi', 'well');
    store.saveClinical('lakshmi', { visit: 3, by: ME_ASHA, fields: bp('152/100') });
    expect(get('lakshmi').ashaFlag).toBeUndefined();
    expect(careStatus(get('lakshmi'))).toBe('alert'); // the new high BP is not hidden behind "Well"
  });

  it('a new check-in clears an old Well call (never hides new danger); Needs care stays', () => {
    store.setFlag('kavitha', 'well');
    store.startCheckIn('kavitha', 2);
    store.recordAnswer('kavitha', 'warning', a('Headache', true, ['headache']));
    store.completeCheckIn('kavitha', 'r-new');
    expect(get('kavitha').ashaFlag).toBeUndefined();
    store.setFlag('meena', 'needs_care');
    store.startCheckIn('meena', 3);
    store.completeCheckIn('meena', 'r-new2');
    expect(get('meena').ashaFlag).toBe('needs_care');
  });

  it('"Recheck" does not decide her status: her findings do', () => {
    store.setFlag('kavitha', 'recheck');
    expect(careStatus(get('kavitha'))).toBe(riskOf(get('kavitha')).high ? 'alert' : 'ok');
  });
});

describe('store: risk, status and check-ins', () => {
  it('the seeded queue puts high risk first', () => {
    const sorted = sortByRisk(store.get());
    const firstNormal = sorted.findIndex((m) => !riskOf(m).high);
    expect(sorted.slice(0, firstNormal).every((m) => riskOf(m).high)).toBe(true);
    expect(sorted.slice(firstNormal).every((m) => !riskOf(m).high)).toBe(true);
    expect(riskOf(get('lakshmi')).hits.map((h) => h.code)).toEqual(expect.arrayContaining(['PE-04', 'AN-01']));
    expect(riskOf(get('meena')).high).toBe(true);
  });

  it('riskOf is cached per answers object and refreshed when answers change', () => {
    const m = get('kavitha');
    expect(riskOf(m).hits).toBe(riskOf(m).hits); // same object: cached
    store.startCheckIn('kavitha', 2);
    store.recordAnswer('kavitha', 'newProblems', a('Reported: fits', true, ['fits']));
    store.completeCheckIn('kavitha', 'r1');
    expect(riskOf(get('kavitha')).high).toBe(true); // new answers, new result
  });

  it('the first visit is kept as her profile; later visits do not replace it', () => {
    store.startCheckIn('kavitha', 1);
    store.recordAnswer('kavitha', 'conditions', a('Thyroid'));
    store.completeCheckIn('kavitha', 'v1');
    store.startCheckIn('kavitha', 2);
    store.recordAnswer('kavitha', 'newProblems', a('No'));
    store.completeCheckIn('kavitha', 'v2');
    const m = get('kavitha');
    expect(m.profile?.conditions?.value).toBe('Thyroid');
    expect(m.reports[0]).toMatchObject({ id: 'v2', visit: 2 });
  });

  it('an urgent sign during a check-in is recorded with what the ASHA did', () => {
    store.recordReferral('meena', { code: 'FM-01', signs: ['reducedMovement'], text: 'Fewer movements', status: 'referred', by: ME_ASHA });
    expect(get('meena').referrals?.[0]).toMatchObject({ code: 'FM-01', status: 'referred', by: ME_ASHA });
  });

  it("the ASHA's own flag overrides the computed status", () => {
    store.setFlag('kavitha', 'needs_care');
    const m = get('kavitha');
    expect(careStatus(m)).toBe('alert');
    expect(riskOf(m).high).toBe(true);
  });

  it('a check-in is filed once, even if completed twice (re-render, reload)', () => {
    store.startCheckIn('roopa', 2);
    store.recordAnswer('roopa', 'newProblems', a('No'));
    store.completeCheckIn('roopa', 'rep-1');
    store.completeCheckIn('roopa', 'rep-1');
    const m = get('roopa');
    expect(m.reports.filter((r) => r.id === 'rep-1')).toHaveLength(1);
    expect(m.draft).toBeNull();
  });

  it('an abandoned check-in leaves the record exactly as it was', () => {
    const before = get('meena');
    store.startCheckIn('meena', 3);
    store.recordAnswer('meena', 'bleedLeak', a('Bleeding', true, ['bleeding']));
    store.discardCheckIn('meena');
    const after = get('meena');
    expect(after.answers).toBe(before.answers);
    expect(after.reports).toBe(before.reports);
    expect(after.draft).toBeNull();
  });

  it('a new report needs a fresh referral', () => {
    store.confirmReferral('lakshmi', ['Come today']);
    store.startCheckIn('lakshmi', 3);
    store.completeCheckIn('lakshmi', 'again');
    expect(get('lakshmi').referred).toBe(false);
  });
});
