"""Low-cost keepalive for the Sakhi Supabase project.

The free Supabase project may pause after sustained inactivity. The existing
EC2 worker is already running continuously, so a systemd timer can issue one
small database write every five hours without adding Lambda, EventBridge, or
another paid service. The row lives in its own operational table and is
explicitly labelled as test-only; it never enters a mother, call, or clinical
telemetry table.

No connection string or credential is ever printed.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone


PURPOSE = "SUPABASE_KEEPALIVE_TEST_ONLY"


def run_keepalive(dsn: str) -> int:
    """Upsert the single non-clinical keepalive row and return its run count."""
    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover - deployment dependency check
        raise RuntimeError("psycopg[binary,pool] is required") from exc

    now = datetime.now(timezone.utc).isoformat()
    with psycopg.connect(dsn, connect_timeout=10) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO maintenance_heartbeats
                    (id, purpose, last_seen_at, run_count)
                VALUES (1, %s, %s, 1)
                ON CONFLICT (id) DO UPDATE SET
                    purpose = EXCLUDED.purpose,
                    last_seen_at = EXCLUDED.last_seen_at,
                    run_count = maintenance_heartbeats.run_count + 1
                RETURNING run_count
                """,
                (PURPOSE, now),
            )
            return int(cur.fetchone()[0])


def main() -> int:
    dsn = os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL/POSTGRES_URL is not configured")
    count = run_keepalive(dsn)
    print(f"Sakhi Supabase keepalive succeeded (test-only row, run_count={count})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
