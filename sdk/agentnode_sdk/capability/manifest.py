"""The ``agentnode.capability.v1`` manifest: canonicalisation, signing, digests.

This is a THIN layer over an existing (imported, unmodified) MCP server. It does
not re-host or rewrite the server; it references it by pinned identity and adds
the metadata AgentNode enforces: a stable capability identity and version, the
requested permissions, the execution model, and precise digests.

Three things are deliberate and are what the independent review checks.

1.  The capability manifest has its OWN domain-separated, versioned canonical
    payload (``_canonical_bytes``). It is not the lockfile canonicaliser reused:
    a capability carries fields (execution model, per-tool schema digests, signer
    role) that the lockfile payload never signed, and reusing that payload would
    leave them outside the signature. The domain tag also means a capability
    signature can never be replayed as a lockfile-entry signature.

2.  The manifest states REQUIRED properties only. It never asserts an achieved
    security guarantee (e.g. "sandbox-isolated"); the runtime computes what was
    actually achieved for a run from the worker it ran on. See ``execution``.

3.  A published ``(capability_id, capability_version)`` is immutable in every
    security-relevant field. ``version_is_immutable`` is how the index refuses a
    second, different manifest for an existing version (including one that merely
    adds a tool): a change needs a new version, never a silent overwrite.

Signing/verification reuse the repo's Ed25519 primitives (``signing_key``) and
its ``ed25519:{sha256(pub)[:16]}`` key-id convention, so a capability key is the
same kind of key the rest of the product already understands.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from agentnode_sdk.signing_key import compute_key_id, sign_payload

#: The schema name this module speaks. A manifest naming any other schema is foreign.
CAPABILITY_SCHEMA = "agentnode.capability.v1"

#: The canonical-payload version. A verifier that does not know a payload's
#: canonicalisation version REFUSES it rather than guessing -- an unknown version
#: could canonicalise differently and let a changed field verify.
CANONICAL_VERSION = 1

#: Domain separation. The signed bytes begin with this tag and the canonical
#: version, so a capability signature is bound to this purpose and cannot be
#: mistaken for, or replayed as, any other signed object in the product.
_DOMAIN = b"agentnode.capability.v1/manifest"

#: The three execution models. Only the first is implemented in slice 1; the
#: other two are declared-but-unimplemented and refuse at every execution door.
EXECUTION_MODELS = ("agentnode_executed_mcp", "external_remote_mcp", "native_integration")
IMPLEMENTED_MODELS = ("agentnode_executed_mcp",)

#: Signer roles are distinct claims; none is collapsed into a vague "signed".
#: Slice 1 signs as the importing publisher (the party that wrapped an upstream
#: server), never as the upstream publisher -- the wrapper is not upstream-signed.
SIGNER_ROLES = ("upstream_publisher", "importing_publisher", "registry", "reviewer", "worker")

_SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_NAMESPACE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_TOOL = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$")

#: The security-relevant fields covered by the signature, in the order they are
#: canonicalised. ``signatures`` is excluded (circularity) and nothing else is.
_SIGNED_FIELDS = (
    "schema",
    "capability_id",
    "capability_version",
    "publisher",
    "execution_model",
    "source",
    "permissions",
    "tools",
    "artifact_digest",
    "schema_digest",
    "signer_role",
)


# --------------------------------------------------------------------------- #
# canonicalisation + digests
# --------------------------------------------------------------------------- #
def _canonical_json(obj: Any) -> bytes:
    """Deterministic JSON: sorted keys, no insignificant whitespace, UTF-8.

    The one canonicaliser used for the payload and for every digest, so a value
    is hashed the same way wherever it appears.
    """
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def schema_digest(tools: list[dict[str, Any]] | None = None) -> dict[str, str]:
    """Digest binding the capability-schema bytes AND the ordered per-tool schema digests.

    A digest of the framework schema alone does not bind the exposed tool schemas, so
    this aggregates the capability schema with each tool's input/output schema digest in
    manifest order. Two manifests whose tools differ therefore have different
    ``schema_digest`` values; adding, removing or reordering a tool changes it.
    """
    per_tool = [tool_schema_digest(t)["value"] for t in (tools or [])]
    aggregate = {
        "capability_schema": _sha256_hex(_canonical_json(CAPABILITY_V1_SCHEMA)),
        "per_tool_schema_digests": per_tool,
    }
    return {
        "algorithm": "sha256",
        "domain": "agentnode.capability.v1/schema+tools",
        "value": _sha256_hex(_canonical_json(aggregate)),
    }


def tool_schema_digest(tool: dict[str, Any]) -> dict[str, str]:
    """Digest binding one tool's input and output JSON Schemas.

    Normalisation: canonical JSON of ``{"input": input_schema, "output":
    output_schema}`` with missing schemas represented as ``{}``.
    """
    normalised = {
        "input": tool.get("input_schema") or {},
        "output": tool.get("output_schema") or {},
    }
    return {
        "algorithm": "sha256",
        "domain": "agentnode.capability.v1/tool-schema",
        "value": _sha256_hex(_canonical_json(normalised)),
    }


def artifact_digest_for_stdio(source: dict[str, Any]) -> dict[str, str]:
    """Digest of the pinned stdio-MCP artifact this capability references.

    The digest domain names exactly what is covered: the pinned launch command
    and the resolved package identity+version (npm/pypi). It is NOT an installed
    tree hash, an OCI image digest or a fetched-archive digest -- those are
    different stages with different domains; this one binds the *reference* the
    manifest makes. ``value`` is sha256 over the canonical JSON of the normalised
    reference (sorted command, package, version, transport).
    """
    normalised = {
        "transport": source.get("transport"),
        "command": list(source.get("command") or ()),
        "package": source.get("package") or {},
    }
    return {
        "algorithm": "sha256",
        "domain": "agentnode.capability.v1/mcp-stdio-pinned-reference",
        "value": _sha256_hex(_canonical_json(normalised)),
    }


def _canonical_bytes(manifest: dict[str, Any]) -> bytes:
    """The exact bytes a capability signature is computed over.

    ``_DOMAIN`` || b"/v" || CANONICAL_VERSION || b"\\n" || canonical-json of the
    signed fields (in ``_SIGNED_FIELDS`` order, via sorted-key canonical JSON).
    """
    body = {k: manifest[k] for k in _SIGNED_FIELDS if k in manifest}
    header = _DOMAIN + b"/v" + str(CANONICAL_VERSION).encode("ascii") + b"\n"
    return header + _canonical_json(body)


def canonical_payload(manifest: dict[str, Any]) -> bytes:
    """Public name for the signed bytes (used by the index and the audit binding)."""
    return _canonical_bytes(manifest)


# --------------------------------------------------------------------------- #
# identity
# --------------------------------------------------------------------------- #
def capability_id(publisher: str, name: str) -> str:
    """The stable capability identifier: ``{publisher}/{name}``.

    Derived deterministically from the ANP publisher namespace and package name;
    this is not a second, independent identity authority.
    """
    if not _NAMESPACE.match(publisher or ""):
        raise ValueError(f"invalid publisher namespace: {publisher!r}")
    if not _NAME.match(name or ""):
        raise ValueError(f"invalid capability name: {name!r}")
    return f"{publisher}/{name}"


def tool_invocation_identity(manifest: dict[str, Any], tool_name: str) -> dict[str, Any]:
    """The identity a run is bound to: capability + version + artifact + tool + tool-schema.

    This is what the audit result (S9) binds, so a result can never be read as
    belonging to a different tool, version or artifact than the one that ran.
    """
    tool = _find_tool(manifest, tool_name)
    if tool is None:
        raise ValueError(f"no such tool in capability: {tool_name!r}")
    return {
        "capability_id": manifest["capability_id"],
        "capability_version": manifest["capability_version"],
        "artifact_digest": manifest["artifact_digest"],
        "tool_name": tool_name,
        "tool_schema_digest": tool.get("tool_schema_digest") or tool_schema_digest(tool),
    }


def _find_tool(manifest: dict[str, Any], tool_name: str) -> dict[str, Any] | None:
    for tool in manifest.get("tools") or ():
        if tool.get("name") == tool_name:
            return tool
    return None


# --------------------------------------------------------------------------- #
# validation
# --------------------------------------------------------------------------- #
def validate_manifest(manifest: dict[str, Any]) -> list[str]:
    """Return a list of problems with *manifest*; empty means well-formed.

    Checks shape, identity, execution model, signer role, tools and digests. It
    does NOT check the signature (see ``verify_manifest``) -- a manifest can be
    well-formed and unsigned.
    """
    errors: list[str] = []

    if manifest.get("schema") != CAPABILITY_SCHEMA:
        errors.append(f"schema must be {CAPABILITY_SCHEMA!r}")

    cid = manifest.get("capability_id")
    if not isinstance(cid, str) or cid.count("/") != 1:
        errors.append("capability_id must be '{publisher}/{name}'")
    else:
        pub, _, name = cid.partition("/")
        if not _NAMESPACE.match(pub):
            errors.append(f"invalid publisher namespace in capability_id: {pub!r}")
        if not _NAME.match(name):
            errors.append(f"invalid name in capability_id: {name!r}")
        if manifest.get("publisher") != pub:
            errors.append("publisher must equal the namespace in capability_id")

    if not isinstance(manifest.get("capability_version"), str) or \
            not _SEMVER.match(manifest.get("capability_version", "")):
        errors.append("capability_version must be an immutable SemVer string")

    model = manifest.get("execution_model")
    if model not in EXECUTION_MODELS:
        errors.append(f"execution_model must be one of {EXECUTION_MODELS}")

    role = manifest.get("signer_role")
    if role not in SIGNER_ROLES:
        errors.append(f"signer_role must be one of {SIGNER_ROLES}")

    perms = manifest.get("permissions")
    if not isinstance(perms, dict):
        errors.append("permissions must be an object")
    else:
        if "user_approval" not in perms:
            errors.append("permissions.user_approval must be present")
        env_keys = perms.get("env_keys", [])
        if not isinstance(env_keys, list) or any(not isinstance(k, str) for k in env_keys):
            errors.append("permissions.env_keys must be a list of names")

    tools = manifest.get("tools")
    if not isinstance(tools, list) or not tools:
        errors.append("tools must be a non-empty list")
    else:
        seen = set()
        for i, tool in enumerate(tools):
            if not isinstance(tool, dict):
                errors.append(f"tools[{i}] must be an object")
                continue
            tname = tool.get("name")
            if not isinstance(tname, str) or not _TOOL.match(tname):
                errors.append(f"tools[{i}].name is invalid: {tname!r}")
            elif tname in seen:
                errors.append(f"duplicate tool name: {tname!r}")
            else:
                seen.add(tname)
            tsd = tool.get("tool_schema_digest")
            if tsd is not None and tsd != tool_schema_digest(tool):
                errors.append(f"tools[{i}].tool_schema_digest does not match its schemas")

    for key in ("artifact_digest", "schema_digest"):
        dig = manifest.get(key)
        if not isinstance(dig, dict) or dig.get("algorithm") != "sha256" \
                or not isinstance(dig.get("value"), str) or not dig.get("domain"):
            errors.append(f"{key} must be a sha256 digest with algorithm, domain and value")
    if isinstance(manifest.get("schema_digest"), dict) and isinstance(manifest.get("tools"), list) \
            and manifest["schema_digest"] != schema_digest(manifest["tools"]):
        errors.append("schema_digest does not match the capability schema and per-tool schemas")

    return errors


# --------------------------------------------------------------------------- #
# immutability
# --------------------------------------------------------------------------- #
def version_is_immutable(existing: dict[str, Any], incoming: dict[str, Any]) -> bool:
    """True iff *incoming* is identical to *existing* in every signed field.

    The index calls this to refuse a second, DIFFERENT manifest for an already
    published ``(capability_id, capability_version)`` -- including one that only
    adds a tool, because ``tools`` is a signed field. A change requires a new
    version, never a silent overwrite of an existing one.
    """
    return _canonical_bytes(existing) == _canonical_bytes(incoming)


# --------------------------------------------------------------------------- #
# signing + verification
# --------------------------------------------------------------------------- #
def sign_manifest(
    manifest: dict[str, Any],
    private_key: Ed25519PrivateKey,
    public_key_bytes: bytes,
    *,
    role: str,
) -> dict[str, Any]:
    """Return a copy of *manifest* carrying a ``signatures`` entry for *role*.

    The signature is over ``canonical_payload(manifest)`` with the ``signatures``
    block excluded. The entry records the signer role, the raw public key and the
    derived key-id, and the canonicalisation version, so a verifier can reject an
    unknown version and a key-id that does not match the key.
    """
    if role not in SIGNER_ROLES:
        raise ValueError(f"unknown signer role: {role!r}")
    errors = validate_manifest(manifest)
    if errors:
        raise ValueError("refusing to sign a malformed manifest: " + "; ".join(errors))
    body = {k: manifest[k] for k in manifest if k != "signatures"}
    body["signer_role"] = role
    sig = sign_payload(_canonical_bytes(body), private_key)
    entry = {
        "role": role,
        "canonicalization_version": CANONICAL_VERSION,
        "key_id": compute_key_id(public_key_bytes),
        "public_key": public_key_bytes.hex(),
        "signature": sig.hex(),
    }
    signed = dict(body)
    signed["signatures"] = list(manifest.get("signatures") or []) + [entry]
    return signed


class VerifyStatus(str, Enum):
    VALID = "valid"
    MISSING = "missing"
    MALFORMED = "malformed"
    UNKNOWN_CANON_VERSION = "unknown_canonicalization_version"
    KEY_ID_MISMATCH = "key_id_mismatch"
    INVALID = "invalid"


@dataclass(frozen=True)
class VerifyResult:
    status: VerifyStatus
    role: str = ""
    key_id: str = ""
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.status is VerifyStatus.VALID


def verify_manifest(manifest: dict[str, Any], *, role: str | None = None) -> VerifyResult:
    """Verify one signature on *manifest* (the *role* one, or the first present).

    REFUSES, in this order, before trusting anything: a malformed manifest; a
    missing signature; an unknown canonicalisation version; a key-id that does
    not equal the identifier derived from the embedded public key; a signature
    that does not verify over the canonical payload.
    """
    errors = validate_manifest(manifest)
    if errors:
        return VerifyResult(VerifyStatus.MALFORMED, reason="; ".join(errors))

    sigs = manifest.get("signatures") or []
    chosen = None
    for entry in sigs:
        if role is None or entry.get("role") == role:
            chosen = entry
            break
    if chosen is None:
        return VerifyResult(VerifyStatus.MISSING,
                            reason=f"no signature for role {role!r}" if role else "no signatures")

    if chosen.get("canonicalization_version") != CANONICAL_VERSION:
        return VerifyResult(VerifyStatus.UNKNOWN_CANON_VERSION, role=chosen.get("role", ""),
                            reason=f"canonicalization_version {chosen.get('canonicalization_version')!r}")

    try:
        pub_bytes = bytes.fromhex(chosen["public_key"])
        sig = bytes.fromhex(chosen["signature"])
    except (KeyError, ValueError) as exc:
        return VerifyResult(VerifyStatus.MALFORMED, role=chosen.get("role", ""),
                            reason=f"unreadable signature fields: {exc}")

    derived = compute_key_id(pub_bytes)
    if chosen.get("key_id") != derived:
        return VerifyResult(VerifyStatus.KEY_ID_MISMATCH, role=chosen.get("role", ""),
                            key_id=chosen.get("key_id", ""),
                            reason=f"key_id {chosen.get('key_id')!r} != derived {derived!r}")

    body = {k: manifest[k] for k in manifest if k != "signatures"}
    try:
        Ed25519PublicKey.from_public_bytes(pub_bytes).verify(sig, _canonical_bytes(body))
    except Exception as exc:  # noqa: BLE001 -- any verify failure is a refusal
        return VerifyResult(VerifyStatus.INVALID, role=chosen.get("role", ""), key_id=derived,
                            reason=f"signature does not verify: {type(exc).__name__}")

    return VerifyResult(VerifyStatus.VALID, role=chosen.get("role", ""), key_id=derived)


# --------------------------------------------------------------------------- #
# the schema (digested by ``schema_digest``)
# --------------------------------------------------------------------------- #
CAPABILITY_V1_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": CAPABILITY_SCHEMA,
    "type": "object",
    "required": [
        "schema", "capability_id", "capability_version", "publisher",
        "execution_model", "source", "permissions", "tools",
        "artifact_digest", "schema_digest", "signer_role",
    ],
    "properties": {
        "schema": {"const": CAPABILITY_SCHEMA},
        "capability_id": {"type": "string"},
        "capability_version": {"type": "string"},
        "publisher": {"type": "string"},
        "execution_model": {"enum": list(EXECUTION_MODELS)},
        "signer_role": {"enum": list(SIGNER_ROLES)},
        "source": {
            "type": "object",
            "required": ["transport", "command"],
            "properties": {
                "transport": {"enum": ["stdio"]},
                "command": {"type": "array", "items": {"type": "string"}},
                "package": {"type": "object"},
            },
        },
        "permissions": {
            "type": "object",
            "required": ["user_approval"],
            "properties": {
                "network": {"type": "object"},
                "filesystem": {"type": "object"},
                "code_execution": {"type": "boolean"},
                "data_access": {"type": "object"},
                "env_keys": {"type": "array", "items": {"type": "string"}},
                "user_approval": {"type": "string", "enum": ["required"]},
            },
        },
        "tools": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["name"],
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "input_schema": {"type": "object"},
                    "output_schema": {"type": "object"},
                    "tool_schema_digest": {"type": "object"},
                },
            },
        },
        "artifact_digest": {"type": "object"},
        "schema_digest": {"type": "object"},
        "signatures": {"type": "array"},
    },
    "additionalProperties": True,
}
