"""ContainerBackend: docker/podman detection + hardened command wrapping.

P0.1 implements detection (`check_available`, cached, no image pull) and the pure
`wrap_command` (argv construction). It does NOT execute anything —
`run_process`/`run_mcp_process` are P0.3/P0.2.
"""
from __future__ import annotations

import re
import shutil
import subprocess

from agentnode_sdk.sandbox.backend import SandboxBackend
from agentnode_sdk.sandbox.types import ProcessSpec, SandboxAvailability


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

# Pinned base image. The digest is a PLACEHOLDER — the real image is chosen and
# pinned by digest in P0.2 (and `check_available` will require it present).
_BASE_IMAGE = (
    "agentnode/sandbox@sha256:"
    "0000000000000000000000000000000000000000000000000000000000000000"
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
    "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
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

        if spec.name:
            argv += ["--name", spec.name, "--label", "agentnode-sandbox"]

        if spec.network == "none":
            argv += ["--network", "none"]
        elif spec.network == "restricted":
            argv += ["--network", "bridge"]  # P0.2 refines to a real egress policy
        # "default": no network flag

        # Clean HOME — the host home (~/.agentnode, .ssh, browser, APPDATA) is
        # NEVER mounted. A fresh ephemeral home is provided instead.
        if spec.clean_home:
            argv += ["-e", "HOME=/sandbox-home",
                     "--tmpfs", "/sandbox-home:rw,size=16m"]

        for m in spec.mounts:  # explicit mounts only
            argv += ["-v", f"{m.src}:{m.dst}:{'ro' if m.read_only else 'rw'}"]
        for k, v in spec.env.items():
            argv += ["-e", f"{k}={v}"]

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
