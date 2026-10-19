from scripts import supabase_keepalive


class _Cursor:
    def __init__(self):
        self.sql = ""
        self.params = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params):
        self.sql = sql
        self.params = params

    def fetchone(self):
        return (17,)


class _Connection:
    def __init__(self):
        self.cursor_value = _Cursor()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return self.cursor_value


def test_keepalive_upserts_one_explicitly_non_clinical_row(monkeypatch):
    conn = _Connection()

    class _Psycopg:
        @staticmethod
        def connect(dsn, connect_timeout):
            assert dsn == "postgresql://redacted.invalid/postgres"
            assert connect_timeout == 10
            return conn

    monkeypatch.setitem(__import__("sys").modules, "psycopg", _Psycopg)
    count = supabase_keepalive.run_keepalive("postgresql://redacted.invalid/postgres")

    assert count == 17
    assert "ON CONFLICT (id) DO UPDATE" in conn.cursor_value.sql
    assert "maintenance_heartbeats" in conn.cursor_value.sql
    assert conn.cursor_value.params[0] == "SUPABASE_KEEPALIVE_TEST_ONLY"


def test_keepalive_refuses_to_run_without_a_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    try:
        supabase_keepalive.main()
    except SystemExit as exc:
        assert "not configured" in str(exc)
    else:  # pragma: no cover - documents the required fail-closed behavior
        raise AssertionError("keepalive ran without a database connection")
