"""Shared slug/tool reference parsing (0.11.2)."""
import pytest

from agentnode_sdk.references import parse_tool_reference


@pytest.mark.parametrize("ref,expected", [
    ("word-counter-pack", ("word-counter-pack", None)),
    ("word-counter-pack:count_words", ("word-counter-pack", "count_words")),
    ("a-b", ("a-b", None)),
    ("a-b:c", ("a-b", "c")),
    ("a-b:", ("a-b", None)),            # empty tool part → None
    ("a-b:c:d", ("a-b", "c:d")),        # only the first colon splits
])
def test_parse_tool_reference(ref, expected):
    assert parse_tool_reference(ref) == expected
