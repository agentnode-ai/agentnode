"""The MCP-first Capability Layer (slice 1, corrected A).

MCP stays the public tool protocol. This package adds the thin, signed
``agentnode.capability.v1`` manifest and the canonical resolve -> prepare ->
approve -> submit contract that the CLI, the SDK and the MCP adapter all go
through, routing an AgentNode-executed capability through the already-accepted
Worker/Job path.

Nothing in here modifies the frozen worker foundation; it composes it.

Slice 1 implements execution model 2 (AgentNode-executed stdio MCP) only.
Models 1 (external remote MCP) and 3 (native integration) are declared but
report a stable ``NOT_IMPLEMENTED`` from every execution entry point and never
substitute another model.
"""
from __future__ import annotations

from agentnode_sdk.capability.manifest import (
    CANONICAL_VERSION,
    CAPABILITY_SCHEMA,
    EXECUTION_MODELS,
    IMPLEMENTED_MODELS,
    SIGNER_ROLES,
    VerifyResult,
    VerifyStatus,
    artifact_digest_for_stdio,
    canonical_payload,
    capability_id,
    schema_digest,
    sign_manifest,
    tool_invocation_identity,
    tool_schema_digest,
    validate_manifest,
    verify_manifest,
    version_is_immutable,
)

__all__ = [
    "CANONICAL_VERSION",
    "CAPABILITY_SCHEMA",
    "EXECUTION_MODELS",
    "IMPLEMENTED_MODELS",
    "SIGNER_ROLES",
    "VerifyResult",
    "VerifyStatus",
    "artifact_digest_for_stdio",
    "canonical_payload",
    "capability_id",
    "schema_digest",
    "sign_manifest",
    "tool_invocation_identity",
    "tool_schema_digest",
    "validate_manifest",
    "verify_manifest",
    "version_is_immutable",
]
