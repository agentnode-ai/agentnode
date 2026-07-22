"""Rule-based multi-step task planner.

Decomposes natural language tasks into sequential capability steps,
resolves packages, and executes via ``run_tool()`` with full policy/audit.

Rules:
- Split on connectors: "then", "and then", "afterwards", "after that", "→"
- Max 3 steps (MVP)
- Each step parsed via ``parse_task()``
- Output of step N piped as input to step N+1 (explicit args take priority)
- Every step runs through ``run_tool()`` — policy/audit preserved
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any


MAX_STEPS = 3

_STEP_CONNECTORS = [
    " and then ",
    " afterwards ",
    " after that ",
    " then ",
    " → ",
]

_CONNECTOR_RE = re.compile(
    "|".join(re.escape(c) for c in _STEP_CONNECTORS),
    re.IGNORECASE,
)


@dataclass
class PlanStep:
    index: int
    sub_task: str
    capability: str
    confidence: str
    source: str
    input_args: dict
    uses_previous: bool = False


@dataclass
class ExecutionPlan:
    original_task: str
    steps: list[PlanStep]

    @property
    def is_multi_step(self) -> bool:
        return len(self.steps) > 1


@dataclass
class StepResult:
    step: PlanStep
    success: bool
    result: Any = None
    error: str | None = None
    slug: str | None = None
    duration_ms: float = 0.0
    policy: dict | None = None
    installed: bool = False

    def to_dict(self) -> dict:
        d: dict[str, Any] = {
            "step": self.step.index,
            "sub_task": self.step.sub_task,
            "capability": self.step.capability,
            "success": self.success,
            "duration_ms": self.duration_ms,
        }
        if self.slug:
            d["slug"] = self.slug
        if self.result is not None:
            d["result"] = self.result
        if self.error:
            d["error"] = self.error
        if self.policy:
            d["policy"] = self.policy
        if self.installed:
            d["installed"] = True
        return d


@dataclass
class PlanResult:
    plan: ExecutionPlan
    steps: list[StepResult]
    success: bool
    duration_ms: float = 0.0

    def to_dict(self) -> dict:
        return {
            "original_task": self.plan.original_task,
            "success": self.success,
            "duration_ms": self.duration_ms,
            "steps": [s.to_dict() for s in self.steps],
        }


def _split_task(task: str) -> list[str]:
    """Split a multi-step task into sub-tasks using connectors."""
    parts = _CONNECTOR_RE.split(task)
    return [p.strip() for p in parts if p.strip()]


_CONTENT_KEYS = frozenset({"file_path", "url", "query"})
_MODIFIER_KEYS = frozenset({"target_language"})


def _has_explicit_input(parsed_args: dict, sub_task: str) -> bool:
    """Check whether parse_task extracted real content from the sub-task text.

    Returns False when:
    - args are empty
    - the only arg is the fallback {"text": sub_task} or {"query": sub_task}
    - only modifier keys (e.g. target_language) are present without content

    Content keys: file_path, url, query (with non-fallback value).
    Modifier keys: target_language — these configure the tool but don't
    supply the content to process.
    """
    if not parsed_args:
        return False

    keys = set(parsed_args)

    if keys & _CONTENT_KEYS:
        for k in keys & _CONTENT_KEYS:
            if k == "query" and parsed_args[k] == sub_task:
                continue
            return True

    non_modifier = keys - _MODIFIER_KEYS
    if not non_modifier:
        return False

    if non_modifier <= {"text", "query"}:
        for k in non_modifier:
            if parsed_args[k] != sub_task:
                return True
        return False

    return True


def plan_task(task: str) -> ExecutionPlan:
    """Decompose a natural language task into an ExecutionPlan.

    Raises ValueError if any sub-task can't be parsed or >3 steps.
    """
    from agentnode_sdk.cli.smart_run import parse_task

    parts = _split_task(task)

    if len(parts) > MAX_STEPS:
        raise ValueError(
            f"Too many steps ({len(parts)}). Maximum is {MAX_STEPS}."
        )

    steps: list[PlanStep] = []
    for i, sub_task in enumerate(parts):
        parsed = parse_task(sub_task)
        if parsed is None:
            raise ValueError(
                f"Step {i + 1} could not be parsed: \"{sub_task}\""
            )

        has_explicit = _has_explicit_input(parsed.input_args, sub_task)
        uses_previous = i > 0 and not has_explicit

        steps.append(PlanStep(
            index=i,
            sub_task=sub_task,
            capability=parsed.capability,
            confidence=parsed.confidence,
            source=parsed.source,
            input_args=parsed.input_args,
            uses_previous=uses_previous,
        ))

    return ExecutionPlan(original_task=task, steps=steps)


def _pipe_result(previous_result: Any) -> dict:
    """Build input args from a previous step's result.

    Extracts specific keys rather than blind **kwargs to avoid
    passing unexpected keys that break the next tool.
    """
    if isinstance(previous_result, dict):
        if "text" in previous_result:
            return {"text": previous_result["text"]}
        if "content" in previous_result:
            return {"text": previous_result["content"]}
        if "result" in previous_result:
            return {"input": previous_result["result"]}
        return {"input": previous_result}

    if isinstance(previous_result, str):
        return {"text": previous_result}

    return {"input": previous_result}


def _get_step_permissions(plan: ExecutionPlan) -> list[dict | None]:
    """Look up permissions for each step's resolved package."""
    from agentnode_sdk.installer import read_lockfile

    lock = read_lockfile()
    pkgs = lock.get("packages", {})

    result: list[dict | None] = []
    for step in plan.steps:
        found = None
        for _slug, info in pkgs.items():
            if step.capability in info.get("capability_ids", []):
                found = info.get("permissions")
                break
        result.append(found)
    return result


def check_plan_risk(plan: ExecutionPlan) -> list[str]:
    """Check for risky step combinations. Returns warning strings.

    Informational only — does not block execution.
    """
    if len(plan.steps) < 2:
        return []

    perms_list = _get_step_permissions(plan)
    warnings: list[str] = []

    for i in range(len(plan.steps) - 1):
        cur = perms_list[i]
        nxt = perms_list[i + 1]
        if cur is None or nxt is None:
            continue

        next_net = nxt.get("network_level", "none")

        if cur.get("filesystem_level", "none") in ("read", "write") and next_net != "none":
            warnings.append(
                f"Step {i + 1} reads filesystem -> Step {i + 2} has network access"
            )

        if cur.get("code_execution_level", "none") != "none" and next_net != "none":
            warnings.append(
                f"Step {i + 1} executes code -> Step {i + 2} has network access"
            )

    net_steps = [
        i for i, p in enumerate(perms_list)
        if p and p.get("network_level", "none") != "none"
    ]
    if len(net_steps) > 2:
        step_nums = ", ".join(str(s + 1) for s in net_steps)
        warnings.append(f"Steps {step_nums} all require network access")

    return warnings


def audit_plan(plan: ExecutionPlan, warnings: list[str]) -> None:
    """Log execution plan to audit trail as a single entry before execution."""
    import json as _json
    from datetime import datetime, timezone

    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": "plan_execute",
        "step_count": len(plan.steps),
        "capabilities": [s.capability for s in plan.steps],
        "data_flow": [
            {"step": s.index + 1, "uses_previous": s.uses_previous}
            for s in plan.steps
        ],
        "warnings": warnings,
    }

    try:
        from agentnode_sdk.config import config_dir

        audit_dir = config_dir()
        audit_dir.mkdir(parents=True, exist_ok=True)
        audit_path = audit_dir / "audit.jsonl"
        with open(audit_path, "a", encoding="utf-8") as f:
            f.write(_json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _find_installed_slug(capability: str) -> str | None:
    """Find an installed package that provides this capability."""
    from agentnode_sdk.installer import read_lockfile

    lock = read_lockfile()
    for slug, info in lock.get("packages", {}).items():
        if capability in info.get("capability_ids", []):
            return slug
    return None


def _install_for_capability(
    capability: str,
) -> tuple[str | None, bool, str | None]:
    """SUGGEST a package for a missing capability — NEVER install it (A1-E-Lock L3: no
    installation during a running plan). The planner may detect a gap and propose an
    explicit out-of-band install, but must not call ``client.install`` itself. A catalog
    ``resolve`` (read-only) is used only to name the suggested package.

    Returns (slug, was_installed, reason). ``was_installed`` is ALWAYS False; ``reason`` is
    a stable, human-readable install suggestion (or a not-found explanation)."""
    try:
        from agentnode_sdk.client import AgentNodeClient

        client = AgentNodeClient()
        try:
            resolve_result = client.resolve([capability])       # read-only catalog lookup
        finally:
            client.close()
    except Exception as e:
        return None, False, f"Capability lookup failed: {e}"

    if not resolve_result.results:
        return None, False, f"No package found in catalog for capability: {capability}"

    best = resolve_result.results[0]
    return None, False, (
        f"Capability '{capability}' is not installed and is NOT auto-installed during a "
        f"run. Install it explicitly: agentnode install {best.slug}"
    )


def plan_and_run(task: str, *, dry_run: bool = False) -> PlanResult:
    """Plan and execute a multi-step task.

    Each step runs via run_tool() with full policy/audit.
    Stops on first failure.
    """
    plan = plan_task(task)

    if dry_run:
        return PlanResult(plan=plan, steps=[], success=True)

    from agentnode_sdk.runner import run_tool

    t0 = time.monotonic()
    step_results: list[StepResult] = []
    previous_result: Any = None

    for step in plan.steps:
        slug = _find_installed_slug(step.capability)
        was_installed = False

        if slug is None:
            slug, was_installed, reason = _install_for_capability(step.capability)

        if slug is None:
            elapsed = (time.monotonic() - t0) * 1000
            step_results.append(StepResult(
                step=step,
                success=False,
                error=reason,
            ))
            return PlanResult(
                plan=plan,
                steps=step_results,
                success=False,
                duration_ms=round(elapsed, 1),
            )

        if step.uses_previous and previous_result is not None:
            run_args = _pipe_result(previous_result)
        else:
            run_args = step.input_args

        step_t0 = time.monotonic()
        result = run_tool(slug, **run_args)
        step_elapsed = (time.monotonic() - step_t0) * 1000

        step_results.append(StepResult(
            step=step,
            success=result.success,
            result=result.result,
            error=result.error,
            slug=slug,
            duration_ms=round(step_elapsed, 1),
            policy=result.policy,
            installed=was_installed,
        ))

        if not result.success:
            elapsed = (time.monotonic() - t0) * 1000
            return PlanResult(
                plan=plan,
                steps=step_results,
                success=False,
                duration_ms=round(elapsed, 1),
            )

        previous_result = result.result

    elapsed = (time.monotonic() - t0) * 1000
    return PlanResult(
        plan=plan,
        steps=step_results,
        success=True,
        duration_ms=round(elapsed, 1),
    )
