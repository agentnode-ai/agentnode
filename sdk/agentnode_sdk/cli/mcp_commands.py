"""MCP-specific CLI commands."""
import json
import os
import shutil
import subprocess
import sys

from agentnode_sdk.cli.output import bold, dim


def _check(label: str, ok: bool, detail: str) -> None:
    marker = "[OK]" if ok else "[!!]"
    color = "\033[32m" if ok else "\033[31m"
    print(f"  {color}{marker}\033[0m {label}: {detail}")


def cmd_mcp_revoke(slug: str | None = None, revoke_all: bool = False,
                   key: str | None = None, json_output: bool = False) -> int:
    """Revoke stored consent grant(s) for a credentialed MCP (Stage 3B-1). Immediate.
    Never reads or prints a secret value (none are stored)."""
    from agentnode_sdk.runtimes import mcp_consent_store as store
    try:
        if revoke_all:
            n = store.revoke_all()
        elif slug:
            n = store.revoke(slug, key=key)
        else:
            print("Specify an MCP <slug> or --all.", file=sys.stderr)
            return 1
    except store.GrantStoreError as e:
        print(f"Grant store error: {e}", file=sys.stderr)
        return 1
    print(f"Revoked {n} consent grant(s).")
    return 0


def cmd_mcp_grants(json_output: bool = False) -> int:
    """List stored consent grants — METADATA ONLY (slug/version/lifetime/domains/key-NAMES).
    Never displays a secret value (none are stored)."""
    from agentnode_sdk.runtimes import mcp_consent_store as store
    try:
        grants = store.list_grants()
    except store.GrantStoreError as e:
        print(f"Grant store error: {e}", file=sys.stderr)
        return 1
    view = [{
        "slug": g.get("slug"),
        "version": g.get("version"),
        "lifetime": g.get("lifetime"),
        "expires_at": g.get("expires_at"),
        "revoked": bool(g.get("revoked")),
        "env_key_names": g.get("env_key_names"),
        "allowed_domains": g.get("allowed_domains"),
        "consent_key": (g.get("consent_key") or "")[:12],
    } for g in grants]
    if json_output:
        print(json.dumps(view, indent=2))
        return 0
    if not view:
        print("No consent grants.")
        return 0
    for v in view:
        if v["revoked"]:
            status = "revoked"
        elif v["expires_at"] is None:
            status = "forever"
        else:
            status = "active"
        print(f"  {v['slug']}@{v['version']} [{v['lifetime']}/{status}] "
              f"keys={v['env_key_names']} domains={v['allowed_domains']} ck={v['consent_key']}…")
    return 0


def cli_consent_callback(identity):
    """Interactive (TTY) consent prompt for a credentialed MCP. Returns ``(approved, lifetime)``
    for the Stage 3B-1 resolver. Shows env-key NAMES + sealed domains + sealed artifact_hash +
    lifetime presets — NEVER a secret value (there is no value channel here).

    Stage 3B-2a: this UI exists and is unit-tested but is NOT wired into the live MCP start path
    (credentialed execution stays refused; the live secret flow is 3B-2b). ``forever`` is an
    explicit ADVANCED choice (never the default) and requires a typed confirmation; any
    unrecognized answer denies (fail-closed)."""
    from agentnode_sdk.runtimes import mcp_consent_store as store
    from agentnode_sdk.runtimes.mcp_consent import redact_env_keys

    dom = ", ".join(identity.allowed_domains) if identity.allowed_domains else "(none)"
    print(f"\n  MCP '{identity.slug}' v{identity.version} requests credentials.", file=sys.stderr)
    print(f"    env keys : {redact_env_keys(identity.env_key_names)}", file=sys.stderr)
    print(f"    egress   : {dom}", file=sys.stderr)
    print(f"    artifact : {identity.artifact_hash}", file=sys.stderr)
    print("  Grant access for how long?", file=sys.stderr)
    print("    [1] this run only (no saved grant)", file=sys.stderr)
    print("    [2] 7 days", file=sys.stderr)
    print("    [3] 30 days", file=sys.stderr)
    print("    [4] 90 days (default)", file=sys.stderr)
    print("    [5] forever, until revoked  (ADVANCED — authorizes FUTURE non-interactive runs)",
          file=sys.stderr)
    print("    [n] deny", file=sys.stderr)
    try:
        ans = input("  Choice [1-5/N, default 4]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("", file=sys.stderr)
        return (False, store.DEFAULT_LIFETIME)
    if ans in ("n", "no"):
        return (False, store.DEFAULT_LIFETIME)
    if ans == "5":
        try:
            confirm = input(
                "  'forever' keeps this grant until you revoke it and authorizes future "
                "non-interactive runs. Type 'forever' to confirm: "
            ).strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("", file=sys.stderr)
            return (False, store.DEFAULT_LIFETIME)
        if confirm != "forever":
            return (False, store.DEFAULT_LIFETIME)
        return (True, store.LIFETIME_FOREVER)
    choice = {
        "": store.LIFETIME_90D, "4": store.LIFETIME_90D,
        "1": store.LIFETIME_THIS_RUN, "2": store.LIFETIME_7D, "3": store.LIFETIME_30D,
    }.get(ans)
    if choice is None:
        return (False, store.DEFAULT_LIFETIME)  # unrecognized ⇒ deny (fail-closed)
    return (True, choice)


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

    # --- 1. Package installed + two-stage integrity gate ---
    # ONE fail-closed read → resolve the entry → integrity gate from the SAME
    # snapshot, BEFORE any Node/npx probe or MCP server start. A malformed/
    # unreadable lockfile or an absent package fails closed here; a strict-mode
    # integrity denial refuses without starting or connecting to anything. This
    # path bypasses run_tool, so it carries its own gate (same core matrix).
    from agentnode_sdk.exceptions import LockfileFormatError
    from agentnode_sdk.guard import _is_strict
    from agentnode_sdk.lock_integrity import LockIntegrityDenied
    from agentnode_sdk.runner import _audit_lock_integrity, _warn_lock_integrity
    from agentnode_sdk.runtime_integrity import gate_lock_integrity

    def _doctor_error(detail: str) -> int:
        if json_output:
            print(json.dumps({"slug": slug, "error": detail, "ready": False}, indent=2))
        else:
            print()
            print(f"  {bold(slug)} - MCP Health Check")
            print(f"  {detail}")
            print()
        return 1

    try:
        _lock, entry, report = gate_lock_integrity(slug, None, strict=_is_strict())
    except LockfileFormatError as e:
        return _doctor_error(f"Lockfile error: {e}")
    except LockIntegrityDenied as denied:
        _audit_lock_integrity(denied.report, slug)
        if denied.report.entry_status == "absent":
            # Preserve the existing "not installed / not found" contract.
            record("installed", False, "not found in lockfile", f"agentnode install {slug}")
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
        return _doctor_error(
            f"Integrity check denied (strict): entry={denied.report.entry_status}, "
            f"structure={denied.report.structure_status}. Run 'agentnode lock verify'."
        )

    # Allowed but not fully verified → exactly one migration warning + one audit,
    # then the normal doctor flow proceeds (start included).
    if report.reason != "verified":
        _warn_lock_integrity(slug, report)
        _audit_lock_integrity(report, slug)

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

    # --- 2b. Host-trust policy snapshot (F1) ---
    # The doctor OWNS its host-trust snapshot for the direct (non-run_tool) MCP
    # start. Read+validate ONCE here — an invalid sandbox.host_trust_policy fails
    # closed with a dedicated 'policy' check + exactly one sandbox_policy_check
    # deny audit, BEFORE any Node/npx/process/network probe or server start.
    from agentnode_sdk.config import read_host_trust_policy_snapshot
    from agentnode_sdk.exceptions import ConfigurationError
    try:
        policy_snapshot = read_host_trust_policy_snapshot()
    except ConfigurationError:
        from agentnode_sdk.runner import _audit_sandbox_policy_denied
        _audit_sandbox_policy_denied(entry.get("trust_level"), slug, "mcp")
        record(
            "policy", False, "invalid sandbox.host_trust_policy",
            "agentnode config set sandbox.host_trust_policy <default|curated_only|none>",
        )
        if json_output:
            print(json.dumps({"slug": slug, "checks": checks, "ready": False}, indent=2))
        else:
            _render_human(slug, version, checks, failed)
        return 1

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
                from agentnode_sdk.runtimes.mcp_launch import build_mcp_launch_plan
                from agentnode_sdk.runtimes.mcp_runner import MCPServerProcess
                from agentnode_sdk.sandbox import enforce_sandbox_policy
                # Build the routing decision from the doctor's own validated snapshot
                # (community MCPs are sandboxed or fail-closed, never started directly
                # on the host). A no-runtime SandboxRequiredError is caught below as a
                # start failure, exactly as before.
                server = MCPServerProcess(slug, mcp_command, trust_level=entry.get("trust_level"))
                decision = enforce_sandbox_policy(entry.get("trust_level"), host_policy=policy_snapshot)
                backend_kind = None
                if decision.execution_boundary == "sandbox":
                    from agentnode_sdk.sandbox import get_default_backend
                    backend_kind = get_default_backend().check_available().backend or None
                plan = build_mcp_launch_plan(slug, entry, decision, backend_kind=backend_kind)
                server.start(timeout=15, env_keys=env_keys, _host_policy_decision=decision, launch_plan=plan)
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
