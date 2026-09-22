from livekit.agents.llm import ChatContext

import agent


def _legacy_prune(context, max_items, max_tokens):
    conversation = list(context.items)

    def tokens():
        probe = context.copy()
        probe.items = list(conversation)
        return agent._approx_context_tokens(probe)

    while conversation and (
        len(conversation) > max_items or tokens() > max_tokens
    ):
        conversation.pop(0)
    return conversation


def test_incremental_pruning_matches_legacy_survivors_exactly():
    context = ChatContext()
    lengths = [1, 2, 4, 8, 17, 31, 64, 129, 257, 513]
    for index, length in enumerate(lengths):
        role = "user" if index % 2 == 0 else "assistant"
        context.add_message(role=role, content=(chr(65 + index) * length))

    expected = _legacy_prune(context, max_items=7, max_tokens=180)
    actual = agent._prune_conversation_incrementally(
        list(context.items), max_items=7, max_tokens=180
    )

    assert actual == expected
    assert [item.text_content for item in actual] == [
        item.text_content for item in expected
    ]
