-- Sakhi Supabase migration 2026-11-02: DPDP consent artifacts.
-- India DPDP Act 2023 s.6 (and the 2025 Rules): consent must be free,
-- specific, informed and affirmative, and the Data Fiduciary must be able to
-- PROVE it - who took it, for what purpose, in which language, against which
-- version of the notice, and when (and when it was withdrawn).
--
-- ADDITIVE and re-runnable, same conventions as 20260925_tenant_rls_vector.sql
-- and 20261023_care_network.sql: schema `sakhi`, tenant_id text default
-- 'mystree', tenant_isolation RLS policy (dropped and recreated).
--
-- Deliberate choices:
--   * consent_granted and asha_id have NO default: consent must be recorded
--     affirmatively, by a named ASHA. A default would let a forgotten field
--     become "granted by asha-field-01" - not a verifiable artifact.
--   * no foreign key to mothers: consent is taken BEFORE the mother's record
--     exists (nothing may be collected before it).
--   * append-only: rows cannot be deleted, and the only change allowed is
--     recording a withdrawal once (withdrawn_at, from empty to a time not
--     before granted_at). A new consent (e.g. a new notice version) is a new
--     row. The table is the audit trail, so it must not be editable.

set local lock_timeout = '3s';   -- never queue a live call's writes behind this

create table if not exists sakhi.patient_consents (
  consent_id       uuid primary key default gen_random_uuid(),
  patient_id       text not null,
  asha_id          text not null,
  purpose          text not null default 'MATERNAL_PREECLAMPSIA_SCREENING',
  language_code    text not null check (language_code in ('kn', 'hi', 'en')),
  notice_version   text not null default 'v1.0',
  consent_granted  boolean not null,
  granted_at       timestamptz not null default clock_timestamp(),
  withdrawn_at     timestamptz,
  tenant_id        text not null default 'mystree',
  constraint patient_consents_withdrawn_after_granted
    check (withdrawn_at is null or withdrawn_at >= granted_at)
);

create index if not exists idx_consent_patient on sakhi.patient_consents (patient_id);
create index if not exists patient_consents_tenant_id_idx on sakhi.patient_consents (tenant_id);

-- ---------------------------------------------------------------------------
-- Append-only: no deletes, no edits except a one-time withdrawal.
-- ---------------------------------------------------------------------------

create or replace function sakhi.patient_consents_append_only()
returns trigger language plpgsql set search_path = sakhi as $$
begin
  if tg_op = 'DELETE' then
    raise exception 'consent records are append-only: record a withdrawal instead of deleting'
      using errcode = 'insufficient_privilege';
  end if;
  -- UPDATE: only withdrawn_at may change, and only from empty to a time.
  if old.withdrawn_at is not null then
    raise exception 'consent % was already withdrawn at %', old.consent_id, old.withdrawn_at
      using errcode = 'insufficient_privilege';
  end if;
  if new.withdrawn_at is null
     or (new.consent_id, new.patient_id, new.asha_id, new.purpose, new.language_code,
         new.notice_version, new.consent_granted, new.granted_at, new.tenant_id)
        is distinct from
        (old.consent_id, old.patient_id, old.asha_id, old.purpose, old.language_code,
         old.notice_version, old.consent_granted, old.granted_at, old.tenant_id) then
    raise exception 'consent records are append-only: only withdrawn_at may be set, once'
      using errcode = 'insufficient_privilege';
  end if;
  return new;
end;
$$;

drop trigger if exists patient_consents_append_only on sakhi.patient_consents;
create trigger patient_consents_append_only
  before update or delete on sakhi.patient_consents
  for each row execute function sakhi.patient_consents_append_only();

-- ---------------------------------------------------------------------------
-- Tenant isolation (same policy as every other sakhi table)
-- ---------------------------------------------------------------------------

alter table sakhi.patient_consents enable row level security;
drop policy if exists tenant_isolation on sakhi.patient_consents;
create policy tenant_isolation on sakhi.patient_consents
  as permissive for all to authenticated
  using (tenant_id = (select auth.jwt() -> 'app_metadata' ->> 'tenant_id'))
  with check (tenant_id = (select auth.jwt() -> 'app_metadata' ->> 'tenant_id'));
