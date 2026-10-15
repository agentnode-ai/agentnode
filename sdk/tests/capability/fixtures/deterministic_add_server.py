#!/usr/bin/env python3
"""A tiny, self-contained, credential-free stdio MCP server (the server we IMPORT).

It is an ordinary standalone program -- stdlib only, no AgentNode import, no
network, no credentials -- that speaks newline-delimited JSON-RPC 2.0 per the
MCP 2024-11-05 handshake and exposes one deterministic tool, ``add`` (sum of two
integers). The capability layer imports it BY REFERENCE (a pinned command + a
content digest) and never rewrites it; this file stands in for any existing
third-party stdio MCP server.

Handshake: initialize -> (notifications/initialized) -> tools/list -> tools/call.
It runs until stdin EOF, like a well-behaved server.
"""
import json
import sys

PROTOCOL = "2024-11-05"

TOOLS = [
    {
        "name": "add",
        "description": "Add two integers and return the sum.",
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


def _ok(mid, result):
    _send({"jsonrpc": "2.0", "id": mid, "result": result})


def _err(mid, code, message):
    _send({"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}})


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
            _ok(mid, {"protocolVersion": PROTOCOL,
                      "serverInfo": {"name": "deterministic-add", "version": "1.0.0"},
                      "capabilities": {"tools": {}}})
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            _ok(mid, {"tools": TOOLS})
        elif method == "tools/call":
            params = msg.get("params") or {}
            name = params.get("name")
            args = params.get("arguments") or {}
            if name != "add":
                _err(mid, -32601, f"unknown tool: {name!r}")
                continue
            a, b = args.get("a"), args.get("b")
            if not isinstance(a, int) or not isinstance(b, int) or isinstance(a, bool) or isinstance(b, bool):
                _err(mid, -32602, "add requires two integers a and b")
                continue
            total = a + b
            _ok(mid, {"content": [{"type": "text", "text": str(total)}],
                      "structuredContent": {"sum": total}, "isError": False})
        elif method is not None and mid is not None:
            _err(mid, -32601, f"method not found: {method}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
