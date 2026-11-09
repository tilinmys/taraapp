import { useEffect, useState, useSyncExternalStore } from 'react';

import type { AppIconName } from '@/components/system/app-icon';
import { setSosNotifier } from '@/data/sos';
import type { Lang, StringKey } from '@/i18n/strings';
import { kv } from '@/lib/kv';

// The care network: zones (villages), staff (ASHA / doctor), doctor slots,
// bookings, notifications and visit reminders. In-memory demo of the
// production schema in deploy/supabase/20261023_care_network.sql; the action
// names match its RPCs (openTimes, bookSlot, cancelBooking, remindVisit),
// so moving to Supabase swaps the bodies, not the screens.
// Design and rules: docs/CARE_NETWORK_DESIGN.md.

// --- Places and people ------------------------------------------------------

export type Zone = { id: string; phcId: string; name: string; kn: string; hi: string; ashaId: string };
export type Staff = {
  id: string;
  role: 'asha' | 'doctor';
  name: string;
  phone: string;
  phcId: string;
  title?: string;
  credentials?: string;
  experience?: string;
  specialty?: string;
  languages?: string[];
  bio?: string;
};

export const PHCS = { hosahalli: { id: 'hosahalli', name: 'Hosahalli PHC', district: 'Bidar' } } as const;

/** Villages around the PHC (Bidar taluk). Exactly one ASHA per zone. */
export const ZONES: Zone[] = [
  { id: 'kamthana', phcId: 'hosahalli', name: 'Kamthana', kn: 'ಕಮಠಾಣ', hi: 'कमठाणा', ashaId: 'sunita' },
  { id: 'chitta', phcId: 'hosahalli', name: 'Chitta', kn: 'ಚಿಟ್ಟಾ', hi: 'चिट्टा', ashaId: 'sunita' },
  { id: 'aliyabad', phcId: 'hosahalli', name: 'Aliyabad', kn: 'ಅಲಿಯಾಬಾದ್', hi: 'अलियाबाद', ashaId: 'sunita' },
  { id: 'janawada', phcId: 'hosahalli', name: 'Janawada', kn: 'ಜನವಾಡ', hi: 'जनवाड़ा', ashaId: 'parvati' },
];

export const STAFF: Staff[] = [
  { id: 'sunita', role: 'asha', name: 'Sunita', phone: '+910000000001', phcId: 'hosahalli' },
  { id: 'parvati', role: 'asha', name: 'Parvati', phone: '+910000000003', phcId: 'hosahalli' },
  {
    id: 'smitha',
    role: 'doctor',
    name: 'Dr. Smitha A.P',
    phone: '+910000000002',
    phcId: 'hosahalli',
    title: 'Founder & High Risk Obstetrician',
    credentials: 'MBBS, MS, DNB(OBG), FFM, FRM, MBA',
    experience: '23 Years Experience (15 as specialist)',
    specialty: 'High Risk Pregnancy, Fetal Programming & Endocrine Care',
    languages: ['English', 'Kannada', 'Hindi'],
    bio: 'Founder & High Risk Obstetrician specializing in High Risk Obstetrics and In-utero Fetal Programming with holistic, evidence-based care.',
  },
  {
    id: 'surbhi',
    role: 'doctor',
    name: 'Dr. Surbhi Sinha',
    phone: '+910000000004',
    phcId: 'hosahalli',
    title: 'Co-Founder & Fertility Specialist',
    credentials: 'MBBS, MS, MD, MRCOG (UK), FRM',
    experience: 'Expert in advanced surgical techniques & fertility solutions',
    specialty: 'Advanced Surgical Techniques, Fertility Solutions & Rapid Recovery',
    languages: ['English', 'Hindi'],
    bio: 'Co-Founder & Fertility Specialist known for precision and empathy, combining cutting-edge technology with compassionate patient care.',
  },
];

/** The signed-in ASHA and doctor of this demo (real app: from the session). */
export const ME_ASHA = 'sunita';
export const ME_DOCTOR = 'smitha';

export const staff = (id: string) =>
  STAFF.find((s) => s.id === id) ??
  (id === 'anand' ? STAFF.find((s) => s.id === 'smitha') : id === 'farzana' ? STAFF.find((s) => s.id === 'surbhi') : undefined);
export const zone = (id: string | undefined) => ZONES.find((z) => z.id === id);
export const zonesOf = (ashaId: string) => ZONES.filter((z) => z.ashaId === ashaId);
/** Location rule: a mother's only point of contact is her zone's ASHA. */
export const ashaForZone = (zoneId: string | undefined) => staff(zone(zoneId)?.ashaId ?? '');
export const doctorsOf = (phcId: string) => STAFF.filter((s) => s.role === 'doctor' && s.phcId === phcId);
export const zoneName = (z: Zone, lang: Lang) => (lang === 'kn' ? z.kn : lang === 'hi' ? z.hi : z.name);

// --- Slots, bookings, notifications -----------------------------------------

export type SlotStatus = 'draft' | 'open' | 'booked' | 'closed';
export type Slot = { id: string; doctorId: string; start: number; minutes: number; status: SlotStatus };
/**
 * A consultation's life, as everyone sees it:
 *   requested    the doctor asked for a visit at this time; her ASHA (or
 *                she) confirms it
 *   confirmed    booked: the ASHA or the mother booked an open time, or the
 *                doctor confirmed it directly
 *   rescheduled  the doctor or the ASHA moved it to another time (still on)
 *   completed    the doctor saw her
 *   cancelled    called off by anyone; the time opens again
 * Every change is told to the other side(s) as a notice.
 */
export type BookingStatus = 'requested' | 'confirmed' | 'rescheduled' | 'completed' | 'cancelled';
/** Statuses of a visit that is still going to happen. */
export const ACTIVE_STATUSES: readonly BookingStatus[] = ['requested', 'confirmed', 'rescheduled'];
export const isActive = (b: Pick<Booking, 'status'>) => ACTIVE_STATUSES.includes(b.status);
/**
 * `ashaId` is always the mother's zone ASHA (her point of contact), whoever
 * booked; `bookedBy` says who booked: the ASHA, the mother herself, or the
 * doctor (a requested or doctor-confirmed consultation).
 */
export type Booking = {
  id: string;
  slotId: string;
  motherId: string;
  ashaId: string;
  bookedBy: 'asha' | 'mother' | 'doctor';
  status: BookingStatus;
  /** When the status last changed. */
  updatedAt?: number;
  createdAt: number;
  reminders: number[];
  /** The ASHA <-> doctor thread about this visit, oldest first. */
  comments: Comment[];
};
/** One message on a booking, from the ASHA or the doctor. */
export type Comment = { id: string; from: 'asha' | 'doctor'; byId: string; text: string; at: number };
/** Who is acting: an ASHA (staff id) or the mother herself. */
export type Actor = { kind: 'asha'; id: string } | { kind: 'mother' };
/** The doctor's usual week: weekdays (0 = Sun) and session he sits in the OPD. */
export type Session = 'morning' | 'afternoon' | 'both';
export type UsualWeek = { weekdays: number[]; session: Session; minutes: number };
export type NoticeKind =
  | 'slots_published'
  | 'booking_confirmed'
  | 'booking_cancelled'
  | 'visit_reminder'
  | 'visit_comment'
  | 'high_risk_flag'
  | 'visit_today'
  | 'checklist_updated'
  | 'appointment_update'
  | 'advice_sent'
  | 'sos';
/** One icon per notice kind, the same in every inbox (doctor, mother, ASHA). */
export const NOTICE_ICONS: Record<NoticeKind, AppIconName> = {
  slots_published: 'calendar',
  booking_confirmed: 'calendar',
  booking_cancelled: 'calendar',
  visit_reminder: 'calendar',
  visit_comment: 'talk',
  high_risk_flag: 'alert',
  visit_today: 'ashaWorker',
  checklist_updated: 'checklist',
  appointment_update: 'calendar',
  advice_sent: 'checklist',
  sos: 'ambulance',
};
/**
 * What the notice says, as translation keys + values, so the ASHA and the
 * mother read it in THEIR language (lib/notice-text.ts). `when` (a time in
 * ms) is formatted for the reader's language. `title` / `body` on the notice
 * stay as the English fallback (doctor screens, logs).
 */
export type NoticeMsg = { title: StringKey; body: StringKey; p: Record<string, string | number> };
/** `ref` points at what the notice is about (a booking or a mother), so tapping it can open that. */
export type Notice = { id: string; to: { kind: 'staff' | 'mother'; id: string }; kind: NoticeKind; title: string; body: string; msg?: NoticeMsg; at: number; read: boolean; ref?: { bookingId?: string; motherId?: string } };

const MIN = 60_000;
const DAY = 24 * 60 * MIN;
/** Slots that start sooner than this can no longer be booked. */
export const BOOKING_CUTOFF_MS = 30 * MIN;
export const SLOT_LENGTHS = [15, 20, 30] as const;
/** OPD sessions a doctor taps instead of typing times (minutes from midnight). */
export const SESSIONS: Record<Session, { label: string; hours: string; windows: [number, number][] }> = {
  morning: { label: 'Morning', hours: '10:00-13:00', windows: [[10 * 60, 13 * 60]] },
  afternoon: { label: 'Afternoon', hours: '14:00-17:00', windows: [[14 * 60, 17 * 60]] },
  both: { label: 'Full day', hours: '10:00-17:00', windows: [[10 * 60, 13 * 60], [14 * 60, 17 * 60]] },
};
/** How far ahead a doctor opens times, and mothers / ASHAs can book. */
export const PLAN_DAYS = 7;

/** Local midnight of the day `offset` days from today. */
export function dayStart(offset = 0, from = Date.now()) {
  const d = new Date(from);
  d.setHours(0, 0, 0, 0);
  return d.getTime() + offset * DAY;
}
export const sameDay = (a: number, b: number) => dayStart(0, a) === dayStart(0, b);
export const slotEnd = (s: Slot) => s.start + s.minutes * MIN;

export function fmtTime(ms: number) {
  const d = new Date(ms);
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}
const DOW = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
export function fmtDay(ms: number, now = Date.now()) {
  if (sameDay(ms, now)) return 'Today';
  if (sameDay(ms, now + DAY)) return 'Tomorrow';
  const d = new Date(ms);
  return `${DOW[d.getDay()]} ${d.getDate()} ${MON[d.getMonth()]}`;
}
export const fmtWhen = (ms: number) => `${fmtDay(ms)}, ${fmtTime(ms)}`;
export const WEEKDAYS = DOW;

type State = { slots: Slot[]; bookings: Booking[]; notices: Notice[]; usual: Record<string, UsualWeek> };
let seq = 1;
const id = (p: string) => `${p}${seq++}`;

/** Demo seed: Dr. Smitha open tomorrow morning and in 3 days; Lakshmi booked. */
function seed(): State {
  const now = Date.now();
  const slots: Slot[] = [];
  const mk = (doctorId: string, dayOffset: number, fromMin: number, count: number, minutes: number, status: SlotStatus) => {
    for (let i = 0; i < count; i++) {
      const start = dayStart(dayOffset, now) + (fromMin + i * minutes) * MIN;
      slots.push({ id: id('s'), doctorId, start, minutes, status });
    }
  };
  // Dr. Surbhi visits on Tuesdays and Thursdays: her next such day.
  const surbhiDay = [1, 2, 3, 4, 5, 6].find((i) => [2, 4].includes(new Date(dayStart(i, now)).getDay())) ?? 2;
  mk('smitha', 1, 10 * 60, 6, 30, 'open');
  mk('smitha', 3, 14 * 60, 6, 30, 'open');
  mk('surbhi', surbhiDay, 10 * 60, 8, 20, 'open');
  const first = slots[0];
  first.status = 'booked';
  const bookings: Booking[] = [
    {
      id: id('b'),
      slotId: first.id,
      motherId: 'lakshmi',
      ashaId: 'sunita',
      bookedBy: 'asha',
      status: 'confirmed',
      createdAt: now - 3 * 60 * MIN,
      reminders: [],
      comments: [{ id: id('c'), from: 'asha', byId: 'sunita', text: 'BP was 148/96 at today’s check-in. She also has headaches.', at: now - 3 * 60 * MIN }],
    },
  ];
  const usual: Record<string, UsualWeek> = {
    smitha: { weekdays: [1, 3, 5], session: 'morning', minutes: 30 },
    surbhi: { weekdays: [2, 4], session: 'morning', minutes: 20 },
    anand: { weekdays: [1, 3, 5], session: 'morning', minutes: 30 },
    farzana: { weekdays: [2, 4], session: 'morning', minutes: 20 },
  };
  const notices: Notice[] = [
    { id: id('n'), to: { kind: 'staff', id: 'sunita' }, kind: 'slots_published', title: 'Dr. Surbhi Sinha opened 8 visit slots', body: `${fmtDay(dayStart(surbhiDay, now))} · 10:00-12:40`, msg: { title: 'aSlotsT', body: 'aSlotsB', p: { doctor: 'Dr. Surbhi Sinha', n: 8 } }, at: now - 40 * MIN, read: false },
    { id: id('n'), to: { kind: 'staff', id: 'sunita' }, kind: 'slots_published', title: 'Dr. Smitha A.P opened 12 visit slots', body: `${fmtDay(dayStart(1, now))} and ${fmtDay(dayStart(3, now))}`, msg: { title: 'aSlotsT', body: 'aSlotsB', p: { doctor: 'Dr. Smitha A.P', n: 12 } }, at: now - 5 * 60 * MIN, read: true },
    { id: id('n'), to: { kind: 'mother', id: 'lakshmi' }, kind: 'booking_confirmed', title: 'Your doctor visit is booked', body: `Dr. Smitha A.P · ${fmtWhen(first.start)} · Sunita booked it for you`, msg: { title: 'nBookedT', body: 'nBookedB', p: { doctor: 'Dr. Smitha A.P', when: first.start, asha: 'Sunita' } }, ref: { bookingId: bookings[0].id }, at: now - 3 * 60 * MIN, read: false },
  ];
  return { slots, bookings, notices, usual };
}

/** The next PLAN_DAYS days (from tomorrow) that fall on his usual weekdays. */
export function usualDays(u: UsualWeek, from = Date.now()) {
  return Array.from({ length: PLAN_DAYS }, (_, i) => dayStart(i + 1, from)).filter((d) => u.weekdays.includes(new Date(d).getDay()));
}

/**
 * The visit times a session would create on these days: never in the past,
 * never overlapping his live times (the same rule as the DB's exclusion
 * constraint). Pure, so screens can preview "18 new times" before the tap.
 */
export function planTimes(doctorId: string, days: number[], session: Session, minutes: number, s: State = state) {
  const now = Date.now();
  const live = s.slots.filter((x) => x.doctorId === doctorId && x.status !== 'closed');
  const out: number[] = [];
  for (const day of days) {
    for (const [from, to] of SESSIONS[session].windows) {
      for (let t = from; t + minutes <= to; t += minutes) {
        const start = day + t * MIN;
        const end = start + minutes * MIN;
        if (start <= now + BOOKING_CUTOFF_MS) continue;
        if (live.some((x) => start < slotEnd(x) && x.start < end)) continue;
        out.push(start);
      }
    }
  }
  return out;
}

const dayList = (days: number[]) => [...new Set(days.map((d) => dayStart(0, d)))].sort((a, b) => a - b).map((d) => fmtDay(d)).join(', ');
const ashasOf = (phcId: string) => [...new Set(ZONES.filter((z) => z.phcId === phcId).map((z) => z.ashaId))];

// Saved on the phone (lib/kv): bookings, their statuses and notices are
// still there when the app reopens. A save older than PLAN_DAYS is dropped
// (its times have passed) and the demo week starts fresh.
const SAVE_KEY = 'tara.network.v1';
function load(): State {
  const saved = kv.get<{ at: number; state: State }>(SAVE_KEY);
  if (!saved || Date.now() - saved.at > PLAN_DAYS * DAY) return seed();
  // New ids must never collide with saved ones ("b12" after a reload).
  const all = [...saved.state.slots, ...saved.state.bookings, ...saved.state.notices, ...saved.state.bookings.flatMap((b) => b.comments)];
  seq = Math.max(seq, ...all.map((x) => Number(x.id.replace(/\D/g, '')) || 0)) + 1;
  return saved.state;
}

let state: State = load();
const listeners = new Set<() => void>();
const set = (next: Partial<State>) => {
  state = { ...state, ...next };
  kv.set(SAVE_KEY, { at: Date.now(), state });
  listeners.forEach((l) => l());
};
const notify = (n: Omit<Notice, 'id' | 'at' | 'read'>) => ({ ...n, id: id('n'), at: Date.now(), read: false });

export type Result = { ok: true } | { ok: false; reason: 'taken' | 'too_soon' | 'has_visit' | 'not_yours' | 'past' };

const APPT_KEYS: Record<BookingStatus, { asha: StringKey; mother: StringKey; word: string }> = {
  requested: { asha: 'aApptRequestedT', mother: 'nApptRequestedT', word: 'requested' },
  confirmed: { asha: 'aApptConfirmedT', mother: 'nApptConfirmedT', word: 'confirmed' },
  rescheduled: { asha: 'aApptMovedT', mother: 'nApptMovedT', word: 'moved' },
  completed: { asha: 'aApptDoneT', mother: 'nApptDoneT', word: 'completed' },
  cancelled: { asha: 'aApptCancelledT', mother: 'nApptCancelledT', word: 'cancelled' },
};

/**
 * One consultation status change: the booking takes the new status, `extra`
 * (slot changes) is applied, and her ASHA, the mother and the doctor each
 * get a notice in their own language. Shared by every doctor-side action.
 */
function apptChange(b: Booking, s: Slot, status: BookingStatus, extra: Partial<State>, motherName: string) {
  const doctor = staff(s.doctorId)?.name ?? 'Doctor';
  const keys = APPT_KEYS[status];
  const p = { doctor, mother: motherName, when: s.start };
  const ref = { bookingId: b.id, motherId: b.motherId };
  set({
    ...extra,
    bookings: state.bookings.map((x) => (x.id === b.id ? { ...x, status, updatedAt: Date.now() } : x)),
    notices: [
      notify({ to: { kind: 'staff', id: b.ashaId }, kind: 'appointment_update', title: `${motherName}: visit ${keys.word}`, body: `${doctor} · ${fmtWhen(s.start)}`, msg: { title: keys.asha, body: 'aApptB', p }, ref }),
      notify({ to: { kind: 'mother', id: b.motherId }, kind: 'appointment_update', title: `Your doctor visit was ${keys.word}`, body: `${doctor} · ${fmtWhen(s.start)}`, msg: { title: keys.mother, body: 'nApptB', p }, ref: { bookingId: b.id } }),
      notify({ to: { kind: 'staff', id: s.doctorId }, kind: 'appointment_update', title: `${motherName}: visit ${keys.word}`, body: fmtWhen(s.start), ref }),
      ...state.notices,
    ],
  });
}

export const network = {
  subscribe(l: () => void) {
    listeners.add(l);
    return () => listeners.delete(l);
  },
  get: () => state,

  /**
   * Doctor OPENS a session on one or more days in a single tap: the times
   * are created already open for booking (no separate lock step) and every
   * ASHA of his PHC gets ONE message for the lot. Past and overlapping
   * times are skipped (planTimes). Returns how many opened. RPC: open_slots.
   */
  openTimes(doctorId: string, days: number[], session: Session, minutes: number) {
    const doctor = staff(doctorId);
    const starts = planTimes(doctorId, days, session, minutes);
    if (!doctor || !starts.length) return 0;
    const added: Slot[] = starts.map((start) => ({ id: id('s'), doctorId, start, minutes, status: 'open' }));
    set({
      slots: [...state.slots, ...added].sort((a, b) => a.start - b.start),
      notices: [
        ...ashasOf(doctor.phcId).map((a) =>
          notify({ to: { kind: 'staff', id: a }, kind: 'slots_published', title: `${doctor.name} opened ${added.length} visit times`, body: `${dayList(starts)} · ${SESSIONS[session].label} ${SESSIONS[session].hours}`, msg: { title: 'aSlotsT', body: 'aSlotsB', p: { doctor: doctor.name, n: added.length } } }),
        ),
        ...state.notices,
      ],
    });
    return added.length;
  },

  /** Doctor saves his usual week (the one-tap "Open my usual week"). */
  setUsual(doctorId: string, usual: UsualWeek) {
    set({ usual: { ...state.usual, [doctorId]: usual } });
  },

  /** Doctor closes one open time. Booked times stay: the booking must be cancelled first. */
  removeSlot(slotId: string) {
    const s = state.slots.find((x) => x.id === slotId);
    if (!s || s.status === 'booked') return;
    set({ slots: s.status === 'draft' ? state.slots.filter((x) => x.id !== slotId) : state.slots.map((x) => (x.id === slotId ? { ...x, status: 'closed' } : x)) });
  },

  /** Doctor is away: close every open time of that day in one tap. Booked visits stay. Returns how many closed. */
  closeDay(doctorId: string, day: number) {
    const ids = new Set(state.slots.filter((s) => s.doctorId === doctorId && s.status === 'open' && sameDay(s.start, day)).map((s) => s.id));
    if (ids.size) set({ slots: state.slots.map((s) => (ids.has(s.id) ? { ...s, status: 'closed' } : s)) });
    return ids.size;
  },

  /**
   * Book an open time for a mother: by her zone's ASHA, or by the mother
   * herself (same checks as the book_slot RPC). The doctor is always told;
   * when the mother books, her ASHA is told too, so the ASHA stays her
   * point of contact.
   */
  bookSlot(slotId: string, mother: { id: string; zoneId?: string; name: string }, by: Actor, note?: string): Result {
    const s = state.slots.find((x) => x.id === slotId);
    const ashaId = zone(mother.zoneId)?.ashaId;
    if (!ashaId || (by.kind === 'asha' && by.id !== ashaId)) return { ok: false, reason: 'not_yours' };
    if (!s || s.status !== 'open') return { ok: false, reason: 'taken' };
    if (s.start < Date.now() + BOOKING_CUTOFF_MS) return { ok: false, reason: 'too_soon' };
    if (upcomingBooking(mother.id)) return { ok: false, reason: 'has_visit' };
    const doctor = staff(s.doctorId);
    const asha = staff(ashaId);
    const text = note?.trim();
    const booking: Booking = {
      id: id('b'),
      slotId,
      motherId: mother.id,
      ashaId,
      bookedBy: by.kind,
      status: 'confirmed',
      createdAt: Date.now(),
      reminders: [],
      comments: text && by.kind === 'asha' ? [{ id: id('c'), from: 'asha', byId: by.id, text, at: Date.now() }] : [],
    };
    const self = by.kind === 'mother';
    set({
      slots: state.slots.map((x) => (x.id === slotId ? { ...x, status: 'booked' } : x)),
      bookings: [booking, ...state.bookings],
      notices: [
        notify({
          to: { kind: 'staff', id: s.doctorId },
          kind: 'booking_confirmed',
          title: `${mother.name} booked`,
          body: `${fmtWhen(s.start)} · ${self ? 'booked by herself' : `by ${asha?.name ?? 'ASHA'}`}${booking.comments[0] ? `\n“${booking.comments[0].text}”` : ''}`,
          ref: { bookingId: booking.id, motherId: mother.id },
        }),
        ...(self ? [notify({ to: { kind: 'staff', id: ashaId }, kind: 'booking_confirmed', title: `${mother.name} booked a doctor visit`, body: `${doctor?.name ?? 'Doctor'} · ${fmtWhen(s.start)}`, msg: { title: 'aSelfBookedT', body: 'aVisitB', p: { mother: mother.name, doctor: doctor?.name ?? '', when: s.start } }, ref: { bookingId: booking.id, motherId: mother.id } })] : []),
        notify({
          to: { kind: 'mother', id: mother.id },
          kind: 'booking_confirmed',
          title: 'Your doctor visit is booked',
          body: `${doctor?.name ?? 'Doctor'} · ${fmtWhen(s.start)} · ${self ? `${asha?.name ?? 'Your ASHA'} knows` : `${asha?.name ?? 'Your ASHA'} booked it for you`}`,
          msg: { title: 'nBookedT', body: self ? 'nBookedSelfB' : 'nBookedB', p: { doctor: doctor?.name ?? '', when: s.start, asha: asha?.name ?? '' } },
          ref: { bookingId: booking.id },
        }),
        ...state.notices,
      ],
    });
    return { ok: true };
  },

  /** The ASHA or the mother cancels: the time opens again (if still ahead); the others are told. */
  cancelBooking(bookingId: string, by: Actor['kind'] | 'doctor' = 'asha', motherName = 'A mother') {
    const b = state.bookings.find((x) => x.id === bookingId);
    const s = b && state.slots.find((x) => x.id === b.slotId);
    if (!b || !s || !isActive(b)) return;
    const reopened = state.slots.map((x) => (x.id === s.id ? { ...x, status: s.start > Date.now() ? ('open' as const) : ('closed' as const) } : x));
    if (by === 'doctor') {
      apptChange(b, s, 'cancelled', { slots: reopened }, motherName);
      return;
    }
    const asha = staff(b.ashaId);
    set({
      bookings: state.bookings.map((x) => (x.id === bookingId ? { ...x, status: 'cancelled', updatedAt: Date.now() } : x)),
      slots: state.slots.map((x) => (x.id === s.id ? { ...x, status: s.start > Date.now() ? 'open' : 'closed' } : x)),
      notices: [
        notify({ to: { kind: 'staff', id: s.doctorId }, kind: 'booking_cancelled', title: 'Visit cancelled', body: fmtWhen(s.start) }),
        by === 'mother'
          ? notify({ to: { kind: 'staff', id: b.ashaId }, kind: 'booking_cancelled', title: `${motherName} cancelled her visit`, body: `${staff(s.doctorId)?.name ?? 'Doctor'} · ${fmtWhen(s.start)}`, msg: { title: 'aCancelledT', body: 'aVisitB', p: { mother: motherName, doctor: staff(s.doctorId)?.name ?? '', when: s.start } }, ref: { motherId: b.motherId } })
          : notify({ to: { kind: 'mother', id: b.motherId }, kind: 'booking_cancelled', title: 'Your visit was cancelled', body: `${asha?.name ?? 'Your ASHA'} will call you with a new time`, msg: { title: 'nCancelT', body: 'nCancelB', p: { asha: asha?.name ?? '' } } }),
        ...state.notices,
      ],
    });
  },

  /**
   * The ASHA <-> doctor thread on a booking: a note when booking, a question
   * later, the doctor's reply. The other side is told. RPC: add_visit_comment.
   */
  addComment(bookingId: string, from: 'asha' | 'doctor', byId: string, text: string, motherName = 'A mother') {
    const b = state.bookings.find((x) => x.id === bookingId);
    const s = b && state.slots.find((x) => x.id === b.slotId);
    const body = text.trim();
    if (!b || !s || !body) return;
    const c: Comment = { id: id('c'), from, byId, text: body, at: Date.now() };
    const to = from === 'asha' ? s.doctorId : b.ashaId;
    set({
      bookings: state.bookings.map((x) => (x.id === bookingId ? { ...x, comments: [...x.comments, c] } : x)),
      notices: [
        notify({
          to: { kind: 'staff', id: to },
          kind: 'visit_comment',
          title: `${staff(byId)?.name ?? (from === 'asha' ? 'ASHA' : 'Doctor')} about ${motherName}`,
          body: `“${body}” · ${fmtWhen(s.start)}`,
          msg: { title: 'aCommentT', body: 'aCommentB', p: { from: staff(byId)?.name ?? '', mother: motherName, text: body } },
          ref: { bookingId, motherId: b.motherId },
        }),
        ...state.notices,
      ],
    });
  },

  /**
   * The ASHA marks a mother "Needs care" and chooses to tell the doctor:
   * every doctor of her PHC gets one alert. RPC: flag_high_risk.
   */
  alertDoctors(motherId: string, motherName: string, ashaId: string, zoneId?: string, note?: string) {
    const phc = zone(zoneId)?.phcId ?? PHCS.hosahalli.id;
    const asha = staff(ashaId);
    const said = note?.trim();
    set({
      notices: [
        ...doctorsOf(phc).map((d) =>
          notify({
            to: { kind: 'staff', id: d.id },
            kind: 'high_risk_flag',
            title: `${motherName} needs care`,
            body: `${said ? `“${said}”\n` : ''}Status: Needs care · ${asha?.name ?? 'Her ASHA'} · ${zone(zoneId)?.name ?? ''}`,
            ref: { motherId },
          }),
        ),
        ...state.notices,
      ],
    });
    return doctorsOf(phc).map((d) => d.name);
  },

  /**
   * The doctor arranges a consultation at one of HIS open times, after
   * reading her report: `requested` (her ASHA confirms it with her) or
   * `confirmed` (he books it outright). Same checks as bookSlot.
   */
  doctorSchedule(slotId: string, mother: { id: string; zoneId?: string; name: string }, doctorId: string, status: 'requested' | 'confirmed', note?: string): Result {
    const s = state.slots.find((x) => x.id === slotId);
    const ashaId = zone(mother.zoneId)?.ashaId;
    if (!ashaId) return { ok: false, reason: 'not_yours' };
    if (!s || s.status !== 'open' || s.doctorId !== doctorId) return { ok: false, reason: 'taken' };
    if (s.start < Date.now() + BOOKING_CUTOFF_MS) return { ok: false, reason: 'too_soon' };
    if (upcomingBooking(mother.id)) return { ok: false, reason: 'has_visit' };
    const text = note?.trim();
    const booking: Booking = {
      id: id('b'),
      slotId,
      motherId: mother.id,
      ashaId,
      bookedBy: 'doctor',
      status,
      createdAt: Date.now(),
      updatedAt: Date.now(),
      reminders: [],
      comments: text ? [{ id: id('c'), from: 'doctor', byId: doctorId, text, at: Date.now() }] : [],
    };
    state = { ...state, bookings: [booking, ...state.bookings] };
    apptChange(booking, s, status, { slots: state.slots.map((x) => (x.id === slotId ? { ...x, status: 'booked' as const } : x)) }, mother.name);
    return { ok: true };
  },

  /** A requested consultation is confirmed (by her ASHA, by her, or by the doctor). */
  confirmBooking(bookingId: string, motherName = 'A mother') {
    const b = state.bookings.find((x) => x.id === bookingId);
    const s = b && state.slots.find((x) => x.id === b.slotId);
    if (!b || !s || b.status !== 'requested') return;
    apptChange(b, s, 'confirmed', {}, motherName);
  },

  /** Move a visit to another open time of the same doctor: the old time opens again. */
  rescheduleBooking(bookingId: string, newSlotId: string, motherName = 'A mother'): Result {
    const b = state.bookings.find((x) => x.id === bookingId);
    const old = b && state.slots.find((x) => x.id === b.slotId);
    const s = state.slots.find((x) => x.id === newSlotId);
    if (!b || !old || !isActive(b)) return { ok: false, reason: 'past' };
    if (!s || s.status !== 'open' || s.doctorId !== old.doctorId) return { ok: false, reason: 'taken' };
    if (s.start < Date.now() + BOOKING_CUTOFF_MS) return { ok: false, reason: 'too_soon' };
    const moved = { ...b, slotId: newSlotId };
    state = { ...state, bookings: state.bookings.map((x) => (x.id === bookingId ? moved : x)) };
    const slots = state.slots.map((x) =>
      x.id === old.id ? { ...x, status: old.start > Date.now() ? ('open' as const) : ('closed' as const) } : x.id === newSlotId ? { ...x, status: 'booked' as const } : x,
    );
    apptChange(moved, s, 'rescheduled', { slots }, motherName);
    return { ok: true };
  },

  /** The doctor saw her. */
  completeBooking(bookingId: string, motherName = 'A mother') {
    const b = state.bookings.find((x) => x.id === bookingId);
    const s = b && state.slots.find((x) => x.id === b.slotId);
    if (!b || !s || !isActive(b)) return;
    apptChange(b, s, 'completed', {}, motherName);
  },

  /** The doctor sent (or updated) the advice: her ASHA and she are told. */
  adviceSent(motherId: string, motherName: string, zoneId: string | undefined, doctorId: string, steps: number) {
    const doctor = staff(doctorId)?.name ?? 'The doctor';
    const ashaId = zone(zoneId)?.ashaId;
    set({
      notices: [
        ...(ashaId
          ? [notify({ to: { kind: 'staff', id: ashaId }, kind: 'advice_sent', title: `${doctor} sent advice for ${motherName}`, body: `${steps} step${steps === 1 ? '' : 's'}`, msg: { title: 'aAdviceT', body: 'aAdviceB', p: { doctor, mother: motherName, n: steps } }, ref: { motherId } })]
          : []),
        notify({ to: { kind: 'mother', id: motherId }, kind: 'advice_sent', title: `Advice from ${doctor}`, body: 'Open it to read the next steps', msg: { title: 'nAdviceT', body: 'nAdviceB', p: { doctor } }, ref: { motherId } }),
        ...state.notices,
      ],
    });
  },

  /** ASHA reminds the mother of her visit (in-app now; SMS / IVR later). */
  remindVisit(bookingId: string) {
    const b = state.bookings.find((x) => x.id === bookingId);
    const s = b && state.slots.find((x) => x.id === b.slotId);
    if (!b || !s || !isActive(b)) return;
    const asha = staff(b.ashaId);
    set({
      bookings: state.bookings.map((x) => (x.id === bookingId ? { ...x, reminders: [Date.now(), ...x.reminders] } : x)),
      notices: [
        notify({ to: { kind: 'mother', id: b.motherId }, kind: 'visit_reminder', title: `${asha?.name ?? 'Your ASHA'} reminds you of your visit`, body: `${staff(s.doctorId)?.name ?? 'Doctor'} · ${fmtWhen(s.start)} · ${PHCS.hosahalli.name}`, msg: { title: 'nRemindT', body: 'nRemindB', p: { asha: asha?.name ?? '', doctor: staff(s.doctorId)?.name ?? '', when: s.start, phc: PHCS.hosahalli.name } }, ref: { bookingId: b.id } }),
        ...state.notices,
      ],
    });
  },

  /**
   * Tell a mother something her ASHA just did for her (she will visit today,
   * she changed the daily checklist). In the app it shows live; outside, it
   * is a phone push (docs/NOTIFICATIONS_DESIGN.md). RPC: notify_mother.
   */
  tellMother(motherId: string, kind: 'visit_today' | 'checklist_updated', ashaId: string) {
    const asha = staff(ashaId)?.name ?? '';
    const msg: NoticeMsg =
      kind === 'visit_today' ? { title: 'nVisitTodayT', body: 'nVisitTodayB', p: { asha } } : { title: 'nChecklistT', body: 'nChecklistB', p: { asha } };
    const english = kind === 'visit_today' ? [`${asha} will visit you today`, 'Keep your MCP card ready'] : [`${asha} updated your daily checklist`, 'See today’s tasks on your Home'];
    set({ notices: [notify({ to: { kind: 'mother', id: motherId }, kind, title: english[0], body: english[1], msg }), ...state.notices] });
  },

  /**
   * SOS (data/sos.ts): an alert, or the all-clear, to one responder, in
   * her own language. Real app: a high-priority push + SMS fallback.
   */
  sosNotice(toId: string, incident: { motherId: string; motherName: string; zoneId: string }, km: number, kind: 'alert' | 'cancelled') {
    const z = zone(incident.zoneId);
    const p = { mother: incident.motherName, km: km.toFixed(1), village: z?.name ?? '' };
    const msg: NoticeMsg = kind === 'alert' ? { title: 'sosNoticeT', body: 'sosNoticeB', p } : { title: 'sosCancelT', body: 'sosCancelB', p };
    const english =
      kind === 'alert'
        ? [`SOS: ${incident.motherName} needs help now`, `${p.km} km away · ${p.village} · call her or go now`]
        : [`SOS cancelled: ${incident.motherName} is safe`, 'She cancelled the alert herself'];
    set({ notices: [notify({ to: { kind: 'staff', id: toId }, kind: 'sos', title: english[0], body: english[1], msg, ref: { motherId: incident.motherId } }), ...state.notices] });
  },

  markRead(to: Notice['to']) {
    if (!state.notices.some((n) => !n.read && n.to.kind === to.kind && n.to.id === to.id)) return;
    set({ notices: state.notices.map((n) => (n.to.kind === to.kind && n.to.id === to.id ? { ...n, read: true } : n)) });
  },

  reset() {
    state = seed();
    kv.remove(SAVE_KEY);
    listeners.forEach((l) => l());
  },
};

/**
 * The current time for screens, refreshed every minute, so an open screen
 * notices when a slot passes its booking cutoff or a visit becomes "soon".
 */
export function useNow(intervalMs = 60_000) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(t);
  }, [intervalMs]);
  return now;
}

export function useNetwork() {
  return useSyncExternalStore(network.subscribe, network.get, network.get);
}

export const slotOf = (b: Booking) => state.slots.find((s) => s.id === b.slotId);

/** Her next confirmed visit that has not started yet (at most one: see bookSlot). */
export function upcomingBooking(motherId: string, s: State = state) {
  const now = Date.now();
  return s.bookings.find((b) => b.motherId === motherId && isActive(b) && (s.slots.find((x) => x.id === b.slotId)?.start ?? 0) > now);
}

/** Her most recent consultation in any status (newest first): what the doctor's case shows. */
export const latestBooking = (motherId: string, s: State = state) => s.bookings.find((b) => b.motherId === motherId);

/**
 * A doctor's coming days at a glance: for each day with times, the hours,
 * how many are still free (bookable) and how many are booked. What the ASHA
 * and the mother read before they book.
 */
export function doctorWeek(doctorId: string, s: State = state, now = Date.now()) {
  const days: { day: number; from: number; to: number; free: number; booked: number }[] = [];
  for (let i = 0; i <= PLAN_DAYS; i++) {
    const day = dayStart(i, now);
    const live = s.slots.filter((x) => x.doctorId === doctorId && sameDay(x.start, day) && (x.status === 'open' || x.status === 'booked') && x.start > now);
    if (!live.length) continue;
    days.push({
      day,
      from: Math.min(...live.map((x) => x.start)),
      to: Math.max(...live.map(slotEnd)),
      free: live.filter((x) => x.status === 'open' && x.start > now + BOOKING_CUTOFF_MS).length,
      booked: live.filter((x) => x.status === 'booked').length,
    });
  }
  return days;
}

export const inbox = (s: State, to: Notice['to']) => s.notices.filter((n) => n.to.kind === to.kind && n.to.id === to.id);

// SOS alerts land in the responders' notifications (data/sos.ts stays free
// of the network module: no import cycle).
setSosNotifier((to, incident, kind) => network.sosNotice(to.id, incident, to.km, kind));
