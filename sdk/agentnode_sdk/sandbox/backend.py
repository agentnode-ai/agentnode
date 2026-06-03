"""SandboxBackend abstraction + a NoSandboxBackend that always fails closed."""
from __future__ import annotations

from abc import ABC, abstractmethod

from agentnode_sdk.sandbox.types import (
    MountSpec,
    ProcessSpec,
    SandboxAvailability,
    SandboxRequiredError,
)


class SandboxBackend(ABC):
    """An isolation backend. P0.1 implements detection + pure command wrapping;
    actual execution (`run_process`/`run_mcp_process`) is P0.3/P0.2."""

    @abstractmethod
    def check_available(self) -> SandboxAvailability:
        """Probe (cached) whether this backend can isolate execution."""

    def explain_unavailable(self) -> str:
        a = self.check_available()
        return "" if a.available else (a.reason or "no container runtime available")

    def build_process_spec(
        self,
        command: list[str],
        *,
        network: str = "none",
        mounts: list[MountSpec] | None = None,
        env: dict[str, str] | None = None,
        limits: dict[str, str] | None = None,
        clean_home: bool = True,
        interactive: bool = False,
    ) -> ProcessSpec:
        return ProcessSpec(
            command=list(command),
            network=network,
            mounts=list(mounts or []),
            env=dict(env or {}),
            limits=dict(limits or {}),
            clean_home=clean_home,
            interactive=interactive,
        )

    @abstractmethod
    def wrap_command(self, spec: ProcessSpec) -> list[str]:
        """Return the argv that runs ``spec.command`` in isolation (no execution)."""

    def run_process(
        self,
        spec: ProcessSpec,
        input_text: str | None = None,
        timeout: float = 120.0,
    ) -> tuple[int, str, str]:
        """One-shot sandboxed exec. Returns ``(returncode, stdout, stderr)``.

        Implemented by :class:`ContainerBackend` (P0.3). Used for BOTH the
        toolpack build (pip install into the volume) and the per-call run
        (``python -c <wrapper>`` with JSON on stdin → JSON on stdout).
        """
        raise NotImplementedError("one-shot sandboxed run requires a real backend")

    def run_mcp_process(self, spec: ProcessSpec):  # pragma: no cover - P0.2
        raise NotImplementedError("long-lived sandboxed MCP run is P0.2")


class NoSandboxBackend(SandboxBackend):
    """Never available. ``wrap_command`` refuses — it must never silently run
    anything on the host."""

    def check_available(self) -> SandboxAvailability:
        return SandboxAvailability(
            available=False,
            backend="none",
            reason="no container runtime (docker/podman) detected",
        )

    def wrap_command(self, spec: ProcessSpec) -> list[str]:
        raise SandboxRequiredError(
            "No sandbox backend available — refusing to run untrusted code."
        )
