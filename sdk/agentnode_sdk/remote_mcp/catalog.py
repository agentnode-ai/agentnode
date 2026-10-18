"""The per-account capability catalog behind the remote MCP endpoint.

One signed document on the control host decides which reviewed capabilities exist (``published``)
and which of them each account sees and may call (an explicit per-account list, or the
``default_active`` list for an account without one). Nothing global decides for every account and
every call any more -- there is no ``capability_id.txt`` in this path.

Trust and durability (review Q-CAT-0001 C1/C5):

* the document is canonical JSON, Ed25519-signed by the stand operator key; the server verifies it
  with ``stand/operator_pub.hex`` -- the configured trust anchor -- on EVERY load. A document that
  does not verify, is malformed, is missing after start, or carries a generation lower than one
  already seen (in ``history/`` or by this process) fails closed (:class:`CatalogUnavailable`); there
  is no last-known-good fallback;
* every write (publish, unpublish, account list, default list, rollback) and the server's run
  admission take ONE cross-process lock, so a revocation and a run admission are linearised: a call
  admitted before the revocation commits may finish, one that is not can never reach the worker;
* a write records the new generation in ``history/<generation>.json`` (fsynced) BEFORE it replaces
  ``catalog.json`` (temp + fsync + rename + directory fsync). Rollback writes a NEW generation that
  carries the old contents, so generations are strictly monotonic and an old cursor never becomes
  valid again;
* an entry is published only after the EXACT imported bytes are re-verified: the manifest at the
  pinned version verifies, is signed by the trusted operator (importing_publisher) key, exports the
  named tool, and its pinned blob hashes to ``source.package.tree_digest``.

The catalog is operator-only. No MCP tool can change it; ``catalog_cli`` is the operator's door.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from agentnode_sdk.capability import contract, tree_pack
from agentnode_sdk.capability import manifest as M
from agentnode_sdk.capability.index import LocalIndex
from agentnode_sdk.gateway import accounts as A
from agentnode_sdk.signing_key import compute_key_id

SCHEMA = "agentnode.mcp-catalog.v1"
_SIGNING_DOMAIN = b"agentnode.mcp-catalog.v1/document\n"
#: the three fixed meta-tools, always listed first, in this order
META_TOOLS = ("agentnode_search", "agentnode_describe", "agentnode_run")
#: the whole namespace is reserved for the platform's own tools
RESERVED_PREFIX = "agentnode_"
MAX_TOOL_NAME = 64
MAX_DOCUMENT_BYTES = 1 << 20
MAX_PUBLISHED = 500
MAX_ACCOUNTS = 10000
HISTORY_KEEP = 200
LOCK_STALE_SECONDS = 30.0
LOCK_TIMEOUT_SECONDS = 10.0
_TOOL_RE = re.compile(r"^[a-z0-9_]{1,64}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class CatalogUnavailable(Exception):
    """The catalog cannot be trusted right now: refuse (503), never fall back."""


class CatalogRefused(Exception):
    """An operator change the catalog refuses (stable ``code``)."""

    def __init__(self, code: str, detail: str):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


class RunNotAdmitted(Exception):
    """A call reached run admission but its exact entry is no longer active for the account."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def canonical(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def alias_for(capability_id: str, export: str) -> str:
    """The public MCP tool name of one capability export: deterministic, normalised, <= 64."""
    name = capability_id.partition("/")[2] or capability_id
    raw = f"{name}__{export}".lower()
    norm = re.sub(r"[^a-z0-9_]", "_", raw)
    if len(norm) > MAX_TOOL_NAME:
        norm = norm[: MAX_TOOL_NAME - 9] + "_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]
    return norm


@dataclass(frozen=True)
class Entry:
    tool: str
    capability_id: str
    version: str
    manifest_digest: str
    artifact_digest: str
    tree_digest: str
    export: str
    published_generation: int

    def to_dict(self) -> dict[str, Any]:
        return {"capability_id": self.capability_id, "version": self.version,
                "manifest_digest": self.manifest_digest, "artifact_digest": self.artifact_digest,
                "tree_digest": self.tree_digest, "export": self.export,
                "published_generation": self.published_generation}


@dataclass(frozen=True)
class Snapshot:
    """One verified, immutable catalog generation."""

    generation: int
    published: dict[str, Entry]
    accounts: dict[str, tuple[str, ...]]
    default_active: tuple[str, ...]
    digest: str
    document: dict[str, Any] = field(repr=False)

    def active_tools(self, account_id: str) -> tuple[str, ...]:
        """The account's explicit list if it has one (an explicit EMPTY list stays empty),
        otherwise the default list. Always sorted by tool name."""
        names = self.accounts[account_id] if account_id in self.accounts else self.default_active
        return tuple(sorted(n for n in names if n in self.published))

    def active_entries(self, account_id: str) -> list[Entry]:
        return [self.published[n] for n in self.active_tools(account_id)]

    def resolve(self, account_id: str, tool: str) -> Entry | None:
        return self.published.get(tool) if tool in self.active_tools(account_id) else None


def _validate_document(doc: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(doc, dict):
        return ["catalog must be an object"]
    want = {"schema", "generation", "published", "accounts", "default_active", "signature"}
    if set(doc) != want:
        errors.append(f"catalog keys must be exactly {sorted(want)}")
        return errors
    if doc["schema"] != SCHEMA:
        errors.append(f"schema must be {SCHEMA!r}")
    gen = doc["generation"]
    if not isinstance(gen, int) or isinstance(gen, bool) or gen < 1:
        errors.append("generation must be a positive integer")
    pub = doc["published"]
    if not isinstance(pub, dict) or len(pub) > MAX_PUBLISHED:
        errors.append("published must be an object of at most %d entries" % MAX_PUBLISHED)
        pub = {}
    entry_keys = {"capability_id", "version", "manifest_digest", "artifact_digest", "tree_digest", "export",
                  "published_generation"}
    for tool, e in pub.items():
        if not isinstance(tool, str) or not _TOOL_RE.match(tool) or tool.startswith(RESERVED_PREFIX):
            errors.append(f"invalid or reserved tool name {tool!r}")
            continue
        if not isinstance(e, dict) or set(e) != entry_keys:
            errors.append(f"entry {tool!r} must carry exactly {sorted(entry_keys)}")
            continue
        if not all(isinstance(e[k], str) and e[k] for k in entry_keys - {"published_generation"}):
            errors.append(f"entry {tool!r} has empty or non-string fields")
            continue
        if not _HEX64.match(e["manifest_digest"]) or not _HEX64.match(e["artifact_digest"]) \
                or not e["tree_digest"].startswith("sha256:") or not _HEX64.match(e["tree_digest"][7:]):
            errors.append(f"entry {tool!r} has malformed digests")
        if not isinstance(e["published_generation"], int) or e["published_generation"] < 1:
            errors.append(f"entry {tool!r} has an invalid published_generation")
        if alias_for(e["capability_id"], e["export"]) != tool:
            errors.append(f"entry {tool!r} is not the deterministic name of its capability export")
    accounts = doc["accounts"]
    if not isinstance(accounts, dict) or len(accounts) > MAX_ACCOUNTS:
        errors.append("accounts must be an object of at most %d accounts" % MAX_ACCOUNTS)
        accounts = {}
    for acct, rec in accounts.items():
        if not A.well_formed(acct):
            errors.append(f"invalid account id {acct!r}")
        if not isinstance(rec, dict) or set(rec) != {"active"} or not isinstance(rec["active"], list):
            errors.append(f"account {acct!r} must be {{'active': [...]}}")
            continue
        for name in rec["active"]:
            if name not in pub:
                errors.append(f"account {acct!r} activates an unpublished tool {name!r}")
        if len(set(rec["active"])) != len(rec["active"]):
            errors.append(f"account {acct!r} lists a tool twice")
    default = doc["default_active"]
    if not isinstance(default, list) or len(set(default)) != len(default):
        errors.append("default_active must be a list without duplicates")
    else:
        for name in default:
            if name not in pub:
                errors.append(f"default_active names an unpublished tool {name!r}")
    sig = doc["signature"]
    if not isinstance(sig, dict) or set(sig) != {"key_id", "signature"} \
            or not all(isinstance(v, str) for v in sig.values()):
        errors.append("signature must be {key_id, signature}")
    return errors


def _signed_bytes(doc: dict[str, Any]) -> bytes:
    body = {k: v for k, v in doc.items() if k != "signature"}
    return _SIGNING_DOMAIN + canonical(body)


def _fsync_dir(path: Path) -> None:
    if os.name == "nt":          # directory handles cannot be fsynced on Windows
        return
    fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_durably(path: Path, data: bytes, *, exclusive: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if exclusive:
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
    else:
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
    _fsync_dir(path.parent)


class CatalogLock:
    """A cross-process lock file (O_EXCL), shared by every catalog writer and run admission."""

    def __init__(self, path: Path, *, timeout: float = LOCK_TIMEOUT_SECONDS, stale: float = LOCK_STALE_SECONDS):
        self.path = path
        self.timeout = timeout
        self.stale = stale
        self._held = False

    def __enter__(self) -> "CatalogLock":
        deadline = time.monotonic() + self.timeout
        self.path.parent.mkdir(parents=True, exist_ok=True)
        while True:
            try:
                fd = os.open(str(self.path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w") as fh:
                    fh.write("%d %f" % (os.getpid(), time.time()))
                self._held = True
                return self
            except FileExistsError:
                try:
                    age = time.time() - os.path.getmtime(self.path)
                except OSError:
                    continue
                if age > self.stale:
                    try:
                        os.remove(self.path)        # a crashed holder; break it
                    except OSError:
                        pass
                    continue
                if time.monotonic() > deadline:
                    raise CatalogUnavailable("catalog lock is held by another process")
                time.sleep(0.01)

    def __exit__(self, *exc) -> None:
        if self._held:
            self._held = False
            try:
                os.remove(self.path)
            except OSError:
                pass


class Catalog:
    """The catalog of one stand. Reads verify every time; writes need the operator private key."""

    def __init__(self, stand: str | os.PathLike[str], operator_public_bytes: bytes, *,
                 operator_private: Ed25519PrivateKey | None = None, index: LocalIndex | None = None,
                 on_change=None):
        self.stand = Path(stand)
        self.dir = self.stand / "catalog"
        self.path = self.dir / "catalog.json"
        self.history = self.dir / "history"
        self.lock_path = self.dir / ".lock"
        self.operator_public_bytes = operator_public_bytes
        self.trusted_key_id = compute_key_id(operator_public_bytes)
        self._public = Ed25519PublicKey.from_public_bytes(operator_public_bytes)
        self._private = operator_private
        self.index = index if index is not None else LocalIndex(str(self.stand / "index"))
        self.on_change = on_change
        self._highest_seen = 0
        self._seen_lock = threading.Lock()
        self._running: dict[int, tuple[str, str, int]] = {}
        self._run_seq = 0

    # ------------------------------------------------------------------ read
    def lock(self) -> CatalogLock:
        return CatalogLock(self.lock_path)

    def _history_max(self) -> int:
        best = 0
        if self.history.is_dir():
            for p in self.history.glob("*.json"):
                stem = p.stem
                if stem.isdigit():
                    best = max(best, int(stem))
        return best

    def load(self) -> Snapshot:
        """Read, validate and verify the catalog. Fail closed on anything doubtful."""
        try:
            raw = self.path.read_bytes()
        except OSError as exc:
            raise CatalogUnavailable(f"catalog unreadable: {type(exc).__name__}") from exc
        if len(raw) > MAX_DOCUMENT_BYTES:
            raise CatalogUnavailable("catalog exceeds the size limit")
        try:
            doc = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise CatalogUnavailable("catalog is not valid JSON") from exc
        errors = _validate_document(doc)
        if errors:
            raise CatalogUnavailable("catalog invalid: " + "; ".join(errors[:5]))
        if doc["signature"]["key_id"] != self.trusted_key_id:
            raise CatalogUnavailable("catalog is not signed by the configured operator key")
        try:
            self._public.verify(bytes.fromhex(doc["signature"]["signature"]), _signed_bytes(doc))
        except (InvalidSignature, ValueError) as exc:
            raise CatalogUnavailable("catalog signature does not verify") from exc
        gen = doc["generation"]
        with self._seen_lock:
            floor = max(self._highest_seen, self._history_max())
            if gen < floor:
                raise CatalogUnavailable(f"catalog generation {gen} is older than {floor} (rollback refused)")
            self._highest_seen = max(self._highest_seen, gen)
        published = {t: Entry(tool=t, **e) for t, e in doc["published"].items()}
        accounts = {a: tuple(r["active"]) for a, r in doc["accounts"].items()}
        return Snapshot(generation=gen, published=published, accounts=accounts,
                        default_active=tuple(doc["default_active"]),
                        digest=hashlib.sha256(raw).hexdigest(), document=doc)

    # ------------------------------------------------------------------ run admission
    def admit(self, account_id: str, expected: Entry) -> tuple[int, int]:
        """Linearisation point of a run: under the catalog lock, the exact entry must still be
        active for the account. Returns (run_token, generation); raises :class:`RunNotAdmitted`."""
        with self.lock():
            snap = self.load()
            current = snap.resolve(account_id, expected.tool)
            if current is None:
                raise RunNotAdmitted("revoked_while_queued")
            if current != expected:
                raise RunNotAdmitted("catalog_changed")
            self._run_seq += 1
            token = self._run_seq
            self._running[token] = (account_id, expected.tool, snap.generation)
            return token, snap.generation

    def finish(self, run_token: int) -> None:
        self._running.pop(run_token, None)

    def running(self) -> list[tuple[str, str, int]]:
        return list(self._running.values())

    # ------------------------------------------------------------------ writes (operator)
    def _sign(self, doc: dict[str, Any]) -> dict[str, Any]:
        if self._private is None:
            raise CatalogRefused("catalog.read_only", "writing the catalog needs the operator private key")
        signed = dict(doc)
        signed["signature"] = {"key_id": self.trusted_key_id,
                               "signature": self._private.sign(_signed_bytes(doc)).hex()}
        return signed

    def _commit(self, doc: dict[str, Any], *, actor: str, command: str, old: Snapshot | None) -> Snapshot:
        doc = self._sign(doc)
        errors = _validate_document(doc)
        if errors:
            raise CatalogRefused("catalog.invalid", "; ".join(errors[:5]))
        data = canonical(doc) + b"\n"
        if len(data) > MAX_DOCUMENT_BYTES:
            raise CatalogRefused("catalog.too_large", "the catalog would exceed the size limit")
        _write_durably(self.history / ("%d.json" % doc["generation"]), data, exclusive=True)
        _write_durably(self.path, data)
        self._prune_history()
        new = self.load()
        if self.on_change is not None:
            self.on_change({"change": "catalog_change", "actor": actor, "command": command,
                            "old_generation": old.generation if old else 0,
                            "new_generation": new.generation,
                            "old_digest": old.digest if old else "", "new_digest": new.digest})
        return new

    def _prune_history(self) -> None:
        gens = sorted(int(p.stem) for p in self.history.glob("*.json") if p.stem.isdigit())
        for g in gens[:-HISTORY_KEEP]:
            try:
                os.remove(self.history / ("%d.json" % g))
            except OSError:
                pass

    def _base(self) -> tuple[dict[str, Any], Snapshot | None]:
        if not self.path.exists():
            if self._history_max():
                raise CatalogUnavailable("catalog.json is missing but history exists; refusing to start over")
            return {"schema": SCHEMA, "generation": 0, "published": {}, "accounts": {},
                    "default_active": []}, None
        snap = self.load()
        doc = copy.deepcopy({k: v for k, v in snap.document.items() if k != "signature"})
        return doc, snap

    def init(self, *, actor: str) -> Snapshot:
        """Create generation 1: an empty catalog. Refuses if a catalog or history exists."""
        with self.lock():
            if self.path.exists() or self._history_max():
                raise CatalogRefused("catalog.exists", "a catalog already exists")
            doc, old = self._base()
            doc["generation"] = 1
            return self._commit(doc, actor=actor, command="init", old=old)

    def _pin(self, capability_id: str, version: str, export: str) -> dict[str, Any]:
        """Re-verify the exact imported bytes and return the pinned entry body."""
        try:
            resolved = self.index.resolve(capability_id, version)
        except Exception as exc:  # noqa: BLE001
            raise CatalogRefused("publish.not_in_index", f"{capability_id}@{version} is not in the index") from exc
        man = resolved.manifest
        if man.get("capability_version") != version:
            raise CatalogRefused("publish.version_mismatch", "the index resolved a different version")
        # the importing_publisher signature itself must verify AND be the configured operator key
        res = M.verify_manifest(man, role="importing_publisher")
        if not res.ok:
            raise CatalogRefused("publish.unverified", f"importing_publisher signature does not verify: {res.reason}")
        if res.key_id != self.trusted_key_id:
            raise CatalogRefused("publish.untrusted_signer",
                                 "the capability is not signed by the configured operator (importing) key")
        if man.get("execution_model") != "agentnode_executed_mcp":
            raise CatalogRefused("publish.execution_model", "only worker-executed capabilities can be published")
        perms = man.get("permissions") or {}
        if (perms.get("network") or {}).get("allowed_domains") or perms.get("env_keys"):
            raise CatalogRefused("publish.network_or_secrets", "network or secret capabilities are out of scope")
        if not any(t.get("name") == export for t in man.get("tools", [])):
            raise CatalogRefused("publish.no_such_export", f"{capability_id} exports no tool {export!r}")
        pkg = (man.get("source") or {}).get("package") or {}
        if pkg.get("kind") != "vendored-tree" or not str(pkg.get("tree_digest", "")).startswith("sha256:"):
            raise CatalogRefused("publish.not_a_tree", "only pinned vendored-tree capabilities can be published")
        try:
            blob = self.index.get_blob(pkg["sha256"])
        except Exception as exc:  # noqa: BLE001
            raise CatalogRefused("publish.blob", "the pinned blob is missing or corrupt") from exc
        if tree_pack.tree_digest_of_tar(blob) != pkg["tree_digest"]:
            raise CatalogRefused("publish.tree_digest", "the pinned blob does not hash to its tree digest")
        return {"capability_id": capability_id, "version": version,
                "manifest_digest": contract.manifest_digest(man),
                "artifact_digest": man["artifact_digest"]["value"],
                "tree_digest": pkg["tree_digest"], "export": export}

    def publish(self, capability_id: str, version: str, export: str, *, actor: str) -> Snapshot:
        """Publish (or move to a new version) one capability export under its deterministic name."""
        tool = alias_for(capability_id, export)
        if tool.startswith(RESERVED_PREFIX) or tool in META_TOOLS:
            raise CatalogRefused("publish.reserved_name", f"{tool!r} is in the reserved agentnode_ namespace")
        body = self._pin(capability_id, version, export)
        with self.lock():
            doc, old = self._base()
            existing = doc["published"].get(tool)
            if existing is not None and (existing["capability_id"], existing["export"]) != (capability_id, export):
                raise CatalogRefused("publish.collision",
                                     f"{tool!r} already names {existing['capability_id']}::{existing['export']}")
            doc["generation"] += 1
            doc["published"][tool] = dict(body, published_generation=doc["generation"])
            return self._commit(doc, actor=actor, command=f"publish {tool} {capability_id}@{version}", old=old)

    def unpublish(self, tool: str, *, actor: str) -> Snapshot:
        """Revoke a tool everywhere: removed from published, every account list and the default."""
        with self.lock():
            doc, old = self._base()
            if tool not in doc["published"]:
                raise CatalogRefused("unpublish.unknown", f"{tool!r} is not published")
            doc["generation"] += 1
            del doc["published"][tool]
            for rec in doc["accounts"].values():
                rec["active"] = [n for n in rec["active"] if n != tool]
            doc["default_active"] = [n for n in doc["default_active"] if n != tool]
            return self._commit(doc, actor=actor, command=f"unpublish {tool}", old=old)

    def set_account(self, account_id: str, tools: Iterable[str] | None, *, actor: str) -> Snapshot:
        """Give an account an explicit list (possibly EMPTY), or None to return it to the default."""
        if not A.well_formed(account_id):
            raise CatalogRefused("account.invalid", f"{account_id!r} is not an account id")
        with self.lock():
            doc, old = self._base()
            if tools is None:
                doc["accounts"].pop(account_id, None)
            else:
                names = sorted(set(tools))
                unknown = [n for n in names if n not in doc["published"]]
                if unknown:
                    raise CatalogRefused("account.unknown_tool", f"not published: {unknown}")
                doc["accounts"][account_id] = {"active": names}
            doc["generation"] += 1
            return self._commit(doc, actor=actor, command=f"set-account {account_id} {tools}", old=old)

    def set_default(self, tools: Iterable[str], *, actor: str) -> Snapshot:
        with self.lock():
            doc, old = self._base()
            names = sorted(set(tools))
            unknown = [n for n in names if n not in doc["published"]]
            if unknown:
                raise CatalogRefused("default.unknown_tool", f"not published: {unknown}")
            doc["default_active"] = names
            doc["generation"] += 1
            return self._commit(doc, actor=actor, command=f"set-default {names}", old=old)

    def rollback(self, to_generation: int, *, actor: str) -> Snapshot:
        """Write a NEW generation carrying the contents of an older one (never restore a number)."""
        with self.lock():
            doc, old = self._base()
            src = self.history / ("%d.json" % int(to_generation))
            try:
                past = json.loads(src.read_bytes().decode("utf-8"))
            except (OSError, ValueError) as exc:
                raise CatalogRefused("rollback.unknown_generation", f"no history for generation {to_generation}") from exc
            if _validate_document(past):
                raise CatalogRefused("rollback.invalid_history", "the historical generation does not validate")
            try:
                self._public.verify(bytes.fromhex(past["signature"]["signature"]), _signed_bytes(past))
            except (InvalidSignature, ValueError) as exc:
                raise CatalogRefused("rollback.unsigned_history", "the historical generation is not signed") from exc
            new = {k: copy.deepcopy(v) for k, v in past.items() if k != "signature"}
            new["generation"] = doc["generation"] + 1
            return self._commit(new, actor=actor, command=f"rollback to contents of {to_generation}", old=old)
