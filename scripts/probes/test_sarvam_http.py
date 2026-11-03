# Manual live probe (network / paid providers): run from anywhere, e.g.
#   python scripts/probes/test_sarvam_http.py
# Kept out of tests/ on purpose (see pytest.ini). It works from the repo root.
import os as _os, sys as _sys
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
_sys.path.insert(0, _ROOT)
_os.chdir(_ROOT)

import asyncio
import os
from dotenv import load_dotenv
load_dotenv()
from sarvam_wrappers import SarvamTTS, SarvamTTSChunkedStream
from livekit.agents.tts import AudioEmitter, SynthesizedAudio
import aiohttp
from livekit import rtc

__test__ = False

async def test_tts():
    tts = SarvamTTS(
        api_key=os.environ.get("SARVAM_API_KEY"),
        model="bulbul:v3",
        speaker="pooja",
        target_language_code="kannada" # wait, let's test kn-IN
    )
    # Mock AudioEmitter
    class MockEmitter:
        def __init__(self):
            self.bytes_pushed = 0
            self.started = False
        def initialize(self, **kwargs):
            self.started = True
            print("Initialized with", kwargs)
        def push(self, data):
            if isinstance(data, bytes):
                self.bytes_pushed += len(data)
            elif isinstance(data, rtc.AudioFrame):
                self.bytes_pushed += len(data.data)
            print(f"Pushed audio, total bytes: {self.bytes_pushed}")
    
    emitter = MockEmitter()
    stream = tts.synthesize("ನಮಸ್ಕಾರ")
    # Stream is a ChunkedStream
    # we need to call _run on it
    await stream._run(emitter)
    print("Done")

if __name__ == "__main__":
    asyncio.run(test_tts())
