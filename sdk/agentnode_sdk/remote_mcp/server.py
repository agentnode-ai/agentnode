"""Assemble the remote MCP ASGI app from a persisted stand, bound to loopback (R11, R17).

Production (:func:`build_production_app`) serves the per-account capability catalog
(:func:`build_catalog_adapter`) over the remote mTLS worker. The local/test stand
(:func:`build_app`) reuses ``usability_adapter.build_from_stand`` to get the REAL
UsabilityMCPAdapter (real index, operator-key-less approval store, real worker, signed audit).
Both place the thin transport + OAuth AS in front of the adapter. TLS and public Host/Origin enforcement are nginx's at deploy; this
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
import json
import os
import time

from agentnode_sdk.capability.usability_adapter import build_from_stand
from agentnode_sdk.remote_mcp.http_transport import RemoteMCPApp
from agentnode_sdk.remote_mcp.limits import Limits
from agentnode_sdk.remote_mcp.oauth_as import AuthorizationServer
from agentnode_sdk.remote_mcp.prm import ResourceConfig
from agentnode_sdk.remote_mcp.tokens import TokenStore

TOKENS_FILE = "tokens.hashed.json"
KEY_FILE = "token_server_key.bin"
#: hash-free list of the OAuth tokens the serving process holds (operator visibility only)
OAUTH_PUBLIC_FILE = "tokens.oauth.public.json"


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
    """The serving store, following the operator's hashed file so that `personal_tokens
    revoke` (and mint) take effect at the resource server on the next request."""
    key = _server_key(stand)
    tokens_path = os.path.join(stand, TOKENS_FILE)
    store = TokenStore.load_hashed(tokens_path, key) if os.path.exists(tokens_path) else TokenStore(key)
    public_path = os.path.join(stand, OAUTH_PUBLIC_FILE)

    def publish(rec):
        # OAuth tokens live only in this process; publish their hash-free views so an operator
        # can name one to `personal_tokens revoke --token-id` (which tombstones it in the file).
        if rec.issuance_kind != "oauth":
            return
        now = int(time.time())
        views = [r.public_view() for r in store._by_id.values()  # noqa: SLF001
                 if r.issuance_kind == "oauth" and r.expires_at > now]
        tmp = public_path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"oauth_tokens": views}, fh, indent=1, sort_keys=True)
        os.replace(tmp, public_path)
    store.on_mint = publish
    return store.watch(tokens_path)


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

    worker_json = os.path.join(stand, "worker.json")
    if not os.path.exists(worker_json):
        raise RuntimeError(
            "production remote worker config %s is missing; the production service has no local "
            "worker fallback and will not start without the mutually-authenticated worker." % worker_json)
    cfg = json.loads(io.open(worker_json, encoding="utf-8").read())
    t = cfg.get("tls") or {}
    # Every mandatory artifact must be present AND exist on disk; a missing one fails the start
    # (fail-closed) rather than silently degrading to anything local.
    required = {"worker_address": cfg.get("worker_address"), "deployment": cfg.get("deployment"),
                "accept": cfg.get("accept"), "keyring": cfg.get("keyring"),
                "tls.certificate": t.get("certificate"), "tls.key": t.get("key"),
                "tls.anchor": t.get("anchor"), "tls.revocation_list": t.get("revocation_list"),
                "tls.floor": t.get("floor"), "tls.identity_tombstones": t.get("identity_tombstones")}
    missing = [k for k, v in required.items() if not v]
    if missing:
        raise RuntimeError("production worker config is missing mandatory artifacts: %s"
                           % ", ".join(sorted(missing)))
    file_artifacts = {"keyring": cfg["keyring"], "tls.certificate": t["certificate"],
                      "tls.key": t["key"], "tls.anchor": t["anchor"],
                      "tls.revocation_list": t["revocation_list"], "tls.floor": t["floor"],
                      "tls.identity_tombstones": t["identity_tombstones"]}
    absent = [k for k, p in file_artifacts.items() if not os.path.exists(p)]
    if absent:
        raise RuntimeError("production worker artifact files do not exist: %s"
                           % ", ".join("%s=%s" % (k, file_artifacts[k]) for k in sorted(absent)))
    tls = TlsSettings(certificate=t["certificate"], key=t["key"], anchor=t["anchor"],
                      deployment=cfg["deployment"], accept=frozenset(cfg["accept"]),
                      revocation_list=t["revocation_list"], floor=t["floor"],
                      identity_tombstones=t["identity_tombstones"],
                      tombstones_required=True)
    keyring = pairkeys.Keyring.read(cfg["keyring"])
    # SEPARATE_WORKER_HOST topology itself refuses a loopback/DNS/wildcard address.
    return from_address(cfg["worker_address"], None, tls=tls,
                        topology=SEPARATE_WORKER_HOST, keyring=keyring,
                        own_instance=own_instance(tls))


def _worker_base(stand: str):
    """(worker.json, the control side's own TlsSettings) -- what every target's connection shares:
    this control plane's certificate, anchor, revocation list, floor and tombstones."""
    import json

    from agentnode_sdk.worker.tls import TlsSettings

    cfg = json.loads(io.open(os.path.join(stand, "worker.json"), encoding="utf-8").read())
    t = cfg["tls"]
    tls = TlsSettings(certificate=t["certificate"], key=t["key"], anchor=t["anchor"],
                      deployment=cfg["deployment"], accept=frozenset(cfg["accept"]),
                      revocation_list=t["revocation_list"], floor=t["floor"],
                      identity_tombstones=t["identity_tombstones"], tombstones_required=True)
    return cfg, tls


def target_worker_factory(stand: str):
    """The mTLS worker of ONE register target: its address, and its proved identity as the ONLY
    instance accepted at that address (a different URI there is refused at the handshake)."""
    import dataclasses

    from agentnode_sdk.worker import SEPARATE_WORKER_HOST, pairkeys
    from agentnode_sdk.worker.remote import from_address
    from agentnode_sdk.worker.tls import own_instance

    def build(target):
        cfg, tls = _worker_base(stand)
        w = target.worker
        if w["pair"]["gateway"] != own_instance(tls):
            raise RuntimeError("target %s is paired with another gateway instance" % target.target_id)
        settings = dataclasses.replace(tls, deployment=w["deployment"], accept=frozenset({w["instance"]}))
        return from_address(w["address"], None, tls=settings, topology=SEPARATE_WORKER_HOST,
                            keyring=pairkeys.Keyring.read(cfg["keyring"]), own_instance=own_instance(tls))
    return build


def deployment_trust(stand: str):
    """The exact trust pair this control plane itself judges by (`trust_install.FACT_FIELDS`), read
    now; None when either file is unreadable or does not parse -- and None admits no target."""
    from agentnode_sdk.worker import trust_install

    def facts():
        _cfg, tls = _worker_base(stand)
        try:
            with open(tls.revocation_list, "rb") as crl, open(tls.identity_tombstones, "rb") as tombs:
                return trust_install.facts_of(crl.read(), tombs.read())
        except (OSError, ValueError):
            return None
    return facts


def previous_trust(stand: str):
    """The pair a scheduled refresh saved right before its last publication (`targets.save_previous_pair`),
    with its facts and content digest -- only if this control plane's own anchor verifies BOTH documents
    at its effective time; None otherwise (absent, unreadable, unsigned, expired)."""
    from agentnode_sdk.pki import revocation, tombstones
    from agentnode_sdk.remote_mcp import targets
    from agentnode_sdk.worker import trust_install

    def facts():
        pair = targets.previous_pair(stand)
        if pair is None:
            return None
        crl, tombs = pair
        try:
            _cfg, tls = _worker_base(stand)
            view = tls.trust("gateway")
            effective = view.effective_time()
            revocation.read(crl, view.anchor(), effective)
            tombstones.read(tombs, deployment=tls.deployment, verifier=view._verify_with_anchor).usable_at(effective)
            return trust_install.facts_of(crl, tombs)
        except Exception:                                     # noqa: BLE001 - not believed: never admits
            return None
    return facts


def build_target_router(stand: str, worker_for=None, deployment=None, previous=None):
    """The execution-target router of a stand (target-router). The register is verified on every
    use; a missing or untrustworthy register refuses every run (503) -- never a fallback."""
    from agentnode_sdk.remote_mcp.target_router import TargetRouter
    from agentnode_sdk.remote_mcp.targets import Register, TrustAcks

    op_pub = bytes.fromhex(io.open(os.path.join(stand, "operator_pub.hex"), encoding="utf-8").read().strip())
    return TargetRouter(register=Register(stand, op_pub), acks=TrustAcks(stand, op_pub),
                        worker_for=worker_for or target_worker_factory(stand),
                        deployment_trust=deployment or deployment_trust(stand),
                        previous_trust=previous or previous_trust(stand))


def build_catalog_adapter(stand: str, worker, *, router=None, authority=None, kill_switch=None):
    """The per-account catalog adapter over *worker*, wired from a stand directory.

    Every security-critical piece (UsabilityClient, tree_pipeline, approval, audit, index) is
    reused unchanged; the catalog decides per account what may be listed and called, and no global
    single-capability file is read. A missing or untrustworthy catalog does not stop the start
    but refuses every catalog request (503) until it verifies again.
    """
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    from agentnode_sdk.capability.approval import ApprovalStore
    from agentnode_sdk.capability.audit import AuditLedger
    from agentnode_sdk.capability.index import LocalIndex
    from agentnode_sdk.capability.usability import UsabilityClient
    from agentnode_sdk.remote_mcp.catalog import Catalog
    from agentnode_sdk.remote_mcp.catalog_adapter import CatalogMCPAdapter, cursor_key_from
    from agentnode_sdk.remote_mcp.catalog_audit import CatalogLedger

    op_pub = bytes.fromhex(io.open(os.path.join(stand, "operator_pub.hex"), encoding="utf-8").read().strip())
    au_priv = Ed25519PrivateKey.from_private_bytes(
        bytes.fromhex(io.open(os.path.join(stand, "audit_signer_priv.hex"), encoding="utf-8").read().strip()))
    au_pub = au_priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    idx = LocalIndex(os.path.join(stand, "index"))
    ledger = AuditLedger(os.path.join(stand, "audit.jsonl"), ledger_id="adapter-L1",
                         signer_private=au_priv, signer_public_bytes=au_pub)
    client = UsabilityClient(index=idx, worker=worker, audit_ledger=ledger,
                             approval_store=ApprovalStore(consumed_dir=os.path.join(stand, "consumed")),
                             operator_public_bytes=op_pub,
                             worker_identity="mtls:" + str(getattr(worker, "address", "")),
                             stand=stand)
    catalog = Catalog(stand, op_pub, index=idx)
    catalog_ledger = CatalogLedger(os.path.join(stand, "catalog-audit.jsonl"), signer_private=au_priv,
                                   signer_public_bytes=au_pub)
    return CatalogMCPAdapter(catalog=catalog, client=client, ledger=catalog_ledger,
                             approvals_dir=os.path.join(stand, "approvals"), stand=stand,
                             cursor_key=cursor_key_from(_server_key(stand)),
                             router=router, authority=authority, kill_switch=kill_switch)


def _build_catalog_adapter(stand: str, gateway_dir: str | None = None, authority=None):
    """Production: the catalog adapter over REMOTE mutually-authenticated workers, never local.

    The configured worker is still built first, so a missing worker/mTLS/keyring/CRL/floor/
    tombstones artifact fails the start. Every run then goes to the register target it resolves
    to (target-router); the gateway kill switch and the live account authority are judged again
    at admission."""
    worker = _build_remote_worker(stand)
    kill_switch = None
    if gateway_dir:
        from agentnode_sdk.gateway import allowance

        def kill_switch():
            return allowance.why_it_is_stopped(gateway_dir)
    return build_catalog_adapter(stand, worker, router=build_target_router(stand),
                                 authority=authority, kill_switch=kill_switch)


def build_production_app(stand: str, *, resource: str, issuer: str,
                         allowed_hosts: tuple[str, ...], gateway_dir: str,
                         console_url: str = "", allowed_origins: tuple[str, ...] = (),
                         preregistered=None, limits: Limits | None = None) -> RemoteMCPApp:
    """PRODUCTION builder — the ONLY production path. It serves the per-account catalog
    (``_build_catalog_adapter``) and execution ALWAYS crosses the separate,
    mutually-authenticated worker over mTLS; there is NO parameter,
    env var or config value that selects a local worker, and missing worker/mTLS/keyring/CRL/
    floor/tombstones artifacts fail the start (``_build_remote_worker``). OAuth binds to the
    real gateway accounts/devices/sessions/capabilities/revocations via :class:`GatewayLogin`,
    which is ALSO the per-call live authority re-check (fail-closed). The ``X-AgentNode-Login``
    shim is never constructed here (no ``principal_resolver``), so that header is structurally
    dead. The only local path is the separate, test-marked :func:`build_app`.
    """
    from agentnode_sdk.remote_mcp.accounts_login import GatewayLogin

    gl = GatewayLogin(gateway_dir)
    adapter = _build_catalog_adapter(stand, gateway_dir, authority=gl.authority_error)
    # before the first request: settle what an earlier process admitted but never ended. If the
    # register cannot be read now, the settlement stays pending and runs before the next target run.
    adapter.settle_earlier_dispatches()
    store = _load_store(stand)
    rc = ResourceConfig(resource=resource, authorization_server=issuer)
    authz = AuthorizationServer(issuer=issuer, resource=resource, token_store=store,
                                preregistered=dict(preregistered or {}))
    return RemoteMCPApp(adapter=adapter, token_store=store, authz=authz, resource=rc,
                        allowed_hosts=allowed_hosts, allowed_origins=allowed_origins,
                        login_provider=gl, live_authority=gl, console_url=console_url,
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
