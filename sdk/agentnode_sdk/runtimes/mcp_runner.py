"""MCP server process pool and tool execution.

Manages MCP server subprocesses via stdio JSON-RPC 2.0 protocol.
Each MCP package gets its own persistent server process, reused across calls.
"""
from __future__ import annotations

import atexit
import itertools
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from agentnode_sdk.models import RunToolResult
from agentnode_sdk.policy import PolicyResult, audit_decision

logger = __import__("logging").getLogger("agentnode.mcp_runner")

_request_id = itertools.count(1)


class MCPServerProcess:
    """A managed MCP server subprocess communicating via stdio JSON-RPC."""

    def __init__(self, slug: str, command: list[str], trust_level: str | None = None,
                 entry: dict | None = None, mcp_consent_callback=None):
        self.slug = slug
        self.command = command
        # Safe default: trust_level missing/None/unknown -> sandbox-required, NEVER
        # host (resolved in start() via sandbox.policy). Default must not be a host tier.
        self.trust_level = trust_level
        # Stage 4B: the lockfile entry (preinstall fields + permissions). When it signals
        # preinstall intent, start() runs the fail-closed sealed-volume path.
        self.entry = entry or {}
        # Stage 3B-2b: DEDICATED consent callback (NOT the guard bool callback). Returns
        # (approved, lifetime); injected by the CLI when interactive, None when non-TTY. The
        # runtime never imports the CLI and never calls isatty() — it only uses what is injected.
        self._consent_callback = mcp_consent_callback
        self._process: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._last_used = time.monotonic()
        self._container_name: str | None = None
        self._runtime: str | None = None
        # F1: full launch/boundary compatibility identity, set by the pool AFTER a
        # successful start. A cache hit is reused ONLY when this equals the current
        # call's requested compatibility (None ⇒ legacy ⇒ never reused).
        self.compatibility = None
        # Stage 3B-2b: live egress proxy handle for a credentialed run (bound in
        # _credentialed_launch, torn down in stop()).
        self._egress_handle = None

    def start(
        self,
        timeout: float = 10.0,
        env_keys: list[str] | None = None,
        *,
        _host_policy_decision: Any,
    ) -> None:
        """Start the MCP server subprocess.

        F1: the host/sandbox routing is taken from the immutable
        ``_host_policy_decision`` made ONCE by the owner (``run_tool`` or
        ``cmd_mcp_doctor``) — start() reads no config and re-runs no policy. The
        real runtime availability is still re-probed on the sandbox path
        (fail-closed if it vanished); the policy is never recomputed. Community/
        unverified tiers ⇒ container; curated ⇒ host; trusted ⇒ host (transition).
        """
        from agentnode_sdk.sandbox import get_default_backend
        from agentnode_sdk.sandbox.policy import HostTrustPolicyDecision

        # Decision is mandatory and must match this process's trust; no fallback.
        if not isinstance(_host_policy_decision, HostTrustPolicyDecision):
            raise RuntimeError("MCP start requires a host-trust policy decision")
        if _host_policy_decision.trust_level != (self.trust_level or ""):
            raise RuntimeError("host-trust policy decision does not match MCP trust level")

        # Windows: CREATE_NEW_PROCESS_GROUP for clean shutdown
        kwargs: dict[str, Any] = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

        backend = None
        spec = None
        credentialed = False
        # Boundary from the decision: sandbox ⇒ container path; host ⇒ host path.
        if _host_policy_decision.sandbox_required:
            # Containerized path. P0.2 isolates host-FS, HOME and secrets — NOT the
            # network (npx/uvx fetch live). No host env, no mounts, clean container HOME.
            from agentnode_sdk.sandbox.mcp_preinstall import has_preinstall_intent

            safe_slug = re.sub(r"[^a-zA-Z0-9_.-]", "-", self.slug)[:40]
            name = f"agentnode-mcp-{safe_slug}-{uuid4().hex[:8]}"
            backend = get_default_backend()

            if env_keys:
                # Stage 3B-2b: credentialed community MCP — gated LIVE secret flow, allowed ONLY
                # for a preinstalled + sealed MCP with a valid consent grant/approval AND a sealed
                # egress allowlist. Any gate fails ⇒ CredentialedMcpRefused (fail-closed). This runs
                # the gates, starts the egress proxy, binds the teardown handle, and returns the
                # ProcessSpec ONLY — wrap_command and the FIRST secret-value read are deferred to the
                # cleanup scope below (strictly after a successful wrap_command), so the secret is
                # never read if wrap_command fails and the proxy never leaks. Credentialed
                # non-preinstalled stays refused (no npx/uvx fetch with a secret).
                spec = self._credentialed_launch(backend, name, env_keys)
                credentialed = True
            elif has_preinstall_intent(self.entry):
                # Stage 4B: preinstalled MCP — run from the read-only, integrity-verified,
                # descriptor- AND content-bound sealed volume. FAIL-CLOSED: any problem
                # raises; there is NO fallback to the registry-fetch (npx/uvx) mcp_command
                # path, no key, no auto-created/rebuilt volume, no permissive network.
                spec = self._preinstalled_spec(backend, name)
            else:
                # MCP net-isolation (Fallback C): a non-preinstalled community MCP would have to
                # fetch at runtime (npx/uvx) with an OPEN network — the exact path we refuse. A
                # community MCP runs ONLY when pinned + sealed + network-isolated/allowlisted;
                # unpinnable (floating npx/uvx/latest/git/url) is fail-closed, never open network.
                from agentnode_sdk.sandbox.types import SandboxRequiredError
                raise SandboxRequiredError(
                    f"MCP '{self.slug}' is not preinstalled — cannot run sandboxed "
                    f"(sandbox.host_trust_policy={_host_policy_decision.policy}). Reinstall it "
                    "pinned (exact mcp_install version) and declare egress domains via "
                    "mcp_allowed_domains; unpinnable (floating npx/uvx) MCPs are refused."
                )
            self._container_name = name

        # Single cleanup scope for EVERYTHING that can fail once the egress proxy is up:
        # wrap_command, the credentialed FIRST secret-value read, Popen, and the handshake. Any
        # failure ⇒ self.stop() ⇒ container rm + egress teardown, so no secret-holding container or
        # proxy lingers. ORDER (3B-2b): the secret VALUE is read ONLY after wrap_command has
        # successfully built the `--env NAME` argv.
        try:
            if spec is not None:
                launch = backend.wrap_command(spec)
                self._runtime = launch[0]
                # FIRST + ONLY secret-value read — strictly AFTER a successful wrap_command, and
                # inside this cleanup scope so a fail-closed read (missing/empty key) tears down the
                # egress proxy. Bound to spec.env_passthrough (the CONSENTED identity names that
                # wrap_command just emitted as `--env NAME`), NEVER the raw env_keys argument — so
                # argv names and injected values are guaranteed identical. Non-credentialed: None.
                popen_env = (_credentialed_client_env(spec.env_passthrough)
                             if credentialed else None)
                logger.info("MCP '%s' started sandboxed via %s", self.slug, self._runtime)
            else:
                # Host path: curated (allowed) / trusted (transition). Existing behaviour.
                launch = self.command
                popen_env = _mcp_env(env_keys)

            self._process = subprocess.Popen(
                launch,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=popen_env,
                text=True,
                **kwargs,
            )

            # Send initialize request
            init_req = {
                "jsonrpc": "2.0",
                "id": next(_request_id),
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "agentnode-sdk", "version": "0.4.0"},
                },
            }
            self._send(init_req)
            resp = self._recv(timeout=timeout)
            if not resp or "error" in resp:
                # Value-free: a credentialed server holds its granted secret and a malicious one
                # could echo it back inside the init response — never interpolate the raw server
                # payload (it may carry the secret). The slug alone locates the failure.
                raise RuntimeError(f"MCP '{self.slug}' failed to initialize")

            # Send initialized notification
            self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        except BaseException:
            self.stop()  # stop()/teardown is idempotent and also tears down the egress proxy
            raise

    def _verify_preinstall(self, backend):
        """Stage 4B gates a–e (SHARED by the non-credentialed and credentialed paths): lockfile
        integrity verified, descriptor/volume-name shape, `volume inspect` (never auto-create),
        and the content↔hash verifier container. Returns the validated PreinstallSpec. NO
        ProcessSpec, NO network choice, NO secret. FAIL-CLOSED — any problem raises."""
        import subprocess as _sp

        from agentnode_sdk.lock_integrity import verify_entry
        from agentnode_sdk.sandbox.mcp_preinstall import (
            PreinstallError,
            validate_preinstall_fields,
            verify_volume_content,
        )
        from agentnode_sdk.sandbox.types import SandboxRequiredError

        entry = self.entry or {}
        mcp_version = entry.get("version")

        # a. lockfile integrity must verify
        ir = verify_entry(self.slug, entry)
        if ir.status != "verified":
            raise PreinstallError(
                f"lockfile integrity for '{self.slug}' is {ir.status!r}, not verified — "
                "refusing to run a preinstalled MCP from an unverified lockfile."
            )
        # b+c. pure shape/command validation + descriptor-bound volume-name gate (mcp_version vs
        #      manager package_version kept distinct).
        pspec = validate_preinstall_fields(self.slug, mcp_version, entry)
        # d. runtime present + volume EXISTS (`volume inspect` BEFORE any -v mount).
        availability = backend.check_available()
        if not availability.available:
            raise SandboxRequiredError(
                "Preinstalled MCP execution requires a container runtime + the pinned "
                f"image. {availability.reason or 'None detected'} — refusing host fallback."
            )
        runtime = availability.backend or "docker"
        try:
            insp = _sp.run(
                [runtime, "volume", "inspect", pspec.volume],
                capture_output=True, timeout=10,
            )
        except Exception as exc:
            raise PreinstallError(f"could not verify sandbox volume: {exc}")
        if insp.returncode != 0:
            raise PreinstallError(
                f"sandbox volume '{pspec.volume}' is missing — reinstall required "
                f"(run: agentnode install {self.slug})."
            )
        # e. content<->hash verifier container (network=none, RO /install, env={}) BEFORE launch.
        verify_volume_content(backend, pspec.volume, pspec.artifact_hash)
        return pspec

    @staticmethod
    def _install_env(pspec):
        """Minimal module-resolution env for the sealed /install volume (NODE_PATH/PYTHONPATH)."""
        return ({"NODE_PATH": "/install/lib/node_modules"} if pspec.manager == "npm"
                else {"PYTHONPATH": "/install"})

    def _preinstalled_spec(self, backend, name: str):
        """Stage 4B (non-credentialed): verify the sealed volume (a–e) then build the hardened
        ProcessSpec — RO /install, clean HOME, minimal module-resolution env only, NO secret.

        Network (MCP net-isolation, Fallback C): a valid sealed ``mcp_allowed_domains`` ⇒ reuse
        the (secret-free) egress-allowlist proxy (network=none + dual-homed CONNECT proxy, domains
        only); otherwise ``network="none"``. NEVER an open ``network="default"`` — a community MCP
        is isolated or allowlisted, never open."""
        from agentnode_sdk.sandbox.domain_policy import (
            DomainPolicyError,
            canonicalize_allowed_domains,
        )
        from agentnode_sdk.sandbox.types import MountSpec, ProcessSpec

        pspec = self._verify_preinstall(backend)
        mounts = [MountSpec(src=pspec.volume, dst="/install", read_only=True)]
        env = self._install_env(pspec)

        # Sealed egress allowlist? Reuse the (secret-free) egress proxy for the declared domains.
        # Invalid/empty ⇒ () ⇒ fully network-isolated (never open); no proxy is started.
        try:
            sealed = canonicalize_allowed_domains(
                (self.entry or {}).get("mcp_allowed_domains") or []
            )
        except (DomainPolicyError, ValueError):
            sealed = ()
        if sealed:
            from agentnode_sdk.sandbox.egress import start_egress_proxy
            handle = start_egress_proxy(list(sealed))
            self._egress_handle = handle
            try:
                return ProcessSpec(
                    command=list(pspec.command), network="egress", egress=handle.spec,
                    clean_home=True, interactive=True, env=env, mounts=mounts, name=name,
                )
            except BaseException:
                self._teardown_egress()
                raise

        # No declared domains ⇒ fully network-isolated (never open).
        return ProcessSpec(
            command=list(pspec.command), network="none", clean_home=True, interactive=True,
            env=env, mounts=mounts, name=name,
        )

    def _credentialed_launch(self, backend, name: str, env_keys):
        """Stage 3B-2b: gated LIVE credentialed launch. Returns the ``ProcessSpec`` and binds
        ``self._egress_handle`` (torn down via stop()). STRICT fail-closed order; NO secret VALUE
        is read in this method — the FIRST + ONLY secret read happens in start() AFTER
        wrap_command succeeds (see _credentialed_client_env):
          1. preinstalled? else refuse (no npx/uvx fetch with a secret)
          2. trust-binding: the passed env_keys must equal the CONSENTED identity names (set) —
             else refuse; from here ONLY the identity names drive injection — NO value read
          3. consent via the injected callback / a valid stored grant — NO value read
          4. preinstall gates a–e (integrity/shape/volume/content-hash) — NO value read
          5. sealed mcp_allowed_domains non-empty + canonical-valid — NO value read
          6. host-key PRESENCE only (`k in os.environ`, consented names) — NO value read
          7. start_egress_proxy(sealed) — failure ⇒ no container; bind handle
          8. ProcessSpec(network=egress, env_passthrough=consented NAMES) — the secret VALUE is read
             LAST in start(), only after wrap_command turns those names into `--env NAME` argv.
        """
        from agentnode_sdk.runtimes.mcp_consent import (
            CredentialedMcpRefused,
            build_identity_from_entry,
            redact_env_keys,
            resolve_consent,
        )
        from agentnode_sdk.runtimes.mcp_consent_store import GrantStoreError
        from agentnode_sdk.sandbox.domain_policy import (
            DomainPolicyError,
            canonicalize_allowed_domains,
        )
        from agentnode_sdk.sandbox.egress import start_egress_proxy
        from agentnode_sdk.sandbox.mcp_preinstall import has_preinstall_intent
        from agentnode_sdk.sandbox.types import MountSpec, ProcessSpec

        entry = self.entry or {}
        # 1. credentialed non-preinstalled stays refused
        if not has_preinstall_intent(entry):
            raise CredentialedMcpRefused(
                "credentialed_requires_preinstall",
                f"MCP '{self.slug}' requests credentials but is not preinstalled — refusing "
                "(no runtime registry-fetch with a secret). Pin an exact mcp_install.",
            )
        # 2. TRUST-BINDING (NO value read). The secret flow binds to the CONSENTED identity names
        #    (the sealed, normalized mcp_env_keys), NEVER to the separately-passed env_keys argument:
        #    a valid grant for one key set must never be used to inject a DIFFERENT host key. Any
        #    drift ⇒ fail-closed BEFORE consent/egress/secret (and before the callback runs).
        #    Set-equality, not ordered: identity.env_key_names is sorted + de-duped by construction,
        #    while env_keys is the raw entry form — so the faithful "same authorized names" test is
        #    on the sets. From here ONLY `consented_names` drives presence/passthrough/read.
        identity = build_identity_from_entry(self.slug, entry)
        consented_names = list(identity.env_key_names)
        if set(env_keys or []) != set(consented_names):
            raise CredentialedMcpRefused(
                "env_keys_identity_mismatch",
                "requested credential names do not match the consented identity — "
                "refusing (names not echoed).",
            )
        # 3. consent (NO secret read). resolve_consent may prompt via the injected callback (TTY)
        #    or honor a valid stored grant (incl. non-TTY = Q3=A). Reasons are value-free.
        try:
            decision = resolve_consent(identity, callback=self._consent_callback)
        except GrantStoreError as e:
            raise CredentialedMcpRefused("grant_store_unusable",
                                         f"consent grant store unusable: {e}")
        if not decision.authorized:
            raise CredentialedMcpRefused(
                decision.reason,
                f"MCP '{self.slug}' credentialed run not authorized ({decision.reason}).",
            )
        # 4. preinstall gates a–e (NO secret read)
        pspec = self._verify_preinstall(backend)
        # 5. sealed allowlist non-empty + canonical-valid (NO secret read)
        try:
            sealed = canonicalize_allowed_domains(entry.get("mcp_allowed_domains") or [])
        except (DomainPolicyError, ValueError):
            sealed = ()
        if not sealed:
            raise CredentialedMcpRefused(
                "missing_or_invalid_allowed_domains",
                f"MCP '{self.slug}' has no valid sealed allowed_domains — refusing credentialed run.",
            )
        # 6. host-key PRESENCE only — the CONSENTED names, never a value read here
        missing = [k for k in consented_names if k not in os.environ]
        if missing:
            raise CredentialedMcpRefused(
                "missing_host_env_key",
                f"MCP '{self.slug}' is missing required environment variable(s): "
                f"{redact_env_keys(missing)}.",
            )
        # 7. egress proxy from the SEALED domains ONLY; bind for teardown. Failure ⇒ no container.
        handle = start_egress_proxy(list(sealed))
        self._egress_handle = handle
        try:
            # 8. credentialed spec — env_passthrough is NAMES only (wrap_command emits `--env NAME`,
            #    never KEY=value); RO /install volume; egress network. NO secret VALUE is read here:
            #    the FIRST secret-value read is deferred to start() AFTER wrap_command succeeds, so a
            #    spec-build or wrap_command failure never touches a secret and never leaks the proxy.
            spec = ProcessSpec(
                command=list(pspec.command), network="egress", egress=handle.spec,
                env_passthrough=list(consented_names), clean_home=True, interactive=True,
                env=self._install_env(pspec),
                mounts=[MountSpec(src=pspec.volume, dst="/install", read_only=True)], name=name,
            )
        except BaseException:
            self._teardown_egress()
            raise
        return spec

    def _teardown_egress(self) -> None:
        """Stop + remove THIS run's egress proxy + its two networks (idempotent, best-effort)."""
        handle = self._egress_handle
        if handle is None:
            return
        self._egress_handle = None
        try:
            from agentnode_sdk.sandbox.egress import stop_egress_proxy
            stop_egress_proxy(handle)
        except Exception:
            pass

    def call_tool(self, name: str, args: dict, timeout: float = 30.0) -> Any:
        """Call a tool on the MCP server."""
        with self._lock:
            self._last_used = time.monotonic()
            req = {
                "jsonrpc": "2.0",
                "id": next(_request_id),
                "method": "tools/call",
                "params": {"name": name, "arguments": args},
            }
            self._send(req)
            resp = self._recv(timeout=timeout)

        if not resp:
            raise RuntimeError("No response from MCP server")
        if "error" in resp:
            # Value-free (see start()): the server's error payload may reflect its granted secret.
            raise RuntimeError(f"MCP '{self.slug}' tool call failed")
        return resp.get("result")

    def stop(self, timeout: float = 5.0) -> None:
        """Gracefully stop the server, kill if needed, and remove the container."""
        proc = self._process
        if proc and proc.poll() is None:
            try:
                proc.stdin.close()
                proc.wait(timeout=timeout)
            except (subprocess.TimeoutExpired, OSError):
                proc.kill()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass
        # Force-remove the container — killing the `docker run` client does NOT
        # reliably stop the container. Best-effort, idempotent (--rm may already
        # have removed it). Guarded so the host path is unaffected.
        if self._container_name and self._runtime:
            try:
                subprocess.run(
                    [self._runtime, "rm", "-f", self._container_name],
                    capture_output=True, timeout=10,
                )
            except Exception:
                pass
            self._container_name = None
        # Stage 3B-2b: a credentialed run's egress proxy + networks must always be torn down
        # too (success, error, timeout, or failed launch). Idempotent.
        self._teardown_egress()

    def health_check(self) -> bool:
        """Check if the server process is still alive."""
        return self._process is not None and self._process.poll() is None

    def _send(self, msg: dict) -> None:
        """Send a JSON-RPC message via stdin."""
        if not self._process or not self._process.stdin:
            raise RuntimeError("MCP server not running")
        line = json.dumps(msg) + "\n"
        self._process.stdin.write(line)
        self._process.stdin.flush()

    def _recv(self, timeout: float = 30.0) -> dict | None:
        """Read a JSON-RPC response from stdout with timeout."""
        if not self._process or not self._process.stdout:
            return None
        result: list[dict | None] = [None]
        error: list[Exception | None] = [None]

        def reader() -> None:
            try:
                line = self._process.stdout.readline()
                if line:
                    result[0] = json.loads(line)
            except Exception as e:
                error[0] = e

        t = threading.Thread(target=reader, daemon=True)
        t.start()
        t.join(timeout)
        if t.is_alive():
            # Check if the process died — if so, clean up to prevent resource leak
            if self._process and self._process.poll() is not None:
                self.stop()
            raise TimeoutError(f"MCP read timeout after {timeout}s")
        if error[0]:
            raise error[0]
        return result[0]


class MCPProcessPool:
    """Pool of MCP server processes, one per package slug."""

    IDLE_TIMEOUT = 300  # 5 minutes

    def __init__(self) -> None:
        self._servers: dict[str, MCPServerProcess] = {}
        self._lock = threading.Lock()
        atexit.register(self.stop_all)

    def get_or_start(
        self, slug: str, command: list[str],
        *,
        host_policy_decision: Any,
        compatibility: Any,
        timeout: float = 10.0, env_keys: list[str] | None = None,
        entry: dict | None = None,
        mcp_consent_callback=None,
    ) -> MCPServerProcess:
        """Get a COMPATIBLE existing server or start a new one.

        Stage 3B-2b (D2): credentialed MCPs (``env_keys`` present) are EPHEMERAL — they are NEVER
        pooled/cached (they hold a live egress proxy + injected secret env). A fresh server is
        started for this call only; the caller (``run_mcp``) ALWAYS stops it afterwards.

        F1: a NON-credentialed healthy cache hit is reused ONLY when its full
        :class:`MCPProcessCompatibility` (boundary + trust + launch identity +
        sandbox profile) equals this call's ``compatibility`` — the slug alone is
        not identity (install/upgrade/reinstall/reseal/trust-refresh can change
        command/version/artifact/trust/profile under the same slug). Any mismatch
        (or a legacy process with no compatibility) ⇒ the cached process is
        removed from the pool FIRST (never reused for a tool call), stopped
        best-effort, and a fresh process is started under the current decision.
        The new entry is published + marked active ONLY after a successful start;
        a failed start leaves no usable pool entry (fail-closed)."""
        with self._lock:
            trust_level = host_policy_decision.trust_level
            if env_keys:
                server = MCPServerProcess(slug, command, trust_level=trust_level, entry=entry,
                                          mcp_consent_callback=mcp_consent_callback)
                server.start(timeout=timeout, env_keys=env_keys,  # NOT stored in the pool
                             _host_policy_decision=host_policy_decision)
                server.compatibility = compatibility
                return server

            server = self._servers.get(slug)
            if server and server.health_check() and server.compatibility == compatibility:
                return server  # healthy + full-identity match ⇒ reuse

            if server is not None:
                # Remove BEFORE stop/start so the old process can never be handed
                # out for a tool call, even if stop() fails.
                self._servers.pop(slug, None)
                if server.compatibility is not None and server.compatibility != compatibility:
                    logger.debug("Restarting cached MCP process because its execution profile changed.")
                try:
                    server.stop()
                except Exception:
                    logger.debug("Failed to stop incompatible MCP process for %s", slug, exc_info=True)

            new_server = MCPServerProcess(slug, command, trust_level=trust_level, entry=entry,
                                          mcp_consent_callback=mcp_consent_callback)
            new_server.start(timeout=timeout, env_keys=env_keys,
                             _host_policy_decision=host_policy_decision)
            new_server.compatibility = compatibility     # mark active only after success
            self._servers[slug] = new_server             # publish only after success
            return new_server

    def stop_all(self) -> None:
        """Stop all managed servers."""
        with self._lock:
            for server in self._servers.values():
                try:
                    server.stop()
                except Exception:
                    pass
            self._servers.clear()


# Global pool singleton
_pool: MCPProcessPool | None = None
_pool_lock = threading.Lock()


def _get_global_pool() -> MCPProcessPool:
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = MCPProcessPool()
        return _pool


def _mcp_env(env_keys: list[str] | None = None) -> dict[str, str]:
    """Build environment for MCP subprocess.

    Only system-safe keys plus explicitly declared env_keys are passed through.
    Declared keys are only included if already set in os.environ.
    """
    safe_keys = {
        "PATH", "HOME", "USERPROFILE", "USER", "LOGNAME",
        "VIRTUAL_ENV", "PYTHONPATH", "PYTHONHOME",
        "SYSTEMROOT", "SYSTEMDRIVE", "COMSPEC", "WINDIR", "PATHEXT",
        "APPDATA", "LOCALAPPDATA", "PROGRAMFILES",
        "TEMP", "TMP", "TMPDIR",
        "LANG", "LC_ALL", "LC_CTYPE",
    }
    result = {k: v for k, v in os.environ.items() if k in safe_keys}
    for key in (env_keys or []):
        val = os.environ.get(key)
        if val is not None:
            result[key] = val
    return result


def _check_env_keys(slug: str, env_keys: list[str]) -> list[str]:
    """Return list of declared env_keys that are not set in os.environ."""
    return [k for k in env_keys if k not in os.environ]


def _credentialed_client_env(env_keys: list[str] | None) -> dict[str, str]:
    """Stage 3B-2b: build the MINIMAL docker-CLIENT environment for a credentialed run — only the
    few vars docker itself needs (PATH/HOME/DOCKER_*) PLUS the consented secret VALUES by name.

    This is the FIRST and ONLY secret-value read in the credentialed path; it runs LAST (after
    consent + integrity + sealed allowlist + host-key presence + egress proxy + a successful
    wrap_command). The values live in the docker-client process env and reach the container ONLY
    via `--env NAME` — they NEVER appear on argv, in logs, or in errors.

    FAIL-CLOSED at the final read: a declared key that is missing (a TOCTOU vanish since the
    presence check) or empty ⇒ value-free CredentialedMcpRefused — NO silent skip, and NO value /
    partial / prefix / name in the message. Raised inside start()'s cleanup scope ⇒ the egress
    proxy is torn down."""
    from agentnode_sdk.runtimes.mcp_consent import CredentialedMcpRefused

    keep = ("PATH", "HOME", "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG",
            "DOCKER_CERT_PATH", "DOCKER_TLS_VERIFY")
    env = {k: os.environ[k] for k in keep if k in os.environ}
    for k in (env_keys or []):
        try:
            v = os.environ[k]
        except KeyError:
            raise CredentialedMcpRefused(
                "missing_host_env_key",
                "a required host environment variable is missing at the final secret "
                "read (name not echoed).",
            )
        if v == "":
            raise CredentialedMcpRefused(
                "empty_host_env_key",
                "a required host environment variable is empty at the final secret "
                "read (name not echoed).",
            )
        env[k] = v
    return env


def run_mcp(
    slug: str,
    tool_name: str | None,
    *,
    _host_policy_decision: Any,
    timeout: float = 30.0,
    entry: dict,
    mcp_consent_callback=None,
    **kwargs: Any,
) -> RunToolResult:
    """Run a tool on an MCP server subprocess.

    Args:
        slug: Package slug.
        tool_name: Tool name to call.
        timeout: Timeout for tool execution.
        entry: Lockfile entry with mcp_command and tools.
        **kwargs: Arguments passed to the tool.
    """
    t0 = time.monotonic()
    name = tool_name
    server = None
    ephemeral = False  # 3B-2b/D2: credentialed servers are ephemeral; torn down in finally
    try:
        # F1: host-trust decision is OWNED by run_tool and passed in immutably —
        # run_mcp never re-reads host_trust_policy. Refuse a missing/entry-
        # mismatched decision before any pool access or process start.
        from agentnode_sdk.runtimes.mcp_launch import build_mcp_launch_plan
        from agentnode_sdk.sandbox.policy import HostTrustPolicyDecision
        if not isinstance(_host_policy_decision, HostTrustPolicyDecision):
            return RunToolResult(
                success=False,
                error=f"run_mcp requires a host-trust policy decision for '{slug}'; none was provided.",
                mode_used="mcp",
            )
        if _host_policy_decision.trust_level != (entry.get("trust_level") or ""):
            return RunToolResult(
                success=False,
                error="host-trust policy decision does not match entry trust level",
                mode_used="mcp",
            )

        command = entry.get("mcp_command")
        if not command:
            return RunToolResult(
                success=False,
                error=f"No mcp_command in lockfile for '{slug}'",
                mode_used="mcp",
            )

        env_keys = entry.get("mcp_env_keys") or []
        missing = _check_env_keys(slug, env_keys)
        if missing:
            names = ", ".join(missing)
            return RunToolResult(
                success=False,
                error=(
                    f"Missing environment variables for '{slug}': {names}\n"
                    f"Set them before running, e.g.: "
                    + " ".join(f"{k}=..." for k in missing[:3])
                ),
                mode_used="mcp",
            )

        pool = _get_global_pool()
        ephemeral = bool(env_keys)  # D2: credentialed MCPs are never pooled
        plan = build_mcp_launch_plan(slug, entry, _host_policy_decision)
        server = pool.get_or_start(
            slug, command, env_keys=env_keys,
            host_policy_decision=_host_policy_decision,
            compatibility=plan.compatibility,
            entry=entry, mcp_consent_callback=mcp_consent_callback,
        )

        # Resolve tool name
        name = tool_name
        if not name:
            tools = entry.get("tools", [])
            if tools:
                name = tools[0].get("name", slug)
            else:
                name = slug

        # Guard: inspect MCP arguments before forwarding
        from agentnode_sdk.guard import inspect_mcp_args
        input_schema = None
        for t in entry.get("tools", []):
            if t.get("name") == name:
                input_schema = t.get("input_schema") or t.get("inputSchema")
                break
        mcp_guard = inspect_mcp_args(slug, name, kwargs, entry, input_schema=input_schema)
        if mcp_guard.action == "deny":
            elapsed = (time.monotonic() - t0) * 1000
            return RunToolResult(
                success=False,
                error=f"MCP argument inspection blocked: {mcp_guard.reason}",
                mode_used="mcp",
                duration_ms=round(elapsed, 1),
            )
        if mcp_guard.action == "prompt":
            elapsed = (time.monotonic() - t0) * 1000
            return RunToolResult(
                success=False,
                error=f"MCP argument inspection requires confirmation: {mcp_guard.reason}",
                mode_used="mcp",
                duration_ms=round(elapsed, 1),
            )

        result = server.call_tool(name, kwargs, timeout=timeout)
        elapsed = (time.monotonic() - t0) * 1000

        _audit_mcp_call(slug, name, success=True, duration_ms=elapsed)

        return RunToolResult(
            success=True,
            result=result,
            mode_used="mcp",
            duration_ms=round(elapsed, 1),
        )
    except TimeoutError:
        elapsed = (time.monotonic() - t0) * 1000
        _audit_mcp_call(slug, name, success=False,
                        duration_ms=elapsed, error_class="TimeoutError")
        return RunToolResult(
            success=False,
            error=f"MCP tool timed out after {timeout}s",
            mode_used="mcp",
            duration_ms=round(elapsed, 1),
            timed_out=True,
        )
    except Exception as exc:
        elapsed = (time.monotonic() - t0) * 1000
        _audit_mcp_call(slug, name, success=False,
                        duration_ms=elapsed, error_class=type(exc).__name__)
        return RunToolResult(
            success=False,
            error=f"{type(exc).__name__}: {exc}",
            mode_used="mcp",
            duration_ms=round(elapsed, 1),
        )
    finally:
        # D2: a credentialed (ephemeral) server is ALWAYS torn down after the call — success,
        # tool error, JSON-RPC error, or timeout — so no secret-holding container / egress proxy
        # lingers. Non-credentialed pooled servers are left running for reuse.
        if ephemeral and server is not None:
            try:
                server.stop()
            except Exception:
                pass


def _audit_mcp_call(
    slug: str,
    tool_name: str | None,
    *,
    success: bool,
    duration_ms: float | None = None,
    error_class: str | None = None,
) -> None:
    """Audit an MCP tool execution result. Never crashes the caller."""
    try:
        dur = round(duration_ms) if duration_ms is not None else 0
        if success:
            reason = f"mcp_call ok duration_ms={dur}"
        else:
            reason = "mcp_call failed"
            if error_class:
                reason += f" error={error_class}"
            reason += f" duration_ms={dur}"

        result = PolicyResult(
            action="allow" if success else "deny",
            reason=reason,
            source="mcp_runner",
        )
        audit_decision(result, "mcp_run", slug, tool_name=tool_name)
    except Exception:
        logger.debug("Failed to audit MCP call for %s", slug, exc_info=True)
