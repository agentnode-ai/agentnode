"""The execution-target register: WHERE an authorized catalog call may run.

Target-router profile `target-router-r1`, decision Q-TGT-0001 (D1/D2/D3, amended), resolutions
DECISION-0001. One operator-signed document on the control host lists every target a call can be
dispatched to: the AgentNode-managed worker(s), and customer-owned workers, each bound to the one
account that owns it.

Trust and durability (D1 as amended):

* the document is canonical JSON signed by the stand operator key (the catalog's trust anchor);
  it is verified on EVERY load, and anything doubtful -- missing, unreadable, unsigned, a lower
  generation than the pinned head, a fork, a missing or divergent history -- fails closed
  (`TargetsUnavailable`, answered 503). There is no fallback to any target;
* history is a HASH-LINKED, signed sequence: each generation names the digest of the one before,
  is written to `history/<generation>.json` (O_EXCL, fsynced) before the live file is replaced,
  and the highest accepted (generation, digest) is pinned in `head.json`. A load walks the chain;
  a missing link, a mismatch or a deleted generation refuses;
* every pair of consecutive generations is checked: a TERMINAL target (expired, revoked,
  provisioning_failed) never changes again; `type`, `owner_account` and `created_at` never change;
  target ids and worker identity URIs, once used, are never used again (permanent sets);
* expiry is DERIVED and monotonic: at `now >= expires_at` a target is `expired` whatever its stored
  status says, and `now` is never earlier than a durably pinned clock high-water, so setting the
  clock back cannot bring a target back;
* ONE lock -- the catalog's -- serialises every writer of either document and every run admission.

What binds an approval to a target (D2) is the target's FACTS -- id, proved identity URI, endpoint,
properties and assurance -- with the generation at which those facts last changed. Operational
state that changes daily (trust acknowledgements, see `TrustAcks`) is kept out of the binding.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from agentnode_sdk.gateway import accounts as A
from agentnode_sdk.remote_mcp.catalog import CatalogLock, _write_durably, canonical
from agentnode_sdk.signing_key import compute_key_id
from agentnode_sdk.worker import trust_install as _trust_install

SCHEMA = "agentnode.mcp-targets.v1"
_DOMAIN = b"agentnode.mcp-targets.v1/document\n"
ACKS_SCHEMA = "agentnode.mcp-target-trust-acks.v1"
_ACKS_DOMAIN = b"agentnode.mcp-target-trust-acks.v1/document\n"
#: Where a scheduled refresh keeps the pair it is about to replace (`stand/targets/trust-previous/`).
PREVIOUS_DIR = "trust-previous"
GENESIS = "0" * 64

MANAGED, CUSTOMER = "managed", "customer"
TYPES = (MANAGED, CUSTOMER)
INVITED, ENROLLED, PROBING, ACTIVE, SUSPENDED = "invited", "enrolled", "probing", "active", "suspended"
EXPIRED, REVOKED, PROVISIONING_FAILED = "expired", "revoked", "provisioning_failed"
STATUSES = (INVITED, ENROLLED, PROBING, ACTIVE, SUSPENDED, EXPIRED, REVOKED, PROVISIONING_FAILED)
TERMINAL = frozenset({EXPIRED, REVOKED, PROVISIONING_FAILED})
#: Allowed stored transitions (terminal states have none). Expiry is also derived at load.
TRANSITIONS = {
    INVITED: {ENROLLED, EXPIRED, REVOKED, PROVISIONING_FAILED},
    ENROLLED: {PROBING, EXPIRED, REVOKED, PROVISIONING_FAILED},
    PROBING: {ACTIVE, ENROLLED, EXPIRED, REVOKED, PROVISIONING_FAILED},
    ACTIVE: {SUSPENDED, PROBING, EXPIRED, REVOKED},
    SUSPENDED: {ACTIVE, PROBING, EXPIRED, REVOKED},
}

OPERATOR_MANAGED = "operator_managed_measured"
HARDWARE_ATTESTED = "hardware_attested"
CUSTOMER_OBSERVED = "customer_controlled_observed"
ASSURANCE_CLASSES = (OPERATOR_MANAGED, HARDWARE_ATTESTED, CUSTOMER_OBSERVED)
#: The longest an observation record authorizes anything (Codex D4).
ATTESTATION_MAX_SECONDS = {OPERATOR_MANAGED: 7 * 86400, CUSTOMER_OBSERVED: 24 * 3600}
#: A target type may only carry its own class; no target can carry hardware attestation here.
ASSURANCE_OF_TYPE = {MANAGED: OPERATOR_MANAGED, CUSTOMER: CUSTOMER_OBSERVED}

MAX_DOCUMENT_BYTES = 1 << 20
MAX_TARGETS = 1000
CLOCK_PIN_EVERY_SECONDS = 60.0
_ID_CHARS = set("abcdefghijklmnopqrstuvwxyz0123456789-")
WORKER_KEYS = ("address", "deployment", "instance", "identity_uri", "topology", "protocols", "pair",
               "key_slot", "config_digest")
ENTRY_KEYS = ("target_id", "type", "owner_account", "created_at", "status", "expires_at", "worker",
              "endpoint_digest", "limits", "attestation", "facts_generation")
IMMUTABLE = ("target_id", "type", "owner_account", "created_at")
#: What of an observation record binds an approval (attest-renewal D2). A renewal that changes only the
#: measurement time, the expiry, the raw results, the nonces and with them the record digest is EQUIVALENT:
#: it keeps the binding, the facts generation and every outstanding approval. Any other change is not.
ATTESTATION_BINDING_KEYS = ("assurance", "suite", "identity_uri", "endpoint_digest", "properties")


def attestation_binding_view(record: dict | None) -> dict | None:
    if not record:
        return None
    return {k: record.get(k) for k in ATTESTATION_BINDING_KEYS}


class TargetsUnavailable(Exception):
    """The register cannot be trusted right now: refuse (503), never fall back."""


class TargetsRefused(Exception):
    """An operator change the register refuses (stable `code`)."""

    def __init__(self, code: str, detail: str):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def digest(obj: Any) -> str:
    return hashlib.sha256(canonical(obj)).hexdigest()


def endpoint_digest(worker: dict[str, Any]) -> str:
    """What the control plane dials and whom it expects there -- never the key material."""
    return digest({k: worker.get(k) for k in ("address", "deployment", "instance", "identity_uri",
                                              "topology", "protocols")})


@dataclass(frozen=True)
class Target:
    target_id: str
    type: str
    owner_account: str | None
    created_at: float
    status: str
    expires_at: float | None
    worker: dict = field(compare=False)
    endpoint_digest: str
    limits: dict = field(compare=False)
    attestation: dict | None = field(compare=False)
    facts_generation: int

    @classmethod
    def from_doc(cls, e: dict) -> "Target":
        return cls(**{k: copy.deepcopy(e[k]) for k in ENTRY_KEYS})

    def effective_status(self, now: float) -> str:
        if self.status in TERMINAL:
            return self.status
        if self.expires_at is not None and now >= float(self.expires_at):
            return EXPIRED
        return self.status

    @property
    def identity_uri(self) -> str:
        return str(self.worker.get("identity_uri") or "")

    @property
    def assurance(self) -> str:
        return str((self.attestation or {}).get("assurance") or "")

    def attestation_valid(self, now: float) -> bool:
        a = self.attestation
        return bool(a) and float(a.get("measured_at", 0)) <= now < float(a.get("expires_at", 0))

    @property
    def properties(self) -> dict:
        return dict((self.attestation or {}).get("properties") or {})

    @property
    def properties_digest(self) -> str:
        """The assurance and the measured properties -- not the record that measured them, so an
        equivalent renewal keeps it (attest-renewal D2); the suite is bound by facts_generation."""
        if not self.attestation:
            return ""
        return digest({"assurance": self.assurance, "properties": self.properties})

    def binding(self, requirements_digest: str) -> dict[str, Any]:
        """The canonical target binding that goes into the invocation nonce (Codex D2)."""
        return {"target_id": self.target_id, "worker_identity_uri": self.identity_uri,
                "endpoint_digest": self.endpoint_digest, "requirements_digest": requirements_digest,
                "properties_digest": self.properties_digest, "attestation_assurance": self.assurance,
                "target_generation": self.facts_generation}

    def visible_to(self, account: str) -> bool:
        return self.type == MANAGED or (self.owner_account is not None and self.owner_account == account)


@dataclass(frozen=True)
class RegisterSnapshot:
    generation: int
    digest: str
    targets: dict
    account_defaults: dict
    default_target: str
    now: float
    document: dict = field(repr=False)

    def target(self, target_id: str) -> Target | None:
        return self.targets.get(target_id) if isinstance(target_id, str) else None

    def visible(self, account: str, target_id: str) -> Target | None:
        """The target if this account may see it; None for unknown AND for foreign (indistinguishable)."""
        t = self.target(target_id)
        return t if t is not None and t.visible_to(account) else None

    def default_for(self, account: str) -> tuple[str, str]:
        """(target_id, reason) of the account's default: its own default, else the managed default."""
        own = self.account_defaults.get(account)
        if own:
            return own, "account_default"
        return self.default_target, "managed_default"

    def visible_targets(self, account: str) -> list[Target]:
        """Targets this account may see in discovery: never a foreign one, never a terminal one."""
        return sorted((t for t in self.targets.values() if t.visible_to(account)
                       and t.effective_status(self.now) not in TERMINAL), key=lambda t: t.target_id)


# ---------------------------------------------------------------------- validation

def _bad_id(value: Any) -> bool:
    return not isinstance(value, str) or not (3 <= len(value) <= 64) or not set(value) <= _ID_CHARS


def _validate_entry(tid: str, e: Any) -> list[str]:
    errs: list[str] = []
    if not isinstance(e, dict) or set(e) != set(ENTRY_KEYS):
        return [f"target {tid!r} must carry exactly {sorted(ENTRY_KEYS)}"]
    if e["target_id"] != tid or _bad_id(tid):
        errs.append(f"target id {tid!r} is malformed or does not match its key")
    if e["type"] not in TYPES:
        errs.append(f"target {tid!r} has an unknown type")
    if e["type"] == MANAGED and e["owner_account"] is not None:
        errs.append(f"managed target {tid!r} carries an owner")
    if e["type"] == CUSTOMER and not (isinstance(e["owner_account"], str) and A.well_formed(e["owner_account"])):
        errs.append(f"customer target {tid!r} has no well-formed owner account")
    if e["status"] not in STATUSES:
        errs.append(f"target {tid!r} has an unknown status")
    if not isinstance(e["created_at"], (int, float)) or isinstance(e["created_at"], bool):
        errs.append(f"target {tid!r} has an invalid created_at")
    if e["expires_at"] is not None and (not isinstance(e["expires_at"], (int, float))
                                        or float(e["expires_at"]) <= float(e["created_at"] or 0)):
        errs.append(f"target {tid!r} has an invalid expiry")
    w = e["worker"]
    if not isinstance(w, dict) or set(w) != set(WORKER_KEYS):
        errs.append(f"target {tid!r} worker must carry exactly {sorted(WORKER_KEYS)}")
    else:
        if not str(w["address"]).startswith("tcps://"):
            errs.append(f"target {tid!r} worker address must be a literal tcps:// address")
        uri = "agentnode://%s/worker/%s" % (w["deployment"], w["instance"])
        if w["identity_uri"] != uri:
            errs.append(f"target {tid!r} identity URI does not match its deployment/instance")
        if not isinstance(w["pair"], dict) or set(w["pair"]) != {"gateway", "worker"} \
                or w["pair"].get("worker") != w["instance"]:
            errs.append(f"target {tid!r} pair must be {{gateway, worker}} naming its own instance")
        if not isinstance(w["protocols"], list) or not w["protocols"]:
            errs.append(f"target {tid!r} must name the protocol versions it speaks")
        if e["endpoint_digest"] != endpoint_digest(w):
            errs.append(f"target {tid!r} endpoint digest does not match its worker")
    lim = e["limits"]
    if not isinstance(lim, dict) or set(lim) != {"concurrency", "queue"} \
            or not all(isinstance(lim[k], int) and not isinstance(lim[k], bool) and lim[k] >= 1 for k in lim):
        errs.append(f"target {tid!r} limits must be {{concurrency>=1, queue>=1}}")
    a = e["attestation"]
    if a is not None:
        need = {"assurance", "suite", "measured_at", "expires_at", "identity_uri", "endpoint_digest",
                "properties", "results", "challenge_nonces", "record_digest"}
        if not isinstance(a, dict) or set(a) != need:
            errs.append(f"target {tid!r} attestation must carry exactly {sorted(need)}")
        else:
            if a["assurance"] != ASSURANCE_OF_TYPE.get(e["type"]):
                errs.append(f"target {tid!r} carries an assurance class its type may not carry")
            limit = ATTESTATION_MAX_SECONDS.get(a["assurance"], 0)
            if not (0 < float(a["expires_at"]) - float(a["measured_at"]) <= limit):
                errs.append(f"target {tid!r} attestation validity exceeds its class maximum")
            if isinstance(w, dict) and (a["identity_uri"] != w.get("identity_uri")
                                        or a["endpoint_digest"] != e["endpoint_digest"]):
                errs.append(f"target {tid!r} attestation is for another identity or endpoint")
            body = {k: a[k] for k in sorted(need - {"record_digest"})}
            if a["record_digest"] != digest(body):
                errs.append(f"target {tid!r} attestation record digest does not match its content")
    if e["status"] == ACTIVE and a is None:
        errs.append(f"target {tid!r} is active without an observation record")
    if not isinstance(e["facts_generation"], int) or e["facts_generation"] < 1:
        errs.append(f"target {tid!r} has an invalid facts_generation")
    return errs


def validate_document(doc: Any) -> list[str]:
    if not isinstance(doc, dict):
        return ["register must be an object"]
    want = {"schema", "generation", "prev_digest", "targets", "account_defaults", "default_target",
            "used_target_ids", "used_identity_uris", "signature"}
    if set(doc) != want:
        return [f"register keys must be exactly {sorted(want)}"]
    errs: list[str] = []
    if doc["schema"] != SCHEMA:
        errs.append("wrong schema")
    g = doc["generation"]
    if not isinstance(g, int) or isinstance(g, bool) or g < 1:
        errs.append("generation must be a positive integer")
    if not isinstance(doc["prev_digest"], str) or len(doc["prev_digest"]) != 64:
        errs.append("prev_digest must be a sha256")
    targets = doc["targets"]
    if not isinstance(targets, dict) or len(targets) > MAX_TARGETS:
        return errs + ["targets must be an object"]
    uris: set[str] = set()
    for tid, e in targets.items():
        errs += _validate_entry(tid, e)
        uri = (e.get("worker") or {}).get("identity_uri") if isinstance(e, dict) else None
        if uri in uris:
            errs.append(f"identity URI bound to two targets: {uri!r}")
        uris.add(uri)
    used_ids, used_uris = doc["used_target_ids"], doc["used_identity_uris"]
    if not isinstance(used_ids, list) or not isinstance(used_uris, list) \
            or not all(isinstance(x, str) for x in used_ids + used_uris) \
            or used_ids != sorted(set(used_ids)) or used_uris != sorted(set(used_uris)):
        errs.append("used sets must be sorted lists without duplicates")
    else:
        if not set(targets) <= set(used_ids):
            errs.append("a target id is missing from the permanent id set")
        if not {u for u in uris if u} <= set(used_uris):
            errs.append("an identity URI is missing from the permanent URI set")
    d = doc["default_target"]
    managed = [t for t, e in targets.items() if isinstance(e, dict) and e.get("type") == MANAGED]
    if not isinstance(d, str) or d not in targets or targets[d].get("type") != MANAGED:
        errs.append("default_target must name exactly one managed target")
    if not managed:
        errs.append("the register needs a managed target")
    defaults = doc["account_defaults"]
    if not isinstance(defaults, dict):
        errs.append("account_defaults must be an object")
    else:
        for acct, tid in defaults.items():
            if not A.well_formed(acct):
                errs.append(f"invalid account {acct!r}")
            t = targets.get(tid) if isinstance(tid, str) else None
            if t is None or not (t.get("type") == MANAGED or t.get("owner_account") == acct):
                errs.append(f"account {acct!r} defaults to a target it may not see")
    s = doc["signature"]
    if not isinstance(s, dict) or set(s) != {"key_id", "signature"}:
        errs.append("signature must be {key_id, signature}")
    return errs


def check_successor(prev: dict, cur: dict) -> list[str]:
    """What may change between two consecutive generations (D1 amendments 3-5)."""
    errs: list[str] = []
    if cur["generation"] != prev["generation"] + 1:
        errs.append("generations must follow each other")
    if cur["prev_digest"] != digest(prev):
        errs.append("generation %d does not link to its predecessor" % cur["generation"])
    if not set(prev["used_target_ids"]) <= set(cur["used_target_ids"]) \
            or not set(prev["used_identity_uris"]) <= set(cur["used_identity_uris"]):
        errs.append("a permanent id or URI was removed")
    for tid, old in prev["targets"].items():
        new = cur["targets"].get(tid)
        if new is None:
            errs.append(f"target {tid!r} disappeared")             # removal = hidden, never deleted
            continue
        for k in IMMUTABLE:
            if new[k] != old[k]:
                errs.append(f"target {tid!r} changed its immutable {k}")
        if old["worker"]["identity_uri"] != new["worker"]["identity_uri"]:
            errs.append(f"target {tid!r} changed its identity; that is a new target")
        if old["status"] in TERMINAL and new != old:
            errs.append(f"terminal target {tid!r} changed")
        if new["status"] != old["status"] and new["status"] not in TRANSITIONS.get(old["status"], ()):
            errs.append(f"target {tid!r} may not move from {old['status']} to {new['status']}")
    for tid in set(cur["targets"]) - set(prev["targets"]):
        if tid in prev["used_target_ids"]:
            errs.append(f"target id {tid!r} was used before")
        uri = cur["targets"][tid]["worker"]["identity_uri"]
        if uri in prev["used_identity_uris"]:
            errs.append(f"identity URI {uri!r} was used before")
        if cur["targets"][tid]["facts_generation"] != cur["generation"]:
            errs.append(f"new target {tid!r} must carry this generation as its facts generation")
    return errs


def _signed_bytes(doc: dict[str, Any], domain: bytes = _DOMAIN) -> bytes:
    return domain + canonical({k: v for k, v in doc.items() if k != "signature"})


BINDING_FIELDS = ("worker", "endpoint_digest", "attestation", "status")


def _binding_view(entry: dict, key: str):
    """What of `entry[key]` binds approvals. The pair-key slot does not: a key rotation under the
    same identity URI needs no new approval (Codex D2 amendment 3), unlike a new URI or endpoint."""
    value = entry.get(key)
    if key == "worker" and isinstance(value, dict):
        return {k: v for k, v in value.items() if k != "key_slot"}
    if key == "attestation":
        return attestation_binding_view(value)
    return value


# ---------------------------------------------------------------------- the register

class Register:
    """The target register of one stand. Reads verify everything; writes need the operator key."""

    def __init__(self, stand, operator_public_bytes: bytes, *, operator_private: Ed25519PrivateKey | None = None,
                 on_change: Callable[[dict], None] | None = None, clock: Callable[[], float] = time.time,
                 pin_clock: bool = True):
        self.stand = Path(stand)
        #: False for a pure reader (the trust watchdog): it reads the pinned clock but never writes it.
        self.pin_clock = pin_clock
        self.dir = self.stand / "targets"
        self.path = self.dir / "targets.json"
        self.history = self.dir / "history"
        self.head_path = self.dir / "head.json"
        self.clock_path = self.dir / "clock.json"
        self.lock_path = self.stand / "catalog" / ".lock"           # ONE lock with the catalog
        self.trusted_key_id = compute_key_id(operator_public_bytes)
        self._public = Ed25519PublicKey.from_public_bytes(operator_public_bytes)
        self._private = operator_private
        self.on_change = on_change
        self._clock = clock
        self._mutex = threading.Lock()
        self._verified: tuple[int, str] | None = None
        self._clock_pinned_at = 0.0

    # ------------------------------------------------------------ time
    def now(self) -> float:
        """max(wall clock, pinned high-water). The high-water is re-pinned at most once a minute."""
        wall = float(self._clock())
        try:
            pinned = float(json.loads(self.clock_path.read_text())["high_water"])
        except (OSError, ValueError, KeyError, TypeError):
            pinned = 0.0
        effective = max(wall, pinned)
        if self.pin_clock and effective - max(pinned, self._clock_pinned_at) >= CLOCK_PIN_EVERY_SECONDS:
            try:
                _write_durably(self.clock_path, canonical({"high_water": effective}))
                self._clock_pinned_at = effective
            except OSError:
                pass                       # the pin only ever raises the floor; not writing it never lowers it
        return effective

    # ------------------------------------------------------------ read
    def lock(self) -> CatalogLock:
        return CatalogLock(self.lock_path)

    def _read_doc(self, path: Path) -> tuple[dict, bytes]:
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise TargetsUnavailable(f"{path.name} unreadable: {type(exc).__name__}") from exc
        if len(raw) > MAX_DOCUMENT_BYTES:
            raise TargetsUnavailable("register exceeds the size limit")
        try:
            doc = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise TargetsUnavailable(f"{path.name} is not valid JSON") from exc
        errs = validate_document(doc)
        if errs:
            raise TargetsUnavailable(f"{path.name} invalid: " + "; ".join(errs[:4]))
        if doc["signature"]["key_id"] != self.trusted_key_id:
            raise TargetsUnavailable(f"{path.name} is not signed by the configured operator key")
        try:
            self._public.verify(bytes.fromhex(doc["signature"]["signature"]), _signed_bytes(doc))
        except (InvalidSignature, ValueError) as exc:
            raise TargetsUnavailable(f"{path.name} signature does not verify") from exc
        return doc, raw

    def _head(self) -> dict:
        try:
            h = json.loads(self.head_path.read_text())
            return {"generation": int(h["generation"]), "digest": str(h["digest"])}
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise TargetsUnavailable("the pinned register head is missing or unreadable") from exc

    def _verify_chain(self, doc: dict) -> None:
        """Walk generations 1..current: every one present, signed, linked and a lawful successor."""
        key = (doc["generation"], digest(doc))
        if self._verified == key:
            return
        prev = None
        for g in range(1, doc["generation"] + 1):
            hdoc = doc if g == doc["generation"] else self._read_doc(self.history / f"{g}.json")[0]
            if g == doc["generation"]:
                hist, _ = self._read_doc(self.history / f"{g}.json")
                if digest(hist) != digest(doc):
                    raise TargetsUnavailable("the live register diverges from its own history")
            if hdoc["generation"] != g:
                raise TargetsUnavailable(f"history {g} carries generation {hdoc['generation']}")
            if prev is None:
                if hdoc["prev_digest"] != GENESIS:
                    raise TargetsUnavailable("the first generation does not start the chain")
            else:
                errs = check_successor(prev, hdoc)
                if errs:
                    raise TargetsUnavailable("history broken at %d: %s" % (g, "; ".join(errs[:3])))
            prev = hdoc
        self._verified = key

    def load(self) -> RegisterSnapshot:
        with self._mutex:
            doc, _raw = self._read_doc(self.path)
            head = self._head()
            if doc["generation"] < head["generation"]:
                raise TargetsUnavailable("register generation %d is older than the pinned head %d"
                                         % (doc["generation"], head["generation"]))
            if doc["generation"] == head["generation"] and digest(doc) != head["digest"]:
                raise TargetsUnavailable("register equivocation: same generation, different content")
            self._verify_chain(doc)
            now = self.now()
            future = [t for t, e in doc["targets"].items() if float(e["created_at"]) > now + 300]
            if future:
                raise TargetsUnavailable("target(s) created in the future: %s" % ", ".join(sorted(future)))
            return RegisterSnapshot(
                generation=doc["generation"], digest=digest(doc),
                targets={t: Target.from_doc(e) for t, e in doc["targets"].items()},
                account_defaults=dict(doc["account_defaults"]), default_target=doc["default_target"],
                now=now, document=doc)

    # ------------------------------------------------------------ write
    def _sign(self, doc: dict) -> dict:
        if self._private is None:
            raise TargetsRefused("register.read_only", "writing the register needs the operator private key")
        signed = dict(doc)
        signed["signature"] = {"key_id": self.trusted_key_id,
                               "signature": self._private.sign(_signed_bytes(doc)).hex()}
        return signed

    def _write(self, doc: dict, *, command: str, actor: str, old: dict | None) -> RegisterSnapshot:
        errs = validate_document(doc)
        if errs:
            raise TargetsRefused("register.invalid", "; ".join(errs[:4]))
        if old is not None:
            errs = check_successor(old, doc)
            if errs:
                raise TargetsRefused("register.unlawful_change", "; ".join(errs[:4]))
        data = canonical(doc) + b"\n"
        _write_durably(self.history / ("%d.json" % doc["generation"]), data, exclusive=True)
        _write_durably(self.path, data)
        _write_durably(self.head_path, canonical({"generation": doc["generation"], "digest": digest(doc)}))
        new = self.load()
        if self.on_change is not None:
            self.on_change({"change": "target_register_change", "actor": actor, "command": command,
                            "old_generation": old["generation"] if old else 0, "new_generation": new.generation,
                            "old_digest": digest(old) if old else "", "new_digest": new.digest})
        return new

    def init(self, managed: dict, *, actor: str) -> RegisterSnapshot:
        """Generation 1: exactly one managed target, active, the only default."""
        with self.lock():
            if self.path.exists() or self.head_path.exists() or (self.history.is_dir() and any(self.history.iterdir())):
                raise TargetsRefused("register.exists", "a register already exists")
            entry = self._new_entry(managed, generation=1)
            doc = {"schema": SCHEMA, "generation": 1, "prev_digest": GENESIS,
                   "targets": {entry["target_id"]: entry}, "account_defaults": {},
                   "default_target": entry["target_id"], "used_target_ids": [entry["target_id"]],
                   "used_identity_uris": [entry["worker"]["identity_uri"]]}
            return self._write(self._sign(doc), command="init", actor=actor, old=None)

    @staticmethod
    def _new_entry(spec: dict, *, generation: int) -> dict:
        worker = {k: copy.deepcopy(spec["worker"][k]) for k in WORKER_KEYS}
        return {"target_id": spec["target_id"], "type": spec["type"], "owner_account": spec.get("owner_account"),
                "created_at": float(spec["created_at"]), "status": spec["status"],
                "expires_at": spec.get("expires_at"), "worker": worker, "endpoint_digest": endpoint_digest(worker),
                "limits": dict(spec["limits"]), "attestation": copy.deepcopy(spec.get("attestation")),
                "facts_generation": generation}

    def change(self, mutate: Callable[[dict, float], str], *, actor: str) -> RegisterSnapshot:
        """Apply `mutate(doc_without_signature, now) -> command` as ONE new generation, under the lock.

        Binding facts that change bump the target's facts_generation; permanent sets grow."""
        with self.lock():
            old = self.load().document
            doc = copy.deepcopy({k: v for k, v in old.items() if k != "signature"})
            command = mutate(doc, self.now())
            doc["generation"] = old["generation"] + 1
            doc["prev_digest"] = digest(old)
            for tid, e in doc["targets"].items():
                if "endpoint_digest" in e and isinstance(e.get("worker"), dict):
                    e["endpoint_digest"] = endpoint_digest(e["worker"])
                before = old["targets"].get(tid)
                if before is None:
                    e["facts_generation"] = doc["generation"]
                elif any(_binding_view(e, k) != _binding_view(before, k) for k in BINDING_FIELDS):
                    e["facts_generation"] = doc["generation"]
            doc["used_target_ids"] = sorted(set(old["used_target_ids"]) | set(doc["targets"]))
            doc["used_identity_uris"] = sorted(set(old["used_identity_uris"])
                                               | {e["worker"]["identity_uri"] for e in doc["targets"].values()})
            return self._write(self._sign(doc), command=command, actor=actor, old=old)

    # ------------------------------------------------------------ operator operations
    def add_customer(self, spec: dict, *, actor: str) -> RegisterSnapshot:
        def m(doc, now):
            if spec["target_id"] in doc["targets"] or spec["target_id"] in doc["used_target_ids"]:
                raise TargetsRefused("target.id_used", "that target id was used before")
            if spec["worker"]["identity_uri"] in doc["used_identity_uris"]:
                raise TargetsRefused("target.uri_used", "that worker identity was used before")
            if spec["type"] != CUSTOMER:
                raise TargetsRefused("target.type", "only customer targets are added after init")
            doc["targets"][spec["target_id"]] = self._new_entry(dict(spec, created_at=now), generation=doc["generation"] + 1)
            return "add %s for %s" % (spec["target_id"], spec["owner_account"])
        return self.change(m, actor=actor)

    def set_status(self, target_id: str, status: str, *, actor: str, why: str = "") -> RegisterSnapshot:
        def m(doc, now):
            e = doc["targets"].get(target_id)
            if e is None:
                raise TargetsRefused("target.unknown", target_id)
            effective = EXPIRED if (e["status"] not in TERMINAL and e["expires_at"] is not None
                                    and now >= e["expires_at"]) else e["status"]
            if effective in TERMINAL and status != effective:
                raise TargetsRefused("target.terminal", f"{target_id} is {effective}; it never changes again")
            if status != e["status"] and status not in TRANSITIONS.get(e["status"], ()):
                raise TargetsRefused("target.transition", f"{e['status']} -> {status} is not allowed")
            if status == ACTIVE and not e["attestation"]:
                raise TargetsRefused("target.unattested", "a target becomes active only with an observation record")
            e["status"] = status
            if target_id == doc["default_target"] and status != ACTIVE:
                raise TargetsRefused("target.default", "the managed default cannot leave active here")
            if status in TERMINAL:
                for acct in [a for a, t in doc["account_defaults"].items() if t == target_id]:
                    del doc["account_defaults"][acct]
            return "status %s %s %s" % (target_id, status, why)
        return self.change(m, actor=actor)

    def set_attestation(self, target_id: str, record: dict | None, *, actor: str) -> RegisterSnapshot:
        def m(doc, now):
            e = doc["targets"].get(target_id)
            if e is None or e["status"] in TERMINAL:
                raise TargetsRefused("target.unknown_or_terminal", target_id)
            e["attestation"] = copy.deepcopy(record)
            return "attestation %s %s" % (target_id, (record or {}).get("record_digest", "none")[:16])
        return self.change(m, actor=actor)

    def renew_attestation(self, target_id: str, record: dict, start: dict, *, actor: str) -> RegisterSnapshot:
        """Publish a renewal's record -- compare-and-change under the catalog lock (attest-renewal D3).

        `start` is what the renewal journaled before it observed: the target's status, identity URI,
        endpoint digest and record digest. If any of them is different now, another change won the
        race (a revocation, a suspension, an endpoint edit, another renewal) and nothing is written:
        `renew.superseded`. Status is never touched here."""
        def m(doc, now):
            e = doc["targets"].get(target_id)
            current = {"status": e["status"] if e else None,
                       "identity_uri": (e or {}).get("worker", {}).get("identity_uri"),
                       "endpoint_digest": (e or {}).get("endpoint_digest"),
                       "record_digest": ((e or {}).get("attestation") or {}).get("record_digest")}
            effective = EXPIRED if (e and e["status"] not in TERMINAL and e["expires_at"] is not None
                                    and now >= e["expires_at"]) else current["status"]
            wanted = {k: start.get(k) for k in current}
            if e is None or current != wanted or effective != ACTIVE:
                raise TargetsRefused("renew.superseded", "%s changed since the renewal began" % target_id)
            if record.get("identity_uri") != current["identity_uri"] \
                    or record.get("endpoint_digest") != current["endpoint_digest"]:
                raise TargetsRefused("renew.superseded", "the record is for another identity or endpoint")
            e["attestation"] = copy.deepcopy(record)
            return "attestation-renew %s %s" % (target_id, record.get("record_digest", "")[:16])
        return self.change(m, actor=actor)

    def block(self, target_id: str, *, actor: str, why: str) -> RegisterSnapshot:
        """Suspend a target on POSITIVE, target-specific evidence (attest-renewal D4) -- the managed
        default included: a conclusively unsafe default is refused for every run, and no other target is
        ever chosen in its place. Only an operator's full probe brings it back."""
        def m(doc, now):
            e = doc["targets"].get(target_id)
            if e is None or e["status"] in TERMINAL:
                raise TargetsRefused("target.unknown_or_terminal", target_id)
            if e["status"] != SUSPENDED:
                if SUSPENDED not in TRANSITIONS.get(e["status"], ()):
                    raise TargetsRefused("target.transition", "%s -> suspended is not allowed" % e["status"])
                e["status"] = SUSPENDED
            return "block %s %s" % (target_id, why[:120])
        return self.change(m, actor=actor)

    def set_account_default(self, account: str, target_id: str | None, *, actor: str) -> RegisterSnapshot:
        if not A.well_formed(account):
            raise TargetsRefused("account.invalid", account)

        def m(doc, now):
            if target_id is None:
                doc["account_defaults"].pop(account, None)
                return "account-default %s clear" % account
            e = doc["targets"].get(target_id)
            if e is None or not (e["type"] == MANAGED or e["owner_account"] == account):
                raise TargetsRefused("account.target_not_visible", "an account may only default to a target it sees")
            if e["status"] in TERMINAL:
                raise TargetsRefused("target.terminal", target_id)
            doc["account_defaults"][account] = target_id
            return "account-default %s %s" % (account, target_id)
        return self.change(m, actor=actor)

    def update_worker(self, target_id: str, changes: dict, *, actor: str) -> RegisterSnapshot:
        """Rotation bookkeeping: key slot / config digest only. The identity never changes here."""
        allowed = {"key_slot", "config_digest"}
        if set(changes) - allowed:
            raise TargetsRefused("target.worker_field", "only %s may change; a new identity is a new target" % sorted(allowed))

        def m(doc, now):
            e = doc["targets"].get(target_id)
            if e is None or e["status"] in TERMINAL:
                raise TargetsRefused("target.unknown_or_terminal", target_id)
            e["worker"].update(changes)
            return "worker %s %s" % (target_id, sorted(changes))
        return self.change(m, actor=actor)


# ---------------------------------------------------------------------- trust acknowledgements

class TrustAcks:
    """Which trust generation each target's worker acknowledged (`install_trust`). Operator-signed,
    OUTSIDE the binding: it changes daily and must not void approvals. A target whose worker has not
    acknowledged the deployment's current, unexpired lists is `target_trust_unavailable`."""

    def __init__(self, stand, operator_public_bytes: bytes, *, operator_private: Ed25519PrivateKey | None = None):
        self.path = Path(stand) / "targets" / "trust-acks.json"
        self.lock_path = Path(stand) / "catalog" / ".lock"
        self.key_id = compute_key_id(operator_public_bytes)
        self._public = Ed25519PublicKey.from_public_bytes(operator_public_bytes)
        self._private = operator_private

    def load(self) -> dict[str, dict]:
        try:
            doc = json.loads(self.path.read_bytes().decode("utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            raise TargetsUnavailable("trust acknowledgements unreadable") from exc
        if not isinstance(doc, dict) or set(doc) != {"schema", "acks", "signature"} or doc["schema"] != ACKS_SCHEMA:
            raise TargetsUnavailable("trust acknowledgements malformed")
        try:
            if doc["signature"]["key_id"] != self.key_id:
                raise ValueError("key")
            self._public.verify(bytes.fromhex(doc["signature"]["signature"]), _signed_bytes(doc, _ACKS_DOMAIN))
        except (InvalidSignature, ValueError, KeyError, TypeError) as exc:
            raise TargetsUnavailable("trust acknowledgements are not signed by the operator") from exc
        return dict(doc["acks"])

    def record(self, target_id: str, ack: dict) -> None:
        if self._private is None:
            raise TargetsRefused("acks.read_only", "recording needs the operator private key")
        missing = [k for k in _trust_install.FACT_FIELDS + ("acked_at",) if k not in ack]
        if missing:
            raise TargetsRefused("acks.incomplete", ",".join(missing))
        with CatalogLock(self.lock_path):
            acks = self.load()
            acks[target_id] = {k: ack[k] for k in _trust_install.FACT_FIELDS + ("acked_at",)}
            doc = {"schema": ACKS_SCHEMA, "acks": acks}
            doc["signature"] = {"key_id": self.key_id,
                                "signature": self._private.sign(_signed_bytes(doc, _ACKS_DOMAIN)).hex()}
            _write_durably(self.path, canonical(doc) + b"\n")


# ---------------------------------------------------------------------- the pair before the last publication
# A scheduled refresh re-signs the deployment's lists; between its publication and a target's acknowledgement
# the target holds -- exactly -- the pair the control plane judged by a moment before. The refresh keeps that
# pair's BYTES here (CA-signed, so self-authenticating) right before it publishes. It may stand in for the
# current pair only while its CONTENT -- the revoked serials, the withdrawn identities, the deployment -- is
# identical to the current pair's: any revocation published since makes the two differ and admission strict.
# No time, lock or run is involved, so nothing can be left behind that grants more than that equality.

def save_previous_pair(stand, revocation_list: bytes, identity_tombstones: bytes) -> None:
    d = Path(stand) / "targets" / PREVIOUS_DIR
    d.mkdir(parents=True, exist_ok=True)
    _write_durably(d / "revoked-identities.json", identity_tombstones)
    _write_durably(d / "revoked.crl", revocation_list)


#: The publication authority's predecessor (trust-floor D4/R3): immutable generations selected by ONE pointer
#: file replaced atomically, so a reader sees one complete generation or the other, never a mixed pair.
PUBLICATION_DIR = "trust-publication"
PREVIOUS_POINTER = "previous.json"
PREVIOUS_GENERATIONS = "previous"


def previous_pair(stand) -> tuple[bytes, bytes] | None:
    """The saved predecessor's bytes: through the pointer when there is one -- a pointer that cannot be read,
    names no generation or disagrees with its files gives NONE, never the legacy files -- else the legacy
    two-file directory (a stand no publication authority has written yet, or the parent's layout)."""
    base = Path(stand) / "targets"
    pointer = base / PUBLICATION_DIR / PREVIOUS_POINTER
    if os.path.lexists(pointer):
        try:
            doc = json.loads(pointer.read_bytes().decode("utf-8"))
            gen = str(doc["generation"])
            if not gen or "/" in gen or "\\" in gen or gen.startswith("."):
                return None
            d = base / PUBLICATION_DIR / PREVIOUS_GENERATIONS / gen
            crl, tombs = (d / "revoked.crl").read_bytes(), (d / "revoked-identities.json").read_bytes()
        except (OSError, ValueError, KeyError, TypeError):
            return None
        if (hashlib.sha256(crl).hexdigest() != doc.get("revocation_list_sha256")
                or hashlib.sha256(tombs).hexdigest() != doc.get("tombstones_sha256")):
            return None
        return crl, tombs
    d = base / PREVIOUS_DIR
    try:
        return (d / "revoked.crl").read_bytes(), (d / "revoked-identities.json").read_bytes()
    except OSError:
        return None


def trust_current(ack: dict | None, *, now: float, deployment: dict | None, previous: dict | None = None) -> bool:
    """A target may run only while its worker holds EXACTLY the pair the control plane itself judges
    by -- number, both digests and every validity time equal (trust-refresh C12) -- and that pair is
    unexpired. A missing deployment pair, a legacy or partial acknowledgement, or any difference
    refuses; there is no number-only fallback. `previous` is the CA-verified pair saved before the
    last scheduled publication: a target holding EXACTLY that pair runs on only while its revocation
    content is identical to the current pair's (`content_sha256`) and it is unexpired."""
    if not ack or not deployment:
        return False
    if _trust_install.differences(ack, deployment):
        if previous is None or _trust_install.differences(ack, previous):
            return False
        if not deployment.get("content_sha256") or previous.get("content_sha256") != deployment["content_sha256"]:
            return False
    if float(ack["revocation_list_next_update"]) <= now or float(ack["tombstones_not_after"]) <= now:
        return False
    return True


__all__ = ["ACTIVE", "ASSURANCE_CLASSES", "ATTESTATION_BINDING_KEYS", "ATTESTATION_MAX_SECONDS",
           "attestation_binding_view", "CUSTOMER", "CUSTOMER_OBSERVED",
           "ENROLLED", "EXPIRED", "HARDWARE_ATTESTED", "INVITED", "MANAGED", "OPERATOR_MANAGED", "PROBING",
           "PROVISIONING_FAILED", "REVOKED", "Register", "RegisterSnapshot", "SUSPENDED", "TERMINAL", "Target",
           "PREVIOUS_DIR", "PREVIOUS_GENERATIONS", "PREVIOUS_POINTER", "PUBLICATION_DIR", "TargetsRefused", "TargetsUnavailable", "TrustAcks", "check_successor", "digest",
           "endpoint_digest", "previous_pair", "save_previous_pair", "trust_current", "validate_document"]
