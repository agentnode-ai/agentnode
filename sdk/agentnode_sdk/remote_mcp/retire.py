"""Retiring a paired worker whose device is gone (local-worker slice 2, Codex Q-LW2-0001 D11 as amended by
Q-LW2-0003 Q3).

Eligibility closes the moment a device disappears or moves: every admission, checkout and renewal reads the device
again. What remains is to make that permanent -- the target revoked, the worker identity tombstoned and the
revocation list published, the pair key forgotten -- and that is `Operations.revoke`, an existing, fsynced, resumable
operation. This root reconciler decides WHEN, and only for an irreversible reason:

  * a dial-back target whose device no longer exists in the gateway, or now belongs to another account
    (a suspended account is reversible and is never retired here);
  * a signed self-retirement request an uninstalling agent left in rmcp-agent's store -- verified AGAIN here (the
    worker certificate of this deployment for that instance, its signature over deployment, instance, target, device,
    request id and time), then the gateway device is withdrawn (`revoke_client` within the owner's account) and the
    target retired.

It is idempotent and restart-safe: the state is derived, an open revoke operation is resumed rather than begun twice,
a request already handled is moved to `retire/done/`. It runs on a `.path` unit (the gateway's token file, the store's
`retire/` directory) and a one-minute timer.
"""
from __future__ import annotations

import json
from pathlib import Path

from agentnode_sdk.remote_mcp import targets as T


def _open_revoke(ops, target_id: str) -> bool:
    return any(o["kind"] == "revoke" and o["target_id"] == target_id for o in ops.open_operations())


def _retire(ops, target_id: str, report: dict, why: str) -> None:
    t = ops.register.load().target(target_id)
    if t is None or t.effective_status(ops.register.now()) in T.TERMINAL and not _open_revoke(ops, target_id):
        return
    if _open_revoke(ops, target_id):
        ops.resume(kinds=("revoke",))
    else:
        ops.revoke(target_id)
    report["retired"].append({"target_id": target_id, "why": why})


def reconcile(ops, gateway_dir, *, store=None, trust=None, deployment: str = "") -> dict:
    from agentnode_sdk.gateway.identity import GatewayState
    from agentnode_sdk.remote_mcp import agent_service as AS

    report: dict = {"retired": [], "requests": [], "skipped": []}
    state = GatewayState(str(gateway_dir), "retire")
    devices = {str(r.get("client_id")): r for r in state.paired_clients()}

    # 1. signed self-retirement requests (verified again, then the device withdrawn and the target retired)
    if store is not None:
        pending = Path(store) / "retire"
        done = pending / "done"
        for f in sorted(pending.glob("*.json")) if pending.is_dir() else ():
            try:
                req = json.loads(f.read_text(encoding="utf-8"))
                AS.check_retire_request(req, trust, deployment, now=float(req.get("ts") or 0))
                t = ops.register.load().target(str(req["target_id"]))
                if t is None or not t.is_reverse or t.worker["instance"] != req["instance"] \
                        or t.device_id != req["device_id"]:
                    raise AS.Refused("retire_request_mismatch")
                state.revoke_client(t.device_id, within_account=str(t.owner_account))
                _retire(ops, t.target_id, report, "self-retired")
                outcome = "retired"
            except AS.Refused as refused:
                outcome = refused.code
            except (OSError, ValueError, KeyError) as exc:
                outcome = "retire_request_invalid:" + type(exc).__name__
            report["requests"].append({"request": f.name, "outcome": outcome})
            done.mkdir(exist_ok=True)
            f.replace(done / f.name)
        devices = {str(r.get("client_id")): r for r in state.paired_clients()}

    # 2. dial-back targets whose device is gone or moved
    snap = ops.register.load()
    for t in snap.targets.values():
        if not t.is_reverse or t.effective_status(snap.now) in T.TERMINAL:
            continue
        record = devices.get(t.device_id)
        if record is None:
            _retire(ops, t.target_id, report, "device_gone")
        elif str(record.get("account_id") or "") != str(t.owner_account):
            _retire(ops, t.target_id, report, "device_moved")
    if ops.open_operations():
        ops.resume(kinds=("revoke",))
    return report


__all__ = ["reconcile"]
