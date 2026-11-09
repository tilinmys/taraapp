import { riskOf, type MotherRecord } from '@/data/store';

// Her latest blood pressure and what the app's rules say to do about it
// (mockScreening.ts RULES, the same thresholds as sakhi_triage.py):
//   severe  >= 160/110 (PE-02)                refer to the PHC now; never wait
//   signs   >= 140/90 with headache, blurred  (PE-04) refer now
//           vision or swelling (PE-03)
//   raised  >= 140/90 (PE-01)                 re-check, and refer to the PHC today
//   low     < 90/60                           rest, fluids, food; re-check after 6 h
//                                             (refer today if faint, dizzy or bleeding)
// null when there is no reading, or it is in the normal range.

export type BpLevel = 'severe' | 'signs' | 'raised' | 'low';
export type BpState = { sys: number; dia: number; level: BpLevel };

const last = (a: number[]) => (a.length ? a[a.length - 1] : undefined);

export function bpState(m: MotherRecord): BpState | null {
  const sys = last(m.vitals.sys);
  const dia = last(m.vitals.dia);
  if (sys === undefined || dia === undefined) return null;
  const codes = new Set(riskOf(m).hits.map((h) => h.code));
  if (sys >= 160 || dia >= 110 || codes.has('PE-02')) return { sys, dia, level: 'severe' };
  if (sys >= 140 || dia >= 90) return { sys, dia, level: codes.has('PE-03') || codes.has('PE-04') ? 'signs' : 'raised' };
  // Low (below 90/60): rest, fluids, food, and a recheck after 6 hours.
  if (sys < 90 || dia < 60) return { sys, dia, level: 'low' };
  return null;
}

/** The pending recheck, if any (a new check-in clears it in the store). */
export function pendingRecheck(m: MotherRecord, now = Date.now()) {
  const r = m.bpRecheck;
  return r ? { ...r, overdue: now >= r.dueAt } : null;
}

/** Any BP out of range gets the 6-hour recheck. */
export const canRecheck = (b: BpState | null) => b !== null;
/** Severe, or with danger signs: refer NOW; the recheck never replaces the referral. */
export const referNow = (b: BpState | null) => b?.level === 'severe' || b?.level === 'signs';
