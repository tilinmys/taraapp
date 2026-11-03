import { beforeEach, describe, expect, it, jest } from '@jest/globals';

import { detailsProblems, toDetails } from '@/lib/mother-details';

import { BOOKING_CUTOFF_MS, inbox, isActive, latestBooking, ME_ASHA, ME_DOCTOR, network, upcomingBooking } from '../care-network';
import { carePlanStore } from '../care-plan';
import { dayPlanStore } from '../day-plan';
import { store } from '../store';

// The UX-review flows, end to end on the data layer the screens trust:
//   ASHA     edit her details (Save), tell the doctor WITH a note, tick visits
//   Doctor   send advice, request / confirm / reschedule / complete / cancel
//            a consultation; every change reaches the ASHA and the mother

jest.mock('expo-router', () => ({ router: { canGoBack: jest.fn(), back: jest.fn(), replace: jest.fn(), push: jest.fn() } }));

const kavitha = () => store.get().find((m) => m.id === 'kavitha')!;
const openSlot = (skip = 0) =>
  network
    .get()
    .slots.filter((s) => s.doctorId === ME_DOCTOR && s.status === 'open' && s.start > Date.now() + BOOKING_CUTOFF_MS)
    .sort((a, b) => a.start - b.start)[skip];
const notices = (kind: 'staff' | 'mother', id: string) => inbox(network.get(), { kind, id });

beforeEach(() => {
  store.resetDemo();
  dayPlanStore.reset();
  carePlanStore.reset();
});

describe('ASHA: her details', () => {
  it('Save updates the profile (months -> weeks, phone) and keeps the rest', () => {
    const before = kavitha();
    expect(store.updateDetails('kavitha', { name: 'Kavitha R.', months: 6, language: 'hi', zoneId: before.zoneId, phone: '9876543210' })).toBe(true);
    const after = kavitha();
    expect([after.months, after.weeksLabel, after.language, after.phone]).toEqual([6, '26w 0d', 'hi', '9876543210']);
    expect(after.reports).toBe(before.reports);
  });

  it('refuses an invalid save (the form shows why)', () => {
    expect(store.updateDetails('kavitha', { name: 'K', months: 6, language: 'kn', zoneId: 'kamthana' })).toBe(false);
    expect(store.updateDetails('kavitha', { name: 'Kavitha', months: 12, language: 'kn', zoneId: 'kamthana' })).toBe(false);
    expect(kavitha().name).toBe('Kavitha R.');
  });

  it('the form: required name, months and village; the phone is optional but must be 10 digits', () => {
    const ok = { name: 'Asha', months: 4, lang: 'kn' as const, zoneId: 'chitta', phone: '' };
    expect(toDetails(ok)).toEqual({ name: 'Asha', months: 4, language: 'kn', zoneId: 'chitta', phone: undefined });
    expect(toDetails({ ...ok, phone: '+91 98765 43210' })?.phone).toBe('9876543210');
    expect(detailsProblems({ ...ok, phone: '12345' }).phone).toBe(true);
    expect(detailsProblems({ ...ok, name: ' ' }).name).toBe(true);
    expect(detailsProblems({ ...ok, months: null }).months).toBe(true);
    expect(toDetails({ ...ok, zoneId: undefined })).toBeNull();
  });
});

describe('ASHA: tell the doctor', () => {
  it("every doctor gets her name, 'Needs care' and the ASHA's note; the case keeps the note", () => {
    store.setEscalation('kavitha', 'Severe headache since morning', ME_ASHA);
    const told = network.alertDoctors('kavitha', 'Kavitha R.', ME_ASHA, 'kamthana', 'Severe headache since morning');
    expect(told).toEqual(['Dr. Anand', 'Dr. Farzana']);
    const n = notices('staff', 'anand')[0];
    expect(n.kind).toBe('high_risk_flag');
    expect(n.title).toBe('Kavitha R. needs care');
    expect(n.body).toContain('Severe headache since morning');
    expect(n.body).toContain('Needs care');
    expect(kavitha().escalation?.note).toBe('Severe headache since morning');
    expect(kavitha().ashaFlag).toBe('needs_care');
  });
});

describe("ASHA: today's visits", () => {
  it('the visited count follows every tick and untick', () => {
    const women = store.get().filter((m) => m.asha === 'Sunita');
    const order = () => dayPlanStore.snapshot().sunita?.order ?? [];
    const visited = () => Object.keys(dayPlanStore.snapshot().sunita?.visited ?? {}).length;
    dayPlanStore.toggleVisited(ME_ASHA, women, 'meena');
    expect(visited()).toBe(1);
    dayPlanStore.toggleVisited(ME_ASHA, women, 'roopa');
    expect(visited()).toBe(2);
    dayPlanStore.toggleVisited(ME_ASHA, women, 'meena');
    expect(visited()).toBe(1);
    expect(order().length).toBeGreaterThanOrEqual(2); // ticking never removes her from the list
  });
});

describe('doctor: advice', () => {
  it('sending advice stores it (who, when) and tells the ASHA and the mother', () => {
    store.sendAdvice('kavitha', ['Rest', 'Come Thursday'], ME_DOCTOR);
    network.adviceSent('kavitha', 'Kavitha R.', 'kamthana', ME_DOCTOR, 2);
    expect(kavitha().advice).toEqual(['Rest', 'Come Thursday']);
    expect(kavitha().adviceBy).toBe(ME_DOCTOR);
    expect(kavitha().adviceAt).toBeGreaterThan(0);
    expect(notices('staff', 'sunita')[0].msg?.title).toBe('aAdviceT');
    expect(notices('mother', 'kavitha')[0].msg?.title).toBe('nAdviceT');
  });
});

describe('doctor: consultation statuses', () => {
  it('requested -> confirmed -> rescheduled -> completed, each told to both', () => {
    const m = kavitha();
    const first = openSlot(0);
    expect(network.doctorSchedule(first.id, m, ME_DOCTOR, 'requested', 'BP check')).toEqual({ ok: true });
    let b = latestBooking('kavitha')!;
    expect([b.status, b.bookedBy, isActive(b)]).toEqual(['requested', 'doctor', true]);
    expect(upcomingBooking('kavitha')?.id).toBe(b.id); // a request already holds the time
    expect(network.get().slots.find((s) => s.id === first.id)?.status).toBe('booked');
    expect(notices('staff', 'sunita')[0].msg?.title).toBe('aApptRequestedT');
    expect(notices('mother', 'kavitha')[0].msg?.title).toBe('nApptRequestedT');

    network.confirmBooking(b.id, m.name); // the ASHA confirms with her
    expect(latestBooking('kavitha')!.status).toBe('confirmed');

    const second = openSlot(0);
    expect(network.rescheduleBooking(b.id, second.id, m.name)).toEqual({ ok: true });
    b = latestBooking('kavitha')!;
    expect([b.status, b.slotId]).toEqual(['rescheduled', second.id]);
    expect(network.get().slots.find((s) => s.id === first.id)?.status).toBe('open'); // the old time opens again
    expect(notices('mother', 'kavitha')[0].msg?.title).toBe('nApptMovedT');

    network.completeBooking(b.id, m.name);
    expect(latestBooking('kavitha')!.status).toBe('completed');
    expect(upcomingBooking('kavitha')).toBeUndefined();
    expect(notices('staff', 'sunita')[0].msg?.title).toBe('aApptDoneT');
  });

  it('the doctor can confirm directly, and cancel (the time opens again)', () => {
    const m = kavitha();
    const slot = openSlot(1);
    network.doctorSchedule(slot.id, m, ME_DOCTOR, 'confirmed');
    const b = latestBooking('kavitha')!;
    expect(b.status).toBe('confirmed');
    network.cancelBooking(b.id, 'doctor', m.name);
    expect(latestBooking('kavitha')!.status).toBe('cancelled');
    expect(network.get().slots.find((s) => s.id === slot.id)?.status).toBe('open');
    expect(notices('mother', 'kavitha')[0].msg?.title).toBe('nApptCancelledT');
  });

  it('refuses a second visit, a taken time, or another doctor’s time', () => {
    const m = kavitha();
    const slot = openSlot(0);
    network.doctorSchedule(slot.id, m, ME_DOCTOR, 'requested');
    expect(network.doctorSchedule(openSlot(0).id, m, ME_DOCTOR, 'requested')).toEqual({ ok: false, reason: 'has_visit' });
    expect(network.doctorSchedule(slot.id, store.get().find((x) => x.id === 'roopa')!, ME_DOCTOR, 'requested')).toEqual({ ok: false, reason: 'taken' });
    const farzana = network.get().slots.find((s) => s.doctorId === 'farzana' && s.status === 'open')!;
    expect(network.doctorSchedule(farzana.id, store.get().find((x) => x.id === 'roopa')!, ME_DOCTOR, 'requested')).toEqual({ ok: false, reason: 'taken' });
  });
});
