"""`agentnode sandbox` — explicit sandbox image management + diagnosis.

Sprint A: `pull` (explicit, user-initiated; NO auto-pull anywhere in the SDK).
Sprint B: `doctor` / `doctor <slug>` / `status` — DIAGNOSIS + GUIDANCE only.

Hard rules (Sprint B):
- The doctor NEVER starts community code, NEVER installs Docker/Podman, NEVER
  touches WSL2/BIOS, NEVER changes a trust level or an execution path.
- The ONLY action the doctor may take is an explicit, TTY-confirmed
  `agentnode sandbox pull`. With `--json` or no TTY it performs NO action — it
  only prints the command to run.
- Diagnosis (incl. `docker volume inspect`) is read-only.
"""
from __future__ import annotations

import subprocess
import sys

from agentnode_sdk.cli.output import bold, dim


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _is_placeholder_digest(image: str) -> bool:
    """True if `_BASE_IMAGE` still carries the all-zero placeholder digest
    (SDK build with no activated/pinned sandbox image)."""
    return set(image.rsplit(":", 1)[-1]) == {"0"}


def _mark(ok) -> str:
    if ok is None:
        return "\033[33m[--]\033[0m"
    return "\033[32m[OK]\033[0m" if ok else "\033[31m[!!]\033[0m"


def _render(title: str, checks: list[dict], summary: list[str]) -> None:
    print()
    print(f"  {bold(title)}")
    print(f"  {'-' * max(12, len(title) + 2)}")
    print()
    for c in checks:
        print(f"  {_mark(c['ok'])} {c['check']}: {c['detail']}")
        if c["ok"] is False and c.get("fix"):
            print(f"    -> {c['fix']}")
    print()
    for line in summary:
        print(f"  {line}")
    print()


def _first_failure(checks: list[dict]) -> dict | None:
    for c in checks:
        if c["ok"] is False:
            return c
    return None


# ---------------------------------------------------------------------------
# sandbox pull  (Sprint A — explicit, no auto-pull)
# ---------------------------------------------------------------------------

def cmd_sandbox_pull() -> int:
    """Explicitly pull the digest-pinned sandbox image. No auto-pull, no tag."""
    from agentnode_sdk.sandbox.container_backend import _BASE_IMAGE, ContainerBackend

    if _is_placeholder_digest(_BASE_IMAGE):
        print(
            "\n  Sandbox image is not activated yet (placeholder digest).\n"
            "  Community code execution is fail-closed until the image is built,\n"
            "  pushed to GHCR and pinned by digest.\n"
            "  See sdk/sandbox-image/README.md.\n"
        )
        return 1

    avail = ContainerBackend().check_available()
    runtime = avail.backend
    if not runtime or runtime == "none":
        print(
            "\n  No container runtime (Docker or Podman) found.\n"
            f"  {avail.reason or ''}\n"
            "  Install Docker or Podman, then run: agentnode sandbox pull\n"
        )
        return 1

    print(f"\n  Pulling pinned sandbox image with {runtime} (explicit, no auto-pull):")
    print(f"  {_BASE_IMAGE}\n")
    try:
        result = subprocess.run([runtime, "pull", _BASE_IMAGE], timeout=600)
    except FileNotFoundError:
        print(f"  {runtime} not found on PATH.\n")
        return 1
    except subprocess.TimeoutExpired:
        print("  Pull timed out.\n")
        return 1
    if result.returncode != 0:
        print(
            "  Pull failed. If the image is private or you are not authenticated,\n"
            "  log in first: docker login ghcr.io   (otherwise check network/registry access).\n"
        )
        return 1

    after = ContainerBackend().check_available()
    if after.available:
        print("  Sandbox ready. Community packages can now run isolated.\n")
        return 0
    print(
        "  Image pulled, but the sandbox still reports unavailable: "
        f"{after.reason or 'unknown'}.\n"
    )
    return 1


# ---------------------------------------------------------------------------
# sandbox status  (Sprint B — one-line health, no action)
# ---------------------------------------------------------------------------

def cmd_sandbox_status() -> int:
    from agentnode_sdk.sandbox.container_backend import _BASE_IMAGE, ContainerBackend

    av = ContainerBackend().check_available()
    if av.available:
        print("sandbox: ready")
        return 0
    if _is_placeholder_digest(_BASE_IMAGE):
        print("sandbox: needs setup (SDK build has no pinned image)")
    elif av.backend == "none":
        print("sandbox: needs setup (no Docker/Podman found)")
    elif not av.daemon_ok:
        print(f"sandbox: needs setup ({av.backend} daemon not reachable)")
    elif not av.image_available:
        print("sandbox: needs setup (image not pulled — run: agentnode sandbox pull)")
    else:
        print(f"sandbox: needs setup ({av.reason or 'unknown'})")
    return 1


# ---------------------------------------------------------------------------
# sandbox doctor  (Sprint B — diagnosis + guidance)
# ---------------------------------------------------------------------------

def cmd_sandbox_doctor(slug: str | None = None, json_output: bool = False) -> int:
    if slug:
        return _doctor_package(slug, json_output)
    return _doctor_env(json_output)


def _build_env_checks() -> tuple[list[dict], bool, bool]:
    """Return (checks, ready, image_missing_but_runtime_ok). Pure: no side effects
    beyond the cached availability probe."""
    from agentnode_sdk.sandbox.container_backend import _BASE_IMAGE, ContainerBackend

    av = ContainerBackend().check_available()
    placeholder = _is_placeholder_digest(_BASE_IMAGE)
    checks: list[dict] = []

    def rec(name, ok, detail, fix=None):
        checks.append({"check": name, "ok": ok, "detail": detail, "fix": fix})

    if av.backend == "none":
        rec("runtime", False, "no Docker or Podman found",
            "Install Docker or Podman (NOT auto-installed): https://docs.docker.com/get-docker/")
        return checks, False, False

    rec("runtime", True, f"{av.backend} ({av.executable_path or 'on PATH'})")

    if not av.daemon_ok:
        rec("daemon", False, f"{av.backend} found but its daemon is not reachable",
            "Start the container runtime (e.g. Docker Desktop). On Windows this can also be "
            "WSL2/Hyper-V not running or hardware virtualization disabled in BIOS.")
        return checks, False, False
    rec("daemon", True, "reachable")

    if placeholder:
        rec("image", False, "this SDK build has no pinned sandbox image (placeholder digest)",
            "Update AgentNode to a build with an activated sandbox image.")
        return checks, False, False

    if not av.image_available:
        rec("image", False, "pinned sandbox image is not present locally",
            "agentnode sandbox pull")
        return checks, False, True  # runtime+daemon ok, image just not pulled

    rec("image", True, "pinned image present")
    return checks, True, False


def _doctor_env(json_output: bool) -> int:
    import json as _json

    checks, ready, image_missing = _build_env_checks()

    if json_output:
        # --json performs NO action, ever.
        print(_json.dumps({"mode": "env", "checks": checks, "ready": ready}, indent=2))
        return 0 if ready else 1

    if ready:
        summary = ["\033[32mSandbox ready\033[0m — community packages run isolated."]
    else:
        summary = ["Community execution is \033[31mfail-closed\033[0m until the issue above is fixed.",
                   dim("(No host fallback — community code runs isolated or not at all.)")]
    _render("AgentNode Sandbox — Doctor", checks, summary)

    # ONLY action: explicit, TTY-confirmed pull when the image is simply not pulled.
    if image_missing and sys.stdin.isatty():
        print("  Pull the pinned AgentNode sandbox image now? [y/N]")
        print(dim("  This downloads the digest-pinned sandbox image."))
        print(dim("  It does not install Docker/Podman."))
        print(dim("  It does not enable auto-pull."))
        try:
            answer = input("  > ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = ""
        if answer == "y":
            return cmd_sandbox_pull()
        print(dim("\n  Skipped. Run `agentnode sandbox pull` when ready.\n"))

    return 0 if ready else 1


def _doctor_package(slug: str, json_output: bool) -> int:
    import json as _json

    from agentnode_sdk.installer import read_lockfile
    from agentnode_sdk.sandbox import requires_sandbox
    from agentnode_sdk.sandbox.container_backend import (
        _BASE_IMAGE, ContainerBackend, sandbox_volume_name,
    )

    lock = read_lockfile()
    entry = lock.get("packages", {}).get(slug)
    checks: list[dict] = []
    summary: list[str] = []

    def rec(name, ok, detail, fix=None):
        checks.append({"check": name, "ok": ok, "detail": detail, "fix": fix})

    if entry is None:
        rec("installed", False, "not found in lockfile", f"agentnode install {slug}")
        ready = False
        summary = [f"'{slug}' is not installed."]
    else:
        rec("installed", True, f"v{entry.get('version', '?')}")
        trust = entry.get("trust_level")
        pkg_type = entry.get("package_type", "")
        runtime_kind = entry.get("runtime", "")

        if not requires_sandbox(trust):
            # curated/trusted: host execution, no sandbox required.
            rec("isolation", True, f"trust '{trust}' runs on the host — no sandbox required")
            ready = True
            summary = [f"\033[32mReady\033[0m — runs on the host (vetted tier '{trust}'); no sandbox needed."]
        else:
            rec("isolation", None,
                f"community (trust '{trust or 'unknown'}') — execution REQUIRES the sandbox")
            checks2, env_ready, _ = _build_env_checks()
            # fold env checks under the package report
            checks.extend(checks2)
            if not env_ready:
                fail = _first_failure(checks2)
                ready = False
                nxt = (fail or {}).get("fix") or "see `agentnode sandbox doctor`"
                summary = [
                    "This package runs community code and \033[31mneeds the sandbox\033[0m.",
                    f"Currently missing: {(fail or {}).get('detail', 'unknown')}.",
                    f"Next step: {nxt}",
                ]
            elif runtime_kind != "mcp" and pkg_type != "skill":
                # toolpack: the build volume must exist and match.
                vol = sandbox_volume_name(slug, entry.get("version"), entry.get("artifact_hash"))
                recorded_ok = bool(entry.get("sandboxed")) and entry.get("sandbox_volume") == vol
                inspect_ok = False
                if recorded_ok:
                    rt = ContainerBackend().check_available().backend or "docker"
                    try:
                        inspect_ok = subprocess.run(
                            [rt, "volume", "inspect", vol], capture_output=True, timeout=10,
                        ).returncode == 0
                    except Exception:
                        inspect_ok = False
                if recorded_ok and inspect_ok:
                    rec("build_volume", True, "present")
                    ready = True
                    summary = ["\033[32mReady\033[0m — this package will run sandboxed."]
                else:
                    rec("build_volume", False, "build volume missing or stale",
                        f"agentnode install {slug}   (rebuild it in the sandbox)")
                    ready = False
                    summary = [
                        "Sandbox is available, but the build volume is \033[31mmissing or stale\033[0m.",
                        f"Next step: reinstall — agentnode install {slug}",
                    ]
            else:
                # community MCP with sandbox ready
                ready = True
                summary = ["\033[32mReady\033[0m — this package will run sandboxed."]

    if json_output:
        print(_json.dumps({"mode": "package", "slug": slug, "checks": checks, "ready": ready}, indent=2))
        return 0 if ready else 1

    _render(f"Sandbox Doctor — {slug}", checks, summary)
    return 0 if ready else 1
