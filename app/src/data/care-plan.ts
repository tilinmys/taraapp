import { useSyncExternalStore } from 'react';

import type { AppIconName } from '@/components/system/app-icon';
import { kv } from '@/lib/kv';

// Each mother's daily checklist, set by her ASHA. The ASHA edits it from her
// workspace; the mother ticks it off in her Pregnancy tab. In-memory until the
// records backend is connected (same shape the API will send: mother id ->
// ordered list of tasks).

export type PlanItem = { id: string; label: string; icon: AppIconName };

export const MAX_PLAN_ITEMS = 8;

/** One-tap tasks for the ASHA. "kicks" is ticked automatically at 10 kicks. */
export const PLAN_PRESETS: PlanItem[] = [
  { id: 'iron', label: 'Iron tablet after lunch', icon: 'medicine' },
  { id: 'calcium', label: 'Calcium tablet after dinner', icon: 'medicine' },
  { id: 'kicks', label: 'Count 10 baby kicks', icon: 'kicks' },
  { id: 'walk', label: '30 min gentle walk', icon: 'maternity' },
  { id: 'water', label: '8 glasses of water', icon: 'food' },
  { id: 'meal', label: 'One extra meal', icon: 'food' },
  { id: 'rest', label: 'Rest 1 hour on left side', icon: 'rest' },
];

const DEFAULT_PLAN = ['iron', 'kicks', 'walk', 'water'].map((id) => PLAN_PRESETS.find((p) => p.id === id)!);

/** A sensible icon for a task the ASHA typed herself. */
export function iconFor(label: string): AppIconName {
  const t = label.toLowerCase();
  if (/tablet|iron|calcium|medicine|pill|injection/.test(t)) return 'medicine';
  if (/kick|move/.test(t)) return 'kicks';
  if (/water|eat|meal|food|milk|fruit|dal|egg/.test(t)) return 'food';
  if (/rest|sleep|lie|nap/.test(t)) return 'rest';
  if (/walk|exercise|yoga|stretch/.test(t)) return 'maternity';
  if (/clinic|phc|doctor|visit|check/.test(t)) return 'clinic';
  return 'checklist';
}

// Saved on the phone (lib/kv): a checklist the ASHA set is still hers tomorrow.
const SAVE_KEY = 'tara.carePlans.v1';
let plans: Record<string, PlanItem[]> = kv.get<Record<string, PlanItem[]>>(SAVE_KEY) ?? {};
const listeners = new Set<() => void>();

export const carePlanStore = {
  subscribe(l: () => void) {
    listeners.add(l);
    return () => listeners.delete(l);
  },
  snapshot: () => plans,
  get: (motherId: string) => plans[motherId] ?? DEFAULT_PLAN,
  set(motherId: string, items: PlanItem[]) {
    plans = { ...plans, [motherId]: items.slice(0, MAX_PLAN_ITEMS) };
    kv.set(SAVE_KEY, plans);
    listeners.forEach((l) => l());
  },
  reset() {
    plans = {};
    kv.remove(SAVE_KEY);
    listeners.forEach((l) => l());
  },
};

export function useCarePlan(motherId: string | undefined): PlanItem[] {
  const all = useSyncExternalStore(carePlanStore.subscribe, carePlanStore.snapshot, carePlanStore.snapshot);
  return motherId ? (all[motherId] ?? DEFAULT_PLAN) : DEFAULT_PLAN;
}
