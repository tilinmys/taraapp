"""DPDP s.6 consent table (deploy/supabase/20261102_dpdp_consent.sql), run
against a throwaway local PostgreSQL. Skipped when initdb/psql are missing.

Checks: the migration is re-runnable; consent must be stated (no default) by
a named ASHA in a supported language; rows are append-only (no delete, no
edit, one withdrawal that cannot predate the consent); tenant RLS holds.
"""

import os
import shutil
import socket
import subprocess
import tempfile
from pathlib import Path

import pytest

MIGRATION = Path(__file__).resolve().parent.parent / "deploy" / "supabase" / "20261102_dpdp_consent.sql"
pytestmark = pytest.mark.skipif(not (shutil.which("initdb") and shutil.which("psql") and shutil.which("pg_ctl")), reason="PostgreSQL not installed")

# Stand-ins for what Supabase provides: the role and auth.jwt().
SUPABASE_STUBS = """
create schema sakhi; create schema auth; create role authenticated;
create function auth.jwt() returns jsonb language sql stable as
  $$ select coalesce(nullif(current_setting('request.jwt.claims', true), ''), '{}')::jsonb $$;
grant usage on schema sakhi, auth to authenticated;
"""
VALID = "insert into sakhi.patient_consents (patient_id, asha_id, language_code, consent_granted) values ('{p}', 'asha-sunita', 'kn', true)"


@pytest.fixture(scope="module")
def db():
    tmp = tempfile.mkdtemp(prefix="sakhi-consent-pg-")
    data = os.path.join(tmp, "data")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = str(s.getsockname()[1])
    subprocess.run(["initdb", "-D", data, "-U", "postgres", "-A", "trust", "-E", "UTF8"], check=True, capture_output=True)
    # Not captured: the server inherits pg_ctl's stdout, and a captured pipe
    # would never close (the test would wait forever). Output goes to -l.
    subprocess.run(["pg_ctl", "-D", data, "-o", f"-p {port}", "-l", os.path.join(tmp, "log"), "-w", "start"],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def sql(statement=None, file=None, role=None, tenant=None):
        prefix = ""
        if tenant:
            prefix += f"select set_config('request.jwt.claims', '{{\"app_metadata\":{{\"tenant_id\":\"{tenant}\"}}}}', false);\n"
        if role:
            prefix += f"set role {role};\n"
        args = ["psql", "-X", "-q", "-t", "-A", "-p", port, "-U", "postgres", "-v", "ON_ERROR_STOP=1"]
        if file:
            r = subprocess.run(args + ["-1", "-f", str(file)], capture_output=True, text=True, encoding="utf-8")
        else:
            r = subprocess.run(args, input=prefix + statement, capture_output=True, text=True, encoding="utf-8")
        return r.returncode, (r.stdout + r.stderr).strip()

    try:
        assert sql(SUPABASE_STUBS)[0] == 0
        yield sql
    finally:
        subprocess.run(["pg_ctl", "-D", data, "-m", "immediate", "-w", "stop"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        shutil.rmtree(tmp, ignore_errors=True)


def test_migration_applies_and_is_rerunnable(db):
    assert db(file=MIGRATION)[0] == 0
    assert db(file=MIGRATION)[0] == 0
    assert db("grant select, insert, update on sakhi.patient_consents to authenticated;")[0] == 0


def test_valid_consent_gets_purpose_notice_tenant_and_time(db):
    code, out = db(VALID.format(p="m-001") + " returning purpose || '|' || notice_version || '|' || tenant_id || '|' || (granted_at is not null);")
    assert code == 0 and out.endswith("MATERNAL_PREECLAMPSIA_SCREENING|v1.0|mystree|true")


@pytest.mark.parametrize("bad", [
    "insert into sakhi.patient_consents (patient_id, asha_id, language_code) values ('m-2', 'asha-sunita', 'kn')",          # consent not stated
    "insert into sakhi.patient_consents (patient_id, language_code, consent_granted) values ('m-2', 'kn', true)",          # no ASHA named
    "insert into sakhi.patient_consents (patient_id, asha_id, language_code, consent_granted) values ('m-2', 'a', 'ta', true)",  # language
])
def test_invalid_consents_are_refused(db, bad):
    assert db(bad + ";")[0] != 0


def test_records_are_append_only_with_one_withdrawal(db):
    db(VALID.format(p="m-010") + ";")
    assert "append-only" in db("delete from sakhi.patient_consents where patient_id = 'm-010';")[1]
    assert "append-only" in db("update sakhi.patient_consents set purpose = 'MARKETING' where patient_id = 'm-010';")[1]
    assert db("update sakhi.patient_consents set withdrawn_at = granted_at - interval '1 day' where patient_id = 'm-010';")[0] != 0
    assert db("update sakhi.patient_consents set withdrawn_at = clock_timestamp() where patient_id = 'm-010';")[0] == 0
    assert "already withdrawn" in db("update sakhi.patient_consents set withdrawn_at = null where patient_id = 'm-010';")[1]


def test_tenant_isolation(db):
    db(VALID.format(p="m-020") + ";")
    code, out = db("select count(*) from sakhi.patient_consents where patient_id = 'm-020';", role="authenticated", tenant="mystree")
    assert code == 0 and out.splitlines()[-1] == "1"
    code, out = db("select count(*) from sakhi.patient_consents;", role="authenticated", tenant="other")
    assert code == 0 and out.splitlines()[-1] == "0"
    code, out = db(VALID.format(p="m-021") + ";", role="authenticated", tenant="other")
    assert code != 0 and "row-level security" in out
