# Sakhi Indic STT — local open-source speech-to-text

Self-hosted speech-to-text for the Sakhi / Tara voice agent, running
[**bodhan-ai/indic-transcribe-flex**](https://huggingface.co/bodhan-ai/indic-transcribe-flex)
(1.2B parameters, 27 Indian languages) on your own GPU. Sakhi uses it for **Kannada, Hindi and
English** answers. It is the **only** speech-to-text in the app: no paid STT provider is called.

> **Built with Indic-Transcribe-Flex from Bodhan AI / AI4Bharat.**
> The model is licensed under the [Bodhan AI Indic Open Model License v1.0](https://huggingface.co/bodhan-ai/indic-transcribe-flex)
> (read it before use). The model weights are **not** stored in this repository: you download
> them from Hugging Face (step 3). The code in this folder is part of this repository's licence.

```
phone (Expo Go app)
  └─► Cloudflare quick tunnel ── npm run tunnel (app/)
        └─► Metro dev server, /api/sakhi/* proxy ── app/metro.config.js
              └─► Sakhi gateway :8787 ── python scripts/sakhi_gateway_dev.py
                    └─► THIS SERVICE :8795 ── stt_service/run.cmd
                          └─► indic-transcribe-flex on the GPU
```

---

## For AI agents: the required order

Run these steps **in order**. Each step has a check; do not continue until it passes.
Never print, log or commit a Hugging Face token. Never commit `.env`, `app/.env.local` or the model files.

| # | Step | Command | Check that must pass |
|---|---|---|---|
| 1 | Prerequisites present | `py -3.10 --version` · `nvidia-smi` · `ffmpeg -version` | Python 3.10.x, an NVIDIA GPU listed, ffmpeg prints a version |
| 2 | Create the venv and install | see [Step 2](#step-2--create-the-python-environment) | `torch.cuda.is_available()` prints `True` |
| 3 | Download the model (human step: licence + login) | `hf download bodhan-ai/indic-transcribe-flex` | `model.safetensors` (~4.9 GB) exists in the cache |
| 4 | Start the service | `stt_service\run.cmd` | log shows `Uvicorn running on http://127.0.0.1:8795` |
| 5 | Smoke test | `stt_service\.venv\Scripts\python stt_service\smoke_test.py` | prints `OK` |
| 6 | Point the gateway at it | add `INDIC_STT_URL=http://127.0.0.1:8795` to `.env` | `GET /api/sakhi/health` shows `"stt_provider": "indic"` |
| 7 | (App) gateway + tunnel | `python scripts\sakhi_gateway_dev.py` then `cd app && npm run tunnel` | the phone transcribes an answer; this service logs `stt lang=...` |

Step 3 needs a **person**: accepting a licence on huggingface.co is a legal agreement and the
token is personal. An agent should stop at step 3, ask the person to do it, and continue after.

---

## Requirements

| | Minimum | Tested with |
|---|---|---|
| GPU | NVIDIA, **≥ 4 GB VRAM** (the model uses ~2.8 GB in bf16) | RTX 4050 Laptop, 6 GB |
| NVIDIA driver | supports CUDA 12.4 | 610.62 |
| Python | **3.10** (its own venv, separate from the repo's Python) | 3.10.0 |
| ffmpeg | on `PATH` (only for audio that is not already 16 kHz mono WAV) | 8.1 |
| Disk | ~10 GB (model 4.9 GB + PyTorch ~4 GB) | |
| OS | Windows 10/11 (commands below); Linux works with the `bash` lines | Windows 11 |

No GPU? It also runs on the CPU (fp32), but about 10× slower: too slow for a live check-in.

---

## Step 1 — Prerequisites

```bat
py -3.10 --version
nvidia-smi
ffmpeg -version
```

Missing ffmpeg on Windows: `winget install Gyan.FFmpeg`, then open a new terminal.
Missing Python 3.10: install it from python.org (keep "py launcher" ticked).

## Step 2 — Create the Python environment

From the **repository root**:

```bat
py -3.10 -m venv stt_service\.venv
stt_service\.venv\Scripts\python -m pip install --upgrade pip
stt_service\.venv\Scripts\python -m pip install torch==2.6.0 torchaudio==2.6.0 --index-url https://download.pytorch.org/whl/cu124
stt_service\.venv\Scripts\python -m pip install -r stt_service\requirements.txt
```

Linux / macOS:

```bash
python3.10 -m venv stt_service/.venv
stt_service/.venv/bin/python -m pip install --upgrade pip
stt_service/.venv/bin/python -m pip install torch==2.6.0 torchaudio==2.6.0 --index-url https://download.pytorch.org/whl/cu124
stt_service/.venv/bin/python -m pip install -r stt_service/requirements.txt
```

Check (must print `cuda True`):

```bat
stt_service\.venv\Scripts\python -c "import torch; print('cuda', torch.cuda.is_available(), torch.cuda.get_device_name(0) if torch.cuda.is_available() else '')"
```

## Step 3 — Download the model (done once, by a person)

The model is **gated**: Hugging Face only serves it to accounts that accepted its licence.

1. Sign in at <https://huggingface.co> (create a free account if needed).
2. Open <https://huggingface.co/bodhan-ai/indic-transcribe-flex>, read the licence, fill in the
   form and click **Agree and access repository**.
3. Create a **read** token: <https://huggingface.co/settings/tokens> → *New token* → type *Read*.
4. Log in on this PC and download (the `hf` command is installed with the venv in step 2):

```bat
stt_service\.venv\Scripts\hf auth login
stt_service\.venv\Scripts\hf download bodhan-ai/indic-transcribe-flex
```

`hf auth login` asks for the token and stores it in your user profile, not in this repository.
Paste it at the prompt only: never put it in a file, `.env` or a commit.

The download is ~4.9 GB and lands in the Hugging Face cache
(Windows: `%USERPROFILE%\.cache\huggingface\hub\models--bodhan-ai--indic-transcribe-flex`,
Linux: `~/.cache/huggingface/hub/...`). Check:

```bat
stt_service\.venv\Scripts\python -c "from huggingface_hub import snapshot_download as s; import os; p=s('bodhan-ai/indic-transcribe-flex', local_files_only=True); print(p); print(round(os.path.getsize(os.path.join(p,'model.safetensors'))/1e9,2),'GB')"
```

After this the service runs **offline**: it loads the model from the cache with
`HF_HUB_OFFLINE=1` and never reads your token. To keep the model somewhere else, set
`INDIC_STT_MODEL_DIR` to the folder that contains `model.safetensors`.

## Step 4 — Start the service

```bat
stt_service\run.cmd
```

Linux / macOS:

```bash
stt_service/.venv/bin/python -m uvicorn stt_service.app:app --host 127.0.0.1 --port 8795
```

Startup takes ~20–30 s (load ~15 s, then one warm-up decode per language). Ready when the log shows:

```
INFO model loaded on NVIDIA GeForce RTX 4050 Laptop GPU in 14.0 s (torch.bfloat16)
INFO warm-up done: {'kn': 3883, 'hi': 408, 'en': 608} ms
INFO:     Uvicorn running on http://127.0.0.1:8795
```

Port **8795** on purpose: 8790 is the local Live Sakhi agent (`scripts/sakhi_agent_dev.py`).
It listens on `127.0.0.1` only. The gateway is the only thing that should call it.

## Step 5 — Smoke test

```bat
curl http://127.0.0.1:8795/health
stt_service\.venv\Scripts\python stt_service\smoke_test.py
```

`/health` must show `"loaded": true`. The smoke test sends a short WAV to `/api/stt/raw` in
each language and prints `OK` when every reply has the expected shape.

## Step 6 — Connect the Sakhi gateway

In the repository's `.env` (never committed), add:

```env
INDIC_STT_URL=http://127.0.0.1:8795
```

Start the gateway and check it:

```bat
python scripts\sakhi_gateway_dev.py
curl http://localhost:8787/api/sakhi/health
```

Expected: `"stt": true, "stt_provider": "indic"`. Without `INDIC_STT_URL` the gateway answers STT
requests with **503** ("not configured"). There is deliberately **no fallback** to another STT provider.

## Step 7 — Use it from the phone (Expo Go)

```bat
cd app
npm install
npm run tunnel
```

Scan the QR code with Expo Go (or open the `exp://…trycloudflare.com` address printed, also saved
in `app/tunnel-url.txt`). In the app: ASHA → a mother → **Talk with Sakhi** → answer aloud.
Every answer appears in the service's log as one line:

```
INFO stt lang=kn->kn mode=native expect=- 2.6s 431ms lid=1.0
```

(language asked → decoded, mode, audio length, decode time, language-ID confidence).

---

## API

| Route | Body | Reply |
|---|---|---|
| `GET /health` | – | `{ok, loaded, model, device, langs, load_s, warm_ms}` |
| `POST /api/stt/raw?lang=kn&mode=&expecting=` | the audio (WAV, or anything ffmpeg reads) | see below |
| `POST /api/stt` | multipart: `file`, `lang`, `mode`, `expecting` | see below |

```json
{"text": "ನನಗೆ ಇಪ್ಪತ್ನಾಲ್ಕು ವರ್ಷ", "en": "", "ms": 431, "provider": "indic",
 "lang": "kn", "lid_confidence": 1.0, "mode": "native", "seconds": 2.57}
```

- `lang`: `kn`, `hi`, `en` or `auto` (languages are locked to these three).
- `mode`: optional, `native` / `mixed` / `romanized`. Default: `native` for kn / hi, `mixed` for en.
- `expecting`: optional hint (`count`, `date`), logged only.
- `en` is filled for English answers only (the model transcribes, it does not translate).
- Errors: `400` bad input (wrong language, unreadable audio), `413` over 30 MB.

## How it decodes (and why)

- **One decode per answer.** The app always sends the language, so there is no language-ID pass.
- **Kannada / Hindi → native script.** The model's *mixed* mode turns spoken numbers into digits
  and gets them wrong in these languages ("ತೊಂಬತ್ತು" → "9", "एक सौ तीस बाई अस्सी" → "13580").
  The app reads number **words** itself, so native is both safer and what it expects.
- **English → mixed mode** (Latin words, digits).
- **Auto language:** the model's English language-ID is weak (plain Indian English scored
  Hindi 0.63), so a kn / hi result written mostly in Latin letters is decoded again as English.
- **One GPU thread.** Loading, warm-up and every decode run on one dedicated thread, which is
  also the GPU lock. CUDA keeps its library handles per thread: when requests landed on the web
  server's worker threads each paid 2–3 s of setup (3.2–5.8 s per answer). On one thread: 0.3–0.9 s.
- **Long audio.** Over ~40 s the model's chunked `long_form.transcribe_long` is used (a single
  pass is refused over 45 s). The gateway itself caps one answer at 25 s.
- **Audio.** A 16 kHz mono PCM16 WAV (what the app records) is used directly; anything else is
  converted with ffmpeg first.

Code: `app.py` (service), `routing.py` (the decisions above, no torch, unit-tested).

## Configuration

| Variable | Where | Default | Meaning |
|---|---|---|---|
| `INDIC_STT_URL` | repo `.env` (gateway) | – | where the gateway sends STT; required |
| `INDIC_STT_TIMEOUT` | repo `.env` (gateway) | `6` | seconds per answer (the app waits ~8 s) |
| `INDIC_STT_MODEL_DIR` | service environment | HF cache | folder holding `model.safetensors` |
| `INDIC_STT_MODEL` | service environment | `bodhan-ai/indic-transcribe-flex` | repo id looked up in the cache |
| `INDIC_STT_LOG` | service environment | `INFO` | log level |

## Tests

From the repository root (the repo's normal Python, no GPU needed):

```bat
python -m pytest tests/test_stt_service_routing.py tests/test_sakhi_gateway_stt.py -q
```

End to end through the phone's path (needs steps 4–7 running):

```bat
python scripts\stt_eval\synth.py          :: test answers for every intake question, kn / hi / en
python scripts\stt_eval\run_e2e.py        :: sends them through the tunnel, as the phone does
cd app && npx jest src/voice/nlu/__tests__/stt-e2e.test.ts   :: app NLU: STT vs reference
python scripts\stt_eval\agent_compare.py  :: the phone agent's slot / symptom / guardrail parsers
python scripts\stt_eval\report.py         :: REPORT.md
```

## Troubleshooting

| Symptom | Cause → fix |
|---|---|
| `LocalEntryNotFoundError` at startup | Model not downloaded → step 3 (or set `INDIC_STT_MODEL_DIR`). |
| `401` / `403` / "gated repo" during download | Licence not accepted on that account, or not logged in → step 3.2 / 3.4. |
| `cuda False` | CPU-only torch was installed → re-run the cu124 `pip install torch` line; check `nvidia-smi`. |
| `CUDA out of memory` | Another program holds the GPU (close it); the model needs ~2.8 GB. |
| Every answer takes 3–6 s | GPU work is not on the single GPU thread (an edit to `app.py`?): see "One GPU thread". |
| Gateway health `"stt": false` | `INDIC_STT_URL` missing from `.env`, or the gateway was started before it was added: restart it. |
| App: answers kept "for later" | Service down → the gateway returns 502 / 504 and the app keeps the answer in its offline outbox; start the service and it is sent automatically when the phone is online. |
| Port 8795 in use | Another copy is running: stop it, or change the port in `run.cmd` and `INDIC_STT_URL` together. |

## Licence and attribution

- **Model:** Bodhan AI Indic Open Model License v1.0 (on the model page). Self-hosting it inside a
  product is allowed. Giving third parties *direct* API access to the model (hosting it as a
  service for others) requires Bodhan AI's written approval (licence §3). Products that make
  the model available must show: **"Built with Indic-Transcribe-Flex from Bodhan AI / AI4Bharat."** (§2).
- The model is **not validated for medical use** (licence §11.2). In Sakhi it only writes down
  what was said. Every clinical decision comes from the deterministic rules, and the ASHA reviews
  every answer before the report is filed.
- **This folder's code:** the repository's licence (`LICENSE`).
