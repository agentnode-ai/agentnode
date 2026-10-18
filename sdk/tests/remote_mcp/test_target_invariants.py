"""Two-service isolation rules and the catalog invariants this arc must not move
(target-router T19/T20; counter-checks 41 and 44)."""
from __future__ import annotations

import hashlib

from agentnode_sdk.remote_mcp import catalog_adapter as CA
from agentnode_sdk.remote_mcp.catalog import canonical
from agentnode_sdk.worker.service_isolation import violations

MANAGED = {"user": "root-managed", "uid": 994, "identity_uri": "agentnode://d/worker/w20", "unit": "rmcp-worker",
           "listen": "10.0.0.2:8443", "state_dir": "/var/lib/agentnode-worker", "tls_dir": "/var/lib/agentnode-worker/wtls",
           "keyring": "/var/lib/agentnode-worker/wtls/pair-keys.json", "floor_dir": "/var/lib/agentnode-worker/wfloor",
           "journal_dir": "/var/lib/agentnode-worker/journal", "runtime_dir": "/run/user/994",
           "trust_dir": "/var/lib/agentnode-worker/wtls"}
CUSTOMER = {"user": "agentnode-cw-c1", "uid": 993, "identity_uri": "agentnode://d/worker/c1", "unit": "agentnode-cw-c1",
            "listen": "10.0.0.2:8444", "state_dir": "/var/lib/agentnode-cw-c1", "tls_dir": "/var/lib/agentnode-cw-c1/tls",
            "keyring": "/var/lib/agentnode-cw-c1/tls/pair-keys.json", "floor_dir": "/var/lib/agentnode-cw-floor/c1",
            "journal_dir": "/var/lib/agentnode-cw-c1/journal", "runtime_dir": "/run/user/993",
            "trust_dir": "/var/lib/agentnode-cw-c1/tls"}


def test_two_service_state_isolation():
    assert violations(MANAGED, CUSTOMER) == []
    shared_keyring = dict(CUSTOMER, keyring=MANAGED["keyring"])
    assert any("keyring" in v for v in violations(MANAGED, shared_keyring))
    shared_state = dict(CUSTOMER, journal_dir="/var/lib/agentnode-worker/journal-c1")
    assert any("journal_dir" in v for v in violations(MANAGED, shared_state))
    same_user = dict(CUSTOMER, user=MANAGED["user"], uid=MANAGED["uid"])
    assert any("user" in v for v in violations(MANAGED, same_user))
    same_port = dict(CUSTOMER, listen=MANAGED["listen"])
    assert any("listen" in v for v in violations(MANAGED, same_port))
    as_root = dict(CUSTOMER, uid=0)
    assert any("root" in v for v in violations(MANAGED, as_root))


def test_parent_head_catalog_invariants():
    """Without targets, the catalog behaves exactly as at the parent: the nonce formula, the
    agentnode_run schema and the reserved routing namespace are unchanged."""
    body = {"account": "acct-0000000000000001", "tool": "t__e", "capability_id": "ns/t", "version": "1",
            "manifest_digest": "m", "artifact_digest": "a", "tree_digest": "sha256:t", "export": "e",
            "arguments": {"x": 1}}
    golden = hashlib.sha256(b"agentnode.catalog.v1/nonce\n" + canonical(body)).hexdigest()

    class E:
        tool, capability_id, version, manifest_digest = "t__e", "ns/t", "1", "m"
        artifact_digest, tree_digest, export = "a", "sha256:t", "e"
    assert CA.CatalogMCPAdapter.nonce_for(None, "acct-0000000000000001", E, {"x": 1}) == golden
    assert set(CA.META_SCHEMAS["agentnode_run"]["inputSchema"]["properties"]) == {"tool", "arguments"}
    assert CA.RESERVED_META_PREFIX == "agentnode/"
    assert CA.APPROVAL_COMMAND.count("--audience {audience}") == 1
