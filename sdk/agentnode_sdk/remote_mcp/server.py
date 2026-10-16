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


def _load_store(stand: str) -> TokenStore:
    key = _server_key(stand)
    tokens_path = os.path.join(stand, TOKENS_FILE)
    return TokenStore.load_hashed(tokens_path, key) if os.path.exists(tokens_path) else TokenStore(key)


def build_app(stand: str, *, resource: str, issuer: str, allowed_hosts: tuple[str, ...],
              allowed_origins: tuple[str, ...] = (), preregistered=None,
              allow_x_access_token: bool = False, limits: Limits | None = None) -> RemoteMCPApp:
    """STAND builder (restricted VPN test stand): LOCAL podman worker + the disabled
    ``X-AgentNode-Login`` pre-shared-secret shim. NOT for production — the shim is wired here
    and ONLY here, so ``build_production_app`` (which never calls this) has no path to it."""
    adapter = build_from_stand(stand)                      # the REAL frozen adapter, local worker
    store = _load_store(stand)
    rc = ResourceConfig(resource=resource, authorization_server=issuer)
    authz = AuthorizationServer(issuer=issuer, resource=resource, token_store=store,
                                preregistered=dict(preregistered or {}))
    return RemoteMCPApp(adapter=adapter, token_store=store, authz=authz, resource=rc,
                        allowed_hosts=allowed_hosts, allowed_origins=allowed_origins,
                        principal_resolver=_stand_login_resolver(stand),
                        limits=limits or Limits(), allow_x_access_token=allow_x_access_token)


def _build_remote_worker(stand: str):
    """Build the mutually-authenticated control→worker mTLS client from ``stand/worker.json``.

    This is the ONLY difference between a production adapter and the frozen local one: execution
    crosses the existing private channel (``worker.remote.from_address`` → ``TlsWorker`` over
    ``tcps://<worker-ip>:8443``) instead of a local podman worker. ``separate-worker-host``
    topology forbids loopback/DNS/wildcard addresses and requires the keyring+tombstones.
    """
    import json

    from agentnode_sdk.worker import SEPARATE_WORKER_HOST, pairkeys
    from agentnode_sdk.worker.remote import from_address
    from agentnode_sdk.worker.tls import TlsSettings, own_instance

    cfg = json.loads(io.open(os.path.join(stand, "worker.json"), encoding="utf-8").read())
    t = cfg["tls"]
    tls = TlsSettings(certificate=t["certificate"], key=t["key"], anchor=t["anchor"],
                      deployment=cfg["deployment"], accept=frozenset(cfg["accept"]),
                      revocation_list=t["revocation_list"], floor=t["floor"],
                      identity_tombstones=t.get("identity_tombstones", ""),
                      tombstones_required=True)
    keyring = pairkeys.Keyring.read(cfg["keyring"])
    return from_address(cfg["worker_address"], None, tls=tls,
                        topology=SEPARATE_WORKER_HOST, keyring=keyring,
                        own_instance=own_instance(tls))


def _build_remote_adapter(stand: str):
    """Mirror of the frozen ``build_from_stand`` wiring, but over the REMOTE worker. Every
    security-critical piece (UsabilityClient, tree_pipeline, approval, audit, index) is reused
    unchanged; only the ``Worker`` instance differs, which ``tree_execution`` treats as a
    drop-in because it uses only the ``Worker`` ABC surface."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    from agentnode_sdk.capability.approval import ApprovalStore
    from agentnode_sdk.capability.audit import AuditLedger
    from agentnode_sdk.capability.index import LocalIndex
    from agentnode_sdk.capability.usability import UsabilityClient

    cap_id = io.open(os.path.join(stand, "capability_id.txt"), encoding="utf-8").read().strip()
    op_pub = bytes.fromhex(io.open(os.path.join(stand, "operator_pub.hex"), encoding="utf-8").read().strip())
    au_priv = Ed25519PrivateKey.from_private_bytes(
        bytes.fromhex(io.open(os.path.join(stand, "audit_signer_priv.hex"), encoding="utf-8").read().strip()))
    au_pub = au_priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    idx = LocalIndex(os.path.join(stand, "index"))
    ledger = AuditLedger(os.path.join(stand, "audit.jsonl"), ledger_id="adapter-L1",
                         signer_private=au_priv, signer_public_bytes=au_pub)
    worker = _build_remote_worker(stand)
    client = UsabilityClient(index=idx, worker=worker, audit_ledger=ledger,
                             approval_store=ApprovalStore(consumed_dir=os.path.join(stand, "consumed")),
                             operator_public_bytes=op_pub,
                             worker_identity="mtls:" + str(getattr(worker, "address", "")),
                             stand=stand)
    return UsabilityMCPAdapter(client, cap_id, approvals_dir=os.path.join(stand, "approvals"))


def build_production_app(stand: str, *, resource: str, issuer: str,
                         allowed_hosts: tuple[str, ...], gateway_dir: str,
                         console_url: str = "", allowed_origins: tuple[str, ...] = (),
                         preregistered=None, limits: Limits | None = None,
                         local_worker: bool = False) -> RemoteMCPApp:
    """PRODUCTION builder: execution on the SEPARATE worker over mTLS, OAuth bound to the real
    gateway accounts/devices/sessions/capabilities/revocations via :class:`GatewayLogin`.

    The ``X-AgentNode-Login`` shim is NEVER constructed here; there is no ``principal_resolver``,
    so that header is structurally dead. ``local_worker=True`` is a narrow escape hatch for a
    single-host bring-up check and still never wires the shim.
    """
    from agentnode_sdk.remote_mcp.accounts_login import GatewayLogin

    adapter = build_from_stand(stand) if local_worker else _build_remote_adapter(stand)
    store = _load_store(stand)
    rc = ResourceConfig(resource=resource, authorization_server=issuer)
    authz = AuthorizationServer(issuer=issuer, resource=resource, token_store=store,
                                preregistered=dict(preregistered or {}))
    return RemoteMCPApp(adapter=adapter, token_store=store, authz=authz, resource=rc,
                        allowed_hosts=allowed_hosts, allowed_origins=allowed_origins,
                        login_provider=GatewayLogin(gateway_dir), console_url=console_url,
                        limits=limits or Limits(), allow_x_access_token=False)


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
