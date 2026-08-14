"""Avatar backends behind one interface.

Prepared now so the avatar can be plugged in without touching the call
pipeline. `entrypoint()` asks the registry for a backend and calls `start()`;
it never learns which vendor answered, and audio-only remains the default.

Why an interface rather than a direct call: the target architecture has three
render modes selected by available bandwidth (D-ID cloud video on good 4G, a
local WebGL canvas on 3G, audio-only on 2G). That is a polymorphism problem, and
hard-coding one vendor into `entrypoint()` would have to be undone the moment
the second mode is added. See oops.md §3.

Current state
-------------
    NullAvatarBackend  - audio-only. Default, always available, cannot fail.
    BeyAvatarBackend   - Beyond Presence. Plugin is installed and
                         BEYOND_PRESENCE_API_KEY is already present in .env,
                         so this path is live behind SAKHI_AVATAR=bey.
    DIDAvatarBackend   - D-ID Talk Stream. Handshake is NOT implemented; it
                         refuses to start rather than pretending to work.

The one hard rule every backend follows: **an avatar failure must never take
down the call.** Voice is the product; video is an enhancement. `start()`
returns False on failure and the session continues audio-only.
"""

from __future__ import annotations

import abc
import logging
import os
from typing import Callable

logger = logging.getLogger("sakhi.avatar")

# Avatar plugins MUST be imported at module scope, not lazily inside start().
#
# LiveKit's Plugin.register_plugin() raises "Plugins must be registered on the
# main thread", and a backend's start() runs inside the job's worker thread - so
# a lazy `from livekit.plugins import tavus` in _start() fails every time, and
# the seam quietly degrades every call to audio-only. Importing here happens on
# the main thread at process import, which is what registration requires.
#
# (This is what the `from livekit.plugins import bey` line at the top of the old
# agent.py was actually for. It looked unused because nothing referenced the
# name - the import itself was the point.)
#
# Each plugin is guarded separately so one missing package cannot take out the
# others, or the worker.
try:
    from livekit.plugins import tavus as _tavus_plugin
except Exception:  # pragma: no cover - optional dependency
    _tavus_plugin = None

try:
    from livekit.plugins import bey as _bey_plugin
except Exception:  # pragma: no cover - optional dependency
    _bey_plugin = None

EventSink = Callable[..., None]


def _noop_event(*args, **kwargs) -> None:
    return None


class AvatarBackend(abc.ABC):
    """One video-avatar rendering path for a call."""

    #: Value of SAKHI_AVATAR that selects this backend.
    key: str = "none"

    def __init__(self, *, event_sink: EventSink = _noop_event) -> None:
        self._event = event_sink
        self._started = False

    @property
    def started(self) -> bool:
        return self._started

    @property
    @abc.abstractmethod
    def description(self) -> str:
        """Human-readable name for logs and the dashboard."""

    @abc.abstractmethod
    async def _start(self, session, room) -> None:
        """Vendor-specific startup. Raise to signal failure."""

    async def start(self, session, room) -> bool:
        """Attach the avatar to a live AgentSession.

        Returns True if video is running, False if the call should proceed
        audio-only. Never raises - a broken avatar must not end a maternal
        health call.
        """
        try:
            await self._start(session, room)
        except Exception as exc:
            self._event(
                "playback", "warn", "Avatar unavailable",
                f"{self.description} failed to start; continuing audio-only",
                event="avatar_start_failed",
                backend=self.key, error_type=type(exc).__name__, error=str(exc),
            )
            logger.warning("Avatar backend %s failed to start", self.key, exc_info=True)
            return False

        self._started = True
        self._event(
            "playback", "ok", "Avatar started",
            f"{self.description} is rendering Sakhi's video track",
            event="avatar_started", backend=self.key,
        )
        return True

    async def aclose(self) -> None:
        """Release vendor resources. Safe to call when never started."""
        return None


class NullAvatarBackend(AvatarBackend):
    """Audio-only. The default, and the fallback for every other backend."""

    key = "none"

    @property
    def description(self) -> str:
        return "Audio-only (no avatar)"

    async def _start(self, session, room) -> None:
        # Nothing to attach. Deliberately not a failure - audio-only is a
        # supported mode (Mode C in the bandwidth matrix), not a degraded one.
        return None

    async def start(self, session, room) -> bool:
        self._event(
            "playback", "info", "Audio-only mode",
            "No avatar backend selected; Sakhi runs voice-only",
            event="avatar_disabled", backend=self.key,
        )
        return False


class BeyAvatarBackend(AvatarBackend):
    """Beyond Presence real-time avatar.

    Uses the `livekit-plugins-bey` package, which publishes the avatar's video
    track into the same room and consumes the agent's audio - so no audio
    routing changes are needed here.
    """

    key = "bey"

    def __init__(self, *, avatar_id: str | None = None, event_sink: EventSink = _noop_event) -> None:
        super().__init__(event_sink=event_sink)
        self._avatar_id = avatar_id or os.getenv("BEY_AVATAR_ID") or None
        self._session = None

    @property
    def description(self) -> str:
        return f"Beyond Presence avatar ({self._avatar_id or 'default'})"

    async def _start(self, session, room) -> None:
        if _bey_plugin is None:
            raise RuntimeError("livekit-plugins-bey is not installed")
        api_key = os.getenv("BEYOND_PRESENCE_API_KEY")
        if not api_key:
            raise RuntimeError("BEYOND_PRESENCE_API_KEY is not set")

        kwargs = {"api_key": api_key, "avatar_participant_name": "Sakhi"}
        if self._avatar_id:
            kwargs["avatar_id"] = self._avatar_id

        self._session = _bey_plugin.AvatarSession(**kwargs)
        await self._session.start(session, room)

    async def aclose(self) -> None:
        session, self._session = self._session, None
        if session is None:
            return
        closer = getattr(session, "aclose", None)
        if closer is not None:
            try:
                await closer()
            except Exception:
                logger.warning("Beyond Presence avatar close failed", exc_info=True)


class TavusAvatarBackend(AvatarBackend):
    """Tavus real-time video avatar, driven by Sakhi's own audio.

    WHY THIS IS SAFE FOR A KANNADA CLINICAL AGENT
    ---------------------------------------------
    Tavus can run a whole conversational stack itself (its own STT, LLM and
    TTS). That is emphatically NOT what happens here, and using it that way
    would break the product: Sakhi's replies come from the Groq/OpenAI chain
    constrained by the clinical prompt, her risk decisions come from
    TriageEngine, and her voice is Sarvam bulbul:v3 speaking Kannada.

    The LiveKit plugin creates the Tavus conversation with
    `"pipeline_mode": "echo"` and `transport_type: "livekit"` (verified in
    livekit/plugins/tavus/api.py). In echo mode Tavus does no thinking and no
    speaking - it receives our already-synthesised audio and returns a video
    track whose lips match it. So:

        our Sarvam Kannada audio  ->  Tavus  ->  lip-synced video
        our LLM + TriageEngine    ->  unchanged, Tavus never sees them

    Tavus contributes a face and nothing else, which is exactly the seam this
    class was built for. The avatar joins the room as its own participant and
    publishes video; the frontend renders that track.

    A NOTE ON THE PILOT
    -------------------
    The default replica is a stock one. For Bidar, a custom replica trained on
    ~2 minutes of footage of a local Kannada-speaking health worker would be
    substantially better received than any stock Western face - Tavus supports
    this, and it is worth doing before field deployment. Set TAVUS_REPLICA_ID
    once that replica exists; nothing else here changes.
    """

    key = "tavus"

    # "Olivia - Doctor" - female, clinical setting, which is the closest stock
    # match for a maternal-health co-pilot. Overridable per deployment.
    DEFAULT_REPLICA_ID = "rd3ba0f30551"

    def __init__(
        self,
        *,
        replica_id: str | None = None,
        persona_id: str | None = None,
        event_sink: EventSink = _noop_event,
    ) -> None:
        super().__init__(event_sink=event_sink)
        self._replica_id = replica_id or os.getenv("TAVUS_REPLICA_ID") or self.DEFAULT_REPLICA_ID
        self._persona_id = persona_id or os.getenv("TAVUS_PERSONA_ID") or None
        self._session = None

    @property
    def description(self) -> str:
        return f"Tavus avatar (replica {self._replica_id})"

    async def _start(self, session, room) -> None:
        if _tavus_plugin is None:
            raise RuntimeError(
                "livekit-plugins-tavus is not installed (pip install livekit-plugins-tavus)"
            )
        api_key = os.getenv("TAVUS_API_KEY")
        if not api_key:
            raise RuntimeError("TAVUS_API_KEY is not set")

        kwargs = {
            "api_key": api_key,
            "replica_id": self._replica_id,
            "avatar_participant_name": "Sakhi",
        }
        # Only pass a persona if one is configured. The plugin creates its own
        # echo-mode persona otherwise, which is the behaviour we want - a
        # persona carrying a system prompt would be Tavus trying to think.
        if self._persona_id:
            kwargs["persona_id"] = self._persona_id

        self._session = _tavus_plugin.AvatarSession(**kwargs)
        await self._session.start(session, room)

    async def aclose(self) -> None:
        session, self._session = self._session, None
        if session is None:
            return
        closer = getattr(session, "aclose", None)
        if closer is not None:
            try:
                await closer()
            except Exception:
                logger.warning("Tavus avatar close failed", exc_info=True)


class DIDAvatarBackend(AvatarBackend):
    """D-ID Talk Stream avatar - NOT YET IMPLEMENTED.

    Refuses to start rather than silently doing nothing, so selecting it cannot
    look like success in the logs.

    To implement (target architecture §4):
      1. POST https://api.d-id.com/talks/streams with the avatar image URL ->
         returns {id, session_id, offer, ice_servers}.
      2. Build an aiortc RTCPeerConnection, set the remote offer, answer via
         POST /talks/streams/{id}/sdp, and trickle candidates to
         POST /talks/streams/{id}/ice.
      3. Bridge Sakhi's TTS PCM into the stream by POSTing audio chunks to
         POST /talks/streams/{id}; D-ID returns lip-synced H.264.
      4. Republish that video track into the LiveKit room.

    Step 3 is the part that needs care: Sarvam TTS audio currently flows
    straight from `tts_node` into the LiveKit audio track. Feeding D-ID means
    teeing that stream, not re-synthesising it - the two must stay
    sample-aligned or the lip-sync drifts against what the caller hears.
    """

    key = "did"

    @property
    def description(self) -> str:
        return "D-ID Talk Stream avatar"

    async def _start(self, session, room) -> None:
        raise NotImplementedError(
            "The D-ID Talk Stream handshake is not implemented yet. "
            "Set SAKHI_AVATAR=bey for a working avatar, or leave it unset for "
            "audio-only."
        )


_BACKENDS: dict[str, type[AvatarBackend]] = {
    NullAvatarBackend.key: NullAvatarBackend,
    TavusAvatarBackend.key: TavusAvatarBackend,
    BeyAvatarBackend.key: BeyAvatarBackend,
    DIDAvatarBackend.key: DIDAvatarBackend,
}


def build_avatar_backend(
    name: str | None = None,
    *,
    event_sink: EventSink = _noop_event,
) -> AvatarBackend:
    """Select a backend by name, defaulting to audio-only.

    An unknown name falls back to audio-only with a warning rather than raising:
    a typo in a deploy env var must not stop calls being answered.
    """
    key = (name or os.getenv("SAKHI_AVATAR", "none")).strip().lower()
    backend_cls = _BACKENDS.get(key)
    if backend_cls is None:
        logger.warning(
            "Unknown avatar backend %r; falling back to audio-only. Valid: %s",
            key, ", ".join(sorted(_BACKENDS)),
        )
        backend_cls = NullAvatarBackend
    return backend_cls(event_sink=event_sink)
