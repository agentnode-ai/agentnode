#!/usr/bin/env python3
"""A minimal, self-contained stdio-MCP client exchange (stdlib only).

One file, two uses:

* imported (``run_exchange``) at BUILD time, so the importer can ask a server
  what tools it exposes (``tools/list``) without the author declaring them;
* shipped verbatim into the sandbox artifact and run as ``python3 handshake.py``
  INSIDE the worker's container at EXECUTION time, so the MCP server runs on the
  worker (not as a local capability subprocess). It reads a job spec on stdin and
  prints exactly one JSON result line on stdout.

Stdlib only on purpose: the sandbox image need not carry AgentNode. A background
reader thread drains stdout so a chatty or slow server cannot deadlock the pipe,
and every response is matched by JSON-RPC id with a deadline.
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from queue import Empty, Queue
from typing import Any

PROTOCOL = "2024-11-05"
_MAX_LINE = 1_000_000  # a single line larger than this is treated as misbehaviour


class HandshakeError(Exception):
    pass


def _reader(stream, q: "Queue[str | None]") -> None:
    try:
        for line in stream:
            q.put(line[:_MAX_LINE])
    finally:
        q.put(None)  # EOF sentinel


def _await_id(q: "Queue[str | None]", want_id: int, deadline: float) -> dict[str, Any]:
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise HandshakeError(f"timed out waiting for response id={want_id}")
        try:
            line = q.get(timeout=min(remaining, 0.5))
        except Empty:
            continue
        if line is None:
            raise HandshakeError(f"server closed stdout before response id={want_id}")
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            continue  # skip log noise / non-JSON
        if isinstance(msg, dict) and msg.get("id") == want_id:
            return msg


def run_exchange(
    server_command: list[str],
    *,
    cwd: str | None = None,
    want_tools: bool = False,
    call: dict[str, Any] | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Spawn *server_command*, do the MCP handshake, optionally list tools / call one.

    Returns ``{"protocol", "server_info", "tools"?, "call"?}``. Raises
    ``HandshakeError`` on any protocol failure. Always reaps the subprocess.
    """
    proc = subprocess.Popen(
        server_command, cwd=cwd,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1,
    )
    q: "Queue[str | None]" = Queue()
    t = threading.Thread(target=_reader, args=(proc.stdout, q), daemon=True)
    t.start()
    deadline = time.monotonic() + timeout

    def send(obj: dict[str, Any]) -> None:
        assert proc.stdin is not None
        proc.stdin.write(json.dumps(obj) + "\n")
        proc.stdin.flush()

    out: dict[str, Any] = {}
    try:
        send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
              "params": {"protocolVersion": PROTOCOL, "clientInfo": {"name": "agentnode-capability", "version": "1"},
                         "capabilities": {}}})
        init = _await_id(q, 1, deadline)
        if "error" in init:
            raise HandshakeError(f"initialize failed: {init['error']}")
        result = init.get("result") or {}
        out["protocol"] = result.get("protocolVersion")
        out["server_info"] = result.get("serverInfo")
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})

        if want_tools:
            send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            tl = _await_id(q, 2, deadline)
            if "error" in tl:
                raise HandshakeError(f"tools/list failed: {tl['error']}")
            out["tools"] = (tl.get("result") or {}).get("tools", [])

        if call is not None:
            send({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                  "params": {"name": call["name"], "arguments": call.get("arguments", {})}})
            cr = _await_id(q, 3, deadline)
            if "error" in cr:
                raise HandshakeError(f"tools/call failed: {cr['error']}")
            out["call"] = cr.get("result")
        return out
    finally:
        try:
            if proc.stdin and not proc.stdin.closed:
                proc.stdin.close()
        except Exception:
            pass
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
            proc.wait()


def main() -> int:
    """In-container entry: read {server_command, tool, arguments} on stdin, print one JSON line."""
    try:
        spec = json.loads(sys.stdin.read() or "{}")
        server_command = spec["server_command"]
        tool = spec["tool"]
        arguments = spec.get("arguments", {})
        res = run_exchange(server_command, want_tools=bool(spec.get("want_tools")),
                           call={"name": tool, "arguments": arguments},
                           timeout=float(spec.get("timeout", 30.0)))
        print(json.dumps({"ok": True, "protocol": res.get("protocol"),
                          "server_info": res.get("server_info"),
                          "tools": res.get("tools"), "call": res.get("call")}))
        return 0
    except Exception as exc:  # noqa: BLE001 -- any failure is a single structured error line
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
