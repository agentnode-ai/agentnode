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


class AgentNodeToolError(Exception):
    """Base error for tool execution failures.

    Pack authors should raise this instead of returning {"error": ...} dicts.
    Adapters (LangChain, MCP) catch this to propagate structured errors.
    """

    def __init__(self, message: str, tool_name: str | None = None, details: dict | None = None):
        self.tool_name = tool_name
        self.details = details or {}
        super().__init__(message)
