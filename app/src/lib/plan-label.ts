import type { PlanItem } from '@/data/care-plan';
import type { StringKey } from '@/i18n/strings';

// A daily-checklist task in the reader's language. The one-tap presets
// (iron, kicks, walk...) have Kannada and Hindi; a task the ASHA typed
// herself is shown exactly as she wrote it.

const KEYS: Record<string, StringKey> = {
  iron: 'plan_iron',
  calcium: 'plan_calcium',
  kicks: 'plan_kicks',
  walk: 'plan_walk',
  water: 'plan_water',
  meal: 'plan_meal',
  rest: 'plan_rest',
};

export function planLabel(item: PlanItem, t: (key: StringKey) => string) {
  const key = KEYS[item.id];
  return key ? t(key) : item.label;
}
