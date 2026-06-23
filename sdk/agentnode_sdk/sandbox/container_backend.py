"""ContainerBackend: docker/podman detection + hardened command wrapping.

P0.1 implements detection (`check_available`, cached, no image pull) and the pure
`wrap_command` (argv construction). It does NOT execute anything —
`run_process`/`run_mcp_process` are P0.3/P0.2.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import threading

from agentnode_sdk.sandbox.agent_session import AgentSandboxSession
from agentnode_sdk.sandbox.backend import SandboxBackend
from agentnode_sdk.sandbox.types import ProcessSpec, SandboxAvailability, SandboxRequiredError


def sandbox_volume_name(slug: str, version: str | None, artifact_hash: str | None) -> str:
    """Deterministic per-pack-version sandbox volume name.

    ``agentnode-pack-<slug>-<version>-<artifact_hash_short>`` — sanitized to the
    docker/podman volume-name charset. The hash short ties the cache to the EXACT
    verified artifact, so a different artifact (even same slug+version) yields a
    different volume and can never silently reuse a stale build. The run path
    recomputes this name from the lockfile fields and compares before trusting a
    volume (see ``python_runner._run_container``).
    """
    short = (artifact_hash or "").split(":")[-1][:8] or "nohash"
    base = re.sub(r"[^a-zA-Z0-9_.-]", "-", f"{slug}-{version or '0'}").strip("-._") or "pack"
    return f"agentnode-pack-{base}-{short}"


def mcp_sandbox_volume_name(
    slug: str, version: str | None, manager: str, package: str, pkg_version: str
) -> str:
    """Deterministic per-MCP-preinstall sandbox volume name (Stage 4A).

    ``agentnode-mcp-<slug>-<version>-<ident12>`` where ``ident12`` is a sha256 over
    slug+version+manager+package+pkg_version. The name is **descriptor-bound** (those
    inputs) — a different descriptor never reuses another's volume. The built-tree
    CONTENT is bound separately via the sealed ``mcp_preinstall.artifact_hash``; the
    run-time content↔hash verification is Stage 4B. Mirrors ``sandbox_volume_name``.
    """
    ident = f"{slug}|{version or '0'}|{manager}|{package}|{pkg_version}"
    short = hashlib.sha256(ident.encode("utf-8")).hexdigest()[:12]
    base = re.sub(r"[^a-zA-Z0-9_.-]", "-", f"{slug}-{version or '0'}").strip("-._")[:40] or "mcp"
    return f"agentnode-mcp-{base}-{short}"

# Pinned base image, by DIGEST (never a tag, never :latest, never auto-pull).
# ACTIVATED 2026-06-03: built on the Hetzner host, pushed to GHCR, and pinned here
# in the SAME state where routing is already active (P0.2/P0.3) — the guardrail.
# The image is acquired only by an explicit `agentnode sandbox pull` (no auto-pull);
# if it is absent/unpullable on a host, `_image_present()` -> `check_available()`
# stays False and community execution remains fail-closed (never a host fallback).
# Build/push/pin procedure + reproducible-build note: sdk/sandbox-image/README.md.
_BASE_IMAGE = (
    "ghcr.io/agentnode-ai/sandbox@sha256:"
    "6c77561965dc9e98ed9cd0437c4de9aa9171cd3753ae9f11672450ce3125c80f"
)

# Hardened flags, mirrored from backend/app/verification/sandbox.py (proven recipe).
# Never --privileged; never mount the docker socket.
_HARDENED_FLAGS = [
    "--rm",
    "--read-only",
    "--cap-drop=ALL",
    "--security-opt=no-new-privileges",
    "--user", "1000:1000",
    "--pids-limit", "256",
    "--memory", "512m",
    "--cpus", "1",
]

_PROBE_TIMEOUT = 5  # seconds; check_available must stay fast (no long ops, no pull)


class ContainerBackend(SandboxBackend):
    def __init__(self, runtime: str | None = None, image: str = _BASE_IMAGE) -> None:
        self._runtime = runtime          # force a runtime (tests); else auto-detect
        self._image = image
        self._cached: SandboxAvailability | None = None

    # -- detection -----------------------------------------------------------

    def check_available(self) -> SandboxAvailability:
        if self._cached is None:
            self._cached = self._probe()
        return self._cached

    def _probe(self) -> SandboxAvailability:
        candidates = [self._runtime] if self._runtime else ["docker", "podman"]
        for rt in candidates:
            path = shutil.which(rt)
            if not path:
                continue
            if not self._runtime_ok(path):
                return SandboxAvailability(
                    available=False, backend=rt,
                    reason=f"{rt} found but its daemon is not reachable",
                    executable_path=path, daemon_ok=False,
                )
            image_ok = self._image_present(path)
            return SandboxAvailability(
                available=image_ok, backend=rt,
                reason="" if image_ok else "sandbox image not present (pull required)",
                executable_path=path, daemon_ok=True,
                image_available=image_ok, image_digest=self._image,
            )
        return SandboxAvailability(
            available=False, backend="none",
            reason="no container runtime (docker or podman) found on PATH",
        )

    def _runtime_ok(self, path: str) -> bool:
        try:
            r = subprocess.run([path, "info"], capture_output=True, timeout=_PROBE_TIMEOUT)
            return r.returncode == 0
        except Exception:
            return False

    def _image_present(self, path: str) -> bool:
        try:
            r = subprocess.run(
                [path, "image", "inspect", self._image],
                capture_output=True, timeout=_PROBE_TIMEOUT,
            )
            return r.returncode == 0
        except Exception:
            return False

    # -- pure argv construction (no execution) -------------------------------

    def wrap_command(self, spec: ProcessSpec) -> list[str]:
        rt = self._runtime
        if not rt and self._cached and self._cached.backend != "none":
            rt = self._cached.backend
        rt = rt or "docker"

        argv = [rt, "run"]
        if spec.interactive:
            argv.append("-i")
        argv += list(_HARDENED_FLAGS)

        # Writable /tmp under the read-only rootfs; size overridable via
        # spec.limits["tmp_size"] (the toolpack BUILD bumps this for large PEP-517
        # builds; MCP/toolpack runs keep the 64m default).
        tmp_size = (spec.limits or {}).get("tmp_size", "64m")
        argv += ["--tmpfs", f"/tmp:rw,noexec,nosuid,size={tmp_size}"]

        if spec.name:
            argv += ["--name", spec.name, "--label", "agentnode-sandbox"]

        # Network modes are EXPLICIT and fail-closed: an unknown value must never
        # silently fall through to open networking.
        if spec.network == "none":
            argv += ["--network", "none"]
        elif spec.network == "restricted":
            argv += ["--network", "bridge"]  # P0.2 refines to a real egress policy
        elif spec.network == "egress":
            # Design A (proven in Stage 0A): join a pre-created --internal network
            # (no host/internet route); the only egress is a dual-homed CONNECT proxy.
            # Stage 1 is INERT — it only builds argv; the network + proxy are created
            # by Stage 2. Fail-closed: no handle -> raise, never an open-network argv.
            eg = spec.egress
            if eg is None or not eg.network_name or not eg.proxy_url:
                raise SandboxRequiredError(
                    "network='egress' needs an EgressSpec with network_name + proxy_url "
                    "(a pre-created internal network + dual-homed proxy) — refusing to "
                    "emit an open-network argv for egress-restricted code."
                )
            argv += ["--network", eg.network_name]
        elif spec.network == "default":
            pass  # explicit: open network (no --network flag)
        else:
            raise SandboxRequiredError(
                f"unknown sandbox network mode {spec.network!r} — refusing "
                "(fail-closed; never default to open networking)."
            )

        # Clean HOME — the host home (~/.agentnode, .ssh, browser, APPDATA) is
        # NEVER mounted. A fresh ephemeral home is provided instead.
        if spec.clean_home:
            home_size = (spec.limits or {}).get("home_size", "16m")
            argv += ["-e", "HOME=/sandbox-home",
                     "--tmpfs", f"/sandbox-home:rw,size={home_size}"]

        for m in spec.mounts:  # explicit mounts only
            argv += ["-v", f"{m.src}:{m.dst}:{'ro' if m.read_only else 'rw'}"]
        # In egress mode the proxy env is CONTROLLED below — never let a
        # caller-supplied proxy var override the egress routing (not the security
        # boundary, but prevents wrong routing).
        _proxy_keys = ("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
                       "http_proxy", "https_proxy", "no_proxy")
        for k, v in spec.env.items():
            if spec.network == "egress" and k in _proxy_keys:
                continue
            argv += ["-e", f"{k}={v}"]
        if spec.network == "egress" and spec.egress is not None:
            purl = spec.egress.proxy_url
            for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
                argv += ["-e", f"{var}={purl}"]
            argv += ["-e", "NO_PROXY=", "-e", "no_proxy="]

        argv.append(self._image)
        argv += list(spec.command)
        return argv

    # -- one-shot execution (P0.3) -------------------------------------------

    def run_process(
        self,
        spec: ProcessSpec,
        input_text: str | None = None,
        timeout: float = 120.0,
    ) -> tuple[int, str, str]:
        """Build the hardened argv and run it once, capturing stdout/stderr.

        Returns ``(returncode, stdout, stderr)``. A timeout returns
        ``(-1, partial_stdout, stderr + marker)`` so callers can distinguish it.
        Used for BOTH the toolpack build (pip install into the volume) and the
        per-call run (``python -c <wrapper>``, JSON stdin → JSON stdout).
        """
        argv = self.wrap_command(spec)
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            out, err = proc.communicate(input=input_text, timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, err = proc.communicate()
            return -1, out or "", (err or "") + f"\n[sandbox timed out after {timeout}s]"
        return proc.returncode, out or "", err or ""

    # -- long-lived agent session (B1) ---------------------------------------

    def open_agent_session(self, spec: ProcessSpec) -> AgentSandboxSession:
        """Start the container from ``spec`` and return a bidirectional session.

        Fail-closed: if no runtime/image is available, raise — never run agent
        code on the host. The caller supplies the hardened spec (network=none,
        env={}, the agent volume RO at /pack, clean_home, interactive) and the
        ``python -c <wrapper>`` command.
        """
        avail = self.check_available()
        if not avail.available:
            raise SandboxRequiredError(
                "Agent sandbox requires a container runtime + the pinned image. "
                f"{avail.reason or 'none available'} — refusing to run agent code "
                "on the host."
            )
        argv = self.wrap_command(spec)
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        return _ContainerAgentSession(proc, runtime=avail.backend, name=spec.name)


class _ContainerAgentSession(AgentSandboxSession):
    """A long-lived bidirectional session over a container's stdio (line-framed
    JSON). The agent's own stdout/stderr are redirected inside the wrapper, so the
    container's real stdout carries only protocol lines."""

    def __init__(self, proc: "subprocess.Popen", *, runtime: str | None = None,
                 name: str | None = None):
        self._proc = proc
        self._runtime = runtime
        self._name = name

    def send(self, message: dict) -> None:
        if not self._proc.stdin:
            raise RuntimeError("agent session is not writable")
        self._proc.stdin.write(json.dumps(message) + "\n")
        self._proc.stdin.flush()

    def recv(self, timeout: float) -> dict | None:
        out = self._proc.stdout
        if not out:
            return None
        box: list = [None]
        t = threading.Thread(target=lambda: box.__setitem__(0, out.readline()), daemon=True)
        t.start()
        t.join(timeout)
        if t.is_alive():
            raise TimeoutError(f"no agent message within {timeout}s")
        line = box[0]
        if not line:
            return None
        return json.loads(line)

    def close(self) -> None:
        try:
            if self._proc.poll() is None:
                self._proc.kill()
        except Exception:
            pass
        # Killing the `docker run` client does not reliably stop the container;
        # force-remove by name (best-effort, idempotent under --rm).
        if self._name and self._runtime:
            try:
                subprocess.run(
                    [self._runtime, "rm", "-f", self._name],
                    capture_output=True, timeout=10,
                )
            except Exception:
                pass
