"""SandboxBackend abstraction + a NoSandboxBackend that always fails closed."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from agentnode_sdk.sandbox.types import (
    MountSpec,
    ProcessSpec,
    SandboxAvailability,
    SandboxRequiredError,
)

if TYPE_CHECKING:
    from agentnode_sdk.sandbox.agent_session import AgentSandboxSession


class Outcome(tuple):
    """What a run produced, and why it stopped.

    A three-tuple, so ``rc, out, err = backend.run_process(...)`` keeps working exactly as it did
    -- and an object, so the reason no longer has to be smuggled inside the exit code.
    `EM3C-E4-CLASSIFY-0001` found the smuggling: a timeout was -1, a Windows client saw
    4294967295, and the two had to be called equal for anything to work.

    ``exit_code`` is None when nothing exited. A process the sandbox killed did not choose a
    status, and reporting one it did not choose is how the reason got lost in the first place.
    ``native_status`` is what the runtime itself reported, kept beside the reason and never
    instead of it, and ``platform`` says whose number it is.
    """

    # No `__slots__`: a tuple subclass cannot have a non-empty one, and an empty one would
    # forbid the three attributes this exists to carry.

    def __new__(cls, exit_code, stdout, stderr, *, reason="exited", native_status=None,
                platform=""):
        made = super().__new__(cls, (exit_code, stdout, stderr))
        made._reason = reason                                  # noqa: SLF001 - own attribute
        made._native = native_status
        made._platform = platform
        return made

    @property
    def reason(self) -> str:
        return self._reason

    @property
    def native_status(self):
        return self._native

    @property
    def platform(self) -> str:
        return self._platform


def why_it_stopped(result) -> tuple:
    """(reason, native status, platform) for whatever a backend returned.

    One place reads it, so a backend that says nothing is read as an ordinary exit in exactly one
    way rather than in several places that could disagree.
    """
    from agentnode_sdk.gateway.protocol import EXITED

    return (getattr(result, "reason", EXITED),
            getattr(result, "native_status", None),
            getattr(result, "platform", ""))


class SandboxBackend(ABC):
    """An isolation backend. P0.1 implements detection + pure command wrapping;
    actual execution (`run_process`/`run_mcp_process`) is P0.3/P0.2."""

    #: Whose numbers this backend's statuses are, for a record that keeps one. Empty here on
    #: purpose: a backend that cannot say is not made to say something, and a number nobody can
    #: attribute is not recorded. `EM3C-E8-RECORD-0001`.
    native_platform = ""

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

        The result may be an :class:`Outcome`, which IS that triple and also carries why the run
        stopped. A backend that returns a plain tuple is read as an ordinary exit, which is what
        every backend meant before there was anything else to say.

        Implemented by :class:`ContainerBackend` (P0.3). Used for BOTH the
        toolpack build (pip install into the volume) and the per-call run
        (``python -c <wrapper>`` with JSON on stdin → JSON on stdout).
        """
        raise NotImplementedError("one-shot sandboxed run requires a real backend")

    def run_mcp_process(self, spec: ProcessSpec):  # pragma: no cover - P0.2
        raise NotImplementedError("long-lived sandboxed MCP run is P0.2")

    def open_agent_session(self, spec: ProcessSpec) -> "AgentSandboxSession":
        """Open a long-lived bidirectional session to a sandboxed AGENT process.

        Default is **fail-closed**: a backend that cannot isolate must never run
        agent code on the host. Implemented by :class:`ContainerBackend`.
        Sprint B1: nothing in production calls this yet (``run_agent`` is
        unchanged) — it only adds the capability.
        """
        raise SandboxRequiredError(
            "No sandbox backend available — refusing to run agent code on the host."
        )


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
