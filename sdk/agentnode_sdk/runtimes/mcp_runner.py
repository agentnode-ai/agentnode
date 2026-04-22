"""MCP server process pool and tool execution.

Manages MCP server subprocesses via stdio JSON-RPC 2.0 protocol.
Each MCP package gets its own persistent server process, reused across calls.
"""
from __future__ import annotations

import atexit
import itertools
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from agentnode_sdk.models import RunToolResult

_request_id = itertools.count(1)


class MCPServerProcess:
    """A managed MCP server subprocess communicating via stdio JSON-RPC."""

    def __init__(self, slug: str, command: list[str]):
        self.slug = slug
        self.command = command
        self._process: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._last_used = time.monotonic()

    def start(self, timeout: float = 10.0) -> None:
        """Start the MCP server subprocess."""
        env = _mcp_env()

        # Windows: CREATE_NEW_PROCESS_GROUP for clean shutdown
        kwargs: dict[str, Any] = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

        self._process = subprocess.Popen(
            self.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            text=True,
            **kwargs,
        )

        # Send initialize request
        init_req = {
            "jsonrpc": "2.0",
            "id": next(_request_id),
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "agentnode-sdk", "version": "0.4.0"},
            },
        }
        self._send(init_req)
        resp = self._recv(timeout=timeout)
        if not resp or "error" in resp:
            self.stop()
            raise RuntimeError(f"MCP initialize failed: {resp}")

        # Send initialized notification
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def call_tool(self, name: str, args: dict, timeout: float = 30.0) -> Any:
        """Call a tool on the MCP server."""
        with self._lock:
            self._last_used = time.monotonic()
            req = {
                "jsonrpc": "2.0",
                "id": next(_request_id),
                "method": "tools/call",
                "params": {"name": name, "arguments": args},
            }
            self._send(req)
            resp = self._recv(timeout=timeout)

        if not resp:
            raise RuntimeError("No response from MCP server")
        if "error" in resp:
            raise RuntimeError(f"MCP error: {resp['error']}")
        return resp.get("result")

    def stop(self, timeout: float = 5.0) -> None:
        """Gracefully stop the server, kill if needed."""
        if not self._process or self._process.poll() is not None:
            return
        try:
            self._process.stdin.close()
            self._process.wait(timeout=timeout)
        except (subprocess.TimeoutExpired, OSError):
            self._process.kill()
            try:
                self._process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass

    def health_check(self) -> bool:
        """Check if the server process is still alive."""
        return self._process is not None and self._process.poll() is None

    def _send(self, msg: dict) -> None:
        """Send a JSON-RPC message via stdin."""
        if not self._process or not self._process.stdin:
            raise RuntimeError("MCP server not running")
        line = json.dumps(msg) + "\n"
        self._process.stdin.write(line)
        self._process.stdin.flush()

    def _recv(self, timeout: float = 30.0) -> dict | None:
        """Read a JSON-RPC response from stdout with timeout."""
        if not self._process or not self._process.stdout:
            return None
        result: list[dict | None] = [None]
        error: list[Exception | None] = [None]

        def reader() -> None:
            try:
                line = self._process.stdout.readline()
                if line:
                    result[0] = json.loads(line)
            except Exception as e:
                error[0] = e

        t = threading.Thread(target=reader, daemon=True)
        t.start()
        t.join(timeout)
        if t.is_alive():
            # Check if the process died — if so, clean up to prevent resource leak
            if self._process and self._process.poll() is not None:
                self.stop()
            raise TimeoutError(f"MCP read timeout after {timeout}s")
        if error[0]:
            raise error[0]
        return result[0]


class MCPProcessPool:
    """Pool of MCP server processes, one per package slug."""

    IDLE_TIMEOUT = 300  # 5 minutes

    def __init__(self) -> None:
        self._servers: dict[str, MCPServerProcess] = {}
        self._lock = threading.Lock()
        atexit.register(self.stop_all)

    def get_or_start(self, slug: str, command: list[str], timeout: float = 10.0) -> MCPServerProcess:
        """Get an existing server or start a new one."""
        with self._lock:
            server = self._servers.get(slug)
            if server and server.health_check():
                return server

            # Clean up dead server
            if server:
                server.stop()

            server = MCPServerProcess(slug, command)
            server.start(timeout=timeout)
            self._servers[slug] = server
            return server

    def stop_all(self) -> None:
        """Stop all managed servers."""
        with self._lock:
            for server in self._servers.values():
                try:
                    server.stop()
                except Exception:
                    pass
            self._servers.clear()


# Global pool singleton
_pool: MCPProcessPool | None = None
_pool_lock = threading.Lock()


def _get_global_pool() -> MCPProcessPool:
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = MCPProcessPool()
        return _pool


def _mcp_env() -> dict[str, str]:
    """Build environment for MCP subprocess."""
    safe_keys = {
        "PATH", "HOME", "USERPROFILE", "USER", "LOGNAME",
        "VIRTUAL_ENV", "PYTHONPATH", "PYTHONHOME",
        "SYSTEMROOT", "SYSTEMDRIVE", "COMSPEC", "WINDIR", "PATHEXT",
        "APPDATA", "LOCALAPPDATA", "PROGRAMFILES",
        "TEMP", "TMP", "TMPDIR",
        "LANG", "LC_ALL", "LC_CTYPE",
    }
    return {k: v for k, v in os.environ.items() if k in safe_keys}


def run_mcp(
    slug: str,
    tool_name: str | None,
    *,
    timeout: float = 30.0,
    entry: dict,
    **kwargs: Any,
) -> RunToolResult:
    """Run a tool on an MCP server subprocess.

    Args:
        slug: Package slug.
        tool_name: Tool name to call.
        timeout: Timeout for tool execution.
        entry: Lockfile entry with mcp_command and tools.
        **kwargs: Arguments passed to the tool.
    """
    t0 = time.monotonic()
    try:
        command = entry.get("mcp_command")
        if not command:
            return RunToolResult(
                success=False,
                error=f"No mcp_command in lockfile for '{slug}'",
                mode_used="mcp",
            )

        pool = _get_global_pool()
        server = pool.get_or_start(slug, command)

        # Resolve tool name
        name = tool_name
        if not name:
            tools = entry.get("tools", [])
            if tools:
                name = tools[0].get("name", slug)
            else:
                name = slug

        result = server.call_tool(name, kwargs, timeout=timeout)
        elapsed = (time.monotonic() - t0) * 1000

        return RunToolResult(
            success=True,
            result=result,
            mode_used="mcp",
            duration_ms=round(elapsed, 1),
        )
    except TimeoutError:
        elapsed = (time.monotonic() - t0) * 1000
        return RunToolResult(
            success=False,
            error=f"MCP tool timed out after {timeout}s",
            mode_used="mcp",
            duration_ms=round(elapsed, 1),
            timed_out=True,
        )
    except Exception as exc:
        elapsed = (time.monotonic() - t0) * 1000
        return RunToolResult(
            success=False,
            error=f"{type(exc).__name__}: {exc}",
            mode_used="mcp",
            duration_ms=round(elapsed, 1),
        )
