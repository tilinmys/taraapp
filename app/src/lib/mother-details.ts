import type { MotherDetails } from '@/data/store';
import type { Lang } from '@/i18n/strings';

// The details form's rules, kept apart from its UI (components/asha/
// mother-details-form) so the add and edit screens and the tests share them.

export type DetailsDraft = { name: string; months: number | null; lang: Lang; zoneId?: string; phone: string };

/** Why the draft cannot be saved yet, per field (all false = ready). */
export function detailsProblems(d: DetailsDraft) {
  const digits = d.phone.replace(/\D/g, '');
  return {
    name: d.name.trim().length < 2,
    months: d.months === null,
    zone: !d.zoneId,
    phone: digits.length > 0 && digits.length !== 10 && !(digits.length === 12 && digits.startsWith('91')),
  };
}

/** The draft as the store's details, or null when it is not ready. */
export function toDetails(d: DetailsDraft): MotherDetails | null {
  const p = detailsProblems(d);
  if (p.name || p.months || p.zone || p.phone || d.months === null || !d.zoneId) return null;
  const digits = d.phone.replace(/\D/g, '').slice(-10);
  return { name: d.name, months: d.months, language: d.lang, zoneId: d.zoneId, phone: digits.length === 10 ? digits : undefined };
}
