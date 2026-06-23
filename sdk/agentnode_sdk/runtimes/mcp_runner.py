"""MCP server process pool and tool execution.

Manages MCP server subprocesses via stdio JSON-RPC 2.0 protocol.
Each MCP package gets its own persistent server process, reused across calls.
"""
from __future__ import annotations

import atexit
import itertools
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from agentnode_sdk.models import RunToolResult
from agentnode_sdk.policy import PolicyResult, audit_decision

logger = __import__("logging").getLogger("agentnode.mcp_runner")

_request_id = itertools.count(1)


class MCPServerProcess:
    """A managed MCP server subprocess communicating via stdio JSON-RPC."""

    def __init__(self, slug: str, command: list[str], trust_level: str | None = None,
                 entry: dict | None = None):
        self.slug = slug
        self.command = command
        # Safe default: trust_level missing/None/unknown -> sandbox-required, NEVER
        # host (resolved in start() via sandbox.policy). Default must not be a host tier.
        self.trust_level = trust_level
        # Stage 4B: the lockfile entry (preinstall fields + permissions). When it signals
        # preinstall intent, start() runs the fail-closed sealed-volume path.
        self.entry = entry or {}
        self._process: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._last_used = time.monotonic()
        self._container_name: str | None = None
        self._runtime: str | None = None

    def start(self, timeout: float = 10.0, env_keys: list[str] | None = None) -> None:
        """Start the MCP server subprocess.

        Community/unverified/unknown tiers run INSIDE a container (P0.2). This is
        the central enforcement+routing point — it covers the agent path (via the
        pool) AND direct CLI use (e.g. `agentnode mcp doctor`), closing the
        run_tool-only gate gap. curated -> host; trusted -> host (transition).
        """
        from agentnode_sdk.sandbox import enforce_sandbox_policy, get_default_backend
        from agentnode_sdk.sandbox.policy import requires_sandbox
        from agentnode_sdk.sandbox.types import ProcessSpec

        # Fail-closed gate: community without a runtime is blocked here, not on host.
        enforce_sandbox_policy(self.trust_level, runtime_hint="mcp")

        # Windows: CREATE_NEW_PROCESS_GROUP for clean shutdown
        kwargs: dict[str, Any] = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

        if requires_sandbox(self.trust_level):
            # Containerized path. P0.2 isolates host-FS, HOME and secrets — NOT the
            # network (npx/uvx fetch live). No host env, no mounts, clean container HOME.
            if env_keys:
                # Stage 3A: credentialed community MCPs stay fail-closed, now with a
                # precise, value-free reason. No secret is read, no key is injected,
                # no egress proxy is started — that wiring is Stage 3B (gated behind
                # pre-install/sealed volume + verified allowed_domains). env_keys are
                # NAMES only here; they are never resolved to values.
                from agentnode_sdk.runtimes.mcp_consent import (
                    CredentialedMcpRefused,
                    redact_env_keys,
                    refusal_reason,
                )
                reason = refusal_reason()
                raise CredentialedMcpRefused(
                    reason,
                    f"MCP '{self.slug}' requests credentials "
                    f"({redact_env_keys(env_keys)}), but secret brokering into a "
                    f"sandboxed community MCP is not enabled ({reason}) — refusing to "
                    "expose secrets to untrusted code.",
                )
            from agentnode_sdk.sandbox.mcp_preinstall import has_preinstall_intent

            safe_slug = re.sub(r"[^a-zA-Z0-9_.-]", "-", self.slug)[:40]
            name = f"agentnode-mcp-{safe_slug}-{uuid4().hex[:8]}"
            backend = get_default_backend()

            if has_preinstall_intent(self.entry):
                # Stage 4B: preinstalled MCP — run from the read-only, integrity-verified,
                # descriptor- AND content-bound sealed volume. FAIL-CLOSED: any problem
                # raises; there is NO fallback to the registry-fetch (npx/uvx) mcp_command
                # path, no key, no auto-created/rebuilt volume, no permissive network.
                spec = self._preinstalled_spec(backend, name)
            else:
                # Existing path: non-preinstalled community MCP fetches at runtime
                # (npx/uvx). Open network; clean HOME; no host env, no mounts, no secrets.
                spec = ProcessSpec(
                    command=list(self.command), network="default", clean_home=True,
                    interactive=True, env={}, mounts=[], name=name,
                )
            launch = backend.wrap_command(spec)
            self._container_name = name
            self._runtime = launch[0]
            # The docker/podman CLIENT inherits the host env (to find the runtime);
            # the CONTAINER env is fully controlled by wrap_command's -e flags.
            popen_env = None
            logger.info("MCP '%s' started sandboxed via %s", self.slug, self._runtime)
        else:
            # Host path: curated (allowed) / trusted (transition). Existing behaviour.
            launch = self.command
            popen_env = _mcp_env(env_keys)

        self._process = subprocess.Popen(
            launch,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=popen_env,
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

    def _preinstalled_spec(self, backend, name: str):
        """Stage 4B: gate + verify the sealed pre-install volume, then return the hardened
        ProcessSpec to launch the MCP from it. FAIL-CLOSED — any problem raises and there
        is NO fallback to the registry-fetch mcp_command path, no key, no auto-created or
        rebuilt volume, no permissive network."""
        import subprocess as _sp

        from agentnode_sdk.lock_integrity import verify_entry
        from agentnode_sdk.sandbox import network_for_level
        from agentnode_sdk.sandbox.mcp_preinstall import (
            PreinstallError,
            validate_preinstall_fields,
            verify_volume_content,
        )
        from agentnode_sdk.sandbox.types import (
            MountSpec,
            ProcessSpec,
            SandboxRequiredError,
        )

        entry = self.entry or {}
        mcp_version = entry.get("version")

        # a. lockfile integrity must verify
        ir = verify_entry(self.slug, entry)
        if ir.status != "verified":
            raise PreinstallError(
                f"lockfile integrity for '{self.slug}' is {ir.status!r}, not verified — "
                "refusing to run a preinstalled MCP from an unverified lockfile."
            )

        # b+c. pure shape/command validation + descriptor-bound volume-name gate. The
        #      MCP/ANP version (mcp_version) and the manager package_version are kept
        #      distinct by validate_preinstall_fields/mcp_sandbox_volume_name.
        pspec = validate_preinstall_fields(self.slug, mcp_version, entry)

        # d. runtime present + volume EXISTS — `volume inspect` BEFORE any -v mount so the
        #    runtime never silently auto-creates an empty volume of that name.
        availability = backend.check_available()
        if not availability.available:
            raise SandboxRequiredError(
                "Preinstalled MCP execution requires a container runtime + the pinned "
                f"image. {availability.reason or 'None detected'} — refusing host fallback."
            )
        runtime = availability.backend or "docker"
        try:
            insp = _sp.run(
                [runtime, "volume", "inspect", pspec.volume],
                capture_output=True, timeout=10,
            )
        except Exception as exc:
            raise PreinstallError(f"could not verify sandbox volume: {exc}")
        if insp.returncode != 0:
            raise PreinstallError(
                f"sandbox volume '{pspec.volume}' is missing — reinstall required "
                f"(run: agentnode install {self.slug})."
            )

        # e. content<->hash verifier container (network=none, RO /install, env={}) runs
        #    BEFORE the MCP container; mismatch/missing ⇒ refuse.
        verify_volume_content(backend, pspec.volume, pspec.artifact_hash)

        # f. hardened launch spec: RO volume at /install, policy network (missing/unknown
        #    ⇒ none, never default), minimal module-resolution env only, clean HOME.
        net = network_for_level((entry.get("permissions") or {}).get("network_level"))
        env = (
            {"NODE_PATH": "/install/lib/node_modules"} if pspec.manager == "npm"
            else {"PYTHONPATH": "/install"}
        )
        return ProcessSpec(
            command=list(pspec.command), network=net, clean_home=True, interactive=True,
            env=env, mounts=[MountSpec(src=pspec.volume, dst="/install", read_only=True)],
            name=name,
        )

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
        """Gracefully stop the server, kill if needed, and remove the container."""
        proc = self._process
        if proc and proc.poll() is None:
            try:
                proc.stdin.close()
                proc.wait(timeout=timeout)
            except (subprocess.TimeoutExpired, OSError):
                proc.kill()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass
        # Force-remove the container — killing the `docker run` client does NOT
        # reliably stop the container. Best-effort, idempotent (--rm may already
        # have removed it). Guarded so the host path is unaffected.
        if self._container_name and self._runtime:
            try:
                subprocess.run(
                    [self._runtime, "rm", "-f", self._container_name],
                    capture_output=True, timeout=10,
                )
            except Exception:
                pass
            self._container_name = None

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

    def get_or_start(
        self, slug: str, command: list[str],
        timeout: float = 10.0, env_keys: list[str] | None = None,
        trust_level: str | None = None, entry: dict | None = None,
    ) -> MCPServerProcess:
        """Get an existing server or start a new one."""
        with self._lock:
            server = self._servers.get(slug)
            if server and server.health_check():
                return server

            # Clean up dead server
            if server:
                server.stop()

            server = MCPServerProcess(slug, command, trust_level=trust_level, entry=entry)
            server.start(timeout=timeout, env_keys=env_keys)
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


def _mcp_env(env_keys: list[str] | None = None) -> dict[str, str]:
    """Build environment for MCP subprocess.

    Only system-safe keys plus explicitly declared env_keys are passed through.
    Declared keys are only included if already set in os.environ.
    """
    safe_keys = {
        "PATH", "HOME", "USERPROFILE", "USER", "LOGNAME",
        "VIRTUAL_ENV", "PYTHONPATH", "PYTHONHOME",
        "SYSTEMROOT", "SYSTEMDRIVE", "COMSPEC", "WINDIR", "PATHEXT",
        "APPDATA", "LOCALAPPDATA", "PROGRAMFILES",
        "TEMP", "TMP", "TMPDIR",
        "LANG", "LC_ALL", "LC_CTYPE",
    }
    result = {k: v for k, v in os.environ.items() if k in safe_keys}
    for key in (env_keys or []):
        val = os.environ.get(key)
        if val is not None:
            result[key] = val
    return result


def _check_env_keys(slug: str, env_keys: list[str]) -> list[str]:
    """Return list of declared env_keys that are not set in os.environ."""
    return [k for k in env_keys if k not in os.environ]


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
    name = tool_name
    try:
        command = entry.get("mcp_command")
        if not command:
            return RunToolResult(
                success=False,
                error=f"No mcp_command in lockfile for '{slug}'",
                mode_used="mcp",
            )

        env_keys = entry.get("mcp_env_keys") or []
        missing = _check_env_keys(slug, env_keys)
        if missing:
            names = ", ".join(missing)
            return RunToolResult(
                success=False,
                error=(
                    f"Missing environment variables for '{slug}': {names}\n"
                    f"Set them before running, e.g.: "
                    + " ".join(f"{k}=..." for k in missing[:3])
                ),
                mode_used="mcp",
            )

        pool = _get_global_pool()
        server = pool.get_or_start(
            slug, command, env_keys=env_keys, trust_level=entry.get("trust_level"),
            entry=entry,
        )

        # Resolve tool name
        name = tool_name
        if not name:
            tools = entry.get("tools", [])
            if tools:
                name = tools[0].get("name", slug)
            else:
                name = slug

        # Guard: inspect MCP arguments before forwarding
        from agentnode_sdk.guard import inspect_mcp_args
        input_schema = None
        for t in entry.get("tools", []):
            if t.get("name") == name:
                input_schema = t.get("input_schema") or t.get("inputSchema")
                break
        mcp_guard = inspect_mcp_args(slug, name, kwargs, entry, input_schema=input_schema)
        if mcp_guard.action == "deny":
            elapsed = (time.monotonic() - t0) * 1000
            return RunToolResult(
                success=False,
                error=f"MCP argument inspection blocked: {mcp_guard.reason}",
                mode_used="mcp",
                duration_ms=round(elapsed, 1),
            )
        if mcp_guard.action == "prompt":
            elapsed = (time.monotonic() - t0) * 1000
            return RunToolResult(
                success=False,
                error=f"MCP argument inspection requires confirmation: {mcp_guard.reason}",
                mode_used="mcp",
                duration_ms=round(elapsed, 1),
            )

        result = server.call_tool(name, kwargs, timeout=timeout)
        elapsed = (time.monotonic() - t0) * 1000

        _audit_mcp_call(slug, name, success=True, duration_ms=elapsed)

        return RunToolResult(
            success=True,
            result=result,
            mode_used="mcp",
            duration_ms=round(elapsed, 1),
        )
    except TimeoutError:
        elapsed = (time.monotonic() - t0) * 1000
        _audit_mcp_call(slug, name, success=False,
                        duration_ms=elapsed, error_class="TimeoutError")
        return RunToolResult(
            success=False,
            error=f"MCP tool timed out after {timeout}s",
            mode_used="mcp",
            duration_ms=round(elapsed, 1),
            timed_out=True,
        )
    except Exception as exc:
        elapsed = (time.monotonic() - t0) * 1000
        _audit_mcp_call(slug, name, success=False,
                        duration_ms=elapsed, error_class=type(exc).__name__)
        return RunToolResult(
            success=False,
            error=f"{type(exc).__name__}: {exc}",
            mode_used="mcp",
            duration_ms=round(elapsed, 1),
        )


def _audit_mcp_call(
    slug: str,
    tool_name: str | None,
    *,
    success: bool,
    duration_ms: float | None = None,
    error_class: str | None = None,
) -> None:
    """Audit an MCP tool execution result. Never crashes the caller."""
    try:
        dur = round(duration_ms) if duration_ms is not None else 0
        if success:
            reason = f"mcp_call ok duration_ms={dur}"
        else:
            reason = "mcp_call failed"
            if error_class:
                reason += f" error={error_class}"
            reason += f" duration_ms={dur}"

        result = PolicyResult(
            action="allow" if success else "deny",
            reason=reason,
            source="mcp_runner",
        )
        audit_decision(result, "mcp_run", slug, tool_name=tool_name)
    except Exception:
        logger.debug("Failed to audit MCP call for %s", slug, exc_info=True)
