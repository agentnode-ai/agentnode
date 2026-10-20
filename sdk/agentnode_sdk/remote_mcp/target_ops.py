"""The lifecycle of execution targets, and the trust distribution that keeps them usable
(target-router T10, T11, T21; Codex D5 replaced, D11/D12; resolutions R3/R5/R6).

Every change to a target touches several stores that cannot share one transaction: the register,
the issuer's inventory and published lists, the pair-key file, the trust acknowledgements and the
audit ledger. So every operation is a durable, RESUMABLE state machine instead of a pretended
cross-file atomic write:

  * the operation is written to `stand/targets/ops/<op_id>.json` (fsynced) BEFORE any side effect,
    naming its kind, target and parameters;
  * its steps run in a fixed order; each is idempotent and marked done durably after it ran;
  * a crash at any boundary leaves the target NON-RUNNABLE (invited / enrolled / probing, or already
    revoked), and `resume()` completes the open operation;
  * a target becomes `active` only in the last step of a probe, after the pair key, the current
    certificate, a trust acknowledgement and a passing observation record are all found consistent.

States: invited -> enrolled -> probing -> active <-> suspended, terminal expired | revoked |
provisioning_failed. A revoked, expired or failed target is never resumed, re-invited or recovered:
the register keeps it (terminal, permanent id and URI) and the issuer withdraws its identity for good.
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import time
from pathlib import Path
from typing import Any, Callable

from agentnode_sdk.pki import revocation as _revocation
from agentnode_sdk.pki import tombstones as _tombstones
from agentnode_sdk.remote_mcp import target_probe as P
from agentnode_sdk.remote_mcp import targets as T
from agentnode_sdk.worker import pairkeys
from agentnode_sdk.worker import trust_install as _trust_install

#: The longest a rotated pair key's predecessor is still accepted (Codex D5: bounded overlap).
PAIR_OVERLAP_SECONDS = 24 * 3600
#: One trust refresh at a time (trust-refresh C6): `stand/targets/.trust-refresh.lock`, and how long a
#: second run waits for the first before it fails as `trust_refresh.busy`.
REFRESH_LOCK_NAME = ".trust-refresh.lock"
REFRESH_LOCK_SECONDS = 600.0
#: Warn when the deployment's revocation list has less than this left.
ALERT_BEFORE_SECONDS = 48 * 3600
INVITE_DAYS = 7
LIVE = (T.ENROLLED, T.PROBING, T.ACTIVE, T.SUSPENDED)

# ---- attestation renewal (attest-renewal profile, Codex Q-ATT-0001) ----
#: Per assurance class: the reserve the schedule keeps, the warning threshold, and "due" = reserve + one
#: timer period (6 h) + 10 min. Boundaries are inclusive (`<=`). Customer records live 24 h, so 72/48 h
#: cannot apply to them; the class exception is frozen (D5).
RENEW_POLICY = {T.OPERATOR_MANAGED: {"reserve": 72 * 3600, "warn": 48 * 3600, "due": 78 * 3600 + 600},
                T.CUSTOMER_OBSERVED: {"reserve": 12 * 3600, "warn": 6 * 3600, "due": 18 * 3600 + 600}}
#: At most this many probes per renewal run -- resumed operations included -- strictly one after another.
MAX_PROBES_PER_RUN = 4
RENEW_LOCK_NAME = ".attest-renew.lock"
PROBE_LOCK_PREFIX = ".probe-"
#: An open renew/probe operation older than this is hung (the watchdog's red).
HUNG_AFTER_SECONDS = 1800
#: A journaled observation older than this is never published on resume: observe again, fresh nonces.
STALE_OBSERVATION_SECONDS = 3600
#: Operation kinds a36ddcdd cannot resume: none may be open across a rollback (D10).
HEAD_ONLY_KINDS = ("renew",)
RENEW_STATE_SCHEMA = "agentnode.attest-renew-state.v1"


def renew_state_path(stand) -> Path:
    """`<stand>/../alerts/attest-renew.json` -- beside the trust watchdog's state, removed on rollback."""
    return Path(stand).resolve().parent / "alerts" / "attest-renew.json"


def due_at(t: T.Target) -> float | None:
    """When `t` becomes due for renewal (effective time), or None without a record or a policy."""
    pol = RENEW_POLICY.get(t.assurance)
    if not t.attestation or pol is None:
        return None
    return float(t.attestation["expires_at"]) - pol["due"]


def is_due(t: T.Target, now: float) -> bool:
    at = due_at(t)
    return at is not None and now >= at                      # remaining <= due: inclusive boundary


class OperationFailed(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(code + (": " + detail if detail else ""))
        self.code = code
        self.detail = detail


class _Close(Exception):
    """End an operation early with a named final state (`superseded`) -- nothing further runs."""

    def __init__(self, state: str, reason: str):
        super().__init__(reason)
        self.state = state
        self.reason = reason


class _NoSlot:
    """`slot` stand-in for a caller that runs no slot service (tests): holds nothing."""

    def __init__(self, target_id: str):
        self.target_id = target_id

    def still_held(self) -> bool:
        return True

    def release(self) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _default_slot(target_id: str):
    from agentnode_sdk.remote_mcp import probe_slot
    return probe_slot.acquire(target_id)


def _write_private(path: Path, doc: dict) -> None:
    """Atomically, 0600: a temporary file beside it, fsync, rename, directory fsync."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name("." + path.name + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, sort_keys=True, indent=1)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(str(tmp), str(path))
    if hasattr(os, "O_DIRECTORY"):
        dfd = os.open(str(path.parent), os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)


def load_renew_state(path) -> dict | None:
    """The renewal state, or None when absent. Unreadable or malformed raises ValueError."""
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise ValueError("renewal state unreadable: %s" % type(exc).__name__) from exc
    if not isinstance(doc, dict) or doc.get("schema") != RENEW_STATE_SCHEMA or not isinstance(doc.get("targets"), dict):
        raise ValueError("renewal state malformed")
    return doc


class Operations:
    def __init__(self, *, stand, register: T.Register, acks: T.TrustAcks, issuer, keyring_path,
                 gateway_instance: str, deployment: str, bundles_dir, worker_for: Callable[[T.Target], Any],
                 canary_host: str = "127.0.0.1", ledger=None, clock: Callable[[], float] = time.time,
                 crash_after: Callable[[str], None] | None = None, refresh_lock_timeout: float = REFRESH_LOCK_SECONDS,
                 slot: Callable[[str], Any] | None = _default_slot, kill_switch: Callable[[], str] | None = None,
                 probe_fn: Callable | None = None, canary_factory: Callable[[], Any] | None = None,
                 state_path=None):
        self.stand = Path(stand)
        self.ops_dir = self.stand / "targets" / "ops"
        self.register = register
        self.acks = acks
        self.issuer = issuer
        self.keyring_path = Path(keyring_path)
        self.gateway = gateway_instance
        self.deployment = deployment
        self.bundles = Path(bundles_dir)
        self.worker_for = worker_for
        self.canary_host = canary_host
        self.ledger = ledger
        self.clock = clock
        self._crash_after = crash_after or (lambda step: None)
        self.refresh_lock_timeout = refresh_lock_timeout
        #: target_id -> a held probe slot of the serving process's scheduler (`probe_slot.acquire`);
        #: None only for a caller that runs no slot service at all (tests): then nothing is held.
        self._slot = slot if slot is not None else _NoSlot
        #: the gateway kill switch: "" while work is taken, else the reason. A probe is work: none runs.
        self._kill_switch = kill_switch or (lambda: "")
        self._probe_fn = probe_fn or P.probe
        self._canary_factory = canary_factory or (lambda: P.Canary(self.canary_host))
        self.state_path = Path(state_path) if state_path is not None else renew_state_path(self.stand)

    # ------------------------------------------------------------ the operation journal
    def _write(self, op: dict) -> None:
        self.ops_dir.mkdir(parents=True, exist_ok=True)
        path = self.ops_dir / (op["op_id"] + ".json")
        tmp = path.with_suffix(".tmp")
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(op, fh, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)

    def _begin(self, kind: str, target_id: str, params: dict) -> dict:
        op = {"op_id": "op-%d-%s" % (int(self.clock()), secrets.token_hex(4)), "kind": kind,
              "target_id": target_id, "params": params, "steps": {}, "state": "open",
              "created_at": round(self.clock(), 3), "error": ""}
        self._write(op)                                       # BEFORE any side effect
        self._note(op, "begin")
        return op

    def _note(self, op: dict, step: str, **extra) -> None:
        if self.ledger is not None:
            self.ledger.append("target_lifecycle", op_id=op["op_id"], operation=op["kind"],
                               target_id=op["target_id"], step=step, **extra)

    def _run(self, op: dict, steps: list[tuple[str, Callable[[dict], None]]]) -> dict:
        for name, fn in steps:
            if name in op["steps"]:
                continue
            try:
                fn(op)
            except _Close as close:
                op["state"], op["error"] = close.state, close.reason
                self._write(op)
                self._note(op, close.state, reason=close.reason)
                return op
            op["steps"][name] = round(self.clock(), 3)
            self._write(op)
            self._note(op, name)
            self._crash_after(name)                           # test seam: a crash right after a boundary
        op["state"] = "done"
        self._write(op)
        self._note(op, "done")
        return op

    def open_operations(self) -> list[dict]:
        if not self.ops_dir.is_dir():
            return []
        ops = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(self.ops_dir.glob("op-*.json"))]
        return [o for o in ops if o["state"] == "open"]

    def resume(self, kinds: tuple[str, ...] | None = None) -> list[str]:
        """Complete every open operation from where it stopped. Steps are idempotent. A probe or renewal
        resumes only while it can hold its target's probe lock (never beside a live probe)."""
        done = []
        for op in self.open_operations():
            if kinds is not None and op["kind"] not in kinds:
                continue
            if op["kind"] in ("probe", "renew"):
                try:
                    with self._probe_lock(op["target_id"]):
                        getattr(self, "_steps_" + op["kind"])(op)
                except OperationFailed as exc:
                    if exc.code != "probe.busy":
                        raise
                    continue
            else:
                getattr(self, "_steps_" + op["kind"])(op)
            done.append(op["op_id"])
        return done

    def _probe_lock(self, target_id: str):
        """One probe of a target at a time, across processes (the timer's renewal and an operator's
        probe alike): `stand/targets/.probe-<target>.lock`, never waited for -- busy is `probe.busy`."""
        from agentnode_sdk.gateway.filelock import LockUnavailable, ProcessLock

        lock = ProcessLock(self.ops_dir.parent / (PROBE_LOCK_PREFIX + target_id), timeout=0.0)
        ops = self

        class _Held:
            def __enter__(self_inner):
                ops.ops_dir.parent.mkdir(parents=True, exist_ok=True)
                try:
                    lock.__enter__()
                except LockUnavailable as exc:
                    raise OperationFailed("probe.busy", target_id) from exc
                return self_inner

            def __exit__(self_inner, *exc):
                return lock.__exit__(*exc)
        return _Held()

    # ------------------------------------------------------------ helpers
    def _keyring(self) -> pairkeys.Keyring:
        try:
            return pairkeys.Keyring.read(self.keyring_path)
        except pairkeys.KeyringRefused:
            if self.keyring_path.exists():
                raise
            return pairkeys.empty()

    def _bundle(self, target_id: str) -> Path:
        d = self.bundles / target_id
        d.mkdir(parents=True, exist_ok=True, mode=0o700)
        return d

    def _target(self, target_id: str) -> T.Target:
        t = self.register.load().target(target_id)
        if t is None:
            raise OperationFailed("target.unknown", target_id)
        return t

    # ------------------------------------------------------------ invite
    def invite(self, account: str, *, address: str, concurrency: int = 1, queue: int = 2,
               valid_days: int = INVITE_DAYS) -> dict:
        target_id = "cust-" + secrets.token_hex(5)
        instance = "c" + secrets.token_hex(6)
        params = {"account": account, "address": address, "instance": instance, "concurrency": int(concurrency),
                  "queue": int(queue), "expires_at": round(self.clock() + valid_days * 86400, 3)}
        op = self._begin("invite", target_id, params)
        return self._steps_invite(op)

    def _steps_invite(self, op: dict) -> dict:
        p, tid = op["params"], op["target_id"]
        bundle = self._bundle(tid)

        def register(op):
            if self.register.load().target(tid) is None:
                w = {"address": p["address"], "deployment": self.deployment, "instance": p["instance"],
                     "identity_uri": "agentnode://%s/worker/%s" % (self.deployment, p["instance"]),
                     "topology": "separate-worker-host", "protocols": ["agentnode-worker/1"],
                     "pair": {"gateway": self.gateway, "worker": p["instance"]}, "key_slot": "k1",
                     "config_digest": ""}
                self.register.add_customer({"target_id": tid, "type": T.CUSTOMER, "owner_account": p["account"],
                                            "status": T.INVITED, "expires_at": p["expires_at"], "worker": w,
                                            "limits": {"concurrency": p["concurrency"], "queue": p["queue"]},
                                            "attestation": None}, actor="targets_cli")

        def pki_entry(op):
            inv = self.issuer.inventory()["entries"].get("worker/" + p["instance"])
            if inv is None or not inv.get("claimed"):
                self.issuer.add("worker", p["instance"], secret_at=bundle / "enrolment-secret",
                                deliver_to=bundle / "cert.pem")

        def pair_key(op):
            ring = self._keyring()
            if (self.gateway, p["instance"]) not in ring.pairs():
                key = os.urandom(32)
                ring.add(gateway=self.gateway, worker=p["instance"], key=key).write(self.keyring_path)
                pairkeys.empty().add(gateway=self.gateway, worker=p["instance"], key=key).write(bundle / "pair-keys.json")

        def trust_files(op):
            for name in ("ca.pem", _revocation.LIST_NAME, "revoked-identities.json"):
                shutil.copyfile(self.issuer.trust_dir / name, bundle / name)
            (bundle / "target.json").write_text(json.dumps({
                "target_id": tid, "deployment": self.deployment, "instance": p["instance"],
                "accept_gateway": self.gateway, "listen": p["address"]}, indent=1, sort_keys=True))

        return self._run(op, [("register_invited", register), ("pki_entry", pki_entry),
                              ("pair_key", pair_key), ("bundle", trust_files)])

    # ------------------------------------------------------------ enroll
    def enroll(self, target_id: str, request_path) -> dict:
        req = json.loads(Path(request_path).read_text(encoding="utf-8"))
        op = self._begin("enroll", target_id, {"csr": req["csr"], "secret": req["secret"]})
        return self._steps_enroll(op)

    def _steps_enroll(self, op: dict) -> dict:
        tid = op["target_id"]

        def issue(op):
            t = self._target(tid)
            if t.effective_status(self.register.now()) in T.TERMINAL:
                raise OperationFailed("target.terminal", tid)
            from cryptography import x509
            pem = self.issuer.enroll(op["params"]["csr"].encode("ascii"), op["params"]["secret"])
            cert = x509.load_pem_x509_certificate(pem)
            san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
            uris = san.get_values_for_type(x509.UniformResourceIdentifier)
            if uris != [t.identity_uri]:
                raise OperationFailed("enroll.identity", "the issued certificate names another identity")
            op["params"].pop("secret", None)                  # spent: never kept in the journal
            op["params"]["certificate_serial"] = format(cert.serial_number, "x")

        def status(op):
            if self._target(tid).status == T.INVITED:
                self.register.set_status(tid, T.ENROLLED, actor="targets_cli", why=op["op_id"])

        return self._run(op, [("pki_enroll", issue), ("status_enrolled", status)])

    # ------------------------------------------------------------ probe -> active
    def probe(self, target_id: str) -> dict:
        """First activation (enrolled) or the operator's way back from suspended. An ACTIVE target --
        the managed default included -- is never set `probing`: it is renewed (attest-renewal D3)."""
        t = self._target(target_id)
        if t.effective_status(self.register.now()) == T.ACTIVE:
            return self.renew(target_id, force=True)
        with self._probe_lock(target_id):
            op = self._begin("probe", target_id, {})
            return self._steps_probe(op)

    def _observe(self, t: T.Target) -> dict:
        """One complete probe of `t` with fresh nonces, inside one slot of the serving scheduler."""
        with self._slot(t.target_id) as slot:
            canary = self._canary_factory()
            try:
                record = self._probe_fn(t, self.worker_for(t), canary, now=self.register.now())
            finally:
                canary.close()
            if not slot.still_held():
                raise OperationFailed("probe.slot_lost", "the serving process went away during the probe")
        return record

    def _steps_probe(self, op: dict) -> dict:
        tid = op["target_id"]

        def current() -> T.Target:
            """The target, or the operation ends `failed`: a terminal target (revoked, expired) is never probed,
            and its probe operation never stays open to be retried forever."""
            t = self._target(tid)
            status = t.effective_status(self.register.now())
            if status in T.TERMINAL:
                raise _Close("failed", "the target is %s; a terminal target is never probed" % status)
            return t

        def probing(op):
            t = current()
            if t.status == T.ACTIVE:
                # an operation the parent release left open for an active target (it could not set the
                # managed default probing): closed, never retried; an active target is renewed instead
                raise _Close("superseded", "the target is active; active targets are renewed, never set probing")
            if t.status == T.ENROLLED:
                self.register.set_status(tid, T.PROBING, actor="targets_cli", why=op["op_id"])
            elif t.status not in (T.PROBING, T.SUSPENDED):
                raise OperationFailed("probe.state", t.status)

        def trust(op):
            t = current()
            try:
                self._push_trust(t)
            except OperationFailed:
                raise
            except Exception as exc:                          # noqa: BLE001 - named, the operation stays open
                raise OperationFailed("probe.trust_push", type(exc).__name__) from exc

        def observe(op):
            t = current()
            if self._kill_switch():
                raise OperationFailed("probe.kill_switch", "the gateway is stopped; no probe runs")
            try:
                record = self._observe(t)
            except Exception as exc:                          # noqa: BLE001 - named, the operation stays open
                code = getattr(exc, "code", "") or type(exc).__name__
                raise OperationFailed("probe.not_observed", str(code)) from exc
            failed = P.passed(record)
            op["params"]["record"] = record
            op["params"]["failed"] = failed
            if failed:
                op["state"] = "failed"
                op["error"] = "probe did not establish: " + ",".join(failed)
                self._write(op)
                self._note(op, "probe_failed", failed=failed)
                raise OperationFailed("probe.failed", ",".join(failed))
            self.register.set_attestation(tid, record, actor="targets_cli")

        def activate(op):
            t = current()
            self._consistent_or_refuse(t)
            self.register.set_status(tid, T.ACTIVE, actor="targets_cli", why=op["op_id"])
            self._state_note(tid, result="activated", reason="", record=t.attestation, op_id=op["op_id"],
                             published=True)

        return self._run(op, [("status_probing", probing), ("trust_ack", trust), ("observe", observe),
                              ("activate", activate)])

    # ------------------------------------------------------------ attestation renewal (attest-renewal)
    def _local_evidence(self, t: T.Target) -> str:
        """POSITIVE target-specific evidence from this side's authoritative inventory (D4): the identity
        withdrawn, an inventory entry without a current certificate, or the pair key gone. "" = none.
        A store this side cannot read is no evidence about the target (`OperationFailed`, inconclusive)."""
        inst = t.worker["instance"]
        try:
            inv = self.issuer.inventory()
        except Exception as exc:                              # noqa: BLE001 - this side's trouble
            raise OperationFailed("renew.inventory_unreadable", type(exc).__name__) from exc
        if t.identity_uri in (inv.get("ever_withdrawn") or []):
            return "identity_withdrawn"
        entry = (inv.get("entries") or {}).get("worker/" + inst)
        if entry is not None and not any(c.get("status") == "current" for c in entry.get("certificates", [])):
            return "no_current_certificate"
        try:
            pairs = pairkeys.Keyring.read(self.keyring_path).pairs()
        except Exception as exc:                              # noqa: BLE001 - this side's trouble
            raise OperationFailed("renew.keyring_unreadable", type(exc).__name__) from exc
        if (self.gateway, inst) not in pairs:
            return "pair_key_missing"
        return ""

    @staticmethod
    def _wrong_peer(exc: BaseException, expected: str) -> str:
        """The URI an anchor-authenticated peer presented at the target's address when it is NOT the
        target's (positive evidence of an identity change), else ""."""
        seen = exc
        while seen is not None:
            check, presented = getattr(seen, "check", None), getattr(seen, "presented", "")
            if isinstance(check, str):
                if check in P.WRONG_IDENTITY_CHECKS and presented and presented != expected:
                    return presented
                return ""
            seen = seen.__cause__ or seen.__context__
        return ""

    def renew(self, target_id: str, *, force: bool = False) -> dict:
        """Renew one target's record now (operator / `probe` on an active target). Same locks, slot,
        judgement and publication as the timer's run."""
        t = self._target(target_id)
        if t.effective_status(self.register.now()) != T.ACTIVE:
            raise OperationFailed("renew.not_active", t.effective_status(self.register.now()))
        return self._renew_one(target_id, forced=force)

    def _renew_one(self, target_id: str, *, forced: bool) -> dict:
        with self._probe_lock(target_id):
            snap = self.register.load()
            t = snap.target(target_id)
            if t is None or t.effective_status(snap.now) != T.ACTIVE or not t.attestation:
                raise OperationFailed("renew.not_active", target_id)
            start = {"status": t.status, "identity_uri": t.identity_uri, "endpoint_digest": t.endpoint_digest,
                     "record_digest": t.attestation["record_digest"], "register_generation": snap.generation,
                     "expires_at": t.attestation["expires_at"]}
            op = self._begin("renew", target_id, {"start": start, "forced": bool(forced)})   # BEFORE observing
            return self._steps_renew(op)

    def _steps_renew(self, op: dict) -> dict:
        """evidence -> observe (journal only; the old record stays in force) -> judge -> publish
        (compare-and-change) | block | degraded. Status is never set `probing`."""
        tid, p = op["target_id"], op["params"]

        def as_started() -> T.Target:
            """The target exactly as this renewal journaled it, or the operation ends `superseded` -- checked
            before the evidence AND again right before the probe (a resumed operation skips the evidence step):
            a target that changed meanwhile is never probed for this renewal, let alone published."""
            snap = self.register.load()
            t = snap.target(tid)
            now_tuple = None if t is None else {
                "status": t.effective_status(snap.now), "identity_uri": t.identity_uri,
                "endpoint_digest": t.endpoint_digest, "record_digest": (t.attestation or {}).get("record_digest")}
            if now_tuple != {k: p["start"][k] for k in ("status", "identity_uri", "endpoint_digest", "record_digest")}:
                self._state_note(tid, result="superseded", reason="changed before the probe", op_id=op["op_id"])
                raise _Close("superseded", "the target changed before the probe")
            return t

        def evidence(op):
            p["evidence"] = ""
            p["inconclusive"] = []
            t = as_started()
            try:
                p["evidence"] = self._local_evidence(t)
            except OperationFailed as exc:
                p["inconclusive"] = [exc.code]

        def observe(op):
            p.pop("record", None)
            p["lost"] = []
            if p["evidence"] or p["inconclusive"]:
                return
            stopped = self._kill_switch()
            if stopped:
                p["inconclusive"] = ["kill_switch"]
                return
            t = as_started()
            try:
                record = self._observe(t)
            except Exception as exc:                          # noqa: BLE001 - every failure is named
                wrong = self._wrong_peer(exc, t.identity_uri)
                if wrong:
                    p["lost"] = ["identity:%s" % wrong]
                else:
                    from agentnode_sdk.remote_mcp.target_router import classify
                    code = getattr(exc, "code", "")
                    p["inconclusive"] = [("no_slot:" + code) if type(exc).__name__ == "SlotRefused"
                                         else ("probe:" + code) if isinstance(exc, OperationFailed)
                                         else "transport:" + classify(exc)]
                return
            p["record"] = record                              # complete, written with the step mark at once

        def judge(op):
            if p["evidence"]:
                p["verdict"], p["reasons"] = P.LOST, [p["evidence"]]
                return
            if p.get("lost"):
                p["verdict"], p["reasons"] = P.LOST, list(p["lost"])
                return
            if p["inconclusive"] or not p.get("record"):
                p["verdict"], p["reasons"] = P.INCONCLUSIVE, list(p["inconclusive"] or ["no_record"])
                return
            record = p["record"]
            if self.register.now() - float(record["measured_at"]) > STALE_OBSERVATION_SECONDS:
                p["verdict"], p["reasons"] = P.INCONCLUSIVE, ["stale_observation"]
                return
            p["verdict"], p["reasons"] = P.judge(record)

        def publish(op):
            verdict, reasons = p["verdict"], p["reasons"]
            t = self._target(tid)
            if verdict == P.ESTABLISHED and self.register.now() - float(p["record"]["measured_at"]) > STALE_OBSERVATION_SECONDS:
                verdict, reasons = P.INCONCLUSIVE, ["stale_observation"]  # resumed too late: never published
            if verdict == P.ESTABLISHED:
                record = p["record"]
                before = T.attestation_binding_view(t.attestation) or {}
                after = T.attestation_binding_view(record)
                changed = sorted(k for k in T.ATTESTATION_BINDING_KEYS if before.get(k) != after.get(k))
                if "properties" in changed:
                    changed += sorted("properties." + k for k in set(before.get("properties") or {})
                                      | set(after["properties"])
                                      if (before.get("properties") or {}).get(k) != after["properties"].get(k))
                try:
                    snap = self.register.renew_attestation(tid, record, p["start"], actor="targets_cli")
                except T.TargetsRefused as exc:
                    if exc.code == "renew.superseded":
                        self._state_note(tid, result="superseded", reason=exc.detail, op_id=op["op_id"])
                        raise _Close("superseded", exc.detail)
                    raise
                p["result"] = "renewed" if not changed else "renewed_changed"
                p["changed"] = changed
                new = snap.target(tid)
                self._note(op, "published", equivalent=not changed, changed=changed,
                           old_record_digest=p["start"]["record_digest"], new_record_digest=record["record_digest"],
                           old_expires_at=p["start"]["expires_at"], new_expires_at=record["expires_at"],
                           old_register_generation=p["start"]["register_generation"],
                           new_register_generation=snap.generation, facts_generation=new.facts_generation)
                self._state_note(tid, result=p["result"], reason=",".join(changed), record=record, op_id=op["op_id"],
                                 published=True)
            elif verdict == P.LOST:
                why = ",".join(reasons)
                try:
                    self.register.block(tid, actor="targets_cli", why="renewal %s: %s" % (op["op_id"], why))
                except T.TargetsRefused as exc:                # already terminal (revoked, expired): nothing to block
                    self._state_note(tid, result="superseded", reason=exc.code, op_id=op["op_id"])
                    raise _Close("superseded", exc.code)
                p["result"] = "blocked"
                self._note(op, "blocked", reasons=reasons)
                self._state_note(tid, result="blocked", reason=why, op_id=op["op_id"])
            else:
                p["result"] = "degraded"
                self._note(op, "degraded", reasons=reasons)
                self._state_note(tid, result="degraded", reason=",".join(reasons), op_id=op["op_id"])

        return self._run(op, [("evidence", evidence), ("observe", observe), ("judge", judge), ("publish", publish)])

    # ------------------------------------------------------------ the renewal state (operational only)
    def _load_state(self) -> dict:
        try:
            doc = load_renew_state(self.state_path)
        except ValueError:
            doc = None                                        # rebuilt below; the watchdog saw it red meanwhile
        return doc or {"schema": RENEW_STATE_SCHEMA, "targets": {}, "last_run": None}

    def _update_state(self, change: Callable[[dict], None]) -> dict:
        """Read-change-write of the renewal state under its own lock: the timer's runner and an operator's
        probe of ANOTHER target may both record a result at once, and neither may lose the other's."""
        from agentnode_sdk.gateway.filelock import ProcessLock
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        with ProcessLock(self.state_path, timeout=30.0):
            doc = self._load_state()
            change(doc)
            doc["updated_at"] = round(self.clock(), 3)
            _write_private(self.state_path, doc)
            return doc

    def _state_note(self, target_id: str, *, result: str, reason: str, op_id: str = "", record: dict | None = None,
                    published: bool = False) -> None:
        def change(doc):
            entry = dict(doc["targets"].get(target_id) or {})
            now = round(self.clock(), 3)
            entry.update(result=result, reason=reason[:300], at=now, op_id=op_id)
            if published and record:
                entry.update(published_record_digest=record["record_digest"], published_expires_at=record["expires_at"],
                             last_success_at=now, consecutive_failures=0)
            elif result in ("degraded", "blocked"):
                entry["consecutive_failures"] = int(entry.get("consecutive_failures") or 0) + 1
            doc["targets"][target_id] = entry
        self._update_state(change)

    def bootstrap_state(self) -> dict:
        """Initialise the renewal state from the verified register (D8): every active target's CURRENT
        record is the last published one, so a fresh install is not a false "missing state"."""
        snap = self.register.load()

        def change(doc):
            for t in snap.targets.values():
                if t.effective_status(snap.now) == T.ACTIVE and t.attestation and t.target_id not in doc["targets"]:
                    doc["targets"][t.target_id] = {"result": "bootstrap", "reason": "", "at": round(self.clock(), 3),
                                                   "op_id": "", "published_record_digest": t.attestation["record_digest"],
                                                   "published_expires_at": t.attestation["expires_at"],
                                                   "last_success_at": float(t.attestation["measured_at"]),
                                                   "consecutive_failures": 0}
            doc.setdefault("bootstrapped_at", round(self.clock(), 3))
        return self._update_state(change)

    # ------------------------------------------------------------ the timer's run
    def attest_renew(self, *, target_id: str | None = None, force: bool = False) -> dict:
        """One renewal run: resume this side's open renewals, then renew the due active targets --
        earliest expiry first, then target id; at most MAX_PROBES_PER_RUN probes, one after another."""
        from agentnode_sdk.gateway.filelock import LockUnavailable, ProcessLock

        self.ops_dir.parent.mkdir(parents=True, exist_ok=True)
        started = round(self.clock(), 3)
        try:
            lock = ProcessLock(self.ops_dir.parent / RENEW_LOCK_NAME, timeout=0.0)
            lock.__enter__()
        except LockUnavailable as exc:
            raise OperationFailed("attest_renew.busy", str(exc)) from exc
        try:
            self.bootstrap_state()
            report: dict[str, Any] = {"started_at": started, "results": {}, "not_due": {}, "probes": 0}
            for op in [o for o in self.open_operations() if o["kind"] == "renew"]:
                if report["probes"] >= MAX_PROBES_PER_RUN:
                    break
                report["probes"] += 1
                try:
                    with self._probe_lock(op["target_id"]):
                        done = self._steps_renew(op)
                    report["results"][op["target_id"]] = done["params"].get("result") or done["state"]
                except OperationFailed as exc:
                    report["results"][op["target_id"]] = exc.code
            snap = self.register.load()
            if target_id is not None:
                t = snap.target(target_id)
                if t is None or t.effective_status(snap.now) != T.ACTIVE:
                    raise OperationFailed("renew.not_active", target_id)
                candidates = [t]
            else:
                candidates = [t for t in snap.targets.values()
                              if t.effective_status(snap.now) == T.ACTIVE and t.attestation]
            due = []
            for t in candidates:
                if t.target_id in report["results"]:
                    continue
                if (force and target_id is not None) or is_due(t, snap.now):
                    due.append(t)
                else:
                    report["not_due"][t.target_id] = {"expires_at": t.attestation["expires_at"], "due_at": due_at(t)}
            due.sort(key=lambda t: (float(t.attestation["expires_at"]), t.target_id))
            for t in due:
                if report["probes"] >= MAX_PROBES_PER_RUN:
                    report["results"].setdefault(t.target_id, "deferred_bound")
                    continue
                report["probes"] += 1
                try:
                    done = self._renew_one(t.target_id, forced=force)
                    report["results"][t.target_id] = done["params"].get("result") or done["state"]
                except OperationFailed as exc:
                    report["results"][t.target_id] = exc.code
                    if exc.code == "probe.busy":
                        self._state_note(t.target_id, result="busy", reason="another probe holds the target",
                                         op_id="")
            results = set(report["results"].values())
            report["status"] = ("blocked" if "blocked" in results else
                                "degraded" if results & {"degraded", "probe.busy", "renew.not_active"} else "ok")
            last = {"started_at": started, "finished_at": round(self.clock(), 3), "status": report["status"],
                    "results": report["results"]}
            self._update_state(lambda doc: doc.__setitem__("last_run", last))
            return report
        finally:
            lock.__exit__(None, None, None)

    def attest_status(self) -> dict:
        """What an operator may see: no address, certificate, nonce, key slot, token or secret."""
        snap = self.register.load()
        try:
            state = load_renew_state(self.state_path) or {}
        except ValueError as exc:
            state = {"error": str(exc)}
        out = {"effective_time": round(snap.now, 3), "effective_time_is_pinned": True,
               "register_generation": snap.generation, "last_run": state.get("last_run"), "targets": {},
               "open_operations": [{"op_id": o["op_id"], "kind": o["kind"], "target_id": o["target_id"],
                                    "age_s": round(self.clock() - float(o["created_at"]), 1)}
                                   for o in self.open_operations()]}
        for t in sorted(snap.targets.values(), key=lambda t: t.target_id):
            a = t.attestation or {}
            st = (state.get("targets") or {}).get(t.target_id) or {}
            out["targets"][t.target_id] = {
                "type": t.type, "status": t.effective_status(snap.now), "assurance": t.assurance,
                "measured_at": a.get("measured_at"), "expires_at": a.get("expires_at"),
                "hours_left": round((float(a["expires_at"]) - snap.now) / 3600, 2) if a else None,
                "record_digest": (a.get("record_digest") or "")[:16], "facts_generation": t.facts_generation,
                "due_at": due_at(t), "renewal": {k: st.get(k) for k in ("result", "reason", "at", "op_id",
                                                                        "consecutive_failures", "last_success_at")}}
        return out

    def _consistent_or_refuse(self, t: T.Target) -> None:
        """Activation only when every piece of material agrees (Codex D5)."""
        now = self.register.now()
        if (self.gateway, t.worker["instance"]) not in self._keyring().pairs():
            raise OperationFailed("activate.pair_key_missing")
        entry = self.issuer.inventory()["entries"].get("worker/" + t.worker["instance"])
        if not entry or not any(c.get("status") == "current" for c in entry.get("certificates", [])):
            raise OperationFailed("activate.no_current_certificate")
        if entry.get("uri") in self.issuer.inventory().get("ever_withdrawn", []):
            raise OperationFailed("activate.identity_withdrawn")
        if not t.attestation_valid(now) or P.passed(t.attestation or {}):
            raise OperationFailed("activate.no_valid_observation")
        if not T.trust_current(self.acks.load().get(t.target_id), now=now, deployment=self._pair()[2]):
            raise OperationFailed("activate.trust_not_acknowledged")

    # ------------------------------------------------------------ rotation (bounded overlap)
    def rotate(self, target_id: str) -> dict:
        op = self._begin("rotate", target_id, {"overlap_until": round(self.clock() + PAIR_OVERLAP_SECONDS, 3)})
        return self._steps_rotate(op)

    def _steps_rotate(self, op: dict) -> dict:
        t = self._target(op["target_id"])
        inst = t.worker["instance"]

        def new_key(op):
            ring = self._keyring()
            pair = ring.for_pair(gateway=self.gateway, worker=inst)
            if op["params"].get("generation") == pair.generation:
                return                                        # already rotated by this op
            key = os.urandom(32)
            rotated = ring.rotate(gateway=self.gateway, worker=inst, key=key)
            rotated.write(self.keyring_path)
            op["params"]["generation"] = rotated.for_pair(gateway=self.gateway, worker=inst).generation
            rotated_peer = pairkeys.empty().add(gateway=self.gateway, worker=inst, key=pair.current) \
                .rotate(gateway=self.gateway, worker=inst, key=key)
            rotated_peer.write(self._bundle(t.target_id) / "pair-keys.json")

        def slot(op):
            self.register.update_worker(t.target_id, {"key_slot": "k%d" % op["params"]["generation"]},
                                        actor="targets_cli")

        return self._run(op, [("pair_rotate", new_key), ("key_slot", slot)])

    def retire_overdue_overlaps(self) -> list[str]:
        """End every pair-key overlap whose bound has passed. Run by the trust timer: no overlap is
        indefinite (Codex D5)."""
        retired = []
        now = self.clock()
        for path in sorted(self.ops_dir.glob("op-*.json")) if self.ops_dir.is_dir() else []:
            op = json.loads(path.read_text(encoding="utf-8"))
            if op["kind"] != "rotate" or op.get("overlap_retired") or float(op["params"]["overlap_until"]) > now:
                continue
            t = self.register.load().target(op["target_id"])
            if t is not None and t.effective_status(now) not in T.TERMINAL:
                ring = self._keyring()
                inst = t.worker["instance"]
                if (self.gateway, inst) in ring.pairs():
                    ring.retire_overlap(gateway=self.gateway, worker=inst).write(self.keyring_path)
            op["overlap_retired"] = round(now, 3)
            self._write(op)
            retired.append(op["op_id"])
        return retired

    # ------------------------------------------------------------ suspend / resume
    def suspend(self, target_id: str) -> None:
        self.register.set_status(target_id, T.SUSPENDED, actor="targets_cli")

    def resume_target(self, target_id: str) -> str:
        t = self._target(target_id)
        if t.effective_status(self.register.now()) in T.TERMINAL:
            raise OperationFailed("target.terminal", target_id)
        if t.attestation_valid(self.register.now()):
            self.register.set_status(target_id, T.ACTIVE, actor="targets_cli")
            return T.ACTIVE
        self.register.set_status(target_id, T.PROBING, actor="targets_cli")
        return T.PROBING

    # ------------------------------------------------------------ revoke / expire (final)
    def revoke(self, target_id: str, *, final_status: str = T.REVOKED) -> dict:
        op = self._begin("revoke", target_id, {"final_status": final_status})
        return self._steps_revoke(op)

    def _steps_revoke(self, op: dict) -> dict:
        tid, final = op["target_id"], op["params"]["final_status"]

        def no_admission(op):
            t = self._target(tid)
            if t.status not in T.TERMINAL:
                status = final if final in (T.REVOKED, T.EXPIRED, T.PROVISIONING_FAILED) else T.REVOKED
                if status == T.PROVISIONING_FAILED and t.status not in (T.INVITED, T.ENROLLED, T.PROBING):
                    status = T.REVOKED
                self.register.set_status(tid, status, actor="targets_cli", why=op["op_id"])

        def withdraw(op):
            inst = self._target(tid).worker["instance"]
            if "worker/" + inst in self.issuer.inventory()["entries"]:
                done = self.issuer.recover_entry("worker", inst)
                if not done.get("effective"):
                    raise OperationFailed("revoke.list_not_published")

        def forget_pair(op):
            inst = self._target(tid).worker["instance"]
            ring = self._keyring()
            if (self.gateway, inst) in ring.pairs():
                ring.remove(gateway=self.gateway, worker=inst).write(self.keyring_path)
            bundle = self.bundles / tid
            if bundle.exists():
                _remove_tree(bundle)                          # the enrolment secret is read-only (0400)

        def propagate(op):
            report = self.refresh_trust(force=False, push_only=True)
            if report["lagging"]:
                raise OperationFailed("revoke.not_acknowledged", ",".join(report["lagging"]))

        return self._run(op, [("no_admission", no_admission), ("pki_withdraw", withdraw),
                              ("forget_pair", forget_pair), ("trust_propagated", propagate)])

    def reconcile_expiry(self) -> list[str]:
        """Commit derived expiry: a target past expires_at is revoked from the PKI and recorded expired."""
        snap = self.register.load()
        out = []
        for t in snap.targets.values():
            if t.status not in T.TERMINAL and t.effective_status(snap.now) == T.EXPIRED:
                self.revoke(t.target_id, final_status=T.EXPIRED)
                out.append(t.target_id)
        return out

    # ------------------------------------------------------------ trust distribution
    def _pair(self) -> tuple[bytes, bytes, dict | None]:
        """The deployment's CRL and tombstone bytes, read ONCE, and their exact facts (None: unparsable)."""
        crl = _read(self.issuer.trust_dir / _revocation.LIST_NAME)
        tombs = _read(self.issuer.trust_dir / _tombstones.LIST_NAME)
        try:
            facts = _trust_install.facts_of(crl, tombs)
        except ValueError:
            facts = None
        return crl, tombs, facts

    def _push_trust(self, t: T.Target, pair: tuple[bytes, bytes, dict | None] | None = None) -> dict:
        """Offer the pair, and record the worker's answer only when it is EXACTLY the pair offered:
        number, both digests and every validity time (trust-refresh C11). Anything else raises."""
        crl, tombs, facts = pair or self._pair()
        if facts is None:
            raise OperationFailed("trust.deployment_pair_unreadable")
        ack = self.worker_for(t).install_trust(crl, tombs)
        wrong = _trust_install.differences(ack if isinstance(ack, dict) else None, facts)
        if wrong:
            raise OperationFailed("trust.ack_mismatch", ",".join(wrong))
        ack = dict({k: ack[k] for k in _trust_install.FACT_FIELDS}, acked_at=round(self.clock(), 3))
        self.acks.record(t.target_id, ack)
        return ack

    def _refresh_lock(self):
        from agentnode_sdk.gateway.filelock import ProcessLock
        self.ops_dir.parent.mkdir(parents=True, exist_ok=True)
        return ProcessLock(self.ops_dir.parent / REFRESH_LOCK_NAME, timeout=self.refresh_lock_timeout)

    def refresh_trust(self, *, force: bool = False, push_only: bool = False) -> dict:
        """The timer's run: publish a fresh list when due, push it to every live target, record what
        each acknowledged, retire overdue pair overlaps, and name every problem (alerting).

        One run at a time across processes (the timer and an operator's `--force` alike): the whole
        run holds the refresh lock, and "due" is judged only once the lock is held, so a run that
        waited sees the list the other one published and does not publish again. A lock that cannot
        be had in time is `trust_refresh.busy` -- a failure, never a skip."""
        from agentnode_sdk.gateway.filelock import LockUnavailable
        try:
            with self._refresh_lock():
                return self._refresh_locked(force=force, push_only=push_only)
        except LockUnavailable as exc:
            raise OperationFailed("trust_refresh.busy", str(exc)) from exc

    def _refresh_locked(self, *, force: bool, push_only: bool) -> dict:
        report: dict[str, Any] = {"published": None, "pushed": {}, "lagging": [], "alerts": []}
        if not push_only:
            current = _read(self.issuer.trust_dir / _revocation.LIST_NAME)
            age = None
            try:
                from cryptography import x509
                crl = x509.load_pem_x509_crl(current)
                age = self.clock() - crl.last_update_utc.timestamp()
            except Exception:                                  # noqa: BLE001 - an unreadable list is republished
                age = None
            if force or age is None or age >= _revocation.REFRESH_AFTER_SECONDS:
                crl_now, tombs_now, facts_now = self._pair()
                if facts_now is not None:                     # targets holding it run on while its content is current
                    T.save_previous_pair(self.stand, crl_now, tombs_now)
                done = self.issuer.publish()
                report["published"] = done.get("number") if done.get("published") else "failed"
            report["retired_overlaps"] = self.retire_overdue_overlaps()
        pair = self._pair()                                   # ONE stable pair for every target
        facts = pair[2]
        report["pair"] = None if facts is None else {
            "number": facts["revocation_list_number"], "sha256": facts["revocation_list_sha256"][:16],
            "tombstones_sha256": facts["tombstones_sha256"][:16]}
        snap = self.register.load()
        for t in snap.targets.values():
            if t.effective_status(snap.now) not in LIVE:
                continue
            try:
                ack = self._push_trust(t, pair)
                report["pushed"][t.target_id] = ack["revocation_list_number"]
            except OperationFailed as exc:
                report["pushed"][t.target_id] = "failed: " + exc.code + (" " + exc.detail if exc.detail else "")
                report["lagging"].append(t.target_id)
            except Exception as exc:                           # noqa: BLE001 - every failure is named
                report["pushed"][t.target_id] = "failed: " + type(exc).__name__
                report["lagging"].append(t.target_id)
        report["alerts"] = deadline_alerts(self.issuer.trust_dir, now=self.clock(), lagging=report["lagging"])
        return report


def _read(path) -> bytes:
    return Path(path).read_bytes()


def _remove_tree(path: Path) -> None:
    """Remove a bundle completely, read-only files included: a secret left behind is the one
    thing the removal exists to prevent, so a refusal to delete is retried writable, then raised."""
    import stat

    def writable_then_retry(func, p, _exc):
        os.chmod(p, stat.S_IWRITE | stat.S_IREAD)
        func(p)
    import sys
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=writable_then_retry)
    else:                                                     # pragma: no cover - older interpreters
        shutil.rmtree(path, onerror=writable_then_retry)


def deadline_alerts(trust_dir, *, now: float, lagging: list[str]) -> list[str]:
    """What an operator must be told now (Codex D11: expiry alerting). Empty means nothing."""
    alerts = []
    try:
        from cryptography import x509
        crl = x509.load_pem_x509_crl(_read(Path(trust_dir) / _revocation.LIST_NAME))
        left = crl.next_update_utc.timestamp() - now
        if left < ALERT_BEFORE_SECONDS:
            alerts.append("revocation list expires in %d s" % int(left))
    except Exception as exc:                                   # noqa: BLE001
        alerts.append("revocation list unreadable: " + type(exc).__name__)
    try:
        body = json.loads(_read(Path(trust_dir) / "revoked-identities.json"))["body"]
        body = json.loads(body) if isinstance(body, str) else body
        left = float(body["not_after"]) - now
        if left < ALERT_BEFORE_SECONDS:
            alerts.append("tombstone list expires in %d s" % int(left))
    except Exception as exc:                                   # noqa: BLE001
        alerts.append("tombstone list unreadable: " + type(exc).__name__)
    for tid in lagging:
        alerts.append("target %s has not acknowledged the current trust lists" % tid)
    return alerts


__all__ = ["ALERT_BEFORE_SECONDS", "HEAD_ONLY_KINDS", "HUNG_AFTER_SECONDS", "MAX_PROBES_PER_RUN", "OperationFailed",
           "Operations", "PAIR_OVERLAP_SECONDS", "PROBE_LOCK_PREFIX", "REFRESH_LOCK_NAME", "REFRESH_LOCK_SECONDS",
           "RENEW_LOCK_NAME", "RENEW_POLICY", "RENEW_STATE_SCHEMA", "STALE_OBSERVATION_SECONDS", "deadline_alerts",
           "due_at", "is_due", "load_renew_state", "renew_state_path"]
