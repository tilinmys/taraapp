"""Phase 2: API authentication, object-level authorization and the SSE stream.

  * the speech gateway fails closed: no SAKHI_GATEWAY_TOKEN, no access;
  * status_server's patient-data routes and writes need the dashboard's bearer
    token (401 without one, 403 with a wrong one); liveness stays open;
  * the SSE stream is origin-locked (never "*") and its loop ends on a
    Windows-style client abort instead of spinning.
"""

import io
import json
import queue
import urllib.error
import urllib.request

import pytest

import sakhi_gateway
import status_server


# ---- the speech gateway -----------------------------------------------------------


def test_gateway_denies_everything_without_a_configured_token(monkeypatch):
    monkeypatch.delenv("SAKHI_GATEWAY_TOKEN", raising=False)
    assert sakhi_gateway.authorized({}) is False
    assert sakhi_gateway.authorized({"Authorization": "Bearer "}) is False
    assert sakhi_gateway.authorized({"Authorization": "Bearer anything"}) is False


def test_gateway_accepts_only_the_exact_bearer_token(monkeypatch):
    monkeypatch.setenv("SAKHI_GATEWAY_TOKEN", "s3cret")
    assert sakhi_gateway.authorized({"Authorization": "Bearer s3cret"}) is True
    assert sakhi_gateway.authorized({"Authorization": "Bearer wrong"}) is False
    assert sakhi_gateway.authorized({"Authorization": "s3cret"}) is False
    assert sakhi_gateway.authorized({}) is False


# ---- status_server -------------------------------------------------------------------


@pytest.fixture
def server():
    srv = status_server.start_status_server(port=0)
    assert srv is not None
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def _call(url, token=None, body=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers)


PATIENT_ROUTES = [
    "/reports",
    "/api/reports",
    "/reports/mcp-card?id=1",
    "/dashboard/patients",
    "/dashboard/patient?id=1",
    "/dashboard/turns?session_id=x",
    "/dashboard/summary",
    "/dashboard/events",
    "/logs",
    "/schedule?doctor=x",
    "/slot-events",
]


@pytest.mark.parametrize("route", PATIENT_ROUTES)
def test_patient_routes_need_the_token(server, monkeypatch, route):
    monkeypatch.setenv("SAKHI_DASHBOARD_TOKEN", "dash")
    assert _call(server + route)[0] == 401
    assert _call(server + route, token="wrong")[0] == 403
    # With the token the request reaches the route (its own answer: 200/400/404, never 401/403).
    assert _call(server + route, token="dash")[0] not in (401, 403)


def test_writes_need_the_token(server, monkeypatch):
    monkeypatch.setenv("SAKHI_DASHBOARD_TOKEN", "dash")
    for route in ("/reports/flag", "/doctors", "/slots"):
        assert _call(server + route, body={})[0] == 401
        assert _call(server + route, token="dash", body={})[0] not in (401, 403)


def test_gateway_token_is_the_fallback_for_the_dashboard(server, monkeypatch):
    monkeypatch.delenv("SAKHI_DASHBOARD_TOKEN", raising=False)
    monkeypatch.setenv("SAKHI_GATEWAY_TOKEN", "gw")
    assert _call(server + "/dashboard/patients")[0] == 401
    assert _call(server + "/dashboard/patients", token="gw")[0] == 200


def test_liveness_stays_open(server, monkeypatch):
    monkeypatch.setenv("SAKHI_DASHBOARD_TOKEN", "dash")
    assert _call(server + "/health")[0] == 200
    assert _call(server + "/")[0] == 200


def test_no_token_configured_is_open_for_local_development(server, monkeypatch):
    monkeypatch.delenv("SAKHI_DASHBOARD_TOKEN", raising=False)
    monkeypatch.delenv("SAKHI_GATEWAY_TOKEN", raising=False)
    assert _call(server + "/dashboard/patients")[0] == 200


# ---- the SSE stream --------------------------------------------------------------------


class _AbortingWriter(io.BytesIO):
    """Accepts the 'connected' event, then the client is gone (Windows: 10053)."""

    def __init__(self):
        super().__init__()
        self.writes = 0

    def write(self, data):
        self.writes += 1
        if self.writes > 1:
            raise ConnectionAbortedError(10053, "An established connection was aborted")
        return super().write(data)


def _sse_handler(monkeypatch):
    handler = object.__new__(status_server._Handler)
    handler.wfile = _AbortingWriter()
    sent = {}
    handler.send_response = lambda code: sent.setdefault("status", code)
    handler.send_header = lambda k, v: sent.setdefault(k, v)
    handler.end_headers = lambda: None
    q = queue.Queue()
    q.put("data: {}\n\n")  # one event waiting: its write hits the dead socket
    monkeypatch.setattr(status_server, "_register_sse_client", lambda: q)
    monkeypatch.setattr(status_server, "_unregister_sse_client", lambda _q: sent.setdefault("unregistered", True))
    return handler, sent


def test_sse_is_origin_locked_never_wildcard(monkeypatch):
    monkeypatch.delenv("ALLOWED_ORIGIN", raising=False)
    handler, sent = _sse_handler(monkeypatch)
    handler._handle_sse()
    assert sent["Access-Control-Allow-Origin"] == "http://localhost:3100"

    monkeypatch.setenv("ALLOWED_ORIGIN", "https://console.example.org")
    handler, sent = _sse_handler(monkeypatch)
    handler._handle_sse()
    assert sent["Access-Control-Allow-Origin"] == "https://console.example.org"


def test_sse_loop_ends_on_a_windows_client_abort(monkeypatch):
    handler, sent = _sse_handler(monkeypatch)
    handler._handle_sse()  # returns instead of spinning or raising
    assert sent.get("unregistered") is True
