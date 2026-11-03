"""Sakhi Direct server (sakhi_ws.py) and the gateway's network hardening.

Deterministic, no network, no provider keys: Silero is replaced by a fake
model (voiced = loud window), and the gateway's tts / stt by stubs.

  Listen       start / end / pause / cough / cap / trim rules on audio time
  protocol     over a real aiohttp WebSocket: hello, line audio framing,
               one turn, Next, Stop, stale seq, malformed commands, the
               watchdog when audio stops, the per-connection request cap,
               auth (header or query token) and the connection rate limit
  gateway      the token bucket, client identification, provider errors
               never leaking to the app
  dev front    pass-through keeps bytes, drops hop-by-hop headers, adds
               X-Forwarded-For, answers 502 (not a traceback) when down
"""

from __future__ import annotations

import asyncio
import importlib
import io
import json
import os
import sys
import unittest
import urllib.error
from pathlib import Path

import numpy as np
from aiohttp import WSMsgType, web
from aiohttp.test_utils import TestClient, TestServer

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import sakhi_gateway as gw  # noqa: E402
import sakhi_ws  # noqa: E402

SR = sakhi_ws.SR
WIN = sakhi_ws.WIN


class FakeModel:
    """Silero stand-in: a window is voice when it is loud."""

    def reset(self) -> None:
        pass

    def __call__(self, x: np.ndarray) -> float:
        return 0.95 if float(np.abs(x).mean()) > 0.02 else 0.02


def tone(seconds: float, amp: int = 3000) -> np.ndarray:
    return np.full(int(seconds * SR), amp, dtype=np.int16)


def quiet(seconds: float) -> np.ndarray:
    return np.zeros(int(seconds * SR), dtype=np.int16)


def feed(lst: sakhi_ws.Listen, *parts: np.ndarray, chunk: int = 320) -> list[str]:
    events: list[str] = []
    pcm = np.concatenate(parts)
    for i in range(0, len(pcm), chunk):
        e = lst.feed(pcm[i : i + chunk])
        if e:
            events.append(e)
            if e in ("end", "silence"):
                break
    return events


class ListenRules(unittest.TestCase):
    def make(self, hangover_ms=800, no_speech_ms=3000):
        return sakhi_ws.Listen(1, hangover_ms, no_speech_ms, FakeModel())

    def test_speech_then_end_after_hangover(self):
        lst = self.make()
        self.assertEqual(feed(lst, quiet(0.5), tone(1.0), quiet(2.0)), ["speech", "end"])

    def test_no_speech_is_silence(self):
        lst = self.make(no_speech_ms=2000)
        self.assertEqual(feed(lst, quiet(3.0)), ["silence"])

    def test_pause_inside_one_answer_stays_one_answer(self):
        lst = self.make(hangover_ms=850)
        events = feed(lst, quiet(0.3), tone(0.8), quiet(0.45), tone(0.8), quiet(2.0))
        self.assertEqual(events, ["speech", "end"])

    def test_cough_is_not_an_answer(self):
        lst = self.make(no_speech_ms=4000)
        # 0.1 s of noise then quiet: under 0.2 s of voice, dropped; still listening
        events = feed(lst, quiet(0.3), tone(0.1), quiet(1.5))
        self.assertNotIn("end", events)
        self.assertFalse(lst.hearing)
        # ...and a real answer afterwards is still heard
        self.assertEqual(feed(lst, tone(1.0), quiet(2.0)), ["speech", "end"])

    def test_answer_is_capped(self):
        lst = self.make()
        events = feed(lst, quiet(0.2), tone(sakhi_ws.MAX_ANSWER_S + 2))
        self.assertEqual(events, ["speech", "end"])

    def test_trim_keeps_preroll_and_a_short_tail(self):
        lst = self.make(hangover_ms=800)
        feed(lst, quiet(0.5), tone(1.0), quiet(2.0))
        pcm = np.frombuffer(lst.answer_pcm(force=False), dtype=np.int16)
        seconds = len(pcm) / SR
        # ~0.5 s pre-roll + 1.0 s voice + 0.25 s tail (window rounding)
        self.assertGreater(seconds, 1.4)
        self.assertLess(seconds, 1.9)


# ---- the WebSocket protocol ------------------------------------------------------


class Protocol(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._saved = (gw.tts, gw.stt, sakhi_ws.onnx_model.OnnxModel, sakhi_ws._vad_session, dict(gw.LIMITS))
        self.tts_calls: list[str] = []

        def fake_tts(text, lang, priority="now"):
            self.tts_calls.append(text)
            if text == "boom":
                raise RuntimeError("provider said: key=SECRET123 quota exceeded")
            return b"RIFF" + b"\0" * 1000

        gw.tts = fake_tts
        gw.stt = lambda wav, lang: {"text": "ಬಿ ಪಿ 148 ಬೈ 96", "en": "", "ms": 5}
        sakhi_ws.onnx_model.OnnxModel = lambda **kw: FakeModel()
        sakhi_ws._vad_session = lambda: None
        gw.LIMITS["ws"] = gw.RateLimiter(per_min=1000, burst=1000)
        # The gateway fails closed: every connection here is an authorised client.
        self._env_token = os.environ.get("SAKHI_GATEWAY_TOKEN")
        os.environ["SAKHI_GATEWAY_TOKEN"] = "t0k"
        app = web.Application()
        app.router.add_get("/ws", sakhi_ws.handle)
        self.client = TestClient(TestServer(app))
        await self.client.start_server()
        connect = self.client.ws_connect
        self.client.ws_connect = lambda path, **kw: connect(path, headers={"Authorization": "Bearer t0k", **kw.pop("headers", {})}, **kw)

    async def asyncTearDown(self):
        await self.client.close()
        if self._env_token is None:
            os.environ.pop("SAKHI_GATEWAY_TOKEN", None)
        else:
            os.environ["SAKHI_GATEWAY_TOKEN"] = self._env_token
        gw.tts, gw.stt, sakhi_ws.onnx_model.OnnxModel, sakhi_ws._vad_session, limits = self._saved
        gw.LIMITS.clear()
        gw.LIMITS.update(limits)

    async def connect(self):
        ws = await self.client.ws_connect("/ws")
        await ws.send_str(json.dumps({"t": "hello", "lang": "kn", "lines": []}))
        self.assertEqual((await self.next_json(ws))["t"], "ready")
        return ws

    async def next_json(self, ws, timeout=5.0):
        while True:
            m = await asyncio.wait_for(ws.receive(), timeout)
            if m.type == WSMsgType.TEXT:
                return json.loads(m.data)
            if m.type in (WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.ERROR):
                raise AssertionError(f"socket closed: {m}")

    async def stream(self, ws, *parts, chunk=320):
        pcm = np.concatenate(parts)
        for i in range(0, len(pcm), chunk):
            await ws.send_bytes(pcm[i : i + chunk].tobytes())

    async def test_line_audio_is_a_header_then_exactly_its_bytes(self):
        ws = await self.connect()
        await ws.send_str(json.dumps({"t": "tts", "id": 7, "text": "Q1"}))
        head = await self.next_json(ws)
        self.assertEqual((head["t"], head["id"]), ("audio", 7))
        body = await asyncio.wait_for(ws.receive(), 5)
        self.assertEqual(body.type, WSMsgType.BINARY)
        self.assertEqual(len(body.data), head["bytes"])
        self.assertEqual(body.data[:4], b"RIFF")

    async def test_provider_failure_never_leaks_its_details(self):
        ws = await self.connect()
        await ws.send_str(json.dumps({"t": "tts", "id": 1, "text": "boom"}))
        err = await self.next_json(ws)
        self.assertEqual(err["t"], "audioError")
        self.assertNotIn("SECRET123", json.dumps(err))

    async def test_one_turn_speech_then_heard(self):
        ws = await self.connect()
        await ws.send_str(json.dumps({"t": "listen", "seq": 3, "hangoverMs": 800, "noSpeechMs": 5000}))
        await self.stream(ws, quiet(0.4), tone(1.0), quiet(1.5))
        self.assertEqual(await self.next_json(ws), {"t": "speech", "seq": 3})
        heard = await self.next_json(ws)
        self.assertEqual((heard["t"], heard["seq"], heard["text"]), ("heard", 3, "ಬಿ ಪಿ 148 ಬೈ 96"))

    async def test_next_ends_the_answer_now_and_stale_next_is_ignored(self):
        ws = await self.connect()
        await ws.send_str(json.dumps({"t": "listen", "seq": 4, "hangoverMs": 800, "noSpeechMs": 5000}))
        await self.stream(ws, quiet(0.3), tone(0.8))
        self.assertEqual((await self.next_json(ws))["t"], "speech")
        await ws.send_str(json.dumps({"t": "next", "seq": 3}))  # stale: ignored
        await ws.send_str(json.dumps({"t": "next", "seq": 4}))
        heard = await self.next_json(ws)
        self.assertEqual((heard["t"], heard["seq"]), ("heard", 4))

    async def test_next_with_nothing_said_is_a_skip(self):
        ws = await self.connect()
        await ws.send_str(json.dumps({"t": "listen", "seq": 5, "hangoverMs": 800, "noSpeechMs": 5000}))
        await self.stream(ws, quiet(0.3))
        await ws.send_str(json.dumps({"t": "next", "seq": 5}))
        self.assertEqual(await self.next_json(ws), {"t": "silence", "seq": 5, "skipped": True})

    async def test_stop_cancels_and_later_audio_belongs_to_nothing(self):
        ws = await self.connect()
        await ws.send_str(json.dumps({"t": "listen", "seq": 6, "hangoverMs": 800, "noSpeechMs": 5000}))
        await ws.send_str(json.dumps({"t": "stop"}))
        await self.stream(ws, tone(1.0), quiet(1.5))
        await ws.send_str(json.dumps({"t": "ping", "at": 1}))
        self.assertEqual(await self.next_json(ws), {"t": "pong", "at": 1})  # nothing about seq 6

    async def test_malformed_commands_never_close_the_connection(self):
        ws = await self.connect()
        await ws.send_str("not json")
        await ws.send_str("[1, 2]")
        await ws.send_str(json.dumps({"t": "listen", "seq": "abc"}))
        err = await self.next_json(ws)
        self.assertEqual(err["t"], "error")
        await ws.send_str(json.dumps({"t": "ping", "at": 2}))
        self.assertEqual(await self.next_json(ws), {"t": "pong", "at": 2})

    async def test_watchdog_ends_the_turn_when_audio_stops_coming(self):
        ws = await self.connect()
        await ws.send_str(json.dumps({"t": "listen", "seq": 8, "hangoverMs": 800, "noSpeechMs": 1000}))
        # no audio at all: 2.0 s (minimum no-speech) + 1.5 s backstop
        msg = await self.next_json(ws, timeout=6.0)
        self.assertEqual((msg["t"], msg["seq"]), ("silence", 8))

    async def test_listen_before_hello_is_refused(self):
        ws = await self.client.ws_connect("/ws")
        await ws.send_str(json.dumps({"t": "listen", "seq": 1}))
        self.assertEqual((await self.next_json(ws))["t"], "error")

    async def test_too_many_open_line_requests_are_refused(self):
        ws = await self.connect()
        conv_cap = sakhi_ws.MAX_TTS_INFLIGHT

        def blocking_tts(text, lang, priority="now"):
            import time

            time.sleep(0.5)  # the provider is slow: requests pile up
            return b"RIFF" + bytes(10)

        gw.tts = blocking_tts
        for i in range(conv_cap + 3):
            await ws.send_str(json.dumps({"t": "tts", "id": i, "text": f"L{i}"}))
        busy = 0
        for _ in range(conv_cap + 3):
            m = await self.next_json(ws, timeout=10)
            if m["t"] == "audioError" and m["message"] == "busy":
                busy += 1
            elif m["t"] == "audio":
                await ws.receive()  # its binary
        self.assertEqual(busy, 3)


class Auth(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import os

        self._env = os.environ.get("SAKHI_GATEWAY_TOKEN")
        os.environ["SAKHI_GATEWAY_TOKEN"] = "t0k"
        self._limits = dict(gw.LIMITS)
        app = web.Application()
        app.router.add_get("/ws", sakhi_ws.handle)
        self.client = TestClient(TestServer(app))
        await self.client.start_server()

    async def asyncTearDown(self):
        import os

        await self.client.close()
        if self._env is None:
            os.environ.pop("SAKHI_GATEWAY_TOKEN", None)
        else:
            os.environ["SAKHI_GATEWAY_TOKEN"] = self._env
        gw.LIMITS.clear()
        gw.LIMITS.update(self._limits)

    async def test_token_required_header_or_query(self):
        gw.LIMITS["ws"] = gw.RateLimiter(per_min=1000, burst=1000)
        r = await self.client.get("/ws")
        self.assertEqual(r.status, 401)
        ws = await self.client.ws_connect("/ws", headers={"Authorization": "Bearer t0k"})
        await ws.close()
        ws = await self.client.ws_connect("/ws?token=t0k")
        await ws.close()

    async def test_connection_rate_limit(self):
        gw.LIMITS["ws"] = gw.RateLimiter(per_min=1, burst=2)
        for _ in range(2):
            ws = await self.client.ws_connect("/ws", headers={"Authorization": "Bearer t0k"})
            await ws.close()
        r = await self.client.get("/ws", headers={"Authorization": "Bearer t0k"})
        self.assertEqual(r.status, 429)


# ---- gateway ---------------------------------------------------------------------------


class GatewayHardening(unittest.TestCase):
    def test_token_bucket_burst_then_refill(self):
        lim = gw.RateLimiter(per_min=60, burst=3)
        self.assertEqual([lim.allow("a", now=0.0) for _ in range(4)], [True, True, True, False])
        self.assertTrue(lim.allow("b", now=0.0))  # per client
        self.assertTrue(lim.allow("a", now=1.0))  # 1 token per second refills
        self.assertFalse(lim.allow("a", now=1.0))

    def test_bucket_memory_is_bounded(self):
        lim = gw.RateLimiter(per_min=60, burst=1, max_clients=10)
        for i in range(100):
            lim.allow(f"c{i}", now=0.0)
        self.assertLessEqual(len(lim._buckets), 10)

    def test_client_identity_prefers_cloudflare_then_forwarded(self):
        self.assertEqual(gw.client_id({"CF-Connecting-IP": "1.1.1.1", "X-Forwarded-For": "2.2.2.2"}, "127.0.0.1"), "1.1.1.1")
        self.assertEqual(gw.client_id({"X-Forwarded-For": "2.2.2.2, 10.0.0.1"}, "127.0.0.1"), "2.2.2.2")
        self.assertEqual(gw.client_id({}, "127.0.0.1"), "127.0.0.1")

    def test_provider_error_body_stays_on_the_server(self):
        original = gw.urllib.request.urlopen

        def refuse(*a, **k):
            raise urllib.error.HTTPError("https://api.example/stt", 401, "no", {}, io.BytesIO(b'{"error":"invalid key sk-LEAK-123 for account 42"}'))

        gw.urllib.request.urlopen = refuse
        try:
            with self.assertRaises(gw.GatewayError) as ctx:
                gw._request("https://api.example/stt", b"", {}, 1.0)
        finally:
            gw.urllib.request.urlopen = original
        self.assertEqual(ctx.exception.status, 502)
        self.assertNotIn("LEAK", ctx.exception.message)
        self.assertNotIn("account", ctx.exception.message)

    def test_unreachable_provider_is_504_without_details(self):
        original = gw.urllib.request.urlopen

        def down(*a, **k):
            raise urllib.error.URLError("getaddrinfo failed for internal-host.corp")

        gw.urllib.request.urlopen = down
        try:
            with self.assertRaises(gw.GatewayError) as ctx:
                gw._request("https://x", b"", {}, 1.0)
        finally:
            gw.urllib.request.urlopen = original
        self.assertEqual(ctx.exception.status, 504)
        self.assertNotIn("internal-host", ctx.exception.message)


# ---- the dev front's pass-through -------------------------------------------------------


class DevFront(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        dev = importlib.import_module("sakhi_gateway_dev")
        self.seen: dict = {}

        async def inner(request: web.Request):
            self.seen = dict(request.headers)
            body = await request.read()
            return web.Response(body=b"\x1f\x8b-gzip-bytes" + body, headers={"Content-Encoding": "gzip", "X-Kept": "1", "Connection": "keep-alive"})

        inner_app = web.Application()
        inner_app.router.add_route("*", "/{tail:.*}", inner)
        self.inner = TestServer(inner_app)
        await self.inner.start_server()
        self.front = TestClient(TestServer(dev.make_front(self.inner.port)), auto_decompress=False)
        await self.front.start_server()

    async def asyncTearDown(self):
        await self.front.close()
        await self.inner.close()

    async def test_bytes_and_encoding_pass_through_untouched(self):
        r = await self.front.post("/api/sakhi/stt?lang=kn", data=b"RIFFwav")
        self.assertEqual(r.status, 200)
        self.assertEqual(await r.read(), b"\x1f\x8b-gzip-bytesRIFFwav")
        self.assertEqual(r.headers.get("Content-Encoding"), "gzip")
        self.assertEqual(r.headers.get("X-Kept"), "1")

    async def test_caller_is_forwarded_for_rate_limits(self):
        await self.front.get("/api/sakhi/health")
        self.assertIn("X-Forwarded-For", self.seen)

    async def test_inner_down_is_502_json_not_a_traceback(self):
        await self.inner.close()
        r = await self.front.get("/api/sakhi/health")
        self.assertEqual(r.status, 502)
        self.assertIn("error", await r.json())


if __name__ == "__main__":
    unittest.main()
