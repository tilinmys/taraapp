import { useSyncExternalStore } from 'react';

import { riskOf, store, type MotherRecord } from '@/data/store';

// What the doctor has already done, so she never has to remember it herself:
//   reviewed   per mother: WHICH report she reviewed and when. A newer ASHA
//              report makes the case "new" again on its own.
//   recent     the last cases she opened (most recent first), for "Pick up
//              where you left off" on her home.
// The app never pushes her to the next case: these marks are memory, and
// where she goes next is her choice. In-memory, like the rest of the demo.

export type Review = { reportId: string; at: number };

type State = { reviewed: Record<string, Review>; recent: string[] };

const RECENT_MAX = 3;

/**
 * Demo start: older reports with nothing found were read yesterday, so the
 * day opens on what is really new (today's findings), as in a real clinic.
 */
function initial(): State {
  const yesterday = Date.now() - 26 * 3_600_000;
  const reviewed: Record<string, Review> = {};
  for (const m of store.get()) {
    const r = m.reports[0];
    if (r && !r.rules.length && !riskOf(m).high) reviewed[m.id] = { reportId: r.id, at: yesterday };
  }
  return { reviewed, recent: [] };
}

let state: State = initial();
const listeners = new Set<() => void>();
const set = (next: State) => {
  state = next;
  listeners.forEach((l) => l());
};

export const doctorReview = {
  subscribe(l: () => void) {
    listeners.add(l);
    return () => listeners.delete(l);
  },
  snapshot: () => state,
  /** She opened a case: it moves to the front of "recent". */
  opened(id: string) {
    if (state.recent[0] === id) return;
    set({ ...state, recent: [id, ...state.recent.filter((x) => x !== id)].slice(0, RECENT_MAX) });
  },
  /** She has read the latest report of this case. */
  markReviewed(m: MotherRecord) {
    const reportId = m.reports[0]?.id ?? 'none';
    set({ ...state, reviewed: { ...state.reviewed, [m.id]: { reportId, at: Date.now() } } });
  },
  undoReviewed(id: string) {
    const reviewed = { ...state.reviewed };
    delete reviewed[id];
    set({ ...state, reviewed });
  },
  reset() {
    set(initial());
  },
};

export function useDoctorReview() {
  return useSyncExternalStore(doctorReview.subscribe, doctorReview.snapshot, doctorReview.snapshot);
}

/** Her review of the LATEST report, if any (an older review does not count). */
export function reviewOf(m: MotherRecord, s: State = state): Review | null {
  const r = s.reviewed[m.id];
  return r && r.reportId === (m.reports[0]?.id ?? 'none') ? r : null;
}

/**
 * The case's place in her day, one of four, in order of urgency:
 *   urgent    high risk and not referred yet: act today
 *   new       a report she has not reviewed (not urgent)
 *   referred  high risk, referral sent: follow up
 *   done      reviewed, nothing pending
 */
export type Triage = 'urgent' | 'new' | 'referred' | 'done';

export function triageOf(m: MotherRecord, s: State = state): Triage {
  const { high } = riskOf(m);
  if (high && !m.referred) return 'urgent';
  if (m.reports.length && !reviewOf(m, s)) return 'new';
  if (high) return 'referred';
  return 'done';
}

const ORDER: Record<Triage, number> = { urgent: 0, new: 1, referred: 2, done: 3 };

/** Most urgent first, then by name. */
export function byTriage(list: MotherRecord[], s: State = state) {
  return [...list].sort((a, b) => ORDER[triageOf(a, s)] - ORDER[triageOf(b, s)] || a.name.localeCompare(b.name));
}
