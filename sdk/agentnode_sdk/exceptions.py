"""Exception hierarchy for AgentNode SDK. Spec §14.1."""


class AgentNodeError(Exception):
    """Base error for all AgentNode API errors."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"[{code}] {message}")


class NotFoundError(AgentNodeError):
    """Package or resource not found (404)."""
    pass


class AuthError(AgentNodeError):
    """Authentication or authorization failure (401/403)."""
    pass


class ValidationError(AgentNodeError):
    """Manifest or input validation failure (422)."""
    pass


class RateLimitError(AgentNodeError):
    """Rate limit exceeded (429)."""
    pass


class LockfileFormatError(AgentNodeError):
    """agentnode.lock is structurally malformed in a way that must FAIL CLOSED
    rather than be treated as empty.

    Currently raised for duplicate object keys at any nesting level — a tamper /
    ambiguity signal that plain ``json.loads`` would silently collapse to
    last-wins. Deliberately NOT a ``json.JSONDecodeError``/``OSError`` subclass,
    so it is not swallowed by the fail-soft handling for a missing file or
    syntactically corrupt JSON (which keep returning an empty default lockfile).
    Only the offending key name is surfaced — never a value or file content.
    """
    pass


class ConfigurationError(AgentNodeError):
    """Local AgentNode configuration is invalid in a way that must FAIL CLOSED.

    Distinct from :class:`ValidationError` (which is bound to the HTTP-422 /
    registry-manifest path): this is a *local* config fault — e.g. an
    unrecognized / empty / null / wrong-typed ``sandbox.host_trust_policy`` value
    that reached a routing decision. The raw offending value is NEVER placed in
    the message (it is not needed and could carry noise); only the safe allowed
    set is surfaced. The top-level CLI handler translates it to a traceback-free
    ``Error: [code] message`` + exit 1.
    """
    pass


HOST_AGENT_EXECUTION_UNSUPPORTED = "host_agent_execution_unsupported"

HOST_AGENT_UNSUPPORTED_MESSAGE = (
    "Host-agent execution is disabled in this SDK slice: there is no verified process-"
    "isolation boundary for running an agent's entrypoint on the host."
)


class HostAgentExecutionUnsupported(AgentNodeError):
    """Running a host/community agent's OWN entrypoint (foreign code) on the host safely
    requires a verified, kernel-enforced isolation boundary this SDK slice does NOT
    provide. Rather than ship a partially-secured executor, host-agent OS-process
    execution is STRUCTURALLY fail-closed: there is no enable flag, env var, config, or
    monkeypatch that turns it on. This is the SINGLE source of the stable code + message;
    every host-agent execution request raises it (the public run path translates it to a
    ``RunToolResult`` with ``error_code`` at one outer boundary)."""

    def __init__(self, message: str = HOST_AGENT_UNSUPPORTED_MESSAGE):
        super().__init__(HOST_AGENT_EXECUTION_UNSUPPORTED, message)


def refuse_host_agent_execution():
    """The single structural chokepoint. ALWAYS raises before any import / spawn / Job /
    environment reader / IPC could run — there is no code path that returns instead."""
    raise HostAgentExecutionUnsupported()


class AgentNodeToolError(Exception):
    """Base error for tool execution failures.

    Pack authors should raise this instead of returning {"error": ...} dicts.
    Adapters (LangChain, MCP) catch this to propagate structured errors.
    """

    def __init__(self, message: str, tool_name: str | None = None, details: dict | None = None):
        self.tool_name = tool_name
        self.details = details or {}
        super().__init__(message)
