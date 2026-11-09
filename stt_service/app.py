"""Self-hosted Indic STT for Sakhi: bodhan-ai/indic-transcribe-flex on this PC's GPU.

    stt_service\\.venv\\Scripts\\python -m uvicorn stt_service.app:app --host 127.0.0.1 --port 8795
    (or: stt_service\\run.cmd)

Port 8795 (8790 is the local Live Sakhi agent worker, scripts/sakhi_agent_dev.py).

Routes
  GET  /health                 {"ok", "loaded", "device", "langs", "warm_ms", ...}
  POST /api/stt                multipart: file=<audio>, lang, mode, expecting
  POST /api/stt/raw?lang=&mode=&expecting=   body = the audio (WAV from the app)
  -> {"text", "en", "ms", "provider": "indic", "lang", "lid_confidence", "mode", "seconds"}

The gateway (sakhi_gateway.py, INDIC_STT_URL) calls /api/stt/raw first and
falls back to Gnani / Sarvam when this service fails or is slow.

Model: loaded ONCE at startup from the local Hugging Face cache (offline: no
token is read or written here; the licence was accepted on the Hub and the
files were downloaded with `hf auth login`), bf16 on CUDA, warmed up with
one decode per language. ALL GPU work runs on ONE dedicated thread (the GPU
lock): CUDA's cuBLAS / cuDNN / cuFFT handles are per thread, and a request
landing on a fresh thread of the web server's pool paid ~2-3 s creating them
(measured: 3.2-5.8 s per answer, against ~0.5-1 s on a warm thread). Audio: a 16 kHz mono PCM16 WAV (what the app sends) is
used as it is; anything else is converted with ffmpeg to 16 kHz mono first.
Over ~40 s the chunked long-form path is used (the model refuses > 45 s).
Decode routing (one decode per answer, native for kn / hi, mixed for en, the
English re-decode in auto mode): stt_service/routing.py.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import os
import subprocess
import sys
import threading
import time
from contextlib import asynccontextmanager

import numpy as np

try:  # run as a package (uvicorn stt_service.app:app) or as a script from stt_service/
    from . import routing
except ImportError:  # pragma: no cover
    import routing  # type: ignore[no-redef]

log = logging.getLogger("indic-stt")
logging.basicConfig(level=os.getenv("INDIC_STT_LOG", "INFO"), format="%(asctime)s %(levelname)s %(message)s")

REPO_ID = os.getenv("INDIC_STT_MODEL", "bodhan-ai/indic-transcribe-flex")
MAX_UPLOAD_BYTES = 30 * 1024 * 1024
SR = 16000


def model_dir() -> str:
    """The downloaded model, from INDIC_STT_MODEL_DIR or the local Hugging Face cache (offline)."""
    explicit = os.getenv("INDIC_STT_MODEL_DIR", "").strip()
    if explicit:
        return explicit
    os.environ.setdefault("HF_HUB_OFFLINE", "1")  # never call the Hub (and never need a token) at run time
    from huggingface_hub import snapshot_download

    return snapshot_download(REPO_ID, local_files_only=True)


class Engine:
    """The model, its GPU lock, and the decode routing."""

    def __init__(self) -> None:
        self.asr = None
        self.transcribe_long = None
        self.device = "?"
        self.lock = threading.Lock()
        # One thread owns the GPU: load, warm-up and every decode run on it.
        self.gpu = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="gpu")
        self.warm_ms: dict[str, int] = {}
        self.load_s = 0.0

    def load(self) -> None:
        import torch

        path = model_dir()
        sys.path.insert(0, path)  # the model code ships inside the download
        from indic_transcribe import IndicTranscribe  # type: ignore[import-not-found]
        from long_form import transcribe_long  # type: ignore[import-not-found]

        t = time.perf_counter()
        dtype = torch.bfloat16 if torch.cuda.is_available() else torch.float32
        self.asr = IndicTranscribe.from_pretrained(path, dtype=dtype)
        self.transcribe_long = transcribe_long
        self.device = torch.cuda.get_device_name(0) if torch.cuda.is_available() else "cpu"
        self.load_s = time.perf_counter() - t
        log.info("model loaded on %s in %.1f s (%s)", self.device, self.load_s, dtype)
        # Warm-up: the first decode per language/mode builds kernels and caches.
        # A short tone burst (noise makes the decoder ramble for hundreds of tokens),
        # at two lengths so the encoder's kernels for short and longer answers are loaded.
        tone = (0.1 * np.sin(2 * np.pi * 220 * np.arange(SR * 3) / SR)).astype(np.float32)
        for lang in routing.ALLOWED_LANGS:
            t = time.perf_counter()
            for n in (SR, SR * 3):
                with self.lock:
                    self.asr.transcribe(tone[:n], lang=lang, mode=routing.default_mode(lang), return_lid=True, allowed_langs=list(routing.ALLOWED_LANGS), max_new_tokens=16)
            self.warm_ms[lang] = round((time.perf_counter() - t) * 1000)
        log.info("warm-up done: %s ms", self.warm_ms)

    def decode(self, wav: np.ndarray, lang: str | None, mode: str | None) -> dict:
        asr = self.asr
        if asr is None:
            raise RuntimeError("model not loaded")
        seconds = wav.shape[0] / SR
        allowed = list(routing.ALLOWED_LANGS)
        t = time.perf_counter()
        with self.lock:
            if seconds > routing.LONG_FORM_SECONDS:
                # Long audio: language identified (if not given) once, then chunked at pauses.
                use_lang = lang
                lid_conf = None
                if use_lang is None:
                    top = asr.identify(wav[: SR * 20], allowed_langs=allowed)
                    use_lang, lid_conf = top[0][0], float(top[0][1])
                use_mode = mode or routing.default_mode(use_lang)
                text = self.transcribe_long(asr, wav, lang=use_lang, mode=use_mode)
                return self._out(text, use_lang, use_mode, lid_conf, seconds, t)
            use_mode = mode or (routing.default_mode(lang) if lang else "native")
            text, lid = asr.transcribe(wav, lang=lang, mode=use_mode, return_lid=True, allowed_langs=allowed)
            detected = lid["lang"]
            conf = next((float(p) for code, p in lid["topk"] if code == detected), None)
            if routing.should_redecode_english(lang, detected, text):
                # Weak English LID: Latin words under a kn / hi label are English.
                text = asr.transcribe(wav, lang="en", mode=mode or routing.default_mode("en"))
                en_conf = next((float(p) for code, p in lid["topk"] if code == "en"), None)
                return self._out(text, "en", mode or routing.default_mode("en"), en_conf, seconds, t, redecoded=True)
            if lang is None and mode is None and detected == "en":
                # Auto mode landed on English: English is read in mixed mode.
                text = asr.transcribe(wav, lang="en", mode="mixed")
                use_mode = "mixed"
            return self._out(text, detected, use_mode, conf, seconds, t)

    @staticmethod
    def _out(text: str, lang: str, mode: str, conf: float | None, seconds: float, t: float, redecoded: bool = False) -> dict:
        text = (text or "").strip()
        return {
            "text": text,
            # The model does not translate: English only when the answer is English.
            "en": text if lang == "en" else "",
            "ms": round((time.perf_counter() - t) * 1000),
            "provider": "indic",
            "lang": lang,
            "lid_confidence": None if conf is None else round(conf, 4),
            "mode": mode,
            "seconds": round(seconds, 2),
            **({"redecoded": True} if redecoded else {}),
        }


ENGINE = Engine()


def to_pcm(data: bytes) -> np.ndarray:
    """Any audio -> 16 kHz mono float32. A 16 kHz mono PCM16 WAV is read directly; the rest goes through ffmpeg."""
    if not data:
        raise ValueError("empty audio")
    if routing.pcm16_mono_16k(data):
        raw = routing.wav_pcm16_payload(data)
    else:
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0", "-ac", "1", "-ar", str(SR), "-f", "s16le", "pipe:1"],
            input=data,
            capture_output=True,
            timeout=30,
        )
        if proc.returncode != 0:
            raise ValueError(f"could not decode the audio ({proc.stderr.decode('utf-8', 'replace').strip()[:200]})")
        raw = proc.stdout
    pcm = np.frombuffer(raw[: len(raw) // 2 * 2], dtype="<i2").astype(np.float32) / 32768.0
    if pcm.size < SR // 10:
        raise ValueError("audio shorter than 0.1 s")
    return pcm


# --- HTTP ---------------------------------------------------------------------

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile  # noqa: E402
from fastapi.concurrency import run_in_threadpool  # noqa: E402


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await asyncio.wrap_future(ENGINE.gpu.submit(ENGINE.load))
    yield


app = FastAPI(title="Sakhi Indic STT", lifespan=lifespan)


async def _run(data: bytes, lang: str | None, mode: str | None, expecting: str | None) -> dict:
    try:
        lang, mode = routing.resolve(lang, mode)
        pcm = await run_in_threadpool(to_pcm, data)  # ffmpeg off the GPU thread
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    t = time.perf_counter()
    out = await asyncio.wrap_future(ENGINE.gpu.submit(ENGINE.decode, pcm, lang, mode))
    out["total_ms"] = round((time.perf_counter() - t) * 1000)
    # `expecting` (the slot a follow-up asked for: count / date) is logged for
    # tuning; kn / hi stay native either way (the app parses number words).
    log.info("stt lang=%s->%s mode=%s expect=%s %.1fs %dms lid=%s", lang or "auto", out["lang"], out["mode"], expecting or "-", out["seconds"], out["ms"], out["lid_confidence"])
    return out


@app.get("/health")
def health() -> dict:
    return {
        "ok": ENGINE.asr is not None,
        "loaded": ENGINE.asr is not None,
        "model": REPO_ID,
        "device": ENGINE.device,
        "langs": list(routing.ALLOWED_LANGS),
        "load_s": round(ENGINE.load_s, 1),
        "warm_ms": ENGINE.warm_ms,
    }


@app.post("/api/stt")
async def stt_multipart(file: UploadFile = File(...), lang: str = Form(""), mode: str = Form(""), expecting: str = Form("")) -> dict:
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "audio too large")
    return await _run(data, lang, mode, expecting)


@app.post("/api/stt/raw")
async def stt_raw(request: Request, lang: str = Query(""), mode: str = Query(""), expecting: str = Query("")) -> dict:
    data = await request.body()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "audio too large")
    return await _run(data, lang, mode, expecting)
