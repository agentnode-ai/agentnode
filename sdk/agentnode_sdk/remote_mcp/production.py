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
    args = p.parse_args(argv)

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
        preregistered=prereg)
    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
