"""Tests for Phase 7: Rule-based multi-step planner."""
import json
import os
from unittest.mock import patch, MagicMock

import pytest

from agentnode_sdk.planner import (
    _split_task,
    _pipe_result,
    _has_explicit_input,
    plan_task,
    plan_and_run,
    PlanStep,
    ExecutionPlan,
    PlanResult,
    StepResult,
    MAX_STEPS,
)
from agentnode_sdk.models import RunToolResult


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.json"
    lock_file = tmp_path / "agentnode.lock"
    cfg_file.write_text(json.dumps({"version": "0.1"}))
    lock_file.write_text(json.dumps({"version": "0.1", "packages": {}}))
    monkeypatch.setenv("AGENTNODE_CONFIG", str(cfg_file))
    monkeypatch.setenv("AGENTNODE_LOCKFILE", str(lock_file))
    monkeypatch.delenv("AGENTNODE_NON_INTERACTIVE", raising=False)
    return tmp_path


# --- Task Splitting ---


def test_split_single_task():
    parts = _split_task("search for AI news")
    assert parts == ["search for AI news"]


def test_split_then():
    parts = _split_task("extract from report.pdf then translate to german")
    assert len(parts) == 2
    assert parts[0] == "extract from report.pdf"
    assert parts[1] == "translate to german"


def test_split_and_then():
    parts = _split_task("search for news and then summarize")
    assert len(parts) == 2
    assert parts[0] == "search for news"
    assert parts[1] == "summarize"


def test_split_arrow():
    parts = _split_task("read pdf → summarize → translate")
    assert len(parts) == 3


def test_split_after_that():
    parts = _split_task("search for data after that analyze it")
    assert len(parts) == 2


def test_split_afterwards():
    parts = _split_task("extract text afterwards summarize")
    assert len(parts) == 2


def test_split_case_insensitive():
    parts = _split_task("search for news THEN summarize")
    assert len(parts) == 2


def test_split_max_steps():
    task = "a then b then c then d"
    parts = _split_task(task)
    assert len(parts) == 4
    with pytest.raises(ValueError, match="Too many steps"):
        plan_task(task)


# --- has_explicit_input ---


def test_has_explicit_input_fallback_text():
    assert _has_explicit_input({"text": "summarize"}, "summarize") is False


def test_has_explicit_input_fallback_query():
    assert _has_explicit_input({"query": "search for news"}, "search for news") is False


def test_has_explicit_input_with_file():
    assert _has_explicit_input({"file_path": "report.pdf"}, "extract from report.pdf") is True


def test_has_explicit_input_empty():
    assert _has_explicit_input({}, "summarize") is False


def test_has_explicit_input_target_language_only():
    """target_language alone is a modifier, not content — uses previous."""
    assert _has_explicit_input({"target_language": "german", "text": "translate to german"}, "translate to german") is False


def test_has_explicit_input_target_language_with_file():
    """target_language + file_path = has explicit content input."""
    assert _has_explicit_input({"target_language": "german", "file_path": "notes.txt"}, "translate notes.txt to german") is True


# --- Plan Task ---


def test_plan_task_single():
    plan = plan_task("search for AI news")
    assert len(plan.steps) == 1
    assert plan.steps[0].capability == "web_search"
    assert not plan.is_multi_step


def test_plan_task_multi():
    plan = plan_task("search for AI news then summarize")
    assert len(plan.steps) == 2
    assert plan.is_multi_step
    assert plan.steps[0].capability == "web_search"
    assert plan.steps[1].capability == "text_summarization"


def test_plan_task_unparseable_step():
    with pytest.raises(ValueError, match="Step 2 could not be parsed"):
        plan_task("search for AI news then xyzzy_unknown_thing_42")


def test_plan_task_three_steps():
    plan = plan_task("extract text from report.pdf then summarize then translate to german")
    assert len(plan.steps) == 3
    assert plan.steps[0].capability == "pdf_extraction"
    assert plan.steps[1].capability == "text_summarization"
    assert plan.steps[2].capability == "text_translation"


# --- uses_previous ---


def test_uses_previous_search_then_summarize():
    """'summarize' after search has no explicit input → uses previous."""
    plan = plan_task("search for news then summarize")
    assert plan.steps[0].uses_previous is False
    assert plan.steps[1].uses_previous is True


def test_uses_previous_extract_then_translate():
    """'translate to german' has target_language but no file → uses previous."""
    plan = plan_task("extract text from report.pdf then translate to german")
    assert plan.steps[0].uses_previous is False
    assert plan.steps[1].uses_previous is True


def test_uses_previous_explicit_file_overrides():
    """'translate notes.txt to german' has a file path → does NOT use previous."""
    plan = plan_task("extract text from report.pdf then translate notes.txt to german")
    assert plan.steps[0].uses_previous is False
    assert plan.steps[1].uses_previous is False
    assert "file_path" in plan.steps[1].input_args or plan.steps[1].input_args.get("text") != plan.steps[1].sub_task


# --- Piping ---


def test_pipe_result_dict_with_text():
    assert _pipe_result({"text": "hello", "metadata": "x"}) == {"text": "hello"}


def test_pipe_result_dict_with_content():
    assert _pipe_result({"content": "hello", "url": "x"}) == {"text": "hello"}


def test_pipe_result_dict_with_result():
    assert _pipe_result({"result": [1, 2], "status": "ok"}) == {"input": [1, 2]}


def test_pipe_result_dict_fallback():
    d = {"foo": "bar", "baz": 42}
    assert _pipe_result(d) == {"input": d}


def test_pipe_result_string():
    assert _pipe_result("hello world") == {"text": "hello world"}


def test_pipe_result_other():
    assert _pipe_result(42) == {"input": 42}


def test_pipe_result_list():
    assert _pipe_result([1, 2, 3]) == {"input": [1, 2, 3]}


# --- PlanResult serialization ---


def test_plan_result_to_dict():
    step = PlanStep(0, "search", "web_search", "high", "pattern", {"query": "news"})
    sr = StepResult(step=step, success=True, result={"text": "found"}, slug="searcher")
    plan = ExecutionPlan("search", [step])
    pr = PlanResult(plan=plan, steps=[sr], success=True, duration_ms=100.0)

    d = pr.to_dict()
    assert d["success"] is True
    assert d["duration_ms"] == 100.0
    assert len(d["steps"]) == 1
    assert d["steps"][0]["capability"] == "web_search"
    assert d["steps"][0]["slug"] == "searcher"


# --- plan_and_run ---


def test_plan_and_run_dry_run():
    result = plan_and_run("search for news then summarize", dry_run=True)
    assert result.success is True
    assert result.steps == []
    assert result.plan.is_multi_step


def test_plan_and_run_executes_steps(tmp_path):
    lock_file = tmp_path / "agentnode.lock"
    lock_file.write_text(json.dumps({
        "version": "0.1",
        "packages": {
            "search-pack": {
                "version": "1.0",
                "capability_ids": ["web_search"],
                "trust_level": "verified",
            },
            "summarizer-pack": {
                "version": "1.0",
                "capability_ids": ["text_summarization"],
                "trust_level": "verified",
            },
        },
    }))
    os.environ["AGENTNODE_LOCKFILE"] = str(lock_file)

    call_log = []

    def mock_run_tool(slug, **kwargs):
        call_log.append((slug, kwargs))
        if slug == "search-pack":
            return RunToolResult(success=True, result={"text": "AI is growing"})
        return RunToolResult(success=True, result={"text": "Summary: AI grows"})

    with patch("agentnode_sdk.runner.run_tool", mock_run_tool):
        result = plan_and_run("search for AI news then summarize")

    assert result.success is True
    assert len(result.steps) == 2
    assert result.steps[0].slug == "search-pack"
    assert result.steps[1].slug == "summarizer-pack"

    assert len(call_log) == 2
    assert call_log[1][1] == {"text": "AI is growing"}


def test_plan_and_run_stops_on_failure(tmp_path):
    lock_file = tmp_path / "agentnode.lock"
    lock_file.write_text(json.dumps({
        "version": "0.1",
        "packages": {
            "search-pack": {
                "version": "1.0",
                "capability_ids": ["web_search"],
                "trust_level": "verified",
            },
            "summarizer-pack": {
                "version": "1.0",
                "capability_ids": ["text_summarization"],
                "trust_level": "verified",
            },
        },
    }))
    os.environ["AGENTNODE_LOCKFILE"] = str(lock_file)

    def mock_run_tool(slug, **kwargs):
        if slug == "search-pack":
            return RunToolResult(success=False, error="Network error")
        return RunToolResult(success=True, result={"text": "ok"})

    with patch("agentnode_sdk.runner.run_tool", mock_run_tool):
        result = plan_and_run("search for AI news then summarize")

    assert result.success is False
    assert len(result.steps) == 1
    assert result.steps[0].success is False
    assert "Network error" in result.steps[0].error


def test_plan_and_run_no_package_shows_reason(tmp_path):
    """When no package is installed and auto-install is off, show clear reason."""
    lock_file = tmp_path / "agentnode.lock"
    lock_file.write_text(json.dumps({"version": "0.1", "packages": {}}))
    os.environ["AGENTNODE_LOCKFILE"] = str(lock_file)

    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({"version": "0.1", "auto_upgrade_policy": "off"}))
    os.environ["AGENTNODE_CONFIG"] = str(cfg_file)

    result = plan_and_run("search for AI news")

    assert result.success is False
    assert len(result.steps) == 1
    assert "auto_upgrade_policy: off" in result.steps[0].error
    assert "Install manually" in result.steps[0].error


def test_plan_and_run_install_blocked_shows_reason(tmp_path):
    """When resolve finds a package but install is blocked, show the reason."""
    lock_file = tmp_path / "agentnode.lock"
    lock_file.write_text(json.dumps({"version": "0.1", "packages": {}}))
    os.environ["AGENTNODE_LOCKFILE"] = str(lock_file)

    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({"version": "0.1"}))
    os.environ["AGENTNODE_CONFIG"] = str(cfg_file)

    mock_resolve = MagicMock()
    mock_resolve.results = [MagicMock(slug="search-pack")]

    mock_install = MagicMock()
    mock_install.installed = False
    mock_install.message = "Trust level too low"

    mock_client = MagicMock()
    mock_client.resolve.return_value = mock_resolve
    mock_client.install.return_value = mock_install
    mock_client.close = MagicMock()

    with patch("agentnode_sdk.client.AgentNodeClient", return_value=mock_client):
        result = plan_and_run("search for AI news")

    assert result.success is False
    assert "Trust level too low" in result.steps[0].error
    assert "agentnode install search-pack" in result.steps[0].error


# --- CLI guardrails ---


def test_cmd_run_plan_low_confidence_noninteractive(tmp_path, monkeypatch, capsys):
    """Low-confidence steps abort in non-interactive mode."""
    from agentnode_sdk.cli.commands import _cmd_run_plan

    monkeypatch.setenv("AGENTNODE_NON_INTERACTIVE", "true")

    from agentnode_sdk.cli.smart_run import ParsedTask
    mock_parsed = ParsedTask(
        capability="web_search", confidence="low",
        input_args={"query": "stuff"}, source="pattern",
    )
    with patch("agentnode_sdk.cli.smart_run.parse_task", return_value=mock_parsed):
        rc = _cmd_run_plan("stuff then stuff", dry_run=False)

    assert rc == 1
    captured = capsys.readouterr()
    assert "low confidence" in captured.err


def test_cmd_run_plan_install_confirmation_prompt(tmp_path, monkeypatch, capsys):
    """install_confirmation=prompt shows prompt before installing."""
    from agentnode_sdk.cli.commands import _cmd_run_plan

    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({"version": "0.1", "install_confirmation": "prompt"}))
    monkeypatch.setenv("AGENTNODE_CONFIG", str(cfg_file))

    lock_file = tmp_path / "agentnode.lock"
    lock_file.write_text(json.dumps({"version": "0.1", "packages": {}}))
    monkeypatch.setenv("AGENTNODE_LOCKFILE", str(lock_file))
    monkeypatch.delenv("AGENTNODE_NON_INTERACTIVE", raising=False)

    with patch("builtins.input", return_value="n"):
        rc = _cmd_run_plan("search for AI news then summarize")

    assert rc == 0
    captured = capsys.readouterr()
    assert "Cancelled" in captured.out


# --- Regression: single-step stays on smart-run path ---


def test_single_step_does_not_use_planner():
    """Single-step tasks must not go through _cmd_run_plan."""
    from agentnode_sdk.planner import _split_task

    single_tasks = [
        "search for AI news",
        "extract text from report.pdf",
        "summarize this document",
        "translate hello to german",
    ]
    for task in single_tasks:
        parts = _split_task(task)
        assert len(parts) == 1, f"'{task}' split into {len(parts)} parts, expected 1"
