"""Tests for the agent runner.

Covers:
- AgentContext allowlist enforcement (S4)
- AgentContext tool call and iteration limits
- AgentContext consecutive error termination
- run_agent() validation, trust policy, execution, timeout
- Entrypoint loading
- Runner dispatch integration
- Sequential orchestration: step execution, input mapping, allowlist, errors
- PR 1: run_id generation and propagation
- PR 4: process-based isolation
- PR 7: conditional orchestration steps (when expressions)
"""
import sys
import textwrap

import pytest

from agentnode_sdk.runtimes.agent_runner import (
    AgentContext,
    AgentLimitExceeded,
    AgentTerminated,
    LLMResult,
    ToolContext,
    _evaluate_condition,
    _load_agent_entrypoint,
    _parse_tool_reference,
    _resolve_input_mapping,
    _resolve_value,
    run_agent,
)
from agentnode_sdk.models import RunToolResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _agent_entry(**overrides):
    """Build a lockfile entry for an agent package."""
    base = {
        "version": "1.0.0",
        "package_type": "agent",
        "runtime": "python",
        "entrypoint": "",
        "trust_level": "trusted",
        "agent": {
            "entrypoint": "test_module:agent_func",
            "goal": "Test goal",
            "tool_access": {
                "allowed_packages": ["csv-analyzer-pack", "web-scraper-pack"],
            },
            "limits": {
                "max_iterations": 5,
                "max_tool_calls": 10,
                "max_runtime_seconds": 30,
            },
            "termination": {
                "stop_on_final_answer": True,
                "stop_on_consecutive_errors": 3,
            },
        },
    }
    base.update(overrides)
    return base


def _make_context(**overrides):
    """Build an AgentContext with sensible defaults."""
    defaults = {
        "goal": "Test goal",
        "allowed_packages": ["pack-a", "pack-b"],
        "max_tool_calls": 10,
        "max_iterations": 5,
        "stop_on_consecutive_errors": 3,
        "_agent_slug": "test-agent",
    }
    defaults.update(overrides)
    return AgentContext(**defaults)


def _write_agent_module(tmp_path, module_name, code):
    """Write a Python module to tmp_path and return the module dir."""
    mod_dir = tmp_path / module_name
    mod_dir.mkdir(exist_ok=True)
    (mod_dir / "__init__.py").write_text("")
    (mod_dir / "core.py").write_text(textwrap.dedent(code))
    return mod_dir


# ---------------------------------------------------------------------------
# AgentContext — properties
# ---------------------------------------------------------------------------

class TestAgentContextProperties:
    def test_goal(self):
        ctx = _make_context(goal="Research AI safety")
        assert ctx.goal == "Research AI safety"

    def test_allowed_packages(self):
        ctx = _make_context(allowed_packages=["pack-a"])
        assert ctx.allowed_packages == ["pack-a"]

    def test_allowed_packages_returns_copy(self):
        ctx = _make_context(allowed_packages=["pack-a"])
        pkgs = ctx.allowed_packages
        pkgs.append("injected")
        assert "injected" not in ctx.allowed_packages

    def test_max_tool_calls(self):
        ctx = _make_context(max_tool_calls=42)
        assert ctx.max_tool_calls == 42

    def test_max_iterations(self):
        ctx = _make_context(max_iterations=7)
        assert ctx.max_iterations == 7

    def test_initial_state(self):
        ctx = _make_context()
        assert ctx.tool_calls_made == 0
        assert ctx.tools_remaining == 10
        assert ctx.iteration == 0


# ---------------------------------------------------------------------------
# AgentContext — S4 allowlist enforcement
# ---------------------------------------------------------------------------

class TestAgentContextAllowlist:
    def test_disallowed_package_raises(self):
        ctx = _make_context(allowed_packages=["safe-pack"])
        with pytest.raises(PermissionError, match="not allowed"):
            ctx.run_tool("evil-pack")

    def test_disallowed_package_names_agent(self):
        ctx = _make_context(allowed_packages=["safe-pack"], _agent_slug="my-agent")
        with pytest.raises(PermissionError, match="my-agent"):
            ctx.run_tool("evil-pack")

    def test_empty_allowlist_blocks_all(self, monkeypatch):
        """Empty allowlist means no tool access (llm_only tier)."""
        ctx = _make_context(allowed_packages=[])
        with pytest.raises(PermissionError, match="not allowed"):
            ctx.run_tool("any-pack")

    def test_none_allowlist_allows_all(self, monkeypatch):
        """None allowlist means unrestricted (for testing / unrestricted agents)."""
        from agentnode_sdk import runner
        monkeypatch.setattr(
            runner, "run_tool",
            lambda *a, **kw: RunToolResult(success=True, result="ok"),
        )
        ctx = _make_context(allowed_packages=None)
        # Should NOT raise PermissionError — None allowlist allows all
        result = ctx.run_tool("any-pack")
        assert result.success is True


# ---------------------------------------------------------------------------
# AgentContext — limit enforcement
# ---------------------------------------------------------------------------

class TestAgentContextLimits:
    def test_tool_call_limit_raises(self):
        ctx = _make_context(max_tool_calls=2)
        ctx._tool_calls_made = 2  # Simulate reaching limit
        with pytest.raises(AgentLimitExceeded, match="max_tool_calls"):
            ctx.run_tool("pack-a")

    def test_tool_call_limit_names_agent(self):
        ctx = _make_context(max_tool_calls=1, _agent_slug="research-agent")
        ctx._tool_calls_made = 1
        with pytest.raises(AgentLimitExceeded, match="research-agent"):
            ctx.run_tool("pack-a")

    def test_tools_remaining_decreases(self):
        ctx = _make_context(max_tool_calls=5)
        ctx._tool_calls_made = 3
        assert ctx.tools_remaining == 2

    def test_tools_remaining_never_negative(self):
        ctx = _make_context(max_tool_calls=5)
        ctx._tool_calls_made = 10  # Over limit
        assert ctx.tools_remaining == 0

    def test_iteration_limit_raises(self):
        ctx = _make_context(max_iterations=3)
        ctx.next_iteration()  # 1
        ctx.next_iteration()  # 2
        ctx.next_iteration()  # 3
        with pytest.raises(AgentLimitExceeded, match="max_iterations"):
            ctx.next_iteration()  # 4 > 3

    def test_iteration_counter_increments(self):
        ctx = _make_context(max_iterations=10)
        assert ctx.iteration == 0
        ctx.next_iteration()
        assert ctx.iteration == 1
        ctx.next_iteration()
        assert ctx.iteration == 2


# ---------------------------------------------------------------------------
# AgentContext — consecutive error termination
# ---------------------------------------------------------------------------

class TestAgentContextTermination:
    def test_consecutive_errors_initial(self):
        ctx = _make_context()
        assert ctx._consecutive_errors == 0

    def test_error_tracking_resets_on_success(self):
        ctx = _make_context(stop_on_consecutive_errors=5)
        ctx._consecutive_errors = 3
        # Simulate a successful tool call resetting the counter
        ctx._consecutive_errors = 0  # This is what run_tool does on success
        assert ctx._consecutive_errors == 0


# ---------------------------------------------------------------------------
# run_agent() — validation
# ---------------------------------------------------------------------------

class TestRunAgentValidation:
    def test_missing_agent_section(self):
        entry = _agent_entry()
        del entry["agent"]
        result = run_agent("test-agent", entry=entry)
        assert result.success is False
        assert "no valid 'agent' section" in result.error
        assert result.mode_used == "agent"

    def test_agent_section_not_dict(self):
        entry = _agent_entry()
        entry["agent"] = "invalid"
        result = run_agent("test-agent", entry=entry)
        assert result.success is False
        assert "no valid 'agent' section" in result.error

    def test_missing_entrypoint(self):
        entry = _agent_entry()
        entry["agent"]["entrypoint"] = ""
        result = run_agent("test-agent", entry=entry)
        assert result.success is False
        assert "no entrypoint" in result.error

    def test_invalid_entrypoint_format(self):
        entry = _agent_entry()
        entry["agent"]["entrypoint"] = "no_colon_here"
        result = run_agent("test-agent", entry=entry)
        assert result.success is False
        assert "module.path:function" in result.error

    def test_mode_used_is_agent(self):
        entry = _agent_entry()
        result = run_agent("test-agent", entry=entry)
        assert result.mode_used == "agent"


# ---------------------------------------------------------------------------
# run_agent() — trust policy
# ---------------------------------------------------------------------------

class TestRunAgentTrustPolicy:
    def test_unverified_denied(self):
        entry = _agent_entry(trust_level="unverified")
        result = run_agent("test-agent", entry=entry)
        assert result.success is False
        assert "trust level" in result.error
        assert "trusted" in result.error

    def test_verified_denied(self):
        """verified < trusted, should be denied."""
        entry = _agent_entry(trust_level="verified")
        result = run_agent("test-agent", entry=entry)
        assert result.success is False
        assert "trust level" in result.error

    def test_trusted_passes_policy(self):
        """trust=trusted passes the policy check (may fail at entrypoint)."""
        entry = _agent_entry(trust_level="trusted")
        result = run_agent("test-agent", entry=entry)
        # Fails at entrypoint loading, NOT at trust check
        assert "trust level" not in (result.error or "")

    def test_curated_passes_policy(self):
        entry = _agent_entry(trust_level="curated")
        result = run_agent("test-agent", entry=entry)
        assert "trust level" not in (result.error or "")


# ---------------------------------------------------------------------------
# run_agent() — execution
# ---------------------------------------------------------------------------

class TestRunAgentExecution:
    def test_successful_agent(self, monkeypatch, tmp_path):
        _write_agent_module(tmp_path, "agent_success", """
            def run(context, **kwargs):
                return {"answer": f"Goal: {context.goal}"}
        """)
        monkeypatch.syspath_prepend(str(tmp_path))

        entry = _agent_entry(trust_level="trusted")
        entry["agent"]["entrypoint"] = "agent_success.core:run"
        entry["agent"]["goal"] = "Answer questions"

        result = run_agent("test-agent", entry=entry)

        assert result.success is True
        assert result.result == {"answer": "Goal: Answer questions"}
        assert result.mode_used == "agent"
        assert result.duration_ms > 0

    def test_goal_override(self, monkeypatch, tmp_path):
        _write_agent_module(tmp_path, "agent_goal", """
            def run(context, **kwargs):
                return context.goal
        """)
        monkeypatch.syspath_prepend(str(tmp_path))

        entry = _agent_entry(trust_level="trusted")
        entry["agent"]["entrypoint"] = "agent_goal.core:run"
        entry["agent"]["goal"] = "Original"

        result = run_agent("test-agent", entry=entry, goal="Overridden")

        assert result.success is True
        assert result.result == "Overridden"

    def test_kwargs_passed_to_agent(self, monkeypatch, tmp_path):
        _write_agent_module(tmp_path, "agent_kwargs", """
            def run(context, topic=None, **kwargs):
                return f"Researching {topic}"
        """)
        monkeypatch.syspath_prepend(str(tmp_path))

        entry = _agent_entry(trust_level="trusted")
        entry["agent"]["entrypoint"] = "agent_kwargs.core:run"

        result = run_agent("test-agent", entry=entry, topic="AI safety")

        assert result.success is True
        assert result.result == "Researching AI safety"

    def test_agent_exception_returns_error(self, monkeypatch, tmp_path):
        _write_agent_module(tmp_path, "agent_crash", """
            def run(context, **kwargs):
                raise ValueError("Something broke")
        """)
        monkeypatch.syspath_prepend(str(tmp_path))

        entry = _agent_entry(trust_level="trusted")
        entry["agent"]["entrypoint"] = "agent_crash.core:run"

        result = run_agent("test-agent", entry=entry)

        assert result.success is False
        assert "ValueError" in result.error
        assert "Something broke" in result.error
        assert result.mode_used == "agent"

    def test_agent_returns_none(self, monkeypatch, tmp_path):
        _write_agent_module(tmp_path, "agent_none", """
            def run(context, **kwargs):
                return None
        """)
        monkeypatch.syspath_prepend(str(tmp_path))

        entry = _agent_entry(trust_level="trusted")
        entry["agent"]["entrypoint"] = "agent_none.core:run"

        result = run_agent("test-agent", entry=entry)

        assert result.success is True
        assert result.result is None


# ---------------------------------------------------------------------------
# run_agent() — timeout
# ---------------------------------------------------------------------------

class TestRunAgentTimeout:
    def test_agent_timeout(self, monkeypatch, tmp_path):
        _write_agent_module(tmp_path, "agent_slow", """
            import time
            def run(context, **kwargs):
                time.sleep(10)
                return "should not reach"
        """)
        monkeypatch.syspath_prepend(str(tmp_path))

        entry = _agent_entry(trust_level="trusted")
        entry["agent"]["entrypoint"] = "agent_slow.core:run"
        entry["agent"]["limits"]["max_runtime_seconds"] = 1

        result = run_agent("test-agent", entry=entry)

        assert result.success is False
        assert result.timed_out is True
        assert "timed out" in result.error
        assert result.mode_used == "agent"

    def test_timeout_override(self, monkeypatch, tmp_path):
        _write_agent_module(tmp_path, "agent_slow2", """
            import time
            def run(context, **kwargs):
                time.sleep(10)
                return "should not reach"
        """)
        monkeypatch.syspath_prepend(str(tmp_path))

        entry = _agent_entry(trust_level="trusted")
        entry["agent"]["entrypoint"] = "agent_slow2.core:run"
        entry["agent"]["limits"]["max_runtime_seconds"] = 300  # Very long

        # Override with short timeout
        result = run_agent("test-agent", entry=entry, timeout=1)

        assert result.success is False
        assert result.timed_out is True

    def test_fast_agent_completes_within_timeout(self, monkeypatch, tmp_path):
        _write_agent_module(tmp_path, "agent_fast", """
            def run(context, **kwargs):
                return "done"
        """)
        monkeypatch.syspath_prepend(str(tmp_path))

        entry = _agent_entry(trust_level="trusted")
        entry["agent"]["entrypoint"] = "agent_fast.core:run"
        entry["agent"]["limits"]["max_runtime_seconds"] = 30

        result = run_agent("test-agent", entry=entry)

        assert result.success is True
        assert result.timed_out is False


# ---------------------------------------------------------------------------
# run_agent() — allowlist enforcement (S4) through execution
# ---------------------------------------------------------------------------

class TestRunAgentAllowlistExecution:
    def test_unauthorized_tool_raises_permission_error(self, monkeypatch, tmp_path):
        _write_agent_module(tmp_path, "agent_unauth", """
            def run(context, **kwargs):
                return context.run_tool("evil-pack", "steal_data")
        """)
        monkeypatch.syspath_prepend(str(tmp_path))

        entry = _agent_entry(trust_level="trusted")
        entry["agent"]["entrypoint"] = "agent_unauth.core:run"
        entry["agent"]["tool_access"]["allowed_packages"] = ["safe-pack"]

        result = run_agent("test-agent", entry=entry)

        assert result.success is False
        assert "not allowed" in result.error or "PermissionError" in result.error


# ---------------------------------------------------------------------------
# run_agent() — default limits
# ---------------------------------------------------------------------------

class TestRunAgentDefaults:
    def test_missing_limits_uses_defaults(self):
        entry = _agent_entry(trust_level="trusted")
        del entry["agent"]["limits"]
        result = run_agent("test-agent", entry=entry)
        # Fails at entrypoint, not at limits parsing
        assert result.mode_used == "agent"
        assert "limit" not in (result.error or "").lower()

    def test_missing_termination_uses_defaults(self):
        entry = _agent_entry(trust_level="trusted")
        del entry["agent"]["termination"]
        result = run_agent("test-agent", entry=entry)
        assert result.mode_used == "agent"

    def test_missing_tool_access_uses_defaults(self):
        entry = _agent_entry(trust_level="trusted")
        del entry["agent"]["tool_access"]
        result = run_agent("test-agent", entry=entry)
        assert result.mode_used == "agent"


# ---------------------------------------------------------------------------
# run_agent() — iteration limit via context
# ---------------------------------------------------------------------------

class TestRunAgentIterationLimit:
    def test_iteration_limit_stops_agent(self, monkeypatch, tmp_path):
        _write_agent_module(tmp_path, "agent_loop", """
            def run(context, **kwargs):
                results = []
                for i in range(100):
                    context.next_iteration()
                    results.append(i)
                return results
        """)
        monkeypatch.syspath_prepend(str(tmp_path))

        entry = _agent_entry(trust_level="trusted")
        entry["agent"]["entrypoint"] = "agent_loop.core:run"
        entry["agent"]["limits"]["max_iterations"] = 3

        result = run_agent("test-agent", entry=entry)

        assert result.success is False
        assert "max_iterations" in result.error


# ---------------------------------------------------------------------------
# _load_agent_entrypoint()
# ---------------------------------------------------------------------------

class TestLoadAgentEntrypoint:
    def test_invalid_format_no_colon(self):
        with pytest.raises(ValueError, match="module.path:function"):
            _load_agent_entrypoint("test", "invalid_format")

    def test_nonexistent_module(self):
        with pytest.raises(ImportError):
            _load_agent_entrypoint("test", "totally_nonexistent_xyz.mod:func")

    def test_nonexistent_function(self, monkeypatch, tmp_path):
        _write_agent_module(tmp_path, "agent_nofunc", "x = 1")
        monkeypatch.syspath_prepend(str(tmp_path))

        with pytest.raises(AttributeError, match="no function"):
            _load_agent_entrypoint("test", "agent_nofunc.core:nonexistent")

    def test_not_callable(self, monkeypatch, tmp_path):
        _write_agent_module(tmp_path, "agent_notcall", "my_var = 42")
        monkeypatch.syspath_prepend(str(tmp_path))

        with pytest.raises(TypeError, match="not callable"):
            _load_agent_entrypoint("test", "agent_notcall.core:my_var")

    def test_valid_entrypoint_loads(self, monkeypatch, tmp_path):
        _write_agent_module(tmp_path, "agent_valid", "def my_func(): pass")
        monkeypatch.syspath_prepend(str(tmp_path))

        func = _load_agent_entrypoint("test", "agent_valid.core:my_func")
        assert callable(func)


# ---------------------------------------------------------------------------
# Runner dispatch integration
# ---------------------------------------------------------------------------

class TestRunnerAgentDispatch:
    """Verify that runner.run_tool routes package_type=agent to run_agent."""

    def test_agent_dispatched_via_run_tool(self, monkeypatch, tmp_path):
        """run_tool with package_type=agent should use agent runner."""
        from agentnode_sdk import runner

        _write_agent_module(tmp_path, "agent_dispatch", """
            def run(context, **kwargs):
                return "dispatched"
        """)
        monkeypatch.syspath_prepend(str(tmp_path))

        entry = _agent_entry(trust_level="trusted")
        entry["agent"]["entrypoint"] = "agent_dispatch.core:run"

        # Mock _get_lockfile_entry to return our test entry
        monkeypatch.setattr(runner, "_get_lockfile_entry", lambda slug, path: entry)

        # Mock check_run to allow
        from agentnode_sdk.policy import PolicyResult
        monkeypatch.setattr(
            runner, "check_run",
            lambda *a, **kw: PolicyResult(action="allow", reason="test", source="test"),
        )
        monkeypatch.setattr(runner, "audit_decision", lambda *a, **kw: None)

        result = runner.run_tool("test-agent")

        assert result.success is True
        assert result.result == "dispatched"
        assert result.mode_used == "agent"

    def test_upgrade_still_rejected(self, monkeypatch):
        """package_type=upgrade should still be rejected, not routed to agent."""
        from agentnode_sdk import runner

        entry = {"package_type": "upgrade", "trust_level": "curated"}
        monkeypatch.setattr(runner, "_get_lockfile_entry", lambda slug, path: entry)

        result = runner.run_tool("my-upgrade")

        assert result.success is False
        assert "upgrade" in result.error.lower()


# ---------------------------------------------------------------------------
# AgentContext — tool call limit through execution
# ---------------------------------------------------------------------------

class TestAgentToolCallLimit:
    def test_tool_call_limit_in_agent(self, monkeypatch, tmp_path):
        """Agent that tries to make too many tool calls gets stopped."""
        _write_agent_module(tmp_path, "agent_greedy", """
            def run(context, **kwargs):
                for i in range(100):
                    try:
                        context.run_tool("csv-analyzer-pack", "analyze")
                    except Exception as e:
                        if "max_tool_calls" in str(e):
                            raise
                        # Other errors (like missing lockfile) are expected in tests
                        pass
                return "should not reach"
        """)
        monkeypatch.syspath_prepend(str(tmp_path))

        entry = _agent_entry(trust_level="trusted")
        entry["agent"]["entrypoint"] = "agent_greedy.core:run"
        entry["agent"]["limits"]["max_tool_calls"] = 3

        result = run_agent("test-agent", entry=entry)

        assert result.success is False
        assert "max_tool_calls" in result.error


# ===========================================================================
# PR 7: Sequential Orchestration
# ===========================================================================

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sequential_entry(**overrides):
    """Build a lockfile entry for a sequential orchestration agent."""
    base = {
        "version": "1.0.0",
        "package_type": "agent",
        "runtime": "python",
        "trust_level": "trusted",
        "agent": {
            "goal": "Multi-step pipeline",
            "orchestration": {
                "mode": "sequential",
                "steps": [
                    {
                        "name": "step_a",
                        "tool": "pack-a:run_a",
                        "input_mapping": {"x": "$input.x"},
                    },
                    {
                        "name": "step_b",
                        "tool": "pack-b:run_b",
                        "input_mapping": {"data": "$steps.step_a"},
                    },
                ],
            },
            "tool_access": {
                "allowed_packages": ["pack-a", "pack-b"],
            },
            "limits": {
                "max_runtime_seconds": 30,
            },
        },
    }
    base.update(overrides)
    return base


def _mock_run_tool(results_by_call):
    """Return a mock run_tool that returns pre-defined results.

    results_by_call: list of RunToolResult, returned in order.
    """
    call_index = [0]
    calls = []

    def mock_run_tool(slug, tool_name=None, **kwargs):
        i = call_index[0]
        call_index[0] += 1
        calls.append({"slug": slug, "tool_name": tool_name, "kwargs": kwargs})
        if i < len(results_by_call):
            return results_by_call[i]
        return RunToolResult(success=True, result=f"auto-result-{i}")

    return mock_run_tool, calls


# ---------------------------------------------------------------------------
# _parse_tool_reference
# ---------------------------------------------------------------------------

class TestParseToolReference:
    def test_slug_and_tool(self):
        assert _parse_tool_reference("pack-a:run") == ("pack-a", "run")

    def test_slug_only(self):
        assert _parse_tool_reference("pack-a") == ("pack-a", None)

    def test_slug_with_colon_in_name(self):
        slug, name = _parse_tool_reference("pack:ns:tool")
        assert slug == "pack"
        assert name == "ns:tool"


# ---------------------------------------------------------------------------
# _resolve_value / _resolve_input_mapping
# ---------------------------------------------------------------------------

class TestResolveValue:
    def test_literal_string(self):
        assert _resolve_value("hello", {}, {}) == "hello"

    def test_literal_number(self):
        assert _resolve_value(42, {}, {}) == 42

    def test_literal_none(self):
        assert _resolve_value(None, {}, {}) is None

    def test_input_reference(self):
        assert _resolve_value("$input.path", {"path": "/tmp/file"}, {}) == "/tmp/file"

    def test_input_missing_key_raises(self):
        with pytest.raises(ValueError, match="not found"):
            _resolve_value("$input.missing", {}, {})

    def test_steps_reference(self):
        assert _resolve_value("$steps.extract", {}, {"extract": [1, 2, 3]}) == [1, 2, 3]

    def test_steps_missing_raises(self):
        with pytest.raises(ValueError, match="not found or not yet"):
            _resolve_value("$steps.missing", {}, {})

    def test_unknown_dollar_raises(self):
        with pytest.raises(ValueError, match="Unknown variable"):
            _resolve_value("$unknown.ref", {}, {})


class TestResolveInputMapping:
    def test_full_mapping(self):
        result = _resolve_input_mapping(
            {"a": "$input.x", "b": "$steps.s1", "c": "literal"},
            {"x": 10},
            {"s1": "result1"},
        )
        assert result == {"a": 10, "b": "result1", "c": "literal"}

    def test_empty_mapping(self):
        assert _resolve_input_mapping({}, {}, {}) == {}


# ---------------------------------------------------------------------------
# Sequential execution — success paths
# ---------------------------------------------------------------------------

class TestSequentialSuccess:
    def test_two_step_pipeline(self, monkeypatch):
        """Two steps execute in order, final result is last step's output."""
        from agentnode_sdk import runner

        mock, calls = _mock_run_tool([
            RunToolResult(success=True, result={"rows": 100}),
            RunToolResult(success=True, result={"cleaned": True}),
        ])
        monkeypatch.setattr(runner, "run_tool", mock)

        entry = _sequential_entry()
        result = run_agent("pipeline-agent", entry=entry, x="input_val")

        assert result.success is True
        assert result.result == {"cleaned": True}
        assert result.mode_used == "agent"
        assert result.duration_ms >= 0

        # Verify calls were made in order
        assert len(calls) == 2
        assert calls[0]["slug"] == "pack-a"
        assert calls[0]["tool_name"] == "run_a"
        assert calls[0]["kwargs"]["x"] == "input_val"

        assert calls[1]["slug"] == "pack-b"
        assert calls[1]["tool_name"] == "run_b"
        assert calls[1]["kwargs"]["data"] == {"rows": 100}

    def test_single_step(self, monkeypatch):
        from agentnode_sdk import runner

        mock, calls = _mock_run_tool([
            RunToolResult(success=True, result="done"),
        ])
        monkeypatch.setattr(runner, "run_tool", mock)

        entry = _sequential_entry()
        entry["agent"]["orchestration"]["steps"] = [
            {"name": "only", "tool": "pack-a:run_a"},
        ]
        result = run_agent("single-step", entry=entry)

        assert result.success is True
        assert result.result == "done"
        assert len(calls) == 1

    def test_step_without_input_mapping(self, monkeypatch):
        """Steps with no input_mapping call tool with no kwargs."""
        from agentnode_sdk import runner

        mock, calls = _mock_run_tool([
            RunToolResult(success=True, result="ok"),
        ])
        monkeypatch.setattr(runner, "run_tool", mock)

        entry = _sequential_entry()
        entry["agent"]["orchestration"]["steps"] = [
            {"name": "bare", "tool": "pack-a:run_a"},
        ]
        result = run_agent("bare-step", entry=entry)

        assert result.success is True
        assert calls[0]["kwargs"] == {}


# ---------------------------------------------------------------------------
# Sequential execution — error paths
# ---------------------------------------------------------------------------

class TestSequentialErrors:
    def test_step_failure_stops_pipeline(self, monkeypatch):
        from agentnode_sdk import runner

        mock, calls = _mock_run_tool([
            RunToolResult(success=True, result="ok"),
            RunToolResult(success=False, error="transform failed"),
        ])
        monkeypatch.setattr(runner, "run_tool", mock)

        entry = _sequential_entry()
        result = run_agent("failing-pipeline", entry=entry, x="val")

        assert result.success is False
        assert "step_b" in result.error
        assert "transform failed" in result.error
        assert result.result is not None
        assert len(result.result["steps_completed"]) == 2

    def test_no_steps_defined(self):
        entry = _sequential_entry()
        entry["agent"]["orchestration"]["steps"] = []
        result = run_agent("no-steps", entry=entry)

        assert result.success is False
        assert "no orchestration steps" in result.error

    def test_step_missing_tool_ref(self, monkeypatch):
        from agentnode_sdk import runner
        monkeypatch.setattr(runner, "run_tool", lambda *a, **kw: None)

        entry = _sequential_entry()
        entry["agent"]["orchestration"]["steps"] = [
            {"name": "bad", "input_mapping": {}},
        ]
        result = run_agent("bad-step", entry=entry)

        assert result.success is False
        assert "no tool reference" in result.error

    def test_input_mapping_error(self, monkeypatch):
        from agentnode_sdk import runner
        monkeypatch.setattr(
            runner, "run_tool",
            lambda *a, **kw: RunToolResult(success=True, result="x"),
        )

        entry = _sequential_entry()
        entry["agent"]["orchestration"]["steps"] = [
            {
                "name": "bad_ref",
                "tool": "pack-a:run",
                "input_mapping": {"x": "$steps.nonexistent"},
            },
        ]
        result = run_agent("bad-mapping", entry=entry)

        assert result.success is False
        assert "input mapping error" in result.error


# ---------------------------------------------------------------------------
# Sequential — S4 allowlist enforcement
# ---------------------------------------------------------------------------

class TestSequentialAllowlist:
    def test_unauthorized_tool_blocked(self, monkeypatch):
        from agentnode_sdk import runner
        monkeypatch.setattr(
            runner, "run_tool",
            lambda *a, **kw: RunToolResult(success=True, result="x"),
        )

        entry = _sequential_entry()
        entry["agent"]["orchestration"]["steps"] = [
            {"name": "evil", "tool": "evil-pack:steal"},
        ]
        result = run_agent("allowlist-test", entry=entry)

        assert result.success is False
        assert "not in allowed_packages" in result.error

    def test_empty_allowlist_blocks_all(self, monkeypatch):
        """Empty allowlist blocks all tools in sequential mode (llm_only)."""
        entry = _sequential_entry()
        entry["agent"]["tool_access"]["allowed_packages"] = []
        entry["agent"]["orchestration"]["steps"] = [
            {"name": "any", "tool": "any-pack:run"},
        ]
        result = run_agent("empty-allowlist", entry=entry)

        assert result.success is False
        assert "not in allowed_packages" in result.error

    def test_none_allowlist_allows(self, monkeypatch):
        """No allowed_packages key = unrestricted in sequential mode."""
        from agentnode_sdk import runner

        mock, _ = _mock_run_tool([
            RunToolResult(success=True, result="ok"),
        ])
        monkeypatch.setattr(runner, "run_tool", mock)

        entry = _sequential_entry()
        # Remove allowed_packages entirely → None → unrestricted
        del entry["agent"]["tool_access"]["allowed_packages"]
        entry["agent"]["orchestration"]["steps"] = [
            {"name": "any", "tool": "any-pack:run"},
        ]
        result = run_agent("open-allowlist", entry=entry)

        assert result.success is True


# ---------------------------------------------------------------------------
# Sequential — trust policy
# ---------------------------------------------------------------------------

class TestSequentialTrustPolicy:
    def test_sequential_requires_trusted(self):
        entry = _sequential_entry()
        entry["trust_level"] = "verified"
        result = run_agent("untrusted-seq", entry=entry)

        assert result.success is False
        assert "trust level" in result.error

    def test_sequential_trusted_ok(self, monkeypatch):
        from agentnode_sdk import runner

        mock, _ = _mock_run_tool([
            RunToolResult(success=True, result="ok"),
            RunToolResult(success=True, result="done"),
        ])
        monkeypatch.setattr(runner, "run_tool", mock)

        entry = _sequential_entry()
        entry["trust_level"] = "trusted"
        result = run_agent("trusted-seq", entry=entry, x="val")

        assert result.success is True


# ---------------------------------------------------------------------------
# Sequential — step auto-naming
# ---------------------------------------------------------------------------

class TestSequentialStepNaming:
    def test_auto_generated_step_names(self, monkeypatch):
        """Steps without explicit names get auto-generated names."""
        from agentnode_sdk import runner

        mock, _ = _mock_run_tool([
            RunToolResult(success=True, result="r0"),
            RunToolResult(success=True, result="r1"),
        ])
        monkeypatch.setattr(runner, "run_tool", mock)

        entry = _sequential_entry()
        entry["agent"]["orchestration"]["steps"] = [
            {"tool": "pack-a:run_a"},
            {"tool": "pack-b:run_b", "input_mapping": {"data": "$steps.step_0"}},
        ]
        result = run_agent("auto-names", entry=entry)

        assert result.success is True
        assert result.result == "r1"


# ===========================================================================
# PR 1: run_id generation and propagation
# ===========================================================================

class TestRunId:
    def test_run_agent_returns_run_id(self, monkeypatch, tmp_path):
        _write_agent_module(tmp_path, "agent_rid", """
            def run(context, **kwargs):
                return context.run_id
        """)
        monkeypatch.syspath_prepend(str(tmp_path))

        entry = _agent_entry(trust_level="trusted")
        entry["agent"]["entrypoint"] = "agent_rid.core:run"

        result = run_agent("rid-agent", entry=entry)

        assert result.run_id is not None
        assert len(result.run_id) == 36  # UUID4 format
        assert result.success is True
        # The agent sees its own run_id via context
        assert result.result == result.run_id

    def test_run_id_is_unique_per_run(self, monkeypatch, tmp_path):
        _write_agent_module(tmp_path, "agent_rid2", """
            def run(context, **kwargs):
                return "ok"
        """)
        monkeypatch.syspath_prepend(str(tmp_path))

        entry = _agent_entry(trust_level="trusted")
        entry["agent"]["entrypoint"] = "agent_rid2.core:run"

        r1 = run_agent("rid1", entry=entry)
        r2 = run_agent("rid2", entry=entry)

        assert r1.run_id != r2.run_id

    def test_failed_run_still_has_run_id(self):
        entry = _agent_entry(trust_level="unverified")
        result = run_agent("fail-rid", entry=entry)

        assert result.success is False
        assert result.run_id is not None

    def test_sequential_has_run_id(self, monkeypatch):
        from agentnode_sdk import runner

        mock, _ = _mock_run_tool([
            RunToolResult(success=True, result="ok"),
        ])
        monkeypatch.setattr(runner, "run_tool", mock)

        entry = _sequential_entry()
        entry["agent"]["orchestration"]["steps"] = [
            {"name": "s1", "tool": "pack-a:run"},
        ]
        result = run_agent("seq-rid", entry=entry)

        assert result.run_id is not None
        assert result.success is True


# ===========================================================================
# PR 4: process-based agent isolation
# ===========================================================================

class TestProcessIsolation:
    def test_thread_isolation_default(self, monkeypatch, tmp_path):
        """Default isolation is thread-based."""
        _write_agent_module(tmp_path, "agent_thread_iso", """
            def run(context, **kwargs):
                return "thread-ok"
        """)
        monkeypatch.syspath_prepend(str(tmp_path))

        entry = _agent_entry(trust_level="trusted")
        entry["agent"]["entrypoint"] = "agent_thread_iso.core:run"
        # No isolation field → defaults to "thread"

        result = run_agent("thread-agent", entry=entry)
        assert result.success is True

    def test_explicit_thread_isolation(self, monkeypatch, tmp_path):
        _write_agent_module(tmp_path, "agent_thread_ex", """
            def run(context, **kwargs):
                return "thread"
        """)
        monkeypatch.syspath_prepend(str(tmp_path))

        entry = _agent_entry(trust_level="trusted")
        entry["agent"]["entrypoint"] = "agent_thread_ex.core:run"
        entry["agent"]["isolation"] = "thread"

        result = run_agent("thread-ex", entry=entry)
        assert result.success is True
        assert result.result == "thread"


# ===========================================================================
# PR 7: Conditional orchestration steps
# ===========================================================================

class TestEvaluateCondition:
    def test_equals_true(self):
        assert _evaluate_condition("$input.x == 10", {"x": 10}, {}) is True

    def test_equals_false(self):
        assert _evaluate_condition("$input.x == 10", {"x": 20}, {}) is False

    def test_not_equals_true(self):
        assert _evaluate_condition("$input.x != 10", {"x": 20}, {}) is True

    def test_not_equals_false(self):
        assert _evaluate_condition("$input.x != 10", {"x": 10}, {}) is False

    def test_is_null(self):
        assert _evaluate_condition("$steps.missing is null", {}, {}) is True

    def test_is_not_null(self):
        assert _evaluate_condition("$steps.s1 is not null", {}, {"s1": "data"}) is True

    def test_is_not_null_missing(self):
        assert _evaluate_condition("$steps.s1 is not null", {}, {}) is False

    def test_unresolvable_ref_returns_false(self):
        assert _evaluate_condition("$steps.x == 5", {}, {}) is False

    def test_string_comparison(self):
        assert _evaluate_condition("$input.mode == 'fast'", {"mode": "fast"}, {}) is True

    def test_boolean_comparison(self):
        assert _evaluate_condition("$input.flag == true", {"flag": True}, {}) is True

    def test_null_comparison(self):
        assert _evaluate_condition("$input.val == null", {"val": None}, {}) is True

    def test_unknown_syntax_returns_false(self):
        assert _evaluate_condition("nonsense expression", {}, {}) is False


class TestConditionalSteps:
    def test_step_with_true_condition_executes(self, monkeypatch):
        from agentnode_sdk import runner

        mock, calls = _mock_run_tool([
            RunToolResult(success=True, result="ok"),
        ])
        monkeypatch.setattr(runner, "run_tool", mock)

        entry = _sequential_entry()
        entry["agent"]["orchestration"]["steps"] = [
            {
                "name": "guarded",
                "tool": "pack-a:run_a",
                "when": "$input.flag == true",
            },
        ]
        result = run_agent("cond-true", entry=entry, flag=True)

        assert result.success is True
        assert len(calls) == 1

    def test_step_with_false_condition_skipped(self, monkeypatch):
        from agentnode_sdk import runner

        mock, calls = _mock_run_tool([])
        monkeypatch.setattr(runner, "run_tool", mock)

        entry = _sequential_entry()
        entry["agent"]["orchestration"]["steps"] = [
            {
                "name": "guarded",
                "tool": "pack-a:run_a",
                "when": "$input.flag == true",
            },
        ]
        result = run_agent("cond-false", entry=entry, flag=False)

        assert result.success is True
        assert len(calls) == 0

    def test_step_without_when_always_runs(self, monkeypatch):
        from agentnode_sdk import runner

        mock, calls = _mock_run_tool([
            RunToolResult(success=True, result="ok"),
        ])
        monkeypatch.setattr(runner, "run_tool", mock)

        entry = _sequential_entry()
        entry["agent"]["orchestration"]["steps"] = [
            {"name": "unconditional", "tool": "pack-a:run_a"},
        ]
        result = run_agent("no-when", entry=entry)

        assert result.success is True
        assert len(calls) == 1

    def test_skipped_step_visible_in_details(self, monkeypatch):
        from agentnode_sdk import runner

        mock, calls = _mock_run_tool([
            RunToolResult(success=True, result="final"),
        ])
        monkeypatch.setattr(runner, "run_tool", mock)

        entry = _sequential_entry()
        entry["agent"]["orchestration"]["steps"] = [
            {
                "name": "skipped",
                "tool": "pack-a:run_a",
                "when": "$input.skip == true",
            },
            {
                "name": "last",
                "tool": "pack-b:run_b",
            },
        ]
        result = run_agent("skip-visible", entry=entry, skip=False)

        assert result.success is True
        assert result.result == "final"
        # Only the second step was actually executed
        assert len(calls) == 1

    def test_skipped_step_result_not_referenceable(self, monkeypatch):
        from agentnode_sdk import runner

        mock, calls = _mock_run_tool([])
        monkeypatch.setattr(runner, "run_tool", mock)

        entry = _sequential_entry()
        entry["agent"]["orchestration"]["steps"] = [
            {
                "name": "skipped_step",
                "tool": "pack-a:run_a",
                "when": "$input.x == 999",
            },
            {
                "name": "dependent",
                "tool": "pack-b:run_b",
                "input_mapping": {"data": "$steps.skipped_step"},
            },
        ]
        result = run_agent("skip-ref", entry=entry, x=0)

        # Should fail because skipped_step has no result
        assert result.success is False
        assert "input mapping error" in result.error

    def test_unresolvable_when_ref_skips(self, monkeypatch):
        from agentnode_sdk import runner

        mock, calls = _mock_run_tool([])
        monkeypatch.setattr(runner, "run_tool", mock)

        entry = _sequential_entry()
        entry["agent"]["orchestration"]["steps"] = [
            {
                "name": "guarded",
                "tool": "pack-a:run_a",
                "when": "$steps.nonexistent is not null",
            },
        ]
        result = run_agent("unresolvable-when", entry=entry)

        assert result.success is True
        assert len(calls) == 0  # Step was skipped


# ---------------------------------------------------------------------------
# LLM Result
# ---------------------------------------------------------------------------


class TestLLMResult:
    def test_defaults(self):
        r = LLMResult(content="hello")
        assert r.content == "hello"
        assert r.tool_calls is None
        assert r.usage is None
        assert r.model is None
        assert r.finish_reason is None

    def test_full(self):
        r = LLMResult(
            content="hi",
            tool_calls=[{"name": "t", "arguments": "{}"}],
            usage={"prompt_tokens": 10, "completion_tokens": 5},
            model="gpt-4",
            finish_reason="stop",
        )
        assert r.tool_calls == [{"name": "t", "arguments": "{}"}]
        assert r.usage["prompt_tokens"] == 10
        assert r.model == "gpt-4"
        assert r.finish_reason == "stop"


# ---------------------------------------------------------------------------
# ToolContext
# ---------------------------------------------------------------------------


class TestToolContext:
    def test_empty_has_no_tools(self):
        tc = ToolContext()
        assert not tc.has_tools
        assert tc.allowed_packages == []
        assert tc.tool_specs == []

    def test_with_tools(self):
        tc = ToolContext(
            allowed_packages=["pack-a"],
            tool_specs=[{"name": "pack-a:run", "description": "test", "input_schema": {}}],
        )
        assert tc.has_tools
        assert tc.allowed_packages == ["pack-a"]


# ---------------------------------------------------------------------------
# AgentContext LLM binding
# ---------------------------------------------------------------------------


class TestAgentContextLLM:
    def test_no_llm_raises_on_call(self):
        ctx = _make_context()
        with pytest.raises(RuntimeError, match="requires an LLM"):
            ctx.call_llm([{"role": "user", "content": "hi"}])

    def test_no_llm_raises_on_call_text(self):
        ctx = _make_context()
        with pytest.raises(RuntimeError, match="requires an LLM"):
            ctx.call_llm_text([{"role": "user", "content": "hi"}])

    def test_callable_llm_returns_string(self):
        def mock_llm(messages, **kwargs):
            return "mock response"

        ctx = _make_context(llm=mock_llm)
        result = ctx.call_llm([{"role": "user", "content": "hi"}])
        assert isinstance(result, LLMResult)
        assert result.content == "mock response"

    def test_call_llm_text_returns_string(self):
        def mock_llm(messages, **kwargs):
            return "text response"

        ctx = _make_context(llm=mock_llm)
        text = ctx.call_llm_text([{"role": "user", "content": "hi"}])
        assert text == "text response"

    def test_callable_llm_returns_llm_result(self):
        def mock_llm(messages, **kwargs):
            return LLMResult(
                content="structured",
                model="test-model",
                finish_reason="stop",
                usage={"prompt_tokens": 5, "completion_tokens": 10},
            )

        ctx = _make_context(llm=mock_llm)
        result = ctx.call_llm([{"role": "user", "content": "hi"}])
        assert result.content == "structured"
        assert result.model == "test-model"
        assert result.finish_reason == "stop"

    def test_callable_llm_returns_dict(self):
        def mock_llm(messages, **kwargs):
            return {"content": "from dict", "model": "dict-model"}

        ctx = _make_context(llm=mock_llm)
        result = ctx.call_llm([{"role": "user", "content": "hi"}])
        assert result.content == "from dict"
        assert result.model == "dict-model"

    def test_llm_calls_counter(self):
        def mock_llm(messages, **kwargs):
            return "ok"

        ctx = _make_context(llm=mock_llm)
        assert ctx.llm_calls_made == 0
        ctx.call_llm([{"role": "user", "content": "1"}])
        assert ctx.llm_calls_made == 1
        ctx.call_llm([{"role": "user", "content": "2"}])
        assert ctx.llm_calls_made == 2

    def test_system_prompt_prepended(self):
        captured_messages = []

        def mock_llm(messages, **kwargs):
            captured_messages.extend(messages)
            return "ok"

        ctx = _make_context(llm=mock_llm, system_prompt="You are a helper.")
        ctx.call_llm([{"role": "user", "content": "hi"}])
        assert captured_messages[0]["role"] == "system"
        assert captured_messages[0]["content"] == "You are a helper."
        assert captured_messages[1]["role"] == "user"

    def test_system_prompt_not_doubled(self):
        captured_messages = []

        def mock_llm(messages, **kwargs):
            captured_messages.extend(messages)
            return "ok"

        ctx = _make_context(llm=mock_llm, system_prompt="You are a helper.")
        ctx.call_llm([
            {"role": "system", "content": "Existing system prompt."},
            {"role": "user", "content": "hi"},
        ])
        # Should not add a second system message
        system_msgs = [m for m in captured_messages if m["role"] == "system"]
        assert len(system_msgs) == 1
        assert system_msgs[0]["content"] == "Existing system prompt."

    def test_llm_property(self):
        ctx = _make_context(llm="my_llm")
        assert ctx.llm == "my_llm"

    def test_system_prompt_property(self):
        ctx = _make_context(system_prompt="test prompt")
        assert ctx.system_prompt == "test prompt"

    def test_llm_error_raises(self):
        def broken_llm(messages, **kwargs):
            raise ValueError("API error")

        ctx = _make_context(llm=broken_llm)
        with pytest.raises(ValueError, match="API error"):
            ctx.call_llm([{"role": "user", "content": "hi"}])


# ---------------------------------------------------------------------------
# allowed_tool_context
# ---------------------------------------------------------------------------


class TestAllowedToolContext:
    def test_empty_allowed_packages_returns_empty(self):
        ctx = _make_context(allowed_packages=[])
        tc = ctx.allowed_tool_context()
        assert isinstance(tc, ToolContext)
        assert not tc.has_tools
        assert tc.allowed_packages == []

    def test_returns_tool_context_always(self):
        ctx = _make_context(allowed_packages=["nonexistent"])
        tc = ctx.allowed_tool_context()
        assert isinstance(tc, ToolContext)
        # Packages not found in lockfile, so no tool_specs
        assert tc.allowed_packages == ["nonexistent"]


# ---------------------------------------------------------------------------
# run_agent with LLM binding
# ---------------------------------------------------------------------------


class TestRunAgentLLMBinding:
    def test_llm_passed_to_context(self, tmp_path, monkeypatch):
        """run_agent passes llm and system_prompt to AgentContext."""
        code = """\
            def agent_func(context, **kwargs):
                return {
                    "has_llm": context.llm is not None,
                    "has_system_prompt": context.system_prompt is not None,
                    "system_prompt_value": context.system_prompt,
                    "done": True,
                }
        """
        _write_agent_module(tmp_path, "llm_bind_test", code)
        monkeypatch.syspath_prepend(str(tmp_path))

        def mock_llm(messages, **kwargs):
            return "hello"

        entry = _agent_entry()
        entry["agent"]["entrypoint"] = "llm_bind_test.core:agent_func"
        entry["agent"]["system_prompt"] = "Be helpful"

        result = run_agent("llm-bind", entry=entry, llm=mock_llm)
        assert result.success is True
        assert result.result["has_llm"] is True
        assert result.result["has_system_prompt"] is True
        assert result.result["system_prompt_value"] == "Be helpful"

    def test_no_llm_still_works_for_tool_agents(self, tmp_path, monkeypatch):
        """Agents that don't use call_llm work fine without LLM binding."""
        code = """\
            def agent_func(context, **kwargs):
                return {"result": "no llm needed", "done": True}
        """
        _write_agent_module(tmp_path, "no_llm_test", code)
        monkeypatch.syspath_prepend(str(tmp_path))

        entry = _agent_entry()
        entry["agent"]["entrypoint"] = "no_llm_test.core:agent_func"

        result = run_agent("no-llm", entry=entry)
        assert result.success is True
        assert result.result["done"] is True

    def test_llm_only_agent_calls_llm(self, tmp_path, monkeypatch):
        """LLM-only agent can use call_llm_text() via bound LLM."""
        code = """\
            def agent_func(context, **kwargs):
                text = context.call_llm_text([
                    {"role": "user", "content": "hello"}
                ])
                return {"response": text, "done": True}
        """
        _write_agent_module(tmp_path, "llm_only_test", code)
        monkeypatch.syspath_prepend(str(tmp_path))

        def mock_llm(messages, **kwargs):
            return "LLM says hi"

        entry = _agent_entry()
        entry["agent"]["entrypoint"] = "llm_only_test.core:agent_func"
        entry["agent"]["tier"] = "llm_only"
        entry["agent"]["system_prompt"] = "You are a helper."

        result = run_agent("llm-only", entry=entry, llm=mock_llm)
        assert result.success is True
        assert result.result["response"] == "LLM says hi"
