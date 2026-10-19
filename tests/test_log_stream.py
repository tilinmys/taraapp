"""The /logs cursor stream behind the ASHA console's log rail.

Regression cover for "logs sometimes do not appear": every poll used to
re-read and split the whole pipeline_events.jsonl (2.8 MB / 7.8k lines on
the live box after three weeks, and only growing), per open console, every
1.2 s. These pin the incremental index that replaced it, and the cursor
semantics the frontend depends on.
"""

import json
import os
import time

import pytest

import status_server
from status_server import _LineIndex


def _event(i: int) -> str:
    return json.dumps({"ts": "2026-09-24T00:00:00Z", "stage": "Stage 5 LLM",
                       "status": "ok", "label": f"event {i}", "message": "", "details": {}})


def _append(path, lines):
    with open(path, "a", encoding="utf-8") as handle:
        for line in lines:
            handle.write(line + "\n")


@pytest.fixture
def log_path(tmp_path, monkeypatch):
    path = tmp_path / "pipeline_events.jsonl"
    monkeypatch.setattr(status_server, "_PIPELINE_LOG_INDEX", _LineIndex(path))
    return path


def test_missing_file_reads_as_empty(log_path):
    assert status_server._read_logs(0) == {"next": 0, "events": []}
    assert status_server._PIPELINE_LOG_INDEX.line_count() == 0


def test_cursor_walks_forward_without_gaps_or_repeats(log_path):
    _append(log_path, [_event(i) for i in range(5)])
    first = status_server._read_logs(0)
    assert [e["label"] for e in first["events"]] == [f"event {i}" for i in range(5)]
    assert first["next"] == 5

    _append(log_path, [_event(i) for i in range(5, 8)])
    second = status_server._read_logs(first["next"])
    assert [e["label"] for e in second["events"]] == ["event 5", "event 6", "event 7"]
    assert second["next"] == 8
    assert status_server._read_logs(8) == {"next": 8, "events": []}


def test_batches_are_capped_at_250_lines(log_path):
    _append(log_path, [_event(i) for i in range(600)])
    page = status_server._read_logs(0)
    assert len(page["events"]) == 250 and page["next"] == 250
    page = status_server._read_logs(500)
    assert len(page["events"]) == 100 and page["next"] == 600


def test_partial_last_line_waits_for_its_newline(log_path):
    """The writer thread can be caught mid-line. That half line must not be
    served as a 'Malformed log line' and then skipped past forever."""
    _append(log_path, [_event(0)])
    with open(log_path, "a", encoding="utf-8") as handle:
        handle.write(_event(1)[:20])
    page = status_server._read_logs(0)
    assert [e["label"] for e in page["events"]] == ["event 0"]
    assert page["next"] == 1

    with open(log_path, "a", encoding="utf-8") as handle:
        handle.write(_event(1)[20:] + "\n")
    page = status_server._read_logs(1)
    assert [e["label"] for e in page["events"]] == ["event 1"]


def test_truncated_file_is_reindexed(log_path):
    _append(log_path, [_event(i) for i in range(10)])
    assert status_server._read_logs(0)["next"] == 10
    log_path.write_text("", encoding="utf-8")
    _append(log_path, [_event(100), _event(101)])
    # A console still holding cursor 10 is told the file is now 2 lines long,
    # then reads the new lines from there.
    stale = status_server._read_logs(10)
    assert stale == {"next": 2, "events": []}
    assert [e["label"] for e in status_server._read_logs(0)["events"]] == ["event 100", "event 101"]


def test_replaced_file_is_reindexed(log_path):
    _append(log_path, [_event(i) for i in range(3)])
    status_server._read_logs(0)
    replacement = log_path.with_suffix(".new")
    _append(replacement, [_event(i) for i in range(50, 60)])
    os.replace(replacement, log_path)
    labels = [e["label"] for e in status_server._read_logs(0)["events"]]
    assert labels[0] == "event 50" and len(labels) == 10


def test_malformed_line_is_reported_not_dropped(log_path):
    _append(log_path, [_event(0), "{not json", _event(2)])
    page = status_server._read_logs(0)
    assert [e["label"] for e in page["events"]] == ["event 0", "Malformed log line", "event 2"]


def test_non_ascii_payload_round_trips(log_path):
    _append(log_path, [json.dumps({"label": "ವಯಸ್ಸು ಹೇಳಿ", "status": "ok"}, ensure_ascii=False)])
    assert status_server._read_logs(0)["events"][0]["label"] == "ವಯಸ್ಸು ಹೇಳಿ"


def test_latest_cursor_skips_history(log_path):
    _append(log_path, [_event(i) for i in range(40)])
    assert status_server._PIPELINE_LOG_INDEX.line_count() == 40


def test_poll_cost_does_not_grow_with_file_size(log_path):
    """100k existing lines (~13 MB). After the first index build, a poll at
    the tail must cost about the same as on a tiny file - the property the
    old read_text().splitlines() implementation lacked."""
    line = _event(0)
    with open(log_path, "w", encoding="utf-8") as handle:
        handle.write((line + "\n") * 100_000)
    index = status_server._PIPELINE_LOG_INDEX
    assert index.line_count() == 100_000          # one-time index build

    _append(log_path, [_event(i) for i in range(5)])
    started = time.perf_counter()
    for _ in range(200):
        page = status_server._read_logs(100_000)
    per_poll = (time.perf_counter() - started) / 200
    assert len(page["events"]) == 5 and page["next"] == 100_005
    assert per_poll < 0.005, f"tail poll took {per_poll * 1000:.2f} ms"
