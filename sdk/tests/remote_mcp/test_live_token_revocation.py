"""R8: an operator's token revocation takes effect at the RUNNING resource server on the next
request -- not at the next restart. The serving store follows the hashed file the human CLI
(`personal_tokens`) writes; the merge only tightens, and an unreadable file refuses."""
from __future__ import annotations

import asyncio
import json
import os

import httpx

from agentnode_sdk.remote_mcp import personal_tokens
from agentnode_sdk.remote_mcp.http_transport import RemoteMCPApp
from agentnode_sdk.remote_mcp.limits import Limits
from agentnode_sdk.remote_mcp.oauth_as import AuthorizationServer
from agentnode_sdk.remote_mcp.prm import ResourceConfig
from agentnode_sdk.remote_mcp.tokens import TokenStore, TokenVerifyError

HOST = "mcp.agentnode.net"
RESOURCE = "https://mcp.agentnode.net/mcp"
KEY = b"k" * 32


class Adapter:
    def handle(self, msg):
        return {"jsonrpc": "2.0", "id": msg.get("id"), "result": {"tools": []}}


def serving(tmp_path):
    path = str(tmp_path / "tokens.hashed.json")
    TokenStore(KEY).dump_hashed(path)
    store = TokenStore.load_hashed(path, KEY).watch(path)
    app = RemoteMCPApp(adapter=Adapter(), token_store=store,
                       authz=AuthorizationServer(issuer="https://mcp.agentnode.net", resource=RESOURCE,
                                                 token_store=store),
                       resource=ResourceConfig(resource=RESOURCE, authorization_server="https://mcp.agentnode.net"),
                       allowed_hosts=(HOST,), limits=Limits())
    keyfile = tmp_path / "key"
    keyfile.write_bytes(KEY)
    return app, store, path, str(keyfile)


def cli(*argv, capsys=None):
    rc = personal_tokens.main(list(argv))
    out = capsys.readouterr().out if capsys else ""
    return rc, out


def mint_via_cli(path, keyfile, capsys, principal="acct-1"):
    rc, out = cli("mint", "--store", path, "--key-file", keyfile, "--principal", principal,
                  "--client-id", "dev-1", "--audience", RESOURCE, "--scope", "mcp:use", "--ttl", "600",
                  capsys=capsys)
    assert rc == 0
    tid = out.split("token_id: ", 1)[1].split()[0]
    return tid, out.strip().splitlines()[-1]


def tools_list(app, token):
    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://%s" % HOST) as c:
            return await c.post("/mcp", headers={
                "content-type": "application/json", "accept": "application/json, text/event-stream",
                "mcp-protocol-version": "2025-11-25", "authorization": "Bearer %s" % token},
                content=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode())
    return asyncio.run(go())


def test_cli_minted_token_works_without_restart_and_cli_revocation_is_immediate(tmp_path, capsys):
    app, store, path, keyfile = serving(tmp_path)
    tid, token = mint_via_cli(path, keyfile, capsys)
    assert tools_list(app, token).status_code == 200           # picked up live, no restart
    rc, _ = cli("revoke", "--store", path, "--key-file", keyfile, "--token-id", tid, capsys=capsys)
    assert rc == 0
    r = tools_list(app, token)                                  # SAME running app, next request
    assert r.status_code == 401 and r.json()["error"] == "revoked_token"


def test_generation_bump_in_the_file_revokes_all_of_a_principals_tokens(tmp_path, capsys):
    app, store, path, keyfile = serving(tmp_path)
    _, token = mint_via_cli(path, keyfile, capsys, principal="acct-9")
    assert tools_list(app, token).status_code == 200
    op = TokenStore.load_hashed(path, KEY)
    op.revoke_all_for("acct-9")
    op.dump_hashed(path)
    assert tools_list(app, token).status_code == 401


def test_merge_never_undoes_a_revocation_and_keeps_memory_only_tokens(tmp_path, capsys):
    app, store, path, keyfile = serving(tmp_path)
    oauth = store.mint(principal_id="acct-2", audience=RESOURCE, scopes=("mcp:use",),
                       issuance_kind="oauth", ttl_seconds=600)       # lives only in memory
    tid, token = mint_via_cli(path, keyfile, capsys)
    assert tools_list(app, token).status_code == 200
    store.revoke(tid)                                           # revoked in the serving memory
    stale = TokenStore.load_hashed(path, KEY)                   # a file still saying "not revoked"
    stale.mint(principal_id="acct-3", audience=RESOURCE, scopes=("mcp:use",),
               issuance_kind="personal", ttl_seconds=600)       # ...and changed, so it is re-read
    stale.dump_hashed(path)
    assert tools_list(app, token).status_code == 401            # still revoked
    assert tools_list(app, oauth.plaintext).status_code == 200  # memory-only token kept


def test_unreadable_store_file_refuses_instead_of_serving_stale_state(tmp_path, capsys):
    app, store, path, keyfile = serving(tmp_path)
    _, token = mint_via_cli(path, keyfile, capsys)
    assert tools_list(app, token).status_code == 200
    with open(path, "w") as fh:
        fh.write("{not json")
    r = tools_list(app, token)
    assert r.status_code == 503 and r.json()["error"] == "token_store_unreadable"
    assert "www-authenticate" not in r.headers


def _serving_from_stand(tmp_path):
    """The serving store exactly as the service builds it (watch + OAuth publication)."""
    from agentnode_sdk.remote_mcp import server as S
    stand = str(tmp_path)
    with open(os.path.join(stand, S.KEY_FILE), "wb") as fh:
        fh.write(KEY)
    store = S._load_store(stand)
    app = RemoteMCPApp(adapter=Adapter(), token_store=store,
                       authz=AuthorizationServer(issuer="https://mcp.agentnode.net", resource=RESOURCE,
                                                 token_store=store),
                       resource=ResourceConfig(resource=RESOURCE, authorization_server="https://mcp.agentnode.net"),
                       allowed_hosts=(HOST,), limits=Limits())
    return app, store, os.path.join(stand, S.TOKENS_FILE), os.path.join(stand, S.KEY_FILE), \
        os.path.join(stand, S.OAUTH_PUBLIC_FILE)


def test_an_oauth_token_can_be_listed_and_revoked_by_id_through_the_cli(tmp_path, capsys):
    app, store, path, keyfile, public = _serving_from_stand(tmp_path)
    oauth = store.mint(principal_id="acct-o", audience=RESOURCE, scopes=("mcp:use",),
                       issuance_kind="oauth", ttl_seconds=600)          # held only by the service
    assert tools_list(app, oauth.plaintext).status_code == 200
    pub = json.load(open(public))["oauth_tokens"]
    assert [v["token_id"] for v in pub] == [oauth.record.token_id]
    assert "token_hash" not in json.dumps(pub) and oauth.plaintext.split("_", 2)[2] not in json.dumps(pub)
    rc, out = cli("list", "--store", path, "--key-file", keyfile, capsys=capsys)
    assert rc == 0 and oauth.record.token_id in out
    rc, out = cli("revoke", "--store", path, "--key-file", keyfile, "--token-id", oauth.record.token_id,
                  capsys=capsys)
    assert rc == 0 and "recorded" in out
    r = tools_list(app, oauth.plaintext)                        # same running service, next request
    assert r.status_code == 401 and r.json()["error"] == "revoked_token"


def test_revoke_principal_through_the_cli_kills_its_oauth_tokens(tmp_path, capsys):
    app, store, path, keyfile, _ = _serving_from_stand(tmp_path)
    oauth = store.mint(principal_id="acct-p", audience=RESOURCE, scopes=("mcp:use",),
                       issuance_kind="oauth", ttl_seconds=600)
    other = store.mint(principal_id="acct-q", audience=RESOURCE, scopes=("mcp:use",),
                       issuance_kind="oauth", ttl_seconds=600)
    assert tools_list(app, oauth.plaintext).status_code == 200
    rc, _ = cli("revoke-principal", "--store", path, "--key-file", keyfile, "--principal", "acct-p",
                capsys=capsys)
    assert rc == 0
    assert tools_list(app, oauth.plaintext).status_code == 401
    assert tools_list(app, other.plaintext).status_code == 200      # only that principal


def test_unwatched_store_is_unchanged(tmp_path):
    store = TokenStore(KEY)
    tok = store.mint(principal_id="p", audience=RESOURCE, scopes=("mcp:use",), issuance_kind="oauth",
                     ttl_seconds=60).plaintext
    assert store.verify(tok, expected_audience=RESOURCE).principal_id == "p"
    try:
        store.verify("ant_x_y", expected_audience=RESOURCE)
    except TokenVerifyError as exc:
        assert exc.http_status == 401
    assert not os.path.exists(str(tmp_path / "tokens.hashed.json"))
