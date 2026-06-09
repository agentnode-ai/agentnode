"""Host-side LLM broker for sandboxed agents (B2b-1).

A sandboxed community agent requests an LLM completion via the ``call_llm`` RPC;
the HOST runs the actual provider call here. The provider API key stays host-side
and NEVER enters the sandbox — the broker receives only ``messages`` and returns
only a structured completion. A PURE RELAY: it never interprets messages as host
commands. Errors are GENERIC — the raw provider exception (which can contain the
key, request URLs, or internal details) is never surfaced to the agent.

Credential policy, per-agent scopes, rate/cost limits, and audit are NOT here —
that is Sprint C. This module only proves the secure plumbing.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("agentnode.agent_sandbox")

# Used only when the host LLM binding does not specify a model.
_DEFAULT_MODELS = {
    "openai": "gpt-4o-mini",
    "anthropic": "claude-3-5-haiku-latest",
}


class LlmBrokerError(RuntimeError):
    """Generic, leak-free LLM broker failure (no key, no provider internals)."""


def host_llm_broker(messages: list) -> dict:
    """Run one LLM completion HOST-side and return ``{"role","content"}``.

    Raises :class:`LlmBrokerError` (generic) on any failure — no provider /
    missing SDK / provider exception — never leaking the key or the raw provider
    exception text.
    """
    from agentnode_sdk.runtimes.agent_runner import _auto_detect_llm

    binding = _auto_detect_llm()
    if not binding:
        raise LlmBrokerError("no LLM provider configured on the host")

    client = binding.get("client")
    provider = binding.get("provider")
    model = binding.get("model") or _DEFAULT_MODELS.get(provider or "", "")
    if client is None or provider not in _DEFAULT_MODELS:
        raise LlmBrokerError("unsupported or unavailable LLM provider")

    try:
        if provider == "openai":
            resp = client.chat.completions.create(model=model, messages=list(messages or []))
            content = resp.choices[0].message.content
        else:  # anthropic
            resp = client.messages.create(model=model, max_tokens=1024, messages=list(messages or []))
            content = resp.content[0].text
    except Exception as exc:  # never surface the raw provider error (may carry the key)
        logger.warning("LLM broker provider call failed: %s", type(exc).__name__)
        raise LlmBrokerError("LLM provider call failed")

    return {"role": "assistant", "content": content or ""}
