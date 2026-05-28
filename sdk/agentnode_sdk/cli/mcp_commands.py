"""MCP-specific CLI commands."""
import json
import os
import shutil
import subprocess
import sys

from agentnode_sdk.cli.output import bold, dim
from agentnode_sdk.installer import read_lockfile


def _check(label: str, ok: bool, detail: str) -> None:
    marker = "[OK]" if ok else "[!!]"
    color = "\033[32m" if ok else "\033[31m"
    print(f"  {color}{marker}\033[0m {label}: {detail}")


def cmd_mcp_doctor(
    slug: str,
    json_output: bool = False,
    skip_start: bool = False,
) -> int:
    """Check MCP server readiness for a specific package."""
    checks: list[dict] = []
    failed = 0

    def record(name: str, ok: bool, detail: str, fix: str | None = None):
        nonlocal failed
        checks.append({"check": name, "ok": ok, "detail": detail, "fix": fix})
        if not ok:
            failed += 1
        return ok

    # --- 1. Package installed ---
    lock = read_lockfile()
    pkgs = lock.get("packages", {})
    entry = pkgs.get(slug)

    if entry is None:
        record("installed", False, "not found in lockfile",
               f"agentnode install {slug}")
        if json_output:
            print(json.dumps({"slug": slug, "checks": checks, "ready": False}, indent=2))
        else:
            print()
            print(f"  {bold(slug)} - MCP Health Check")
            print(f"  {'-' * 36}")
            print()
            _check("Package installed", False, "not found")
            print(f"    -> agentnode install {slug}")
            print()
        return 1

    version = entry.get("version", "?")
    record("installed", True, f"v{version}")

    # --- 2. Runtime is MCP ---
    runtime = entry.get("runtime", "")
    if runtime != "mcp":
        record("runtime", False, f"runtime is '{runtime}', not 'mcp'")
        if json_output:
            print(json.dumps({"slug": slug, "checks": checks, "ready": False}, indent=2))
        else:
            print()
            print(f"  {bold(slug)} - MCP Health Check")
            print(f"  {'-' * 36}")
            print()
            _check("Package installed", True, f"v{version}")
            _check("MCP runtime", False, f"{slug} is not an MCP package (runtime: {runtime})")
            print()
        return 1

    record("runtime", True, "mcp")

    # --- 3. Node.js available ---
    node_path = shutil.which("node")
    if node_path is None:
        record("node", False, "not found", "Install Node.js: https://nodejs.org")
        # Can't continue without Node
        if json_output:
            print(json.dumps({"slug": slug, "checks": checks, "ready": False}, indent=2))
        else:
            _render_human(slug, version, checks, failed)
        return 1

    try:
        node_ver = subprocess.run(
            ["node", "--version"], capture_output=True, text=True, timeout=5
        ).stdout.strip()
    except Exception:
        node_ver = "unknown"
    record("node", True, node_ver)

    # --- 4. npx available ---
    npx_path = shutil.which("npx")
    if npx_path is None:
        record("npx", False, "not found", "Install Node.js (includes npx): https://nodejs.org")
        if json_output:
            print(json.dumps({"slug": slug, "checks": checks, "ready": False}, indent=2))
        else:
            _render_human(slug, version, checks, failed)
        return 1
    record("npx", True, "available")

    # --- 5. Environment variables ---
    env_keys = entry.get("mcp_env_keys") or []
    missing_keys = [k for k in env_keys if k not in os.environ]
    if missing_keys:
        for k in missing_keys:
            record("env", False, f"{k} not set", f"export {k}=your-key-here")
    elif env_keys:
        record("env", True, f"{len(env_keys)} key{'s' if len(env_keys) > 1 else ''} set")
    # No env_keys declared -> skip silently

    # --- 6. MCP starts + handshake ---
    if not skip_start and failed == 0:
        mcp_command = entry.get("mcp_command")
        if not mcp_command:
            record("start", False, "no mcp_command in lockfile")
        else:
            try:
                from agentnode_sdk.runtimes.mcp_runner import MCPServerProcess, _mcp_env
                server = MCPServerProcess(slug, mcp_command)
                server.start(timeout=15, env_keys=env_keys)
                server.stop()
                record("start", True, "server starts and handshake passed")
            except Exception as e:
                err = str(e)
                if len(err) > 120:
                    err = err[:120] + "..."
                record("start", False, f"failed: {err}")
    elif skip_start:
        checks.append({"check": "start", "ok": None, "detail": "skipped (--skip-start)", "fix": None})

    ready = failed == 0

    if json_output:
        print(json.dumps({"slug": slug, "checks": checks, "ready": ready}, indent=2))
        return 0 if ready else 1

    _render_human(slug, version, checks, failed)
    return 0 if ready else 1


def _render_human(slug: str, version: str, checks: list[dict], failed: int) -> None:
    print()
    print(f"  {bold(slug)} - MCP Health Check")
    print(f"  {'-' * 36}")
    print()

    for c in checks:
        if c["ok"] is None:
            print(f"  \033[33m[--]\033[0m {c['check']}: {c['detail']}")
        elif c["ok"]:
            _check(c["check"], True, c["detail"])
        else:
            _check(c["check"], False, c["detail"])
            if c.get("fix"):
                print(f"    -> {c['fix']}")

    print()
    if failed == 0:
        print(f"  \033[32mReady to run:\033[0m")
        print(f"  agentnode run {slug} --input '{{\"query\": \"test\"}}'")
    else:
        print(f"  {failed} issue{'s' if failed > 1 else ''} found. Fix {'them' if failed > 1 else 'it'}, then run doctor again.")
    print()
