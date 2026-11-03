"""Public, real-time, measurable data (UNICEF Innovation Fund REOI): the
/public/metrics counters are open to anyone, carry counts only, and never a
count small enough to point to a person in one village.
"""

import json
import urllib.error
import urllib.request

import pytest

import status_server


class _Store:
    def __init__(self, summary):
        self._summary = summary

    def summary(self):
        return self._summary


@pytest.fixture
def server():
    srv = status_server.start_status_server(port=0)
    assert srv is not None
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def _get(url):
    with urllib.request.urlopen(url, timeout=10) as r:
        return r.status, dict(r.headers), json.loads(r.read())


def test_public_metrics_need_no_token_even_when_the_dashboard_has_one(server, monkeypatch):
    monkeypatch.setenv("SAKHI_DASHBOARD_TOKEN", "dash")
    monkeypatch.setattr(status_server, "get_store", lambda: _Store(
        {"backend": "sqlite", "mothers": 42, "high_risk_mothers": 7, "calls": 130, "escalations": 9, "errors": 3}))
    status, headers, body = _get(server + "/public/metrics")
    assert status == 200
    assert headers.get("Access-Control-Allow-Origin") == "*"
    assert body["metrics"] == {"screenings": 130, "mothers_screened": 42, "high_risk_mothers": 7, "escalations": 9}
    assert body["generated_at"].endswith("Z") and body["suppressed_below"] == 5
    assert set(body["definitions"]) == set(body["metrics"])


def test_small_counts_are_suppressed_zero_is_not(server, monkeypatch):
    monkeypatch.setattr(status_server, "get_store", lambda: _Store(
        {"mothers": 3, "high_risk_mothers": 1, "calls": 4, "escalations": 0}))
    _, _, body = _get(server + "/public/metrics")
    assert body["metrics"] == {"screenings": "<5", "mothers_screened": "<5", "high_risk_mothers": "<5", "escalations": 0}


def test_only_aggregate_counts_ever_leave(server, monkeypatch):
    monkeypatch.setattr(status_server, "get_store", lambda: _Store(
        {"backend": "postgres", "mothers": 50, "high_risk_mothers": 6, "calls": 80, "escalations": 6, "errors": 11}))
    _, _, body = _get(server + "/public/metrics")
    assert set(body) == {"generated_at", "suppressed_below", "metrics", "definitions"}
    assert all(isinstance(v, int) or v == "<5" for v in body["metrics"].values())
    assert "backend" not in json.dumps(body) and "errors" not in body["metrics"]


def test_a_broken_store_answers_503_not_a_traceback(server, monkeypatch):
    def boom():
        raise RuntimeError("db down: postgresql://user:pass@host")
    monkeypatch.setattr(status_server, "get_store", boom)
    with pytest.raises(urllib.error.HTTPError) as e:
        _get(server + "/public/metrics")
    assert e.value.code == 503
    assert b"pass" not in e.value.read()
