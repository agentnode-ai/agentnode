"""Import a REAL existing multi-file stdio MCP server WITHOUT rewriting it (Slice 2, S4/U2).

Slice 1's ``importer.import_stdio_server`` handles a single-file stdlib server. A real
upstream server (an npm package + its resolved dependency closure, or any multi-file
package) is imported here, by mechanism (b): the complete upstream tree is packed
deterministically (``tree_pack``) and pinned by a content tree-digest; the manifest
REFERENCES it by a pinned run command + the packed artifact's sha256 + the tree-digest.
The upstream files are delivered byte-unchanged -- they are packaged, never
source-transplanted (the AST converter ``backend/app/import_/converters/mcp.py`` is NOT
used), and the manifest is signed as the ``importing_publisher``, never as upstream.

The server is still asked what it exposes over a real stdio handshake (``tools/list``);
its tools are not declared by hand. Discovery runs the real entry with its interpreter
(``node`` for npm, ``python3`` for a pure-python package) at BUILD time only -- this is not
the capability execution path.
"""
from __future__ import annotations

import hashlib
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agentnode_sdk.capability import _handshake, tree_pack
from agentnode_sdk.capability import manifest as M


@dataclass(frozen=True)
class ImportedTreeCapability:
    manifest: dict[str, Any]
    tar_bytes: bytes
    tar_sha256: str
    tree_digest: str
    entry: str
    interpreter: str


def import_vendored_tree(
    tree_dir: str | os.PathLike[str],
    *,
    publisher: str,
    name: str,
    version: str,
    entry: str,
    interpreter: str,
    private_key: Ed25519PrivateKey,
    public_key_bytes: bytes,
    registry: dict[str, Any],
    discovered_tools: list[dict[str, Any]] | None = None,
    inspect_with: str | None = None,
    timeout: float = 90.0,
    sign_as: str = "importing_publisher",
) -> ImportedTreeCapability:
    """Produce a signed capability manifest referencing the vendored server tree at *tree_dir*.

    *entry* is the server's entry module RELATIVE to *tree_dir* (e.g.
    ``lib/node_modules/@scope/pkg/dist/index.js``); *interpreter* is ``node`` or
    ``python`` / ``python3``. *registry* records the pinned upstream coordinates
    (source, publisher, license, package, version, integrity) for provenance -- it is
    carried verbatim into ``source.package`` and is part of the signed payload.

    ``discovered_tools`` lets a caller inject the tools (so a unit test need not spawn the
    real interpreter); when None the tools are read from the real server over a build-time
    ``tools/list`` handshake using ``inspect_with`` (default: the interpreter named).
    """
    root = Path(tree_dir)
    entry_abs = root / entry
    if not entry_abs.is_file():
        raise ValueError(f"entry not found in tree: {entry!r}")

    tar_bytes = tree_pack.pack_tree(root)
    tar_sha256 = hashlib.sha256(tar_bytes).hexdigest()
    tree_digest = tree_pack.tree_digest_of_dir(root)

    if discovered_tools is None:
        inspector = inspect_with or (sys.executable if interpreter.startswith("python") else interpreter)
        # -B for a python inspector so build-time discovery never writes __pycache__ into the
        # tree (that would mutate what we just packed and pinned).
        cmd = [inspector] + (["-B"] if os.path.basename(inspector).startswith("python") else []) + [str(entry_abs)]
        discovered = _handshake.run_exchange(cmd, want_tools=True, timeout=timeout)
        discovered_tools = discovered.get("tools") or []
    if not discovered_tools:
        raise ValueError("imported server reported no tools")

    tools: list[dict[str, Any]] = []
    for t in discovered_tools:
        tool = {
            "name": t.get("name"),
            "description": t.get("description", ""),
            "input_schema": t.get("inputSchema") or t.get("input_schema") or {},
            "output_schema": t.get("outputSchema") or t.get("output_schema") or {},
        }
        tool["tool_schema_digest"] = M.tool_schema_digest(tool)
        tools.append(tool)

    # The pinned reference: the command run INSIDE the sandbox (interpreter + entry,
    # relative to the extracted tree root) + the packed-artifact sha256 + the content
    # tree-digest that binds EVERY delivered file.
    source = {
        "transport": "stdio",
        "command": [interpreter, entry],
        "package": {
            "kind": "vendored-tree",
            "entry": entry,
            "interpreter": interpreter,
            "sha256": tar_sha256,       # sha256 of the deterministic packed tar (the stored blob)
            "tree_digest": tree_digest,  # content digest binding every delivered file
            "registry": registry,
        },
    }

    man: dict[str, Any] = {
        "schema": M.CAPABILITY_SCHEMA,
        "capability_id": M.capability_id(publisher, name),
        "capability_version": version,
        "publisher": publisher,
        "execution_model": "agentnode_executed_mcp",
        "source": source,
        "permissions": {
            "network": {"allowed_domains": []},   # credential-free, no egress
            "env_keys": [],                         # no credentials
            "code_execution": True,
            "user_approval": "required",
        },
        "tools": tools,
        "artifact_digest": M.artifact_digest_for_stdio(source),
        "schema_digest": M.schema_digest(tools),
        "signer_role": sign_as,
    }
    signed = M.sign_manifest(man, private_key, public_key_bytes, role=sign_as)
    return ImportedTreeCapability(signed, tar_bytes, tar_sha256, tree_digest, entry, interpreter)
