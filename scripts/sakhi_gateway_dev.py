"""Run the Sakhi voice gateway on this PC and reach it from Expo Go.

    python scripts/sakhi_gateway_dev.py

1. loads the repo's .env (provider keys stay HERE, never in the app)
2. serves api/index.py's real handler on http://localhost:8787
   (/api/sakhi/health, /tts, /stt, and /live/* for Live Sakhi), and Sakhi
   Direct's WebSocket on the same port: /api/sakhi/ws (sakhi_ws.py)
3. opens a Cloudflare quick tunnel to it (the cloudflared binary the app's
   Metro tunnel already uses)
4. writes app/.env.local: EXPO_PUBLIC_SAKHI_GATEWAY=<public url>/api/sakhi
   (phones) and EXPO_PUBLIC_SAKHI_GATEWAY_LOCAL=http://localhost:8787/api/sakhi
   (the desktop browser)
   Phones do NOT use this address: the app's own tunnel (npm run tunnel)
   forwards /api/sakhi/* to this gateway (app/metro.config.js), so the app
   always talks to the gateway at the address it was loaded from. This
   second tunnel only serves Sakhi Direct's WebSocket.
5. watches the gateway's files and restarts ONLY the server when one changes:
   the tunnel (and so the URL baked into the app) stays the same.

Live Sakhi calls go to the local agent (scripts/sakhi_agent_dev.py) under its
own name, never to the production worker.

Production uses the same handler on Vercel (vercel.json routes /api/sakhi/*);
set GNANI_API_KEY (or SARVAM_API_KEY) and the LiveKit keys there instead.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# numpy's BLAS reserves memory per CPU thread; nothing here does matrix maths.
for _var in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_var, "1")
PORT = int(os.getenv("SAKHI_GATEWAY_PORT", "8787"))
WATCH = [ROOT / "api" / "index.py", ROOT / "sakhi_gateway.py", ROOT / "sakhi_ws.py", ROOT / "vercel_common.py", ROOT / "voice_catalog.py", Path(__file__).resolve()]


def load_env(path: Path) -> int:
    n = 0
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = re.split(r"\s+#", value, maxsplit=1)[0].strip().strip('"').strip("'")
        if key.strip() and value:
            os.environ.setdefault(key.strip(), value)
            n += 1
    return n


INNER = PORT - 1  # the plain HTTP handler, reachable only from this PC
HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer", "transfer-encoding", "upgrade", "host", "content-length"}


def make_front(inner_port: int, ws_handler=None):
    """The aiohttp front: /api/sakhi/ws (Sakhi Direct) + a pass-through of
    every other request to the plain handler on `inner_port`.

    The pass-through keeps bodies and encodings byte for byte (gzip assets
    are not re-encoded), drops hop-by-hop headers both ways, tells the inner
    handler who the caller is (X-Forwarded-For, for the per-client limits)
    and answers 502 JSON if the inner handler is down: never a traceback.
    """
    from aiohttp import ClientError, ClientSession, ClientTimeout, web

    http_key = web.AppKey("http", ClientSession)

    async def proxy(request: web.Request) -> web.StreamResponse:
        session = request.app[http_key]
        headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP}
        if "CF-Connecting-IP" not in request.headers and request.remote:
            headers["X-Forwarded-For"] = request.remote
        body = await request.read()
        try:
            async with session.request(request.method, f"http://127.0.0.1:{inner_port}{request.rel_url}", headers=headers, data=body or None, allow_redirects=False) as r:
                data = await r.read()
                out = {k: v for k, v in r.headers.items() if k.lower() not in HOP}
                return web.Response(status=r.status, body=data, headers=out)
        except (ClientError, OSError):
            return web.json_response({"error": "gateway restarting, try again"}, status=502, headers={"Retry-After": "2"})

    async def startup(app: web.Application) -> None:
        # auto_decompress off: gzipped assets pass through untouched.
        app[http_key] = ClientSession(auto_decompress=False, timeout=ClientTimeout(total=60))

    async def cleanup(app: web.Application) -> None:
        await app[http_key].close()

    app = web.Application(client_max_size=8 * 1024 * 1024)
    app.on_startup.append(startup)
    app.on_cleanup.append(cleanup)
    if ws_handler is not None:
        app.router.add_get("/api/sakhi/ws", ws_handler)
    app.router.add_route("*", "/{tail:.*}", proxy)
    return app


def serve() -> None:
    """Child process: ONE process for the whole gateway, so the Gnani lane
    (one request at a time per key) and the TTS cache are shared:
      :PORT   aiohttp front (make_front): /api/sakhi/ws (Sakhi Direct,
              sakhi_ws.py) and a pass-through of every other request
      :INNER  api/index.py's real handler (the same code Vercel runs)
    """
    import asyncio
    import logging
    from http.server import ThreadingHTTPServer

    from aiohttp import web

    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "api"))
    from index import handler  # noqa: E402

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
    inner = ThreadingHTTPServer(("127.0.0.1", INNER), handler)
    threading.Thread(target=inner.serve_forever, daemon=True).start()

    # Sakhi Direct (sakhi_ws) pulls in livekit-agents + Silero: ~680 MB of
    # RAM. The app's check-in does not use it by default, so it is loaded on
    # the first /ws connection only; the gateway itself idles at ~25 MB.
    ws_mod = None
    ws_lock = asyncio.Lock()

    async def ws_handle(request: web.Request) -> web.StreamResponse:
        nonlocal ws_mod
        async with ws_lock:
            if ws_mod is None:
                ws_mod = await asyncio.to_thread(__import__, "sakhi_ws")
                await asyncio.to_thread(ws_mod._vad_session)
        return await ws_mod.handle(request)

    app = make_front(INNER, ws_handle)
    web.run_app(app, host="0.0.0.0", port=PORT, print=None, access_log=None)


def mtimes() -> dict[Path, float]:
    return {p: p.stat().st_mtime for p in WATCH if p.exists()}


def write_env_local(public: str | None) -> None:
    """app/.env.local: the gateway's addresses and its token, for the app (dev only)."""
    lines = ["# Written by scripts/sakhi_gateway_dev.py (dev only)\n"]
    if public:
        lines.append(f"EXPO_PUBLIC_SAKHI_GATEWAY={public}\n")
    lines += [
        "# The desktop browser (Expo web on localhost) uses this PC's gateway directly.\n",
        f"EXPO_PUBLIC_SAKHI_GATEWAY_LOCAL=http://localhost:{PORT}/api/sakhi\n",
        "# The gateway refuses requests without it. Development token only: an\n",
        "# EXPO_PUBLIC_ value is readable by anyone holding the app build.\n",
        f"EXPO_PUBLIC_SAKHI_GATEWAY_TOKEN={os.environ.get('SAKHI_GATEWAY_TOKEN', '')}\n",
    ]
    (ROOT / "app" / ".env.local").write_text("".join(lines), encoding="utf-8")


def main() -> None:
    # Live Sakhi in development goes to the LOCAL agent (scripts/sakhi_agent_dev.py),
    # under its own name, never to the production worker on the same LiveKit project.
    os.environ.setdefault("SAKHI_LIVE_AGENT_NAME", "sakhi-app-dev")
    os.environ.setdefault("SAKHI_LIVE_WORKER_URL", "http://localhost:8790")
    count = load_env(ROOT / ".env")
    # The gateway fails closed without a token: .env's, else this PC's dev token.
    from dev_token import ensure_dev_token

    ensure_dev_token()
    write_env_local(None)
    sys.path.insert(0, str(ROOT))
    import sakhi_gateway  # noqa: E402

    health = sakhi_gateway.health()
    print(f"  .env loaded ({count} values). Speech provider: {health['provider'] or 'NONE - set GNANI_API_KEY'}")
    print(f"  Live Sakhi agent: {sakhi_gateway.live_agent_name()}")

    def start_server() -> subprocess.Popen:
        return subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--serve"], env=os.environ.copy())

    server = start_server()
    print(f"  Gateway on http://localhost:{PORT}/api/sakhi")

    def watch() -> None:
        nonlocal server
        seen = mtimes()
        while True:
            time.sleep(1)
            now = mtimes()
            if now != seen:
                seen = now
                server.terminate()
                server.wait()
                server = start_server()
                print("  Gateway code changed: server restarted (same public URL).", flush=True)

    threading.Thread(target=watch, daemon=True).start()

    exe = "cloudflared.exe" if os.name == "nt" else "cloudflared"
    binary = ROOT / "app" / "node_modules" / "cloudflared" / "bin" / exe
    # The quick-tunnel hostname; NOT api.trycloudflare.com, which cloudflared
    # names in its own error messages when Cloudflare refuses a tunnel.
    url_re = re.compile(r"https://(?!api\.)[a-z0-9-]+\.trycloudflare\.com")
    try:
        for attempt in range(1, 6):
            tunnel = subprocess.Popen(
                [str(binary), "tunnel", "--no-autoupdate", "--url", f"http://localhost:{PORT}"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
            )
            assert tunnel.stdout is not None
            public = None
            for line in tunnel.stdout:
                if " ERR " in line or "error" in line.lower():
                    print(f"  cloudflared: {line.strip()[:200]}", flush=True)
                m = url_re.search(line)
                if m:
                    public = f"{m.group(0)}/api/sakhi"
                    break
            if public:
                write_env_local(public)
                print(f"  Public gateway: {public} (Sakhi Direct's WebSocket only)")
                print("  Phones reach the gateway through the app's own tunnel (npm run tunnel): /api/sakhi on Metro.", flush=True)
                # Keep reading cloudflared: a full, unread pipe blocks it and the tunnel stalls.
                for line in tunnel.stdout:
                    if " ERR " in line:
                        print(f"  cloudflared: {line.strip()[:200]}", flush=True)
                tunnel.wait()
                print("  Tunnel closed.", flush=True)
                break
            tunnel.wait()
            wait = 10 * attempt
            print(f"  Tunnel not created (attempt {attempt}); the desktop browser still works on localhost. Retrying in {wait} s.", flush=True)
            time.sleep(wait)
        # Keep serving localhost even without a tunnel.
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        server.terminate()


if __name__ == "__main__":
    if "--serve" in sys.argv:
        serve()
    else:
        main()
