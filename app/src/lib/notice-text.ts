import type { Notice } from '@/data/care-network';
import type { Lang, StringKey } from '@/i18n/strings';
import { whenLabel } from '@/lib/day-label';

/**
 * A notice in the READER's language: its translation keys filled with its
 * values (a `when` time is written the local way, e.g. "ನಾಳೆ, 10:00").
 * Notices without keys (doctor-side, old ones) fall back to their English.
 */
export function noticeText(n: Notice, t: (key: StringKey, values?: Record<string, string | number>) => string, lang: Lang) {
  if (!n.msg) return { title: n.title, body: n.body.split('\n').join(' ') };
  const p = Object.fromEntries(Object.entries(n.msg.p).map(([k, v]) => [k, k === 'when' && typeof v === 'number' ? whenLabel(v, lang) : v]));
  return { title: t(n.msg.title, p), body: t(n.msg.body, p) };
}
