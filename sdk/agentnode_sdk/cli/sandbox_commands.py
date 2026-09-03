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

    # EM-3B-R1/R3: the refusal and this output come from ONE classifier, so the doctor cannot
    # drift from what the SDK tells a caller. `fix` is the first action that applies here.
    from agentnode_sdk.sandbox.refusal import RefusalCase, classify

    backend = ContainerBackend()
    av = backend.check_available()
    placeholder = _is_placeholder_digest(_BASE_IMAGE)
    refusal = classify(av, placeholder=placeholder)
    checks: list[dict] = []

    def rec(name, ok, detail, fix=None):
        checks.append({"check": name, "ok": ok, "detail": detail, "fix": fix})

    def first_action() -> str | None:
        """Every action that applies on this platform, in one line.

        Not just the first: the Windows start advice ends with the firmware and WSL2 case, and a
        person whose engine will not start needs that as much as the first sentence.
        """
        if refusal is None or not refusal.actions:
            return None
        parts = []
        for a in refusal.actions:
            parts.append(a.text + (f" ({a.command or a.url})" if (a.command or a.url) else ""))
        return "; ".join(parts)

    if av.backend == "none":
        rec("runtime", False, "no Docker or Podman found", first_action()
            or "Install Docker or Podman (NOT auto-installed): https://docs.docker.com/get-docker/")
        return checks, False, False

    rec("runtime", True, f"{av.backend} ({av.executable_path or 'on PATH'})")

    if not av.daemon_ok:
        permitted = refusal is not None and refusal.case is RefusalCase.NOT_PERMITTED
        rec("daemon", False,
            f"{av.backend} is installed but this account may not use it" if permitted
            else f"{av.backend} found but its daemon is not reachable",
            first_action()
            or ("Start the container runtime (e.g. Docker Desktop). On Windows this can also be "
                "WSL2/Hyper-V not running or hardware virtualization disabled in BIOS."))
        return checks, False, False
    rec("daemon", True, "reachable")

    if refusal is not None and refusal.case is RefusalCase.INCOMPATIBLE:
        rec("engine", False, refusal.headline, first_action())
        return checks, False, False

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


def _infer_build_mode(entry: dict) -> str:
    """build_mode from the lockfile, or inferred for pre-0.18 entries (no field):
    a recorded sandbox volume ⇒ sandbox_volume, otherwise host."""
    bm = entry.get("build_mode")
    if bm:
        return bm
    if entry.get("sandboxed") or entry.get("mcp_preinstalled"):
        return "sandbox_volume"
    return "host"


def _infer_pinnable(entry: dict) -> bool:
    """pinnable from the lockfile, or inferred for pre-0.18 entries: an MCP is
    pinnable iff it was preinstalled (had an mcp_install); toolpacks always are."""
    if "pinnable" in entry:
        return bool(entry["pinnable"])
    if entry.get("runtime") == "mcp":
        return bool(entry.get("mcp_preinstalled"))
    return True


def _doctor_package(slug: str, json_output: bool) -> int:
    import json as _json

    from agentnode_sdk.config import host_trust_policy
    from agentnode_sdk.installer import read_lockfile
    from agentnode_sdk.sandbox.container_backend import ContainerBackend, sandbox_volume_name
    from agentnode_sdk.sandbox.policy import requires_sandbox_for_policy

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
        policy = host_trust_policy()
        host_tier = (trust or "").lower() in ("curated", "trusted")

        if not requires_sandbox_for_policy(trust, policy):
            # host execution under the active policy — no sandbox required.
            note = f"trust '{trust}' runs on the host"
            if policy != "default":
                note += f" (allowed by sandbox.host_trust_policy={policy})"
            rec("isolation", True, note + " — no sandbox required")
            ready = True
            summary = [f"\033[32mReady\033[0m — runs on the host (tier '{trust}'); no sandbox needed."]
        else:
            # sandbox-required: community, OR a normally-host tier that the ACTIVE
            # host-trust policy sandboxes.
            if host_tier:
                rec("policy", None,
                    f"tier '{trust}' is sandboxed by sandbox.host_trust_policy={policy}")
                if policy == "none":
                    rec("system-warning", None,
                        "under 'none', curated/system packages that need host access may break",
                        "if a curated/system package stops working, set "
                        "sandbox.host_trust_policy=curated_only")
            else:
                rec("isolation", None,
                    f"community (trust '{trust or 'unknown'}') — execution REQUIRES the sandbox")

            # A4: a trusted/curated AGENT sandboxed by the policy runs the STRICT
            # community profile — flag the breaking expectations (host FS / broad
            # tools / LLM keys / network) that a host run would have granted it.
            if pkg_type == "agent" and host_tier:
                rec("agent_profile", None,
                    "sandboxed agents run the strict profile: declared tools only, "
                    "default-deny host-brokered LLM, network=none, read-only /pack — a "
                    "trusted/curated agent expecting host FS, broad tools, LLM keys or "
                    "network may break (see docs/security/host-trust-policy.md)")

            checks2, env_ready, _ = _build_env_checks()
            checks.extend(checks2)  # fold env checks under the package report
            if not env_ready:
                fail = _first_failure(checks2)
                ready = False
                nxt = (fail or {}).get("fix") or "see `agentnode sandbox doctor`"
                summary = [
                    "This package \033[31mneeds the sandbox\033[0m under the current policy.",
                    f"Currently missing: {(fail or {}).get('detail', 'unknown')}.",
                    f"Next step: {nxt}",
                ]
            elif runtime_kind == "mcp":
                # pinnable (mcp_install present) ⇒ a sealed volume exists. Not pinnable ⇒
                # the PUBLISHER must pin it — reinstalling cannot fix this (distinct guidance).
                if _infer_pinnable(entry):
                    rec("pinned", True, "pinned (mcp_install) — runs sandboxed")
                    ready = True
                    summary = ["\033[32mReady\033[0m — this MCP will run sandboxed."]
                else:
                    rec("pinned", False, "not pinned — ships no mcp_install descriptor",
                        f"the PUBLISHER must add a pinned mcp_install to '{slug}' — "
                        "reinstalling cannot fix this")
                    ready = False
                    summary = [
                        f"'{slug}' cannot run sandboxed: it ships no pinned mcp_install.",
                        "Not user-fixable — the publisher must pin it (or keep the publisher at a "
                        f"host-allowed tier under sandbox.host_trust_policy={policy}).",
                    ]
            elif pkg_type != "skill" and host_tier and _infer_build_mode(entry) == "host":
                # build-vs-policy mismatch: host-built (under an older/looser policy) but the
                # active policy now sandboxes it → no volume exists → reinstall to build it.
                installed_under = entry.get("effective_host_trust_policy_at_install", "unknown")
                rec("build_volume", False,
                    f"built for the host (policy at install: '{installed_under}') — no sandbox volume",
                    f"agentnode install {slug}   (rebuild in the sandbox under {policy})")
                ready = False
                summary = [
                    f"'{slug}' was built for the host, but sandbox.host_trust_policy={policy} "
                    "now requires it sandboxed.",
                    f"Next step: reinstall — agentnode install {slug}",
                ]
            elif pkg_type != "skill":
                # toolpack already meant for the sandbox: the build volume must exist + match.
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
                # skill (or other) with the sandbox ready
                ready = True
                summary = ["\033[32mReady\033[0m — this package will run sandboxed."]

    if json_output:
        print(_json.dumps({"mode": "package", "slug": slug, "checks": checks, "ready": ready}, indent=2))
        return 0 if ready else 1

    _render(f"Sandbox Doctor — {slug}", checks, summary)
    return 0 if ready else 1
