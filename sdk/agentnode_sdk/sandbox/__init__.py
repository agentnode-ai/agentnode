"""SDK execution sandbox (P0.1: abstraction + detection + fail-closed gate).

P0.1 does NOT yet run code inside a container. It provides the backend
abstraction, container-runtime detection, and a live fail-closed gate that
refuses to run community/unverified code when no sandbox is available. Actual
container routing of MCP runs (P0.2) and toolpack install+run (P0.3) come next.
"""
from agentnode_sdk.sandbox.types import (
    MountSpec,
    ProcessSpec,
    SandboxAvailability,
    SandboxRequiredError,
)
from agentnode_sdk.sandbox.backend import NoSandboxBackend, SandboxBackend
from agentnode_sdk.sandbox.container_backend import ContainerBackend
from agentnode_sdk.sandbox.policy import (
    enforce_sandbox_policy,
    get_default_backend,
    require_sandbox_for_tier,
    requires_sandbox,
    set_default_backend,
)

__all__ = [
    "SandboxAvailability",
    "ProcessSpec",
    "MountSpec",
    "SandboxRequiredError",
    "SandboxBackend",
    "NoSandboxBackend",
    "ContainerBackend",
    "require_sandbox_for_tier",
    "requires_sandbox",
    "enforce_sandbox_policy",
    "get_default_backend",
    "set_default_backend",
]
