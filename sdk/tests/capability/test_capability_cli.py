"""The CLI resolves through the same one contract as the SDK and the adapter."""
from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

from agentnode_sdk.capability import cli, importer as I
from agentnode_sdk.capability.client import CapabilityClient
from agentnode_sdk.capability.index import LocalIndex
from agentnode_sdk.signing_key import generate_ed25519_keypair

FIXTURE = Path(__file__).parent / "fixtures" / "deterministic_add_server.py"


def _index(tmp_path):
    idx = LocalIndex(tmp_path / "index")
    priv, pub = generate_ed25519_keypair()
    imp = I.import_stdio_server(FIXTURE, publisher="acme", name="add-server", version="1.0.0",
                                private_key=priv, public_key_bytes=pub)
    idx.put_blob(imp.server_bytes)
    idx.register(imp.manifest)
    return idx


def _run(argv):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cli.main(argv)
    return rc, buf.getvalue()


def test_cli_search_describe(tmp_path):
    root = str(tmp_path / "index")
    _index(tmp_path)
    rc, out = _run(["--index", root, "search", "add"])
    assert rc == 0 and "acme/add-server" in out
    rc, out = _run(["--index", root, "describe", "acme/add-server"])
    d = json.loads(out)
    assert d["execution_model"] == "agentnode_executed_mcp"
    assert d["guarantees"]["kind"] == "required-not-asserted"


def test_cli_prepare_matches_the_sdk_contract(tmp_path):
    idx = _index(tmp_path)
    root = str(tmp_path / "index")
    rc, out = _run(["--index", root, "prepare", "acme/add-server", "add", "--args", '{"a": 5, "b": 7}'])
    cli_hash = json.loads(out)["invocation_hash"]
    # the SDK, using the same deterministic nonce, produces the same canonical invocation
    client = CapabilityClient(index=idx)
    nonce = client.deterministic_nonce("acme/add-server", "1.0.0", "add", {"a": 5, "b": 7})
    sdk_inv = client.prepare("acme/add-server", "add", {"a": 5, "b": 7}, nonce=nonce)
    assert cli_hash == sdk_inv.invocation_hash
