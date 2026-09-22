"""Regression coverage for clause-level pre-speech release."""

import agent
from sakhi_domain import Language, SakhiCallState


def _guard_chunks(chunks, state, split_re):
    """Mirror the small accumulate/vet/release loop inside tts_node."""
    pending = ""
    released = []
    for chunk in chunks:
        pending += chunk
        while True:
            match = split_re.search(pending)
            if not match:
                break
            piece, pending = pending[: match.end()], pending[match.end():]
            reason = agent.unbacked_claim_reason(piece, state)
            vetted = agent.bridge_line_for(state.language) if reason else piece
            released.append(vetted)
    if pending:
        reason = agent.unbacked_claim_reason(pending, state)
        released.append(agent.bridge_line_for(state.language) if reason else pending)
    return released


def test_second_clause_unbacked_claim_is_still_replaced():
    state = SakhiCallState(language=Language.ENGLISH)
    state.begin_turn()

    released = _guard_chunks(
        ["Her BP is stable, ", "I have recorded her blood pressure. "],
        state,
        agent._CLAUSE_BREAK_RE,
    )

    assert released[0] == "Her BP is stable, "
    assert "I have recorded" not in "".join(released)
    assert "let me put that into her chart" in released[1].lower()


def test_clean_first_clause_releases_before_second_arrives():
    state = SakhiCallState(language=Language.ENGLISH)
    state.begin_turn()

    released = _guard_chunks(
        ["Thank you, "],
        state,
        agent._CLAUSE_BREAK_RE,
    )

    assert released == ["Thank you, "]
    assert agent._SENTENCE_END_RE.search("Thank you, ") is None
