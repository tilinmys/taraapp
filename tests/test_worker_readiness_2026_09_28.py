"""Worker readiness must not expire on a healthy long-running worker.

Incident 2026-09-27 23:05 UTC: every call was refused with "Worker not ready
yet" because the last "registered worker" line was >24 h old - a worker
registers once and stays connected, so registration age says nothing.
"""

import time
from datetime import datetime

import status_server as ss


def _line(ts, text):
    return f"{datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S')},000 - INFO livekit.agents - {text}"


def _setup(monkeypatch, tmp_path, lines, started_at):
    log = tmp_path / "worker_background.log"
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    monkeypatch.setattr(ss, "WORKER_LOG_PATH", log)
    monkeypatch.setattr(ss, "_PROCESS_STARTED_AT", started_at)
    monkeypatch.setattr(ss, "WORKER_READY_MAX_AGE_SECONDS", 0)


def test_worker_up_for_two_days_is_still_ready(monkeypatch, tmp_path):
    now = time.time()
    _setup(monkeypatch, tmp_path, [_line(now - 2 * 86400 + 30, "registered worker")], now - 2 * 86400)
    assert ss.worker_status()["ready"] is True


def test_registration_from_a_previous_process_does_not_count(monkeypatch, tmp_path):
    now = time.time()
    _setup(monkeypatch, tmp_path, [_line(now - 3600, "registered worker")], now - 10)
    status = ss.worker_status()
    assert status["ready"] is False
    assert "since it restarted" in status["reason"]


def test_disconnect_after_registration_is_not_ready(monkeypatch, tmp_path):
    now = time.time()
    _setup(monkeypatch, tmp_path, [
        _line(now - 600, "registered worker"),
        _line(now - 60, "failed to connect to livekit"),
    ], now - 900)
    assert ss.worker_status()["ready"] is False


def test_reconnected_worker_is_ready_again(monkeypatch, tmp_path):
    now = time.time()
    _setup(monkeypatch, tmp_path, [
        _line(now - 600, "registered worker"),
        _line(now - 300, "worker connection closed unexpectedly"),
        _line(now - 290, "registered worker"),
    ], now - 900)
    assert ss.worker_status()["ready"] is True


def test_optional_age_limit_still_available(monkeypatch, tmp_path):
    now = time.time()
    _setup(monkeypatch, tmp_path, [_line(now - 7200, "registered worker")], now - 8000)
    monkeypatch.setattr(ss, "WORKER_READY_MAX_AGE_SECONDS", 3600)
    assert ss.worker_status()["ready"] is False
