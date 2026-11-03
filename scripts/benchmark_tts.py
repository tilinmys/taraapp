"""Benchmark the same Sarvam request shapes that production sends first."""

import base64
import sys
import time

import boto3


DEFAULT_MODEL = "bulbul:v2"
DEFAULT_SPEAKER = "manisha"
INSTANCE_ID = "i-0b4c434a3c7647384"
REGION = "ap-south-1"


def build_remote_script() -> str:
    """Return a self-contained benchmark that reads the worker's environment."""
    # The 2026-08-14 production-region measurement was about 438 ms for a v2
    # clause versus about 1012 ms for a v3 clause. Benchmarking only the old v3
    # full-sentence payload therefore overstated the path callers actually use.
    # Keep the defaults explicit, but let /etc/sakhi.env or the process env
    # override them so this diagnostic stays aligned with a deployed worker.
    script = r'''
import asyncio
import os
import time

import httpx


def load_worker_env(path="/etc/sakhi.env"):
    values = {}
    try:
        with open(path, encoding="utf-8") as env_file:
            for raw_line in env_file:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return values


async def test():
    file_env = load_worker_env()
    api_key = os.getenv("SARVAM_API_KEY") or file_env.get("SARVAM_API_KEY")
    if not api_key:
        raise RuntimeError("SARVAM_API_KEY is not configured")
    model = (
        os.getenv("SARVAM_TTS_MODEL")
        or file_env.get("SARVAM_TTS_MODEL")
        or __DEFAULT_MODEL__
    )
    speaker = (
        os.getenv("SARVAM_SPEAKER")
        or file_env.get("SARVAM_SPEAKER")
        or __DEFAULT_SPEAKER__
    )
    cases = [
        ("short_first_clause", "ನಮಸ್ಕಾರ, ನಾನು ಸಖಿ."),
        (
            "full_sentence",
            "ನಮಸ್ಕಾರ, ನಾನು ಸಖಿ. ತಾಯಿಯ ಹೆಸರು ಮತ್ತು ಅವರ ವಯಸ್ಸು ಎಷ್ಟು ಹೇಳಿ ಅಕ್ಕಾ?",
        ),
    ]
    url = "https://api.sarvam.ai/text-to-speech"
    headers = {
        "api-subscription-key": api_key,
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        for label, text in cases:
            payload = {
                "inputs": [text],
                "target_language_code": "kn-IN",
                "speaker": speaker,
                "model": model,
            }
            started = time.perf_counter()
            response = await client.post(url, headers=headers, json=payload)
            elapsed = time.perf_counter() - started
            response.raise_for_status()
            audio = response.json().get("audios", [""])[0]
            print(
                "{}: model={} speaker={} elapsed={:.3f}s audio_bytes={}".format(
                    label, model, speaker, elapsed, len(audio)
                )
            )


asyncio.run(test())
'''
    return script.replace("__DEFAULT_MODEL__", repr(DEFAULT_MODEL)).replace(
        "__DEFAULT_SPEAKER__", repr(DEFAULT_SPEAKER)
    )


def main() -> int:
    ssm = boto3.client("ssm", region_name=REGION)
    encoded = base64.b64encode(build_remote_script().encode("utf-8")).decode("ascii")
    command = (
        "/opt/sakhi/venv/bin/python3 -c \"import base64; "
        f"exec(base64.b64decode('{encoded}').decode())\""
    )
    response = ssm.send_command(
        InstanceIds=[INSTANCE_ID],
        DocumentName="AWS-RunShellScript",
        Parameters={"commands": [command]},
    )
    command_id = response["Command"]["CommandId"]
    time.sleep(2)
    for _ in range(15):
        result = ssm.get_command_invocation(
            CommandId=command_id,
            InstanceId=INSTANCE_ID,
        )
        if result["Status"] in {"Success", "Failed", "TimedOut"}:
            print("Status:", result["Status"])
            print(result.get("StandardOutputContent", ""))
            print(result.get("StandardErrorContent", ""), file=sys.stderr)
            return 0 if result["Status"] == "Success" else 1
        time.sleep(2)
    print("Timed out waiting for the SSM benchmark", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
