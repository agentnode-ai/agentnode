#!/usr/bin/env python3
"""A tiny, self-contained, MULTI-FILE, credential-free stdio MCP server (a test stand-in).

Stdlib only, no AgentNode import, no network, no credentials. It speaks the MCP 2024-11-05
handshake over newline-delimited JSON-RPC 2.0 and exposes one deterministic tool, ``echo_sum``.
The computation lives in a SIBLING module (``toolimpl``) so a run proves the whole tree was
delivered to the sandbox, not just the entry file. It stands in for any real multi-file
stdio MCP server (e.g. an npm package) in podman-free unit tests.
"""
import json
import sys

import toolimpl  # sibling module in the same (extracted) directory

PROTOCOL = "2024-11-05"

TOOLS = [
    {
        "name": "echo_sum",
        "description": "Add two integers and return the sum (computed in a sibling module).",
        "inputSchema": {
            "type": "object",
            "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
            "required": ["a", "b"],
            "additionalProperties": False,
        },
    }
]


def _send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def main() -> int:
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            continue
        mid = msg.get("id")
        method = msg.get("method")
        if method == "initialize":
            _send({"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": PROTOCOL,
                "serverInfo": {"name": "multifile-echo", "version": "1.0.0"},
                "capabilities": {"tools": {}}}})
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            _send({"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}})
        elif method == "tools/call":
            params = msg.get("params") or {}
            if params.get("name") != "echo_sum":
                _send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601,
                       "message": f"unknown tool: {params.get('name')!r}"}})
                continue
            args = params.get("arguments") or {}
            try:
                total = toolimpl.compute_sum(args.get("a"), args.get("b"))
            except ValueError as exc:
                _send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": str(exc)}})
                continue
            _send({"jsonrpc": "2.0", "id": mid, "result": {
                "content": [{"type": "text", "text": str(total)}],
                "structuredContent": {"sum": total}, "isError": False}})
        elif method is not None and mid is not None:
            _send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601,
                   "message": f"method not found: {method}"}})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
