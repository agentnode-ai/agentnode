"""The ONE production entrypoint (profile R17, /goal #1 & #5).

systemd runs exactly this: ``python -m agentnode_sdk.remote_mcp.production <stand> ...``. It
starts ONLY :func:`agentnode_sdk.remote_mcp.server.build_production_app`. There is deliberately
no ``--local-worker`` flag, no environment switch, and no import of ``LocalWorker`` or the
stand builder here: execution always crosses the separate mutually-authenticated worker, and a
missing worker/mTLS/keyring/CRL/floor/tombstones artifact fails the start rather than degrading
to anything local. The disabled ``X-AgentNode-Login`` stand shim is unreachable from this path.

TLS and public Host/Origin are nginx's at deploy; this process binds loopback only.
"""
from __future__ import annotations

import argparse

from agentnode_sdk.remote_mcp.server import build_production_app


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="remote_mcp.production",
                                description="Production remote-MCP service (no local fallback).")
    p.add_argument("stand", help="the production stand dir (holds worker.json + capability/audit/approvals)")
    p.add_argument("--gateway-dir", required=True,
                   help="the AgentNode gateway state dir OAuth binds to (accounts/devices/sessions)")
    p.add_argument("--resource", required=True)
    p.add_argument("--issuer", required=True)
    p.add_argument("--allowed-host", action="append", dest="allowed_hosts", required=True)
    p.add_argument("--allowed-origin", action="append", dest="allowed_origins", default=[])
    p.add_argument("--console-url", default="")
    p.add_argument("--host", default="127.0.0.1", help="loopback only; nginx terminates TLS in front")
    p.add_argument("--port", type=int, default=8800)
    p.add_argument("--probe-slot-socket", default=None,
                   help="the root-only Unix socket renewal probes take their run slot from "
                        "(default /run/agentnode-mcp/probe-slot.sock); a path, never a network address")
    p.add_argument("--limits-file", default=None,
                   help="an operator's JSON object overriding named fields of remote_mcp.limits.Limits (absent: the "
                        "decided defaults); an unknown field or a non-number is refused at start")
    args = p.parse_args(argv)
    limits = None
    if args.limits_file:
        import dataclasses
        import json as _json

        from agentnode_sdk.remote_mcp.limits import Limits

        with open(args.limits_file, encoding="utf-8") as fh:
            over = _json.load(fh)
        known = {f.name: f.type for f in dataclasses.fields(Limits)}
        if not isinstance(over, dict) or set(over) - set(known) or not all(
                isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 for v in over.values()):
            raise SystemExit("--limits-file names an unknown limit or a value that is not a positive number")
        limits = Limits(**over)
        print("limits overridden by %s: %s" % (args.limits_file, ", ".join(sorted(over))), flush=True)

    # Optional preregistered OAuth clients (client_id -> [redirect_uri,...]) from a stand file.
    # CIMD stays the primary dynamic path; this is for a named, pre-agreed client whose metadata
    # URL cannot be fetched (e.g. a closed/loopback deployment), not a substitute for it.
    import json
    import os
    prereg = None
    prereg_path = os.path.join(args.stand, "preregistered_clients.json")
    if os.path.exists(prereg_path):
        with open(prereg_path, encoding="utf-8") as fh:
            prereg = {cid: tuple(uris) for cid, uris in json.load(fh).items()}

    app = build_production_app(
        args.stand, resource=args.resource, issuer=args.issuer,
        allowed_hosts=tuple(args.allowed_hosts), gateway_dir=args.gateway_dir,
        console_url=args.console_url, allowed_origins=tuple(args.allowed_origins),
        preregistered=prereg, probe_slot_path=args.probe_slot_socket, limits=limits)
    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning", lifespan="on")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
