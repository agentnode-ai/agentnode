"""AgentNode Python SDK — discover, resolve, and install AI agent capabilities."""

from agentnode_sdk.async_client import AsyncAgentNode
from agentnode_sdk.client import AgentNode, AgentNodeClient
from agentnode_sdk.exceptions import (
    AgentNodeError,
    AgentNodeToolError,
    AuthError,
    NotFoundError,
    RateLimitError,
    ValidationError,
)
from agentnode_sdk.installer import load_tool
from agentnode_sdk.models import (
    CanInstallResult,
    InstallMetadata,
    InstallResult,
    PackageDetail,
    ResolvedPackage,
    ResolveResult,
    SearchHit,
    SearchResult,
)

# Convenience aliases
Client = AgentNodeClient
ToolError = AgentNodeToolError

__version__ = "0.2.0"
__all__ = [
    "AgentNode",
    "AsyncAgentNode",
    "AgentNodeClient",
    "Client",
    "load_tool",
    "AgentNodeError",
    "AgentNodeToolError",
    "ToolError",
    "NotFoundError",
    "AuthError",
    "RateLimitError",
    "ValidationError",
    "PackageDetail",
    "SearchResult",
    "SearchHit",
    "ResolveResult",
    "ResolvedPackage",
    "InstallMetadata",
    "InstallResult",
    "CanInstallResult",
]
