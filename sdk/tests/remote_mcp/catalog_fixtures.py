"""Shared fixtures for the catalog tests: a real stand (index + keys + catalog), real gateway
accounts/devices, real tokens, and the REAL transport + catalog adapter over a counting worker."""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import threading
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from agentnode_sdk.capability.approval import grant
from agentnode_sdk.capability.index import LocalIndex
from agentnode_sdk.gateway import accounts as A
from agentnode_sdk.gateway.identity import GatewayState
from agentnode_sdk.remote_mcp.accounts_login import GatewayLogin
from agentnode_sdk.remote_mcp.catalog import Catalog
from agentnode_sdk.remote_mcp.catalog_audit import CatalogLedger
from agentnode_sdk.remote_mcp.http_transport import RemoteMCPApp
from agentnode_sdk.remote_mcp.limits import Limits
from agentnode_sdk.remote_mcp.oauth_as import AuthorizationServer
from agentnode_sdk.remote_mcp.prm import ResourceConfig
from agentnode_sdk.remote_mcp.server import build_catalog_adapter
from agentnode_sdk.remote_mcp.tokens import TokenStore
from agentnode_sdk.worker import Isolation, Outcome

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "capability"))
from _pack_fixtures import FIXTURE_PACKS, STARTER, build_artifact, lock_entry_for  # noqa: E402, F401

HOST = "mcp.agentnode.net"
RESOURCE = "https://mcp.agentnode.net/mcp"
ISSUER = "https://mcp.agentnode.net"
NS = "local"
PACKS = ("regex-builder-pack", "document-redaction-pack", "citation-manager-pack")


def raw_pub(k):
    return k.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


class CountingWorker:
    """Runs the job's bootstrap locally (a TEST DOUBLE, not a sandbox) and counts dispatches."""

    def __init__(self, available=True):
        self.dispatched = 0
        self.available = available
        self.gate: threading.Event | None = None
        self.started = threading.Event()
        self.address = "tcps://test-worker:8443"

    def can_it_isolate(self):
        return Isolation(available=self.available, backend="fake-sandbox")

    def run(self, job):
        self.dispatched += 1
        self.started.set()
        if self.gate is not None:
            self.gate.wait(30)
        stub = job.command[2]
        proc = subprocess.run([sys.executable, "-c", stub], input=job.stdin, text=True,
                              capture_output=True, timeout=60)
        return Outcome(exit_code=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)


def import_into(stand: Path, op, pack_dir: Path, *, namespace=NS):
    from agentnode_sdk.capability.pack_import import import_pack as _ip
    import yaml
    man = yaml.safe_load((pack_dir / "agentnode.yaml").read_text(encoding="utf-8"))
    art = build_artifact(pack_dir)
    imp = _ip(art, lock_entry_for(pack_dir, art), package_key=man["package_id"], package_id=man["package_id"],
              version=man["version"], namespace=namespace, private_key=op, public_key_bytes=raw_pub(op))
    idx = LocalIndex(str(stand / "index"))
    idx.put_blob(imp.tar_bytes)
    idx.register(imp.manifest)
    return imp


class Stand:
    """A complete catalog stand + gateway + transport, in a temp dir."""

    def __init__(self, root: Path, *, limits: Limits | None = None, worker: CountingWorker | None = None,
                 packs=PACKS, extra_packs=()):
        self.root = root
        self.stand = root / "stand"
        self.gw = root / "gw"
        (self.stand / "approvals").mkdir(parents=True)
        (self.stand / "consumed").mkdir()
        self.gw.mkdir()
        self.op = Ed25519PrivateKey.generate()
        self.au = Ed25519PrivateKey.generate()
        (self.stand / "operator_pub.hex").write_text(raw_pub(self.op).hex())
        (self.stand / "operator_priv.hex").write_text(self.op.private_bytes_raw().hex())
        (self.stand / "audit_signer_priv.hex").write_text(self.au.private_bytes_raw().hex())
        self.imports = {}
        for name in packs:
            self.imports[name] = import_into(self.stand, self.op, STARTER / name)
        for path in extra_packs:
            self.imports[path.name] = import_into(self.stand, self.op, path)
        self.ledger = CatalogLedger(self.stand / "catalog-audit.jsonl", signer_private=self.au,
                                    signer_public_bytes=raw_pub(self.au))
        self.catalog = Catalog(self.stand, raw_pub(self.op), operator_private=self.op,
                               on_change=lambda ev: self.ledger.append("operator_change", **ev))
        self.catalog.init(actor="test")
        self.worker = worker or CountingWorker()
        self.adapter = build_catalog_adapter(str(self.stand), self.worker)
        self.state = GatewayState(str(self.gw), "test")
        self.devices: dict = {}
        self.store = TokenStore(b"k" * 32)
        gl = GatewayLogin(str(self.gw))
        self.app = RemoteMCPApp(adapter=self.adapter, token_store=self.store,
                                authz=AuthorizationServer(issuer=ISSUER, resource=RESOURCE, token_store=self.store),
                                resource=ResourceConfig(resource=RESOURCE, authorization_server=ISSUER),
                                allowed_hosts=(HOST,), login_provider=gl, live_authority=gl,
                                limits=limits or Limits())

    # ---- accounts + tokens ----
    def account(self, name: str) -> tuple[str, str]:
        acct = A.new_account_id()
        self.state.accounts.create(account_id=acct, name=name)
        dev = "dev-%s" % name
        self.devices["hash-" + dev] = {"client_id": dev, "account_id": acct, "client_name": name,
                                       "issued_at": 1.0, "capabilities": ["run", "read"]}
        self.state._write_private("tokens.json", json.dumps(self.devices))
        token = self.store.mint(principal_id=acct, audience=RESOURCE, scopes=("mcp:use",), issuance_kind="oauth",
                                ttl_seconds=3600, device_or_connection_id=dev).plaintext
        return acct, token

    def approve(self, invocation_hash: str, capability_id: str):
        ap = grant(invocation_hash, audience=capability_id, human_actor="human@test",
                   operator_private=self.op, operator_public_bytes=raw_pub(self.op))
        (self.stand / "approvals" / f"{ap.approval_id}.json").write_text(json.dumps(ap.to_dict()))
        return ap


META = {"io.modelcontextprotocol/protocolVersion": "2026-07-28", "io.modelcontextprotocol/clientCapabilities": {}}


def rpc(app, token, method, params=None, *, mid=1, legacy=False, name=None):
    params = dict(params or {})
    headers = {"content-type": "application/json", "accept": "application/json, text/event-stream",
               "authorization": "Bearer %s" % token}
    if legacy:
        headers["mcp-protocol-version"] = "2025-11-25"
    else:
        headers.update({"mcp-protocol-version": "2026-07-28", "mcp-method": method})
        params["_meta"] = {**META, **(params.get("_meta") or {})}
        if method == "tools/call":
            headers["mcp-name"] = name if name is not None else params.get("name", "")
    body = {"jsonrpc": "2.0", "id": mid, "method": method, "params": params}

    async def go():
        import httpx
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://%s" % HOST) as c:
            r = await c.post("/mcp", headers=headers, content=json.dumps(body))
            return r.status_code, (r.json() if r.content else None)
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(go())
    finally:
        loop.close()


def call(app, token, tool, arguments, **kw):
    return rpc(app, token, "tools/call", {"name": tool, "arguments": arguments}, **kw)


def hash_of(resp_json) -> str:
    return resp_json["result"]["structuredContent"]["invocation_hash"]
