"""Move mock_sixtydb.json into the SQLite store.

    python migrate_to_sqlite.py [--source mock_sixtydb.json] [--db data/sakhi.sqlite3]

Idempotent: a mother already present (matched on patient_id) is skipped rather
than duplicated, so re-running after a partial failure is safe.

Junk rows are reported, not silently imported. The live JSON file accumulated
placeholder mothers named "test" during development, and importing those into
the pilot database would put fake patients in front of an ASHA worker.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from sakhi_domain import BloodPressure, RiskLevel, SymptomSet
from sakhi_sql_repository import SQLitePatientRepository, _utc_now
from sakhi_triage import TRIAGE

JUNK_NAMES = {"test", "testing", "aaa", "xxx", ""}


async def migrate(source: Path, db_path: Path, include_junk: bool = False) -> int:
    raw = json.loads(source.read_text(encoding="utf-8"))
    repo = await SQLitePatientRepository(db_path).load()

    imported = skipped_existing = skipped_junk = readings = 0

    for patient in raw.get("patients", []):
        pid = str(patient.get("patient_id") or "").strip()
        name = str(patient.get("name") or "").strip()
        if not pid:
            continue
        if not include_junk and name.lower() in JUNK_NAMES:
            print(f"  SKIP junk  {pid} name={name!r}")
            skipped_junk += 1
            continue
        if await repo.get_patient(pid):
            skipped_existing += 1
            continue

        # Insert with the ORIGINAL id rather than create_patient(), which
        # allocates a new one - the id is referenced by existing bp_records.
        def _insert() -> None:
            now = _utc_now()
            with repo._lock:                       # noqa: SLF001 - migration tool
                conn = repo._require()             # noqa: SLF001
                sym = patient.get("symptoms") or {}
                conn.execute(
                    "INSERT INTO mothers (patient_id, name, age, village, phone, "
                    "gestational_weeks, risk_status, headache, blurry_vision, "
                    "swelling, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (pid, name, patient.get("age"), patient.get("village"),
                     _digits(patient.get("phone")), patient.get("gestational_weeks"),
                     _risk(patient.get("risk_status")),
                     int(bool(sym.get("headache"))), int(bool(sym.get("blurry_vision"))),
                     int(bool(sym.get("swelling"))), now, now),
                )
                conn.commit()

        await asyncio.to_thread(_insert)
        imported += 1

        for entry in patient.get("history") or []:
            bp_text = str(entry.get("bp") or "")
            if "/" not in bp_text:
                continue
            try:
                systolic, diastolic = (int(part) for part in bp_text.split("/", 1))
            except ValueError:
                continue
            assessment = TRIAGE.assess(BloodPressure(systolic, diastolic), SymptomSet())
            await repo.record_blood_pressure(pid, BloodPressure(systolic, diastolic), assessment)
            readings += 1

    stats = await repo.stats()
    await repo.close()

    print(f"\n  imported={imported} readings={readings} "
          f"already_present={skipped_existing} junk_skipped={skipped_junk}")
    print(f"  final counts: {stats}")
    return imported


def _digits(value) -> str | None:
    """The JSON store holds phones as '9-8-7-...' for digit-by-digit speech."""
    if not value:
        return None
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    return digits or None


def _risk(value) -> str:
    """Legacy rows use 'NORMAL', which is not a RiskLevel member."""
    text = str(value or "").strip().upper()
    if text in {"", "NORMAL"}:
        return RiskLevel.ROUTINE.value
    try:
        return RiskLevel(text).value
    except ValueError:
        return RiskLevel.ROUTINE.value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="mock_sixtydb.json", type=Path)
    parser.add_argument("--db", default="data/sakhi.sqlite3", type=Path)
    parser.add_argument("--include-junk", action="store_true",
                        help="import placeholder rows like 'test' as well")
    args = parser.parse_args()

    if not args.source.exists():
        print(f"source not found: {args.source}")
        return 1
    print(f"Migrating {args.source} -> {args.db}\n")
    asyncio.run(migrate(args.source, args.db, args.include_junk))
    return 0


if __name__ == "__main__":
    sys.exit(main())
