"""ContainerBackend: docker/podman detection + hardened command wrapping.

P0.1 implements detection (`check_available`, cached, no image pull) and the pure
`wrap_command` (argv construction). It does NOT execute anything —
`run_process`/`run_mcp_process` are P0.3/P0.2.
"""
from __future__ import annotations

import shutil
import subprocess

from agentnode_sdk.sandbox.backend import SandboxBackend
from agentnode_sdk.sandbox.types import ProcessSpec, SandboxAvailability

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
