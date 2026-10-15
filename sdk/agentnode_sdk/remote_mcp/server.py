"""Assemble the remote MCP ASGI app from a persisted stand, bound to loopback (R11, R17).

Reuses ``usability_adapter.build_from_stand`` to get the REAL UsabilityMCPAdapter (real index,
operator-key-less approval store, real worker, signed audit) and places the thin transport +
OAuth AS in front of it. TLS and public Host/Origin enforcement are nginx's at deploy; this
app binds to 127.0.0.1 / a unix socket and additionally enforces Host/Origin itself.

Stand-only login shim: on the restricted VPN test stand there is no browser login UI, so
``/authorize`` resolves the principal from a pre-shared ``X-AgentNode-Login`` secret delivered
to the operator out of band (``stand/login_secret.txt`` -> ``stand/login_principal.txt``). In
production this is replaced by real AgentNode account authentication; the transport only needs
*a* principal resolver, so nothing downstream changes.

Run (on the stand, behind nginx or via an SSH tunnel for tests):
  python -m agentnode_sdk.remote_mcp.server <stand> \
      --resource https://mcp.agentnode.net/mcp --issuer https://mcp.agentnode.net \
      --host 127.0.0.1 --port 8800 --allowed-host mcp.agentnode.net --allowed-host 127.0.0.1
"""
from __future__ import annotations

import argparse
import io
import os

from agentnode_sdk.capability.usability_adapter import build_from_stand
from agentnode_sdk.remote_mcp.http_transport import RemoteMCPApp
from agentnode_sdk.remote_mcp.limits import Limits
from agentnode_sdk.remote_mcp.oauth_as import AuthorizationServer
from agentnode_sdk.remote_mcp.prm import ResourceConfig
from agentnode_sdk.remote_mcp.tokens import TokenStore

TOKENS_FILE = "tokens.hashed.json"
KEY_FILE = "token_server_key.bin"


def _server_key(stand: str) -> bytes:
    path = os.path.join(stand, KEY_FILE)
    if not os.path.exists(path):
        with open(path, "wb") as fh:
            fh.write(os.urandom(48))
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    return io.open(path, "rb").read().strip()


def _stand_login_resolver(stand: str):
    """Return (scope, headers) -> principal_id for the stand-only pre-shared-secret shim."""
    secret_path = os.path.join(stand, "login_secret.txt")
    principal_path = os.path.join(stand, "login_principal.txt")

    def resolve(scope, headers):
        if not (os.path.exists(secret_path) and os.path.exists(principal_path)):
            return None
        secret = io.open(secret_path, encoding="utf-8").read().strip()
        presented = headers.get("x-agentnode-login", "")
        if presented and presented == secret:
            return io.open(principal_path, encoding="utf-8").read().strip()
        return None

    return resolve


def build_app(stand: str, *, resource: str, issuer: str, allowed_hosts: tuple[str, ...],
              allowed_origins: tuple[str, ...] = (), preregistered=None,
              allow_x_access_token: bool = False, limits: Limits | None = None) -> RemoteMCPApp:
    adapter = build_from_stand(stand)                      # the REAL frozen adapter
    key = _server_key(stand)
    tokens_path = os.path.join(stand, TOKENS_FILE)
    store = TokenStore.load_hashed(tokens_path, key) if os.path.exists(tokens_path) else TokenStore(key)
    rc = ResourceConfig(resource=resource, authorization_server=issuer)
    authz = AuthorizationServer(issuer=issuer, resource=resource, token_store=store,
                                preregistered=dict(preregistered or {}))
    return RemoteMCPApp(adapter=adapter, token_store=store, authz=authz, resource=rc,
                        allowed_hosts=allowed_hosts, allowed_origins=allowed_origins,
                        principal_resolver=_stand_login_resolver(stand),
                        limits=limits or Limits(), allow_x_access_token=allow_x_access_token)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="remote_mcp.server")
    p.add_argument("stand")
    p.add_argument("--resource", required=True)
    p.add_argument("--issuer", required=True)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8800)
    p.add_argument("--allowed-host", action="append", dest="allowed_hosts", required=True)
    p.add_argument("--allowed-origin", action="append", dest="allowed_origins", default=[])
    p.add_argument("--allow-x-access-token", action="store_true")
    args = p.parse_args(argv)
    app = build_app(args.stand, resource=args.resource, issuer=args.issuer,
                    allowed_hosts=tuple(args.allowed_hosts),
                    allowed_origins=tuple(args.allowed_origins),
                    allow_x_access_token=args.allow_x_access_token)
    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
