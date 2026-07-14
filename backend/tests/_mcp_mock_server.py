"""A tiny stdio MCP server for the 2c-6 handshake integration tests.

Reads newline-delimited JSON-RPC from stdin and replies on stdout, per a mode
(argv[1] or MOCK_MODE). Modes model the behaviors the deterministic handshake
must handle — especially `exit_after_init`, which reproduces the production
race: the server exits right after answering initialize, before tools/list.
This is a REAL interactive subprocess (not a mock object), so the tests exercise
the real Popen + threaded reader + handshake state machine.
"""

import json
import os
import sys
import time


def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("MOCK_MODE", "normal")

    if mode == "crash":
        sys.exit(1)  # exit immediately, no output -> startup_crash

    if mode == "flood":
        # G3: spew many newline-delimited lines before answering init -> the bounded
        # reader must hit the byte cap and return _EXCESSIVE_OUTPUT.
        line = "x" * 500 + "\n"
        for _ in range(5000):
            sys.stdout.write(line)
        sys.stdout.flush()
        return

    if mode == "giant_line":
        # G3: one huge line WITHOUT a newline -> the byte cap must trip before any
        # newline (a mere line-based/bounded-queue reader would blow memory here).
        sys.stdout.write("y" * 2_000_000)
        sys.stdout.flush()
        return

    if mode == "noise":
        # log noise + a notification BEFORE any response (must be skipped)
        sys.stdout.write("mock server starting (not json)\n")
        send(
            {
                "jsonrpc": "2.0",
                "method": "notifications/message",
                "params": {"level": "info"},
            }
        )

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
            if mode == "init_error":
                send(
                    {
                        "jsonrpc": "2.0",
                        "id": mid,
                        "error": {"code": -32603, "message": "init refused"},
                    }
                )
                return
            if mode == "hang_init":
                time.sleep(30)  # never answer within the step timeout
                return
            send(
                {
                    "jsonrpc": "2.0",
                    "id": mid,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "serverInfo": {"name": "mock", "version": "1"},
                        "capabilities": {"tools": {}},
                    },
                }
            )
            if mode == "exit_after_init":
                return  # THE RACE: exit before answering tools/list

        elif method == "notifications/initialized":
            continue

        elif method == "tools/list":
            if mode == "tools_error":
                send(
                    {
                        "jsonrpc": "2.0",
                        "id": mid,
                        "error": {"code": -32601, "message": "no tools"},
                    }
                )
                return
            send(
                {
                    "jsonrpc": "2.0",
                    "id": mid,
                    "result": {"tools": [{"name": "a"}, {"name": "b"}]},
                }
            )
            # keep running until stdin EOF (a well-behaved server)


if __name__ == "__main__":
    main()
