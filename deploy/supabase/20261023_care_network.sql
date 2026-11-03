-- Sakhi Supabase migration 2026-10-23: the care network.
-- Zones (villages), staff (ASHA / doctor / admin), zone assignments, doctor
-- slots, bookings, notifications and visit reminders. Design and rules:
-- docs/CARE_NETWORK_DESIGN.md.
--
-- ADDITIVE and re-runnable (IF NOT EXISTS everywhere, policies dropped and
-- recreated). Same conventions as 20260925_tenant_rls_vector.sql: schema
-- `sakhi`, tenant_id text with default 'mystree', tenant_isolation policy.
-- Role scoping is added as RESTRICTIVE policies, so it narrows (never widens)
-- what tenant_isolation already allows.

set local lock_timeout = '3s';

create extension if not exists btree_gist with schema extensions;

-- ---------------------------------------------------------------------------
-- Places and people
-- ---------------------------------------------------------------------------

create table if not exists sakhi.phcs (
  id          text primary key,
  tenant_id   text not null default 'mystree',
  name        text not null,
  district    text not null,
  taluk       text,
  created_at  timestamptz not null default now()
);

-- A zone is a village or hamlet: the unit an ASHA is responsible for.
create table if not exists sakhi.zones (
  id          text primary key,
  tenant_id   text not null default 'mystree',
  phc_id      text not null references sakhi.phcs(id),
  name        text not null,
  name_kn     text,
  name_hi     text,
  lat         double precision,
  lng         double precision,
  radius_m    integer,
  created_at  timestamptz not null default now()
);
create index if not exists zones_phc_idx on sakhi.zones (phc_id);

create table if not exists sakhi.staff (
  id            text primary key,
  tenant_id     text not null default 'mystree',
  auth_user_id  uuid unique,                       -- auth.users.id once signed in
  role          text not null check (role in ('asha', 'doctor', 'admin')),
  name          text not null,
  phone         text,
  phc_id        text references sakhi.phcs(id),
  title         text,                              -- e.g. 'Obstetrician'
  active        boolean not null default true,
  created_at    timestamptz not null default now()
);

-- Exactly one ACTIVE ASHA per zone (an ASHA may cover several zones).
create table if not exists sakhi.zone_assignments (
  id           bigint generated always as identity primary key,
  tenant_id    text not null default 'mystree',
  zone_id      text not null references sakhi.zones(id),
  asha_id      text not null references sakhi.staff(id),
  active_from  timestamptz not null default now(),
  active_to    timestamptz
);
create unique index if not exists zone_assignments_one_active
  on sakhi.zone_assignments (zone_id) where active_to is null;
create index if not exists zone_assignments_asha_idx on sakhi.zone_assignments (asha_id) where active_to is null;

-- Mothers (existing table) gain a zone, a language and who added them.
alter table sakhi.mothers add column if not exists zone_id text references sakhi.zones(id);
alter table sakhi.mothers add column if not exists preferred_language text check (preferred_language in ('kn', 'hi', 'en'));
alter table sakhi.mothers add column if not exists added_by text references sakhi.staff(id);
alter table sakhi.mothers add column if not exists auth_user_id uuid unique;
create index if not exists mothers_zone_idx on sakhi.mothers (zone_id);

-- ---------------------------------------------------------------------------
-- Doctor slots and bookings
-- ---------------------------------------------------------------------------

create table if not exists sakhi.doctor_slots (
  id            bigint generated always as identity primary key,
  tenant_id     text not null default 'mystree',
  doctor_id     text not null references sakhi.staff(id),
  phc_id        text not null references sakhi.phcs(id),
  starts_at     timestamptz not null,
  ends_at       timestamptz not null,
  status        text not null default 'draft' check (status in ('draft', 'open', 'booked', 'closed')),
  published_at  timestamptz,
  created_at    timestamptz not null default now(),
  check (ends_at > starts_at),
  check (ends_at - starts_at between interval '10 minutes' and interval '2 hours')
);
-- A doctor can never be in two places at once: no overlapping live slots.
do $$
begin
  if not exists (select 1 from pg_constraint where conname = 'doctor_slots_no_overlap') then
    alter table sakhi.doctor_slots
      add constraint doctor_slots_no_overlap
      exclude using gist (doctor_id with =, tstzrange(starts_at, ends_at) with &&)
      where (status <> 'closed');
  end if;
end
$$;
create index if not exists doctor_slots_open_idx on sakhi.doctor_slots (phc_id, starts_at) where status = 'open';

create table if not exists sakhi.bookings (
  id          bigint generated always as identity primary key,
  tenant_id   text not null default 'mystree',
  slot_id     bigint not null references sakhi.doctor_slots(id),
  mother_id   text not null references sakhi.mothers(patient_id),
  asha_id     text not null references sakhi.staff(id),   -- the mother's zone ASHA (her point of contact), whoever booked
  booked_by_kind text not null default 'asha' check (booked_by_kind in ('asha', 'mother')),
  status      text not null default 'confirmed' check (status in ('confirmed', 'cancelled', 'completed', 'no_show')),
  starts_at   timestamptz not null,                -- copied from the slot: lets the next index work
  created_at  timestamptz not null default now(),
  cancelled_at timestamptz
);
-- One live booking per slot.
create unique index if not exists bookings_one_per_slot
  on sakhi.bookings (slot_id) where status = 'confirmed';
-- One live booking per mother (an ASHA cannot double-book her). The doctor
-- marks a past visit completed / no_show, which frees her for the next one.
create unique index if not exists bookings_one_upcoming_per_mother
  on sakhi.bookings (mother_id) where status = 'confirmed';

-- The doctor's usual OPD week: the app's one-tap "Open my usual week".
create table if not exists sakhi.doctor_usual_week (
  doctor_id     text primary key references sakhi.staff(id),
  tenant_id     text not null default 'mystree',
  weekdays      smallint[] not null default '{1,3,5}',     -- 0 = Sunday
  session       text not null default 'morning' check (session in ('morning', 'afternoon', 'both')),
  slot_minutes  integer not null default 30 check (slot_minutes in (15, 20, 30)),
  updated_at    timestamptz not null default now()
);

create table if not exists sakhi.notifications (
  id              bigint generated always as identity primary key,
  tenant_id       text not null default 'mystree',
  recipient_kind  text not null check (recipient_kind in ('staff', 'mother')),
  recipient_id    text not null,
  kind            text not null check (kind in ('slots_published', 'booking_confirmed', 'booking_cancelled', 'visit_reminder', 'visit_comment', 'high_risk_flag', 'visit_today', 'checklist_updated')),
  title           text not null,
  body            text not null,
  payload         jsonb not null default '{}'::jsonb,
  created_at      timestamptz not null default now(),
  read_at         timestamptz
);
create index if not exists notifications_inbox_idx
  on sakhi.notifications (recipient_kind, recipient_id, created_at desc);

-- The ASHA <-> doctor thread on one booked visit (the booking note and replies).
create table if not exists sakhi.visit_comments (
  id          bigint generated always as identity primary key,
  tenant_id   text not null default 'mystree',
  booking_id  bigint not null references sakhi.bookings(id),
  author_kind text not null check (author_kind in ('asha', 'doctor')),
  author_id   text not null references sakhi.staff(id),
  body        text not null check (length(body) between 1 and 300),
  created_at  timestamptz not null default now()
);
create index if not exists visit_comments_booking_idx on sakhi.visit_comments (booking_id, created_at);

-- Push delivery (docs/NOTIFICATIONS_DESIGN.md): the text as a translation
-- key + values (rendered in the recipient's language), priority, collapse
-- key and delivery state for the push worker.
alter table sakhi.notifications add column if not exists msg_key text;        -- title key, e.g. nVisitTodayT
alter table sakhi.notifications add column if not exists msg_body_key text;   -- body key, e.g. nVisitTodayB
alter table sakhi.notifications add column if not exists msg_params jsonb not null default '{}'::jsonb;
alter table sakhi.notifications add column if not exists priority text not null default 'normal' check (priority in ('high', 'normal', 'low'));
alter table sakhi.notifications add column if not exists collapse_key text;
alter table sakhi.notifications add column if not exists push_status text not null default 'pending'
  check (push_status in ('pending', 'held', 'sent', 'skipped_seen', 'no_device', 'failed'));
alter table sakhi.notifications add column if not exists send_after timestamptz;
alter table sakhi.notifications add column if not exists pushed_at timestamptz;
create index if not exists notifications_push_queue_idx on sakhi.notifications (push_status, send_after) where push_status in ('pending', 'held');

-- One row per phone: the Expo push token of a staff member or a mother.
create table if not exists sakhi.device_tokens (
  id          bigint generated always as identity primary key,
  tenant_id   text not null default 'mystree',
  owner_kind  text not null check (owner_kind in ('staff', 'mother')),
  owner_id    text not null,
  token       text not null unique,
  platform    text not null check (platform in ('android', 'ios', 'web')),
  active      boolean not null default true,
  created_at  timestamptz not null default now(),
  last_seen   timestamptz not null default now()
);
create index if not exists device_tokens_owner_idx on sakhi.device_tokens (owner_kind, owner_id) where active;
alter table sakhi.staff add column if not exists preferred_language text check (preferred_language in ('kn', 'hi', 'en'));

-- The ASHA's plan for one day: who she visits, in which order, who is done.
create table if not exists sakhi.visit_plans (
  id          bigint generated always as identity primary key,
  tenant_id   text not null default 'mystree',
  asha_id     text not null references sakhi.staff(id),
  day         date not null,
  mother_ids  text[] not null default '{}',               -- visiting order
  visited     jsonb not null default '{}'::jsonb,          -- mother id -> visited at
  updated_at  timestamptz not null default now(),
  unique (asha_id, day)
);

-- The ASHA's own call on a woman (the "How is she?" dropdown).
alter table sakhi.mothers add column if not exists asha_flag text check (asha_flag in ('well', 'needs_care'));
alter table sakhi.mothers add column if not exists asha_flag_at timestamptz;

create table if not exists sakhi.visit_reminders (
  id          bigint generated always as identity primary key,
  tenant_id   text not null default 'mystree',
  booking_id  bigint not null references sakhi.bookings(id),
  sent_by     text not null references sakhi.staff(id),
  channel     text not null default 'app' check (channel in ('app', 'sms', 'ivr')),
  sent_at     timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- Who is calling (helpers for RLS and the RPCs)
-- ---------------------------------------------------------------------------

create or replace function sakhi.current_staff()
returns sakhi.staff language sql stable security definer set search_path = sakhi as $$
  select s.* from sakhi.staff s where s.auth_user_id = auth.uid() and s.active limit 1
$$;

create or replace function sakhi.my_zone_ids()
returns setof text language sql stable security definer set search_path = sakhi as $$
  select za.zone_id
  from sakhi.zone_assignments za
  join sakhi.staff s on s.id = za.asha_id
  where s.auth_user_id = auth.uid() and za.active_to is null
$$;

create or replace function sakhi.my_mother_id()
returns text language sql stable security definer set search_path = sakhi as $$
  select m.patient_id from sakhi.mothers m where m.auth_user_id = auth.uid() limit 1
$$;

-- ---------------------------------------------------------------------------
-- Row level security: tenant isolation (permissive) + role scope (restrictive)
-- ---------------------------------------------------------------------------

do $$
declare
  t text;
begin
  foreach t in array array['phcs', 'zones', 'staff', 'zone_assignments', 'doctor_slots', 'doctor_usual_week', 'bookings', 'visit_comments', 'notifications', 'visit_reminders', 'device_tokens', 'visit_plans'] loop
    execute format('create index if not exists %I on sakhi.%I (tenant_id)', t || '_tenant_id_idx', t);
    execute format('alter table sakhi.%I enable row level security', t);
    execute format('drop policy if exists tenant_isolation on sakhi.%I', t);
    execute format(
      $p$create policy tenant_isolation on sakhi.%I
           as permissive for all to authenticated
           using (tenant_id = (select auth.jwt() -> 'app_metadata' ->> 'tenant_id'))
           with check (tenant_id = (select auth.jwt() -> 'app_metadata' ->> 'tenant_id'))$p$,
      t);
  end loop;
end
$$;

-- Mothers: an ASHA sees her zones; a mother sees herself; doctors see mothers
-- booked with them; admins see all.
drop policy if exists location_scope on sakhi.mothers;
create policy location_scope on sakhi.mothers as restrictive for select to authenticated using (
  (select role from sakhi.current_staff()) = 'admin'
  or zone_id in (select sakhi.my_zone_ids())
  or patient_id = (select sakhi.my_mother_id())
  or exists (
    select 1 from sakhi.bookings b join sakhi.doctor_slots d on d.id = b.slot_id
    where b.mother_id = mothers.patient_id and d.doctor_id = (select id from sakhi.current_staff())
  )
);

-- Slots: the doctor sees all of his; everyone else sees only OPEN or BOOKED
-- slots of their own PHC (drafts are private until locked).
drop policy if exists slot_scope on sakhi.doctor_slots;
create policy slot_scope on sakhi.doctor_slots as restrictive for select to authenticated using (
  doctor_id = (select id from sakhi.current_staff())
  or (select role from sakhi.current_staff()) = 'admin'
  or (
    status in ('open', 'booked')
    and phc_id in (
      select z.phc_id from sakhi.zones z
      where z.id in (select sakhi.my_zone_ids())
         or z.id = (select zone_id from sakhi.mothers where patient_id = (select sakhi.my_mother_id()))
    )
  )
);
-- Only the owning doctor writes slots (create / lock / close).
drop policy if exists slot_write on sakhi.doctor_slots;
create policy slot_write on sakhi.doctor_slots as restrictive for all to authenticated
  using (doctor_id = (select id from sakhi.current_staff()))
  with check (doctor_id = (select id from sakhi.current_staff()));

-- Usual week: only the doctor himself reads and writes it.
drop policy if exists usual_week_own on sakhi.doctor_usual_week;
create policy usual_week_own on sakhi.doctor_usual_week as restrictive for all to authenticated
  using (doctor_id = (select id from sakhi.current_staff()))
  with check (doctor_id = (select id from sakhi.current_staff()));

-- Bookings: the booking ASHA, the mother, the slot's doctor, admins. Writes
-- only through the RPCs below (SECURITY DEFINER), so no write policy.
drop policy if exists booking_scope on sakhi.bookings;
create policy booking_scope on sakhi.bookings as restrictive for select to authenticated using (
  asha_id = (select id from sakhi.current_staff())
  or mother_id = (select sakhi.my_mother_id())
  or exists (select 1 from sakhi.doctor_slots d where d.id = bookings.slot_id and d.doctor_id = (select id from sakhi.current_staff()))
  or (select role from sakhi.current_staff()) = 'admin'
);

-- Visit comments: whoever can see the booking can read its thread. Writes
-- only through add_visit_comment (SECURITY DEFINER), so no write policy.
drop policy if exists comment_scope on sakhi.visit_comments;
create policy comment_scope on sakhi.visit_comments as restrictive for select to authenticated using (
  exists (select 1 from sakhi.bookings b where b.id = visit_comments.booking_id)
);

-- Notifications: only your own inbox.
drop policy if exists inbox_scope on sakhi.notifications;
create policy inbox_scope on sakhi.notifications as restrictive for all to authenticated using (
  (recipient_kind = 'staff' and recipient_id = (select id from sakhi.current_staff()))
  or (recipient_kind = 'mother' and recipient_id = (select sakhi.my_mother_id()))
);

-- ---------------------------------------------------------------------------
-- RPCs: every multi-row change is one transaction with its own checks
-- ---------------------------------------------------------------------------

-- Doctor locks (publishes) his draft slots for a day and tells the ASHAs.
create or replace function sakhi.publish_slots(p_day date)
returns integer language plpgsql security definer set search_path = sakhi as $$
declare
  me sakhi.staff;
  n integer;
  first_at timestamptz;
  last_at timestamptz;
begin
  me := sakhi.current_staff();
  if me.role is distinct from 'doctor' then raise exception 'only a doctor can publish slots'; end if;

  update sakhi.doctor_slots
     set status = 'open', published_at = now()
   where doctor_id = me.id and status = 'draft'
     and (starts_at at time zone 'Asia/Kolkata')::date = p_day
     and starts_at > now();
  get diagnostics n = row_count;
  if n = 0 then return 0; end if;

  select min(starts_at), max(ends_at) into first_at, last_at
    from sakhi.doctor_slots
   where doctor_id = me.id and status = 'open' and (starts_at at time zone 'Asia/Kolkata')::date = p_day;

  insert into sakhi.notifications (tenant_id, recipient_kind, recipient_id, kind, title, body, payload)
  select distinct me.tenant_id, 'staff', za.asha_id, 'slots_published',
         me.name || ' opened ' || n || ' visit slots',
         to_char(first_at at time zone 'Asia/Kolkata', 'Dy DD Mon, HH24:MI') || ' - ' ||
         to_char(last_at at time zone 'Asia/Kolkata', 'HH24:MI'),
         jsonb_build_object('doctor_id', me.id, 'day', p_day)
    from sakhi.zones z
    join sakhi.zone_assignments za on za.zone_id = z.id and za.active_to is null
   where z.phc_id = me.phc_id;
  return n;
end
$$;

-- Doctor OPENS a session on one or more days in one call (the app's one-tap
-- "Open my usual week" and the two-tap "open one day"): slots are created
-- already open, with no draft / lock step. Times in the past, inside the
-- 30-minute booking cutoff, or overlapping his live slots are skipped.
-- Every ASHA of his PHC gets ONE notification for the lot. Returns how many
-- opened. (publish_slots stays for drafts made by other tools.)
create or replace function sakhi.open_slots(p_days date[], p_session text, p_minutes integer)
returns integer language plpgsql security definer set search_path = sakhi as $$
declare
  me sakhi.staff;
  n integer;
  windows int4range[];
begin
  me := sakhi.current_staff();
  if me.role is distinct from 'doctor' then raise exception 'only a doctor can open slots'; end if;
  if p_minutes not in (15, 20, 30) then raise exception 'visit length must be 15, 20 or 30 minutes'; end if;
  -- Minutes from midnight: morning 10:00-13:00, afternoon 14:00-17:00.
  windows := case p_session
    when 'morning' then array[int4range(600, 780)]
    when 'afternoon' then array[int4range(840, 1020)]
    when 'both' then array[int4range(600, 780), int4range(840, 1020)]
  end;
  if windows is null then raise exception 'unknown session %', p_session; end if;

  with candidates as (
    select (d::timestamp + make_interval(mins => t)) at time zone 'Asia/Kolkata' as starts_at
      from unnest(p_days) d
      cross join unnest(windows) w
      cross join lateral generate_series(lower(w), upper(w) - p_minutes, p_minutes) t
  ), fresh as (
    select c.starts_at from candidates c
     where c.starts_at > now() + interval '30 minutes'
       and not exists (
         select 1 from sakhi.doctor_slots x
          where x.doctor_id = me.id and x.status <> 'closed'
            and tstzrange(x.starts_at, x.ends_at) && tstzrange(c.starts_at, c.starts_at + make_interval(mins => p_minutes))
       )
  )
  insert into sakhi.doctor_slots (tenant_id, doctor_id, phc_id, starts_at, ends_at, status, published_at)
  select me.tenant_id, me.id, me.phc_id, f.starts_at, f.starts_at + make_interval(mins => p_minutes), 'open', now()
    from fresh f;
  get diagnostics n = row_count;
  if n = 0 then return 0; end if;

  insert into sakhi.notifications (tenant_id, recipient_kind, recipient_id, kind, title, body, payload)
  select distinct me.tenant_id, 'staff', za.asha_id, 'slots_published',
         me.name || ' opened ' || n || ' visit times',
         (select string_agg(to_char(d, 'Dy DD Mon'), ', ' order by d) from unnest(p_days) d) || ' · ' || p_session,
         jsonb_build_object('doctor_id', me.id, 'days', p_days, 'session', p_session)
    from sakhi.zones z
    join sakhi.zone_assignments za on za.zone_id = z.id and za.active_to is null
   where z.phc_id = me.phc_id;
  return n;
end
$$;

-- Doctor is away: close every OPEN slot of the day. Booked visits stay.
create or replace function sakhi.close_day(p_day date)
returns integer language plpgsql security definer set search_path = sakhi as $$
declare
  me sakhi.staff;
  n integer;
begin
  me := sakhi.current_staff();
  if me.role is distinct from 'doctor' then raise exception 'only a doctor can close slots'; end if;
  update sakhi.doctor_slots set status = 'closed'
   where doctor_id = me.id and status = 'open' and (starts_at at time zone 'Asia/Kolkata')::date = p_day;
  get diagnostics n = row_count;
  return n;
end
$$;

-- Book an open slot: by the mother's zone ASHA, or by the mother herself
-- (signed in, booking for herself). Race-safe: the slot row is locked, and
-- the partial unique indexes are the final guard. bookings.asha_id is always
-- her zone's ASHA (her point of contact); when the mother books, that ASHA
-- is notified too.
create or replace function sakhi.book_slot(p_slot_id bigint, p_mother_id text)
returns bigint language plpgsql security definer set search_path = sakhi as $$
declare
  me sakhi.staff;
  by_mother boolean;
  zone_asha sakhi.staff;
  s sakhi.doctor_slots;
  booking_id bigint;
begin
  me := sakhi.current_staff();
  by_mother := me.id is null and p_mother_id = (select sakhi.my_mother_id());
  if not by_mother and me.role is distinct from 'asha' then raise exception 'only the mother or her ASHA can book'; end if;

  if not by_mother and not exists (
    select 1 from sakhi.mothers m
    where m.patient_id = p_mother_id and m.zone_id in (select sakhi.my_zone_ids())
  ) then
    raise exception 'this mother is not in your zones';
  end if;

  select st.* into zone_asha
    from sakhi.mothers m
    join sakhi.zone_assignments za on za.zone_id = m.zone_id and za.active_to is null
    join sakhi.staff st on st.id = za.asha_id
   where m.patient_id = p_mother_id
   limit 1;
  if zone_asha.id is null then raise exception 'this mother has no ASHA for her village'; end if;

  select * into s from sakhi.doctor_slots where id = p_slot_id for update;
  if s.id is null or s.status <> 'open' then raise exception 'slot just taken'; end if;
  if s.starts_at < now() + interval '30 minutes' then raise exception 'slot starts too soon'; end if;
  if exists (select 1 from sakhi.bookings where mother_id = p_mother_id and status = 'confirmed' and starts_at > now()) then
    raise exception 'mother already has an upcoming visit';
  end if;

  insert into sakhi.bookings (tenant_id, slot_id, mother_id, asha_id, booked_by_kind, starts_at)
  values (zone_asha.tenant_id, s.id, p_mother_id, zone_asha.id, case when by_mother then 'mother' else 'asha' end, s.starts_at)
  returning id into booking_id;
  update sakhi.doctor_slots set status = 'booked' where id = s.id;

  insert into sakhi.notifications (tenant_id, recipient_kind, recipient_id, kind, title, body, payload) values
    (zone_asha.tenant_id, 'staff', s.doctor_id, 'booking_confirmed', 'New visit booked',
     to_char(s.starts_at at time zone 'Asia/Kolkata', 'Dy DD Mon, HH24:MI') ||
       case when by_mother then ' · booked by the mother' else ' · by ' || zone_asha.name end,
     jsonb_build_object('booking_id', booking_id, 'mother_id', p_mother_id)),
    (zone_asha.tenant_id, 'mother', p_mother_id, 'booking_confirmed', 'Your doctor visit is booked',
     to_char(s.starts_at at time zone 'Asia/Kolkata', 'Dy DD Mon, HH24:MI') || ' · ' || zone_asha.name ||
       case when by_mother then ' knows' else ' booked it for you' end,
     jsonb_build_object('booking_id', booking_id));
  if by_mother then
    -- The ASHA stays her point of contact: tell her (the app shows the mother's name from mother_id).
    insert into sakhi.notifications (tenant_id, recipient_kind, recipient_id, kind, title, body, payload) values
      (zone_asha.tenant_id, 'staff', zone_asha.id, 'booking_confirmed', 'A mother booked a doctor visit',
       to_char(s.starts_at at time zone 'Asia/Kolkata', 'Dy DD Mon, HH24:MI'),
       jsonb_build_object('booking_id', booking_id, 'mother_id', p_mother_id));
  end if;
  return booking_id;
end
$$;

create or replace function sakhi.cancel_booking(p_booking_id bigint)
returns void language plpgsql security definer set search_path = sakhi as $$
declare
  me sakhi.staff;
  b sakhi.bookings;
  s sakhi.doctor_slots;
begin
  me := sakhi.current_staff();
  select * into b from sakhi.bookings where id = p_booking_id for update;
  if b.id is null or b.status <> 'confirmed' then return; end if;
  -- The mother may cancel her own visit; her ASHA and admins may cancel theirs.
  if b.mother_id is distinct from (select sakhi.my_mother_id())
     and b.asha_id is distinct from me.id and me.role is distinct from 'admin' then
    raise exception 'not your booking';
  end if;

  update sakhi.bookings set status = 'cancelled', cancelled_at = now() where id = b.id;
  select * into s from sakhi.doctor_slots where id = b.slot_id for update;
  update sakhi.doctor_slots set status = case when s.starts_at > now() then 'open' else 'closed' end where id = s.id;

  insert into sakhi.notifications (tenant_id, recipient_kind, recipient_id, kind, title, body, payload) values
    (b.tenant_id, 'staff', s.doctor_id, 'booking_cancelled', 'Visit cancelled',
     to_char(s.starts_at at time zone 'Asia/Kolkata', 'Dy DD Mon, HH24:MI'), jsonb_build_object('booking_id', b.id));
  if me.id is null then
    -- The mother cancelled: tell her ASHA.
    insert into sakhi.notifications (tenant_id, recipient_kind, recipient_id, kind, title, body, payload) values
      (b.tenant_id, 'staff', b.asha_id, 'booking_cancelled', 'A mother cancelled her visit',
       to_char(s.starts_at at time zone 'Asia/Kolkata', 'Dy DD Mon, HH24:MI'), jsonb_build_object('booking_id', b.id, 'mother_id', b.mother_id));
  else
    insert into sakhi.notifications (tenant_id, recipient_kind, recipient_id, kind, title, body, payload) values
      (b.tenant_id, 'mother', b.mother_id, 'booking_cancelled', 'Your visit was cancelled',
       me.name || ' will call you with a new time', jsonb_build_object('booking_id', b.id));
  end if;
end
$$;

create or replace function sakhi.remind_visit(p_booking_id bigint, p_channel text default 'app')
returns void language plpgsql security definer set search_path = sakhi as $$
declare
  me sakhi.staff;
  b sakhi.bookings;
  doc text;
begin
  me := sakhi.current_staff();
  select * into b from sakhi.bookings where id = p_booking_id;
  if b.id is null or b.status <> 'confirmed' then raise exception 'no such upcoming visit'; end if;
  if b.asha_id <> me.id then raise exception 'not your booking'; end if;
  select st.name into doc from sakhi.doctor_slots d join sakhi.staff st on st.id = d.doctor_id where d.id = b.slot_id;

  insert into sakhi.visit_reminders (tenant_id, booking_id, sent_by, channel) values (me.tenant_id, b.id, me.id, p_channel);
  insert into sakhi.notifications (tenant_id, recipient_kind, recipient_id, kind, title, body, payload) values
    (me.tenant_id, 'mother', b.mother_id, 'visit_reminder', me.name || ' reminds you of your visit',
     doc || ' · ' || to_char(b.starts_at at time zone 'Asia/Kolkata', 'Dy DD Mon, HH24:MI'),
     jsonb_build_object('booking_id', b.id));
end
$$;

-- ASHA or doctor writes on a booked visit's thread; the other side is told.
create or replace function sakhi.add_visit_comment(p_booking_id bigint, p_body text)
returns bigint language plpgsql security definer set search_path = sakhi as $$
declare
  me sakhi.staff;
  b sakhi.bookings;
  s sakhi.doctor_slots;
  c_id bigint;
  to_id text;
begin
  me := sakhi.current_staff();
  select * into b from sakhi.bookings where id = p_booking_id;
  if b.id is null or b.status <> 'confirmed' then raise exception 'no such upcoming visit'; end if;
  select * into s from sakhi.doctor_slots where id = b.slot_id;
  if me.id is distinct from b.asha_id and me.id is distinct from s.doctor_id then raise exception 'not your visit'; end if;
  if length(trim(coalesce(p_body, ''))) = 0 then raise exception 'empty message'; end if;

  insert into sakhi.visit_comments (tenant_id, booking_id, author_kind, author_id, body)
  values (b.tenant_id, b.id, me.role, me.id, left(trim(p_body), 300))
  returning id into c_id;
  to_id := case when me.id = b.asha_id then s.doctor_id else b.asha_id end;
  insert into sakhi.notifications (tenant_id, recipient_kind, recipient_id, kind, title, body, payload) values
    (b.tenant_id, 'staff', to_id, 'visit_comment', me.name || ' about a visit',
     left(trim(p_body), 120) || ' · ' || to_char(s.starts_at at time zone 'Asia/Kolkata', 'Dy DD Mon, HH24:MI'),
     jsonb_build_object('booking_id', b.id, 'mother_id', b.mother_id));
  return c_id;
end
$$;

-- ASHA marks a woman of her zones Well / Needs care. With p_tell_doctors
-- (the "Tell the doctor?" popup, confirmed) every doctor of that PHC gets a
-- high-risk alert. Returns how many doctors were told.
create or replace function sakhi.flag_mother(p_mother_id text, p_flag text, p_tell_doctors boolean default false)
returns integer language plpgsql security definer set search_path = sakhi as $$
declare
  me sakhi.staff;
  m_zone text;
  n integer := 0;
begin
  me := sakhi.current_staff();
  if me.role is distinct from 'asha' then raise exception 'only an ASHA can flag'; end if;
  if p_flag not in ('well', 'needs_care') then raise exception 'flag must be well or needs_care'; end if;
  select zone_id into m_zone from sakhi.mothers where patient_id = p_mother_id and zone_id in (select sakhi.my_zone_ids());
  if m_zone is null then raise exception 'this mother is not in your zones'; end if;

  update sakhi.mothers set asha_flag = p_flag, asha_flag_at = now() where patient_id = p_mother_id;
  if p_flag = 'needs_care' and p_tell_doctors then
    insert into sakhi.notifications (tenant_id, recipient_kind, recipient_id, kind, title, body, payload)
    select me.tenant_id, 'staff', st.id, 'high_risk_flag', 'A mother needs care',
           me.name || ' marked her high risk · ' || z.name,
           jsonb_build_object('mother_id', p_mother_id)
      from sakhi.zones z
      join sakhi.staff st on st.phc_id = z.phc_id and st.role = 'doctor' and st.active
     where z.id = m_zone;
    get diagnostics n = row_count;
  end if;
  return n;
end
$$;

-- Device tokens: you see and manage only your own phone's token.
drop policy if exists device_own on sakhi.device_tokens;
create policy device_own on sakhi.device_tokens as restrictive for all to authenticated using (
  (owner_kind = 'staff' and owner_id = (select id from sakhi.current_staff()))
  or (owner_kind = 'mother' and owner_id = (select sakhi.my_mother_id()))
) with check (
  (owner_kind = 'staff' and owner_id = (select id from sakhi.current_staff()))
  or (owner_kind = 'mother' and owner_id = (select sakhi.my_mother_id()))
);
-- Visit plans: only the ASHA herself (writes through save_visit_plan).
drop policy if exists plan_own on sakhi.visit_plans;
create policy plan_own on sakhi.visit_plans as restrictive for select to authenticated using (
  asha_id = (select id from sakhi.current_staff())
);

-- ASHA saves today's plan (order + visited). Only women of her zones are
-- kept; women ADDED since the last save get a visit_today notification.
create or replace function sakhi.save_visit_plan(p_day date, p_mother_ids text[], p_visited jsonb default '{}'::jsonb)
returns integer language plpgsql security definer set search_path = sakhi as $$
declare
  me sakhi.staff;
  kept text[];
  before text[];
  n integer := 0;
begin
  me := sakhi.current_staff();
  if me.role is distinct from 'asha' then raise exception 'only an ASHA plans visits'; end if;
  select coalesce(array_agg(x order by o), '{}') into kept
    from unnest(p_mother_ids) with ordinality as u(x, o)
   where exists (select 1 from sakhi.mothers m where m.patient_id = u.x and m.zone_id in (select sakhi.my_zone_ids()));
  select mother_ids into before from sakhi.visit_plans where asha_id = me.id and day = p_day;

  insert into sakhi.visit_plans (tenant_id, asha_id, day, mother_ids, visited, updated_at)
  values (me.tenant_id, me.id, p_day, kept, coalesce(p_visited, '{}'::jsonb), now())
  on conflict (asha_id, day) do update set mother_ids = excluded.mother_ids, visited = excluded.visited, updated_at = now();

  if p_day = (now() at time zone 'Asia/Kolkata')::date then
    insert into sakhi.notifications (tenant_id, recipient_kind, recipient_id, kind, title, body, msg_key, msg_body_key, msg_params, priority, payload)
    select me.tenant_id, 'mother', x, 'visit_today', me.name || ' will visit you today', 'Keep your MCP card ready',
           'nVisitTodayT', 'nVisitTodayB', jsonb_build_object('asha', me.name), 'high', '{}'::jsonb
      from unnest(kept) x
     where not (x = any (coalesce(before, '{}')));
    get diagnostics n = row_count;
  end if;
  return n;
end
$$;

revoke all on function sakhi.save_visit_plan(date, text[], jsonb) from public;
grant execute on function sakhi.save_visit_plan(date, text[], jsonb) to authenticated;

revoke all on function sakhi.add_visit_comment(bigint, text), sakhi.flag_mother(text, text, boolean) from public;
grant execute on function sakhi.add_visit_comment(bigint, text), sakhi.flag_mother(text, text, boolean) to authenticated;

revoke all on function sakhi.publish_slots(date), sakhi.open_slots(date[], text, integer), sakhi.close_day(date),
  sakhi.book_slot(bigint, text), sakhi.cancel_booking(bigint), sakhi.remind_visit(bigint, text) from public;
grant execute on function sakhi.publish_slots(date), sakhi.open_slots(date[], text, integer), sakhi.close_day(date),
  sakhi.book_slot(bigint, text), sakhi.cancel_booking(bigint), sakhi.remind_visit(bigint, text) to authenticated;
