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
