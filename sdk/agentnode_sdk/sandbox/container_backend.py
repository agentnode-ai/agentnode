"""ContainerBackend: docker/podman detection + hardened command wrapping.

P0.1 implements detection (`check_available`, cached, no image pull) and the pure
`wrap_command` (argv construction). It does NOT execute anything —
`run_process`/`run_mcp_process` are P0.3/P0.2.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid

from dataclasses import replace

from agentnode_sdk.sandbox.agent_session import AgentSandboxSession
from agentnode_sdk.sandbox.backend import SandboxBackend
from agentnode_sdk.sandbox.types import (
    ProcessSpec,
    SandboxAvailability,
    SandboxContainmentError,
    SandboxRequiredError,
)

# Stage 3B-2a: a valid env-var NAME for name-only secret pass-through (`--env NAME`).
_ENV_PASSTHROUGH_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _is_placeholder(image: str) -> bool:
    """A digest of all zeroes is the marker for a build with no activated image."""
    tail = (image or "").rsplit(":", 1)[-1]
    return bool(tail) and set(tail) == {"0"}


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
    # EM-3B-R1/R2. `--memory` alone is not a ceiling: with no `--memory-swap`, the runtime grants
    # a swap allowance of twice the memory limit, and the conformance suite watched a 768 MiB
    # allocation finish with exit code 0 under a 512 MiB "limit". Equal values mean the total of
    # memory AND swap is the limit, which is the only form of it a payload cannot walk around.
    "--memory-swap", "512m",
    "--cpus", "1",
]

_PROBE_TIMEOUT = 5  # seconds; check_available must stay fast (no long ops, no pull)
_CLIENT_REAP_TIMEOUT = 10   # how long the runtime client gets to die after being killed
_KILL_TIMEOUT = 30          # how long the runtime gets to remove the container
_REAP_TIMEOUT = 30          # how long the container gets to disappear afterwards


def _run_runtime(argv: list, timeout: float = _PROBE_TIMEOUT):
    """Run a runtime command, or return None if it could not be run at all."""
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except Exception:                                              # noqa: BLE001 - deliberate
        return None


def _remove_quietly(path: str, directory: str) -> None:
    for target, remover in ((path, os.unlink), (directory, os.rmdir)):
        try:
            remover(target)
        except OSError:
            pass


class ContainerBackend(SandboxBackend):
    def __init__(self, runtime: str | None = None, image: str = _BASE_IMAGE) -> None:
        self._runtime = runtime          # force a runtime (tests); else auto-detect
        self._image = image
        self._cached: SandboxAvailability | None = None

    # -- detection -----------------------------------------------------------

    def check_available(self, force: bool = False) -> SandboxAvailability:
        """Cached probe. ``force`` re-asks, which is what a re-check after a fix needs."""
        if self._cached is None or force:
            self._cached = self._probe()
        return self._cached

    def _probe(self) -> SandboxAvailability:
        candidates = [self._runtime] if self._runtime else ["docker", "podman"]
        for rt in candidates:
            path = shutil.which(rt)
            if not path:
                continue
            ok, probe_error = self._runtime_ok(path)
            if not ok:
                return SandboxAvailability(
                    available=False, backend=rt,
                    reason=f"{rt} found but its daemon is not reachable",
                    executable_path=path, daemon_ok=False, probe_error=probe_error,
                )
            engine_os, mem_ok = self._engine_facts(path)
            if engine_os not in ("", "linux"):
                return SandboxAvailability(
                    available=False, backend=rt,
                    reason=f"{rt} is running {engine_os} containers; the pinned image needs linux",
                    executable_path=path, daemon_ok=True, engine_os=engine_os,
                    memory_limit_enforceable=mem_ok,
                )
            image_ok = self._image_present(path)
            return SandboxAvailability(
                available=image_ok, backend=rt,
                reason="" if image_ok else "sandbox image not present (pull required)",
                executable_path=path, daemon_ok=True,
                image_available=image_ok, image_digest=self._image,
                engine_os=engine_os, memory_limit_enforceable=mem_ok,
            )
        return SandboxAvailability(
            available=False, backend="none",
            reason="no container runtime (docker or podman) found on PATH",
        )

    def _runtime_ok(self, path: str) -> tuple[bool, str]:
        """(usable, what it said). EM-3B-R1/R3: the message is what tells a permission problem
        apart from a stopped daemon, and the old boolean threw it away."""
        r = _run_runtime([path, "info"])
        if r is None:
            return False, "the runtime did not answer within the probe timeout"
        if r.returncode == 0:
            return True, ""
        return False, ((r.stderr or "") + (r.stdout or "")).strip()[:400]

    def _engine_facts(self, path: str) -> tuple[str, bool | None]:
        """(container OS, whether this engine reports it can hold a memory AND swap ceiling).

        EM-3B-R1/R2: an engine that cannot account for swap cannot enforce the ceiling this
        backend asks for. That is recorded rather than assumed, so a conformance report can say
        so instead of resting on the flag having been passed.
        """
        r = _run_runtime([path, "info", "--format", "{{.OSType}}|{{.MemoryLimit}}|{{.SwapLimit}}"])
        if r is None or r.returncode != 0:
            return "", None
        parts = (r.stdout or "").strip().split("|")
        engine_os = parts[0].strip().lower() if parts else ""
        if len(parts) >= 3:
            mem, swap = parts[1].strip().lower(), parts[2].strip().lower()
            if mem in ("true", "false") and swap in ("true", "false"):
                return engine_os, (mem == "true" and swap == "true")
        return engine_os, None

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

    def refusal(self):
        """The structured refusal for the current state, or None when the sandbox is usable.

        EM-3B-R1/R3. One classifier, so what the SDK says and what the doctor prints cannot drift.
        """
        from agentnode_sdk.sandbox.refusal import classify

        return classify(self.check_available(), placeholder=_is_placeholder(self._image),
                        probe_error=self.check_available().probe_error)

    def explain_unavailable(self) -> str:
        refusal = self.refusal()
        return "" if refusal is None else refusal.render()

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

        # Stage 3B-2a: name-only secret pass-through. Emit `--env NAME` (NO value) — docker reads
        # the value from the controlled docker-client env at run time; the VALUE never lands on
        # argv. Fail-closed: ONLY with network=="egress"; each name must be a valid env-var name
        # and DISJOINT from the literal `env` (a secret name must never be emitted as KEY=value).
        if spec.env_passthrough:
            if spec.network != "egress":
                raise SandboxRequiredError(
                    "env_passthrough requires network='egress' — refusing to pass secrets by "
                    "name on an open/none/restricted network."
                )
            _seen: set[str] = set()
            for name in spec.env_passthrough:
                if not isinstance(name, str) or not _ENV_PASSTHROUGH_NAME.match(name):
                    # VALUE-FREE: never echo the offending entry (no {name!r}, no length, no
                    # prefix). A caller may have mistakenly passed a secret VALUE instead of an
                    # env-var NAME; it must never reach the error message / logs.
                    raise SandboxRequiredError(
                        "invalid env_passthrough name — refusing name-only pass-through "
                        "(offending entry not echoed)."
                    )
                if name in spec.env:
                    raise SandboxRequiredError(
                        "an env_passthrough name is also a literal env key — refusing "
                        "(a secret name must never be emitted as KEY=value)."
                    )
                if name in _seen:
                    continue
                _seen.add(name)
                argv += ["--env", name]

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
        ``(-1, partial_stdout, stderr + marker)`` so callers can distinguish it. Used for BOTH the
        toolpack build (pip install into the volume) and the per-call run.

        **EM-3B-R1.** Killing the ``docker run`` client does not stop the container -- the client
        is a pipe to a daemon that owns the process, and ``--rm`` only removes a container that
        exits. The conformance suite caught the consequence: the SDK reported a timeout while the
        payload kept running. Every run now carries an exact identity (a unique name AND a cidfile)
        and a timeout ends that exact container, waits for the removal, and verifies that neither
        the id nor the name remains. If any of that cannot be shown,
        :class:`SandboxContainmentError` is raised rather than the ordinary timeout being returned:
        a stop nobody could verify is not a stop.
        """
        spec, name, cidfile, tmpdir = self._with_identity(spec)
        argv = self._argv_with_cidfile(spec, cidfile)
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            try:
                out, err = proc.communicate(input=input_text, timeout=timeout)
            except subprocess.TimeoutExpired:
                return self._end_timed_out_run(proc, argv[0], name, cidfile, timeout)
            return proc.returncode, out or "", err or ""
        finally:
            _remove_quietly(cidfile, tmpdir)

    # -- EM-3B-R1: identity, and ending a run that ignored its deadline -------

    def _with_identity(self, spec: ProcessSpec):
        """Give every run an exact identity. Nothing here can target another container.

        A name alone is targetable from the moment the command is built; a cidfile alone is the
        container itself but does not exist until the runtime creates it. Carrying both means the
        timeout path always has something exact to act on, and a disagreement between them is
        itself a containment failure rather than a coin toss.
        """
        name = spec.name or f"agentnode-run-{uuid.uuid4().hex}"
        tmpdir = tempfile.mkdtemp(prefix="agentnode-cid-")
        cidfile = os.path.join(tmpdir, "cid")      # must NOT exist: the runtime creates it
        if spec.name != name:
            spec = replace(spec, name=name)
        return spec, name, cidfile, tmpdir

    def _argv_with_cidfile(self, spec: ProcessSpec, cidfile: str) -> list[str]:
        argv = self.wrap_command(spec)
        return argv[:2] + ["--cidfile", cidfile] + argv[2:]

    def _resolve_identity(self, runtime: str, name: str, cidfile: str):
        """(container id, how it was resolved). Exact lookups only -- never a pattern or a prefix."""
        from_file = ""
        try:
            with open(cidfile, encoding="utf-8") as fh:
                from_file = fh.read().strip()
        except OSError:
            pass
        from_name = ""
        r = _run_runtime([runtime, "inspect", "--format", "{{.Id}}", name])
        if r is not None and r.returncode == 0:
            from_name = r.stdout.strip()
        if from_file and from_name and not (from_file.startswith(from_name)
                                            or from_name.startswith(from_file)):
            raise SandboxContainmentError(
                "the run's cidfile and its name resolve to different containers "
                f"({from_file[:12]} vs {from_name[:12]}) -- refusing to stop either, because "
                "stopping the wrong container is worse than reporting this")
        return (from_file or from_name), ("cidfile" if from_file else
                                          ("name" if from_name else "none"))

    def _exists(self, runtime: str, ident: str) -> bool:
        r = _run_runtime([runtime, "inspect", "--format", "{{.Id}}", ident])
        return bool(r is not None and r.returncode == 0 and r.stdout.strip())

    def _end_timed_out_run(self, proc, runtime: str, name: str, cidfile: str, timeout: float):
        """Stop the client, then the container, then prove the container is gone."""
        try:
            proc.kill()
        except Exception:                                          # noqa: BLE001
            pass
        try:
            out, err = proc.communicate(timeout=_CLIENT_REAP_TIMEOUT)
        except Exception:                                          # noqa: BLE001
            out, err = "", ""
        if proc.poll() is None:
            raise SandboxContainmentError(
                "the runtime client would not terminate after the sandbox timed out, so the "
                "payload cannot be shown to have stopped")

        ident, how = self._resolve_identity(runtime, name, cidfile)
        if not ident:
            # The timeout may have fired before the runtime created anything. That is only
            # acceptable if nothing by this exact name exists.
            if self._exists(runtime, name):
                raise SandboxContainmentError(
                    f"a container named {name} exists but no identity could be resolved for it, "
                    "so this run cannot be shown to have stopped")
            return -1, out or "", (err or "") + f"\n[sandbox timed out after {timeout}s]"

        removed = _run_runtime([runtime, "rm", "-f", ident], timeout=_KILL_TIMEOUT)
        if removed is None:
            raise SandboxContainmentError(
                f"the runtime did not answer the request to stop container {ident[:12]} after the "
                "sandbox timed out")
        if removed.returncode != 0 and self._exists(runtime, ident):
            raise SandboxContainmentError(
                f"stopping container {ident[:12]} failed ({removed.stderr.strip()[:160]}) and it "
                "is still there")

        deadline = time.monotonic() + _REAP_TIMEOUT
        while time.monotonic() < deadline:
            if not self._exists(runtime, ident) and not self._exists(runtime, name):
                return -1, out or "", (err or "") + f"\n[sandbox timed out after {timeout}s]"
            time.sleep(0.1)
        raise SandboxContainmentError(
            f"container {ident[:12]} (resolved by {how}) is still present after being stopped, so "
            "the payload may still be running"
        )

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
