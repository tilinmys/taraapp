"""Opt-in localhost integration checks for the Next.js -> LiveKit -> worker path.

Run while the local worker and frontend are running:

    RUN_FRONTEND_INTEGRATION=1 FRONTEND_URL=http://127.0.0.1:3001 \
        python -m pytest -q tests/test_frontend_backend_integration.py
"""

from __future__ import annotations

import asyncio
import os

import pytest
import requests
from livekit import rtc


RUN_LIVE = os.getenv("RUN_FRONTEND_INTEGRATION", "").strip() == "1"
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://127.0.0.1:3001").rstrip("/")

pytestmark = pytest.mark.skipif(
    not RUN_LIVE,
    reason="set RUN_FRONTEND_INTEGRATION=1 to exercise the running localhost stack",
)


def _get_json(path: str, *, timeout: float = 20.0) -> dict:
    response = requests.get(f"{FRONTEND_URL}{path}", timeout=timeout)
    response.raise_for_status()
    return response.json()


def test_frontend_proxies_registered_worker_and_config():
    status = _get_json("/api/worker-status")
    assert status.get("ready") is True, status

    config = _get_json("/api/config")
    assert config.get("providers", {}).get("tts", {}).get("primary", {}).get("provider")
    assert config.get("providers", {}).get("stt", {}).get("primary", {}).get("provider")
    assert config.get("providers", {}).get("llm", {}).get("primary", {}).get("provider")


@pytest.mark.asyncio
async def test_token_dispatch_connects_and_receives_agent_audio():
    token_payload = await asyncio.to_thread(
        _get_json,
        "/api/token?language=kannada",
        timeout=30.0,
    )
    assert token_payload.get("token")
    assert token_payload.get("url", "").startswith("wss://")
    assert token_payload.get("dispatch_id")
    assert token_payload.get("worker", {}).get("ready") is True

    room = rtc.Room()
    received_audio = asyncio.Event()
    reader_tasks: set[asyncio.Task] = set()

    async def read_first_audio_frame(track: rtc.Track) -> None:
        stream = rtc.AudioStream(track)
        try:
            event = await asyncio.wait_for(stream.__anext__(), timeout=30.0)
            if len(event.frame.data) > 0:
                received_audio.set()
        except (asyncio.TimeoutError, StopAsyncIteration):
            return
        finally:
            await stream.aclose()

    @room.on("track_subscribed")
    def on_track_subscribed(track, _publication, _participant) -> None:
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            task = asyncio.create_task(read_first_audio_frame(track))
            reader_tasks.add(task)
            task.add_done_callback(reader_tasks.discard)

    try:
        await room.connect(token_payload["url"], token_payload["token"])
        await asyncio.wait_for(received_audio.wait(), timeout=45.0)
        assert room.remote_participants, "LiveKit connected but the Sakhi worker never joined"
    finally:
        for task in tuple(reader_tasks):
            task.cancel()
        if reader_tasks:
            await asyncio.gather(*reader_tasks, return_exceptions=True)
        await room.disconnect()
