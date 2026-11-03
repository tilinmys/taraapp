import { dayStart, fmtTime } from '@/data/care-network';
import type { Lang } from '@/i18n/strings';

// Day names an ASHA reads at a glance, in her language: "Today", "Tomorrow",
// then "Mon 27". Numbers stay as digits (read the same in every script).

const DOW: Record<Lang, string[]> = {
  en: ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'],
  kn: ['ಭಾನು', 'ಸೋಮ', 'ಮಂಗಳ', 'ಬುಧ', 'ಗುರು', 'ಶುಕ್ರ', 'ಶನಿ'],
  hi: ['रवि', 'सोम', 'मंगल', 'बुध', 'गुरु', 'शुक्र', 'शनि'],
};
const TODAY: Record<Lang, [string, string]> = { en: ['Today', 'Tomorrow'], kn: ['ಇಂದು', 'ನಾಳೆ'], hi: ['आज', 'कल'] };

export function dayLabel(ms: number, lang: Lang) {
  const d = Math.round((dayStart(0, ms) - dayStart(0)) / 86_400_000);
  if (d === 0) return TODAY[lang][0];
  if (d === 1) return TODAY[lang][1];
  const date = new Date(ms);
  return `${DOW[lang][date.getDay()]} ${date.getDate()}`;
}

export const whenLabel = (ms: number, lang: Lang) => `${dayLabel(ms, lang)}, ${fmtTime(ms)}`;

const REL: Record<Lang, { today: string; yesterday: string; daysAgo: (n: string) => string; justNow: string }> = {
  en: { today: 'Today', yesterday: 'Yesterday', daysAgo: (n) => `${n} days ago`, justNow: 'Just now' },
  kn: { today: 'ಇಂದು', yesterday: 'ನಿನ್ನೆ', daysAgo: (n) => `${n} ದಿನಗಳ ಹಿಂದೆ`, justNow: 'ಈಗಷ್ಟೇ' },
  hi: { today: 'आज', yesterday: 'कल', daysAgo: (n) => `${n} दिन पहले`, justNow: 'अभी' },
};

/**
 * A record's English relative date ("Today, 10:42", "Yesterday, 16:10",
 * "4 days ago", "Just now") in the reader's language. Anything else (a
 * real date) is returned as it is: digits read the same in every script.
 */
export function relLabel(label: string, lang: Lang) {
  if (lang === 'en') return label;
  const r = REL[lang];
  return label
    .replace(/^Just now/i, r.justNow)
    .replace(/^Today/i, r.today)
    .replace(/^Tomorrow/i, TODAY[lang][1])
    .replace(/^Yesterday/i, r.yesterday)
    .replace(/^(\d+)\s+days?\s+ago/i, (_, n: string) => r.daysAgo(n));
}
