"""Signed releases of the dial-back worker (local-worker slice 2, Codex Q-LW2-0001 D7 as amended by Q-LW2-0003 Q4;
Q-LW2-0002 Q1/Q4).

One canonical JSON manifest per release, and one detached Ed25519 signature over

    b"agentnode-worker-release/1\\n" + <the manifest's exact bytes>

made with the RELEASE key, whose private half never sits on a server. Both bootstraps (Linux `install.sh` with
OpenSSL 3, Windows `install-agentnode-worker.ps1` with its vendored verifier) carry the public key and check the
signature, the expiry, the platform and the monotonic floor BEFORE they extract, import, install or run anything;
every artifact is then checked by SHA-256 and size. The manifest:

    {"schema": "agentnode-worker-release/1", "channel", "release_seq", "min_release_seq", "version", "git_commit",
     "issued_at", "expires_at", "compat": {...}, "pins": {<worker.pins block>},
     "artifacts": [{"name", "kind", "platform", "sha256", "size", "source", "version"}]}

`release_seq` only grows: the floor (root-owned) refuses anything lower (`release_downgrade`); a rollback is a NEW
manifest with a higher sequence that names older artifacts. A manifest lives at most 30 days (`release_expired`).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path

SCHEMA = "agentnode-worker-release/1"
DOMAIN = b"agentnode-worker-release/1\n"
MAX_DAYS = 30
KINDS = ("wheel", "installer", "bootstrap", "rootfs", "rpm", "sbom", "unit", "verifier")
PLATFORMS = ("any", "linux-x86_64", "windows-x86_64", "wsl-rocky10-x86_64")
KEYS = ("schema", "channel", "release_seq", "min_release_seq", "version", "git_commit", "issued_at", "expires_at",
        "compat", "pins", "artifacts")
ARTIFACT_KEYS = ("name", "kind", "platform", "sha256", "size", "source", "version")
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+~-]{0,200}\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
ED25519_SPKI_PREFIX = bytes.fromhex("302a300506032b6570032100")


class ReleaseRefused(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code + (": " + detail if detail else ""))
        self.code = code
        self.detail = detail


def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def signed_bytes(manifest_bytes: bytes) -> bytes:
    return DOMAIN + manifest_bytes


def public_pem(raw32: bytes) -> str:
    """The Ed25519 public key as SPKI PEM, for `openssl pkeyutl -verify -pubin`."""
    import base64
    der = ED25519_SPKI_PREFIX + bytes(raw32)
    b = base64.b64encode(der).decode("ascii")
    return "-----BEGIN PUBLIC KEY-----\n%s\n-----END PUBLIC KEY-----\n" % b


def parse(manifest_bytes: bytes) -> dict:
    """The manifest, only if its bytes ARE its canonical form and its shape is the closed one."""
    try:
        doc = json.loads(manifest_bytes.decode("ascii"))
    except (UnicodeDecodeError, ValueError):
        raise ReleaseRefused("release_manifest_invalid", "not ASCII JSON") from None
    if canonical(doc) != manifest_bytes:
        raise ReleaseRefused("release_manifest_invalid", "not in canonical form")
    if not isinstance(doc, dict) or set(doc) != set(KEYS) or doc["schema"] != SCHEMA:
        raise ReleaseRefused("release_manifest_invalid", "shape")
    for k in ("release_seq", "min_release_seq"):
        if not isinstance(doc[k], int) or isinstance(doc[k], bool) or doc[k] < 1:
            raise ReleaseRefused("release_manifest_invalid", k)
    if doc["min_release_seq"] > doc["release_seq"]:
        raise ReleaseRefused("release_manifest_invalid", "min_release_seq above release_seq")
    if not isinstance(doc["artifacts"], list) or not doc["artifacts"]:
        raise ReleaseRefused("release_manifest_invalid", "artifacts")
    names = set()
    for a in doc["artifacts"]:
        if not isinstance(a, dict) or set(a) != set(ARTIFACT_KEYS) or not _NAME.match(str(a["name"])) \
                or a["kind"] not in KINDS or a["platform"] not in PLATFORMS or not _HEX64.match(str(a["sha256"])) \
                or not isinstance(a["size"], int) or isinstance(a["size"], bool) or a["size"] < 0 \
                or a["name"] in names:
            raise ReleaseRefused("release_manifest_invalid", "artifact %r" % (a.get("name") if isinstance(a, dict) else a))
        names.add(a["name"])
    issued, expires = float(doc["issued_at"]), float(doc["expires_at"])
    if not 0 < expires - issued <= MAX_DAYS * 86400:
        raise ReleaseRefused("release_manifest_invalid", "a release lives at most %d days" % MAX_DAYS)
    return doc


def verify(manifest_bytes: bytes, signature: bytes, public_raw: bytes, *, now: float | None = None,
           floor: int | None = None, platform: str | None = None) -> dict:
    """Signature FIRST (nothing of an unsigned manifest is believed), then shape, expiry, floor and platform."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    try:
        Ed25519PublicKey.from_public_bytes(bytes(public_raw)).verify(bytes(signature), signed_bytes(manifest_bytes))
    except (InvalidSignature, ValueError):
        raise ReleaseRefused("release_signature_invalid") from None
    doc = parse(manifest_bytes)
    when = float(now if now is not None else time.time())
    if when >= float(doc["expires_at"]) or when < float(doc["issued_at"]) - 300:
        raise ReleaseRefused("release_expired", "valid %s .. %s" % (doc["issued_at"], doc["expires_at"]))
    if floor is not None and doc["release_seq"] < int(floor):
        raise ReleaseRefused("release_downgrade", "sequence %d is below the floor %d" % (doc["release_seq"], floor))
    # at least one artifact FOR this platform: "any" files (an SBOM, an installer) say nothing about support
    if platform is not None and not any(a["platform"] == platform for a in doc["artifacts"]):
        raise ReleaseRefused("release_platform_mismatch", platform)
    return doc


def check_artifact(doc: dict, name: str, data_path) -> dict:
    """An artifact's bytes against its manifest entry: size, then SHA-256 (`artifact_hash_mismatch`)."""
    entry = next((a for a in doc["artifacts"] if a["name"] == name), None)
    if entry is None:
        raise ReleaseRefused("artifact_hash_mismatch", "%s is not in the manifest" % name)
    p = Path(data_path)
    if not p.is_file() or p.stat().st_size != entry["size"]:
        raise ReleaseRefused("artifact_hash_mismatch", "%s has another size" % name)
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    if h.hexdigest() != entry["sha256"]:
        raise ReleaseRefused("artifact_hash_mismatch", name)
    return entry


# ---------------------------------------------------------------------- the floor (root-owned, monotonic)
def read_floor(path) -> int:
    """The highest sequence ever installed here. Absent = 0 (first install). Unreadable, malformed, or not
    root-owned/narrow on POSIX = `release_floor_unreadable` -- never "no floor"."""
    p = Path(path)
    if not p.exists():
        return 0
    try:
        if os.name == "posix":
            st = p.stat()
            if st.st_uid != 0 or st.st_mode & 0o022:
                raise ReleaseRefused("release_floor_unreadable", "the floor must be root's and writable by no one else")
        doc = json.loads(p.read_text(encoding="ascii"))
        seq = doc["release_seq"]
        if not isinstance(seq, int) or isinstance(seq, bool) or seq < 0:
            raise ValueError
        return seq
    except ReleaseRefused:
        raise
    except (OSError, ValueError, KeyError, TypeError):
        raise ReleaseRefused("release_floor_unreadable") from None


def raise_floor(path, seq: int) -> None:
    current = read_floor(path)
    if seq < current:
        raise ReleaseRefused("release_downgrade", "the floor is %d" % current)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name("." + p.name + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    with os.fdopen(fd, "w", encoding="ascii") as fh:
        json.dump({"release_seq": int(seq), "at": time.time()}, fh)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(str(tmp), str(p))


# ---------------------------------------------------------------------- building a release (off any server)
def sbom(doc_without_sbom: dict) -> dict:
    """CycloneDX 1.5: every distributed artifact, with what its source publishes and nothing inferred."""
    comps = []
    for a in doc_without_sbom["artifacts"]:
        if a["kind"] == "sbom":
            continue
        comps.append({"type": "file" if a["kind"] in ("installer", "bootstrap", "unit", "verifier") else "library",
                      "name": a["name"], "version": a["version"] or "NOASSERTION",
                      "hashes": [{"alg": "SHA-256", "content": a["sha256"]}],
                      "externalReferences": [{"type": "distribution", "url": a["source"]}] if a["source"] else [],
                      "licenses": [{"license": {"name": "NOASSERTION"}}]})
    return {"bomFormat": "CycloneDX", "specVersion": "1.5", "version": 1,
            "metadata": {"timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(doc_without_sbom["issued_at"])),
                         "component": {"type": "application", "name": "agentnode-worker",
                                       "version": doc_without_sbom["version"]}},
            "components": comps}


def build(directory, *, private_key, channel: str, release_seq: int, min_release_seq: int, version: str,
          git_commit: str, pins: dict, artifacts: list[dict], compat: dict, now: float | None = None,
          days: int = MAX_DAYS) -> dict:
    """Write manifest.json, manifest.sig and sbom.json into `directory`. `artifacts` entries name files there:
    {"name", "kind", "platform", "source", "version"}; size and SHA-256 are measured here."""
    d = Path(directory)
    issued = int(now if now is not None else time.time())     # whole seconds: one spelling in every language
    rows = []
    for a in artifacts:
        p = d / a["name"]
        rows.append({"name": a["name"], "kind": a["kind"], "platform": a["platform"], "source": a.get("source", ""),
                     "version": a.get("version", ""), "size": p.stat().st_size,
                     "sha256": hashlib.sha256(p.read_bytes()).hexdigest()})
    doc = {"schema": SCHEMA, "channel": channel, "release_seq": int(release_seq), "min_release_seq": int(min_release_seq),
           "version": version, "git_commit": git_commit, "issued_at": issued,
           "expires_at": issued + days * 86400, "compat": compat, "pins": pins, "artifacts": rows}
    bom = (json.dumps(sbom(doc), sort_keys=True, indent=1) + "\n").encode("utf-8")
    (d / "sbom.json").write_bytes(bom)
    doc["artifacts"].append({"name": "sbom.json", "kind": "sbom", "platform": "any", "source": "", "version": version,
                             "size": len(bom), "sha256": hashlib.sha256(bom).hexdigest()})
    data = canonical(doc)
    parse(data)
    (d / "manifest.json").write_bytes(data)
    (d / "manifest.sig").write_bytes(private_key.sign(signed_bytes(data)))
    return doc


def main(argv=None) -> int:
    """verify:  python -m agentnode_sdk.worker.release verify DIR --pubkey HEX [--floor FILE] [--platform P]"""
    import argparse
    import sys

    p = argparse.ArgumentParser(prog="agentnode-worker-release")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("verify")
    s.add_argument("dir")
    s.add_argument("--pubkey", required=True)
    s.add_argument("--floor", default=None)
    s.add_argument("--platform", default=None)
    args = p.parse_args(argv)
    d = Path(args.dir)
    try:
        floor = read_floor(args.floor) if args.floor else None
        doc = verify((d / "manifest.json").read_bytes(), (d / "manifest.sig").read_bytes(), bytes.fromhex(args.pubkey),
                     floor=floor, platform=args.platform)
        for a in doc["artifacts"]:
            if args.platform is None or a["platform"] in (args.platform, "any"):
                check_artifact(doc, a["name"], d / a["name"])
    except ReleaseRefused as refused:
        print("REFUSED %s %s" % (refused.code, refused.detail))
        return 2
    except OSError as exc:
        print("REFUSED artifact_hash_mismatch %s" % type(exc).__name__)
        return 2
    print("VERIFIED release %d (%s) channel %s" % (doc["release_seq"], doc["version"], doc["channel"]))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
