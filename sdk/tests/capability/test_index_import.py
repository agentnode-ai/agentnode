"""S2/S3/S4: local index (register/search/describe), honest describe, import without rewrite."""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

from agentnode_sdk.capability import importer as I
from agentnode_sdk.capability import manifest as M
from agentnode_sdk.capability import _handshake
from agentnode_sdk.capability.index import IndexError_, LocalIndex
from agentnode_sdk.signing_key import generate_ed25519_keypair

FIXTURE = Path(__file__).parent / "fixtures" / "deterministic_add_server.py"


def _import():
    priv, pub = generate_ed25519_keypair()
    imp = I.import_stdio_server(FIXTURE, publisher="acme", name="add-server", version="1.0.0",
                                private_key=priv, public_key_bytes=pub)
    return imp


def test_import_without_rewrite_builds_a_signed_referencing_manifest():
    imp = _import()
    man = imp.manifest
    assert M.validate_manifest(man) == []
    assert M.verify_manifest(man).ok
    # tool discovered by asking the server, not declared by us
    names = [t["name"] for t in man["tools"]]
    assert names == ["add"]
    assert man["tools"][0]["input_schema"]["required"] == ["a", "b"]
    # references the server by pinned command + content digest; does not re-host its source
    assert man["source"]["command"] == ["python3", "deterministic_add_server.py"]
    assert man["source"]["package"]["sha256"] == imp.server_sha256
    # signed as importing_publisher, NOT upstream
    assert man["signatures"][0]["role"] == "importing_publisher"


def test_register_search_list_describe(tmp_path):
    idx = LocalIndex(tmp_path / "index")
    imp = _import()
    idx.put_blob(imp.server_bytes)
    resolved = idx.register(imp.manifest)
    assert resolved.capability_id == "acme/add-server"

    assert idx.search("add") and idx.search("add")[0]["capability_id"] == "acme/add-server"
    assert {"capability_id": "acme/add-server", "capability_version": "1.0.0",
            "execution_model": "agentnode_executed_mcp"} in idx.list()

    d = idx.describe("acme/add-server")
    assert d["execution_model"] == "agentnode_executed_mcp"
    assert d["provenance"]["signer_roles"] == ["importing_publisher"]
    assert d["provenance"]["upstream_signed"] is False
    assert d["requested_permissions"]["user_approval"] == "required"
    # honesty: guarantees are required-not-asserted; the worker target's isolation is computed at run time
    assert d["guarantees"]["kind"] == "required-not-asserted"
    assert d["targets"][0]["isolation"] == "computed_at_run_time_from_worker_measurement"
    assert "shares a kernel" in d["targets"][0]["caveat"]


def test_index_refuses_an_unsigned_manifest(tmp_path):
    idx = LocalIndex(tmp_path / "index")
    unsigned = _import().manifest
    unsigned.pop("signatures", None)
    with pytest.raises(IndexError_):
        idx.register(unsigned)


def test_version_is_immutable_in_the_index(tmp_path):
    idx = LocalIndex(tmp_path / "index")
    imp = _import()
    idx.register(imp.manifest)
    # re-registering the identical manifest is idempotent
    idx.register(copy.deepcopy(imp.manifest))
    # a changed manifest under the SAME version is refused
    changed = copy.deepcopy(imp.manifest)
    changed["tools"].append({"name": "sneaky", "description": "", "input_schema": {},
                             "output_schema": {}})
    changed["tools"][-1]["tool_schema_digest"] = M.tool_schema_digest(changed["tools"][-1])
    # re-sign so it is a VALID but DIFFERENT manifest for the same version
    priv, pub = generate_ed25519_keypair()
    changed.pop("signatures")
    changed = M.sign_manifest(changed, priv, pub, role="importing_publisher")
    with pytest.raises(IndexError_) as exc:
        idx.register(changed)
    assert "new version" in str(exc.value)


def test_blob_store_roundtrip_and_corruption_detected(tmp_path):
    idx = LocalIndex(tmp_path / "index")
    digest = idx.put_blob(b"hello-server-bytes")
    assert idx.get_blob(digest) == b"hello-server-bytes"
    # corrupt the stored blob -> get_blob refuses
    (tmp_path / "index" / "blobs" / digest).write_bytes(b"tampered")
    with pytest.raises(IndexError_):
        idx.get_blob(digest)


def test_handshake_runner_actually_calls_the_tool():
    """Unit test of the shared runner against the fixture server (NOT the capability exec path)."""
    res = _handshake.run_exchange([sys.executable, str(FIXTURE)], want_tools=True,
                                  call={"name": "add", "arguments": {"a": 2, "b": 40}})
    assert res["protocol"] == "2024-11-05"
    assert [t["name"] for t in res["tools"]] == ["add"]
    assert res["call"]["structuredContent"]["sum"] == 42
    assert res["call"]["content"][0]["text"] == "42"
