"""AgentNode Python SDK — discover, resolve, and install AI agent capabilities."""

from agentnode_sdk.async_client import AsyncAgentNode
from agentnode_sdk.client import AgentNode, AgentNodeClient
from agentnode_sdk.config import load_config, config_path, installation_behavior_label
from agentnode_sdk.detect import detect_gap
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
    DetectAndInstallResult,
    DetectedGap,
    InstallMetadata,
    InstallResult,
    PackageDetail,
    ResolvedPackage,
    ResolveResult,
    RunToolResult,
    SearchHit,
    SearchResult,
    SmartRunResult,
)
from agentnode_sdk.runner import run_tool

# Convenience aliases
Client = AgentNodeClient
ToolError = AgentNodeToolError

__version__ = "0.4.0"
__all__ = [
    "AgentNode",
    "AsyncAgentNode",
    "AgentNodeClient",
    "Client",
    "load_config",
    "config_path",
    "installation_behavior_label",
    "detect_gap",
    "load_tool",
    "run_tool",
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
    "RunToolResult",
    "DetectedGap",
    "DetectAndInstallResult",
    "SmartRunResult",
]
