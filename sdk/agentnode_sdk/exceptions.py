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


class AgentNodeToolError(Exception):
    """Base error for tool execution failures.

    Pack authors should raise this instead of returning {"error": ...} dicts.
    Adapters (LangChain, MCP) catch this to propagate structured errors.
    """

    def __init__(self, message: str, tool_name: str | None = None, details: dict | None = None):
        self.tool_name = tool_name
        self.details = details or {}
        super().__init__(message)
