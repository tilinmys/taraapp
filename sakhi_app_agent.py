"""Live Sakhi for the tara app: the VOICE EXECUTOR on the server side.

    python scripts/sakhi_agent_dev.py        (development, this PC)

WHY A SEPARATE, SMALL AGENT
---------------------------
The phone-call agent (agent.py) lets an LLM write every sentence Sakhi says.
In the app that showed up as two questions in one turn, invented wording and,
after Gnani's streaming STT closed on 60 s of silence, a silent fallback to an
English-first recogniser. The app does not need any of that: its check-in is
a fixed questionnaire with deterministic rules (app/src/voice/engine.ts,
classify.ts). So the APP is the state manager and this agent only does what
the app tells it, one step at a time, over WebRTC:

    app -> agent   {t:"hello", lang, lines}        pre-synthesize every line
                   {t:"say", seq, text, listen}    speak ONE line, then listen
                   {t:"next", seq}                  end the answer now
                   {t:"stop"}                       cancel whatever is running
    agent -> app   {t:"ready"}  {t:"said", seq}  {t:"speech", seq}
                   {t:"heard", seq, text, ms, speechMs}  {t:"silence", seq}
                   {t:"error", seq, message}

Every reply carries the seq of the command it answers; the app drops any
reply whose seq is not the one it is waiting for, so a late result can never
land on the wrong question.

HOW IT LISTENS (the STT that the call agent got wrong)
-------------------------------------------------------
  * Silero VAD (neural) on the ASHA's audio, 16 kHz, decides speech / silence.
    Robust to fans, traffic and children in the room, unlike an energy VAD.
  * End of answer = silence for the question's hangover (the app sends it:
    longer for numbers and descriptions). A pause shorter than that is NOT
    the end: the two parts are merged into one answer.
  * The whole answer is transcribed in ONE request (Gnani REST, format=
    transcribe: Kannada / Hindi, numbers as digits "148 ಬೈ 96"). Full context
    is more accurate than word-by-word streaming, there is no WebSocket to
    time out, and no fallback to an English model.
  * Half-duplex: while Sakhi speaks, the ASHA's audio is not used (no echo,
    no talking over her own question).

ONE PROVIDER LANE
-----------------
Gnani allows one request at a time per key. This agent never calls Gnani
itself: TTS and STT go through the Sakhi gateway (sakhi_gateway.py), whose
single priority lane serves STT first, then the line being said now, then
pre-synthesis. Its TTS cache is shared with the app's offline mode too.
"""

from __future__ import annotations

import os

# Before numpy is imported: its BLAS reserves memory per CPU thread (~1 GB of
# commit on an 8-core Windows PC). This agent does no matrix maths (Silero
# runs in onnxruntime), so one thread costs nothing and saves ~900 MB per process.
for _var in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_var, "1")

import asyncio
import collections
import contextlib
import io
import json
import logging
import time
import wave
from urllib.parse import quote

from pathlib import Path

import aiohttp
from livekit import rtc
from livekit.agents import AutoSubscribe, JobContext, JobExecutorType, JobProcess, WorkerOptions, cli
from livekit.agents import vad as lkvad
from livekit.plugins import silero

logger = logging.getLogger("sakhi-app-agent")

GATEWAY = os.getenv("SAKHI_GATEWAY_INTERNAL", "http://localhost:8787/api/sakhi").rstrip("/")
GATEWAY_TOKEN = os.getenv("SAKHI_GATEWAY_TOKEN", "")
TOPIC = "sakhi.app"
SR = 16000
FRAME = SR // 50  # 20 ms
VAD_SILENCE_S = 0.30  # Silero's own end-of-segment; the app's hangover is on top
PRE_ROLL_S = 0.30
MAX_ANSWER_S = 25.0
TAIL_FRAMES = 15  # keep ~150 ms after the last voiced frame (10 ms frames)
# Sakhi's recorded lines (scripts/prerecord_sakhi.py): the same files the app
# ships. A line found here is played straight from memory: no TTS request,
# no wait. Only a line without a recording goes to the gateway's TTS.
RECORDINGS_DIR = Path(os.getenv("SAKHI_RECORDINGS_DIR", str(Path(__file__).resolve().parent / "app" / "assets" / "voice")))


def _key(text: str) -> str:
    """Lines match whatever the whitespace (the app trims, the manifest keeps)."""
    return " ".join(text.split())


def load_recordings(lang: str) -> dict[str, tuple[bytes, int]]:
    """Every recorded line of `lang`, text -> (PCM, rate). ~5 MB per language."""
    out: dict[str, tuple[bytes, int]] = {}
    try:
        manifest = json.loads((RECORDINGS_DIR / "manifest.json").read_text(encoding="utf-8"))
    except Exception:
        logger.warning("no recordings at %s: every line goes through TTS", RECORDINGS_DIR)
        return out
    for entry in (manifest.get("lines", {}).get(lang) or {}).values():
        with contextlib.suppress(Exception):
            out[_key(entry["text"])] = _wav_to_pcm((RECORDINGS_DIR / entry["file"]).read_bytes())
    return out


def _headers(priority: str | None = None) -> dict[str, str]:
    h = {"User-Agent": "tara-sakhi-app-agent/1.0"}
    if GATEWAY_TOKEN:
        h["Authorization"] = f"Bearer {GATEWAY_TOKEN}"
    if priority:
        h["X-Sakhi-Priority"] = priority
    return h


def _wav_to_pcm(data: bytes) -> tuple[bytes, int]:
    with wave.open(io.BytesIO(data), "rb") as w:
        if w.getsampwidth() != 2 or w.getnchannels() != 1:
            raise ValueError("expected 16-bit mono WAV")
        return w.readframes(w.getnframes()), w.getframerate()


class Voice:
    """Sakhi's lines as PCM, from the gateway (cached there and here)."""

    def __init__(self, http: aiohttp.ClientSession, lang: str, recorded: dict[str, tuple[bytes, int]] | None = None):
        self.http = http
        self.lang = lang
        self.recorded = recorded or {}
        self.cache: dict[str, tuple[bytes, int]] = {}
        self.inflight: dict[str, asyncio.Future] = {}

    def has(self, text: str) -> bool:
        return _key(text) in self.recorded or text in self.cache

    async def pcm(self, text: str, priority: str = "now") -> tuple[bytes, int]:
        rec = self.recorded.get(_key(text))
        if rec is not None:
            return rec
        if text in self.cache:
            return self.cache[text]
        if text in self.inflight:
            return await self.inflight[text]
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self.inflight[text] = fut
        try:
            url = f"{GATEWAY}/tts?lang={self.lang}&text={quote(text)}"
            t0 = time.perf_counter()
            async with self.http.get(url, headers=_headers(priority), timeout=aiohttp.ClientTimeout(total=20)) as r:
                if r.status != 200:
                    raise RuntimeError(f"tts HTTP {r.status}")
                out = _wav_to_pcm(await r.read())
            logger.info("tts %s %d ms %d B '%s'", priority, (time.perf_counter() - t0) * 1000, len(out[0]), text[:30])
            self.cache[text] = out
            fut.set_result(out)
            return out
        except Exception as exc:
            fut.set_exception(exc)
            raise
        finally:
            self.inflight.pop(text, None)

    async def warm(self, lines: list[str]) -> None:
        """Pre-synthesize, in speaking order and one at a time (lowest
        priority), only the lines that have no recording."""
        todo = [t for t in lines if not self.has(t)]
        logger.info("warm: %d line(s) recorded, %d to synthesize", len(lines) - len(todo), len(todo))
        for text in todo:
            with contextlib.suppress(Exception):
                await self.pcm(text, "warm")


class Ears:
    """One complete answer -> text, through the gateway's STT."""

    def __init__(self, http: aiohttp.ClientSession, lang: str):
        self.http = http
        self.lang = lang

    async def transcribe(self, frames: list[rtc.AudioFrame]) -> tuple[str, int]:
        wav = rtc.combine_audio_frames(frames).to_wav_bytes()
        t0 = time.perf_counter()
        last: Exception | None = None
        for attempt in range(3):
            try:
                async with self.http.post(
                    f"{GATEWAY}/stt?lang={self.lang}",
                    data=wav,
                    headers={**_headers(), "Content-Type": "audio/wav"},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as r:
                    if r.status == 200:
                        body = await r.json()
                        total = round((time.perf_counter() - t0) * 1000)
                        logger.info("stt %d ms total (provider %s ms, %.1f s audio)", total, body.get("ms"), len(wav) / 2 / SR)
                        return (body.get("text") or "").strip(), total
                    last = RuntimeError(f"stt HTTP {r.status}")
                    if r.status < 500 and r.status != 429:
                        break
            except Exception as exc:  # network
                last = exc
            await asyncio.sleep(0.3 * (attempt + 1))
        raise last or RuntimeError("stt failed")


class Executor:
    """Speak one line, then listen for one answer. State is explicit:

        idle --say--> speaking --(done, listen)--> listening --speech--> hearing
        hearing --pause < hangover--> hearing (merged)
        hearing --pause >= hangover | next--> thinking --stt--> idle  (heard)
        listening --no speech in time--> idle (silence)
        any --stop / new say--> (cancelled: the old seq is dead)
    """

    def __init__(self, room: rtc.Room, vad: lkvad.VAD, lang: str, http: aiohttp.ClientSession, recorded: dict | None = None):
        self.room = room
        self.vad = vad
        self.voice = Voice(http, lang, recorded)
        self.ears = Ears(http, lang)
        self.source = rtc.AudioSource(SR, 1, queue_size_ms=400)
        self.state = "idle"
        self.seq = -1
        self.task: asyncio.Task | None = None
        self.listen_opts: dict | None = None
        self.segments: list[rtc.AudioFrame] = []
        self.speech_ms = 0.0
        # Frames up to the last end of speech: the trailing hangover silence
        # is not sent to STT (less audio, faster and cleaner recognition).
        self.speech_end = 0
        self.listen_started = 0.0
        self.end_timer: asyncio.Task | None = None
        self.answer_done: asyncio.Event | None = None
        self.vad_tasks: list[asyncio.Task] = []
        self.warmed = False
        # SPECULATIVE STT: the answer so far goes to STT the moment Silero
        # hears it end, while the hangover runs. If she says nothing more,
        # the transcript is (nearly) ready when the hangover ends: the STT
        # time is hidden inside the pause instead of added after it. If she
        # goes on, the guess is dropped. (frames it covered, its task)
        self.spec: tuple[int, asyncio.Task] | None = None

    # ---- wiring --------------------------------------------------------------

    async def start(self) -> None:
        # Commands first: anything the app sends from now on is handled.
        self.room.on("data_received", self._on_data)
        self.room.on("track_subscribed", self._on_track)
        track = rtc.LocalAudioTrack.create_audio_track("sakhi-voice", self.source)
        await self.room.local_participant.publish_track(track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE))
        for p in self.room.remote_participants.values():
            for pub in p.track_publications.values():
                if pub.track and pub.kind == rtc.TrackKind.KIND_AUDIO:
                    self._on_track(pub.track, pub, p)
        await self._state("idle")
        # The app may have sent "hello" before this agent could hear it:
        # announce that commands are taken now (the app re-sends until ready).
        await self._send({"t": "up"})
        logger.info("executor ready (lang=%s, gateway=%s)", self.voice.lang, GATEWAY)

    def _on_track(self, track: rtc.Track, _pub, participant: rtc.RemoteParticipant) -> None:
        if track.kind != rtc.TrackKind.KIND_AUDIO:
            return
        logger.info("listening to %s", participant.identity)
        task = asyncio.create_task(self._pump_audio(track))
        # A background task's exception is otherwise silent: log it.
        task.add_done_callback(lambda t: t.cancelled() or not t.exception() or logger.error("audio pump died: %r", t.exception()))
        self.vad_tasks.append(task)

    def _on_data(self, packet: rtc.DataPacket) -> None:
        if packet.topic != TOPIC:
            return
        try:
            cmd = json.loads(packet.data.decode("utf-8"))
        except Exception:
            return
        asyncio.create_task(self._command(cmd))

    async def _send(self, msg: dict) -> None:
        with contextlib.suppress(Exception):
            await self.room.local_participant.publish_data(json.dumps(msg, ensure_ascii=False), reliable=True, topic=TOPIC)

    async def _state(self, state: str) -> None:
        self.state = state
        with contextlib.suppress(Exception):
            await self.room.local_participant.set_attributes({"lk.agent.state": {"hearing": "listening"}.get(state, state)})

    # ---- commands -------------------------------------------------------------

    async def _command(self, cmd: dict) -> None:
        t = cmd.get("t")
        if t == "hello":
            # Idempotent: the app re-sends until it hears "ready".
            lines = [str(x) for x in (cmd.get("lines") or []) if str(x).strip()]
            if not self.warmed:
                self.warmed = True
                asyncio.create_task(self.voice.warm(lines))
            await self._send({"t": "ready", "lines": len(lines)})
        elif t == "say":
            await self._cancel()
            self.seq = int(cmd.get("seq", 0))
            logger.info("say #%d%s: '%s'", self.seq, " + listen" if cmd.get("listen") else "", str(cmd.get("text") or "")[:40])
            self.task = asyncio.create_task(self._say(self.seq, str(cmd.get("text") or ""), cmd.get("listen")))
        elif t == "next":
            if int(cmd.get("seq", -2)) == self.seq and self.state in ("listening", "hearing"):
                await self._finish_answer(forced=True)
        elif t == "stop":
            await self._cancel()
            self.seq = -1  # nothing running answers to any seq now

    async def _cancel(self) -> None:
        if self.task and not self.task.done():
            self.task.cancel()
            with contextlib.suppress(BaseException):
                await self.task
        self.task = None
        self.source.clear_queue()
        self._reset_listen()
        await self._state("idle")

    # ---- speak ------------------------------------------------------------------

    async def _say(self, seq: int, text: str, listen: dict | None) -> None:
        try:
            if text:
                await self._state("thinking")
                pcm, rate = await self.voice.pcm(text, "now")
                if seq != self.seq:
                    return
                await self._state("speaking")
                await self._play(pcm, rate, seq)
                if seq != self.seq:
                    return
            await self._send({"t": "said", "seq": seq})
            if listen:
                await self._listen(seq, listen)
            else:
                await self._state("idle")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("say failed")
            await self._send({"t": "error", "seq": seq, "message": str(exc)[:200]})
            await self._state("idle")

    async def _play(self, pcm: bytes, rate: int, seq: int) -> None:
        step = rate // 50 * 2  # 20 ms of 16-bit mono
        for i in range(0, len(pcm), step):
            if seq != self.seq:
                return
            chunk = pcm[i : i + step]
            if len(chunk) < step:
                chunk = chunk + b"\0" * (step - len(chunk))
            await self.source.capture_frame(rtc.AudioFrame(chunk, rate, 1, len(chunk) // 2))
        await self.source.wait_for_playout()

    # ---- listen -------------------------------------------------------------------

    def _drop_spec(self) -> None:
        if self.spec and not self.spec[1].done():
            self.spec[1].cancel()
        self.spec = None

    def _reset_listen(self) -> None:
        if self.end_timer and not self.end_timer.done():
            self.end_timer.cancel()
        self.end_timer = None
        self._drop_spec()
        self.segments = []
        self.speech_ms = 0.0
        self.speech_end = 0
        self.listen_opts = None
        if self.answer_done and not self.answer_done.is_set():
            self.answer_done.set()

    async def _listen(self, seq: int, opts: dict) -> None:
        self._reset_listen()
        self.listen_opts = {
            "hangover": max(0.35, float(opts.get("hangoverMs", 700)) / 1000.0),
            "noSpeech": float(opts.get("noSpeechMs", 7000)) / 1000.0,
        }
        self.answer_done = asyncio.Event()
        self.listen_started = time.monotonic()
        await self._state("listening")
        logger.info("listening #%d (hangover %.2f s, no-speech %.0f s)", seq, self.listen_opts["hangover"], self.listen_opts["noSpeech"])
        try:
            await asyncio.wait_for(self._wait_first_speech(), timeout=self.listen_opts["noSpeech"])
        except asyncio.TimeoutError:
            if seq == self.seq and not self.segments and self.state == "listening":
                self._reset_listen()
                await self._state("idle")
                await self._send({"t": "silence", "seq": seq})
            return
        # Hard cap: an answer that never pauses (continuous noise) still ends.
        try:
            await asyncio.wait_for(self.answer_done.wait(), timeout=MAX_ANSWER_S + 5)
        except asyncio.TimeoutError:
            if seq == self.seq and self.state == "hearing":
                await self._finish_answer(forced=True)

    async def _wait_first_speech(self) -> None:
        while self.state == "listening":
            await asyncio.sleep(0.05)

    async def _pump_audio(self, track: rtc.Track) -> None:
        """The ASHA's audio -> Silero VAD, always running; used only while listening.

        Capture is continuous, not per VAD segment: while she answers, EVERY
        frame is kept (pauses included) from 0.5 s before speech started (a
        ring buffer covers the VAD's reaction time, so the first syllable is
        never lost). The answer so far is therefore always complete: Next can
        end it at any instant.
        """
        stream = rtc.AudioStream(track, sample_rate=SR, num_channels=1)
        vad_stream = self.vad.stream()
        preroll: collections.deque[rtc.AudioFrame] = collections.deque()
        preroll_samples = 0

        async def feed() -> None:
            nonlocal preroll_samples
            async for ev in stream:
                frame = ev.frame
                if self.state == "hearing":
                    self.segments.append(frame)
                else:
                    preroll.append(frame)
                    preroll_samples += frame.samples_per_channel
                    while preroll_samples > SR * PRE_ROLL_S and preroll:
                        preroll_samples -= preroll.popleft().samples_per_channel
                vad_stream.push_frame(frame)

        feeder = asyncio.create_task(feed())
        feeder.add_done_callback(lambda t: t.cancelled() or not t.exception() or logger.error("audio feed died: %r", t.exception()))
        speech_started_at: float | None = None
        try:
            async for ev in vad_stream:
                if ev.type != lkvad.VADEventType.INFERENCE_DONE:
                    logger.info("vad %s (state %s, %.2f s speech)", ev.type.value, self.state, ev.speech_duration)
                if ev.type == lkvad.VADEventType.START_OF_SPEECH:
                    speech_started_at = time.monotonic()
                    if self.state in ("listening", "hearing") and speech_started_at >= self.listen_started:
                        if self.end_timer and not self.end_timer.done():
                            self.end_timer.cancel()  # a pause inside one answer
                        self._drop_spec()  # she went on: the guess is stale
                        if self.state == "listening":
                            self.segments = list(preroll)
                            preroll.clear()
                            preroll_samples = 0
                            await self._state("hearing")
                            await self._send({"t": "speech", "seq": self.seq})
                elif ev.type == lkvad.VADEventType.END_OF_SPEECH:
                    started = speech_started_at or 0.0
                    if self.state != "hearing" or started < self.listen_started:
                        continue  # spoken while Sakhi was speaking: not an answer
                    self.speech_ms += ev.speech_duration * 1000
                    self.speech_end = len(self.segments)
                    total = sum(f.samples_per_channel for f in self.segments) / SR
                    if total >= MAX_ANSWER_S:
                        await self._finish_answer()
                    else:
                        wait = max(0.0, self.listen_opts["hangover"] - VAD_SILENCE_S) if self.listen_opts else 0.0
                        self._speculate()
                        self.end_timer = asyncio.create_task(self._end_after(wait, self.seq))
        finally:
            feeder.cancel()
            await vad_stream.aclose()
            await stream.aclose()

    async def close(self) -> None:
        """Job shutdown, while the event loop still runs: stop what is being
        said, the audio pumps and the speculative STT, and free Sakhi's audio
        source (native buffers that otherwise outlive the session)."""
        with contextlib.suppress(Exception):
            await self._cancel()
        for task in self.vad_tasks:
            task.cancel()
        await asyncio.gather(*self.vad_tasks, return_exceptions=True)
        self.vad_tasks.clear()
        with contextlib.suppress(Exception):
            await self.source.aclose()

    def _speculate(self) -> None:
        """Start transcribing the answer as it stands (see self.spec)."""
        self._drop_spec()
        frames = self.segments[: self.speech_end + TAIL_FRAMES]
        if frames:
            self.spec = (len(frames), asyncio.create_task(self.ears.transcribe(list(frames))))

    async def _end_after(self, wait: float, seq: int) -> None:
        await asyncio.sleep(wait)
        if seq == self.seq and self.state == "hearing":
            # This task IS end_timer: detach it first, or finishing the answer
            # would cancel the very task doing the finishing.
            self.end_timer = None
            await self._finish_answer()

    async def _finish_answer(self, forced: bool = False) -> None:
        """End of one answer: transcribe it (or report a skip). `forced` = the
        ASHA tapped Next or the cap was hit; the merged speech so far is used."""
        seq = self.seq
        frames, speech_ms = self.segments, self.speech_ms
        if not forced and self.speech_end:
            frames = frames[: self.speech_end + TAIL_FRAMES]
        self.speech_end = 0
        if forced:
            logger.info("answer ended by %s", "Next" if speech_ms < MAX_ANSWER_S * 1000 else "the length cap")
        self.segments, self.speech_ms = [], 0.0
        if self.end_timer and not self.end_timer.done() and self.end_timer is not asyncio.current_task():
            self.end_timer.cancel()
        self.end_timer = None
        # A guess still alive means nothing was said since it started (new
        # speech drops it): it covers this answer. Use it, done or nearly.
        guess = self.spec[1] if self.spec and frames else None
        self.spec = None
        await self._state("thinking")
        if not frames:
            # "Next" with nothing said: an explicit skip.
            await self._state("idle")
            await self._send({"t": "silence", "seq": seq, "skipped": True})
        else:
            try:
                text, ms = None, 0
                if guess is not None:
                    with contextlib.suppress(Exception):
                        t0 = time.perf_counter()
                        text, ms = await guess
                        logger.info("speculative stt used (+%d ms wait after the pause)", (time.perf_counter() - t0) * 1000)
                if text is None:
                    text, ms = await self.ears.transcribe(frames)
                if seq == self.seq:
                    await self._send({"t": "heard", "seq": seq, "text": text, "ms": ms, "speechMs": round(speech_ms)})
                    # Length only, never her words: this log is not a health record (DPDP s.8(5)).
                    logger.info("heard (%d ms stt, %.1f s speech): %d chars", ms, speech_ms / 1000, len(text))
            except Exception as exc:
                logger.warning("stt failed: %s", exc)
                await self._send({"t": "error", "seq": seq, "message": f"stt: {exc}"[:200]})
            await self._state("idle")
        if self.answer_done:
            self.answer_done.set()


def prewarm(proc: JobProcess) -> None:
    proc.userdata["recorded"] = {lang: load_recordings(lang) for lang in ("kn", "hi")}
    logger.info("recordings: %s", {k: len(v) for k, v in proc.userdata["recorded"].items()})
    proc.userdata["vad"] = silero.VAD.load(
        min_speech_duration=0.08,
        min_silence_duration=VAD_SILENCE_S,
        prefix_padding_duration=PRE_ROLL_S,
        activation_threshold=0.5,
        sample_rate=SR,
    )


async def entrypoint(ctx: JobContext) -> None:
    meta = {}
    with contextlib.suppress(Exception):
        meta = json.loads(ctx.job.metadata or "{}")
    lang = str(meta.get("language") or "kn")
    if lang not in ("kn", "hi"):
        lang = "kn"
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)
    http = aiohttp.ClientSession()
    ctx.add_shutdown_callback(http.close)
    ex = Executor(ctx.room, ctx.proc.userdata["vad"], lang, http, (ctx.proc.userdata.get("recorded") or {}).get(lang))
    ctx.add_shutdown_callback(ex.close)

    # The ASHA's app left (End, exit, phone locked for good): free this job
    # now instead of idling in an empty room until LiveKit closes it.
    def on_left(p: rtc.RemoteParticipant) -> None:
        if p.kind == rtc.ParticipantKind.PARTICIPANT_KIND_AGENT:
            return
        logger.info("%s left: ending the session", p.identity)
        ctx.shutdown(reason="asha left")

    ctx.room.on("participant_disconnected", on_left)
    await ex.start()
    logger.info("live session for %s (%s)", (meta.get("mother") or {}).get("name", "?"), lang)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            prewarm_fnc=prewarm,
            agent_name=os.getenv("SAKHI_LIVE_AGENT_NAME", "sakhi-app-dev"),
            port=int(os.getenv("SAKHI_AGENT_HTTP_PORT", "8790")),
            # One process per check-in, everywhere (livekit's Windows default
            # is a thread in this process, where every session's memory stays
            # behind for good: ~60-120 MB each). The process ends with the
            # visit and gives all of it back; one is kept warm, so a new
            # check-in still joins at once.
            job_executor_type=JobExecutorType.PROCESS,
            num_idle_processes=1,
            job_memory_warn_mb=400,
        )
    )


if __name__ == "__main__":
    main()
