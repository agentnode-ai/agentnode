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
import subprocess
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from app.config import CONTAINER_RUNTIME, settings

logger = logging.getLogger("agentnode.mcp.smoke")

_MAX_OUTPUT_BYTES = 64 * 1024

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


def smoke_availability() -> tuple[bool, str]:
    """Whether a real smoke can run. Fail-closed: (False, reason) unless
    MCP_SMOKE_MODE=container AND a runtime AND the pinned image are present.

    reason is one of: "disabled" | "no_runtime" | "image_missing" | "".
    """
    if settings.MCP_SMOKE_MODE != "container":
        return False, "disabled"
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


def install_argv(image: str, volume: str, package: str, version: str) -> list[str]:
    """Phase 1: install the pinned package into the volume, WITH network. Runs as
    root (--user 0:0) because a fresh named volume is root-owned; still fully
    hardened (cap-drop=ALL, no-new-privileges, limits, no host mounts, ephemeral).
    """
    return [
        CONTAINER_RUNTIME or "docker",
        "run",
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


def handshake_frames() -> str:
    """The three newline-delimited JSON-RPC messages fed to the server's stdin:
    initialize (id=1), the initialized notification, and tools/list (id=2)."""
    init = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "agentnode-smoke", "version": "0.1.0"},
        },
    }
    initialized = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    tools = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
    return "\n".join(json.dumps(m) for m in (init, initialized, tools)) + "\n"


def parse_handshake_output(stdout: str) -> dict:
    """Pure parse of the server's stdout. Matches JSON-RPC responses by id.

    Returns {any_json, malformed, init_result, init_error, tools_count,
    tools_error}. Non-JSON log lines are ignored, but a line that starts like
    JSON yet fails to parse flips ``malformed``.
    """
    any_json = False
    malformed = False
    init_result = False
    init_error = False
    tools_count: int | None = None
    tools_error = False

    for line in (stdout or "").splitlines():
        s = line.strip()
        if not s:
            continue
        if s[0] not in "{[":
            continue  # server log noise, not a JSON-RPC frame
        try:
            msg = json.loads(s)
        except Exception:
            malformed = True
            continue
        if not isinstance(msg, dict):
            continue
        any_json = True
        mid = msg.get("id")
        if mid == 1:
            if "error" in msg:
                init_error = True
            elif "result" in msg:
                init_result = True
        elif mid == 2:
            if "error" in msg:
                tools_error = True
            else:
                result = msg.get("result") or {}
                tools = result.get("tools")
                if isinstance(tools, list):
                    tools_count = len(tools)
                else:
                    tools_error = True

    return {
        "any_json": any_json,
        "malformed": malformed,
        "init_result": init_result,
        "init_error": init_error,
        "tools_count": tools_count,
        "tools_error": tools_error,
    }


def _truncate(text: str) -> str:
    b = (text or "").encode("utf-8", "replace")
    if len(b) <= _MAX_OUTPUT_BYTES:
        return text or ""
    return b[:_MAX_OUTPUT_BYTES].decode("utf-8", "replace") + "\n[truncated]"


# ---------------------------------------------------------------------------
# The single subprocess seam (mocked in tests) + the orchestrator
# ---------------------------------------------------------------------------


def _run_container(argv: list[str], input_text: str | None, timeout: int):
    """Run one container invocation. Returns (returncode, stdout, stderr).
    Raises subprocess.TimeoutExpired on timeout. The ONLY seam that touches a
    real runtime — tests inject a fake."""
    proc = subprocess.run(
        argv,
        input=input_text,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def _result(status: str, **fields) -> dict:
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
        "failure_reason": fields.get("failure_reason"),
        "review_reason": fields.get("review_reason"),
        "checked_at": fields.get("checked_at"),
    }
    return r


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


def run_smoke(manifest: dict, sv: dict, *, run_container=None, now=None) -> dict | None:
    """Execute (or skip) the sandbox smoke for an npm or PyPI MCP and return a
    SmokeResult dict, or None if no smoke should be recorded (a pre-smoke gate
    handles it). Registry-generic: the npm (2c-2) and pypi (2c-3) paths differ
    only in the install/run argv + evidence labels; the JSON-RPC handshake,
    parsing, status mapping, and cleanup are shared.

    ``run_container`` and ``now`` are injectable for testing. Pure except for the
    container seam. Never raises — failures map to a SmokeResult.
    """
    run_container = run_container or _run_container
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
        # Phase 1 — install (with network) into the volume.
        try:
            rc, out, err = run_container(
                install_fn(image, volume, package, version),
                None,
                settings.MCP_SMOKE_INSTALL_TIMEOUT,
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

        # Phase 2 — run the server (network=none, volume read-only) + handshake.
        try:
            rc2, out2, err2 = run_container(
                run_fn(image, volume, package, version),
                handshake_frames(),
                settings.MCP_SMOKE_RUNTIME_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            return _result(
                "failed", failure_reason="timeout", duration_ms=_elapsed_ms(), **base
            )

        if len(_truncate(out2)) != len(out2 or "") or len(_truncate(err2)) != len(
            err2 or ""
        ):
            # Output blew past the cap — couldn't complete cleanly.
            return _result(
                "failed",
                failure_reason="excessive_output",
                duration_ms=_elapsed_ms(),
                **base,
            )

        p = parse_handshake_output(out2)
        dur = _elapsed_ms()
        if not p["any_json"]:
            reason = "startup_crash" if rc2 != 0 else "protocol_error"
            return _result("failed", failure_reason=reason, duration_ms=dur, **base)
        if p["malformed"] and not p["init_result"]:
            return _result(
                "failed", failure_reason="protocol_error", duration_ms=dur, **base
            )
        if p["init_error"] or not p["init_result"]:
            return _result(
                "failed", failure_reason="initialize_failed", duration_ms=dur, **base
            )
        if p["tools_error"] or p["tools_count"] is None:
            return _result(
                "failed", failure_reason="tools_list_failed", duration_ms=dur, **base
            )
        return _result(
            "passed",
            initialized=True,
            tools_count=p["tools_count"],
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
            run_container(volume_rm_argv(volume), None, 15)
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


def maybe_schedule_smoke(
    background_tasks, submission_id, manifest: dict, sv: dict
) -> bool:
    """Schedule the smoke as a background task IF enabled AND eligible. A cheap
    no-op when MCP_SMOKE_MODE is disabled (the default) — the availability check
    short-circuits before touching a runtime. Never blocks the request. Returns
    True if a task was scheduled."""
    available, _ = smoke_availability()
    if not available:
        return False
    run, _skip, _reason = should_smoke(manifest, sv)
    if not run:
        return False
    background_tasks.add_task(run_and_store_smoke, str(submission_id))
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
                current = sv.get("smoke") or {}
                if current.get("status") == "running" and _is_fresh(
                    current.get("started_at")
                ):
                    return  # another fresh run is in progress
                sv["smoke"] = {
                    "status": "running",
                    "started_at": datetime.now(timezone.utc).isoformat(),
                }
                row.server_verification = sv
                await session.commit()

                manifest = row.manifest_raw or {}
                report = row.verification_report or {}

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
                if result is None:
                    sv2.pop("smoke", None)  # nothing to record; clear the marker
                else:
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
