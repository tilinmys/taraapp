"""Preview the admin dashboard with fictional data - no worker, no LiveKit,
no paid provider calls, and never the real patient database.

Seeds a throwaway SQLite database with three FICTIONAL mothers, serves the
real status_server.py dashboard API on it, and keeps emitting live pipeline
events (including a simulated LLM outage every ~40 s) so the red/green board
and the live log visibly update.

    python scripts/dashboard_demo_server.py            # serves on :8095
    set WORKER_STATUS_URL=http://127.0.0.1:8095 && npm --prefix frontend run dev -- --port 3105
    open http://localhost:3105/admin
"""

from __future__ import annotations

import asyncio
import itertools
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DEMO_DB = Path(os.getenv("SAKHI_DEMO_DB", Path(tempfile.gettempdir()) / "sakhi_dashboard_demo.sqlite3"))
PORT = int(os.getenv("SAKHI_DEMO_PORT", "8095"))

# Must be set before status_server / the dashboard store are imported, so the
# demo can never pick up DATABASE_URL or the real SAKHI_DB_PATH.
os.environ.pop("DATABASE_URL", None)
os.environ.pop("POSTGRES_URL", None)
os.environ["SAKHI_DB_PATH"] = str(DEMO_DB)

from sakhi_domain import BloodPressure, SymptomSet  # noqa: E402
from sakhi_sql_repository import SQLitePatientRepository  # noqa: E402
from sakhi_telemetry import CURRENT_SESSION, TelemetryWriter  # noqa: E402
from sakhi_triage import TRIAGE  # noqa: E402
import status_server  # noqa: E402
import sakhi_aws_costs  # noqa: E402


class _DemoCostExplorer:
    """FICTIONAL AWS bill for the "AWS costs" tab - never calls AWS."""

    _SERVICES = {
        "Amazon Elastic Compute Cloud - Compute": (14.10, 11.20,
            {"APS3-BoxUsage:t3.small": (13.60, 10.80), "APS3-CPUCredits:t3": (0.50, 0.40)}),
        "EC2 - Other": (2.05, 1.62,
            {"APS3-EBS:VolumeUsage.gp3": (1.60, 1.28), "APS3-EBS:SnapshotUsage": (0.45, 0.34)}),
        "Amazon Simple Storage Service": (0.42, 0.31,
            {"APS3-TimedStorage-ByteHrs": (0.30, 0.22), "APS3-Requests-Tier1": (0.12, 0.09)}),
        "AWS Data Transfer": (0.38, 0.29, {"APS3-DataTransfer-Out-Bytes": (0.38, 0.29)}),
        "AWS Systems Manager": (0.00, 0.00, {"APS3-Automation-Steps": (0.00, 0.00)}),
    }

    @staticmethod
    def _m(value):
        return {"UnblendedCost": {"Amount": f"{value:.4f}", "Unit": "USD"}}

    def get_cost_and_usage(self, **kw):
        start = kw["TimePeriod"]["Start"]
        service = (kw.get("Filter") or {}).get("Dimensions", {}).get("Values", [None])[0]
        if kw["Granularity"] == "MONTHLY":
            if service:
                items = {k: v for k, v in self._SERVICES[service][2].items()}
            else:
                items = {k: (v[0], v[1]) for k, v in self._SERVICES.items()}
            # (month_to_date, last_month) -> last month first, then this month
            return {"ResultsByTime": [
                {"TimePeriod": {"Start": start},
                 "Groups": [{"Keys": [k], "Metrics": self._m(v[1])} for k, v in items.items()]},
                {"TimePeriod": {"Start": sakhi_aws_costs._today().replace(day=1).isoformat()},
                 "Groups": [{"Keys": [k], "Metrics": self._m(v[0])} for k, v in items.items()]},
            ]}
        share = (self._SERVICES[service][0] / 16.95) if service else 1.0
        first = datetime.fromisoformat(start).date()
        days = (sakhi_aws_costs._today() - first).days + 1
        return {"ResultsByTime": [
            {"TimePeriod": {"Start": (first + timedelta(days=i)).isoformat()},
             "Total": self._m(share * (0.52 + 0.08 * ((i * 7) % 5)))}
            for i in range(days)]}

    def get_cost_forecast(self, **kw):
        return {"Total": {"Amount": "2.40", "Unit": "USD"}}


sakhi_aws_costs._CLIENT_FACTORY = _DemoCostExplorer
os.environ.setdefault("AWS_COST_CACHE_PATH", str(Path(tempfile.gettempdir()) / "sakhi_demo_costs.json"))
os.environ.setdefault("AWS_COST_INR_RATE", "83")

DEMO_MOTHERS = [
    # (name, age, weeks, village, bp, symptoms, conversation)
    ("Demo Mother Lakshmi", 26, 32, "Basavakalyan", (152, 98), SymptomSet(headache=True, fever=True),
     [("sakhi", "ತಾಯಿಯ ಹೆಸರು ಮತ್ತು ವಯಸ್ಸು ಹೇಳಿ."), ("asha", "Lakshmi, 26"),
      ("sakhi", "ಈಗ ಯಾವಟು ತಿಂಗಳು ಗರ್ಭವಿದೆ?"), ("asha", "ಎಂಟು ತಿಂಗಳು"),
      ("sakhi", "ರಕ್ತದೊತ್ತಡ ಎಷ್ಟು?"), ("asha", "152 by 98, she has headache and fever")]),
    ("Demo Mother Sunita", 22, 20, "Bhalki", (118, 76), SymptomSet(),
     [("sakhi", "माता का नाम और उम्र बताइए दीदी"), ("asha", "Sunita, 22"),
      ("sakhi", "अभी कितने महीने की गर्भवती है?"), ("asha", "पांच महीने")]),
    ("Demo Mother Renuka", 30, 36, "Aurad", (128, 82), SymptomSet(swelling=True),
     [("sakhi", "Name and age of the mother?"), ("asha", "Renuka, 30")]),
]


def _event(label, stage, status="ok", message="", **details):
    return {"ts": datetime.now(timezone.utc).isoformat(), "stage": stage, "status": status,
            "label": label, "message": message, "details": details}


async def seed(repo: SQLitePatientRepository, writer: TelemetryWriter) -> None:
    for i, (name, age, weeks, village, (sys_, dia), symptoms, convo) in enumerate(DEMO_MOTHERS, 1):
        existing = await repo.find_patients(name=name)
        if existing:
            continue
        mother = await repo.create_patient(name=name, age=age, gestational_weeks=weeks, village=village)
        bp = BloodPressure(sys_, dia)
        assessment = TRIAGE.assess(bp, symptoms, gestational_weeks=weeks)
        await repo.record_blood_pressure(mother.patient_id, bp, assessment)
        await repo.record_symptoms(mother.patient_id, symptoms)
        if assessment.requires_escalation:
            await repo.log_teleconsult(mother.patient_id, "demo escalation", assessment,
                                       trigger_sign="fever" if symptoms.fever else "symptomatic_bp")
        session = f"demo-room-{i}"
        await repo.save_call_report({
            "session_id": session, "patient_id": mother.patient_id, "language": "kn",
            "risk_level": assessment.level.value, "intake_answered": len(convo) // 2,
            "intake_total": 24, "escalated": assessment.requires_escalation, "duration_ms": 184000,
        })
        token = CURRENT_SESSION.set(session)
        for speaker, text in convo:
            if speaker == "sakhi":
                writer.submit(_event("Sakhi said", "Stage 8 TTS", message=text, event="agent_speech"))
            else:
                writer.submit(_event("Transcript final", "Stage 5 STT", message=text, language="kn"))
        CURRENT_SESSION.reset(token)
    await writer.flush()


async def live_events(writer: TelemetryWriter) -> None:
    """A heartbeat per component, plus a simulated LLM outage every ~40 s."""
    stages = ["Stage 3 Microphone", "Stage 2 WebRTC", "Stage 5 STT", "Stage 6 Semantic Turn",
              "Stage 7 LLM", "Stage 8 TTS", "Stage 9 Playback", "Stage 10 Tools DB", "Stage 0 Worker"]
    token = CURRENT_SESSION.set("demo-live")
    for tick in itertools.count():
        for stage in stages:
            if stage == "Stage 7 LLM" and tick % 20 in (10, 11, 12):
                writer.submit(_event("LLM failed", stage, status="error",
                                     message="Groq 429 rate limit (simulated demo outage)"))
            else:
                writer.submit(_event("Heartbeat", stage, message="demo heartbeat"))
        await asyncio.sleep(2)
    CURRENT_SESSION.reset(token)


async def main() -> None:
    repo = await SQLitePatientRepository(DEMO_DB).load()
    writer = TelemetryWriter(repo.record_telemetry, health_min_interval=1.0)
    writer.start()
    await seed(repo, writer)
    if status_server.start_status_server(PORT) is None:
        raise SystemExit(f"port {PORT} is busy - set SAKHI_DEMO_PORT")
    print(f"Demo dashboard API on http://127.0.0.1:{PORT}  (database: {DEMO_DB})", flush=True)
    await live_events(writer)


if __name__ == "__main__":
    asyncio.run(main())
