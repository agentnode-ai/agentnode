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


class OperationFailed(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(code + (": " + detail if detail else ""))
        self.code = code
        self.detail = detail


class Operations:
    def __init__(self, *, stand, register: T.Register, acks: T.TrustAcks, issuer, keyring_path,
                 gateway_instance: str, deployment: str, bundles_dir, worker_for: Callable[[T.Target], Any],
                 canary_host: str = "127.0.0.1", ledger=None, clock: Callable[[], float] = time.time,
                 crash_after: Callable[[str], None] | None = None, refresh_lock_timeout: float = REFRESH_LOCK_SECONDS):
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
            fn(op)
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

    def resume(self) -> list[str]:
        """Complete every open operation from where it stopped. Steps are idempotent."""
        done = []
        for op in self.open_operations():
            getattr(self, "_steps_" + op["kind"])(op)
            done.append(op["op_id"])
        return done

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
        op = self._begin("probe", target_id, {})
        return self._steps_probe(op)

    def _steps_probe(self, op: dict) -> dict:
        tid = op["target_id"]

        def probing(op):
            t = self._target(tid)
            if t.status == T.ENROLLED or t.status == T.ACTIVE:
                self.register.set_status(tid, T.PROBING, actor="targets_cli", why=op["op_id"])
            elif t.status not in (T.PROBING, T.SUSPENDED):
                raise OperationFailed("probe.state", t.status)

        def trust(op):
            self._push_trust(self._target(tid))

        def observe(op):
            t = self._target(tid)
            canary = P.Canary(self.canary_host)
            try:
                record = P.probe(t, self.worker_for(t), canary, now=self.clock())
            finally:
                canary.close()
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
            self._consistent_or_refuse(self._target(tid))
            self.register.set_status(tid, T.ACTIVE, actor="targets_cli", why=op["op_id"])

        return self._run(op, [("status_probing", probing), ("trust_ack", trust), ("observe", observe),
                              ("activate", activate)])

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


__all__ = ["ALERT_BEFORE_SECONDS", "OperationFailed", "Operations", "PAIR_OVERLAP_SECONDS", "REFRESH_LOCK_NAME",
           "REFRESH_LOCK_SECONDS", "deadline_alerts"]
