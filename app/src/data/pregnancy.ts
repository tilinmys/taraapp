import { useSyncExternalStore } from 'react';

import type { AppIconName } from '@/components/system/app-icon';

// Mother-side data: baby size and growth by week, the daily checklist, the
// medicine schedule, the kick counter, and the topics (maternity, fertility,
// reproductive health).
// Plain, offline content; nothing a mother reads or ticks here is shared.

/** "30w 2d" -> 30 */
export const weekOf = (label: string) => Number.parseInt(label, 10) || 0;

export type BabySize = { week: number; like: string; cm: number; kg: number };

// Familiar Indian produce, closest week at or below the mother's week.
export const BABY_SIZES: BabySize[] = [
  { week: 12, like: 'a lime', cm: 5.4, kg: 0.014 },
  { week: 16, like: 'a mango', cm: 11.6, kg: 0.1 },
  { week: 20, like: 'a banana', cm: 25.6, kg: 0.3 },
  { week: 24, like: 'a corn cob', cm: 30, kg: 0.6 },
  { week: 28, like: 'a brinjal', cm: 37.6, kg: 1.0 },
  { week: 30, like: 'a cabbage', cm: 39.9, kg: 1.3 },
  { week: 32, like: 'a coconut', cm: 42.4, kg: 1.7 },
  { week: 34, like: 'a pineapple', cm: 45, kg: 2.1 },
  { week: 36, like: 'a papaya', cm: 47.4, kg: 2.6 },
  { week: 38, like: 'a small jackfruit', cm: 49.8, kg: 3.1 },
  { week: 40, like: 'a watermelon', cm: 51.2, kg: 3.4 },
];

export function babySize(week: number): BabySize {
  return [...BABY_SIZES].reverse().find((s) => s.week <= week) ?? BABY_SIZES[0];
}

export const trimesterOf = (week: number) => (week < 14 ? 1 : week < 28 ? 2 : 3);

// Fetal growth reference: median estimated fetal weight in grams by week
// (Hadlock-style values, rounded) with approximate 10th / 90th bands. Used
// for the growth chart; a guide for the mother, not a diagnostic tool.
const EFW_P50: Record<number, number> = {
  12: 58, 14: 93, 16: 146, 18: 222, 20: 331, 22: 478, 24: 670, 26: 913,
  28: 1210, 30: 1559, 32: 1953, 34: 2377, 36: 2813, 38: 3233, 40: 3619,
};
export const GROWTH_WEEKS = Object.keys(EFW_P50).map(Number);
export const MIN_WEEK = 12;
export const MAX_WEEK = 40;

/** Median weight (g) for any week, interpolated between reference weeks. */
export function weightAt(week: number) {
  const w = Math.min(MAX_WEEK, Math.max(MIN_WEEK, week));
  const lo = Math.floor(w / 2) * 2;
  const hi = Math.min(MAX_WEEK, lo + 2);
  const a = EFW_P50[lo] ?? EFW_P50[MIN_WEEK];
  const b = EFW_P50[hi] ?? a;
  return Math.round(a + ((b - a) * (w - lo)) / 2);
}
export const bandAt = (week: number) => ({ p10: weightAt(week) * 0.84, p50: weightAt(week), p90: weightAt(week) * 1.17 });

/** Her baby's estimated weight from scans (grams). Mock until scan data is connected. */
export const BABY_SCANS = [
  { week: 20, g: 320 },
  { week: 24, g: 640 },
  { week: 28, g: 1150 },
  { week: 30, g: 1300 },
];

/** "~600 g" / "~1.3 kg" */
export const formatWeight = (g: number) => (g < 1000 ? `~${Math.round(g / 10) * 10} g` : `~${(g / 1000).toFixed(1)} kg`);

// Medicine schedule (the daily checklist is set by the ASHA: data/care-plan.ts) -----------------------------------

export type Medication = { id: string; name: string; dose: string; time: string; daysLeft: number };
export const MEDICATIONS: Medication[] = [
  { id: 'ifa', name: 'Iron + Folic acid', dose: '1 tablet', time: 'After lunch · 2:00 PM', daysLeft: 12 },
  { id: 'calcium', name: 'Calcium + Vitamin D3', dose: '500 mg', time: 'After dinner · 8:30 PM', daysLeft: 3 },
];
/** Refill alert at or below this many days of stock. */
export const REFILL_DAYS = 5;

// Tiny reactive tracker: kicks today, today's checklist and doses taken.
type Tracker = {
  kicks: number;
  kickStart: number | null;
  lastKick: number | null;
  done: Record<string, boolean>;
  taken: Record<string, boolean>;
  streak: number;
};
let tracker: Tracker = { kicks: 0, kickStart: null, lastKick: null, done: { iron: true }, taken: {}, streak: 4 };
const listeners = new Set<() => void>();
const set = (next: Partial<Tracker>) => {
  tracker = { ...tracker, ...next };
  listeners.forEach((l) => l());
};

export const trackerStore = {
  subscribe(l: () => void) {
    listeners.add(l);
    return () => listeners.delete(l);
  },
  get: () => tracker,
  kick() {
    const now = Date.now();
    const kicks = tracker.kicks + 1;
    // Reaching 10 ticks "Log 10 kick counts" on the checklist by itself.
    set({ kicks, kickStart: tracker.kickStart ?? now, lastKick: now, done: kicks >= 10 ? { ...tracker.done, kicks: true } : tracker.done });
  },
  resetKicks() {
    set({ kicks: 0, kickStart: null, lastKick: null });
  },
  toggle(id: string) {
    set({ done: { ...tracker.done, [id]: !tracker.done[id] } });
  },
  toggleDose(id: string) {
    set({ taken: { ...tracker.taken, [id]: !tracker.taken[id] } });
  },
};

export function useTracker() {
  return useSyncExternalStore(trackerStore.subscribe, trackerStore.get, trackerStore.get);
}

// Topics ------------------------------------------------------------------

export type TopicId = 'maternity' | 'fertility' | 'reproductive';
/** `icon` is a clay icon (components/system/app-icon.tsx). */
export type TopicCard = { id: string; title: string; icon: AppIconName; tips: string[]; urgent?: boolean };
export type Topic = {
  id: TopicId;
  title: string;
  blurb: string;
  icon: 'mother' | 'sprout' | 'lotus';
  tone: 'cherry' | 'sage' | 'plum';
  cards: TopicCard[];
};

export const TOPICS: Record<TopicId, Topic> = {
  maternity: {
    id: 'maternity',
    title: 'Maternity',
    blurb: 'Week by week care for you and your baby.',
    icon: 'mother',
    tone: 'cherry',
    cards: [
      {
        id: 'danger',
        title: 'Danger signs',
        icon: 'alert',
        urgent: true,
        tips: [
          'Bleeding, severe headache, blurred vision or fits: call 108 now.',
          'Swollen face or hands, or the baby moving less: tell your ASHA today.',
          'High fever or water leaking: go to the PHC today.',
        ],
      },
      { id: 'food', title: 'Eating well', icon: 'food', tips: ['One extra meal a day.', 'Greens, dal, eggs or milk daily.', 'Iron tablet after food, not with tea.'] },
      { id: 'moves', title: 'Baby movements', icon: 'kicks', tips: ['From 28 weeks, count 10 kicks in 2 hours.', 'Fewer kicks than usual? Lie on your left side and count again.', 'Still fewer: call your ASHA.'] },
      { id: 'rest', title: 'Sleep and rest', icon: 'rest', tips: ['Sleep on your left side.', 'Rest for an hour after lunch.', 'Avoid lifting heavy water pots.'] },
      { id: 'meds', title: 'Tablets and injections', icon: 'medicine', tips: ['Iron and calcium every day.', 'Two tetanus injections in pregnancy.', 'Ask before taking any other medicine.'] },
      { id: 'bag', title: 'Hospital bag', icon: 'bag', tips: ['Mother card and ID.', 'Two clean saris, baby clothes, cloth pads.', 'Keep the 108 number and ASHA number written down.'] },
    ],
  },
  fertility: {
    id: 'fertility',
    title: 'Fertility',
    blurb: 'Plan a healthy pregnancy, when you are ready.',
    icon: 'sprout',
    tone: 'sage',
    cards: [
      { id: 'cycle', title: 'Your cycle', icon: 'calendar', tips: ['A cycle is usually 21 to 35 days.', 'The fertile days are about 2 weeks before the next period.', 'Irregular cycles are common; tell a doctor if they stop for 3 months.'] },
      { id: 'folic', title: 'Folic acid', icon: 'medicine', tips: ['Start folic acid 3 months before trying.', 'It protects the baby’s brain and spine.', 'Free at your PHC.'] },
      { id: 'weight', title: 'Healthy weight', icon: 'weight', tips: ['Very low or very high weight can delay pregnancy.', 'Eat regular meals with protein.', 'Walk 30 minutes most days.'] },
      { id: 'doctor', title: 'When to see a doctor', icon: 'doctor', tips: ['No pregnancy after 1 year of trying (6 months if over 35).', 'Very painful periods or no periods.', 'Both partners can be checked.'] },
      { id: 'myths', title: 'Myths and facts', icon: 'tip', tips: ['Not getting pregnant is not only the woman’s problem.', 'Papaya in normal food amounts is safe.', 'Stress alone rarely causes infertility.'] },
    ],
  },
  reproductive: {
    id: 'reproductive',
    title: 'Reproductive health',
    blurb: 'Periods, infections, family planning and your rights.',
    icon: 'lotus',
    tone: 'plum',
    cards: [
      { id: 'periods', title: 'Periods and hygiene', icon: 'water', tips: ['Change pads every 4 to 6 hours.', 'Wash cloth pads with soap and dry them in the sun.', 'Bleeding for more than 7 days: see a doctor.'] },
      { id: 'infection', title: 'Signs of infection', icon: 'alert', urgent: true, tips: ['Itching, bad smell or unusual discharge.', 'Pain when passing urine, or fever with lower belly pain.', 'Treatment is simple and private at the PHC.'] },
      { id: 'planning', title: 'Family planning', icon: 'family', tips: ['Space children at least 2 years apart.', 'Options: pills, Copper-T, injections, condoms.', 'All free and confidential at the PHC.'] },
      { id: 'screening', title: 'Screening', icon: 'scan', tips: ['Cervical screening from age 30.', 'Check your breasts once a month.', 'Ask your ASHA about the next camp.'] },
      { id: 'rights', title: 'Your rights and privacy', icon: 'privacy', tips: ['Your health information is yours.', 'You can ask for a woman doctor or nurse.', 'Nothing you read here is saved or shared.'] },
    ],
  },
};
