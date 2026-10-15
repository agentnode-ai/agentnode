"""The one canonical invocation contract: resolve -> prepare -> approve -> submit.

The CLI, the SDK and the MCP adapter all go through ``prepare`` and ``submit``;
none of them has its own execution path. ``prepare`` produces a canonical
``Invocation`` whose ``invocation_hash`` binds the exact tuple a run is authorised
and audited against: capability id+version, the manifest digest, the artifact
digest, the tool and its schema digest, and the normalised input digest. Change
any of those after approval and the hash changes, so the approval (bound to the
old hash) no longer matches and ``submit`` refuses.

``submit`` is the only door to execution; it consumes a single-use, invocation
-bound approval atomically before dispatching, and never runs anything locally.
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from agentnode_sdk.capability import manifest as M
from agentnode_sdk.capability.index import ResolvedCapability


def _canon(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def manifest_digest(man: dict[str, Any]) -> str:
    """sha256 over the manifest's signed canonical payload -- binds the exact manifest."""
    return _sha256(M.canonical_payload(man))


def normalize_input(arguments: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """Return (normalised arguments, input_digest). Canonical JSON is the normalisation."""
    normalised = arguments or {}
    return normalised, _sha256(_canon(normalised))


@dataclass(frozen=True)
class Invocation:
    capability_id: str
    capability_version: str
    manifest_digest: str
    artifact_digest: dict[str, str]
    tool_name: str
    tool_schema_digest: dict[str, str]
    execution_model: str
    input_digest: str
    arguments: dict[str, Any]
    requested_permissions: dict[str, Any]
    created_at: str
    nonce: str
    invocation_hash: str = ""

    def binding(self) -> dict[str, Any]:
        """The exact tuple the invocation_hash commits to (approval + audit bind to this)."""
        return {
            "capability_id": self.capability_id,
            "capability_version": self.capability_version,
            "manifest_digest": self.manifest_digest,
            "artifact_digest": self.artifact_digest,
            "tool_name": self.tool_name,
            "tool_schema_digest": self.tool_schema_digest,
            "execution_model": self.execution_model,
            "input_digest": self.input_digest,
            "nonce": self.nonce,
        }


def _hash_invocation(binding: dict[str, Any]) -> str:
    return _sha256(b"agentnode.capability.v1/invocation\n" + _canon(binding))


def prepare(resolved: ResolvedCapability, tool_name: str, arguments: dict[str, Any],
            *, nonce: str | None = None) -> Invocation:
    """Build the canonical Invocation for (capability, tool, arguments).

    THE shared entry the CLI, SDK and MCP adapter all call. The ``nonce`` makes an
    invocation unique so a single-use approval binds exactly one run; left None it
    is random (one-shot CLI/SDK). A caller that must reproduce the same
    invocation_hash across a prepare/approve/re-submit round trip (the MCP adapter's
    human-approval flow) passes a deterministic idempotency key as ``nonce``.
    """
    man = resolved.manifest
    tool = next((t for t in man.get("tools", []) if t.get("name") == tool_name), None)
    if tool is None:
        raise ValueError(f"no such tool {tool_name!r} in {resolved.capability_id}")
    normalised, input_digest = normalize_input(arguments)
    inv = Invocation(
        capability_id=man["capability_id"],
        capability_version=man["capability_version"],
        manifest_digest=manifest_digest(man),
        artifact_digest=man["artifact_digest"],
        tool_name=tool_name,
        tool_schema_digest=tool.get("tool_schema_digest") or M.tool_schema_digest(tool),
        execution_model=man["execution_model"],
        input_digest=input_digest,
        arguments=normalised,
        requested_permissions=man.get("permissions", {}),
        created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        nonce=nonce if nonce is not None else uuid.uuid4().hex,
    )
    object.__setattr__(inv, "invocation_hash", _hash_invocation(inv.binding()))
    return inv
