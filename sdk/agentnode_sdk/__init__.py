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
from agentnode_sdk.skill import load_skill, SkillContext, SkillAsset
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
from agentnode_sdk.compatibility import recommend_model
from agentnode_sdk.risk_profile import RiskProfile, compute_risk_profile, get_risk_profile
from agentnode_sdk.policy import PolicyResult, check_install, check_run
from agentnode_sdk.runner import run_tool
from agentnode_sdk.runtime import AgentNodeRuntime

# Convenience aliases
Client = AgentNodeClient
ToolError = AgentNodeToolError

#: THE ONE PLACE THE VERSION IS WRITTEN. `pyproject.toml` builds the wheel's version from this
#: line rather than carrying its own copy, because it did carry one: the project said 0.25.0
#: while this said 0.24.1, so the gateway announced 0.24.1 to every client it answered while
#: running the 0.25.0 build. Two copies of one number drift, and the drift is invisible until
#: something reports the wrong one.
__version__ = "0.25.0"
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
    "load_skill",
    "SkillContext",
    "SkillAsset",
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
    "AgentNodeRuntime",
    "PolicyResult",
    "check_install",
    "check_run",
    "recommend_model",
    "RiskProfile",
    "compute_risk_profile",
    "get_risk_profile",
]
