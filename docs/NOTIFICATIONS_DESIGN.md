# tara notifications: live in the app, push on the phone

How the ASHA, the mother and the doctor learn **what is happening, as it
happens**: a visit booked, a reminder, "Sunita will visit you today", a
doctor's reply, a high-risk alert. Covers the event model, the push
algorithm, the client state and the database. The demo app implements the
in-app part (`data/care-network.ts`, `components/system/live-banner.tsx`);
the push worker is specified here for the backend developer.

## 1. Principles

1. **One event, one row.** Every change that someone should hear about writes
   exactly one row per recipient into `sakhi.notifications`, in the SAME
   transaction as the change (the RPCs already do this). No row, no message;
   no double messages.
2. **Her language, not ours.** A notification stores a translation key and
   its values (`msg_key` for the title, `msg_body_key`, `msg_params`), never only English. The app and the
   push worker render it in the recipient's language (Kannada / Hindi /
   English) with the same templates (`i18n/strings.ts`, keys `n*` for
   mothers, `a*` for ASHAs). A time (`when`) is written the local way
   ("ನಾಳೆ, 10:00", "कल, 10:00").
3. **Calm by default.** Only what needs action today is loud. Everything
   else waits for a sensible hour or is grouped.
4. **Tap = the right page.** Every notification carries a reference (booking,
   mother) and opens the page where the person acts on it.

## 2. Events and who hears them

| Event (kind) | Recipient | Priority | Opens |
|---|---|---|---|
| `high_risk_flag` ASHA marks "needs care" + tells doctor | doctors of the PHC | **high** | the case |
| `booking_cancelled` | mother / ASHA / doctor (the others) | **high** | visit page |
| `visit_today` ASHA puts her on today's visits | mother | **high** (morning) | Notifications |
| `visit_reminder` ASHA reminds | mother | normal | visit page |
| `booking_confirmed` | doctor, mother, ASHA (if the mother booked) | normal | visit page / her page |
| `visit_comment` ASHA <-> doctor message | the other side | normal | the thread |
| `slots_published` doctor opened times | ASHAs of the PHC | low (grouped) | Doctor visits |
| `checklist_updated` ASHA changed her tasks | mother | low (grouped) | Home |

## 3. Push algorithm (worker)

A small worker (Supabase Edge Function on a database webhook for INSERT on
`notifications`, plus a 1-minute cron for held rows) runs, for each new row:

```
on notification N (recipient R):
  1  devices  = device_tokens where owner = R and active
     if none: if N.priority = high -> SMS fallback (step 8); mark 'no_device'; stop
  2  lang     = R.preferred_language (mother: mothers.preferred_language,
                staff: staff.preferred_language), default 'kn'
     text     = render(N.msg_key, N.msg_params, lang)          # same templates as the app
  3  if N.read_at is set (already seen live in the app): mark 'skipped_seen'; stop
  4  quiet hours 21:00-07:00 IST:
       high   -> send now
       normal -> hold until 07:00 (status 'held', send_after = 07:00)
       low    -> hold until 09:00
  5  collapse: key = (R, N.kind, N.ref or day). If another unsent row with the
     same key exists in the last 10 min, merge (count++) and send ONE push:
       e.g. 3 x slots_published -> "Doctors opened 24 visit times"
  6  rate limit for low priority: at most 1 push per recipient per 30 min;
     the rest wait and go out as one digest
  7  send through the Expo Push API (FCM + APNs in one call):
       { to: tokens, title, body, sound: high ? 'default' : null,
         priority: high ? 'high' : 'normal', channelId: N.priority,
         data: { route, ref } }      # route = where a tap opens (section 2)
     store ticket ids; status 'sent'; pushed_at = now()
  8  receipts (cron, 15 min later): DeviceNotRegistered -> deactivate token;
     high priority still not delivered -> SMS (Kannada / Hindi text, the
     ASHA's or PHC's number to call); later IVR voice call for mothers who
     cannot read
```

Android notification channels: `high` (sound, heads-up), `normal`, `low`
(silent). The user can mute a channel in phone settings; we never re-enable it.

## 4. App state (how "live" works)

```
Supabase Realtime (notifications where recipient = me)
        |  insert / update
        v
network store (useSyncExternalStore)  <-- RPC results (my own actions)
        |  derived, never stored twice
        +--> unread count (bell badge)      = notices where !read
        +--> Notifications page             = notices sorted by created_at desc
        +--> LiveBanner                     = newest unread created after the
                                              workspace opened (5 s, tap to open)
```

* **Single source of truth:** the notices list. Badges and banners are
  derived from it, so they can never disagree.
* **Read:** leaving the Notifications page marks them read
  (`update notifications set read_at = now()`); the badge clears everywhere.
* **Offline:** the list is cached on the phone; on reconnect the app fetches
  `created_at > last_seen` (cursor) and merges by id (no duplicates).
* **Push tap:** `data.route` + `data.ref` deep-link straight to the page
  (`/asha/messages`, `/mother/notifications`, `/doctor/case/[id]`).
* **Seen live = no push:** if the app is open and the row is read before the
  worker runs (step 3), the phone is not buzzed.

## 5. Database (in `deploy/supabase/20261023_care_network.sql`)

* `notifications` + `msg_key`, `msg_body_key`, `msg_params jsonb`, `priority`, `collapse_key`,
  `push_status` (`pending`/`held`/`sent`/`skipped_seen`/`no_device`/`failed`),
  `send_after`, `pushed_at`.
* `device_tokens` (owner kind + id, Expo push token, platform, active).
* `staff.preferred_language` (mothers already have `preferred_language`).
* `visit_plans` (ASHA's ordered list of today's visits; see below) and RPCs
  `save_visit_plan`, `notify_mother`.

## 6. Today's visits (the ASHA's plan)

One row per ASHA per day: `mother_ids text[]` in visiting order and `visited
jsonb` (mother id -> time). The app suggests the order (needs care, not yet
checked, doctor visit within 2 days, not seen for a week); the ASHA moves
women up / down, adds or ticks "Visited". Adding a woman sends her a
`visit_today` notification ("Sunita will visit you today. Keep your MCP card
ready.").
