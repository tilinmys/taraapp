"""Sakhi Direct: the voice of one check-in over ONE WebSocket.

    wss://<gateway>/api/sakhi/ws

Why: Expo Go has no native WebRTC, and a hidden WebView is one more moving
part. A WebSocket is built into React Native: the app keeps one connection
open for the whole check-in and every turn is a couple of messages on it.

Roles (the same split as Live Sakhi, docs/VOICE_IN_APP_DESIGN.md 10/11):
  the APP (voice/engine.ts) is the single state manager: which question,
      what an answer means, flags, records. It plays Sakhi's lines and
      streams the microphone ONLY while Sakhi listens (half-duplex: no echo,
      and on iOS a recording session mutes playback anyway).
  this SERVER is the ears and the mouth: Gnani's voice (Kaveri) for lines,
      Silero VAD on the streamed audio to find where the answer starts and
      ends, the self-hosted Indic STT model for the words (sakhi_gateway.stt).
      Provider keys never leave it.

Protocol. Text frames are JSON; binary frames are audio.
  app -> server
    {"t":"hello","lang":"kn","lines":[...]}   -> {"t":"ready"}; then every
                                                 line is synthesized in
                                                 speaking order (warm)
    {"t":"tts","id":7,"text":"..."}           -> {"t":"audio","id":7,"bytes":n}
                                                 + ONE binary frame (WAV)
    {"t":"listen","seq":3,"hangoverMs":800,"noSpeechMs":7000}
       then binary frames: PCM16 LE, 16 kHz, mono (20 ms each)
                                              -> {"t":"speech","seq":3}
                                              -> {"t":"heard","seq":3,"text":..,"sttMs":..}
                                                 | {"t":"silence","seq":3,"skipped":false}
    {"t":"next","seq":3}                      the ASHA's Next: end the answer now
    {"t":"stop"}                              cancel the listen (Repeat, exit)
    {"t":"ping","at":..}                      -> {"t":"pong","at":..}
  Every reply carries the seq / id it answers: the app drops stale ones.

Listening (one answer):
  * Silero on 32 ms windows. Speech STARTS at 3 voiced windows out of the
    last 5 (~100 ms): a click or a cough is not an answer. A 0.5 s pre-roll
    ring keeps the first syllable.
  * It ENDS after `hangoverMs` (at least 450 ms) without voice: pauses
    inside one answer ("148 ... by 96") stay one answer.
  * Speech shorter than 200 ms is dropped and listening goes on.
  * No speech within `noSpeechMs` -> silence; 25 s cap per answer.
  * Time is counted in AUDIO (samples received), never wall clock, so a
    slow network cannot cut an answer short. A wall-clock watchdog is only
    the backstop for audio that STOPS coming (the phone's mic failed, the
    app went away): the turn still ends, so the conversation never hangs.
  The answer is trimmed to 250 ms after the last voiced window and sent to
  STT (retried on busy / network errors).
"""

from __future__ import annotations

import asyncio
import collections
import io
import json
import logging
import os
import time
import wave

import numpy as np
from aiohttp import WSMsgType, web

# Imported here, at module load: livekit registers its plugins on the main
# thread only (the ONNX session itself may then be created anywhere).
from livekit.plugins.silero import onnx_model

import sakhi_gateway as gw

log = logging.getLogger("sakhi-ws")

SR = 16000
WIN = 512  # Silero window at 16 kHz (32 ms)
WIN_S = WIN / SR
VOICED = 0.5  # speech probability that counts as voice
START_OF = (3, 5)  # 3 voiced windows of the last 5
PRE_ROLL_S = 0.5
MIN_SPEECH_S = 0.2
MIN_HANGOVER_S = 0.45
TAIL_S = 0.25
MAX_ANSWER_S = 25.0
PING_S = 20.0
MAX_TTS_INFLIGHT = 32  # line requests one connection may have open at once
# Transcripts are health data: only their length is logged unless asked for.
LOG_TRANSCRIPTS = os.getenv("SAKHI_LOG_TRANSCRIPTS") == "1"
AUDIO_GAP_S = 1.5  # no audio for this long while hearing: end the answer

_session = None


def _vad_session():
    """One ONNX session per process; each connection keeps its own RNN state."""
    global _session
    if _session is None:
        _session = onnx_model.new_inference_session(force_cpu=True)
    return _session


def _wav(pcm: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm)
    return buf.getvalue()


class Listen:
    """One answer being heard: VAD state, audio and the rules above."""

    def __init__(self, seq: int, hangover_ms: int, no_speech_ms: int, model) -> None:
        self.seq = seq
        self.hangover = max(MIN_HANGOVER_S, hangover_ms / 1000)
        self.no_speech = max(2.0, no_speech_ms / 1000)
        self.model = model
        model.reset()
        self.pending = np.zeros(0, dtype=np.int16)  # samples not yet in a window
        self.t = 0.0  # audio seconds received
        self.recent: collections.deque[bool] = collections.deque(maxlen=START_OF[1])
        self.pre: collections.deque[np.ndarray] = collections.deque(maxlen=int(PRE_ROLL_S / WIN_S) + 1)
        self.audio: list[np.ndarray] = []
        self.started_at: float | None = None
        self.audio_t0 = 0.0  # audio time where self.audio begins (pre-roll included)
        self.voiced_s = 0.0
        self.last_voice = 0.0
        self.created = time.monotonic()
        self.last_rx = self.created  # wall clock of the last audio received

    @property
    def hearing(self) -> bool:
        return self.started_at is not None

    def feed(self, pcm: np.ndarray) -> str | None:
        """New audio. Returns 'speech', 'end', 'silence' or None."""
        self.last_rx = time.monotonic()
        self.pending = np.concatenate([self.pending, pcm]) if self.pending.size else pcm
        event = None
        while self.pending.size >= WIN:
            win, self.pending = self.pending[:WIN], self.pending[WIN:]
            self.t += WIN_S
            voiced = self.model(win.astype(np.float32) / 32768.0) >= VOICED
            self.recent.append(voiced)
            if not self.hearing:
                self.pre.append(win)
                if sum(self.recent) >= START_OF[0]:
                    self.started_at = self.t - len(self.recent) * WIN_S
                    self.audio_t0 = self.t - len(self.pre) * WIN_S
                    self.audio = list(self.pre)
                    self.voiced_s = sum(self.recent) * WIN_S
                    self.last_voice = self.t
                    event = "speech"
                elif self.t >= self.no_speech:
                    return "silence"
                continue
            self.audio.append(win)
            if voiced:
                self.voiced_s += WIN_S
                self.last_voice = self.t
            elif self.t - self.last_voice >= self.hangover:
                if self.voiced_s < MIN_SPEECH_S:
                    # A click, a cough: not an answer. Keep listening.
                    self.started_at = None
                    self.audio = []
                    self.recent.clear()
                    continue
                return "end"
            if self.t - self.started_at >= MAX_ANSWER_S:
                return "end"
        return event

    def answer_pcm(self, force: bool) -> bytes:
        if not self.audio:
            return b""
        pcm = np.concatenate(self.audio)
        if not force:
            # Up to TAIL_S after the last voiced window: no trailing silence.
            end_s = self.last_voice + TAIL_S - self.audio_t0
            pcm = pcm[: max(WIN, int(end_s * SR))]
        return pcm.tobytes()


class Conversation:
    def __init__(self, ws: web.WebSocketResponse) -> None:
        self.ws = ws
        self.lang = "kn"
        self.send_lock = asyncio.Lock()
        self.model = None
        self.listen: Listen | None = None
        self.tasks: set[asyncio.Task] = set()
        self.warm_task: asyncio.Task | None = None
        self.tts_inflight = 0

    # ---- output -------------------------------------------------------------

    async def send(self, msg: dict, binary: bytes | None = None) -> None:
        if self.ws.closed:
            return
        # Header and its audio go out back to back: the next binary frame
        # the app receives IS the audio this header announces.
        async with self.send_lock:
            await self.ws.send_str(json.dumps(msg, ensure_ascii=False))
            if binary is not None:
                await self.ws.send_bytes(binary)

    def spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self._done)

    def _done(self, task: asyncio.Task) -> None:
        self.tasks.discard(task)
        if not task.cancelled() and task.exception():
            log.error("task failed: %r", task.exception())

    # ---- commands -------------------------------------------------------------

    async def on_text(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except ValueError:
            return
        if not isinstance(msg, dict):
            return
        try:
            await self.command(msg)
        except (TypeError, ValueError) as e:
            # A malformed command never takes the connection down.
            log.warning("bad command %r: %s", msg.get("t"), e)
            await self.send({"t": "error", "seq": msg.get("seq"), "message": "bad command"})

    async def command(self, msg: dict) -> None:
        t = msg.get("t")
        if t == "hello":
            lang = str(msg.get("lang") or "kn")
            self.lang = lang if lang in ("kn", "hi", "en") else "kn"
            if self.model is None:
                self.model = onnx_model.OnnxModel(onnx_session=_vad_session(), sample_rate=SR)
            await self.send({"t": "ready", "provider": gw.provider()})
            lines = [str(x) for x in (msg.get("lines") or []) if str(x).strip()][:60]
            if self.warm_task is None and lines:
                self.warm_task = asyncio.create_task(self.warm(lines))
        elif t == "tts":
            if self.tts_inflight >= MAX_TTS_INFLIGHT:
                await self.send({"t": "audioError", "id": msg.get("id"), "message": "busy"})
                return
            self.tts_inflight += 1
            self.spawn(self.tts(msg.get("id"), str(msg.get("text") or ""), str(msg.get("prio") or "now")))
        elif t == "listen":
            if self.model is None:
                await self.send({"t": "error", "seq": msg.get("seq"), "message": "say hello first"})
                return
            self.listen = Listen(int(msg.get("seq") or 0), int(msg.get("hangoverMs") or 800), int(msg.get("noSpeechMs") or 7000), self.model)
            log.info("listen #%s (hangover %.2f s)", self.listen.seq, self.listen.hangover)
            self.spawn(self.watchdog(self.listen))
        elif t == "next":
            lst = self.listen
            if lst and lst.seq == msg.get("seq"):
                await self.finish(lst, force=True)
        elif t == "stop":
            self.listen = None
        elif t == "ping":
            await self.send({"t": "pong", "at": msg.get("at")})

    async def warm(self, lines: list[str]) -> None:
        """Every line in speaking order, so each 'tts' later is a cache hit."""
        t0 = time.perf_counter()
        for text in lines:
            if self.ws.closed:
                return
            try:
                await asyncio.to_thread(gw.tts, text, self.lang, "warm")
            except Exception as e:  # noqa: BLE001 - a line that fails is fetched again on demand
                log.warning("warm failed: %s", e)
        log.info("warm: %d lines ready in %.1f s", len(lines), time.perf_counter() - t0)

    async def tts(self, rid, text: str, prio: str) -> None:
        t0 = time.perf_counter()
        try:
            audio = await asyncio.to_thread(gw.tts, text, self.lang, prio if prio in ("now", "next", "warm") else "now")
        except gw.GatewayError as e:
            await self.send({"t": "audioError", "id": rid, "message": e.message})
            return
        except Exception as e:  # noqa: BLE001 - details stay in the server log
            log.warning("tts failed: %r", e)
            await self.send({"t": "audioError", "id": rid, "message": "voice unavailable"})
            return
        finally:
            self.tts_inflight -= 1
        ms = round((time.perf_counter() - t0) * 1000)
        await self.send({"t": "audio", "id": rid, "bytes": len(audio), "ms": ms}, audio)

    # ---- listening --------------------------------------------------------------

    async def on_audio(self, data: bytes) -> None:
        lst = self.listen
        if lst is None or len(data) < 2:
            return
        event = lst.feed(np.frombuffer(data[: len(data) // 2 * 2], dtype=np.int16))
        if event == "speech":
            await self.send({"t": "speech", "seq": lst.seq})
        elif event == "end":
            await self.finish(lst, force=False)
        elif event == "silence":
            self.listen = None
            log.info("silence #%s", lst.seq)
            await self.send({"t": "silence", "seq": lst.seq, "skipped": False})

    async def watchdog(self, lst: Listen) -> None:
        """Backstop when audio stops arriving: the turn still ends."""
        while self.listen is lst:
            await asyncio.sleep(0.25)
            if self.listen is not lst:
                return
            now = time.monotonic()
            if not lst.hearing and now - lst.created > lst.no_speech + AUDIO_GAP_S:
                log.info("silence #%s (no audio from the app)", lst.seq)
                self.listen = None
                await self.send({"t": "silence", "seq": lst.seq, "skipped": False})
                return
            if lst.hearing and now - lst.last_rx > AUDIO_GAP_S:
                log.info("answer #%s ended: audio stopped coming", lst.seq)
                await self.finish(lst, force=True)
                return

    async def finish(self, lst: Listen, force: bool) -> None:
        if self.listen is not lst:
            return
        self.listen = None  # audio from now on belongs to no answer
        if not lst.hearing:
            await self.send({"t": "silence", "seq": lst.seq, "skipped": force})
            return
        pcm = lst.answer_pcm(force)
        self.spawn(self.transcribe(lst.seq, pcm, lst.voiced_s))

    async def transcribe(self, seq: int, pcm: bytes, voiced_s: float) -> None:
        wav = _wav(pcm)
        t0 = time.perf_counter()
        text, last = "", None
        for attempt in range(3):
            try:
                out = await asyncio.to_thread(gw.stt, wav, self.lang)
                text = (out.get("text") or "").strip()
                last = None
                break
            except gw.GatewayError as e:
                last = e
                if e.status not in (429, 502, 503, 504):
                    break
            except Exception as e:  # noqa: BLE001 - network
                last = e
            await asyncio.sleep(0.25 * (attempt + 1))
        ms = round((time.perf_counter() - t0) * 1000)
        if last is not None:
            log.warning("stt failed #%s: %s", seq, last)
            await self.send({"t": "error", "seq": seq, "message": f"stt: {last}"})
            return
        log.info("heard #%s in %d ms (%.1f s audio, %.1f s voice): %s", seq, ms, len(pcm) / 2 / SR, voiced_s, text if LOG_TRANSCRIPTS else f"{len(text)} chars")
        if text:
            await self.send({"t": "heard", "seq": seq, "text": text, "sttMs": ms})
        else:
            await self.send({"t": "silence", "seq": seq, "skipped": False})

    async def close(self) -> None:
        self.listen = None
        for task in [*self.tasks, *( [self.warm_task] if self.warm_task else [])]:
            task.cancel()


async def handle(request: web.Request) -> web.StreamResponse:
    # Phones send the token as a header; a browser cannot set headers on a
    # WebSocket, so it may use ?token= (never logged: access logs are off).
    if not gw.authorized(request.headers) and not gw.authorized({"Authorization": f"Bearer {request.query.get('token', '')}"}):
        return web.json_response({"error": "unauthorized"}, status=401)
    if not gw.rate_ok("ws", request.headers, request.remote):
        return web.json_response({"error": "too many connections"}, status=429, headers={"Retry-After": "10"})
    ws = web.WebSocketResponse(heartbeat=PING_S, max_msg_size=4 * 1024 * 1024, compress=False)
    await ws.prepare(request)
    conv = Conversation(ws)
    peer = request.headers.get("CF-Connecting-IP") or request.remote
    log.info("connected (%s)", peer)
    try:
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                await conv.on_text(msg.data)
            elif msg.type == WSMsgType.BINARY:
                await conv.on_audio(msg.data)
            elif msg.type == WSMsgType.ERROR:
                log.warning("socket error: %s", ws.exception())
    finally:
        await conv.close()
        log.info("closed (%s)", peer)
    return ws
