import asyncio
import os
import json
import base64
import aiohttp
from dotenv import load_dotenv

load_dotenv('/etc/sakhi.env')
load_dotenv('.env')

api_key = os.getenv('SARVAM_API_KEY')
print(f"Loaded API key prefix: {api_key[:8] if api_key else 'None'}")

async def main():
    text = "ತಾಯಿ ಹೆಸರು ಪ್ರಿಯಾ ಶರ್ಮಾ, ಅವರ ವಯಸ್ಸು ಇಪ್ಪತ್ತೆಂಟು ವರ್ಷ."
    print(f"Input text for TTS: {text}")

    async with aiohttp.ClientSession() as session:
        # Generate TTS audio
        headers = {"api-subscription-key": api_key, "Content-Type": "application/json"}
        tts_payload = {
            "inputs": [text],
            "target_language_code": "kn-IN",
            "speaker": "shreya",
            "model": "bulbul:v3"
        }
        async with session.post("https://api.sarvam.ai/text-to-speech", headers=headers, json=tts_payload) as resp:
            data = await resp.json()
            if "audios" not in data or not data["audios"]:
                print(f"TTS failed: {data}")
                return
            audio_bytes = base64.b64decode(data["audios"][0])
            print(f"TTS succeeded: {len(audio_bytes)} bytes audio")

        # Test STT configurations
        configs = [
            ("saaras:v3", "codemix"),
            ("saaras:v3", "transcribe"),
            ("saarika:v2.5", "transcribe"),
        ]

        for model, mode in configs:
            form = aiohttp.FormData()
            form.add_field("file", audio_bytes, filename="audio.wav", content_type="audio/wav")
            form.add_field("model", model)
            form.add_field("language_code", "kn-IN")
            if mode:
                form.add_field("mode", mode)

            stt_headers = {"api-subscription-key": api_key}
            async with session.post("https://api.sarvam.ai/speech-to-text", headers=stt_headers, data=form) as stt_resp:
                stt_data = await stt_resp.json()
                print(f"\n--- Model: {model} | Mode: {mode} ---")
                print(f"Status: {stt_resp.status}")
                print(f"Transcript: {stt_data.get('transcript')}")
                print(f"Full response: {stt_data}")

if __name__ == "__main__":
    asyncio.run(main())
