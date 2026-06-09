"""Transport-agnostic bidirectional session for sandboxed agent execution.

Sprint A: the interface + an in-process ``FakeSession`` for tests. This module is
NOT imported by any production path (no ``run_agent``, no backend wiring) — it
only lays the rails. See ``docs/design/agent-sandbox-architecture.md``.

A session is the channel an :class:`AgentRpcHost` talks over, regardless of WHERE
the sandbox runs (local container stdio, the user's own server, or the managed
cloud). The agent RPC protocol rides over ``send``/``recv``; the transport is the
backend's concern (added in later sprints).
"""
from __future__ import annotations

import queue
from abc import ABC, abstractmethod


class AgentSandboxSession(ABC):
    """A bidirectional channel to a sandboxed agent process."""

    @abstractmethod
    def send(self, message: dict) -> None:
        """Send one protocol message to the agent side."""

    @abstractmethod
    def recv(self, timeout: float) -> dict | None:
        """Receive one protocol message from the agent side.

        Returns ``None`` on EOF (the agent side closed). Raises ``TimeoutError``
        if no message arrives within ``timeout`` seconds.
        """

    @abstractmethod
    def close(self) -> None:
        """Tear down the session (and the underlying sandbox process)."""


class FakeSession(AgentSandboxSession):
    """In-process session for tests — no container, no transport, no Docker.

    The 'agent side' is scripted: ``agent_script`` is the ordered list of messages
    the host will ``recv``. Everything the host ``send``s is recorded in ``sent``.
    Use ``feed`` to append more agent messages (``None`` = EOF).
    """

    def __init__(self, agent_script: list[dict] | None = None):
        self._outbox: "queue.Queue[dict | None]" = queue.Queue()
        for m in agent_script or []:
            self._outbox.put(m)
        self.sent: list[dict] = []
        self.closed = False

    def feed(self, message: dict | None) -> None:
        self._outbox.put(message)

    def send(self, message: dict) -> None:
        if self.closed:
            raise RuntimeError("session is closed")
        self.sent.append(message)

    def recv(self, timeout: float) -> dict | None:
        try:
            return self._outbox.get(timeout=timeout)
        except queue.Empty:
            raise TimeoutError(f"no message within {timeout}s")

    def close(self) -> None:
        self.closed = True
