import { useSyncExternalStore } from 'react';

import { dayStart, upcomingBooking, slotOf, type Booking } from '@/data/care-network';
import { careStatus, type MotherRecord } from '@/data/store';
import { kv } from '@/lib/kv';

// The ASHA's plan for today: which women she visits, in which order.
// One plan per ASHA per day. Until she changes anything, the plan is a
// suggestion worked out from her women (needs care first, then women not
// yet checked, then a doctor visit in the next 2 days, then no check-in for
// a week). The first change (move, add, remove, visited) saves it for the
// day. Same shape as the visit_plans table (deploy/supabase).

export type Reason = 'needs_care' | 'not_checked' | 'doctor_soon' | 'overdue';
export type DayPlan = { day: number; order: string[]; visited: Record<string, number> };

const DAY = 86_400_000;
// Saved on the phone (lib/kv): today's ticks survive closing the app. A
// plan from another day is simply ignored (current() starts a new one).
const SAVE_KEY = 'tara.dayPlans.v1';
let plans: Record<string, DayPlan> = kv.get<Record<string, DayPlan>>(SAVE_KEY) ?? {};
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((l) => l());

/** Days since her last check-in, from the record's label ("Today, 10:42", "4 days ago"). */
function daysSince(lastCheck: string) {
  if (/^(Today|Just now)/i.test(lastCheck)) return 0;
  if (/^Yesterday/i.test(lastCheck)) return 1;
  const n = /(\d+)\s*day/i.exec(lastCheck);
  return n ? Number(n[1]) : 0;
}

/** Why she should be visited today (strongest reason), or null. */
export function reasonFor(m: MotherRecord, booking: Booking | undefined, now = Date.now()): Reason | null {
  if (careStatus(m) === 'alert') return 'needs_care';
  if (careStatus(m) === 'new') return 'not_checked';
  const s = booking && slotOf(booking);
  if (s && s.start - now < 2 * DAY) return 'doctor_soon';
  if (daysSince(m.lastCheck) >= 7) return 'overdue';
  return null;
}

const RANK: Record<Reason, number> = { needs_care: 0, not_checked: 1, doctor_soon: 2, overdue: 3 };

/** The suggested order for today: only women with a reason, strongest first. */
export function suggest(women: MotherRecord[], now = Date.now()) {
  return women
    .map((m) => ({ m, r: reasonFor(m, upcomingBooking(m.id), now) }))
    .filter((x): x is { m: MotherRecord; r: Reason } => x.r !== null)
    .sort((a, b) => RANK[a.r] - RANK[b.r] || a.m.name.localeCompare(b.m.name))
    .map((x) => x.m.id);
}

/** Her plan for today from a snapshot of all plans (the suggestion until she changes it). */
function planOf(all: Record<string, DayPlan>, ashaId: string, women: MotherRecord[]): DayPlan {
  const today = dayStart(0);
  const p = all[ashaId];
  return p && p.day === today ? p : { day: today, order: suggest(women), visited: {} };
}

const current = (ashaId: string, women: MotherRecord[]) => planOf(plans, ashaId, women);

function save(ashaId: string, women: MotherRecord[], change: (p: DayPlan) => DayPlan) {
  plans = { ...plans, [ashaId]: change(current(ashaId, women)) };
  kv.set(SAVE_KEY, plans);
  emit();
}

export const dayPlanStore = {
  subscribe(l: () => void) {
    listeners.add(l);
    return () => listeners.delete(l);
  },
  snapshot: () => plans,
  /** Move one place up (-1) or down (+1). */
  move(ashaId: string, women: MotherRecord[], id: string, dir: -1 | 1) {
    save(ashaId, women, (p) => {
      const i = p.order.indexOf(id);
      const j = i + dir;
      if (i < 0 || j < 0 || j >= p.order.length) return p;
      const order = [...p.order];
      [order[i], order[j]] = [order[j], order[i]];
      return { ...p, order };
    });
  },
  add(ashaId: string, women: MotherRecord[], id: string) {
    save(ashaId, women, (p) => (p.order.includes(id) ? p : { ...p, order: [...p.order, id] }));
  },
  remove(ashaId: string, women: MotherRecord[], id: string) {
    save(ashaId, women, (p) => {
      const visited = { ...p.visited };
      delete visited[id];
      return { ...p, order: p.order.filter((x) => x !== id), visited };
    });
  },
  /**
   * Visited, set once (a finished Sakhi check-in): unlike toggleVisited this
   * never undoes a visit she already ticked. Puts her on today's list if she
   * was not on it.
   */
  markVisited(ashaId: string, women: MotherRecord[], id: string) {
    save(ashaId, women, (p) => ({
      ...p,
      order: p.order.includes(id) ? p.order : [...p.order, id],
      visited: p.visited[id] ? p.visited : { ...p.visited, [id]: Date.now() },
    }));
  },
  /** Visited (or undo). */
  toggleVisited(ashaId: string, women: MotherRecord[], id: string) {
    save(ashaId, women, (p) => {
      const visited = { ...p.visited };
      if (visited[id]) delete visited[id];
      else visited[id] = Date.now();
      return { ...p, visited };
    });
  },
  reset() {
    plans = {};
    kv.remove(SAVE_KEY);
    emit();
  },
};

/** Today's plan for this ASHA (the suggestion until she changes it). */
export function useDayPlan(ashaId: string, women: MotherRecord[]) {
  // Read the plan FROM the snapshot: the React Compiler memoizes by inputs,
  // so reading the module variable here kept showing the old plan after a
  // tick (the "Mark visited does nothing" bug).
  const all = useSyncExternalStore(dayPlanStore.subscribe, dayPlanStore.snapshot, dayPlanStore.snapshot);
  const p = planOf(all, ashaId, women);
  // Only her own women, and never someone who left her villages.
  const ids = new Set(women.map((m) => m.id));
  return { ...p, order: p.order.filter((id) => ids.has(id)) };
}
