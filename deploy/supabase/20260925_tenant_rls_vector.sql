-- Sakhi Supabase migration 2026-09-25: pgvector + tenant_id + tenant RLS.
-- Project: Sakhi (xvmbvcnmeqlirnacpxkb), schema `sakhi`.
--
-- ADDITIVE and re-runnable. It never drops or rewrites a table:
--   * the worker's own bootstrap (sakhi_schema.schema_statements) keeps
--     creating the tables with CREATE TABLE IF NOT EXISTS;
--   * tenant_id gets a DEFAULT, so every existing INSERT keeps working
--     without naming it;
--   * the backend connects as `sakhi_runtime`, which OWNS these tables, so
--     RLS does not apply to it (no FORCE ROW LEVEL SECURITY) - live calls
--     and the dashboard are unaffected.
--
-- Access model: the `sakhi` schema is NOT exposed through the Data API and
-- anon/authenticated/service_role have no grants on it, so RLS is default-deny
-- defence in depth. The tenant policies below only matter if the schema is
-- ever exposed to signed-in users; the tenant comes from app_metadata (set
-- server-side), never user_metadata (user-editable).

set local lock_timeout = '3s';   -- never queue a live call's writes behind this

create extension if not exists vector with schema extensions;

do $$
declare
  t text;
begin
  foreach t in array array[
    'mothers', 'bp_records', 'symptom_records', 'teleconsults',
    'call_reports', 'call_turns', 'pipeline_events', 'component_health',
    'maintenance_heartbeats'
  ] loop
    execute format(
      'alter table sakhi.%I add column if not exists tenant_id text not null default %L',
      t, 'mystree');
    execute format(
      'create index if not exists %I on sakhi.%I (tenant_id)', t || '_tenant_id_idx', t);
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
