"""The per-account MCP catalog (profile mcp-catalog-r1, criteria K2-K11).

Real stand (index of import-pack capabilities signed by the operator key), real catalog document,
real gateway accounts/devices/tokens, the REAL transport and catalog adapter; the worker is a
counting test double that runs the job's program locally (the real sandbox is proven on the hosts).
"""
from __future__ import annotations

import json
import os
import shutil
import threading
from pathlib import Path

import pytest
from .catalog_fixtures import FIXTURE_PACKS, NS, CountingWorker, Stand, call, hash_of, import_into, rpc
from .catalog_fixtures import HOST, META
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agentnode_sdk.remote_mcp import catalog as C
from agentnode_sdk.remote_mcp.catalog import META_TOOLS, CatalogRefused, alias_for
from agentnode_sdk.remote_mcp.catalog_audit import AuditTampered
from agentnode_sdk.remote_mcp.limits import Limits

REGEX = "regex_builder_pack__data_cleaning"
REDACT = "document_redaction_pack__document_parsing"
CITE = "citation_manager_pack__citation_extraction"
REGEX_ARGS = {"pattern": r"\d+", "test_string": "a1 b22", "operation": "findall"}


def _cap(name):
    return f"{NS}/{name}"


@pytest.fixture
def st(tmp_path):
    s = Stand(tmp_path)
    _publish_starters(s)
    s.catalog.set_default([REGEX, REDACT, CITE], actor="t")
    return s


def _publish_starters(s):
    for imp in s.imports.values():
        s.catalog.publish(imp.manifest["capability_id"], "1.0.0", imp.manifest["tools"][0]["name"], actor="t")


def _tool_names(resp):
    return [t["name"] for t in resp["result"]["tools"]]


def _bodies(s):
    return [json.loads(x)["body"] for x in (s.stand / "catalog-audit.jsonl").read_text().splitlines()]


def _b64(data):
    import base64
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _raise_oserror(*a, **k):
    raise OSError("disk full")


def _regex_v2(st, tmp_path):
    pack = tmp_path / "regex-v2"
    shutil.copytree(Path(__file__).resolve().parents[3] / "starter-packs" / "regex-builder-pack", pack)
    y = (pack / "agentnode.yaml").read_text().replace('version: "1.0.0"', 'version: "1.0.1"')
    (pack / "agentnode.yaml").write_text(y)
    import_into(st.stand, st.op, pack)


# ===================================================================== K2 durability + trust
def test_publish_pins_exact_imported_bytes(st):
    snap = st.catalog.load()
    e = snap.published[REGEX]
    man = st.imports["regex-builder-pack"].manifest
    assert (e.capability_id, e.version, e.export) == (_cap("regex-builder-pack"), "1.0.0", "data_cleaning")
    assert e.tree_digest == man["source"]["package"]["tree_digest"]
    assert e.artifact_digest == man["artifact_digest"]["value"]
    assert e.tree_digest != "sha256:" + e.artifact_digest   # two different digests, never conflated


def test_generations_are_monotonic_and_history_is_written_first(st):
    g = st.catalog.load().generation
    st.catalog.set_default([REGEX], actor="t")
    assert st.catalog.load().generation == g + 1
    assert (st.stand / "catalog" / "history" / f"{g + 1}.json").exists()


@pytest.mark.parametrize("damage", ["bad_json", "edited_body", "wrong_key", "missing"])
def test_untrustworthy_catalog_fails_closed_503(st, damage):
    _, token = st.account("alice")
    path = st.stand / "catalog" / "catalog.json"
    assert rpc(st.app, token, "tools/list")[0] == 200
    if damage == "bad_json":
        path.write_text("{not json")
    elif damage == "edited_body":
        doc = json.loads(path.read_text())
        doc["default_active"] = []
        path.write_text(json.dumps(doc))
    elif damage == "wrong_key":
        other = Ed25519PrivateKey.generate()
        doc = json.loads(path.read_text())
        body = {k: v for k, v in doc.items() if k != "signature"}
        doc["signature"]["signature"] = other.sign(C._signed_bytes(body)).hex()
        path.write_text(json.dumps(doc))
    else:
        path.unlink()
    status, body = rpc(st.app, token, "tools/list")
    assert status == 503 and body == {"error": "catalog_unavailable"}
    assert call(st.app, token, REGEX, REGEX_ARGS)[0] == 503


def test_lower_generation_is_refused_and_rollback_is_a_new_generation(st):
    _, token = st.account("alice")
    first = st.catalog.load().generation
    st.catalog.set_default([REGEX], actor="t")
    old = (st.stand / "catalog" / "history" / f"{first}.json").read_bytes()
    current = (st.stand / "catalog" / "catalog.json").read_bytes()
    (st.stand / "catalog" / "catalog.json").write_bytes(old)          # replay an older signed generation
    assert rpc(st.app, token, "tools/list")[0] == 503
    (st.stand / "catalog" / "catalog.json").write_bytes(current)
    snap = st.catalog.rollback(first, actor="t")
    assert snap.generation == first + 2                               # NEW number, old contents
    assert set(snap.default_active) == {REGEX, REDACT, CITE}


def test_publish_refuses_untrusted_signer_altered_blob_and_unknown(st, tmp_path):
    other = Ed25519PrivateKey.generate()
    import_into(st.stand, other, FIXTURE_PACKS / "slow-pack", namespace="rogue")
    with pytest.raises(CatalogRefused) as e:
        st.catalog.publish("rogue/slow-pack", "1.0.0", "sleep_for", actor="t")
    assert e.value.code == "publish.untrusted_signer"
    with pytest.raises(CatalogRefused) as e:
        st.catalog.publish(_cap("regex-builder-pack"), "9.9.9", "data_cleaning", actor="t")
    assert e.value.code == "publish.not_in_index"
    with pytest.raises(CatalogRefused) as e:
        st.catalog.publish(_cap("regex-builder-pack"), "1.0.0", "nope", actor="t")
    assert e.value.code == "publish.no_such_export"
    blob = st.stand / "index" / "blobs" / st.imports["citation-manager-pack"].tar_sha256
    blob.write_bytes(b"tampered")
    with pytest.raises(CatalogRefused) as e:
        st.catalog.publish(_cap("citation-manager-pack"), "1.0.0", "citation_extraction", actor="t")
    assert e.value.code == "publish.blob"


def test_tree_digest_that_does_not_match_the_blob_is_refused(st):
    # correctly signed by the trusted key, the real blob present -- but the manifest pins another tree
    from agentnode_sdk.capability import manifest as M
    from agentnode_sdk.capability.index import LocalIndex
    man = {k: v for k, v in json.loads(json.dumps(st.imports["regex-builder-pack"].manifest)).items()
           if k != "signatures"}
    man["capability_version"] = "9.0.1"
    man["source"]["package"]["tree_digest"] = "sha256:" + "0" * 64
    LocalIndex(str(st.stand / "index")).register(
        M.sign_manifest(man, st.op, st.op.public_key().public_bytes_raw(), role="importing_publisher"))
    with pytest.raises(CatalogRefused) as e:
        st.catalog.publish(_cap("regex-builder-pack"), "9.0.1", "data_cleaning", actor="t")
    assert e.value.code == "publish.tree_digest"


@pytest.mark.parametrize("perm", ["network", "env_keys"])
def test_network_and_secret_capabilities_are_not_publishable(st, perm):
    # import-pack already refuses such packs; the catalog refuses them again even when a manifest
    # carrying them is correctly signed by the trusted operator key and indexed
    from agentnode_sdk.capability import manifest as M
    from agentnode_sdk.capability.index import LocalIndex
    man = {k: v for k, v in json.loads(json.dumps(st.imports["regex-builder-pack"].manifest)).items()
           if k != "signatures"}
    man["capability_version"] = "9.0.0"
    perms = man.setdefault("permissions", {})
    if perm == "network":
        perms["network"] = dict(perms.get("network") or {}, allowed_domains=["example.com"])
    else:
        perms["env_keys"] = ["API_TOKEN"]
    LocalIndex(str(st.stand / "index")).register(
        M.sign_manifest(man, st.op, st.op.public_key().public_bytes_raw(), role="importing_publisher"))
    with pytest.raises(CatalogRefused) as e:
        st.catalog.publish(_cap("regex-builder-pack"), "9.0.0", "data_cleaning", actor="t")
    assert e.value.code == "publish.network_or_secrets"


def test_collision_after_normalisation_and_reserved_names_are_refused(st):
    import_into(st.stand, st.op, FIXTURE_PACKS / "slow-pack", namespace="other")
    st.catalog.publish("other/slow-pack", "1.0.0", "sleep_for", actor="t")
    import_into(st.stand, st.op, FIXTURE_PACKS / "slow-pack", namespace="third")
    with pytest.raises(CatalogRefused) as e:
        st.catalog.publish("third/slow-pack", "1.0.0", "sleep_for", actor="t")   # same normalised alias
    assert e.value.code == "publish.collision"
    with pytest.raises(CatalogRefused) as e:
        st.catalog.publish("x/agentnode", "1.0.0", "run", actor="t")             # agentnode__run
    assert e.value.code == "publish.reserved_name"
    long = alias_for("p/" + "a" * 80, "export")
    assert len(long) == 64 and long == alias_for("p/" + "a" * 80, "export")


# ===================================================================== K3 account visibility
def test_default_explicit_and_explicit_empty(st):
    a, ta = st.account("alice")
    b, tb = st.account("bob")
    c, tc = st.account("carol")
    st.catalog.set_account(b, [CITE], actor="t")
    st.catalog.set_account(c, [], actor="t")
    assert _tool_names(rpc(st.app, ta, "tools/list")[1]) == list(META_TOOLS) + sorted([REGEX, REDACT, CITE])
    assert _tool_names(rpc(st.app, tb, "tools/list")[1]) == list(META_TOOLS) + [CITE]
    assert _tool_names(rpc(st.app, tc, "tools/list")[1]) == list(META_TOOLS)   # explicit empty stays empty
    assert call(st.app, tb, REGEX, REGEX_ARGS)[1]["error"]["message"] == "unknown tool"
    assert call(st.app, tc, CITE, {"source": {}})[1]["error"]["message"] == "unknown tool"


def test_unknown_unpublished_and_inactive_names_look_identical(st):
    b, tb = st.account("bob")
    st.catalog.set_account(b, [CITE], actor="t")
    errs = {json.dumps(call(st.app, tb, n, {})[1]["error"]) for n in (REGEX, "not_published__x", "zzz")}
    assert len(errs) == 1


# ===================================================================== K4 routing
def test_routing_claims_are_refused_and_ordinary_fields_are_not(st):
    _, t = st.account("alice")
    status, body = rpc(st.app, t, "tools/call", {"name": REGEX, "arguments": REGEX_ARGS,
                                                 "_meta": {"agentnode/capability": _cap("other")}})
    assert body["error"]["code"] == -32602 and "routing" in body["error"]["message"]
    # an export whose schema has fields named like routing fields is called normally
    ok = call(st.app, t, REGEX, dict(REGEX_ARGS, version="2", capability_id="x"))[1]
    assert ok["result"]["structuredContent"]["status"] == "approval_required"


# ===================================================================== K5 meta-tools
def test_meta_tools_listed_first_and_projection_is_safe(st):
    _, t = st.account("alice")
    names = _tool_names(rpc(st.app, t, "tools/list")[1])
    assert names[:3] == ["agentnode_search", "agentnode_describe", "agentnode_run"]
    res = call(st.app, t, "agentnode_describe", {"tool": REGEX})[1]["result"]["structuredContent"]
    assert set(res) == {"tool", "description", "input_schema", "capability_id", "version", "tree_digest_prefix",
                        "requested_permissions", "signer_role", "upstream_signature_status"}
    text = json.dumps(res)
    assert str(st.stand) not in text and "signature\"" not in text and "operator" not in text
    found = call(st.app, t, "agentnode_search", {"query": "redact"})[1]["result"]["structuredContent"]
    assert [r["tool"] for r in found["results"]] == [REDACT]


def test_direct_and_meta_run_share_one_path_and_hash(st):
    _, t = st.account("alice")
    direct = call(st.app, t, REGEX, REGEX_ARGS)[1]
    meta = call(st.app, t, "agentnode_run", {"tool": REGEX, "arguments": REGEX_ARGS})[1]
    assert hash_of(direct) == hash_of(meta)
    assert call(st.app, t, "agentnode_run", {"tool": "agentnode_run", "arguments": {}})[1]["result"]["isError"]


# ===================================================================== K6 account-bound approval
def test_full_flow_approval_result_and_replay(st):
    _, t = st.account("alice")
    first = call(st.app, t, REGEX, REGEX_ARGS)[1]["result"]
    assert first["structuredContent"]["status"] == "approval_required"
    assert "--audience %s" % _cap("regex-builder-pack") in first["structuredContent"]["next_step"]
    st.approve(first["structuredContent"]["invocation_hash"], _cap("regex-builder-pack"))
    ran = call(st.app, t, REGEX, REGEX_ARGS)[1]["result"]
    assert ran["isError"] is False and ran["structuredContent"]["call"]["structuredContent"]["count"] == 2
    assert ran["structuredContent"]["achieved_isolation"]["network"] == "none"
    again = call(st.app, t, REGEX, REGEX_ARGS)[1]["result"]
    assert again["structuredContent"]["status"] == "approval_required"     # single-use: replay refused
    assert st.worker.dispatched == 1


def test_foreign_account_cannot_use_an_approval(st):
    _, ta = st.account("alice")
    _, tb = st.account("bob")
    ha = hash_of(call(st.app, ta, REGEX, REGEX_ARGS)[1])
    hb = hash_of(call(st.app, tb, REGEX, REGEX_ARGS)[1])
    assert ha != hb                                   # the account is inside the invocation hash
    st.approve(ha, _cap("regex-builder-pack"))
    assert call(st.app, tb, REGEX, REGEX_ARGS)[1]["result"]["structuredContent"]["status"] == "approval_required"
    assert st.worker.dispatched == 0
    assert call(st.app, ta, REGEX, REGEX_ARGS)[1]["result"]["isError"] is False


def test_alias_arguments_and_version_redirection_need_a_new_approval(st):
    _, t = st.account("alice")
    h = hash_of(call(st.app, t, REDACT, {"text": "a@b.co"})[1])
    st.approve(h, _cap("document-redaction-pack"))
    assert call(st.app, t, REDACT, {"text": "other"})[1]["result"]["structuredContent"]["status"] == "approval_required"
    assert call(st.app, t, CITE, {"text": "a@b.co"})[1]["result"]["structuredContent"]["status"] == "approval_required"
    assert st.worker.dispatched == 0


def test_version_change_between_prepare_and_submit_is_refused(st, tmp_path):
    _, t = st.account("alice")
    h1 = hash_of(call(st.app, t, REGEX, REGEX_ARGS)[1])
    st.approve(h1, _cap("regex-builder-pack"))
    # the operator moves the tool to a new version before the approved call is repeated
    _regex_v2(st, tmp_path)
    st.catalog.publish(_cap("regex-builder-pack"), "1.0.1", "data_cleaning", actor="t")
    after = call(st.app, t, REGEX, REGEX_ARGS)[1]["result"]
    assert after["structuredContent"]["status"] == "approval_required"
    assert hash_of({"result": after}) != h1 and st.worker.dispatched == 0


def test_nonce_covers_every_binding_field(st):
    a, _ = st.account("alice")
    b, _ = st.account("bob")
    e = st.catalog.load().published[REGEX]
    base = st.adapter.nonce_for(a, e, REGEX_ARGS)
    assert base == st.adapter.nonce_for(a, e, dict(REGEX_ARGS))          # deterministic
    variants = {"account": st.adapter.nonce_for(b, e, REGEX_ARGS),
                "arguments": st.adapter.nonce_for(a, e, dict(REGEX_ARGS, operation="test"))}
    for f in ("tool", "capability_id", "version", "manifest_digest", "artifact_digest", "tree_digest", "export"):
        variants[f] = st.adapter.nonce_for(a, C.Entry(**dict(vars(e), **{f: getattr(e, f) + "x"})), REGEX_ARGS)
    assert [f for f, n in variants.items() if n == base] == []


def test_cursor_fields_are_each_bound(st):
    a, _ = st.account("alice")
    b, _ = st.account("bob")
    ad = st.adapter
    good = ad._cursor("search", a, 7, 50, "q1")
    assert ad._read_cursor(good, "search", a, 7, 200, "q1") == 50
    for args in (("tools/list", a, 7, 200, "q1"), ("search", b, 7, 200, "q1"), ("search", a, 8, 200, "q1"),
                 ("search", a, 7, 200, "q2"), ("search", a, 7, 50, "q1")):
        with pytest.raises(ValueError):
            ad._read_cursor(good, *args)
    forged = C.canonical({"v": 1, "op": "search", "acct": a, "gen": 7, "ps": 50, "off": 100, "q": "q1"})
    with pytest.raises(ValueError, match="not issued"):
        ad._read_cursor(_b64(forged) + "." + good.split(".")[1], "search", a, 7, 200, "q1")


def test_newer_indexed_version_does_not_replace_the_pinned_one(st, tmp_path):
    _, t = st.account("alice")
    st.approve(hash_of(call(st.app, t, REGEX, REGEX_ARGS)[1]), _cap("regex-builder-pack"))
    _regex_v2(st, tmp_path)                                           # indexed, NOT published
    out = call(st.app, t, REGEX, REGEX_ARGS)[1]["result"]
    assert out["isError"] is False and st.worker.dispatched == 1
    assert st.catalog.load().published[REGEX].version == "1.0.0"


def test_catalog_change_at_admission_is_refused_without_dispatch(st):
    _, t = st.account("alice")
    h = hash_of(call(st.app, t, REGEX, REGEX_ARGS)[1])
    st.approve(h, _cap("regex-builder-pack"))
    st.adapter._barrier = lambda name: name == "before_admission" and st.catalog.set_default([REDACT], actor="t")
    out = call(st.app, t, REGEX, REGEX_ARGS)[1]["result"]
    assert out["isError"] and "revoked_while_queued" in out["content"][0]["text"]
    assert st.worker.dispatched == 0
    assert not any((st.stand / "consumed").iterdir())                 # the approval was NOT consumed


def test_version_moved_at_admission_is_catalog_changed_without_dispatch(st, tmp_path):
    _, t = st.account("alice")
    st.approve(hash_of(call(st.app, t, REGEX, REGEX_ARGS)[1]), _cap("regex-builder-pack"))
    _regex_v2(st, tmp_path)

    def move(name):
        if name == "before_admission":
            st.catalog.publish(_cap("regex-builder-pack"), "1.0.1", "data_cleaning", actor="t")
    st.adapter._barrier = move
    out = call(st.app, t, REGEX, REGEX_ARGS)[1]["result"]
    assert out["isError"] and out["content"][0]["text"] == "refused: catalog_changed"
    assert st.worker.dispatched == 0 and not any((st.stand / "consumed").iterdir())


def test_self_approval_with_a_foreign_key_is_refused(st):
    from agentnode_sdk.capability.approval import grant
    _, t = st.account("alice")
    h = hash_of(call(st.app, t, REGEX, REGEX_ARGS)[1])
    me = Ed25519PrivateKey.generate()
    ap = grant(h, audience=_cap("regex-builder-pack"), human_actor="me", operator_private=me,
               operator_public_bytes=me.public_key().public_bytes_raw())
    (st.stand / "approvals" / f"{ap.approval_id}.json").write_text(json.dumps(ap.to_dict()))
    out = call(st.app, t, REGEX, REGEX_ARGS)[1]["result"]
    assert out["isError"] and "untrusted operator key" in out["content"][0]["text"]
    assert st.worker.dispatched == 0
    assert any(b.get("reason") == "approval" for b in _bodies(st))


def test_wrong_audience_approval_is_refused(st):
    _, t = st.account("alice")
    h = hash_of(call(st.app, t, REGEX, REGEX_ARGS)[1])
    st.approve(h, _cap("citation-manager-pack"))                      # approved for another capability
    out = call(st.app, t, REGEX, REGEX_ARGS)[1]["result"]
    assert out["isError"] and st.worker.dispatched == 0


# ===================================================================== K7 pagination + protocol
@pytest.fixture
def many(tmp_path):
    pack = tmp_path / "many-tool-pack"
    (pack / "src" / "many_tool_pack").mkdir(parents=True)
    (pack / "src" / "many_tool_pack" / "__init__.py").write_text("")
    defs = "".join(f"\n\ndef t{i:02d}(x: str = '') -> dict:\n    return {{'i': {i}}}\n" for i in range(60))
    (pack / "src" / "many_tool_pack" / "tool.py").write_text('"""many"""' + defs)
    tools = "".join(f"    - name: \"t{i:02d}\"\n      capability_id: \"testing\"\n"
                    f"      entrypoint: \"many_tool_pack.tool:t{i:02d}\"\n" for i in range(60))
    (pack / "agentnode.yaml").write_text(
        'manifest_version: "0.2"\npackage_id: "many-tool-pack"\npackage_type: "toolpack"\nname: "Many Tool Pack"\n'
        'publisher: "agentnode-test"\nversion: "1.0.0"\nsummary: "Sixty trivial tools for the pagination tests."\n'
        'entrypoint: "many_tool_pack.tool"\ncapabilities:\n  tools:\n' + tools)
    (pack / "pyproject.toml").write_text('[project]\nname = "many-tool-pack"\nversion = "1.0.0"\ndependencies = []\n')
    s = Stand(tmp_path / "s", packs=(), extra_packs=(pack,))
    for i in range(60):
        s.catalog.publish(f"{NS}/many-tool-pack", "1.0.0", f"t{i:02d}", actor="t")
    s.catalog.set_default([f"many_tool_pack__t{i:02d}" for i in range(60)], actor="t")
    return s


def test_tools_list_pages_at_50_with_meta_first_and_bound_cursors(many):
    a, t = many.account("alice")
    _, tb = many.account("bob")
    p1 = rpc(many.app, t, "tools/list")[1]["result"]
    assert len(p1["tools"]) == 50 and _tool_names({"result": p1})[:3] == list(META_TOOLS)
    assert p1["_meta"]["net.agentnode/catalogGeneration"] == many.catalog.load().generation
    assert p1["ttlMs"] == 0 and p1["cacheScope"] == "private"
    p2 = rpc(many.app, t, "tools/list", {"cursor": p1["nextCursor"]})[1]["result"]
    assert len(p2["tools"]) == 13 and "nextCursor" not in p2
    assert not set(_tool_names({"result": p2})) & set(META_TOOLS)
    # foreign account, tampered, wrong operation, stale generation, out of range: all -32602
    assert rpc(many.app, tb, "tools/list", {"cursor": p1["nextCursor"]})[1]["error"]["code"] == -32602
    tampered = p1["nextCursor"][:-2] + ("AA" if not p1["nextCursor"].endswith("AA") else "BB")
    assert rpc(many.app, t, "tools/list", {"cursor": tampered})[1]["error"]["code"] == -32602
    s = call(many.app, t, "agentnode_search", {"query": "", "cursor": p1["nextCursor"]})[1]
    assert s["error"]["code"] == -32602
    many.catalog.set_default([f"many_tool_pack__t{i:02d}" for i in range(59)], actor="t")
    assert rpc(many.app, t, "tools/list", {"cursor": p1["nextCursor"]})[1]["error"]["code"] == -32602
    assert rpc(many.app, t, "tools/list", {"cursor": "x.y"})[1]["error"]["code"] == -32602


def test_search_cursor_is_bound_to_the_query(many):
    _, t = many.account("alice")
    s1 = call(many.app, t, "agentnode_search", {"query": "many"})[1]["result"]["structuredContent"]
    assert len(s1["results"]) == 50
    # another query with just as many hits: only the query binding can refuse the cursor here
    other = call(many.app, t, "agentnode_search", {"query": "many_tool_pack", "cursor": s1["nextCursor"]})[1]
    assert other["error"]["code"] == -32602 and "query" in other["error"]["message"]
    s2 = call(many.app, t, "agentnode_search", {"query": "many", "cursor": s1["nextCursor"]})[1]
    assert len(s2["result"]["structuredContent"]["results"]) == 10


def test_legacy_is_filtered_and_paginated_without_modern_stamping(many):
    _, t = many.account("alice")
    p1 = rpc(many.app, t, "tools/list", legacy=True)[1]["result"]
    assert len(p1["tools"]) == 50 and "ttlMs" not in p1 and "nextCursor" in p1
    assert len(rpc(many.app, t, "tools/list", {"cursor": p1["nextCursor"]}, legacy=True)[1]["result"]["tools"]) == 13


def test_protocol_boundaries_stay(st):
    _, t = st.account("alice")
    status, body = rpc(st.app, t, "server/discover")
    assert body["result"]["capabilities"] == {"tools": {"listChanged": False}}
    assert rpc(st.app, t, "prompts/list")[1]["error"]["code"] == -32601
    assert rpc(st.app, t, "resources/list")[1]["error"]["code"] == -32601


# ===================================================================== K8 revocation linearisation
def test_revocation_takes_effect_on_the_next_request(st):
    _, t = st.account("alice")
    st.catalog.unpublish(REDACT, actor="t")
    assert REDACT not in _tool_names(rpc(st.app, t, "tools/list")[1])
    assert call(st.app, t, "agentnode_search", {"query": "redact"})[1]["result"]["structuredContent"]["results"] == []
    assert call(st.app, t, REDACT, {"text": "x"})[1]["error"]["message"] == "unknown tool"


def test_barrier_2_revoked_after_slot_before_admission_never_dispatches(st):
    _, t = st.account("alice")
    st.approve(hash_of(call(st.app, t, CITE, {"source": {"type": "book", "authors": ["A"], "title": "T", "year": 1}})[1]),
               _cap("citation-manager-pack"))
    st.adapter._barrier = lambda n: n == "before_admission" and st.catalog.unpublish(CITE, actor="t")
    out = call(st.app, t, CITE, {"source": {"type": "book", "authors": ["A"], "title": "T", "year": 1}})[1]["result"]
    assert "revoked_while_queued" in out["content"][0]["text"] and st.worker.dispatched == 0


def test_barrier_3_admitted_run_completes_while_revocation_hides_the_tool(st):
    _, t = st.account("alice")
    args = {"text": "mail a@b.co"}
    st.approve(hash_of(call(st.app, t, REDACT, args)[1]), _cap("document-redaction-pack"))
    st.adapter._barrier = lambda n: n == "after_admission" and st.catalog.unpublish(REDACT, actor="t")
    out = call(st.app, t, REDACT, args)[1]["result"]
    assert out["isError"] is False and "[REDACTED_EMAIL]" in out["content"][0]["text"]
    assert st.worker.dispatched == 1
    st.adapter._barrier = lambda n: None
    assert REDACT not in _tool_names(rpc(st.app, t, "tools/list")[1])
    assert call(st.app, t, REDACT, args)[1]["error"]["message"] == "unknown tool"
    kinds = [json.loads(line)["body"].get("decision") for line in
             (st.stand / "catalog-audit.jsonl").read_text().splitlines()]
    assert "admitted" in kinds and "ran" in kinds


def test_barrier_1_revoked_before_slot_acquisition_never_dispatches(tmp_path):
    worker = CountingWorker()
    s = Stand(tmp_path, worker=worker, limits=Limits(max_concurrent_calls=1, max_queue_wait_seconds=20))
    _publish_starters(s)
    s.catalog.set_default([REGEX, REDACT, CITE], actor="t")
    _, ta = s.account("alice")
    _, tb = s.account("bob")
    s.approve(hash_of(call(s.app, ta, REGEX, REGEX_ARGS)[1]), _cap("regex-builder-pack"))
    s.approve(hash_of(call(s.app, tb, REDACT, {"text": "x"})[1]), _cap("document-redaction-pack"))
    worker.gate = threading.Event()
    results = {}
    import asyncio
    import httpx

    async def both():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=s.app), base_url="http://%s" % HOST) as c:
            def req(token, name, args):
                return c.post("/mcp", headers={"content-type": "application/json",
                                               "accept": "application/json, text/event-stream",
                                               "mcp-protocol-version": "2026-07-28", "mcp-method": "tools/call",
                                               "mcp-name": name, "authorization": "Bearer %s" % token},
                              content=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                                  "params": {"name": name, "arguments": args, "_meta": META}}))
            first = asyncio.ensure_future(req(ta, REGEX, REGEX_ARGS))
            while not worker.started.is_set():
                await asyncio.sleep(0.02)
            second = asyncio.ensure_future(req(tb, REDACT, {"text": "x"}))   # queued behind the one slot
            await asyncio.sleep(0.3)
            s.catalog.unpublish(REDACT, actor="t")                         # revoked while queued
            worker.gate.set()
            results["first"] = (await first).json()
            results["second"] = (await second).json()
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(both())
    finally:
        loop.close()
    assert results["first"]["result"]["isError"] is False
    assert results["second"]["error"]["message"] == "unknown tool"
    assert worker.dispatched == 1


# ===================================================================== K9 limits
def test_more_tools_create_no_more_capacity(st):
    _, t = st.account("alice")
    outs = [call(st.app, t, n, a)[0] for n, a in ((REGEX, REGEX_ARGS), (REDACT, {"text": "x"}),
                                                  (CITE, {"source": {}}), ("agentnode_run",
                                                                           {"tool": REGEX, "arguments": REGEX_ARGS}))]
    assert outs[:3] == [200, 200, 200] and outs[3] == 429            # one account budget (burst 3)
    assert [b["tool"] for b in _bodies(st) if b.get("reason") == "rate_limited"] == ["agentnode_run"]


def test_search_and_describe_use_the_list_budget_not_the_call_budget(st):
    _, t = st.account("alice")
    for _ in range(5):
        assert call(st.app, t, "agentnode_describe", {"tool": REGEX})[0] == 200
    assert call(st.app, t, REGEX, REGEX_ARGS)[0] == 200


def test_queue_overload_is_429_across_accounts_and_tools(tmp_path):
    # one run slot, no queue: while alice's approved run holds the slot, bob's approved call of a
    # DIFFERENT tool is refused 429 overloaded and never reaches the worker
    worker = CountingWorker()
    s = Stand(tmp_path, worker=worker, limits=Limits(max_concurrent_calls=1, max_queued_calls=0))
    _publish_starters(s)
    s.catalog.set_default([REGEX, REDACT, CITE], actor="t")
    _, ta = s.account("alice")
    _, tb = s.account("bob")
    s.approve(hash_of(call(s.app, ta, REGEX, REGEX_ARGS)[1]), _cap("regex-builder-pack"))
    s.approve(hash_of(call(s.app, tb, REDACT, {"text": "x"})[1]), _cap("document-redaction-pack"))
    worker.gate = threading.Event()
    import asyncio
    import httpx
    out = {}

    async def both():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=s.app), base_url="http://%s" % HOST) as c:
            def req(token, name, args):
                return c.post("/mcp", headers={"content-type": "application/json",
                                               "accept": "application/json, text/event-stream",
                                               "mcp-protocol-version": "2026-07-28", "mcp-method": "tools/call",
                                               "mcp-name": name, "authorization": "Bearer %s" % token},
                              content=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                                  "params": {"name": name, "arguments": args, "_meta": META}}))
            first = asyncio.ensure_future(req(ta, REGEX, REGEX_ARGS))
            while not worker.started.is_set():
                await asyncio.sleep(0.02)
            second = await req(tb, REDACT, {"text": "x"})
            out["second"] = (second.status_code, second.json())
            worker.gate.set()
            out["first"] = (await first).json()
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(both())
    finally:
        loop.close()
    assert out["second"][0] == 429 and out["second"][1]["error"] == "overloaded"
    assert out["first"]["result"]["isError"] is False and worker.dispatched == 1
    refused = [b for b in _bodies(s) if b.get("reason") == "overloaded"]
    assert len(refused) == 1 and refused[0]["tool"] == REDACT and refused[0]["era"] == "modern"


def test_limit_refusal_that_cannot_be_audited_fails_closed(st, monkeypatch):
    _, t = st.account("alice")
    for _ in range(3):
        call(st.app, t, REGEX, REGEX_ARGS)
    monkeypatch.setattr(st.adapter.ledger, "_write_entry", _raise_oserror)
    status, body = call(st.app, t, REGEX, REGEX_ARGS)
    assert body["error"]["code"] == -32603 and body["error"]["message"] == "catalog audit unavailable"


# ===================================================================== K10 execution boundary
def test_no_worker_isolation_refuses_without_local_fallback(tmp_path):
    s = Stand(tmp_path, worker=CountingWorker(available=False))
    _publish_starters(s)
    s.catalog.set_default([REGEX], actor="t")
    _, t = s.account("alice")
    s.approve(hash_of(call(s.app, t, REGEX, REGEX_ARGS)[1]), _cap("regex-builder-pack"))
    out = call(s.app, t, REGEX, REGEX_ARGS)[1]["result"]
    assert out["isError"] and "ExecutionError" in out["content"][0]["text"] and s.worker.dispatched == 0


# ===================================================================== K11 audit
def test_every_decision_is_signed_chained_and_privacy_safe(st):
    a, t = st.account("alice")
    secret = "very-secret-argument-value"
    rpc(st.app, t, "tools/list")
    call(st.app, t, REDACT, {"text": secret})
    call(st.app, t, "unknown_x", {})
    n = st.ledger.verify()
    lines = (st.stand / "catalog-audit.jsonl").read_text()
    assert n >= 3 and secret not in lines
    bodies = [json.loads(x)["body"] for x in lines.splitlines()]
    mine = [b for b in bodies if b.get("account") == a]
    assert {b["decision"] for b in mine} >= {"listed", "approval_required", "refused"}
    assert all(b.get("token_id") for b in mine)
    assert any(b.get("kind") == "operator_change" for b in bodies)
    path = st.stand / "catalog-audit.jsonl"
    rows = path.read_text().splitlines()
    tampered = json.loads(rows[0])
    tampered["body"]["decision"] = "x"
    path.write_text("\n".join([json.dumps(tampered)] + rows[1:]) + "\n")
    with pytest.raises(AuditTampered):
        st.ledger.verify()


def test_audit_append_failure_fails_closed_before_dispatch(st, monkeypatch):
    _, t = st.account("alice")
    st.approve(hash_of(call(st.app, t, REGEX, REGEX_ARGS)[1]), _cap("regex-builder-pack"))

    def broken(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(st.adapter.ledger, "_write_entry", broken)
    status, body = call(st.app, t, REGEX, REGEX_ARGS)
    assert body["error"]["code"] == -32603 and st.worker.dispatched == 0


def test_terminal_append_failure_is_recoverable(st, monkeypatch):
    _, t = st.account("alice")
    st.approve(hash_of(call(st.app, t, REGEX, REGEX_ARGS)[1]), _cap("regex-builder-pack"))
    real = st.adapter.ledger._write_entry

    def fail_terminal(body):
        if body.get("kind") == "catalog_terminal":
            raise OSError("disk full")
        return real(body)
    monkeypatch.setattr(st.adapter.ledger, "_write_entry", fail_terminal)
    out = call(st.app, t, REGEX, REGEX_ARGS)[1]["result"]
    assert out["isError"] is False                                   # the run happened and is returned
    rec = list((st.stand / "catalog-audit.jsonl.recovery").glob("*.json"))
    assert len(rec) == 1
    monkeypatch.setattr(st.adapter.ledger, "_write_entry", real)
    rpc(st.app, t, "tools/list")                                     # next append re-ingests it
    assert not list((st.stand / "catalog-audit.jsonl.recovery").glob("*.json"))
    assert any(json.loads(x)["body"].get("recovered") for x in (st.stand / "catalog-audit.jsonl").read_text().splitlines())


def test_operator_cli_changes_are_signed_audited_and_refusals_exit_5(st, capsys):
    from agentnode_sdk.remote_mcp import catalog_cli
    stand = str(st.stand)
    g = st.catalog.load().generation
    assert catalog_cli.main(["--stand", stand, "--actor", "op", "unpublish", "--tool", CITE]) == 0
    assert json.loads(capsys.readouterr().out)["generation"] == g + 1
    assert catalog_cli.main(["--stand", stand, "unpublish", "--tool", CITE]) == 5
    assert json.loads(capsys.readouterr().out)["code"] == "unpublish.unknown"
    assert catalog_cli.main(["--stand", stand, "rollback", "--to-generation", str(g)]) == 0
    assert CITE in json.loads(capsys.readouterr().out)["default_active"]
    assert catalog_cli.main(["--stand", stand, "heads"]) == 0
    heads = json.loads(capsys.readouterr().out)
    assert heads["catalog"]["generation"] == g + 2 and heads["catalog_audit_entries_verified"] >= 2
    changes = [b for b in _bodies(st) if b.get("kind") == "operator_change"]
    assert [c["actor"] for c in changes[-2:]] == ["op", os.environ.get("USER") or os.environ.get("USERNAME")
                                                  or "operator"]
    (st.stand / "catalog" / "catalog.json").write_text("{}")
    assert catalog_cli.main(["--stand", stand, "show"]) == 6


class _FailingWorker(CountingWorker):
    def run(self, job):
        self.dispatched += 1
        raise RuntimeError("worker connection lost")


def test_worker_failure_gets_a_terminal_event(tmp_path):
    s = Stand(tmp_path, worker=_FailingWorker())
    _publish_starters(s)
    s.catalog.set_default([REGEX], actor="t")
    _, t = s.account("alice")
    s.approve(hash_of(call(s.app, t, REGEX, REGEX_ARGS)[1]), _cap("regex-builder-pack"))
    out = call(s.app, t, REGEX, REGEX_ARGS)[1]["result"]
    assert out["isError"]
    term = [b for b in _bodies(s) if b.get("kind") == "catalog_terminal"]
    assert len(term) == 1 and term[0]["decision"] in ("error", "ran")
    assert s.catalog.running() == []                                  # the admission token is released


def test_admitted_run_links_to_the_capability_ledger(st):
    _, t = st.account("alice")
    st.approve(hash_of(call(st.app, t, REGEX, REGEX_ARGS)[1]), _cap("regex-builder-pack"))
    ran = call(st.app, t, REGEX, REGEX_ARGS)[1]["result"]["structuredContent"]
    term = [json.loads(x)["body"] for x in (st.stand / "catalog-audit.jsonl").read_text().splitlines()
            if json.loads(x)["body"].get("decision") == "ran"][-1]
    assert term["capability_audit"] == {"ledger_id": "adapter-L1", "seq": ran["audit_seq"]}
    assert term["invocation_hash"] == ran["invocation_hash"]
