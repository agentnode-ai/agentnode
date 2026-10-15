"""Import an existing stdio MCP server WITHOUT rewriting it (S4).

The server is treated as a black box: the importer reads its bytes (for a content
digest) and ASKS it, over a real handshake, what tools it exposes (``tools/list``)
-- it does not parse or transform the server's source. The AST source-transplant
converter (``backend/app/import_/converters/mcp.py``) is deliberately not used.

The result is a signed ``agentnode.capability.v1`` manifest that REFERENCES the
server by a pinned command and a content digest. It is signed as the
``importing_publisher`` -- never as the upstream publisher; the wrapper is not
represented as upstream-signed.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agentnode_sdk.capability import _handshake, manifest as M


@dataclass(frozen=True)
class ImportedCapability:
    manifest: dict[str, Any]
    server_bytes: bytes
    server_sha256: str
    entrypoint: str


def import_stdio_server(
    server_path: str | os.PathLike[str],
    *,
    publisher: str,
    name: str,
    version: str,
    private_key: Ed25519PrivateKey,
    public_key_bytes: bytes,
    runtime_python: str = "python3",
    inspect_with: str | None = None,
    sign_as: str = "importing_publisher",
    timeout: float = 30.0,
) -> ImportedCapability:
    """Produce a signed capability manifest referencing the stdio server at *server_path*.

    Spawns the server locally only to read its ``tools/list`` (build-time
    inspection, not the capability execution path). Does not modify the server.
    ``runtime_python`` is the interpreter the sealed manifest records for the
    sandbox (default ``python3``); ``inspect_with`` is the local interpreter used
    only for the build-time ``tools/list`` (default: the running interpreter).
    """
    import sys as _sys

    path = Path(server_path)
    server_bytes = path.read_bytes()
    server_sha256 = hashlib.sha256(server_bytes).hexdigest()
    entrypoint = path.name

    # Ask the server what it exposes -- do not declare it by hand.
    discovered = _handshake.run_exchange([inspect_with or _sys.executable, str(path)],
                                         want_tools=True, timeout=timeout)
    tools_reported = discovered.get("tools") or []
    if not tools_reported:
        raise ValueError("imported server reported no tools")

    tools: list[dict[str, Any]] = []
    for t in tools_reported:
        tool = {
            "name": t.get("name"),
            "description": t.get("description", ""),
            "input_schema": t.get("inputSchema") or t.get("input_schema") or {},
            "output_schema": t.get("outputSchema") or t.get("output_schema") or {},
        }
        tool["tool_schema_digest"] = M.tool_schema_digest(tool)
        tools.append(tool)

    # The pinned reference: the command run INSIDE the sandbox (relative to the
    # sealed artifact root) + the content digest that binds the exact server bytes.
    source = {
        "transport": "stdio",
        "command": [runtime_python, entrypoint],
        "package": {"kind": "local-pinned", "entrypoint": entrypoint, "sha256": server_sha256},
    }

    man: dict[str, Any] = {
        "schema": M.CAPABILITY_SCHEMA,
        "capability_id": M.capability_id(publisher, name),
        "capability_version": version,
        "publisher": publisher,
        "execution_model": "agentnode_executed_mcp",
        "source": source,
        "permissions": {
            "network": {"allowed_domains": []},   # this credential-free tool needs no egress
            "env_keys": [],                         # no credentials
            "code_execution": True,
            "user_approval": "required",
        },
        "tools": tools,
        "artifact_digest": M.artifact_digest_for_stdio(source),
        "schema_digest": M.schema_digest(),
        "signer_role": sign_as,
    }
    signed = M.sign_manifest(man, private_key, public_key_bytes, role=sign_as)
    return ImportedCapability(signed, server_bytes, server_sha256, entrypoint)
