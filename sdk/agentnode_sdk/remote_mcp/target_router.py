"""Which target a catalog call runs on, and whether it may (target-router profile T5-T9, T13).

ONE resolver for a direct call and for `agentnode_run` (Codex D2). A client's target is a WISH:

  1. the per-call wish (`_meta["net.agentnode/target"]` on a direct call, the `target` argument of
     `agentnode_run`) -- used only if it names a target this account may see;
  2. otherwise the account's own default;
  3. otherwise the register's managed default.

The chosen target must then be eligible (Codex D3/D4): effectively `active` (not invited, enrolled,
probing, suspended, expired, revoked), with a valid observation record of an assurance class the
catalog entry accepts, with measured properties that cover the capability's requirements, and with a
worker that acknowledged the deployment's current trust lists. Anything else is a structured refusal
with a stable code. Nothing ever falls back to another target, to the managed worker or to a local
one. An unknown target and a target of another account answer the SAME code: `target_unknown`.

What binds the approval is `Target.binding(requirements_digest)` -- the target's id, proved worker
URI, endpoint, properties, assurance and the generation those facts last changed in.
"""
from __future__ import annotations

import ssl
import threading
from dataclasses import dataclass
from typing import Any, Callable

from agentnode_sdk.remote_mcp import targets as T

TARGET_META_KEY = "net.agentnode/target"
MAX_TARGET_ID = 64

#: What the catalog path asks of a target for every capability it runs today (the job the tree
#: execution builds: network none, these ceilings). Kept in one place so the requirement and the job
#: cannot drift apart.
JOB_REQUIREMENTS = {"network": "none", "memory_mb": 1024, "wall_clock_s": 120, "storage_mb": 512}

CODES = ("target_unknown", "target_inactive", "target_expired", "target_revoked", "target_unsuitable",
         "target_properties_stale", "target_trust_unavailable", "target_full", "target_offline",
         "target_identity_mismatch", "target_protocol_mismatch", "target_bad_wish")


class TargetRefusal(Exception):
    """A call is refused because of its target. `code` is stable; `detail` names no foreign fact."""

    def __init__(self, code: str, detail: str = "", *, target_id: str = "", reason: str = ""):
        super().__init__(code)
        self.code = code
        self.detail = detail
        self.target_id = target_id
        self.selection_reason = reason


@dataclass(frozen=True)
class Resolution:
    target: T.Target
    selection_reason: str          # call_wish | account_default | managed_default
    requirements_digest: str
    binding: dict

    @property
    def target_id(self) -> str:
        return self.target.target_id


def requirements_of(entry) -> dict[str, Any]:
    return dict(JOB_REQUIREMENTS, accepted_assurance=sorted(getattr(entry, "accepted_assurance", ())
                                                          or (T.OPERATOR_MANAGED,)))


def wish_of(value: Any) -> str | None:
    """A client's target wish, or None. A wish that is not a short id is refused, never ignored."""
    if value is None:
        return None
    if not isinstance(value, str) or not (1 <= len(value) <= MAX_TARGET_ID) or not set(value) <= T._ID_CHARS:
        raise TargetRefusal("target_bad_wish", "a target is named by a short id")
    return value


def _eligible(target: T.Target, entry, now: float, acks: dict, deployment: dict | None,
              previous: dict | None = None) -> None:
    status = target.effective_status(now)
    if status == T.REVOKED:
        raise TargetRefusal("target_revoked")
    if status == T.EXPIRED:
        raise TargetRefusal("target_expired")
    if status != T.ACTIVE:
        raise TargetRefusal("target_inactive", status)
    if not target.attestation_valid(now):
        raise TargetRefusal("target_properties_stale")
    req = requirements_of(entry)
    if target.assurance not in req["accepted_assurance"]:
        raise TargetRefusal("target_unsuitable", "assurance")
    props = target.properties
    failed = []
    if req["network"] == "none" and props.get("network_none_observed") is not True:
        failed.append("network")
    for key, prop in (("memory_mb", "memory_ceiling_mb"), ("storage_mb", "storage_mb")):
        have = props.get(prop)
        if not isinstance(have, (int, float)) or isinstance(have, bool) or have < req[key]:
            failed.append(key)
    if props.get("wall_clock_enforced") is not True:      # observed to cut a job off, not a claimed duration
        failed.append("wall_clock_s")
    if failed:
        raise TargetRefusal("target_unsuitable", ",".join(failed))
    if not T.trust_current(acks.get(target.target_id), now=now, deployment=deployment, previous=previous):
        raise TargetRefusal("target_trust_unavailable")


class TargetRouter:
    def __init__(self, *, register: T.Register, acks: T.TrustAcks, worker_for: Callable[[T.Target], Any],
                 deployment_trust: Callable[[], dict | None], previous_trust: Callable[[], dict | None] | None = None):
        self.register = register
        self.acks = acks
        self._worker_for = worker_for
        self._deployment_trust = deployment_trust
        #: the CA-verified pair saved before the last scheduled publication (`targets.trust_current`)
        self._previous_trust = previous_trust or (lambda: None)
        self._workers: dict[tuple, Any] = {}
        self._mutex = threading.Lock()

    # ------------------------------------------------------------ resolution
    def resolve(self, account: str, wish: Any, entry, *, snapshot: T.RegisterSnapshot | None = None) -> Resolution:
        snap = snapshot or self.register.load()
        wanted = wish_of(wish)
        if wanted is not None:
            target, reason = snap.visible(account, wanted), "call_wish"
            if target is None:
                raise TargetRefusal("target_unknown", reason="call_wish")
        else:
            tid, reason = snap.default_for(account)
            target = snap.visible(account, tid)
            if target is None:                                 # a default that is not visible: never widened
                raise TargetRefusal("target_unknown", reason=reason)
        try:
            _eligible(target, entry, snap.now, self.acks.load(), self._deployment_trust(), self._previous_trust())
        except TargetRefusal as refusal:
            refusal.target_id, refusal.selection_reason = target.target_id, reason
            raise
        rd = T.digest(requirements_of(entry))
        return Resolution(target=target, selection_reason=reason, requirements_digest=rd,
                          binding=target.binding(rd))

    def recheck(self, account: str, resolution: Resolution, entry) -> None:
        """At admission (under the shared lock): the same answer, byte for byte, or a named refusal."""
        snap = self.register.load()
        t = snap.visible(account, resolution.target_id)
        if t is None:
            raise TargetRefusal("target_revoked_while_queued", target_id=resolution.target_id)
        status = t.effective_status(snap.now)
        if status == T.REVOKED:
            raise TargetRefusal("target_revoked_while_queued", target_id=t.target_id)
        if status == T.EXPIRED:
            raise TargetRefusal("target_expired_while_queued", target_id=t.target_id)
        if status == T.SUSPENDED:
            raise TargetRefusal("target_suspended_while_queued", target_id=t.target_id)
        try:
            _eligible(t, entry, snap.now, self.acks.load(), self._deployment_trust(), self._previous_trust())
        except TargetRefusal as refusal:
            code ="target_properties_stale" if refusal.code == "target_properties_stale" else refusal.code
            raise TargetRefusal(code, target_id=t.target_id) from refusal
        if t.binding(resolution.requirements_digest) != resolution.binding:
            raise TargetRefusal("target_changed", target_id=t.target_id)

    # ------------------------------------------------------------ the worker of a target
    def worker(self, target: T.Target):
        key = (target.target_id, target.endpoint_digest, target.worker.get("key_slot"))
        with self._mutex:
            w = self._workers.get(key)
            if w is None:
                w = self._worker_for(target)
                self._workers[key] = w
            return w

    def preflight(self, target: T.Target, *, budget: float = 5.0) -> None:
        """A fresh connection to the target: handshake and identity, nothing sent. Classifies a
        failure into offline / identity mismatch / trust unavailable -- never one for another."""
        try:
            worker = self.worker(target)
        except Exception as exc:                               # noqa: BLE001 - this side's material for it
            # building the connection reads THIS side's trust material for the target (pair key,
            # anchor, list, floor): unreadable or unusable is trust unavailable, never "offline"
            raise TargetRefusal("target_trust_unavailable", target_id=target.target_id) from exc
        try:
            worker.confirm_reachable(budget)
        except Exception as exc:                               # noqa: BLE001 - classified below
            raise TargetRefusal(classify(exc), target_id=target.target_id) from exc


IDENTITY_CHECKS = {"instance", "deployment", "role", "subject-alternative-name", "extended-key-usage",
                   "no-certificate"}


def classify(exc: BaseException) -> str:
    """offline (no authenticated peer) vs identity mismatch vs trust unusable (Codex D3)."""
    seen = exc
    while seen is not None:
        check = getattr(seen, "check", None)
        if isinstance(check, str):
            return "target_identity_mismatch" if check in IDENTITY_CHECKS else "target_trust_unavailable"
        if isinstance(seen, ssl.SSLCertVerificationError):
            # a peer answered with a certificate that does not chain to this deployment's anchor:
            # somebody is there, and it is not the target -- never "offline"
            return "target_identity_mismatch"
        if type(seen).__name__ in ("TrustRefused", "ListUnusable", "FloorUnusable", "KeyringRefused"):
            return "target_trust_unavailable"
        if "no-common-protocol" in str(seen) or "no tested wire version" in str(seen):
            return "target_protocol_mismatch"
        seen = seen.__cause__ or seen.__context__
    return "target_offline"


__all__ = ["CODES", "JOB_REQUIREMENTS", "Resolution", "TARGET_META_KEY", "TargetRefusal", "TargetRouter",
           "classify", "requirements_of", "wish_of"]
