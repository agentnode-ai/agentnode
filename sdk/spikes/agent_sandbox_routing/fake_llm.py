"""Fake host-side LLM broker for the spike. No real provider, no API key.

The whole point: the agent (in the container) never sees a key — it sends a
``call_llm`` RPC and the HOST answers. Here the host answer is a deterministic
echo so the protocol can be tested offline.
"""
from __future__ import annotations


def complete(messages: list) -> dict:
    last = ""
    if messages:
        last = messages[-1].get("content", "") if isinstance(messages[-1], dict) else str(messages[-1])
    return {"role": "assistant", "content": "[fake-llm] " + str(last)}
