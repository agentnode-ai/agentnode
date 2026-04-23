"""Tests for the llm_call event in the RunLog system."""
import uuid

import pytest

from agentnode_sdk.run_log import MAX_ENTRIES_PER_RUN, RunLog, read_run


@pytest.fixture
def run_id():
    return str(uuid.uuid4())


@pytest.fixture(autouse=True)
def _use_tmp_config(monkeypatch, tmp_path):
    """Point config_dir to a temp directory."""
    from agentnode_sdk import config

    monkeypatch.setattr(config, "config_dir", lambda: tmp_path)


class TestLlmCallAllFields:
    def test_llm_call_with_all_fields(self, run_id):
        log = RunLog(run_id)
        log.llm_call(
            model="gpt-4o",
            duration_ms=1523.7,
            usage={"prompt_tokens": 100, "completion_tokens": 50},
            finish_reason="stop",
            tool_calls_count=3,
        )

        events = read_run(run_id)
        assert len(events) == 1
        ev = events[0]
        assert ev["event"] == "llm_call"
        assert ev["run_id"] == run_id
        assert ev["model"] == "gpt-4o"
        assert ev["duration_ms"] == 1523.7
        assert ev["usage"] == {"prompt_tokens": 100, "completion_tokens": 50}
        assert ev["finish_reason"] == "stop"
        assert ev["tool_calls_count"] == 3
        assert "ts" in ev


class TestLlmCallMinimalFields:
    def test_llm_call_with_only_duration(self, run_id):
        log = RunLog(run_id)
        log.llm_call(duration_ms=250.0)

        events = read_run(run_id)
        assert len(events) == 1
        ev = events[0]
        assert ev["event"] == "llm_call"
        assert ev["duration_ms"] == 250.0
        # Optional fields should not be present
        assert "model" not in ev
        assert "usage" not in ev
        assert "finish_reason" not in ev
        assert "tool_calls_count" not in ev
        assert "error" not in ev


class TestLlmCallError:
    def test_llm_call_with_error(self, run_id):
        log = RunLog(run_id)
        log.llm_call(duration_ms=0.0, error="Rate limit exceeded")

        events = read_run(run_id)
        assert len(events) == 1
        assert events[0]["error"] == "Rate limit exceeded"

    def test_llm_call_error_truncated_to_500(self, run_id):
        log = RunLog(run_id)
        long_error = "x" * 1000
        log.llm_call(duration_ms=0.0, error=long_error)

        events = read_run(run_id)
        assert len(events) == 1
        assert len(events[0]["error"]) == 500


class TestLlmCallTruncation:
    def test_llm_call_respects_max_entries(self, run_id):
        log = RunLog(run_id)
        for i in range(MAX_ENTRIES_PER_RUN + 50):
            log.llm_call(model="gpt-4o", duration_ms=float(i))

        events = read_run(run_id)
        # MAX_ENTRIES_PER_RUN regular events + 1 truncated event
        assert len(events) == MAX_ENTRIES_PER_RUN + 1
        assert events[-1]["event"] == "truncated"
        # All non-truncated events should be llm_call
        for ev in events[:-1]:
            assert ev["event"] == "llm_call"


class TestLlmCallReadBack:
    def test_llm_call_read_via_read_run(self, run_id):
        log = RunLog(run_id)
        log.llm_call(
            model="claude-3-opus",
            duration_ms=800.5,
            usage={"prompt_tokens": 200, "completion_tokens": 100},
            finish_reason="end_turn",
            tool_calls_count=1,
        )

        events = read_run(run_id)
        assert len(events) == 1
        ev = events[0]
        assert ev["event"] == "llm_call"
        assert ev["model"] == "claude-3-opus"
        assert ev["duration_ms"] == 800.5
        assert ev["usage"]["prompt_tokens"] == 200
        assert ev["usage"]["completion_tokens"] == 100
        assert ev["finish_reason"] == "end_turn"
        assert ev["tool_calls_count"] == 1


class TestLlmCallMultiple:
    def test_multiple_llm_calls_all_recorded(self, run_id):
        log = RunLog(run_id)
        log.llm_call(model="gpt-4o", duration_ms=100.0, finish_reason="stop")
        log.llm_call(model="gpt-4o-mini", duration_ms=50.0, finish_reason="stop")
        log.llm_call(
            model="claude-3-opus",
            duration_ms=200.0,
            finish_reason="end_turn",
            tool_calls_count=2,
        )

        events = read_run(run_id)
        assert len(events) == 3
        assert all(ev["event"] == "llm_call" for ev in events)
        assert events[0]["model"] == "gpt-4o"
        assert events[0]["duration_ms"] == 100.0
        assert events[1]["model"] == "gpt-4o-mini"
        assert events[1]["duration_ms"] == 50.0
        assert events[2]["model"] == "claude-3-opus"
        assert events[2]["duration_ms"] == 200.0
        assert events[2]["tool_calls_count"] == 2


class TestLlmCallNoneFields:
    def test_none_fields_excluded_from_event(self, run_id):
        log = RunLog(run_id)
        log.llm_call(
            model=None,
            duration_ms=300.0,
            usage=None,
            finish_reason=None,
        )

        events = read_run(run_id)
        assert len(events) == 1
        ev = events[0]
        assert ev["event"] == "llm_call"
        assert ev["duration_ms"] == 300.0
        assert "model" not in ev
        assert "usage" not in ev
        assert "finish_reason" not in ev
