"""Where does an Indic STT decode spend its time? (run with stt_service/.venv)"""

import os
import sys
import time

os.environ.setdefault("HF_HUB_OFFLINE", "1")
import numpy as np
import soundfile as sf
import torch
from huggingface_hub import snapshot_download

path = snapshot_download("bodhan-ai/indic-transcribe-flex", local_files_only=True)
sys.path.insert(0, path)
from indic_transcribe import IndicTranscribe  # noqa: E402

asr = IndicTranscribe.from_pretrained(path, dtype=torch.bfloat16)
m = asr.model
print("param device", next(m.parameters()).device, next(m.parameters()).dtype)
print("fe device", getattr(asr.fe, "device", "?"), type(asr.fe).__name__)
files = [torch.from_numpy(sf.read(f, dtype="float32")[0]) for f in sys.argv[1:]]


def sync():
    torch.cuda.synchronize()


for run, wav in enumerate([files[0], files[0]] + files[1:]):
    t = time.perf_counter()
    feats, mask = asr._features(wav)
    sync()
    t1 = time.perf_counter()
    with torch.inference_mode():
        enc = m.model.encoder(feats, attention_mask=mask)
    sync()
    t2 = time.perf_counter()
    prompt = asr.tokenizer.encode_prompt("kn", itn=False, romanized=False)
    ids = torch.tensor([prompt], device=asr.device)
    with torch.inference_mode():
        out = m.generate(encoder_outputs=enc, attention_mask=mask, decoder_input_ids=ids, max_new_tokens=512)
    sync()
    t3 = time.perf_counter()
    n = out.shape[1] - len(prompt)
    print(f"run {run}: features {1000*(t1-t):.0f} ms, encoder {1000*(t2-t1):.0f} ms, generate {1000*(t3-t2):.0f} ms for {n} tokens ({1000*(t3-t2)/max(n,1):.0f} ms/token)")
    print(f"   {wav.shape[0]/16000:.2f}s", asr.tokenizer.decode(asr.tokenizer.strip_prompt_and_trim(out[0].tolist(), prompt)))
print("generation config:", {k: getattr(m.generation_config, k, None) for k in ("num_beams", "do_sample", "use_cache", "max_length")})
