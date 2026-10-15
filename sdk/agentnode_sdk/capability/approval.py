"""Invocation-bound, short-lived, single-use human approval (S6).

Approval is NOT a model operation. Creating one requires the operator's signing
key -- the "trusted human channel". The model-facing MCP adapter holds no such
key and exposes no tool that calls ``grant``; it can only ``prepare`` and then
``submit`` an approval that an operator issued out of band. So a model cannot
approve its own action or mint a grant.

An approval binds the exact ``invocation_hash`` (the whole tuple -- capability,
version, manifest, artifact, tool, schema, input). It expires, and it is consumed
once, atomically, at submit. Any change to the invocation after approval changes
the hash, so the approval no longer matches and submit refuses.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from agentnode_sdk.signing_key import compute_key_id, sign_payload


def _canon(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


class ApprovalError(Exception):
    """A submit-time refusal: no approval, wrong binding, expired, reused, or bad signature."""


@dataclass(frozen=True)
class Approval:
    approval_id: str
    invocation_hash: str
    human_actor: str
    granted_scope: dict[str, Any]
    issued_at: float
    expires_at: float
    operator_key_id: str
    operator_public: str   # hex raw Ed25519 public key
    signature: str         # hex

    def to_dict(self) -> dict[str, Any]:
        return {"approval_id": self.approval_id, "invocation_hash": self.invocation_hash,
                "human_actor": self.human_actor, "granted_scope": self.granted_scope,
                "issued_at": self.issued_at, "expires_at": self.expires_at,
                "operator_key_id": self.operator_key_id, "operator_public": self.operator_public,
                "signature": self.signature}

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "Approval":
        return Approval(**d)

    def _signed_body(self) -> bytes:
        return b"agentnode.capability.v1/approval\n" + _canon({
            "approval_id": self.approval_id,
            "invocation_hash": self.invocation_hash,
            "human_actor": self.human_actor,
            "granted_scope": self.granted_scope,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "operator_key_id": self.operator_key_id,
        })


def grant(
    invocation_hash: str,
    *,
    human_actor: str,
    operator_private: Ed25519PrivateKey,
    operator_public_bytes: bytes,
    ttl_seconds: float = 300.0,
    granted_scope: dict[str, Any] | None = None,
    now: float | None = None,
) -> Approval:
    """Operator-only: issue a single-use approval bound to *invocation_hash*.

    Requires the operator's private key -- this is the human channel. The model
    path cannot call this (it has no key).
    """
    t = time.time() if now is None else now
    body_fields = {
        "approval_id": uuid.uuid4().hex,
        "invocation_hash": invocation_hash,
        "human_actor": human_actor,
        "granted_scope": granted_scope or {},
        "issued_at": t,
        "expires_at": t + ttl_seconds,
        "operator_key_id": compute_key_id(operator_public_bytes),
        "operator_public": operator_public_bytes.hex(),
    }
    ap = Approval(signature="", **body_fields)
    sig = sign_payload(ap._signed_body(), operator_private)
    return Approval(signature=sig.hex(), **body_fields)


def verify(approval: Approval, invocation_hash: str, *, trusted_operator_public_bytes: bytes,
           now: float | None = None) -> None:
    """Raise ApprovalError unless *approval* is a valid, unexpired approval for *invocation_hash*.

    Checks, in order: the operator key is the trusted one; the signature verifies;
    the approval binds exactly this invocation; it has not expired.
    """
    t = time.time() if now is None else now
    if bytes.fromhex(approval.operator_public) != trusted_operator_public_bytes:
        raise ApprovalError("approval signed by an untrusted operator key")
    if approval.operator_key_id != compute_key_id(trusted_operator_public_bytes):
        raise ApprovalError("approval operator_key_id does not match the operator key")
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(approval.operator_public)).verify(
            bytes.fromhex(approval.signature), approval._signed_body())
    except Exception as exc:  # noqa: BLE001
        raise ApprovalError(f"approval signature does not verify: {type(exc).__name__}") from exc
    if approval.invocation_hash != invocation_hash:
        raise ApprovalError("approval does not bind this invocation (tuple changed after approval)")
    if t > approval.expires_at:
        raise ApprovalError("approval has expired")


class ApprovalStore:
    """Holds issued approvals and consumes each exactly once, atomically."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._issued: dict[str, Approval] = {}
        self._consumed: set[str] = set()

    def put(self, approval: Approval) -> None:
        with self._lock:
            self._issued[approval.approval_id] = approval

    def find_unconsumed_for(self, invocation_hash: str) -> str | None:
        """The approval_id of an unconsumed approval bound to *invocation_hash*, if any."""
        with self._lock:
            for aid, ap in self._issued.items():
                if aid not in self._consumed and ap.invocation_hash == invocation_hash:
                    return aid
        return None

    def load_dir(self, path: str | os.PathLike[str]) -> int:
        """Load approvals an operator deposited as JSON files (the out-of-band human channel)."""
        d = Path(path)
        n = 0
        if not d.exists():
            return 0
        for f in sorted(d.glob("*.json")):
            try:
                self.put(Approval.from_dict(json.loads(f.read_text(encoding="utf-8"))))
                n += 1
            except Exception:  # noqa: BLE001
                continue
        return n

    def consume(self, approval_id: str, invocation_hash: str, *,
                trusted_operator_public_bytes: bytes, now: float | None = None) -> Approval:
        """Atomically verify + mark consumed. A second consume of the same id refuses."""
        with self._lock:
            if approval_id in self._consumed:
                raise ApprovalError("approval already used (single-use)")
            ap = self._issued.get(approval_id)
            if ap is None:
                raise ApprovalError("no such approval")
            verify(ap, invocation_hash, trusted_operator_public_bytes=trusted_operator_public_bytes, now=now)
            self._consumed.add(approval_id)
            return ap


def effective_grant(requested: dict[str, Any], granted_scope: dict[str, Any]) -> dict[str, Any]:
    """The effective grant: never more than requested, narrowed by the human grant.

    Slice 1 permissions: network.allowed_domains and env_keys as sets (granted must
    be a subset of requested); booleans are ANDed. An empty granted_scope means
    'exactly the requested permissions were approved'.
    """
    if not granted_scope:
        return requested
    eff: dict[str, Any] = dict(requested)
    req_net = set((requested.get("network") or {}).get("allowed_domains") or [])
    grant_net = set((granted_scope.get("network") or {}).get("allowed_domains") or [])
    if not grant_net <= req_net:
        raise ApprovalError("granted network domains exceed requested")
    if "network" in granted_scope:
        eff["network"] = {"allowed_domains": sorted(grant_net)}
    req_env = set(requested.get("env_keys") or [])
    grant_env = set(granted_scope.get("env_keys") or [])
    if not grant_env <= req_env:
        raise ApprovalError("granted env_keys exceed requested")
    if "env_keys" in granted_scope:
        eff["env_keys"] = sorted(grant_env)
    for b in ("code_execution",):
        if b in granted_scope:
            eff[b] = bool(requested.get(b)) and bool(granted_scope.get(b))
    return eff
