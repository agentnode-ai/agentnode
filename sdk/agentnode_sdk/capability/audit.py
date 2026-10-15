"""A signed, chain-bound audit result (S9).

Each run appends one entry whose body BINDS the job, the manifest and the result:
the full tool-invocation identity, the manifest digest, the job/run id, the
normalised input digest, the approval id and the human actor, the execution
model, the worker identity, the achieved isolation, the output digest and the
terminal status, plus a timestamp and nonce. The body is signed (Ed25519), and
the entry carries a sequence number, a ledger id and the previous entry's hash,
so modification and reordering inside the retained chain are detectable.

Honesty (carried in ``integrity_statement``): this is tamper-EVIDENT, not
rollback/truncation-proof. Deleting the tail, or replacing the whole file with a
shorter valid prefix, is not detected without a checkpoint anchored OUTSIDE this
file -- and an external anchor is explicitly out of slice 1.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from agentnode_sdk.signing_key import compute_key_id, sign_payload

_GENESIS = "0" * 64


def _canon(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class AuditError(Exception):
    pass


@dataclass(frozen=True)
class AuditEntry:
    seq: int
    ledger_id: str
    prev_hash: str
    body: dict[str, Any]
    body_digest: str
    key_id: str
    public_key: str
    signature: str

    def entry_hash(self) -> str:
        return _sha256(_canon({"seq": self.seq, "ledger_id": self.ledger_id,
                              "prev_hash": self.prev_hash, "body_digest": self.body_digest,
                              "signature": self.signature}))


class AuditLedger:
    def __init__(self, path: str | Path, *, ledger_id: str,
                 signer_private: Ed25519PrivateKey, signer_public_bytes: bytes):
        self.path = Path(path)
        self.ledger_id = ledger_id
        self._priv = signer_private
        self._pub = signer_public_bytes
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _entries(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                out.append(json.loads(line))
        return out

    def _head_hash(self) -> tuple[int, str]:
        entries = self._entries()
        if not entries:
            return 0, _GENESIS
        last = entries[-1]
        return last["seq"], AuditEntry(**last).entry_hash()

    def record_result(self, *, invocation, approval, exec_result, worker_identity: str) -> AuditEntry:
        """Append a signed entry binding this run's job, manifest and result."""
        with self._lock:
            seq, prev = self._head_hash()
            body = {
                "kind": "agentnode.capability.v1/run-result",
                "capability_id": invocation.capability_id,
                "capability_version": invocation.capability_version,
                "manifest_digest": invocation.manifest_digest,
                "artifact_digest": invocation.artifact_digest,
                "tool_name": invocation.tool_name,
                "tool_schema_digest": invocation.tool_schema_digest,
                "execution_model": invocation.execution_model,
                "input_digest": invocation.input_digest,
                "invocation_hash": invocation.invocation_hash,
                "job_run_id": exec_result.run_id,
                "approval_id": approval.approval_id,
                "human_actor": approval.human_actor,
                "worker_identity": worker_identity,
                "achieved_isolation": exec_result.achieved_isolation,
                "terminal_status": "ok" if exec_result.ok else "error",
                "output_digest": _sha256(_canon(exec_result.result)),
                "exit_code": exec_result.exit_code,
                "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "nonce": uuid.uuid4().hex,
            }
            body_digest = _sha256(_canon(body))
            sig = sign_payload(
                b"agentnode.capability.v1/audit\n"
                + _canon({"seq": seq + 1, "ledger_id": self.ledger_id, "prev_hash": prev,
                          "body_digest": body_digest}),
                self._priv)
            entry = AuditEntry(seq=seq + 1, ledger_id=self.ledger_id, prev_hash=prev,
                               body=body, body_digest=body_digest,
                               key_id=compute_key_id(self._pub), public_key=self._pub.hex(),
                               signature=sig.hex())
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(_as_dict(entry)) + "\n")
            return entry

    def verify(self, *, trusted_public_bytes: bytes | None = None) -> None:
        """Raise AuditError on any tamper or reordering detectable from the chain itself."""
        entries = self._entries()
        prev = _GENESIS
        for i, raw in enumerate(entries, start=1):
            e = AuditEntry(**raw)
            if e.seq != i:
                raise AuditError(f"sequence gap/reorder at position {i}: seq={e.seq}")
            if e.prev_hash != prev:
                raise AuditError(f"broken chain link at seq {e.seq}")
            if e.body_digest != _sha256(_canon(e.body)):
                raise AuditError(f"body digest mismatch at seq {e.seq} (body tampered)")
            pub = bytes.fromhex(e.public_key)
            if e.key_id != compute_key_id(pub):
                raise AuditError(f"key_id does not match public key at seq {e.seq}")
            if trusted_public_bytes is not None and pub != trusted_public_bytes:
                raise AuditError(f"entry {e.seq} signed by an untrusted key")
            try:
                Ed25519PublicKey.from_public_bytes(pub).verify(
                    bytes.fromhex(e.signature),
                    b"agentnode.capability.v1/audit\n"
                    + _canon({"seq": e.seq, "ledger_id": e.ledger_id, "prev_hash": e.prev_hash,
                              "body_digest": e.body_digest}))
            except Exception as exc:  # noqa: BLE001
                raise AuditError(f"signature does not verify at seq {e.seq}: {type(exc).__name__}") from exc
            prev = e.entry_hash()

    @staticmethod
    def integrity_statement() -> dict[str, Any]:
        return {
            "tamper_evident": True,
            "detects": ["body modification", "reordering", "broken links", "bad/foreign signatures"],
            "does_not_detect_without_external_anchor": ["truncation of the tail", "rollback to an "
                "older valid prefix", "deletion of the whole ledger"],
            "note": "An external signed checkpoint/head is out of slice 1 by scope; until then this "
                    "ledger is tamper-evident but rollback/truncation-vulnerable, and is described as such.",
        }


def _as_dict(e: AuditEntry) -> dict[str, Any]:
    return {"seq": e.seq, "ledger_id": e.ledger_id, "prev_hash": e.prev_hash, "body": e.body,
            "body_digest": e.body_digest, "key_id": e.key_id, "public_key": e.public_key,
            "signature": e.signature}
