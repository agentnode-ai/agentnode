"""Agent execution runtime — ReAct-style agent loop.

Executes package_type=agent packages by loading the agent entrypoint
and providing it with a guarded AgentContext for tool access.

Security invariants:
- S4: Only explicit allowlist — no dynamic discovery, no delegation
- Audit entry for overall agent run
- Hard limits: max_tool_calls, max_runtime_seconds, max_iterations
- Termination: stop_on_consecutive_errors

v0.4 additions:
- run_id (UUID4) per agent execution for correlation
- Structured RunLog (JSONL) for observability
- Process-based isolation with configurable fallback to threads
- Conditional orchestration steps (when expressions)

v0.5 additions:
- LLM binding: AgentContext.llm for agent packages that use LLM reasoning
- call_llm() / call_llm_text() — structured LLM interface for agents
- allowed_tool_context() — policy-filtered tool context for LLM tool calls
- system_prompt injection from manifest
- Agent tiers: llm_only, llm_plus_tools, llm_plus_credentials
"""
from __future__ import annotations

import importlib
import logging
import multiprocessing
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from agentnode_sdk.models import RunToolResult
from agentnode_sdk.policy import PolicyResult, _trust_meets_minimum, audit_decision
from agentnode_sdk.run_log import RunLog, cleanup_old_runs

logger = logging.getLogger("agentnode.agent_runner")


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class AgentLimitExceeded(RuntimeError):
    """Raised when an agent exceeds a hard limit (tool calls, iterations)."""
    pass


class AgentTerminated(RuntimeError):
    """Raised when an agent is stopped by a termination condition."""
    pass


# ---------------------------------------------------------------------------
# LLM result types
# ---------------------------------------------------------------------------

@dataclass
class LLMResult:
    """Structured result from an LLM call.

    Returned by ``AgentContext.call_llm()``.
    """

    content: str
    tool_calls: list[dict] | None = None
    usage: dict | None = None
    model: str | None = None
    finish_reason: str | None = None


@dataclass
class ToolContext:
    """Policy-filtered tool context for LLM tool calls.

    Contains the list of packages the agent is allowed to use,
    formatted for passing to an LLM as available tools.
    Empty when tier is ``llm_only`` (allowed_packages=[]).
    """

    allowed_packages: list[str] = field(default_factory=list)
    tool_specs: list[dict] = field(default_factory=list)

    @property
    def has_tools(self) -> bool:
        return len(self.tool_specs) > 0


# ---------------------------------------------------------------------------
# AgentContext — guarded tool access for agent entrypoints
# ---------------------------------------------------------------------------

class AgentContext:
    """Execution context passed to agent entrypoint functions.

    The agent uses ``context.run_tool()`` to call allowed tools.
    The context enforces the allowlist (S4) and tracks limits.

    Agent entrypoint contract (v1)::

        def my_agent(context: AgentContext, **kwargs) -> Any:
            # context.goal — what the agent should accomplish
            # context.run_tool(slug, tool_name, **kw) — call an allowed tool
            # context.next_iteration() — mark the start of a loop iteration
            return final_result
    """

    def __init__(
        self,
        *,
        goal: str,
        allowed_packages: list[str] | None,
        max_tool_calls: int,
        max_iterations: int,
        stop_on_consecutive_errors: int,
        _agent_slug: str,
        _run_id: str | None = None,
        _run_log: RunLog | None = None,
        llm: Any | None = None,
        system_prompt: str | None = None,
    ) -> None:
        self._goal = goal
        # None = unrestricted tool access; [] = no tool access (llm_only)
        self._allowed_packages = list(allowed_packages) if allowed_packages is not None else None
        self._max_tool_calls = max_tool_calls
        self._max_iterations = max_iterations
        self._stop_on_consecutive_errors = stop_on_consecutive_errors
        self._agent_slug = _agent_slug
        self._run_id = _run_id
        self._run_log = _run_log
        self._llm = llm
        self._system_prompt = system_prompt

        # Mutable tracking state
        self._tool_calls_made = 0
        self._consecutive_errors = 0
        self._iteration = 0
        self._llm_calls_made = 0

    # --- Public properties ---

    @property
    def goal(self) -> str:
        return self._goal

    @property
    def allowed_packages(self) -> list[str] | None:
        return list(self._allowed_packages) if self._allowed_packages is not None else None

    @property
    def tool_calls_made(self) -> int:
        return self._tool_calls_made

    @property
    def tools_remaining(self) -> int:
        return max(0, self._max_tool_calls - self._tool_calls_made)

    @property
    def max_tool_calls(self) -> int:
        return self._max_tool_calls

    @property
    def max_iterations(self) -> int:
        return self._max_iterations

    @property
    def iteration(self) -> int:
        return self._iteration

    @property
    def run_id(self) -> str | None:
        return self._run_id

    @property
    def llm(self) -> Any | None:
        return self._llm

    @property
    def system_prompt(self) -> str | None:
        return self._system_prompt

    @property
    def llm_calls_made(self) -> int:
        return self._llm_calls_made

    # --- LLM access ---

    def call_llm(
        self,
        messages: list[dict],
        *,
        tool_context: ToolContext | None = None,
    ) -> LLMResult:
        """Call the bound LLM with messages and optional tool context.

        System prompt from manifest is automatically prepended.
        The LLM must be bound via the constructor (passed by run_agent).

        Args:
            messages: Chat messages in [{role, content}] format.
            tool_context: Optional tool context from allowed_tool_context().

        Returns:
            LLMResult with content, tool_calls, usage, model, finish_reason.

        Raises:
            RuntimeError: If no LLM is bound to this context.
        """
        if self._llm is None:
            raise RuntimeError(
                f"Agent '{self._agent_slug}' requires an LLM but none is bound. "
                "Ensure the runtime provides an LLM when starting this agent."
            )

        # Prepend system prompt if present
        effective_messages = list(messages)
        if self._system_prompt:
            # Check if there's already a system message
            has_system = any(m.get("role") == "system" for m in effective_messages)
            if not has_system:
                effective_messages.insert(
                    0, {"role": "system", "content": self._system_prompt}
                )

        t0 = time.monotonic()
        try:
            result = self._dispatch_llm_call(effective_messages, tool_context)
        except Exception as exc:
            duration_ms = (time.monotonic() - t0) * 1000
            if self._run_log:
                self._run_log.llm_call(
                    duration_ms=duration_ms,
                    error=str(exc),
                )
            raise

        duration_ms = (time.monotonic() - t0) * 1000
        self._llm_calls_made += 1

        # Log the LLM call
        if self._run_log:
            self._run_log.llm_call(
                model=result.model,
                duration_ms=duration_ms,
                usage=result.usage,
                finish_reason=result.finish_reason,
                tool_calls_count=len(result.tool_calls) if result.tool_calls else 0,
            )

        return result

    def call_llm_text(
        self,
        messages: list[dict],
        **kwargs: Any,
    ) -> str:
        """Convenience: call_llm() but return only content string."""
        return self.call_llm(messages, **kwargs).content

    def _dispatch_llm_call(
        self,
        messages: list[dict],
        tool_context: ToolContext | None,
    ) -> LLMResult:
        """Dispatch LLM call to the appropriate provider.

        The LLM object is expected to be one of:
        - An OpenAI-compatible client (has chat.completions.create)
        - An Anthropic client (has messages.create)
        - A callable (duck-typing: called with messages, returns LLMResult)
        - A dict with {client, provider, model} for explicit provider routing
        """
        llm = self._llm

        # Dict-style LLM binding: {client, provider, model}
        if isinstance(llm, dict):
            if "client" not in llm:
                raise RuntimeError(
                    "Dict-style LLM binding must include a 'client' key. "
                    "Expected: {client: <api_client>, provider: 'openai'|'anthropic', model: '...'}"
                )
            return self._call_llm_via_provider(
                client=llm["client"],
                provider=llm.get("provider", "openai"),
                model=llm.get("model", ""),
                messages=messages,
                tool_context=tool_context,
            )

        # Callable LLM (e.g. mock, custom wrapper)
        if callable(llm):
            raw = llm(messages, tool_context=tool_context)
            if isinstance(raw, LLMResult):
                return raw
            if isinstance(raw, str):
                return LLMResult(content=raw)
            if isinstance(raw, dict):
                return LLMResult(
                    content=raw.get("content", ""),
                    tool_calls=raw.get("tool_calls"),
                    usage=raw.get("usage"),
                    model=raw.get("model"),
                    finish_reason=raw.get("finish_reason"),
                )
            return LLMResult(content=str(raw))

        # OpenAI-compatible client (has chat.completions.create)
        if hasattr(llm, "chat") and hasattr(llm.chat, "completions"):
            return self._call_llm_via_provider(
                client=llm,
                provider="openai",
                model="",
                messages=messages,
                tool_context=tool_context,
            )

        # Anthropic client (has messages.create)
        if hasattr(llm, "messages") and hasattr(llm.messages, "create"):
            return self._call_llm_via_provider(
                client=llm,
                provider="anthropic",
                model="",
                messages=messages,
                tool_context=tool_context,
            )

        raise RuntimeError(
            f"Cannot use LLM of type {type(llm).__name__}. "
            "Provide a dict with {client, provider, model}, "
            "an OpenAI/Anthropic client, or a callable."
        )

    def _call_llm_via_provider(
        self,
        *,
        client: Any,
        provider: str,
        model: str,
        messages: list[dict],
        tool_context: ToolContext | None,
    ) -> LLMResult:
        """Call LLM through a known provider SDK."""
        if not model:
            try:
                from agentnode_sdk.compatibility import recommend_model
                rec = recommend_model(provider)
                if rec:
                    model = rec
            except Exception:
                pass

        if provider == "openai":
            return self._call_openai(client, model, messages, tool_context)
        elif provider == "anthropic":
            return self._call_anthropic(client, model, messages, tool_context)
        else:
            raise RuntimeError(f"Unsupported provider for call_llm: {provider}")

    def _call_openai(
        self,
        client: Any,
        model: str,
        messages: list[dict],
        tool_context: ToolContext | None,
    ) -> LLMResult:
        """OpenAI-compatible single LLM call (no tool loop)."""
        create_kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
        }
        if tool_context and tool_context.has_tools:
            create_kwargs["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": ts["name"],
                        "description": ts.get("description", ""),
                        "parameters": ts.get("input_schema", {}),
                    },
                }
                for ts in tool_context.tool_specs
            ]
            create_kwargs["parallel_tool_calls"] = False

        response = client.chat.completions.create(**create_kwargs)
        choice = response.choices[0] if response.choices else None
        if not choice or not choice.message:
            return LLMResult(content="")

        msg = choice.message
        tool_calls = None
        if msg.tool_calls:
            tool_calls = [
                {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                }
                for tc in msg.tool_calls
            ]

        usage = None
        if hasattr(response, "usage") and response.usage:
            usage = {
                "prompt_tokens": getattr(response.usage, "prompt_tokens", 0),
                "completion_tokens": getattr(response.usage, "completion_tokens", 0),
            }

        return LLMResult(
            content=msg.content or "",
            tool_calls=tool_calls,
            usage=usage,
            model=getattr(response, "model", model),
            finish_reason=getattr(choice, "finish_reason", None),
        )

    def _call_anthropic(
        self,
        client: Any,
        model: str,
        messages: list[dict],
        tool_context: ToolContext | None,
    ) -> LLMResult:
        """Anthropic single LLM call (no tool loop)."""
        # Extract system from messages (Anthropic uses separate param)
        system = None
        filtered: list[dict] = []
        for m in messages:
            if m.get("role") == "system":
                system = m.get("content", "")
            else:
                filtered.append(m)

        create_kwargs: dict[str, Any] = {
            "model": model,
            "messages": filtered,
            "max_tokens": 4096,
        }
        if system:
            create_kwargs["system"] = system
        if tool_context and tool_context.has_tools:
            create_kwargs["tools"] = [
                {
                    "name": ts["name"],
                    "description": ts.get("description", ""),
                    "input_schema": ts.get("input_schema", {}),
                }
                for ts in tool_context.tool_specs
            ]

        response = client.messages.create(**create_kwargs)

        # Extract content text
        content_parts = []
        tool_calls = None
        for block in response.content:
            if hasattr(block, "type"):
                if block.type == "text":
                    content_parts.append(block.text)
                elif block.type == "tool_use":
                    if tool_calls is None:
                        tool_calls = []
                    tool_calls.append({
                        "name": block.name,
                        "arguments": block.input,
                    })

        usage = None
        if hasattr(response, "usage") and response.usage:
            usage = {
                "prompt_tokens": getattr(response.usage, "input_tokens", 0),
                "completion_tokens": getattr(response.usage, "output_tokens", 0),
            }

        return LLMResult(
            content="\n".join(content_parts),
            tool_calls=tool_calls,
            usage=usage,
            model=getattr(response, "model", model),
            finish_reason=getattr(response, "stop_reason", None),
        )

    # --- Tool context ---

    def allowed_tool_context(self) -> ToolContext:
        """Return a policy-filtered tool context for LLM tool calls.

        Builds tool specs from allowed packages in the lockfile.
        Returns an empty ToolContext when no packages are allowed (llm_only)
        or when allowed_packages is None (unrestricted — loads all installed).
        Always returns a ToolContext object, never None.
        """
        if self._allowed_packages is not None and len(self._allowed_packages) == 0:
            return ToolContext()

        # Load tool specs from lockfile for allowed packages
        tool_specs: list[dict] = []
        # None = unrestricted, iterate all installed; list = only those slugs
        slugs_to_check = self._allowed_packages
        try:
            from agentnode_sdk.installer import read_lockfile
            lockfile = read_lockfile()
            packages = lockfile.get("packages", {})

            if slugs_to_check is None:
                slugs_to_check = list(packages.keys())

            for slug in slugs_to_check:
                pkg_info = packages.get(slug)
                if not pkg_info:
                    continue
                # Check trust level
                trust = pkg_info.get("trust_level", "unverified")
                if not _trust_meets_minimum(trust, "verified"):
                    continue
                for tool in pkg_info.get("tools", []):
                    tool_specs.append({
                        "name": f"{slug}:{tool.get('name', '')}",
                        "description": tool.get("description", ""),
                        "input_schema": tool.get("input_schema", {}),
                    })
        except Exception:
            logger.debug(
                "Failed to load tool specs for allowed_tool_context",
                exc_info=True,
            )

        return ToolContext(
            allowed_packages=list(slugs_to_check) if slugs_to_check else [],
            tool_specs=tool_specs,
        )

    # --- Tool access (S4: strict allowlist) ---

    def run_tool(
        self,
        slug: str,
        tool_name: str | None = None,
        **kwargs: Any,
    ) -> RunToolResult:
        """Run an allowed tool. Enforces allowlist (S4) and limits.

        Args:
            slug: Package slug to run.
            tool_name: Tool name for multi-tool packages.
            **kwargs: Tool arguments.

        Returns:
            RunToolResult from the tool execution.

        Raises:
            PermissionError: If package is not in the allowlist.
            AgentLimitExceeded: If max_tool_calls is exceeded.
            AgentTerminated: If consecutive error limit is exceeded.
        """
        # S4: Strict allowlist — no free tool search
        # Empty list means "no tool access" (e.g. llm_only tier),
        # not "unrestricted". Use None or omit for unrestricted.
        if self._allowed_packages is not None and slug not in self._allowed_packages:
            raise PermissionError(
                f"Agent '{self._agent_slug}' is not allowed to use package "
                f"'{slug}'. Allowed: {self._allowed_packages}"
            )

        # Tool call limit
        if self._tool_calls_made >= self._max_tool_calls:
            raise AgentLimitExceeded(
                f"Agent '{self._agent_slug}' exceeded max_tool_calls "
                f"({self._max_tool_calls})"
            )

        self._tool_calls_made += 1

        # Log tool_call event
        if self._run_log:
            self._run_log.tool_call(slug, tool_name)

        # Delegate to the main runner (lazy import to avoid circular dep)
        t0 = time.monotonic()
        from agentnode_sdk.runner import run_tool
        result = run_tool(slug, tool_name, **kwargs)
        duration_ms = (time.monotonic() - t0) * 1000

        # Log tool_result event
        if self._run_log:
            self._run_log.tool_result(
                slug, tool_name,
                success=result.success,
                duration_ms=duration_ms,
                error=result.error,
            )

        # Track consecutive errors for termination
        if not result.success:
            self._consecutive_errors += 1
            if self._consecutive_errors >= self._stop_on_consecutive_errors:
                raise AgentTerminated(
                    f"Agent '{self._agent_slug}' stopped after "
                    f"{self._consecutive_errors} consecutive tool errors"
                )
        else:
            self._consecutive_errors = 0

        return result

    # --- Iteration tracking ---

    def next_iteration(self) -> None:
        """Mark the start of a new iteration. Call at the top of each loop.

        Raises:
            AgentLimitExceeded: If max_iterations is exceeded.
        """
        self._iteration += 1
        if self._run_log:
            self._run_log.iteration(self._iteration)
        if self._iteration > self._max_iterations:
            raise AgentLimitExceeded(
                f"Agent '{self._agent_slug}' exceeded max_iterations "
                f"({self._max_iterations})"
            )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_agent(
    slug: str,
    *,
    entry: dict,
    goal: str | None = None,
    timeout: float | None = None,
    llm: Any | None = None,
    **kwargs: Any,
) -> RunToolResult:
    """Execute an agent package.

    Flow:
    1. Generate run_id (UUID4)
    2. Validate agent: section in lockfile entry
    3. Check agent-specific policy (trust >= trusted)
    4. Build AgentContext with guarded tool access + RunLog
    5. Load and call agent entrypoint with timeout
    6. Audit the agent run (with run_id)
    7. Return RunToolResult (with run_id)

    Args:
        slug: Agent package slug.
        entry: Lockfile entry dict (must contain 'agent' section).
        goal: Override goal (uses agent.goal from manifest if not provided).
        timeout: Override timeout in seconds (uses max_runtime_seconds if None).
        **kwargs: Additional arguments passed to the agent entrypoint.
    """
    t0 = time.monotonic()
    run_id = str(uuid.uuid4())
    run_log = RunLog(run_id)

    # --- 1. Validate agent section ---
    agent_config = entry.get("agent")
    if not agent_config or not isinstance(agent_config, dict):
        return RunToolResult(
            success=False,
            error=f"Package '{slug}' has no valid 'agent' section in lockfile.",
            mode_used="agent",
            run_id=run_id,
        )

    # --- 2. Agent-specific policy: trust >= trusted ---
    trust_level = entry.get("trust_level", "unverified")
    if not _trust_meets_minimum(trust_level, "trusted"):
        _audit_agent_run(
            slug, success=False,
            reason=f"trust_level '{trust_level}' below 'trusted'",
            run_id=run_id,
        )
        return RunToolResult(
            success=False,
            error=(
                f"Agent packages require trust level >= 'trusted' "
                f"('{slug}' has '{trust_level}'). "
                "Only trusted and curated agents can be executed."
            ),
            mode_used="agent",
            run_id=run_id,
        )

    # --- 3. Build configuration from agent section ---
    limits = agent_config.get("limits") or {}
    max_iterations = limits.get("max_iterations", 12)
    max_tool_calls = limits.get("max_tool_calls", 40)
    max_runtime_seconds = limits.get("max_runtime_seconds", 180)
    effective_timeout = timeout if timeout is not None else max_runtime_seconds

    tool_access = agent_config.get("tool_access") or {}
    # None = unrestricted (no tool_access section or no allowed_packages key)
    # []   = no tool access (explicit empty list, e.g. llm_only tier)
    allowed_packages = tool_access.get("allowed_packages")

    termination = agent_config.get("termination") or {}
    stop_on_consecutive_errors = termination.get("stop_on_consecutive_errors", 3)

    effective_goal = goal or agent_config.get("goal", "")

    # --- 4. Sequential orchestration dispatch ---
    orchestration = agent_config.get("orchestration")
    if isinstance(orchestration, dict) and orchestration.get("mode") == "sequential":
        return _run_sequential(slug, agent_config, kwargs, effective_timeout, run_id=run_id, run_log=run_log)

    # --- 5. Entrypoint validation (not needed for sequential) ---
    entrypoint = agent_config.get("entrypoint", "")
    if not entrypoint:
        return RunToolResult(
            success=False,
            error=f"Agent '{slug}' has no entrypoint defined.",
            mode_used="agent",
            run_id=run_id,
        )

    # --- 6. Build AgentContext ---
    system_prompt = agent_config.get("system_prompt")
    context = AgentContext(
        goal=effective_goal,
        allowed_packages=allowed_packages,
        max_tool_calls=max_tool_calls,
        max_iterations=max_iterations,
        stop_on_consecutive_errors=stop_on_consecutive_errors,
        _agent_slug=slug,
        _run_id=run_id,
        _run_log=run_log,
        llm=llm,
        system_prompt=system_prompt,
    )

    # --- 7. Load entrypoint ---
    try:
        func = _load_agent_entrypoint(slug, entrypoint)
    except Exception as exc:
        elapsed = (time.monotonic() - t0) * 1000
        _audit_agent_run(slug, success=False, reason=f"load_failed: {exc}", run_id=run_id)
        run_log.run_end(success=False, duration_ms=elapsed, error=str(exc))
        return RunToolResult(
            success=False,
            error=f"Failed to load agent entrypoint: {exc}",
            mode_used="agent",
            duration_ms=round(elapsed, 1),
            run_id=run_id,
        )

    # --- 8. Execute ---
    isolation = agent_config.get("isolation", "thread")

    tier = agent_config.get("tier", "")
    run_log.run_start(
        slug, effective_goal,
        max_tool_calls=max_tool_calls,
        max_iterations=max_iterations,
        timeout=effective_timeout,
        isolation=isolation,
        tier=tier,
        has_llm=llm is not None,
    )

    logger.info(
        "agent_run: slug=%s run_id=%s goal=%r timeout=%.1fs max_tools=%d max_iter=%d isolation=%s tier=%s has_llm=%s",
        slug, run_id, effective_goal[:80], effective_timeout,
        max_tool_calls, max_iterations, isolation, tier, llm is not None,
    )

    run_result: RunToolResult
    try:
        if isolation == "process":
            result = _execute_with_process(
                func, context, kwargs,
                timeout=effective_timeout,
            )
        else:
            result = _execute_with_timeout(
                func, context, kwargs,
                timeout=effective_timeout,
            )
        elapsed = (time.monotonic() - t0) * 1000

        _audit_agent_run(
            slug, success=True,
            tool_calls=context.tool_calls_made,
            iterations=context.iteration,
            run_id=run_id,
        )
        run_log.run_end(success=True, duration_ms=elapsed)

        run_result = RunToolResult(
            success=True,
            result=result,
            mode_used="agent",
            duration_ms=round(elapsed, 1),
            run_id=run_id,
        )

    except AgentLimitExceeded as exc:
        elapsed = (time.monotonic() - t0) * 1000
        _audit_agent_run(
            slug, success=False, reason=str(exc),
            tool_calls=context.tool_calls_made,
            iterations=context.iteration,
            run_id=run_id,
        )
        run_log.run_end(success=False, duration_ms=elapsed, error=str(exc))
        run_result = RunToolResult(
            success=False,
            error=str(exc),
            mode_used="agent",
            duration_ms=round(elapsed, 1),
            run_id=run_id,
        )

    except AgentTerminated as exc:
        elapsed = (time.monotonic() - t0) * 1000
        _audit_agent_run(
            slug, success=False, reason=str(exc),
            tool_calls=context.tool_calls_made,
            iterations=context.iteration,
            run_id=run_id,
        )
        run_log.run_end(success=False, duration_ms=elapsed, error=str(exc))
        run_result = RunToolResult(
            success=False,
            error=str(exc),
            mode_used="agent",
            duration_ms=round(elapsed, 1),
            run_id=run_id,
        )

    except _TimeoutReached:
        elapsed = (time.monotonic() - t0) * 1000
        _audit_agent_run(
            slug, success=False, reason="timeout",
            tool_calls=context.tool_calls_made,
            iterations=context.iteration,
            run_id=run_id,
        )
        run_log.run_end(success=False, duration_ms=elapsed, error="timeout")
        run_result = RunToolResult(
            success=False,
            error=(
                f"Agent '{slug}' timed out after {effective_timeout}s "
                f"({context.tool_calls_made} tool calls made)"
            ),
            mode_used="agent",
            duration_ms=round(elapsed, 1),
            timed_out=True,
            run_id=run_id,
        )

    except PermissionError as exc:
        elapsed = (time.monotonic() - t0) * 1000
        _audit_agent_run(
            slug, success=False, reason=str(exc),
            tool_calls=context.tool_calls_made,
            run_id=run_id,
        )
        run_log.run_end(success=False, duration_ms=elapsed, error=str(exc))
        run_result = RunToolResult(
            success=False,
            error=str(exc),
            mode_used="agent",
            duration_ms=round(elapsed, 1),
            run_id=run_id,
        )

    except Exception as exc:
        elapsed = (time.monotonic() - t0) * 1000
        _audit_agent_run(
            slug, success=False,
            reason=f"{type(exc).__name__}: {exc}",
            tool_calls=context.tool_calls_made,
            run_id=run_id,
        )
        run_log.run_end(success=False, duration_ms=elapsed, error=str(exc))
        run_result = RunToolResult(
            success=False,
            error=f"{type(exc).__name__}: {exc}",
            mode_used="agent",
            duration_ms=round(elapsed, 1),
            run_id=run_id,
        )

    # Run log retention cleanup (once per agent run, non-blocking)
    try:
        cleanup_old_runs()
    except Exception:
        logger.debug("Run log cleanup failed", exc_info=True)

    return run_result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _TimeoutReached(Exception):
    """Internal: raised when agent execution exceeds max_runtime_seconds."""
    pass


def _load_agent_entrypoint(slug: str, entrypoint: str):
    """Load the agent function from module.path:function format.

    Raises:
        ValueError: If entrypoint format is invalid.
        ImportError: If module cannot be imported.
        AttributeError: If function does not exist in the module.
        TypeError: If the attribute is not callable.
    """
    if ":" not in entrypoint:
        raise ValueError(
            f"Agent entrypoint must be module.path:function format "
            f"(got '{entrypoint}')"
        )

    module_path, func_name = entrypoint.rsplit(":", 1)

    try:
        module = importlib.import_module(module_path)
    except ImportError as exc:
        raise ImportError(
            f"Cannot import agent module '{module_path}': {exc}"
        ) from exc

    func = getattr(module, func_name, None)
    if func is None:
        raise AttributeError(
            f"Agent module '{module_path}' has no function '{func_name}'"
        )

    if not callable(func):
        raise TypeError(
            f"Agent entrypoint '{entrypoint}' is not callable"
        )

    return func


def _execute_with_timeout(
    func,
    context: AgentContext,
    kwargs: dict,
    *,
    timeout: float,
) -> Any:
    """Run the agent function with a hard timeout (thread-based).

    Uses a daemon thread so the main thread is not blocked past the
    timeout. If the agent exceeds the timeout, _TimeoutReached is raised.
    The daemon thread continues until it finishes or the process exits.
    """
    result_box: list[Any] = [None]
    error_box: list[BaseException | None] = [None]

    def _run():
        try:
            result_box[0] = func(context, **kwargs)
        except BaseException as exc:
            error_box[0] = exc

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    thread.join(timeout=timeout)

    if thread.is_alive():
        raise _TimeoutReached()

    if error_box[0] is not None:
        raise error_box[0]

    return result_box[0]


def _execute_with_process(
    func,
    context: AgentContext,
    kwargs: dict,
    *,
    timeout: float,
    grace_period: float = 5.0,
) -> Any:
    """Run the agent function in a separate process with hard kill on timeout.

    Unlike thread-based execution, processes CAN be force-killed.
    Result is returned via a multiprocessing.Queue.
    """
    result_queue: multiprocessing.Queue = multiprocessing.Queue()

    def _wrapper():
        try:
            result = func(context, **kwargs)
            result_queue.put(("ok", result))
        except BaseException as exc:
            result_queue.put(("error", exc))

    proc = multiprocessing.Process(target=_wrapper, daemon=True)
    proc.start()
    proc.join(timeout=timeout)

    if proc.is_alive():
        # Graceful termination attempt
        proc.terminate()
        proc.join(timeout=grace_period)
        if proc.is_alive():
            proc.kill()
            proc.join(timeout=2)
        raise _TimeoutReached()

    # Process finished — read result
    if result_queue.empty():
        raise RuntimeError("Agent process ended without producing a result")

    status, payload = result_queue.get_nowait()
    if status == "error":
        raise payload
    return payload


def _audit_agent_run(
    slug: str,
    *,
    success: bool,
    reason: str = "",
    tool_calls: int = 0,
    iterations: int = 0,
    run_id: str | None = None,
) -> None:
    """Audit an agent run. Never crashes the caller."""
    try:
        result = PolicyResult(
            action="allow" if success else "deny",
            reason=reason or ("agent_completed" if success else "agent_failed"),
            source="agent_runner",
        )
        audit_decision(
            result, "agent_run", slug,
            tool_name=None,
            trust_level=None,
            run_id=run_id,
        )
    except Exception:
        logger.debug("Failed to audit agent run: %s", slug, exc_info=True)


# ---------------------------------------------------------------------------
# Sequential Orchestration
# ---------------------------------------------------------------------------

def _run_sequential(
    slug: str,
    agent_config: dict,
    initial_input: dict,
    effective_timeout: float,
    *,
    run_id: str | None = None,
    run_log: RunLog | None = None,
) -> RunToolResult:
    """Execute a sequential orchestration pipeline.

    Steps are executed in order. Each step calls a tool via the standard
    ``run_tool()`` pipeline (full policy check + audit per call).
    Results are passed between steps via ``$input`` and ``$steps`` references
    in ``input_mapping``.

    Steps may have a ``when`` condition. If the condition evaluates to false,
    the step is skipped (not treated as an error).

    Stops on the first failed step. Returns the last step's result on success.
    """
    t0 = time.monotonic()

    orchestration = agent_config.get("orchestration", {})
    steps = orchestration.get("steps", [])

    if not steps:
        return RunToolResult(
            success=False,
            error=f"Agent '{slug}' has no orchestration steps defined.",
            mode_used="agent",
            run_id=run_id,
        )

    tool_access = agent_config.get("tool_access") or {}
    allowed_packages = tool_access.get("allowed_packages")

    if run_log:
        run_log.run_start(slug, agent_config.get("goal", ""), mode="sequential", steps=len(steps))

    step_results: dict[str, Any] = {}
    step_details: list[dict] = []

    for i, step in enumerate(steps):
        # Check overall timeout between steps
        elapsed = time.monotonic() - t0
        if elapsed > effective_timeout:
            _audit_agent_run(
                slug, success=False,
                reason=f"sequential_timeout after {i}/{len(steps)} steps",
                tool_calls=i,
                run_id=run_id,
            )
            if run_log:
                run_log.run_end(success=False, duration_ms=elapsed * 1000, error="timeout")
            return RunToolResult(
                success=False,
                error=(
                    f"Sequential orchestration timed out after "
                    f"{i}/{len(steps)} steps ({effective_timeout}s limit)"
                ),
                result={"steps_completed": step_details},
                mode_used="agent",
                duration_ms=round(elapsed * 1000, 1),
                timed_out=True,
                run_id=run_id,
            )

        step_name = step.get("name", f"step_{i}")
        tool_ref = step.get("tool", "")
        input_mapping = step.get("input_mapping") or {}

        # --- Conditional step (v0.4) ---
        when_expr = step.get("when")
        if when_expr is not None:
            condition_met = _evaluate_condition(when_expr, initial_input, step_results)
            if not condition_met:
                logger.info(
                    "agent_step_skip: slug=%s step=%s when=%r → skipped",
                    slug, step_name, when_expr,
                )
                if run_log:
                    run_log.step_result(step_name, success=True, skipped=True)
                step_details.append({
                    "name": step_name,
                    "tool": tool_ref,
                    "success": True,
                    "skipped": True,
                    "duration_ms": 0.0,
                })
                continue

        if not tool_ref:
            return RunToolResult(
                success=False,
                error=f"Step '{step_name}' has no tool reference.",
                mode_used="agent",
                duration_ms=round((time.monotonic() - t0) * 1000, 1),
                run_id=run_id,
            )

        # Parse tool reference: "slug:tool_name" or "slug"
        tool_slug, tool_name = _parse_tool_reference(tool_ref)

        # Allowlist check (S4) — empty list = no access, not unrestricted
        if allowed_packages is not None and tool_slug not in allowed_packages:
            _audit_agent_run(
                slug, success=False,
                reason=f"step '{step_name}': '{tool_slug}' not in allowlist",
                tool_calls=i,
                run_id=run_id,
            )
            return RunToolResult(
                success=False,
                error=(
                    f"Step '{step_name}': package '{tool_slug}' "
                    f"is not in allowed_packages."
                ),
                mode_used="agent",
                duration_ms=round((time.monotonic() - t0) * 1000, 1),
                run_id=run_id,
            )

        # Resolve input mapping
        try:
            resolved_input = _resolve_input_mapping(
                input_mapping, initial_input, step_results,
            )
        except ValueError as exc:
            return RunToolResult(
                success=False,
                error=f"Step '{step_name}' input mapping error: {exc}",
                mode_used="agent",
                duration_ms=round((time.monotonic() - t0) * 1000, 1),
                run_id=run_id,
            )

        # Execute the step via the standard runner pipeline
        logger.info(
            "agent_step: slug=%s step=%s (%d/%d) tool=%s",
            slug, step_name, i + 1, len(steps), tool_ref,
        )

        if run_log:
            run_log.step_start(step_name, tool_ref)

        step_t0 = time.monotonic()
        from agentnode_sdk.runner import run_tool
        result = run_tool(tool_slug, tool_name, **resolved_input)
        step_elapsed = (time.monotonic() - step_t0) * 1000

        if run_log:
            run_log.step_result(step_name, success=result.success, duration_ms=step_elapsed)

        step_details.append({
            "name": step_name,
            "tool": tool_ref,
            "success": result.success,
            "duration_ms": round(step_elapsed, 1),
            "skipped": False,
        })

        # Stop on first failure
        if not result.success:
            elapsed_total = (time.monotonic() - t0) * 1000
            _audit_agent_run(
                slug, success=False,
                reason=f"step '{step_name}' failed: {result.error}",
                tool_calls=i + 1,
                run_id=run_id,
            )
            if run_log:
                run_log.run_end(success=False, duration_ms=elapsed_total, error=result.error)
            return RunToolResult(
                success=False,
                error=f"Step '{step_name}' failed: {result.error}",
                result={"steps_completed": step_details},
                mode_used="agent",
                duration_ms=round(elapsed_total, 1),
                run_id=run_id,
            )

        step_results[step_name] = result.result

    # All steps succeeded — return last step's result
    elapsed_total = (time.monotonic() - t0) * 1000
    last_step_name = steps[-1].get("name", f"step_{len(steps) - 1}")
    final_result = step_results.get(last_step_name)

    _audit_agent_run(
        slug, success=True,
        tool_calls=len(steps),
        reason=f"sequential completed {len(steps)} steps",
        run_id=run_id,
    )
    if run_log:
        run_log.run_end(success=True, duration_ms=elapsed_total)

    return RunToolResult(
        success=True,
        result=final_result,
        mode_used="agent",
        duration_ms=round(elapsed_total, 1),
        run_id=run_id,
    )


# ---------------------------------------------------------------------------
# Condition evaluator (v0.4 — PR 7)
# ---------------------------------------------------------------------------

def _evaluate_condition(
    expr: str,
    initial_input: dict,
    step_results: dict[str, Any],
) -> bool:
    """Evaluate a simple condition expression.

    Supported:
    - ``$ref == value``
    - ``$ref != value``
    - ``$ref is null``
    - ``$ref is not null``

    Unresolvable $ref → false (not error).
    """
    expr = expr.strip()

    # "is not null" check
    if expr.endswith(" is not null"):
        ref = expr[: -len(" is not null")].strip()
        val = _safe_resolve(ref, initial_input, step_results)
        return val is not _UNRESOLVABLE

    # "is null" check
    if expr.endswith(" is null"):
        ref = expr[: -len(" is null")].strip()
        val = _safe_resolve(ref, initial_input, step_results)
        return val is _UNRESOLVABLE or val is None

    # == / !=
    for op in ("!=", "=="):
        if f" {op} " in expr:
            ref_part, value_part = expr.split(f" {op} ", 1)
            ref_part = ref_part.strip()
            value_part = value_part.strip()

            resolved = _safe_resolve(ref_part, initial_input, step_results)
            if resolved is _UNRESOLVABLE:
                return False

            target = _parse_literal(value_part)

            if op == "==":
                return resolved == target
            else:
                return resolved != target

    # Unknown expression syntax → false
    logger.warning("Unrecognized condition expression: %r", expr)
    return False


class _Unresolvable:
    """Sentinel for unresolvable references."""
    pass


_UNRESOLVABLE = _Unresolvable()


def _safe_resolve(
    ref: str,
    initial_input: dict,
    step_results: dict[str, Any],
) -> Any:
    """Resolve a $-reference, returning _UNRESOLVABLE on failure."""
    try:
        return _resolve_value(ref, initial_input, step_results)
    except (ValueError, KeyError):
        return _UNRESOLVABLE


def _parse_literal(value: str) -> Any:
    """Parse a literal value from a condition expression."""
    if value == "null":
        return None
    if value == "true":
        return True
    if value == "false":
        return False
    # Quoted string
    if (value.startswith('"') and value.endswith('"')) or \
       (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
    # Try numeric
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _parse_tool_reference(ref: str) -> tuple[str, str | None]:
    """Parse 'slug:tool_name' or 'slug' into (slug, tool_name)."""
    if ":" in ref:
        slug, tool_name = ref.split(":", 1)
        return slug, tool_name
    return ref, None


def _resolve_input_mapping(
    mapping: dict,
    initial_input: dict,
    step_results: dict[str, Any],
) -> dict:
    """Resolve all $-references in an input_mapping dict.

    Supported expressions:
    - ``$input.key`` — value from the initial kwargs
    - ``$steps.name`` — full result of a previous step
    - Literal values — passed through as-is
    """
    return {
        key: _resolve_value(value, initial_input, step_results)
        for key, value in mapping.items()
    }


def _resolve_value(
    value: Any,
    initial_input: dict,
    step_results: dict[str, Any],
) -> Any:
    """Resolve a single value from an input_mapping entry."""
    if not isinstance(value, str) or not value.startswith("$"):
        return value

    if value.startswith("$input."):
        key = value[len("$input."):]
        if key not in initial_input:
            raise ValueError(f"Input key '{key}' not found in kwargs")
        return initial_input[key]

    if value.startswith("$steps."):
        step_name = value[len("$steps."):]
        if step_name not in step_results:
            raise ValueError(
                f"Step '{step_name}' not found or not yet executed"
            )
        return step_results[step_name]

    raise ValueError(f"Unknown variable reference: {value}")
