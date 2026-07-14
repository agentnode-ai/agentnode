"""Slice 2c-2/2c-3 — sandbox-smoke executor (INERT by default, fail-closed).

Runs a submitted MCP server in the pinned sandbox container and checks it answers
the MCP basics (initialize + tools/list), producing a SmokeResult (see mcp.smoke).
Registry-generic (npm 2c-2, PyPI 2c-3) — the two paths differ only in the
install/run argv; the handshake, parsing, status mapping, and cleanup are shared.
Two phases:

  Phase 1 (install): container with network, as root (a fresh named volume is
      root-owned) -> npm: `npm install`; pypi: `uv pip install --target /app`.
  Phase 2 (runtime): container with network=none, volume read-only, as non-root
      (uid 1000) -> npm: `npx --offline`; pypi: the console script
      `/app/bin/<pypi_package>` (host-verified). Speak JSON-RPC over stdio.

Then the volume is removed (always, even on error). No host filesystem, no host
secrets, no host fallback: if the runtime/image is missing or MCP_SMOKE_MODE is
not "container", the executor reports 'unavailable' and runs nothing.

INERT by default: MCP_SMOKE_MODE defaults to "disabled" -> smoke_availability()
returns False -> the background task is never scheduled and no container runs.
Enabling it in prod is a separate gated config + deploy. The live container path
is NOT exercised in CI (needs a runtime + the sandbox image); CI mocks the single
`run_container` seam and tests the pure helpers.

Container invocation mirrors the existing backend pattern (raw subprocess with
the docker/podman CLI, hardened flags) — NO SDK cross-package import.
"""

from __future__ import annotations

import asyncio
import functools
import hashlib
import json
import logging
import os
import queue
import re
import subprocess
import threading
from datetime import datetime, timedelta, timezone
from typing import NamedTuple
from uuid import UUID, uuid4

from app.config import CONTAINER_RUNTIME, settings
from app.mcp.smoke import TRANSIENT_FAILURES, evaluate_smoke_freshness

logger = logging.getLogger("agentnode.mcp.smoke")

# Hardened flags shared by both phases (network, read-only, and USER differ per
# phase — see below). Host verification (2c-2) showed a fresh named volume is
# root-owned, so phase 1 (install) must run as root to populate it; the
# security-critical phase 2 (running the untrusted server) stays non-root
# (uid 1000) + network=none + read-only + volume read-only.
_HARDENED_BASE = [
    "--rm",
    "--cap-drop=ALL",
    "--security-opt=no-new-privileges:true",
    "--pids-limit=256",
    "--memory=512m",
    "--cpus=1.0",
]


# ---------------------------------------------------------------------------
# Availability (fail-closed) + eligibility
# ---------------------------------------------------------------------------


# G2 — process-local recovery gate. The startup reaper + smoke_running recovery
# must complete cleanly before THIS process runs any new smoke. Fail-closed: until
# recovery is confirmed, smoke_availability() reports unavailable so no container
# runs — while the rest of the API still comes up normally. A later successful
# recovery attempt (only at startup today) flips it to ready.
# Values: "not_started" | "ready" | "unavailable".
_RECOVERY_STATUS = "not_started"


def get_recovery_status() -> str:
    return _RECOVERY_STATUS


def _set_recovery_status(status: str) -> None:
    global _RECOVERY_STATUS
    _RECOVERY_STATUS = status


def smoke_availability() -> tuple[bool, str]:
    """Whether a real smoke can run. Fail-closed: (False, reason) unless
    MCP_SMOKE_MODE=container AND the startup recovery is ready AND a runtime AND
    the pinned image are present.

    reason is one of: "disabled" | "recovery_pending" | "recovery_unavailable" |
    "no_runtime" | "image_missing" | "".
    """
    if settings.MCP_SMOKE_MODE != "container":
        return False, "disabled"
    # G2: never start a container until this process has confirmed a clean
    # orphan/marker recovery. A reaper/DB-recovery failure keeps smokes off.
    if _RECOVERY_STATUS != "ready":
        reason = (
            "recovery_pending"
            if _RECOVERY_STATUS == "not_started"
            else "recovery_unavailable"
        )
        return False, reason
    if not CONTAINER_RUNTIME:
        return False, "no_runtime"
    try:
        r = subprocess.run(
            [CONTAINER_RUNTIME, "image", "inspect", settings.MCP_SMOKE_IMAGE],
            capture_output=True,
            timeout=10,
        )
        if r.returncode != 0:
            return False, "image_missing"
    except Exception:
        return False, "image_missing"
    return True, ""


def should_smoke(manifest: dict, sv: dict) -> tuple[bool, str | None, str | None]:
    """Decide whether to smoke this submission.

    Returns (run, skip_status, review_reason):
      - (True, None, None)          -> run the smoke
      - (False, "skipped", reason)  -> record a SKIPPED SmokeResult (review-fallback)
      - (False, None, None)         -> do NOT record a smoke; a pre-smoke gate
                                       (package_exists / version_pinned) handles it
    """
    mcp_server = manifest.get("mcp_server") or {}
    registry = (sv.get("registry") or "").lower()

    # npm (2c-2) and pypi (2c-3) are supported; anything else is review-fallback.
    if registry not in ("npm", "pypi"):
        return False, "skipped", "unsupported_registry"
    # Credentialed servers can't be fairly smoked without real secrets.
    if mcp_server.get("env_keys"):
        return False, "skipped", "credentialed"
    # Not resolvable on the public registry (nonexistent or private): the
    # package_exists / version gates already block it — don't run a smoke.
    if not sv.get("package_exists") or not sv.get("resolved_version"):
        return False, None, None
    # Unpinned launch command: the version_pinned gate handles it — don't run.
    if sv.get("command_pinning") != "pinned":
        return False, None, None
    return True, None, None


# ---------------------------------------------------------------------------
# Pure helpers — argv builders, JSON-RPC frames, output parsing
# ---------------------------------------------------------------------------


def command_hash(command) -> str:
    raw = json.dumps(command, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _volume_name() -> str:
    return f"mcp-smoke-{uuid4().hex[:16]}"


# G2 — strict identity so the reaper can target ONLY our own resources. Every
# phase container gets a deterministic --name + AgentNode labels; the volume keeps
# its strict prefix. The reaper only ever acts on the label / this exact regex —
# never an unscoped substring, never a generic prune.
_SMOKE_COMPONENT_LABEL = "agentnode.component=mcp-smoke"
_VOLUME_RE = re.compile(r"^mcp-smoke-[0-9a-f]{16}$")


def _sid_from_volume(volume: str) -> str:
    """The 16-hex smoke id embedded in our own volume name (never a package name)."""
    return volume[len("mcp-smoke-") :] if volume.startswith("mcp-smoke-") else volume


def _identity_args(volume: str, phase: str) -> list[str]:
    """`--name` + labels tying a container to this smoke. phase = install|runtime.
    Derived from our own volume id, so no user-controlled string reaches a Docker
    name/label."""
    sid = _sid_from_volume(volume)
    return [
        "--name",
        f"mcp-smoke-{sid}-{phase}",
        "--label",
        "agentnode.managed=true",
        "--label",
        _SMOKE_COMPONENT_LABEL,
        "--label",
        f"agentnode.smoke_id={sid}",
        "--label",
        f"agentnode.phase={phase}",
    ]


def install_argv(image: str, volume: str, package: str, version: str) -> list[str]:
    """Phase 1: install the pinned package into the volume, WITH network. Runs as
    root (--user 0:0) because a fresh named volume is root-owned; still fully
    hardened (cap-drop=ALL, no-new-privileges, limits, no host mounts, ephemeral).
    """
    return [
        CONTAINER_RUNTIME or "docker",
        "run",
        *_identity_args(volume, "install"),
        *_HARDENED_BASE,
        "--user",
        "0:0",
        "--network",
        "default",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=64m",
        "-v",
        f"{volume}:/app:rw",
        "-e",
        "HOME=/app",
        "-e",
        "npm_config_cache=/app/.npm",
        "-w",
        "/app",
        image,
        "sh",
        "-c",
        f"npm install --prefix /app --no-audit --no-fund {package}@{version}",
    ]


def run_argv(image: str, volume: str, package: str, version: str) -> list[str]:
    """Phase 2: start the server from the volume, network=none, volume read-only,
    as non-root (uid 1000) — the security-critical phase where untrusted server
    code runs. Reads the root-installed files (world-readable)."""
    return [
        CONTAINER_RUNTIME or "docker",
        "run",
        "-i",
        *_identity_args(volume, "runtime"),
        *_HARDENED_BASE,
        "--user",
        "1000:1000",
        "--read-only",
        "--network",
        "none",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=32m",
        "-v",
        f"{volume}:/app:ro",
        "-e",
        "HOME=/app",
        "-e",
        "npm_config_cache=/app/.npm",
        "-e",
        "npm_config_offline=true",
        "-w",
        "/app",
        image,
        "npx",
        "--offline",
        "--prefix",
        "/app",
        "-y",
        f"{package}@{version}",
    ]


def install_argv_pypi(image: str, volume: str, package: str, version: str) -> list[str]:
    """Phase 1 (PyPI): `uv pip install --target /app pkg==ver` into the volume,
    WITH network, as root (fresh named volume is root-owned — same as npm). The uv
    cache goes to a tmpfs so nothing writable is needed in the runtime volume.
    Host-verified 2c-3 (Variant B)."""
    return [
        CONTAINER_RUNTIME or "docker",
        "run",
        *_identity_args(volume, "install"),
        *_HARDENED_BASE,
        "--user",
        "0:0",
        "--network",
        "default",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=256m",
        "-v",
        f"{volume}:/app:rw",
        "-e",
        "HOME=/app",
        "-e",
        "UV_CACHE_DIR=/tmp/uvcache",
        "-w",
        "/app",
        image,
        "sh",
        "-c",
        f"uv pip install --target /app {package}=={version}",
    ]


def run_argv_pypi(image: str, volume: str, package: str, version: str) -> list[str]:
    """Phase 2 (PyPI): run the installed console script `/app/bin/<package>` from
    the volume, network=none, volume read-only, non-root (uid 1000), PYTHONPATH=/app
    so the flat --target install resolves. This is the faithful analog of
    `uvx <package>`. If the package ships no such console script the container
    fails -> startup_crash (honest). ``version`` is unused (the installed script is
    already the pinned version) but kept for a uniform dispatch signature.
    Host-verified 2c-3 (Variant B, console-script run model)."""
    return [
        CONTAINER_RUNTIME or "docker",
        "run",
        "-i",
        *_identity_args(volume, "runtime"),
        *_HARDENED_BASE,
        "--user",
        "1000:1000",
        "--read-only",
        "--network",
        "none",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=32m",
        "-v",
        f"{volume}:/app:ro",
        "-e",
        "HOME=/app",
        "-e",
        "PYTHONPATH=/app",
        "-w",
        "/app",
        image,
        f"/app/bin/{package}",
    ]


def volume_rm_argv(volume: str) -> list[str]:
    return [CONTAINER_RUNTIME or "docker", "volume", "rm", "-f", volume]


# ---------------------------------------------------------------------------
# Deterministic MCP stdio handshake (2c-6). MCP stdio transport = newline-
# delimited JSON. We send one request at a time and FULLY await its response by
# id BEFORE the next step, keeping stdin OPEN until after the tools/list response.
# This fixes the 2c-2 send-all-then-EOF race, where a server could exit on stdin
# EOF before emitting the tools/list response (falsely classified tools_list_failed).
# ---------------------------------------------------------------------------

_EOF = object()  # sentinel: the process closed stdout / exited
_MALFORMED = object()  # sentinel: a line looked like JSON but did not parse
_EXCESSIVE_OUTPUT = object()  # sentinel (G3): stdout exceeded the byte cap
_MAX_SKIP = 200  # bound the skip loop against a chatty server (log/notification spam)
_READ_CHUNK = 8192  # G3: fixed byte chunk for the bounded stdout reader


class _Timeout(Exception):
    pass


def _init_msg() -> dict:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "agentnode-smoke", "version": "0.1.0"},
        },
    }


_INITIALIZED_MSG = {"jsonrpc": "2.0", "method": "notifications/initialized"}


def _tools_list_msg() -> dict:
    return {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}


def _parse_line(line: str):
    """A stdout line -> a JSON-RPC dict, None (log noise), or _MALFORMED."""
    s = (line or "").strip()
    if not s or s[0] not in "{[":
        return None  # server log noise, not a JSON-RPC frame
    try:
        msg = json.loads(s)
    except Exception:
        return _MALFORMED
    return msg if isinstance(msg, dict) else None


def _hs(stage: str, failure=None, initialized: bool = False, tools_count=None) -> dict:
    return {
        "ok": stage == "ok",
        "initialized": initialized,
        "tools_count": tools_count,
        "protocol_stage": stage,
        "failure_reason": failure,
    }


def _do_handshake(send, recv_line, *, step_timeout: float) -> dict:
    """Sequential JSON-RPC handshake. ``send(dict)`` writes a message; each read
    ``recv_line(timeout)`` returns the next stdout line (str), the ``_EOF``
    sentinel on process exit, and raises ``_Timeout`` on a per-step timeout.

    Requests go one at a time and each response is awaited by id before the next
    step (stdin stays open). Returns {ok, initialized, tools_count, protocol_stage,
    failure_reason}. protocol_stage is a fine-grained sanitized diagnostic; the
    failure_reason maps to the STABLE codes the gate already understands.
    """
    # --- step 1: initialize (wait for the id=1 response) ---
    send(_init_msg())
    for _ in range(_MAX_SKIP):
        try:
            line = recv_line(step_timeout)
        except _Timeout:
            return _hs("initialize_timeout", "timeout")
        if line is _EOF:
            return _hs("process_exited_before_initialize", "startup_crash")
        if line is _EXCESSIVE_OUTPUT:
            return _hs("initialize_excessive_output", "excessive_output")
        msg = _parse_line(line)
        if msg is None:
            continue  # server log noise
        if msg is _MALFORMED:
            return _hs("initialize_malformed", "protocol_error")
        if msg.get("id") == 1:
            if "error" in msg:
                return _hs("initialize_error", "initialize_failed")
            if "result" in msg:
                break
            return _hs("initialize_malformed", "protocol_error")
        # a notification or a different id -> skip and keep reading
    else:
        return _hs("initialize_timeout", "timeout")  # only noise, never a response

    # --- step 2: initialized notification + tools/list (wait for the id=2 response) ---
    send(_INITIALIZED_MSG)
    send(_tools_list_msg())
    for _ in range(_MAX_SKIP):
        try:
            line = recv_line(step_timeout)
        except _Timeout:
            return _hs("tools_list_timeout", "timeout", initialized=True)
        if line is _EOF:
            # Server exited after initialize without answering tools/list — the old
            # race symptom. Classify TRANSIENT (review), never a hard tools_list_failed,
            # so a good-but-eager server is never falsely hard-blocked.
            return _hs("tools_list_no_response", "timeout", initialized=True)
        if line is _EXCESSIVE_OUTPUT:
            return _hs(
                "tools_list_excessive_output", "excessive_output", initialized=True
            )
        msg = _parse_line(line)
        if msg is None:
            continue
        if msg is _MALFORMED:
            return _hs("tools_list_malformed", "protocol_error", initialized=True)
        if msg.get("id") == 2:
            if "error" in msg:
                # A genuine JSON-RPC error response -> the server is broken (hard).
                return _hs("tools_list_error", "tools_list_failed", initialized=True)
            result = msg.get("result") or {}
            tools = result.get("tools")
            if isinstance(tools, list):
                return _hs("ok", None, initialized=True, tools_count=len(tools))
            return _hs("tools_list_malformed", "protocol_error", initialized=True)
        # skip
    else:
        return _hs("tools_list_timeout", "timeout", initialized=True)


class _StdioProc:
    """Wraps a Popen'd stdio process (BINARY stdout). ONE background thread reads
    stdout in fixed byte chunks (G3), splits newline-delimited frames, and pushes
    decoded str lines into a BOUNDED queue — capping TOTAL bytes at
    ``max_output_bytes`` so neither a chatty server (many lines) nor a single giant
    line (no newline) can grow the API process's memory. stderr goes straight to
    DEVNULL (no pipe -> no drain thread, no full-pipe deadlock). ``recv_line``
    yields a str line, the ``_EOF`` sentinel on exit, or ``_EXCESSIVE_OUTPUT`` when
    the cap is hit; it raises ``_Timeout`` on a per-step timeout."""

    def __init__(self, proc: subprocess.Popen, max_output_bytes: int):
        self._proc = proc
        self._max = max_output_bytes
        self._q: queue.Queue = queue.Queue(maxsize=256)
        self._stop = threading.Event()
        self._reader = threading.Thread(target=self._read_stdout, daemon=True)
        self._reader.start()

    def _safe_put(self, item) -> None:
        # Bounded queue: never block forever. Once close() sets _stop (consumer is
        # gone), stop trying — the daemon thread then exits with the process.
        while not self._stop.is_set():
            try:
                self._q.put(item, timeout=0.2)
                return
            except queue.Full:
                continue

    def _read_stdout(self) -> None:
        buf = bytearray()
        total = 0
        try:
            stream = self._proc.stdout  # binary BufferedReader
            while not self._stop.is_set():
                chunk = stream.read1(_READ_CHUNK)  # available bytes, not block-for-full
                if not chunk:
                    break  # EOF
                total += len(chunk)
                if total > self._max:
                    self._safe_put(_EXCESSIVE_OUTPUT)  # hard byte cap (line or flood)
                    return
                buf.extend(chunk)
                while True:
                    nl = buf.find(b"\n")
                    if nl < 0:
                        break
                    line = bytes(buf[:nl])
                    del buf[: nl + 1]
                    self._safe_put(line.decode("utf-8", "replace"))
            if buf and not self._stop.is_set():
                self._safe_put(bytes(buf).decode("utf-8", "replace"))
        except Exception:
            pass
        finally:
            self._safe_put(_EOF)

    def send(self, msg: dict) -> None:
        try:
            self._proc.stdin.write((json.dumps(msg) + "\n").encode("utf-8"))
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError, ValueError):
            # The process exited / stdin closed. Don't raise — the recv side will
            # surface EOF and classify the stage correctly (process_exited_before_
            # initialize, or tools_list_no_response when it exits after initialize).
            pass

    def recv_line(self, timeout: float):
        try:
            return self._q.get(timeout=timeout)  # str line, _EOF, or _EXCESSIVE_OUTPUT
        except queue.Empty:
            raise _Timeout

    def close(self) -> None:
        self._stop.set()  # unblock a queue-full reader; stop further reads
        try:
            if self._proc.stdin and not self._proc.stdin.closed:
                self._proc.stdin.close()
        except Exception:
            pass
        try:
            self._proc.terminate()
            self._proc.wait(timeout=5)
        except Exception:
            try:
                self._proc.kill()
                self._proc.wait(timeout=2)
            except Exception:
                pass
        # Reader ends on process-exit (read1 -> b"") or _stop; join so no thread leak.
        try:
            self._reader.join(timeout=2)
        except Exception:
            pass


def _run_handshake(argv: list[str], timeout: int) -> dict:
    """Popen the runtime container (argv) in BINARY mode with stderr -> DEVNULL, run
    the deterministic handshake over its stdio, and ALWAYS terminate + reap the
    process in finally. ``timeout`` is the per-step read timeout. Returns the
    _do_handshake result; never leaves the process running. Real-runtime seam."""
    proc = subprocess.Popen(
        argv,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,  # server logs discarded — no pipe, no deadlock
    )
    sp = _StdioProc(proc, settings.MCP_SMOKE_MAX_OUTPUT_BYTES)
    try:
        return _do_handshake(sp.send, sp.recv_line, step_timeout=timeout)
    except (BrokenPipeError, OSError):
        # stdin write failed -> the process exited immediately (startup crash).
        return _hs("process_exited_before_initialize", "startup_crash")
    finally:
        sp.close()


# ---------------------------------------------------------------------------
# The single subprocess seam (mocked in tests) + the orchestrator
# ---------------------------------------------------------------------------


def _run_container(argv: list[str], input_text: str | None, timeout: int, capture=True):
    """Run one container invocation. Returns (returncode, stdout, stderr). Raises
    subprocess.TimeoutExpired on timeout. The ONLY seam that touches a real runtime.

    G3: ``capture=False`` sends stdout+stderr to DEVNULL and returns empty strings —
    used for the install + volume-rm phases (run_smoke only needs the returncode),
    so a verbose/hostile install can't hold unbounded output in the API process's
    RAM. The reaper/list/inspect callers keep ``capture=True`` (they need the text)."""
    if not capture:
        proc = subprocess.run(
            argv,
            input=input_text,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=timeout,
        )
        return proc.returncode, "", ""
    proc = subprocess.run(
        argv,
        input=input_text,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def _result(status: str, **fields) -> dict:
    # 2c-4a: stamp expires_at / recheck_at (checked_at + TTL) + schema_version so
    # freshness can be evaluated later. checked_at is present on every result.
    checked = fields.get("checked_at")
    expires = None
    recheck = None
    if checked:
        try:
            dt = datetime.fromisoformat(checked)
            exp = dt + timedelta(days=settings.MCP_SMOKE_TTL_DAYS)
            expires = exp.isoformat()
            recheck = expires  # recheck when it expires
        except Exception:
            pass
    r = {
        "status": status,
        "runtime": fields.get("runtime"),
        "package": fields.get("package"),
        "version": fields.get("version"),
        "command_hash": fields.get("command_hash"),
        "initialized": bool(fields.get("initialized")),
        "tools_count": fields.get("tools_count"),
        "duration_ms": fields.get("duration_ms"),
        "sandbox_backend": fields.get("sandbox_backend"),
        "image_digest": fields.get("image_digest"),
        "run_model": fields.get("run_model"),
        "schema_version": settings.MCP_SMOKE_SCHEMA_VERSION,
        "failure_reason": fields.get("failure_reason"),
        "review_reason": fields.get("review_reason"),
        # 2c-6: fine-grained sanitized handshake diagnostic (no secrets); the
        # stable failure_reason above is what the gate classifies on.
        "protocol_stage": fields.get("protocol_stage"),
        "checked_at": checked,
        "expires_at": expires,
        "recheck_at": recheck,
    }
    return r


def current_smoke_keys(manifest: dict, sv: dict) -> dict:
    """The binding keys the CURRENT submission would produce, for freshness
    comparison against a stored SmokeResult. Pure (config reads only)."""
    registry = (sv.get("registry") or "").lower()
    reg = _REGISTRIES.get(registry, {})
    mcp_server = manifest.get("mcp_server") or {}
    package = mcp_server.get(reg.get("package_field", "")) or sv.get("package_name")
    return {
        "runtime": registry,
        "package": package,
        "version": sv.get("resolved_version"),
        "command_hash": command_hash(mcp_server.get("command") or []),
        "image_digest": settings.MCP_SMOKE_IMAGE,
        "run_model": reg.get("run_model"),
        "schema_version": settings.MCP_SMOKE_SCHEMA_VERSION,
    }


# Per-registry install/run argv builders + evidence labels. npm = 2c-2,
# pypi = 2c-3 (both host-verified). Adding a registry here does not touch the
# shared orchestration below.
_REGISTRIES = {
    "npm": {
        "install": install_argv,
        "run": run_argv,
        "package_field": "npm_package",
        "run_model": "npx_offline",
    },
    "pypi": {
        "install": install_argv_pypi,
        "run": run_argv_pypi,
        "package_field": "pypi_package",
        "run_model": "console_script",
    },
}


def run_smoke(
    manifest: dict, sv: dict, *, run_container=None, handshake=None, now=None
) -> dict | None:
    """Execute (or skip) the sandbox smoke for an npm or PyPI MCP and return a
    SmokeResult dict, or None if no smoke should be recorded (a pre-smoke gate
    handles it). Registry-generic: the npm (2c-2) and pypi (2c-3) paths differ
    only in the install/run argv + evidence labels; the deterministic JSON-RPC
    handshake (2c-6), status mapping, and cleanup are shared.

    ``run_container`` (install + volume rm), ``handshake`` (the phase-2 stdio
    handshake), and ``now`` are injectable for testing. Never raises — failures
    map to a SmokeResult.
    """
    run_container = run_container or _run_container
    handshake = handshake or _run_handshake
    now = now or (lambda: datetime.now(timezone.utc))
    stamp = now().isoformat()

    available, why = smoke_availability()
    if not available:
        return _result("unavailable", failure_reason=why, checked_at=stamp)

    run, skip_status, review_reason = should_smoke(manifest, sv)
    if not run:
        if skip_status == "skipped":
            return _result("skipped", review_reason=review_reason, checked_at=stamp)
        return None  # pre-smoke gate handles it — record nothing

    registry = (sv.get("registry") or "").lower()
    reg = _REGISTRIES[registry]
    install_fn, run_fn = reg["install"], reg["run"]

    mcp_server = manifest.get("mcp_server") or {}
    package = mcp_server.get(reg["package_field"]) or sv.get("package_name")
    version = sv.get("resolved_version")
    chash = command_hash(mcp_server.get("command") or [])
    image = settings.MCP_SMOKE_IMAGE
    base = dict(
        runtime=registry,
        package=package,
        version=version,
        command_hash=chash,
        sandbox_backend=CONTAINER_RUNTIME,
        image_digest=image,
        run_model=reg["run_model"],
        checked_at=stamp,
    )
    volume = _volume_name()
    started = now()

    def _elapsed_ms() -> int:
        return int((now() - started).total_seconds() * 1000)

    try:
        # Phase 1 — install (with network) into the volume. G3: no output capture
        # (only rc matters) so a verbose/hostile install can't fill the API's RAM.
        try:
            rc, out, err = run_container(
                install_fn(image, volume, package, version),
                None,
                settings.MCP_SMOKE_INSTALL_TIMEOUT,
                capture=False,
            )
        except subprocess.TimeoutExpired:
            return _result(
                "failed", failure_reason="timeout", duration_ms=_elapsed_ms(), **base
            )
        if rc != 0:
            return _result(
                "failed",
                failure_reason="install_failed",
                duration_ms=_elapsed_ms(),
                **base,
            )

        # Phase 2 — run the server (network=none, volume read-only) + the
        # deterministic sequential JSON-RPC handshake (2c-6).
        try:
            hs = handshake(
                run_fn(image, volume, package, version),
                settings.MCP_SMOKE_RUNTIME_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            return _result(
                "failed",
                failure_reason="timeout",
                protocol_stage="runtime_timeout",
                duration_ms=_elapsed_ms(),
                **base,
            )
        dur = _elapsed_ms()
        if hs.get("ok"):
            return _result(
                "passed",
                initialized=True,
                tools_count=hs.get("tools_count"),
                protocol_stage="ok",
                duration_ms=dur,
                **base,
            )
        return _result(
            "failed",
            failure_reason=hs.get("failure_reason") or "protocol_error",
            protocol_stage=hs.get("protocol_stage"),
            duration_ms=dur,
            **base,
        )
    except Exception as e:  # noqa: BLE001 — never let the executor raise
        logger.warning("mcp smoke internal error: %s", e)
        return _result(
            "failed", failure_reason="internal_error", duration_ms=_elapsed_ms(), **base
        )
    finally:
        # Always remove the volume, even on failure — no leaked state.
        try:
            run_container(volume_rm_argv(volume), None, 15, capture=False)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Background integration — non-blocking, own session, race-guarded
# ---------------------------------------------------------------------------

_smoke_sem: asyncio.Semaphore | None = None
_RUNNING_TTL = timedelta(minutes=10)


def _get_sem() -> asyncio.Semaphore:
    global _smoke_sem
    if _smoke_sem is None:
        _smoke_sem = asyncio.Semaphore(settings.MCP_SMOKE_MAX_CONCURRENT)
    return _smoke_sem


def _is_fresh(iso: str | None) -> bool:
    if not iso:
        return False
    try:
        started = datetime.fromisoformat(iso)
    except Exception:
        return False
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - started < _RUNNING_TTL


# ---------------------------------------------------------------------------
# G3 — host-resource preflight (RAM/disk) + backlog bounding. Measured with stdlib
# only (/proc/meminfo, os.statvfs, os.getloadavg) — no psutil, no shell pipes, no
# docker-info per smoke. A shortfall or a measurement error is FAIL-CLOSED for the
# smoke (no container) and transient/review, never a hard package fault.
# ---------------------------------------------------------------------------


class ResourceCheck(NamedTuple):
    ok: bool
    memory_available_mb: int | None
    memory_required_mb: int
    disk_free_gb: float | None
    disk_required_gb: int
    load_1m: float | None
    reason: str | None  # None when ok; else *_below_minimum / *_measurement_failed


def _read_mem_available_mb() -> int | None:
    """MemAvailable from /proc/meminfo, in MiB. None on any read/parse error."""
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024  # kB -> MiB
    except Exception:
        return None
    return None


def _free_disk_gb(path: str) -> float | None:
    """Free space (available to unprivileged users) at ``path``, in GiB. None on
    error. On this host /var/lib/docker is on /, so this reflects the docker fs."""
    try:
        st = os.statvfs(path)
        return (st.f_bavail * st.f_frsize) / (1024**3)
    except Exception:
        return None


def check_host_resources() -> ResourceCheck:
    """Authoritative host-resource gate, measured just before a docker run. Load is
    diagnostic ONLY (never blocks). RAM/disk below the configured minimum -> not ok;
    a measurement failure -> not ok (fail-closed). Testable via the injectable
    module-level measurement helpers (_read_mem_available_mb / _free_disk_gb)."""
    req_mem = settings.MCP_SMOKE_MIN_AVAILABLE_MEMORY_MB
    req_disk = settings.MCP_SMOKE_MIN_FREE_DISK_GB
    try:
        load1 = round(os.getloadavg()[0], 2)
    except Exception:
        load1 = None
    mem = _read_mem_available_mb()
    if mem is None:
        return ResourceCheck(
            False, None, req_mem, None, req_disk, load1, "memory_measurement_failed"
        )
    disk = _free_disk_gb(settings.MCP_SMOKE_DOCKER_ROOT)
    if disk is None:
        return ResourceCheck(
            False, mem, req_mem, None, req_disk, load1, "disk_measurement_failed"
        )
    if mem < req_mem:
        return ResourceCheck(
            False, mem, req_mem, round(disk, 2), req_disk, load1, "memory_below_minimum"
        )
    if disk < req_disk:
        return ResourceCheck(
            False, mem, req_mem, round(disk, 2), req_disk, load1, "disk_below_minimum"
        )
    return ResourceCheck(True, mem, req_mem, round(disk, 2), req_disk, load1, None)


def _should_store_resource_unavailable(prev: dict | None, prev_freshness: str) -> bool:
    """Whether a resource_unavailable ('could not test right now') result may REPLACE
    the stored smoke. It must NEVER downgrade authoritative evidence: not a fresh,
    key-matching PASS (kept; G1 already guards this) and not an objective HARD
    failure (kept — a momentary host shortfall must not turn a real hard block into
    transient/review). It DOES replace absent / expired / key-mismatch / transient /
    unavailable / skipped evidence."""
    if not prev:
        return True
    if prev_freshness == "fresh":
        return False  # fresh key-matching pass — keep
    if (
        prev.get("status") == "failed"
        and prev.get("failure_reason") not in TRANSIENT_FAILURES
    ):
        return False  # objective HARD failure — never downgrade to transient
    return True


# ---------------------------------------------------------------------------
# G3 — bounded in-flight scheduling. asyncio is single-threaded, so this counter is
# mutated only from the event loop with NO await between check and increment ->
# atomic, no lock needed. Process-local: an API restart resets it to 0. It caps how
# many tasks may WAIT (the Semaphore already caps ACTIVE docker runs at 1).
# ---------------------------------------------------------------------------

_inflight = 0


def _try_claim_inflight() -> bool:
    """Non-blocking, no-await claim of a scheduling slot. max in-flight = 1 active +
    MCP_SMOKE_MAX_PENDING. Returns False (busy) when the cap is reached."""
    global _inflight
    if _inflight >= 1 + settings.MCP_SMOKE_MAX_PENDING:
        return False
    _inflight += 1
    return True


def _release_inflight() -> None:
    global _inflight
    if _inflight > 0:  # never negative
        _inflight -= 1


async def _run_scheduled_smoke(submission_id: str) -> None:
    """BackgroundTask wrapper that ALWAYS releases the in-flight slot — on success,
    G1-skip, resource_unavailable, or any error. NOTE: run_and_store_smoke may also
    be called DIRECTLY (canary/tests) without a claim; that path does not touch the
    counter (only this wrapper claims/releases)."""
    try:
        await run_and_store_smoke(submission_id)
    finally:
        _release_inflight()


# ---------------------------------------------------------------------------
# G2 — orphan reaper + startup recovery (restart/crash hardening). No new worker,
# no cron: this runs once in the FastAPI lifespan at startup, before any smoke can
# be scheduled. After a full restart every existing smoke resource is orphaned by
# construction (BackgroundTasks are not persisted, the old process is gone, the
# semaphore is fresh) — so we can safely remove ALL of our own resources.
# ---------------------------------------------------------------------------


def reap_smoke_orphans(run=None, *, timeout: int = 15) -> dict:
    """Remove orphaned AgentNode smoke containers + volumes left by a prior process.

    Targets ONLY resources carrying our label / matching our strict volume regex —
    never a generic prune, never a fuzzy substring, never the sandbox image.
    Idempotent and race-tolerant (already-gone objects are ignored). Containers are
    stopped then removed BEFORE their volume (a volume in use can't be removed).
    ``run`` is the injectable docker subprocess seam (tests mock it).

    Returns a sanitized summary: {containers_found, containers_stopped,
    containers_removed, volumes_found, volumes_removed, errors, successful}.
    ``successful`` is True only if enumeration succeeded (or there is no runtime, so
    nothing can be leaked). An enumeration/exec failure -> False -> fail-closed.
    """
    run = run or _run_container
    res: dict = {
        "containers_found": 0,
        "containers_stopped": 0,
        "containers_removed": 0,
        "volumes_found": 0,
        "volumes_removed": 0,
        "errors": [],
        "successful": False,
    }
    cr = CONTAINER_RUNTIME
    if not cr:
        # No runtime -> no smoke resources can exist. Nothing to reap; not a failure.
        res["successful"] = True
        return res
    try:
        # --- containers: only those carrying our component label ---
        rc, out, _ = run(
            [cr, "ps", "-aq", "--filter", f"label={_SMOKE_COMPONENT_LABEL}"],
            None,
            timeout,
        )
        if rc != 0:
            res["errors"].append("container_list_failed")
            return res
        cids = [c.strip() for c in (out or "").splitlines() if c.strip()]
        res["containers_found"] = len(cids)
        for cid in cids:
            rc_s, _, _ = run([cr, "stop", "-t", "2", cid], None, timeout)
            if rc_s == 0:
                res["containers_stopped"] += 1
            rc_r, _, _ = run([cr, "rm", "-f", cid], None, timeout)
            if rc_r == 0:
                res["containers_removed"] += 1  # non-zero = already gone -> ignore
        # --- volumes: strict regex over the name-prefixed candidates ---
        rc, out, _ = run(
            [cr, "volume", "ls", "-q", "--filter", "name=mcp-smoke-"], None, timeout
        )
        if rc != 0:
            res["errors"].append("volume_list_failed")
            return res
        vols = [
            v.strip() for v in (out or "").splitlines() if _VOLUME_RE.match(v.strip())
        ]
        res["volumes_found"] = len(vols)
        for v in vols:
            rc_v, _, _ = run([cr, "volume", "rm", "-f", v], None, timeout)
            if rc_v == 0:
                res["volumes_removed"] += 1
        res["successful"] = not res["errors"]
    except subprocess.TimeoutExpired:
        res["errors"].append("timeout")
    except Exception as e:  # noqa: BLE001 — recovery must never raise
        res["errors"].append(type(e).__name__)
    return res


async def _recover_stale_smoke_running() -> tuple[bool, int]:
    """Clear smoke_running markers left by a prior process (submission-specific,
    transactional). At full startup any existing marker is orphaned, so it is
    removed and the gate recomputed via the existing central helper. Never deletes a
    stored `smoke` result or touches report/ownership fields. Returns (ok, cleared).
    """
    from sqlalchemy import select

    from app.database import async_session_factory
    from app.mcp.models import McpSubmission

    cleared = 0
    try:
        async with async_session_factory() as session:
            rows = (
                (
                    await session.execute(
                        select(McpSubmission).where(
                            McpSubmission.server_verification.has_key("smoke_running")
                        )
                    )
                )
                .scalars()
                .all()
            )
            for row in rows:
                sv = dict(row.server_verification or {})
                if "smoke_running" not in sv:
                    continue
                started = (sv.get("smoke_running") or {}).get("started_at")
                if started is not None and not isinstance(started, str):
                    logger.warning(
                        "smoke_running recovery: non-str started_at for %s, "
                        "treating as stale",
                        row.id,
                    )
                sv.pop("smoke_running", None)  # remove ONLY this field
                from app.mcp.router import _attach_gate_result

                sv = await _attach_gate_result(
                    sv,
                    row.manifest_raw or {},
                    row.verification_report or {},
                    session,
                    publisher_id=row.publisher_id,
                )
                row.server_verification = sv
                cleared += 1
            await session.commit()
        return True, cleared
    except Exception as e:  # noqa: BLE001 — recovery must never crash startup
        logger.warning("smoke_running startup recovery failed: %s", type(e).__name__)
        return False, cleared


async def startup_smoke_recovery() -> dict:
    """G2 startup recovery: reap orphaned smoke containers/volumes + clear stale
    smoke_running markers, then flip the process-local recovery gate. Fail-closed:
    ONLY a clean reaper AND a clean DB recovery set status=ready; any failure (or an
    undecidable state) leaves status=unavailable, so no smoke runs this process. The
    rest of the API is unaffected. NEVER raises. Called from the FastAPI lifespan
    before serving requests."""
    _set_recovery_status("not_started")
    summary: dict = {"reaper": None, "markers_cleared": 0, "status": "unavailable"}
    try:
        loop = asyncio.get_running_loop()
        reap = await asyncio.wait_for(
            loop.run_in_executor(None, reap_smoke_orphans), timeout=90
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("smoke reaper failed: %s", type(e).__name__)
        reap = {"successful": False, "errors": [type(e).__name__]}
    summary["reaper"] = reap

    db_ok, cleared = await _recover_stale_smoke_running()
    summary["markers_cleared"] = cleared

    if reap.get("successful") and db_ok:
        _set_recovery_status("ready")
        summary["status"] = "ready"
    else:
        _set_recovery_status("unavailable")
        summary["status"] = "unavailable"
    logger.info(
        "mcp smoke startup recovery: status=%s reaper_ok=%s db_ok=%s "
        "markers_cleared=%s containers_removed=%s volumes_removed=%s",
        summary["status"],
        reap.get("successful"),
        db_ok,
        cleared,
        reap.get("containers_removed"),
        reap.get("volumes_removed"),
    )
    return summary


def should_schedule_smoke_recheck(manifest: dict, sv: dict, now=None) -> bool:
    """2c-4b: whether a (re)check is worth scheduling for the CURRENT stored smoke.
    Anti-spam: a fresh passed smoke or a fresh running marker returns False; no
    result / expired / key_mismatch / failed / skipped / unavailable / stale-running
    returns True. Pure (config reads only)."""
    now = now or datetime.now(timezone.utc)
    sv = sv or {}
    # A fresh run already in progress (marker in either the new or legacy slot).
    if _is_fresh((sv.get("smoke_running") or {}).get("started_at")):
        return False
    smoke = sv.get("smoke") or {}
    if smoke.get("status") == "running" and _is_fresh(smoke.get("started_at")):
        return False
    if smoke.get("status") == "passed":
        keys = current_smoke_keys(manifest, sv)
        return evaluate_smoke_freshness(smoke, keys, now) != "fresh"
    # no result / failed / skipped / unavailable / not_run -> (re)schedule on an
    # explicit trigger. Submit is once and reverify is manual, so no spam (no cron).
    return True


def _should_overwrite_smoke(previous_freshness: str, result: dict | None) -> bool:
    """2c-4b transient-protection: whether a recheck result should REPLACE the
    stored smoke. A fresh passed smoke is never clobbered by a transient blip
    (unavailable / transient failure) — only by a new pass or a definitive HARD
    failure. If the previous result was not a fresh pass, the new result wins."""
    if previous_freshness != "fresh":
        return True
    status = (result or {}).get("status")
    if status == "passed":
        return True
    if status == "failed":
        return (result or {}).get("failure_reason") not in TRANSIENT_FAILURES
    return False  # unavailable / skipped / other transient -> keep the fresh pass


def maybe_schedule_smoke(
    background_tasks, submission_id, manifest: dict, sv: dict
) -> bool:
    """Schedule the smoke as a background task IF enabled, eligible, AND a recheck
    is actually needed (freshness-gated — 2c-4b). A cheap no-op when
    MCP_SMOKE_MODE is disabled (the default). Never blocks the request. Returns
    True if a task was scheduled."""
    available, _ = smoke_availability()
    if not available:
        return False
    run, _skip, _reason = should_smoke(manifest, sv)
    if not run:
        return False
    if not should_schedule_smoke_recheck(manifest, sv):
        return False  # a fresh result / running marker already covers it — no spam
    # G3: bound the WAITING backlog (the semaphore bounds ACTIVE runs). Excess ->
    # busy: no task, no smoke_running, no docker; gate stays not_run/future so an
    # admin reverify can retry later.
    if not _try_claim_inflight():
        logger.info(
            "mcp smoke busy: inflight=%s max_inflight=%s active_limit=1 "
            "max_pending=%s sub=%s",
            _inflight,
            1 + settings.MCP_SMOKE_MAX_PENDING,
            settings.MCP_SMOKE_MAX_PENDING,
            str(submission_id)[:8],
        )
        return False
    try:
        background_tasks.add_task(_run_scheduled_smoke, str(submission_id))
    except Exception:
        _release_inflight()  # scheduling failed -> free the slot immediately
        raise
    return True


async def run_and_store_smoke(submission_id: str) -> None:
    """Background: run the smoke, store the SmokeResult in server_verification
    under 'smoke', and recompute gate_result. Own session; semaphore-limited;
    race-guarded on a fresh 'running' marker; never raises out of the task."""
    from app.database import async_session_factory
    from app.mcp.models import McpSubmission

    async with _get_sem():
        try:
            async with async_session_factory() as session:
                row = await session.get(McpSubmission, UUID(submission_id))
                if row is None:
                    return
                sv = dict(row.server_verification or {})
                running = sv.get("smoke_running") or {}
                if _is_fresh(running.get("started_at")):
                    return  # another fresh run is in progress
                manifest = row.manifest_raw or {}
                report = row.verification_report or {}

                # G1 dedup: a concurrently-scheduled task (e.g. two near-simultaneous
                # reverifies) may have already produced a fresh, key-matching passed
                # smoke while this task waited on the semaphore. If so, skip the
                # duplicate run — no container, no new smoke_running marker.
                if (
                    evaluate_smoke_freshness(
                        sv.get("smoke"),
                        current_smoke_keys(manifest, sv),
                        datetime.now(timezone.utc),
                    )
                    == "fresh"
                ):
                    logger.info(
                        "mcp smoke dedup: fresh passing smoke already present for "
                        "%s; skipping duplicate run",
                        submission_id,
                    )
                    return

                # G3: host-resource preflight — measured NOW, just before the docker
                # run, after G1. A shortfall/measurement-error never starts a
                # container, sets no smoke_running, and is transient/review
                # (resource_unavailable). It must NOT downgrade authoritative
                # evidence (a fresh pass or an objective hard fail).
                rchk = check_host_resources()
                if not rchk.ok:
                    logger.info(
                        "mcp smoke resource block: sub=%s reason=%s mem=%s/%s MB "
                        "disk=%s/%s GB load=%s",
                        str(submission_id)[:8],
                        rchk.reason,
                        rchk.memory_available_mb,
                        rchk.memory_required_mb,
                        rchk.disk_free_gb,
                        rchk.disk_required_gb,
                        rchk.load_1m,
                    )
                    r_now = datetime.now(timezone.utc)
                    prev = sv.get("smoke")
                    prev_fresh = evaluate_smoke_freshness(
                        prev, current_smoke_keys(manifest, sv), r_now
                    )
                    if _should_store_resource_unavailable(prev, prev_fresh):
                        sv_r = dict(row.server_verification or {})
                        sv_r["smoke"] = _result(
                            "unavailable",
                            failure_reason="resource_unavailable",
                            checked_at=r_now.isoformat(),
                        )
                        from app.mcp.router import _attach_gate_result

                        sv_r = await _attach_gate_result(
                            sv_r,
                            manifest,
                            report,
                            session,
                            publisher_id=row.publisher_id,
                        )
                        row.server_verification = sv_r
                        await session.commit()
                    return

                # 2c-4b: mark running in a SEPARATE key so the previous result
                # stays valid during the recheck (a fresh passed smoke keeps
                # counting while we re-run — no transient gap).
                previous = sv.get("smoke")
                now = datetime.now(timezone.utc)
                prev_freshness = evaluate_smoke_freshness(
                    previous, current_smoke_keys(manifest, sv), now
                )
                sv["smoke_running"] = {"started_at": now.isoformat()}
                row.server_verification = sv
                await session.commit()

                loop = asyncio.get_running_loop()
                budget = (
                    settings.MCP_SMOKE_INSTALL_TIMEOUT
                    + settings.MCP_SMOKE_RUNTIME_TIMEOUT
                    + 60
                )
                try:
                    result = await asyncio.wait_for(
                        loop.run_in_executor(
                            None, functools.partial(run_smoke, manifest, dict(sv))
                        ),
                        timeout=budget,
                    )
                except asyncio.TimeoutError:
                    result = _result(
                        "failed",
                        failure_reason="timeout",
                        checked_at=datetime.now(timezone.utc).isoformat(),
                    )

                sv2 = dict(row.server_verification or {})
                sv2.pop("smoke_running", None)  # clear the running marker
                # Overwrite only when authoritative — never clobber a fresh passed
                # smoke with a transient blip (2c-4b transient-protection). result
                # is None -> nothing to record; keep the previous smoke.
                if result is not None and _should_overwrite_smoke(
                    prev_freshness, result
                ):
                    sv2["smoke"] = result
                # Recompute the gate with the new smoke (reuses the same wiring as
                # submit; reads sv2["smoke"]). Lazy import avoids a circular import.
                from app.mcp.router import _attach_gate_result

                sv2 = await _attach_gate_result(
                    sv2, manifest, report, session, publisher_id=row.publisher_id
                )
                row.server_verification = sv2
                await session.commit()
        except Exception as e:  # noqa: BLE001 — a background task must not crash
            logger.warning("run_and_store_smoke failed for %s: %s", submission_id, e)
