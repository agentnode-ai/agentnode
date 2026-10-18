"""The signed, hash-chained catalog security ledger (review Q-CAT-0001 C9).

The frozen capability run ledger records WHAT ran; it carries no caller identity. This ledger
records WHO asked for WHAT and what the catalog decided, for every authenticated catalog decision
(list page, search, describe, approval_required, refusals with a stable reason code, overload,
terminal result) and every operator change. Privacy: arguments and search queries are recorded as
canonical-JSON DIGESTS with a length, never raw.

Each entry is ``{seq, ledger_id, prev_hash, body, body_digest, signature}``; the Ed25519 signature
(audit key) covers ``{seq, ledger_id, prev_hash, body_digest}`` under a domain tag. Appends are
serialised across threads AND processes (a lock file) and fsynced.

Failure behaviour: a decision event that cannot be appended raises :class:`AuditUnavailable`, and
the caller refuses before dispatching or answering. A TERMINAL event of a run that already happened
which cannot be appended is written O_EXCL to ``<ledger>.recovery/`` and re-ingested on the next
successful append, so a completed run is never lost from this ledger.

Limitation, stated plainly: like every hash chain without an externally anchored head, the ledger
detects edits and reordering, not truncation of its tail or a rollback to an older file. The
evidence records its head for that reason.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from agentnode_sdk.remote_mcp.catalog import CatalogLock, canonical

_DOMAIN = b"agentnode.mcp-catalog.v1/audit-entry\n"
_GENESIS = "0" * 64


class AuditUnavailable(Exception):
    """The catalog ledger could not record a decision; the decision must not take effect."""


class AuditTampered(Exception):
    """The ledger fails verification (edited, reordered or mis-signed)."""


def digest_of(value: Any) -> dict[str, Any]:
    """A privacy-safe stand-in for a raw value: its canonical digest and length."""
    try:
        data = canonical(value)
    except (TypeError, ValueError):
        data = repr(value).encode("utf-8")
    return {"sha256": hashlib.sha256(data).hexdigest(), "length": len(data)}


class CatalogLedger:
    def __init__(self, path: str | os.PathLike[str], *, signer_private: Ed25519PrivateKey,
                 signer_public_bytes: bytes, ledger_id: str = "catalog-L1"):
        self.path = Path(path)
        self.recovery = Path(str(self.path) + ".recovery")
        self.ledger_id = ledger_id
        self._private = signer_private
        self.public_bytes = signer_public_bytes
        self._lock = threading.Lock()
        self._file_lock = CatalogLock(Path(str(self.path) + ".lock"))

    # ------------------------------------------------------------------ internals
    def _entries(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        out = []
        with open(self.path, encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    out.append(json.loads(line))
        return out

    def _tail(self) -> tuple[int, str]:
        entries = self._entries()
        if not entries:
            return 0, _GENESIS
        last = entries[-1]
        return int(last["seq"]), _entry_hash(last)

    def _write_entry(self, body: dict[str, Any]) -> dict[str, Any]:
        seq, prev = self._tail()
        body_digest = hashlib.sha256(canonical(body)).hexdigest()
        head = {"seq": seq + 1, "ledger_id": self.ledger_id, "prev_hash": prev, "body_digest": body_digest}
        signature = self._private.sign(_DOMAIN + canonical(head)).hex()
        entry = dict(head, body=body, signature=signature)
        line = (json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(self.path), os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_BINARY", 0), 0o600)
        try:
            os.write(fd, line)
            os.fsync(fd)
        finally:
            os.close(fd)
        return entry

    def _ingest_recovery(self) -> None:
        if not self.recovery.is_dir():
            return
        for p in sorted(self.recovery.glob("*.json")):
            body = json.loads(p.read_text(encoding="utf-8"))
            body["recovered"] = True
            self._write_entry(body)
            os.remove(p)

    # ------------------------------------------------------------------ public
    def append(self, kind: str, **fields: Any) -> dict[str, Any]:
        """Record one decision. Raises :class:`AuditUnavailable` if it cannot be recorded."""
        body = {"kind": kind, "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **fields}
        try:
            with self._lock, self._file_lock:
                self._ingest_recovery()
                return self._write_entry(body)
        except Exception as exc:  # noqa: BLE001
            raise AuditUnavailable(f"catalog audit append failed: {type(exc).__name__}") from exc

    def record_terminal(self, kind: str, *, key: str, **fields: Any) -> dict[str, Any] | None:
        """Record the terminal event of a run that already happened; never lose it.

        If the append fails, the body is kept as ``<ledger>.recovery/<key>.json`` (O_EXCL) and the
        next successful append re-ingests it. Returns the entry, or None if it went to recovery.
        """
        try:
            return self.append(kind, **fields)
        except AuditUnavailable:
            body = {"kind": kind, "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **fields}
            self.recovery.mkdir(parents=True, exist_ok=True)
            safe = "".join(c for c in key if c.isalnum() or c in "-_")[:96] or "run"
            target = self.recovery / f"{safe}-{time.time_ns()}.json"
            fd = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(body, fh, sort_keys=True)
                fh.flush()
                os.fsync(fh.fileno())
            return None

    def head(self) -> dict[str, Any]:
        seq, h = self._tail()
        return {"ledger_id": self.ledger_id, "seq": seq, "head_hash": h}

    def verify(self, *, trusted_public_bytes: bytes | None = None) -> int:
        """Re-check every signature and link. Returns the number of entries; raises on tamper."""
        pub = Ed25519PublicKey.from_public_bytes(trusted_public_bytes or self.public_bytes)
        prev = _GENESIS
        entries = self._entries()
        for i, e in enumerate(entries, start=1):
            if e.get("seq") != i or e.get("ledger_id") != self.ledger_id:
                raise AuditTampered(f"entry {i}: sequence or ledger id is wrong")
            if e.get("prev_hash") != prev:
                raise AuditTampered(f"entry {i}: broken link")
            if hashlib.sha256(canonical(e["body"])).hexdigest() != e.get("body_digest"):
                raise AuditTampered(f"entry {i}: body digest mismatch")
            head = {k: e[k] for k in ("seq", "ledger_id", "prev_hash", "body_digest")}
            try:
                pub.verify(bytes.fromhex(e["signature"]), _DOMAIN + canonical(head))
            except Exception as exc:  # noqa: BLE001
                raise AuditTampered(f"entry {i}: signature does not verify") from exc
            prev = _entry_hash(e)
        return len(entries)


def _entry_hash(entry: dict[str, Any]) -> str:
    head = {k: entry[k] for k in ("seq", "ledger_id", "prev_hash", "body_digest", "signature")}
    return hashlib.sha256(canonical(head)).hexdigest()
