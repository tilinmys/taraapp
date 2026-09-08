"""Hedged LLM: converts fallback cost from additive to concurrent.

WHY THIS EXISTS
---------------
A serial FallbackAdapter pays for every tier it tries. Measured on this
account (2026-08-17), with Groq's whole group unreachable:

    Groq group fails fast (4 tiers)  ~1.8s
    OpenRouter answers               ~1.9s
    -----------------------------------------
    first audible word               ~3.7s

3.7 seconds of silence on a call where an ASHA worker is waiting to report a
blood pressure. The tiers are not the problem - the *serialism* is. Nothing
about trying Groq requires us to have finished failing before OpenRouter may
start.

THE ALGORITHM
-------------
Start the primary. If it has not produced its FIRST TOKEN within `hedge_delay`,
start the backup concurrently rather than waiting for the primary to fail.
Whichever produces a first token first wins; the loser is cancelled and its
output discarded.

Failover cost becomes:

    max(hedge_delay, backup_latency)   instead of   primary_timeout + backup_latency

With hedge_delay=0.4s and OpenRouter at ~1.9s, a total Groq outage costs ~1.9s
instead of ~3.7s, and - the important part - a merely SLOW primary costs
nothing at all beyond the hedge delay, because the primary is never cancelled:
if it recovers and speaks first, it still wins.

The hedge now fires BEFORE the latent filler (1.2s), so OpenRouter starts
racing Groq while Groq is still within its normal TTFT range (~550ms).
This eliminates the "one moment please" filler on healthy calls.

WHY FIRST TOKEN AND NOT COMPLETION
----------------------------------
This is a voice agent. TTS starts on the first clause, so the first token is
the moment the caller stops hearing silence. Racing to completion would
optimise a number nobody experiences.

COST
----
When the hedge fires, both providers are billed for a generation and only one
is used. That is deliberate: hedging only fires when the primary is already
late, which is rare, and a duplicated 200-token completion is cheaper than a
mother hanging up. Set SAKHI_LLM_HEDGE=false to disable.

TOOL CALLS ARE SAFE
-------------------
An LLM only *emits* tool-call requests; the agent executes them after the
stream resolves. The loser's chunks are dropped before they reach the agent,
so a hedged turn can never double-execute record_bp or double-register a
mother.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from typing import Any

from livekit.agents import APIConnectOptions
from livekit.agents.llm import LLM, ChatChunk, ChatContext, LLMStream
from livekit.agents.types import NOT_GIVEN, NotGivenOr

logger = logging.getLogger(__name__)

DEFAULT_HEDGE_DELAY = 0.4


class HedgedLLMStream(LLMStream):
    """One turn, raced between a primary and a backup LLM."""

    def __init__(
        self,
        hedged: "HedgedLLM",
        *,
        chat_ctx: ChatContext,
        tools: list[Any],
        conn_options: APIConnectOptions,
        parallel_tool_calls: NotGivenOr[bool] = NOT_GIVEN,
        tool_choice: NotGivenOr[Any] = NOT_GIVEN,
        extra_kwargs: NotGivenOr[dict[str, Any]] = NOT_GIVEN,
    ) -> None:
        super().__init__(hedged, chat_ctx=chat_ctx, tools=tools, conn_options=conn_options)
        self._hedged = hedged
        self._parallel_tool_calls = parallel_tool_calls
        self._tool_choice = tool_choice
        self._extra_kwargs = extra_kwargs
        self._winner: int | None = None

    async def _branch(
        self,
        idx: int,
        target: LLM,
        timeout: float,
        queue: "asyncio.Queue[tuple[int, str, Any]]",
    ) -> None:
        """Run one candidate, publishing to `queue` only while it is winning."""
        try:
            async with target.chat(
                chat_ctx=self._chat_ctx,
                tools=self._tools,
                parallel_tool_calls=self._parallel_tool_calls,
                tool_choice=self._tool_choice,
                extra_kwargs=self._extra_kwargs,
                conn_options=dataclasses.replace(
                    self._conn_options, max_retry=0, timeout=timeout, retry_interval=0.0
                ),
            ) as stream:
                async for chunk in stream:
                    if self._winner is None:
                        # First token anywhere wins the race.
                        self._winner = idx
                        await queue.put((idx, "won", None))
                    if self._winner != idx:
                        return  # lost; drop everything, execute nothing
                    await queue.put((idx, "chunk", chunk))
            if self._winner == idx:
                await queue.put((idx, "done", None))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - reported to the race, not swallowed
            await queue.put((idx, "error", exc))

    async def _run(self) -> None:
        queue: asyncio.Queue[tuple[int, str, Any]] = asyncio.Queue()
        candidates = [
            (self._hedged._primary, self._hedged._primary_timeout),
            (self._hedged._backup, self._hedged._backup_timeout),
        ]
        tasks: dict[int, asyncio.Task] = {}
        errors: dict[int, BaseException] = {}

        def start(idx: int) -> None:
            if idx in tasks or idx >= len(candidates):
                return
            target, timeout = candidates[idx]
            tasks[idx] = asyncio.create_task(
                self._branch(idx, target, timeout, queue), name=f"hedge-{idx}"
            )

        start(0)
        hedge_deadline = asyncio.get_running_loop().time() + self._hedged._hedge_delay

        try:
            while True:
                if len(tasks) < len(candidates) and self._winner is None:
                    # Wait only until the hedge fires, then add the backup.
                    remaining = hedge_deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        logger.info(
                            "LLM hedge fired after %.2fs; racing %s alongside %s",
                            self._hedged._hedge_delay,
                            self._hedged._backup.label,
                            self._hedged._primary.label,
                        )
                        start(1)
                        continue
                    try:
                        idx, kind, payload = await asyncio.wait_for(queue.get(), remaining)
                    except asyncio.TimeoutError:
                        continue
                else:
                    idx, kind, payload = await queue.get()

                if kind == "won":
                    continue
                if kind == "chunk":
                    if idx == self._winner:
                        self._event_ch.send_nowait(payload)
                    continue
                if kind == "done":
                    if idx == self._winner:
                        return
                    continue
                if kind == "error":
                    errors[idx] = payload
                    logger.warning(
                        "LLM branch %s failed: %s", candidates[idx][0].label, payload
                    )
                    if idx == self._winner:
                        # It had already started speaking; nobody else can take
                        # over mid-utterance without producing a spliced reply.
                        raise payload
                    if len(tasks) < len(candidates):
                        start(1)  # primary died early - do not wait out the hedge
                        continue
                    if len(errors) >= len(candidates):
                        raise payload
        finally:
            for idx, task in tasks.items():
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks.values(), return_exceptions=True)


class HedgedLLM(LLM):
    """Races `primary` against `backup`, hedging after `hedge_delay` seconds."""

    def __init__(
        self,
        primary: LLM,
        backup: LLM,
        *,
        hedge_delay: float = DEFAULT_HEDGE_DELAY,
        primary_timeout: float = 1.5,
        backup_timeout: float = 4.0,
    ) -> None:
        super().__init__()
        self._primary = primary
        self._backup = backup
        self._hedge_delay = hedge_delay
        self._primary_timeout = primary_timeout
        self._backup_timeout = backup_timeout

    @property
    def model(self) -> str:
        return f"hedged({getattr(self._primary, 'model', '?')}|{getattr(self._backup, 'model', '?')})"

    @property
    def label(self) -> str:
        return f"hedged[{self._primary.label} | {self._backup.label}]"

    def chat(
        self,
        *,
        chat_ctx: ChatContext,
        tools: list[Any] | None = None,
        conn_options: APIConnectOptions = APIConnectOptions(),
        parallel_tool_calls: NotGivenOr[bool] = NOT_GIVEN,
        tool_choice: NotGivenOr[Any] = NOT_GIVEN,
        extra_kwargs: NotGivenOr[dict[str, Any]] = NOT_GIVEN,
    ) -> LLMStream:
        return HedgedLLMStream(
            self,
            chat_ctx=chat_ctx,
            tools=tools or [],
            conn_options=conn_options,
            parallel_tool_calls=parallel_tool_calls,
            tool_choice=tool_choice,
            extra_kwargs=extra_kwargs,
        )
