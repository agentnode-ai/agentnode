"""dependency_audit_agent — AgentNode agent (ANP v0.2)

Dependency Audit Agent: Scan project dependencies for known vulnerabilities, outdated versions, license issues, and leaked secrets.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# Workflow steps: (capability_id, param_key, description)
STEPS = [
        ("code_analysis", "file_path", "Parse dependency files"),
        ("web_search", "package", "Check for known vulnerabilities"),
        ("code_analysis", "file_path", "Scan for leaked secrets"),
]


class DependencyAuditAgent:
    """
    Audit project dependencies by scanning for known CVEs, checking for outdated packages, verifying license compatibility, and detecting leaked secrets in configuration.

    Uses AgentNode SDK's detect_and_install + run_tool pattern to dynamically
    discover and use capabilities from the full skill registry.
    """

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or os.environ.get("AGENTNODE_API_KEY", "")

    async def execute(self, goal: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
        """Run the agent workflow.

        Args:
            goal: The objective to accomplish.
            context: Optional parameters and context.

        Returns:
            Dict with result, done status, and metadata.
        """
        findings: list[dict[str, Any]] = []
        consecutive_errors = 0

        try:
            from agentnode_sdk import AgentNodeClient
            client = AgentNodeClient(api_key=self._api_key)
        except ImportError:
            logger.warning("agentnode_sdk not installed, returning stub result")
            return {"result": None, "done": False, "error": "agentnode_sdk not installed"}

        try:
            for capability, param_key, description in STEPS:
                step_result = await self._use_capability(client, capability, {
                    param_key: goal,
                    **(context or {}),
                })
                findings.append({"step": description, "result": step_result})
                if step_result.get("error"):
                    consecutive_errors += 1
                    if consecutive_errors >= 3:
                        break
                else:
                    consecutive_errors = 0
        finally:
            client.close()

        return {
            "result": findings,
            "done": True,
            "goal": goal,
            "steps_completed": len(findings),
        }

    async def _use_capability(
        self,
        client: Any,
        capability: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        """Use a capability via smart_run with auto-detection and install."""
        try:
            result = client.smart_run(
                lambda: client.run_tool(capability, **params),
                auto_upgrade_policy="safe",
            )
            if result.success:
                return result.result if isinstance(result.result, dict) else {"output": result.result}
            return {"error": result.error or "Unknown error"}
        except Exception as exc:
            logger.warning("Capability %s failed: %s", capability, exc)
            return {"error": str(exc)}


async def run(goal: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Agent entrypoint for AgentNode agent runner.

    Args:
        goal: The objective for this agent.
        context: Optional context with parameters and configuration.

    Returns:
        Structured result with findings and metadata.
    """
    ctx = context or {}
    agent = DependencyAuditAgent(api_key=ctx.get("api_key"))
    return await agent.execute(goal=goal, context=ctx)
