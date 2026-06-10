"""C1 — host-credential LLM broker policy for sandboxed community agents.

This protects the HOST USER's LLM credentials/wallet from UNTRUSTED
(verified/unverified) sandboxed community code that borrows the host key via the
broker. It is NOT a limit on agents in general: trusted/curated/self-run agents
(own key, host path) are unaffected — they keep "run until done".

DEFAULT-DENY: like ``tool_access``, an agent reaches the host LLM only if it
explicitly declares ``llm_access.enabled: true`` in its manifest. The host-config
ceiling (``agent_sandbox.llm`` in ~/.agentnode/config.json) ALWAYS wins — it can
lower the caps or force-disable, and a higher manifest value is clamped to it.

Refusals/errors come back as a STRUCTURED ``{"ok": False, "error": ...}`` so the
RPC host can return them as graceful per-call errors the agent can catch — never
a whole-run crash, never a host fallback. Errors are sanitized: no key, no prompt,
no raw provider internals.
"""
from __future__ import annotations

from dataclasses import dataclass

from agentnode_sdk.runtimes.agent_llm_broker import LlmBrokerError

# Conservative built-in ceilings: the fallback when the host config does not set a
# field, and the default the manifest request is clamped against.
_DEFAULT_MAX_CALLS = 20
_DEFAULT_MAX_INPUT_CHARS = 24_000
_DEFAULT_MAX_OUTPUT_CHARS = 24_000


@dataclass(frozen=True)
class LlmAccessPolicy:
    """Resolved per-run LLM access policy for one sandboxed community agent."""

    enabled: bool = False
    max_calls: int = 0
    max_input_chars: int = 0
    max_output_chars: int = 0


def _ceiling(host_llm: dict, key: str, default: int) -> int:
    """The host-config ceiling for ``key`` (a positive int), else the built-in."""
    try:
        v = int(host_llm[key])
    except (KeyError, TypeError, ValueError):
        return int(default)
    return v if v > 0 else int(default)


def _request(manifest: dict, key: str, ceiling: int) -> int:
    """The manifest's requested value for ``key`` (positive int), else the ceiling."""
    try:
        v = int(manifest[key])
    except (KeyError, TypeError, ValueError):
        return ceiling
    return v if v > 0 else ceiling


def resolve_llm_policy(agent_config: dict | None, host_config: dict | None = None) -> LlmAccessPolicy:
    """Resolve the effective policy = min(manifest request, host ceiling).

    Default-deny: a missing ``llm_access`` or ``enabled != true`` → disabled. The
    host config can also force-disable (``agent_sandbox.llm.enabled: false``).
    """
    manifest = ((agent_config or {}).get("llm_access")) or {}
    host_llm = ((((host_config or {}).get("agent_sandbox")) or {}).get("llm")) or {}

    manifest_enabled = manifest.get("enabled") is True
    host_enabled = host_llm.get("enabled", True)  # host omits → defer to manifest
    if not (manifest_enabled and host_enabled is not False):
        return LlmAccessPolicy(enabled=False)

    mc = _ceiling(host_llm, "max_calls", _DEFAULT_MAX_CALLS)
    ic = _ceiling(host_llm, "max_input_chars", _DEFAULT_MAX_INPUT_CHARS)
    oc = _ceiling(host_llm, "max_output_chars", _DEFAULT_MAX_OUTPUT_CHARS)
    return LlmAccessPolicy(
        enabled=True,
        max_calls=min(_request(manifest, "max_calls", mc), mc),
        max_input_chars=min(_request(manifest, "max_input_chars", ic), ic),
        max_output_chars=min(_request(manifest, "max_output_chars", oc), oc),
    )


def _input_chars(messages) -> int:
    return sum(len(str((m or {}).get("content", "") or "")) for m in (messages or []))


def make_policy_broker(policy: LlmAccessPolicy, base_broker):
    """Wrap ``base_broker`` (the host LLM broker) with policy enforcement.

    Returns a callable ``(messages) -> {"ok": bool, "completion"?|"error"?}``.
    Per-run state (the call counter) lives in this closure, so one run gets one
    fresh budget. Never raises — all failures are structured + sanitized so the
    RPC host turns them into graceful per-call errors (no host fallback).
    """
    state = {"calls": 0}

    def broker(messages):
        if not policy.enabled:
            return {"ok": False, "error":
                    "LLM access not granted: this agent did not declare llm_access.enabled"}

        state["calls"] += 1
        if state["calls"] > policy.max_calls:
            return {"ok": False, "error": "LLM call limit reached for this run"}

        if _input_chars(messages) > policy.max_input_chars:
            return {"ok": False, "error": "LLM request exceeds the allowed input size"}

        try:
            completion = base_broker(messages)
        except LlmBrokerError as exc:
            # LlmBrokerError is our own already-sanitized type (no key/internals).
            return {"ok": False, "error": str(exc)}
        except Exception:
            # Never surface a raw provider/host exception (may carry secrets).
            return {"ok": False, "error": "LLM call failed"}

        content = str(completion.get("content", "") or "") if isinstance(completion, dict) else ""
        if len(content) > policy.max_output_chars:
            return {"ok": False, "error": "LLM response exceeds the allowed output size"}

        return {"ok": True, "completion": completion}

    return broker
