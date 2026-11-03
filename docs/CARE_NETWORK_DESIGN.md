# tara care network: zones, doctor slots, reminders

System design for how ASHA workers, PHC doctors and pregnant women are
connected in tara. Covers the domain model, the database, the slot-booking
logic, notifications, location-based visibility and the app flows. The demo
app (`app/src/data/care-network.ts`) implements the same model in memory; the
Supabase migration (`deploy/supabase/20261023_care_network.sql`) is the
production schema.

## 1. Principles

1. **The ASHA stays the mother's point of contact.** A mother can book (and
   cancel) her own doctor visit in three taps, and her ASHA can book for her;
   either way the booking carries her zone's ASHA (`bookings.asha_id`) and the
   ASHA is told, so nothing happens behind her back. Reminders and follow-up
   still come from the ASHA. In the mother's Care Circle the ASHA has "Call",
   each doctor has "Book".
2. **Each user gets the one thing they lack.** Doctors lack time: opening a
   normal week is ONE tap ("Open my usual week"), one day is two taps, there
   is no draft / lock step. ASHAs and mothers lack confidence with apps: big
   picture buttons, numbered steps, only real choices shown (days and times
   that are actually free), one button that repeats the choice in words.
3. **Mothers never see risk.** Her screens show her readings as plain
   numbers, all in one calm colour, with no high / low words, red or warning
   icons. Judging them is the ASHA's and the doctor's job (their screens flag
   risk).
4. **Location decides visibility.** Every mother lives in one zone (her
   village / hamlet). Every zone has exactly one active ASHA. A mother sees
   only her zone's ASHA; an ASHA sees only mothers in her zones; a doctor sees
   only his PHC's slots and the mothers booked into them. Enforced in the
   database (row level security), not only in the UI.
5. **One pattern everywhere (human-centred design).** Every list is a card
   with a clay icon, a big title, one status word and an arrow (›) when it
   opens another page. Red = needs care today, green = all well, nothing
   else. The same three-part card is used for zones, mothers, slots and
   reminders, so the ASHA learns it once.
6. **No clinical detail at a glance.** Zone tiles show only a place, a count
   and one status. Details live one tap deeper.

## 2. Actors and what each one does

| Actor | Can | Cannot |
|---|---|---|
| ASHA | add mothers in her zones, run Sakhi check-ins, see every doctor's week (days, hours, free times), book open slots for her mothers, cancel their bookings, send visit reminders | see other zones' mothers, create slots |
| Doctor | save his usual week, open it in one tap (or one day in two), close a time or a whole day, see bookings into his slots, review cases | book on a mother's behalf, see mothers outside his PHC |
| Mother | see her ASHA, her PHC's doctors and their free times, book and cancel her own visit, see her readings and reminders | see other mothers, see ASHAs outside her zone, hold more than one upcoming visit |
| Admin (later) | manage zones, ASHA assignments, doctors, PHCs; see the network dashboard | (scoped by tenant) |

## 3. Domain model

```
Tenant (state / programme)
 └─ PHC (facility)                      e.g. Hosahalli PHC, Bidar taluk
     ├─ Doctor (staff, role=doctor)     works at one or more PHCs
     │    └─ Slot                        one bookable time with this doctor
     │    │    └─ Booking               slot + mother + her zone ASHA + booked_by (asha / mother)
     │    └─ Usual week                 weekdays + session + visit length
     └─ Zone (village / hamlet)         Kamthana, Chitta, Aliyabad, Janawada
          ├─ ASHA (staff, role=asha)    exactly one active per zone
          └─ Mother                     lives in exactly one zone
Notification  (recipient = staff or mother; kind; payload; read_at)
Reminder      (booking + sent_by ASHA + channel + sent_at)
```

## 4. Database (Supabase / Postgres)

New tables in schema `sakhi`, all with `tenant_id` (same convention as the
2026-09-25 migration).

| Table | Key columns | Notes |
|---|---|---|
| `phcs` | id, name, district, taluk | Facility. |
| `zones` | id, phc_id, name, name_kn, name_hi, geo (point), radius_m | A village / hamlet. |
| `staff` | id, auth_user_id, role (`asha`/`doctor`/`admin`), name, phone, phc_id | One row per signed-in worker. |
| `zone_assignments` | zone_id, asha_id, active_from, active_to | Exactly one active ASHA per zone (partial unique index). |
| `mothers` (existing) | + zone_id, + preferred_language, + added_by | Existing table extended; `village` kept for display. |
| `doctor_slots` | id, doctor_id, phc_id, starts_at, ends_at (timestamptz), status (`draft`/`open`/`booked`/`closed`), published_at | **No overlap per doctor** via a GiST exclusion constraint on `tstzrange(starts_at, ends_at)`. |
| `doctor_usual_week` | doctor_id, weekdays smallint[], session (`morning`/`afternoon`/`both`), slot_minutes | The doctor's one-tap week. Only he reads / writes it. |
| `bookings` | id, slot_id, mother_id, asha_id (her zone ASHA), booked_by_kind (`asha`/`mother`), status (`confirmed`/`cancelled`/`completed`/`no_show`), created_at | **One active booking per slot** and **one upcoming active booking per mother** (partial unique indexes). |
| `notifications` | id, recipient_kind, recipient_id, kind, title, body, payload jsonb, created_at, read_at | Outbox: the app reads it; a worker pushes it (FCM / SMS later). |
| `visit_reminders` | id, booking_id, sent_by, channel (`app`/`sms`/`ivr`), sent_at | History of every reminder. |

### Slot logic (the rules that must never break)

* All times stored as `timestamptz`; shown in Asia/Kolkata.
* A doctor opens times with one RPC, `open_slots(days[], session, minutes)`:
  sessions are Morning 10:00-13:00, Afternoon 14:00-17:00 or Full day (both),
  visits 15/20/30 min. Times are created **open** straight away (no draft /
  lock step: doctors asked for one tap). Times in the past, inside the
  30-minute cutoff, or overlapping any live slot of that doctor are skipped
  (the exclusion constraint is the final guard). Every ASHA of the PHC gets
  ONE notification for the whole batch. The app's "Open my usual week" calls
  it with the next 7 days that fall on his saved weekdays.
* `close_day(day)` closes every open, unbooked time of a day (doctor away);
  closing one time is an update to `closed`. A booked slot is only freed by
  cancelling the booking.
* Booking goes through one RPC, `book_slot(slot_id, mother_id)`, callable by
  the mother's zone ASHA **or the signed-in mother for herself**. In one
  transaction it locks the slot row (`SELECT ... FOR UPDATE`), checks it is
  `open`, starts at least 30 minutes from now, the caller may book for this
  mother, and she has no other upcoming active booking; inserts the booking
  with `asha_id` = her zone ASHA and `booked_by_kind`; marks the slot
  `booked`; and notifies the doctor, the mother and (when the mother booked)
  her ASHA. Two people tapping the same time at the same moment: one wins,
  the other gets "just taken" and the list refreshes.
* `cancel_booking(booking_id)` (the mother, her ASHA or an admin) frees the
  slot (back to `open` if still in the future) and notifies the doctor plus
  the mother (ASHA cancelled) or the ASHA (mother cancelled).
* `publish_slots(day)` is kept for drafts created by other tools.

### Location-based visibility (RLS)

* ASHA: `mothers.zone_id in (her active zone_assignments)`.
* Mother (signed in with her phone): her own row; the ASHA of her zone; the
  doctors of her zone's PHC; `open` slots of those doctors; her own bookings.
* Doctor: slots where `doctor_id = me`; bookings into those slots; mothers
  booked with him or referred to his PHC.
* Admin: everything in the tenant.

## 5. Notifications

| Event | Who is told | Text (example) |
|---|---|---|
| Doctor opens times | ASHAs of that PHC (one message per batch) | "Dr. Anand opened 18 visit times · Mon 26, Wed 28, Fri 30 · Morning" |
| ASHA books a slot | Doctor, mother | "Lakshmi D. booked Mon 27 Oct 10:30 by Sunita" |
| Mother books herself | Doctor, mother, her ASHA | "Lakshmi D. booked a doctor visit · Dr. Farzana, Tue 27 Oct 10:20" |
| ASHA cancels | Doctor, mother | "Visit on Mon 10:30 cancelled. Sunita will call you." |
| Mother cancels | Doctor, her ASHA | "Lakshmi D. cancelled her visit · Dr. Anand, Sat 10:00" |
| ASHA reminds | Mother | "Sunita reminds you: Dr. Anand, Mon 27 Oct, 10:30, Hosahalli PHC" |

Delivery: written to `notifications` (the in-app inbox reads it live via
Supabase Realtime); a small worker fans out to FCM push, and SMS for mothers
without a smartphone (later).

## 6. App flows

**ASHA home**
`Your day` → **zone tiles** (2 per row: village, N women, one status, ›) →
Sakhi card → **Doctor visits** card (open slots, ›) → **Doctors this week**
(one card per doctor: his next days, hours and free times, ›) →
**Reminders** card (visits in the next 2 days, ›) → Add pregnant woman →
all mothers.
* Zone page: that village's women only, risk first, one status word each,
  › to her check-in.
* Doctor visits: next 7 days as day chips; open slots as big time tiles;
  tap a time → choose the mother (her zone's women, high risk first) →
  "Book". Booked visits listed below with Cancel; visits a mother booked
  herself say so.
* Reminders: upcoming visits with "Remind" (and "Remind all"), plus when
  each was last reminded.
* Bell: notifications (new slots, booking confirmations).

**Doctor**
Home: three equal tiles (urgent, visits today, referred) → **To do today**
(review urgent cases · see today's mothers · my visit times, which says when
nothing is open) → case queue with three equal filters.
**My visit times**: *Your usual week* (weekday toggles, Morning / Afternoon /
Full day, 15 / 20 / 30 min; saved) with the sticky **Open my usual week**
(1 tap) → *Or change one day*: date strip, then Morning / Afternoon / Full
day opens it (2 taps), **Away this day** closes it → the day's times as
equal tiles (Free with ×, or the mother's name).

**Mother**
Home: weekly story → **Talk to Tara** (the main card) → three equal picture
tiles **My health · Book doctor · Count kicks** → **Today**: her doctor visit
(or "Book a doctor visit"), today's checklist, today's medicines → Care
Circle (ASHA: Call · doctors: Book · 108: Call).
Pregnancy tab: week header → **My health** → Explore care → Count kicks.
**Book a doctor**: 1 choose a doctor (picture, "Next free: Tomorrow") → 2 a
day (only days with free times) → 3 a time (Morning / Afternoon, equal
tiles) → **Book my visit** (repeats the choice in words). Booked: the visit
card, what to bring, Cancel (behind a confirm), Call her ASHA.
**My health**: who checked her and when, BP / Hb / sugar / weight as plain
numbers with last time's value, all one calm colour, Call her ASHA.

## 7. Later: the network dashboard (web, `frontend/`)

PHC and district views on the same tables: zone coverage (mothers per ASHA,
check-ins this month, high-risk count), slot utilisation per doctor, booking
no-shows, reminder delivery, and the ASHA <-> zone assignment editor.

## 8. Rollout

1. Run the migration on staging; seed PHCs, zones, staff, assignments.
2. Point the app's data layer (`care-network.ts`) at Supabase. Every screen
   calls only `network.*`, so this swaps function bodies, not screens:

   | App (`network.`) | Supabase |
   |---|---|
   | `openTimes(doctorId, days, session, minutes)` | `rpc('open_slots', { p_days, p_session, p_minutes })` |
   | `setUsual(doctorId, usual)` | `upsert` into `doctor_usual_week` |
   | `removeSlot(slotId)` | `update doctor_slots set status = 'closed'` (RLS: own slots) |
   | `closeDay(doctorId, day)` | `rpc('close_day', { p_day })` |
   | `bookSlot(slotId, mother, by)` | `rpc('book_slot', { p_slot_id, p_mother_id })` (the caller's role decides ASHA vs mother) |
   | `cancelBooking(bookingId, by)` | `rpc('cancel_booking', { p_booking_id })` |
   | `remindVisit(bookingId)` | `rpc('remind_visit', { p_booking_id })` |
   | `addComment(bookingId, from, byId, text)` | `rpc('add_visit_comment', { p_booking_id, p_body })` (the ASHA's booking note is its first comment) |
   | `store.setFlag(id, flag)` + `alertDoctors(...)` | `rpc('flag_mother', { p_mother_id, p_flag, p_tell_doctors })` (`p_tell_doctors` = the "Tell the doctor?" popup confirmed) |
   | `markRead(to)` | `update notifications set read_at = now()` |
   | `useNetwork()` | selects on `doctor_slots`, `bookings`, `notifications`, `doctor_usual_week` + Realtime |

   `planTimes` and `doctorWeek` stay client-side (pure previews over the
   rows the user can already see).
3. Turn on Realtime for `notifications`, then the push worker.
