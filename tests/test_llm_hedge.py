"""Hedged LLM racing behaviour - no network, all timing is simulated.

Guards the fix for "the fallback lags the system". Measured before/after on
the real chain (2026-08-17):

    happy path (Groq healthy)   ~4200ms  ->   519ms
    total Groq outage           ~3700ms  ->  1610ms

The 4200ms came from OpenRouter sitting at tier 1 behind a 1.8s global timeout
it could never beat. The 3700ms came from strictly serial groups. These tests
pin the racing semantics that replaced both.
"""

import asyncio

import pytest
from livekit.agents import APIConnectOptions
from livekit.agents.llm import LLM, ChatChunk, ChatContext, ChoiceDelta, LLMStream

from llm_hedge import HedgedLLM


class _FakeStream(LLMStream):
    def __init__(self, fake, *, chat_ctx, tools, conn_options):
        super().__init__(fake, chat_ctx=chat_ctx, tools=tools, conn_options=conn_options)
        self._fake = fake

    async def _run(self) -> None:
        await asyncio.sleep(self._fake.first_token_delay)
        if self._fake.error is not None:
            self._fake.started_and_failed = True
            raise self._fake.error
        for text in self._fake.texts:
            self._event_ch.send_nowait(
                ChatChunk(id=self._fake.name, delta=ChoiceDelta(role="assistant", content=text))
            )


class _FakeLLM(LLM):
    """An LLM that emits `texts` after `first_token_delay`, or raises."""

    def __init__(self, name, first_token_delay=0.0, texts=("hello",), error=None):
        super().__init__()
        self.name = name
        self.first_token_delay = first_token_delay
        self.texts = texts
        self.error = error
        self.chat_calls = 0
        self.started_and_failed = False

    @property
    def model(self) -> str:
        return self.name

    @property
    def label(self) -> str:
        return self.name

    def chat(self, *, chat_ctx, tools=None, conn_options=APIConnectOptions(), **kw):
        self.chat_calls += 1
        return _FakeStream(self, chat_ctx=chat_ctx, tools=tools or [], conn_options=conn_options)


async def _collect(chain):
    ctx = ChatContext()
    ctx.add_message(role="user", content="my bp is one forty over ninety")
    out = []
    stream = chain.chat(chat_ctx=ctx)
    try:
        async for chunk in stream:
            if chunk.delta and chunk.delta.content:
                out.append(chunk.delta.content)
    finally:
        await stream.aclose()
    return "".join(out)


@pytest.mark.asyncio
async def test_fast_primary_wins_and_backup_is_never_started():
    """The common case must cost nothing: no second request, no added latency."""
    primary = _FakeLLM("primary", first_token_delay=0.05, texts=("from-primary",))
    backup = _FakeLLM("backup", first_token_delay=0.05, texts=("from-backup",))

    chain = HedgedLLM(primary, backup, hedge_delay=0.5)
    started = asyncio.get_event_loop().time()
    result = await _collect(chain)
    elapsed = asyncio.get_event_loop().time() - started

    assert result == "from-primary"
    assert backup.chat_calls == 0, "hedging billed a backup request on a healthy turn"
    assert elapsed < 0.4, f"healthy turn took {elapsed:.2f}s; hedging must be free"


@pytest.mark.asyncio
async def test_slow_primary_triggers_the_hedge_and_backup_wins():
    """A merely SLOW primary must not cost the full serial fallback."""
    primary = _FakeLLM("primary", first_token_delay=5.0, texts=("from-primary",))
    backup = _FakeLLM("backup", first_token_delay=0.1, texts=("from-backup",))

    chain = HedgedLLM(primary, backup, hedge_delay=0.2)
    started = asyncio.get_event_loop().time()
    result = await _collect(chain)
    elapsed = asyncio.get_event_loop().time() - started

    assert result == "from-backup"
    assert backup.chat_calls == 1
    # ~= hedge_delay + backup latency, NOT primary timeout + backup latency.
    assert elapsed < 1.0, f"failover took {elapsed:.2f}s; the race did not overlap"


@pytest.mark.asyncio
async def test_slow_primary_still_wins_if_it_recovers_first():
    """Hedging must not cancel a slow primary that is about to answer."""
    primary = _FakeLLM("primary", first_token_delay=0.3, texts=("from-primary",))
    backup = _FakeLLM("backup", first_token_delay=5.0, texts=("from-backup",))

    chain = HedgedLLM(primary, backup, hedge_delay=0.1)
    result = await _collect(chain)

    assert result == "from-primary"
    assert backup.chat_calls == 1, "the hedge should have fired"


@pytest.mark.asyncio
async def test_primary_failing_early_starts_backup_without_waiting_the_hedge():
    """A fast hard failure (401/404/503) must not sit out the hedge delay.

    This is the real Groq-outage path: the group returns 401s in ~400ms, and
    waiting the full hedge delay on top of that would be pure dead air.
    """
    primary = _FakeLLM("primary", first_token_delay=0.02, error=RuntimeError("401 invalid key"))
    backup = _FakeLLM("backup", first_token_delay=0.1, texts=("from-backup",))

    chain = HedgedLLM(primary, backup, hedge_delay=10.0)  # would hang if honoured
    started = asyncio.get_event_loop().time()
    result = await _collect(chain)
    elapsed = asyncio.get_event_loop().time() - started

    assert result == "from-backup"
    assert elapsed < 1.0, (
        f"took {elapsed:.2f}s - an early primary failure waited out the hedge delay"
    )


@pytest.mark.asyncio
async def test_both_providers_failing_raises_rather_than_hanging():
    primary = _FakeLLM("primary", first_token_delay=0.02, error=RuntimeError("primary down"))
    backup = _FakeLLM("backup", first_token_delay=0.02, error=RuntimeError("backup down"))

    chain = HedgedLLM(primary, backup, hedge_delay=0.1)
    with pytest.raises(Exception):
        await _collect(chain)


@pytest.mark.asyncio
async def test_loser_output_never_reaches_the_caller():
    """Critical for tool calls: only the winner's chunks may be acted on."""
    primary = _FakeLLM("primary", first_token_delay=0.05, texts=("A", "B", "C"))
    backup = _FakeLLM("backup", first_token_delay=0.06, texts=("X", "Y", "Z"))

    chain = HedgedLLM(primary, backup, hedge_delay=0.0)  # force both to start
    result = await _collect(chain)

    assert result == "ABC", f"got {result!r} - output was spliced across providers"
    assert "X" not in result and "Y" not in result and "Z" not in result
