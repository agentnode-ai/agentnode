"""A local development index for capabilities: register / search / list / describe.

Deliberately local and file-backed. Slice 1 has no public registry and no
attestation service -- the trust that matters here is the manifest's own Ed25519
signature, which ``register`` verifies before it will store anything, and the
version immutability it enforces.

``describe`` is where honesty is visible: it renders the capability's provenance
and signer roles, the REQUESTED permissions, the execution model, and the one
worker target's properties -- and it never presents a publisher-asserted achieved
guarantee. Whether a run was isolated is computed by the runtime from the worker
it actually ran on; a local-development worker is labelled shared-kernel.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentnode_sdk.capability import manifest as M


class IndexError_(Exception):
    """A registration the index refuses -- malformed, unsigned, or an immutable-version clash."""


@dataclass(frozen=True)
class ResolvedCapability:
    manifest: dict[str, Any]
    capability_id: str
    capability_version: str
    artifact_digest: dict[str, str]


class LocalIndex:
    def __init__(self, root: str | os.PathLike[str]):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    # -- storage layout ---------------------------------------------------- #
    def _path(self, capability_id: str, version: str) -> Path:
        pub, _, name = capability_id.partition("/")
        return self.root / pub / name / f"{version}.json"

    def _write_atomic(self, path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    # -- content-addressed blob store (the imported server's bytes) -------- #
    def put_blob(self, data: bytes) -> str:
        """Store *data* by its sha256 and return the hex digest. Idempotent."""
        digest = hashlib.sha256(data).hexdigest()
        path = self.root / "blobs" / digest
        if not path.exists():
            self._write_atomic(path, data)
        return digest

    def get_blob(self, digest: str) -> bytes:
        path = self.root / "blobs" / digest
        if not path.exists():
            raise IndexError_(f"no blob for digest {digest}")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != digest:
            raise IndexError_(f"blob {digest} is corrupt (digest mismatch)")
        return data

    # -- register ---------------------------------------------------------- #
    def register(self, signed_manifest: dict[str, Any]) -> ResolvedCapability:
        """Verify and store a signed manifest. Refuse malformed, unsigned, or a changed existing version.

        Verification requires a VALID signature for a recorded signer role. Version
        immutability is enforced here: a second, DIFFERENT manifest for an existing
        ``(capability_id, capability_version)`` -- including one that only adds a
        tool -- is refused; re-registering the byte-identical manifest is idempotent.
        """
        errors = M.validate_manifest(signed_manifest)
        if errors:
            raise IndexError_("malformed manifest: " + "; ".join(errors))
        res = M.verify_manifest(signed_manifest)
        if not res.ok:
            raise IndexError_(f"refusing to register an unverified manifest: {res.status.value} ({res.reason})")

        cid = signed_manifest["capability_id"]
        ver = signed_manifest["capability_version"]
        path = self._path(cid, ver)
        if path.exists():
            existing = json.loads(path.read_text(encoding="utf-8"))
            if not M.version_is_immutable(existing, signed_manifest):
                raise IndexError_(
                    f"{cid}@{ver} already registered with different content; "
                    f"a change requires a new version (versions are immutable)")
            # identical -> idempotent
        else:
            self._write_atomic(path, M._canonical_json(signed_manifest) + b"\n")

        return ResolvedCapability(signed_manifest, cid, ver, signed_manifest["artifact_digest"])

    # -- read -------------------------------------------------------------- #
    def _all(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for p in sorted(self.root.rglob("*.json")):
            try:
                out.append(json.loads(p.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        return out

    def list(self) -> list[dict[str, str]]:
        return [{"capability_id": m["capability_id"], "capability_version": m["capability_version"],
                 "execution_model": m.get("execution_model", "")}
                for m in self._all()]

    def search(self, query: str) -> list[dict[str, str]]:
        q = (query or "").lower()
        hits: list[dict[str, str]] = []
        for m in self._all():
            tool_names = [t.get("name", "") for t in m.get("tools", [])]
            haystack = " ".join([m.get("capability_id", ""), *tool_names,
                                 *(t.get("description", "") for t in m.get("tools", []))]).lower()
            if q in haystack:
                hits.append({"capability_id": m["capability_id"],
                             "capability_version": m["capability_version"],
                             "tools": ",".join(tool_names)})
        return hits

    def resolve(self, capability_id: str, version: str | None = None) -> ResolvedCapability:
        candidates = [m for m in self._all() if m["capability_id"] == capability_id
                      and (version is None or m["capability_version"] == version)]
        if not candidates:
            raise IndexError_(f"capability not found: {capability_id}"
                              + (f"@{version}" if version else ""))
        # highest version when unspecified (simple, deterministic SemVer-ish sort)
        chosen = max(candidates, key=lambda m: _semver_key(m["capability_version"]))
        return ResolvedCapability(chosen, chosen["capability_id"],
                                  chosen["capability_version"], chosen["artifact_digest"])

    def describe(self, capability_id: str, version: str | None = None) -> dict[str, Any]:
        """A resolved, honest view: provenance, signer roles, requested permissions, model, target limits.

        Guarantees are REQUESTED/required, never asserted-as-achieved. The only
        execution target in slice 1 is the accepted worker; its isolation is
        computed at run time from the worker's own measurement, and a
        local-development worker is explicitly labelled shared-kernel.
        """
        resolved = self.resolve(capability_id, version)
        man = resolved.manifest
        sigs = man.get("signatures", [])
        return {
            "capability_id": man["capability_id"],
            "capability_version": man["capability_version"],
            "publisher": man["publisher"],
            "execution_model": man["execution_model"],
            "provenance": {
                "signer_roles": [s.get("role") for s in sigs],
                "key_ids": {s.get("role"): s.get("key_id") for s in sigs},
                "upstream_signed": any(s.get("role") == "upstream_publisher" for s in sigs),
                "source": man.get("source", {}),
            },
            "requested_permissions": man.get("permissions", {}),
            "tools": [{"name": t.get("name"), "description": t.get("description", ""),
                       "tool_schema_digest": t.get("tool_schema_digest")}
                      for t in man.get("tools", [])],
            "artifact_digest": man["artifact_digest"],
            "schema_digest": man["schema_digest"],
            "guarantees": {
                "kind": "required-not-asserted",
                "note": "This manifest states required properties only. Whether a run was isolated "
                        "is COMPUTED by the runtime from the worker it actually ran on; the manifest "
                        "never asserts an achieved guarantee.",
            },
            "targets": [{
                "kind": "accepted_worker",
                "execution_model": man["execution_model"],
                "isolation": "computed_at_run_time_from_worker_measurement",
                "caveat": "A local-development / single-host worker shares a kernel with the control "
                          "plane; it is not a control-plane isolation boundary and not a multi-tenant "
                          "boundary. Only properties the worker measures for a run are claimed.",
            }],
        }


def _semver_key(v: str) -> tuple[int, int, int]:
    try:
        a, b, c = (int(x) for x in v.split("."))
        return (a, b, c)
    except Exception:  # noqa: BLE001
        return (0, 0, 0)
