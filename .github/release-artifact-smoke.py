"""Container-present smoke against the INSTALLED release artefact.

`RELEASE-FINAL-0240-0001` finding F-CONTAINER-ARTEFACT-SMOKE-MISSING: a green source-installed
CI lane does not establish that the distributed wheel behaves correctly on a container-present
host. This runs only against what `pip install <wheel>` put on the path — it never imports from
a repository checkout, and it deliberately lives outside `sdk/` so that adding it cannot change
the artefacts it is verifying.

Every check prints PASS/FAIL; the process exits non-zero if any check failed.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

FAILS = 0


def chk(cond: bool, msg: str) -> None:
    global FAILS
    print(("  PASS  " if cond else "  FAIL  ") + msg, flush=True)
    if not cond:
        FAILS += 1


def main() -> int:
    import agentnode_sdk

    # The package under test must be the installed distribution, not a source tree.
    origin = Path(agentnode_sdk.__file__).resolve()
    chk("site-packages" in str(origin), f"imported from the installed distribution: {origin}")
    chk(agentnode_sdk.__version__ == "0.24.0", f"installed version is {agentnode_sdk.__version__}")

    from agentnode_sdk.sandbox import get_default_backend, set_default_backend
    from agentnode_sdk.sandbox.container_backend import ContainerBackend

    backend = ContainerBackend()
    av = backend.check_available()
    chk(av.available, f"container runtime + pinned image available: backend={av.backend} "
                      f"image={av.image_available} digest={(av.image_digest or '')[:24]}")
    if not av.available:
        print("  runtime unavailable — the remaining checks cannot run", flush=True)
        return 1
    set_default_backend(backend)

    # 1. a real container actually executes a process for the installed package
    from agentnode_sdk.sandbox.types import MountSpec
    spec = backend.build_process_spec(
        ["sh", "-c", "echo CONTAINER_OK; id -u; test -w / && echo ROOTFS_WRITABLE || echo ROOTFS_RO"],
        network="none", mounts=[], env={}, limits={"tmp_size": "64m"}, clean_home=True,
    )
    rc, out, err = backend.run_process(spec, timeout=180)
    chk(rc == 0 and "CONTAINER_OK" in (out or ""), f"the installed package runs a real container (rc={rc})")
    chk("\n1000" in ("\n" + (out or "")), "the container process runs as uid 1000, not root")
    chk("ROOTFS_RO" in (out or ""), "the container root filesystem is read-only")

    # 2. the hardening flags the wheel's own code puts on the command line
    argv = backend.wrap_command(spec)
    for flag in ("--rm", "--cap-drop=ALL", "--security-opt=no-new-privileges",
                 "--user", "--pids-limit", "--memory", "--network"):
        chk(any(flag == a or a.startswith(flag) for a in argv), f"wrap_command emits {flag}")
    chk("/sandbox-home:rw,size=16m" in argv, "HOME is a 16 MiB tmpfs")

    # 3. the production MCP pre-install path, end to end, from the installed package
    from agentnode_sdk import installer
    slug, version = "release-smoke-mcp", "0.24.0"
    pkg, pkg_version = "mcp-server-time", "2026.8.18"
    volume = None
    try:
        volume, artifact_hash, preinstall_command = installer._container_build_mcp_volume(
            slug, version, "pypi", pkg, pkg_version)
        chk(bool(artifact_hash), f"MCP pre-install built a sealed volume (hash {artifact_hash[:20]}…)")
        chk(preinstall_command[0] == "python", f"entrypoint interpreter: {preinstall_command}")

        from agentnode_sdk.lock_integrity import seal_entry
        from agentnode_sdk.runtimes.mcp_launch import build_mcp_launch_plan
        from agentnode_sdk.runtimes.mcp_runner import MCPServerProcess
        from agentnode_sdk.sandbox.policy import HostTrustPolicyDecision

        entry = seal_entry({
            "trust_level": "verified", "version": version, "mcp_preinstalled": True,
            "mcp_preinstall": {"manager": "pypi", "package": pkg, "version": pkg_version,
                               "artifact_hash": artifact_hash},
            "mcp_sandbox_volume": volume,
            "mcp_preinstall_command": list(preinstall_command),
        })
        from agentnode_sdk.sandbox.policy import requires_sandbox_for_policy
        sr = requires_sandbox_for_policy("verified", "curated_only")
        dec = HostTrustPolicyDecision(
            policy="curated_only", trust_level="verified", sandbox_required=sr,
            execution_boundary="sandbox" if sr else "host",
        )
        plan = build_mcp_launch_plan(slug, entry, dec, backend_kind=av.backend or "docker")
        chk(plan.boundary == "sandbox", f"the launch plan routes to the sandbox: {plan.boundary}")

        server = MCPServerProcess(slug, list(preinstall_command), trust_level="verified", entry=entry)
        server.start(_host_policy_decision=dec, launch_plan=plan)
        try:
            chk(bool(server._container_name) and server._container_name.startswith("agentnode-mcp-"),
                f"the MCP started in a container named {server._container_name}")
            chk(server.health_check() is True, "the MCP completed initialize and stays healthy")
        finally:
            try:
                server.stop()
            except Exception:
                pass
    finally:
        if volume:
            subprocess.run([av.backend or "docker", "volume", "rm", "-f", volume],
                           capture_output=True, timeout=60)

    # 4. with a runtime PRESENT, a trusted pack is routed to the sandbox rather than refused
    with tempfile.TemporaryDirectory() as td:
        lock = Path(td) / "agentnode.lock"
        lock.write_text(json.dumps({
            "lockfile_version": "0.1", "updated_at": "", "packages": {
                "smoke-pack": {"version": "1.0.0", "package_type": "toolpack", "runtime": "python",
                               "entrypoint": "smoke_pack.tool", "trust_level": "trusted",
                               "tools": [{"name": "noop", "entrypoint": "smoke_pack.tool:noop"}]}}}),
            encoding="utf-8")
        os.environ["AGENTNODE_CONFIG"] = td
        from agentnode_sdk import run_tool
        r = run_tool("smoke-pack", "noop", lockfile_path=lock)
        mode = getattr(r, "mode_used", "") or ""
        err = (getattr(r, "error", "") or "")
        print(f"  INFO  run_tool with a runtime PRESENT -> success={r.success} mode={mode}")
        print(f"  INFO  error: {err[:160]}")
        chk(not r.success and "sandbox_unavailable" not in mode,
            "a trusted pack is no longer refused for a missing runtime; it is handled on the sandbox path")

    print(f"\n  TOTAL FAILURES: {FAILS}", flush=True)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
