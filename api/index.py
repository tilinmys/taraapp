"""Single Vercel Python entrypoint, routing all API paths internally.

Vercel's current Python runtime (CLI 55.0.0, confirmed live 2026-07) wants
one entrypoint at a "default location" (app.py/index.py/server.py/main.py/
wsgi.py/asgi.py, at root or inside src/, app/, or api/) defining a top-level
`app`/`application`/`handler`. The older "one file per endpoint under /api,
each auto-detected" pattern documented elsewhere did not reliably trigger
here - the build failed with "No python entrypoint found in default
locations" even though api/livekit-token.py etc. all defined `handler`.

Fix: consolidate every endpoint into this one file (api/index.py - a
recognized default location), with vercel.json rewriting each public path
(/api/token, /api/logs, /api/worker-status, /api/tts-catalog) to /api/index.
Vercel rewrites preserve the original request path, so do_GET still sees the
real path to dispatch on.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import sakhi_gateway  # noqa: E402
import voice_catalog  # noqa: E402
from vercel_common import fetch_logs, fetch_worker_status  # noqa: E402

# Default matches LIVEKIT_AGENT_NAME in the deployed env - only reached if
# that var is unset. NOT "mystree-care": a separate worker under that name
# runs on this same LiveKit Cloud project, and a silent fallback would
# dispatch calls to it instead of the Sakhi worker (2026-09-02) - see
# .env's LIVEKIT_AGENT_NAME comment in the main repo.
DEFAULT_AGENT_NAME = "sakhi-maternal-care"


# --- token issuance + dispatch -------------------------------------------

def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def livekit_token(room: str, participant: str, metadata: str | None = None, room_config: dict | None = None) -> str:
    api_key = os.getenv("LIVEKIT_API_KEY")
    api_secret = os.getenv("LIVEKIT_API_SECRET")
    if not api_key or not api_secret:
        raise RuntimeError("missing LiveKit credentials")

    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "iss": api_key,
        "sub": participant,
        "nbf": now,
        "exp": now + 6 * 60 * 60,
        "name": participant,
        "video": {
            "room": room,
            "roomJoin": True,
            "canPublish": True,
            "canSubscribe": True,
            "canPublishData": True,
        },
    }
    if metadata:
        payload["metadata"] = metadata
    if room_config:
        # e.g. {"agents": [{"agentName": ..., "metadata": ...}]}: LiveKit
        # dispatches the agent itself when this participant joins.
        payload["roomConfig"] = room_config
    signing_input = f"{b64url(json.dumps(header, separators=(',', ':')).encode())}.{b64url(json.dumps(payload, separators=(',', ':')).encode())}"
    signature = hmac.new(api_secret.encode(), signing_input.encode(), hashlib.sha256).digest()
    return f"{signing_input}.{b64url(signature)}"


def create_agent_dispatch(room: str, metadata: str | None = None, agent_name: str | None = None) -> str | None:
    agent_name = agent_name or os.getenv("LIVEKIT_AGENT_NAME", DEFAULT_AGENT_NAME)
    if os.getenv("LIVEKIT_EXPLICIT_DISPATCH", "true").lower() not in {"1", "true", "yes", "on"}:
        return None

    from livekit import api

    async def _create() -> str:
        lkapi = api.LiveKitAPI(
            url=os.getenv("LIVEKIT_URL"),
            api_key=os.getenv("LIVEKIT_API_KEY"),
            api_secret=os.getenv("LIVEKIT_API_SECRET"),
        )
        try:
            dispatch = await lkapi.agent_dispatch.create_dispatch(
                api.CreateAgentDispatchRequest(
                    agent_name=agent_name,
                    room=room,
                    metadata=metadata or "",
                )
            )
            return dispatch.id
        finally:
            await lkapi.aclose()

    return asyncio.run(_create())


def _dispatch_quietly(plan: dict) -> None:
    started = time.perf_counter()
    try:
        dispatch_id = create_agent_dispatch(plan["room"], plan["metadata"], agent_name=plan["agent"])
        print(f"[sakhi live] dispatched {plan['agent']} to {plan['room']} in {round((time.perf_counter() - started) * 1000)} ms ({dispatch_id})", flush=True)
    except Exception as exc:  # noqa: BLE001 - logged; the app times out and offers offline mode
        print(f"[sakhi live] DISPATCH FAILED for {plan['room']}: {exc}", flush=True)


class handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path.endswith("/token"):
            self._handle_token(parsed)
        elif path.endswith("/logs"):
            self._handle_logs(parsed)
        elif path.endswith("/worker-status"):
            self._send_json(fetch_worker_status())
        elif path.endswith("/tts-catalog"):
            self._send_json(voice_catalog.as_json_catalog())
        elif "/sakhi/live/" in path:
            self._handle_sakhi_live_get(path)
        elif path.endswith("/sakhi/health"):
            self._send_json(sakhi_gateway.health())
        elif path.endswith("/sakhi/tts"):
            self._handle_sakhi_tts(parsed)
        else:
            self.send_error(404)

    # --- Sakhi in-app voice gateway (docs/VOICE_IN_APP_DESIGN.md) ----------

    def _is_sakhi(self) -> bool:
        return "/sakhi/" in urlparse(self.path).path

    def end_headers(self) -> None:
        # The app on the web (Expo web, another origin) calls these routes
        # too. No cookies are involved (optional bearer token), so "*" is safe.
        if self._is_sakhi():
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Sakhi-Priority")
            self.send_header("Access-Control-Max-Age", "86400")
        super().end_headers()

    def do_OPTIONS(self) -> None:
        if not self._is_sakhi():
            self.send_error(404)
            return
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path.endswith("/sakhi/stt"):
            self._handle_sakhi_stt(urlparse(self.path))
        elif path.endswith("/sakhi/live/session"):
            self._handle_sakhi_live_session()
        else:
            self.send_error(404)

    # --- Sakhi LIVE (WebRTC via LiveKit, docs/VOICE_IN_APP_DESIGN.md 10) ----

    def _handle_sakhi_live_get(self, path: str) -> None:
        if path.endswith("/live/config"):
            self._send_json(sakhi_gateway.live_config())
            return
        asset = sakhi_gateway.live_asset(path.rsplit("/", 1)[-1], "gzip" in self.headers.get("Accept-Encoding", ""))
        if asset is None:
            self.send_error(404)
            return
        body, content_type, encoding, cache = asset
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        if encoding:
            self.send_header("Content-Encoding", encoding)
            self.send_header("Vary", "Accept-Encoding")
        self.end_headers()
        self.wfile.write(body)

    def _limited(self, kind: str) -> bool:
        """True (and a 429 sent) when this client is over its limit for `kind`."""
        if sakhi_gateway.rate_ok(kind, self.headers, self.client_address[0] if self.client_address else None):
            return False
        self.send_response(429)
        body = json.dumps({"error": "Too many requests, slow down."}).encode()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Retry-After", "10")
        self.end_headers()
        self.wfile.write(body)
        return True

    def _content_length(self) -> int:
        try:
            return max(0, int(self.headers.get("Content-Length") or 0))
        except ValueError:
            return -1

    def _handle_sakhi_live_session(self) -> None:
        """One live conversation: a room, the app's token, Sakhi dispatched into it."""
        if not sakhi_gateway.authorized(self.headers):
            self._send_json({"error": "unauthorized"}, status=401)
            return
        if self._limited("live"):
            return
        length = self._content_length()
        try:
            req = json.loads(self.rfile.read(length) or b"{}") if 0 < length <= 4096 else {}
        except (ValueError, json.JSONDecodeError):
            req = {}
        if not isinstance(req, dict):
            req = {}
        started = time.perf_counter()
        # How Sakhi is dispatched (both keep this response ~instant):
        #   background  a dispatch API call in a thread, so Sakhi joins WHILE
        #               the phone connects (a long-running server: this PC)
        #   token       the dispatch rides inside the token and LiveKit runs it
        #               when the phone joins: no API call, safe on serverless
        #               (Vercel may freeze a thread once the response is sent)
        mode = os.getenv("SAKHI_LIVE_DISPATCH") or ("token" if os.getenv("VERCEL") else "background")
        if not os.getenv("VERCEL") and req.get("dispatch") in ("token", "background"):
            mode = req["dispatch"]  # development: compare both
        try:
            plan = sakhi_gateway.live_plan(req)
            room_config = {"agents": [{"agentName": plan["agent"], "metadata": plan["metadata"]}]} if mode == "token" else None
            token = livekit_token(plan["room"], plan["identity"], plan["metadata"], room_config)
        except sakhi_gateway.GatewayError as exc:
            self._send_json({"error": exc.message}, status=exc.status)
            return
        except Exception as exc:  # misconfigured: details in the server log only
            print(f"live session failed: {exc!r}", flush=True)
            self._send_json({"error": "Live Sakhi is not available right now."}, status=502)
            return
        # The dispatch API call alone measured ~1.9 s: never on the response
        # path. A failed dispatch shows up as "Sakhi did not join" in the app.
        if mode == "background":
            threading.Thread(target=_dispatch_quietly, args=(plan,), daemon=True).start()
        self._send_json({
            "url": plan["url"],
            "token": token,
            "room": plan["room"],
            "identity": plan["identity"],
            "agent": plan["agent"],
            "dispatch": mode,
            "ms": round((time.perf_counter() - started) * 1000),
        })

    def _handle_sakhi_tts(self, parsed) -> None:
        if not sakhi_gateway.authorized(self.headers):
            self._send_json({"error": "unauthorized"}, status=401)
            return
        if self._limited("tts"):
            return
        params = parse_qs(parsed.query)
        try:
            # X-Sakhi-Priority: now (being said) / next / warm (background download).
            audio = sakhi_gateway.tts(params.get("text", [""])[0], params.get("lang", ["kn"])[0], self.headers.get("X-Sakhi-Priority", "now"))
        except sakhi_gateway.GatewayError as exc:
            self._send_json({"error": exc.message}, status=exc.status)
            return
        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(audio)))
        # Same question text = same URL: the CDN serves it from the edge.
        self.send_header("Cache-Control", "public, max-age=31536000, s-maxage=31536000, immutable")
        self.end_headers()
        self.wfile.write(audio)

    def _handle_sakhi_stt(self, parsed) -> None:
        if not sakhi_gateway.authorized(self.headers):
            self._send_json({"error": "unauthorized"}, status=401)
            return
        if self._limited("stt"):
            return
        length = self._content_length()
        if length <= 0 or length > sakhi_gateway.MAX_AUDIO_BYTES:
            self._send_json({"error": "Body must be a WAV file up to 25 s."}, status=413 if length > 0 else 400)
            return
        wav = self.rfile.read(length)
        lang = parse_qs(parsed.query).get("lang", ["kn"])[0]
        try:
            self._send_json(sakhi_gateway.stt(wav, lang))
        except sakhi_gateway.GatewayError as exc:
            self._send_json({"error": exc.message}, status=exc.status)

    def _handle_token(self, parsed) -> None:
        params = parse_qs(parsed.query)

        # Unique room per call: reusing a fixed room name lets a second call join a
        # stale room whose agent already greeted or shut down, producing a silent call.
        default_room = f"sakhi-room-{int(time.time() * 1000):x}-{os.urandom(3).hex()}"
        room = params.get("room", [default_room])[0]
        participant = f"clinic-user-{int(time.time())}"

        provider = params.get("provider", ["smallest"])[0].strip().lower()
        if provider not in voice_catalog.PROVIDERS:
            provider = "smallest"
        if not voice_catalog.is_available(provider):
            reason = voice_catalog.PROVIDER_UNAVAILABLE_REASON.get(provider, "This provider is temporarily unavailable.")
            self._send_json(
                {"error": f"{provider} is unavailable right now - {reason} Pick Sarvam or Smallest.ai instead."},
                status=400,
            )
            return

        voice = params.get("voice", [""])[0].strip()
        if provider == "sarvam":
            voice = voice.lower()
        caller_phone = params.get("phone", [""])[0].strip()
        metadata_payload = {}
        if voice_catalog.is_valid(provider, voice):
            metadata_payload["tts_provider"] = provider
            metadata_payload["voice_id"] = voice
        if caller_phone:
            metadata_payload["caller_phone"] = caller_phone
        metadata = json.dumps(metadata_payload) if metadata_payload else None

        ws_url = os.getenv("LIVEKIT_URL")
        try:
            if not ws_url:
                raise RuntimeError("missing LIVEKIT_URL")
            status = fetch_worker_status()
            if not status.get("ready"):
                self._send_json(
                    {
                        "error": "Worker not ready yet. Wait a few seconds and start the call again.",
                        "worker": status,
                    },
                    status=503,
                )
                return
            dispatch_id = create_agent_dispatch(room, metadata)
            token = livekit_token(room, participant, metadata)
            worker_url = os.getenv("RAILWAY_WORKER_URL", "").strip().rstrip("/")
            self._send_json(
                {
                    "token": token,
                    "url": ws_url,
                    "participant": participant,
                    "provider": provider,
                    "voice": voice or "default",
                    "dispatch_id": dispatch_id,
                    "worker": status,
                    "worker_url": worker_url,
                }
            )
        except Exception as exc:
            self._send_json({"error": f"Server misconfigured: {exc}"}, status=500)

    def _handle_logs(self, parsed) -> None:
        params = parse_qs(parsed.query)
        raw_since = params.get("since", ["0"])[0]
        if raw_since == "latest":
            self._send_json({"next": 0, "events": []})
            return
        try:
            since = max(0, int(raw_since))
        except ValueError:
            since = 0
        self._send_json(fetch_logs(since))

    def _send_json(self, payload, status: int = 200) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
