"""The operator's door to the execution-target register (target-router). Never an MCP tool; it
needs the stand operator key, the issuer and the pair-key file, so it runs as root on the control host.

  python -m agentnode_sdk.remote_mcp.targets_cli --stand S [--canary-host 10.0.0.4] <command>

  init-register --managed-id ID [--concurrency 8 --queue 32]   probe the configured worker, then
                                                               generation 1 with it as the ONLY default
  invite --account A --address tcps://IP:PORT [--concurrency 1 --queue 2 --valid-days 7]
  enroll --target T --request PATH        issue the certificate for the worker's request
  probe --target T                        observe, attest, and activate if everything is consistent
  rotate --target T | retire-overlaps     pair-key rotation with a bounded overlap
  suspend --target T | resume-target --target T
  revoke --target T                       final: admission off, identity withdrawn, pair forgotten
  reconcile-expiry | resume-ops           complete derived expiry / open operations
  account-default --account A (--target T | --clear)
  trust-refresh [--force]                 publish when due, push to every live target, alert (exit 3)
  reconcile-dispatch --invocation-hash H  settle an indeterminate dispatch from the target's journal
  show                                    the register without transport secrets

Every change is a signed register generation and a catalog-ledger event. Refusals exit 5.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from agentnode_sdk.remote_mcp import target_probe as P
from agentnode_sdk.remote_mcp import targets as T
from agentnode_sdk.remote_mcp.target_ops import OperationFailed, Operations


def _key(stand: str, name: str) -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(
        bytes.fromhex(io.open(os.path.join(stand, name), encoding="utf-8").read().strip()))


def _pub(k) -> bytes:
    return k.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


def build(stand: str, canary_host: str):
    from agentnode_sdk.pki.issuer import Issuer
    from agentnode_sdk.remote_mcp.catalog_audit import CatalogLedger
    from agentnode_sdk.remote_mcp.server import _worker_base, target_worker_factory
    from agentnode_sdk.worker.tls import own_instance

    op = _key(stand, "operator_priv.hex")
    au = _key(stand, "audit_signer_priv.hex")
    ledger = CatalogLedger(os.path.join(stand, "catalog-audit.jsonl"), signer_private=au, signer_public_bytes=_pub(au))
    register = T.Register(stand, _pub(op), operator_private=op,
                          on_change=lambda ev: ledger.append("operator_change", **ev))
    acks = T.TrustAcks(stand, _pub(op), operator_private=op)
    cfg, tls = _worker_base(stand)
    trust_dir = os.path.dirname(tls.anchor)
    issuer = Issuer(os.path.join(os.path.dirname(trust_dir), "ca"), trust_dir)
    ops = Operations(stand=stand, register=register, acks=acks, issuer=issuer, keyring_path=cfg["keyring"],
                     gateway_instance=own_instance(tls), deployment=cfg["deployment"],
                     bundles_dir=os.path.join(stand, "targets", "bundles"),
                     worker_for=target_worker_factory(stand), canary_host=canary_host, ledger=ledger)
    return ops, cfg, tls


def _managed_spec(cfg, tls, target_id: str, concurrency: int, queue: int) -> dict:
    from agentnode_sdk.worker.tls import own_instance

    instance = sorted(cfg["accept"])[0]
    w = {"address": cfg["worker_address"], "deployment": cfg["deployment"], "instance": instance,
         "identity_uri": "agentnode://%s/worker/%s" % (cfg["deployment"], instance),
         "topology": "separate-worker-host", "protocols": ["agentnode-worker/1"],
         "pair": {"gateway": own_instance(tls), "worker": instance}, "key_slot": "k1", "config_digest": ""}
    return {"target_id": target_id, "type": T.MANAGED, "owner_account": None, "status": T.ACTIVE,
            "expires_at": None, "worker": w, "limits": {"concurrency": concurrency, "queue": queue}}


def _safe(snap: T.RegisterSnapshot) -> dict:
    return {"generation": snap.generation, "digest": snap.digest, "default_target": snap.default_target,
            "account_defaults": snap.account_defaults,
            "targets": {t.target_id: {"type": t.type, "owner_account": t.owner_account,
                                      "status": t.effective_status(snap.now), "assurance": t.assurance,
                                      "attestation_expires_at": (t.attestation or {}).get("expires_at"),
                                      "facts_generation": t.facts_generation, "limits": t.limits,
                                      "instance": t.worker["instance"]}
                        for t in snap.targets.values()}}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="agentnode-mcp-targets")
    p.add_argument("--stand", required=True)
    p.add_argument("--canary-host", default="127.0.0.1")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("init-register")
    s.add_argument("--managed-id", required=True)
    s.add_argument("--concurrency", type=int, default=8)
    s.add_argument("--queue", type=int, default=32)
    s = sub.add_parser("invite")
    s.add_argument("--account", required=True)
    s.add_argument("--address", required=True)
    s.add_argument("--concurrency", type=int, default=1)
    s.add_argument("--queue", type=int, default=2)
    s.add_argument("--valid-days", type=int, default=7)
    s = sub.add_parser("enroll")
    s.add_argument("--target", required=True)
    s.add_argument("--request", required=True)
    for name in ("probe", "rotate", "suspend", "resume-target", "revoke"):
        sub.add_parser(name).add_argument("--target", required=True)
    for name in ("retire-overlaps", "reconcile-expiry", "resume-ops", "show"):
        sub.add_parser(name)
    s = sub.add_parser("account-default")
    s.add_argument("--account", required=True)
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--target")
    g.add_argument("--clear", action="store_true")
    s = sub.add_parser("trust-refresh")
    s.add_argument("--force", action="store_true")
    s = sub.add_parser("reconcile-dispatch")
    s.add_argument("--invocation-hash", required=True)
    args = p.parse_args(argv)
    ops, cfg, tls = build(args.stand, args.canary_host)
    out: object
    try:
        if args.cmd == "init-register":
            spec = _managed_spec(cfg, tls, args.managed_id, args.concurrency, args.queue)
            provisional = T.Target.from_doc(T.Register._new_entry(dict(spec, created_at=0.0), generation=1))
            worker = ops.worker_for(provisional)
            crl = (Path(ops.issuer.trust_dir) / "revoked.crl").read_bytes()
            tombs = (Path(ops.issuer.trust_dir) / "revoked-identities.json").read_bytes()
            ack = worker.install_trust(crl, tombs)
            canary = P.Canary(args.canary_host)
            try:
                record = P.probe(provisional, worker, canary)
            finally:
                canary.close()
            failed = P.passed(record)
            if failed:
                raise OperationFailed("init.probe_failed", ",".join(failed))
            import time as _t
            spec["created_at"] = _t.time()
            spec["attestation"] = record
            snap = ops.register.init(spec, actor="targets_cli")
            ops.acks.record(args.managed_id, dict(ack, acked_at=_t.time()))
            out = _safe(snap)
        elif args.cmd == "invite":
            op = ops.invite(args.account, address=args.address, concurrency=args.concurrency, queue=args.queue,
                            valid_days=args.valid_days)
            out = {"target_id": op["target_id"], "instance": op["params"]["instance"],
                   "bundle": str(Path(ops.bundles) / op["target_id"]), "op": op["op_id"]}
        elif args.cmd == "enroll":
            out = {"op": ops.enroll(args.target, args.request)["op_id"]}
        elif args.cmd == "probe":
            op = ops.probe(args.target)
            out = {"op": op["op_id"], "status": ops.register.load().target(args.target).status}
        elif args.cmd == "rotate":
            out = {"op": ops.rotate(args.target)["op_id"]}
        elif args.cmd == "retire-overlaps":
            out = {"retired": ops.retire_overdue_overlaps()}
        elif args.cmd == "suspend":
            ops.suspend(args.target)
            out = {"status": T.SUSPENDED}
        elif args.cmd == "resume-target":
            out = {"status": ops.resume_target(args.target)}
        elif args.cmd == "revoke":
            out = {"op": ops.revoke(args.target)["op_id"], "status": ops.register.load().target(args.target).status}
        elif args.cmd == "reconcile-expiry":
            out = {"expired": ops.reconcile_expiry()}
        elif args.cmd == "resume-ops":
            out = {"resumed": ops.resume()}
        elif args.cmd == "account-default":
            out = _safe(ops.register.set_account_default(args.account, None if args.clear else args.target,
                                                         actor="targets_cli"))
        elif args.cmd == "trust-refresh":
            report = ops.refresh_trust(force=args.force)
            print(json.dumps(report, indent=1, default=str))
            return 3 if (report["alerts"] or report["lagging"]) else 0
        elif args.cmd == "reconcile-dispatch":
            out = reconcile_dispatch(ops, args.stand, args.invocation_hash)
        else:
            out = _safe(ops.register.load())
    except (T.TargetsRefused, OperationFailed) as exc:
        print(json.dumps({"status": "refused", "code": exc.code, "detail": exc.detail}, indent=1))
        return 5
    except T.TargetsUnavailable as exc:
        print(json.dumps({"status": "unavailable", "detail": str(exc)}, indent=1))
        return 6
    print(json.dumps(out, indent=1, default=str))
    return 0


def reconcile_dispatch(ops: Operations, stand: str, invocation_hash: str) -> dict:
    """Settle an `indeterminate` dispatch from the SAME target's journal; never resend it."""
    marker = Path(stand) / "targets" / "indeterminate" / (invocation_hash + ".json")
    rec = json.loads(marker.read_text(encoding="utf-8"))
    t = ops.register.load().target(rec["target_id"])
    known = ops.worker_for(t).result(rec["dispatch_id"])
    settled = bool(known.known and (known.never_ran or known.outcome is not None))
    if ops.ledger is not None:
        ops.ledger.append("catalog_decision", decision="dispatch_reconciled" if settled else "still_indeterminate",
                          invocation_hash=invocation_hash, dispatch_id=rec["dispatch_id"], target_id=rec["target_id"],
                          reconciliation="never_ran" if known.never_ran else
                          ("outcome_recovered" if known.outcome is not None else "unknown"))
    if settled:
        marker.unlink()
    return {"settled": settled, "never_ran": bool(known.never_ran), "dispatch_id": rec["dispatch_id"]}


if __name__ == "__main__":
    sys.exit(main())
