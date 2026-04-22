"""cloud_cost_agent — AgentNode agent (ANP v0.2)

Cloud Cost Agent: Analyze cloud infrastructure costs across AWS/Azure, identify waste, and recommend optimization strategies.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# Workflow steps: (capability_id, param_key, description)
STEPS = [
        ("csv_analysis", "data", "Analyze billing and usage data"),
        ("chart_generation", "data", "Visualize cost trends and breakdown"),
        ("document_summary", "text", "Generate cost optimization report"),
]


class CloudCostAgent:
    """
    Analyze cloud costs by fetching billing data, identifying underutilized resources and waste, benchmarking against best practices, and recommending cost optimizations.

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
    agent = CloudCostAgent(api_key=ctx.get("api_key"))
    return await agent.execute(goal=goal, context=ctx)
