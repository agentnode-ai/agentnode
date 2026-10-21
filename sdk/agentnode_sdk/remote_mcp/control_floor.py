"""The remote-MCP control's floor run (trust-floor D2/D3; `rmcp-control-floor.service`, every 5 minutes).

It replaces the hand-installed `control_floor_advance.py`, whose `Issuer.tick` re-signed the deployment's
trust lists once they were 24 h old -- a second publisher beside the trust refresh, which saved no
predecessor and pushed nothing (the daily admission gap). Here, in this order, LOCAL work only:

  1. under the publication lock (bounded wait): the publication authority's local phase WITHOUT rotation --
     resume an open publication record, and publish at once a security change the lists do not carry yet
     (a revoked serial, a withdrawn identity, an unreadable list); never a network push;
  2. `Issuer.tick(floor_dir, publish=False)`: settle crashed stages, end run-out overlaps, advance the
     floors -- held back while a revocation is not durably published. It never publishes.

The floors are written even when step 1 could not run (lock busy, a named refusal): they decide whether
the control's TLS door works at all (900 s), and a pending revocation still holds them back. Any failure
exits non-zero, so `OnFailure=` alerts. Distribution and rotation are the cycle's (`targets_cli trust-cycle`).

  python -m agentnode_sdk.remote_mcp.control_floor --stand S [--floor-dir D]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

#: How long the floor run waits for the publication lock (held only for local steps by anyone else).
LOCK_WAIT_SECONDS = 20.0


def run(ops, floor_dir) -> dict:
    from agentnode_sdk.remote_mcp import trust_publication as TP
    from agentnode_sdk.remote_mcp.target_ops import OperationFailed

    report = {"reconcile": TP.new_report("floor"), "tick": None, "failure": ""}
    saved = ops.refresh_lock_timeout
    ops.refresh_lock_timeout = LOCK_WAIT_SECONDS
    try:
        with TP._publication_lock(ops):
            TP.local(ops, report["reconcile"], rotate=False)
    except (OperationFailed, TP.Refused) as exc:
        report["failure"] = exc.code + (" " + exc.detail if exc.detail else "")
    except Exception as exc:                                  # noqa: BLE001 - named; the floors still run
        report["failure"] = "reconcile_error " + type(exc).__name__
    finally:
        ops.refresh_lock_timeout = saved
    report["tick"] = ops.issuer.tick(floor_dir, publish=False)
    return report


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="rmcp-control-floor")
    p.add_argument("--stand", required=True)
    p.add_argument("--floor-dir", default=None, help="default: <pki>/floor beside the deployment's trust dir")
    args = p.parse_args(argv)
    from agentnode_sdk.remote_mcp import targets_cli
    ops, _cfg, _tls = targets_cli.build(args.stand, "127.0.0.1")
    floor_dir = Path(args.floor_dir) if args.floor_dir else Path(ops.issuer.trust_dir).parent / "floor"
    report = run(ops, floor_dir)
    print(json.dumps(report, default=str, sort_keys=True))
    tick = report["tick"] or {}
    floors = tick.get("floors") or {}
    bad = (bool(report["failure"]) or bool(report["reconcile"]["alerts"]) or bool(tick.get("pending_revocation"))
           or not floors or any(not str(v).startswith("written") for v in floors.values())
           or "could not" in str(tick.get("list", "")))
    return 1 if bad else 0


if __name__ == "__main__":
    os.umask(0o022)
    sys.exit(main())
