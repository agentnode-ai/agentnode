"""Tests for the structured run log — write, read, limits, and cleanup."""
import json
import os
import time
import uuid

import pytest

from agentnode_sdk.run_log import MAX_ENTRIES_PER_RUN, RunLog, cleanup_old_runs, list_runs, read_run


@pytest.fixture
def run_id():
    return str(uuid.uuid4())


@pytest.fixture(autouse=True)
def _use_tmp_config(monkeypatch, tmp_path):
    """Point config_dir to a temp directory."""
    from agentnode_sdk import config

    monkeypatch.setattr(config, "config_dir", lambda: tmp_path)


class TestRunLogWrite:
    def test_run_start_creates_file(self, run_id, tmp_path):
        log = RunLog(run_id)
        log.run_start("my-agent", "Research AI")

        events = read_run(run_id)
        assert len(events) == 1
        assert events[0]["event"] == "run_start"
        assert events[0]["run_id"] == run_id
        assert events[0]["slug"] == "my-agent"
        assert "ts" in events[0]

    def test_tool_call_and_result(self, run_id):
        log = RunLog(run_id)
        log.tool_call("csv-pack", "analyze")
        log.tool_result("csv-pack", "analyze", success=True, duration_ms=42.3)

        events = read_run(run_id)
        assert len(events) == 2
        assert events[0]["event"] == "tool_call"
        assert events[0]["slug"] == "csv-pack"
        assert events[1]["event"] == "tool_result"
        assert events[1]["success"] is True
        assert events[1]["duration_ms"] == 42.3

    def test_tool_result_with_error(self, run_id):
        log = RunLog(run_id)
        log.tool_result("bad-pack", None, success=False, error="Connection refused")

        events = read_run(run_id)
        assert events[0]["error"] == "Connection refused"

    def test_iteration_event(self, run_id):
        log = RunLog(run_id)
        log.iteration(3)

        events = read_run(run_id)
        assert events[0]["event"] == "iteration"
        assert events[0]["iteration"] == 3

    def test_step_events(self, run_id):
        log = RunLog(run_id)
        log.step_start("extract", "csv-pack:read")
        log.step_result("extract", success=True, duration_ms=100.0)

        events = read_run(run_id)
        assert events[0]["event"] == "step_start"
        assert events[1]["event"] == "step_result"
        assert events[1]["success"] is True

    def test_step_result_skipped(self, run_id):
        log = RunLog(run_id)
        log.step_result("optional", success=True, skipped=True)

        events = read_run(run_id)
        assert events[0]["skipped"] is True

    def test_run_end(self, run_id):
        log = RunLog(run_id)
        log.run_end(success=True, duration_ms=1234.5)

        events = read_run(run_id)
        assert events[0]["event"] == "run_end"
        assert events[0]["success"] is True
        assert events[0]["duration_ms"] == 1234.5

    def test_run_end_with_error(self, run_id):
        log = RunLog(run_id)
        log.run_end(success=False, error="timeout")

        events = read_run(run_id)
        assert events[0]["error"] == "timeout"


class TestRunLogLimits:
    def test_max_entries_truncates(self, run_id):
        log = RunLog(run_id)
        for i in range(MAX_ENTRIES_PER_RUN + 50):
            log.iteration(i)

        events = read_run(run_id)
        # Should have MAX_ENTRIES_PER_RUN regular events + 1 truncated event
        assert len(events) == MAX_ENTRIES_PER_RUN + 1
        assert events[-1]["event"] == "truncated"

    def test_no_writes_after_truncation(self, run_id):
        log = RunLog(run_id)
        for i in range(MAX_ENTRIES_PER_RUN + 10):
            log.iteration(i)
        # This should be silently dropped
        log.run_end(success=True, duration_ms=0)

        events = read_run(run_id)
        assert events[-1]["event"] == "truncated"


class TestRunLogNoSecrets:
    def test_goal_truncated_to_200(self, run_id):
        log = RunLog(run_id)
        long_goal = "x" * 500
        log.run_start("agent", long_goal)

        events = read_run(run_id)
        assert len(events[0]["goal"]) == 200

    def test_error_truncated_to_500(self, run_id):
        log = RunLog(run_id)
        long_error = "e" * 1000
        log.tool_result("pack", None, success=False, error=long_error)

        events = read_run(run_id)
        assert len(events[0]["error"]) == 500


class TestReadRun:
    def test_read_nonexistent_run(self):
        events = read_run("nonexistent-id")
        assert events == []

    def test_read_empty_file(self, run_id, tmp_path):
        # Create empty file
        runs_dir = tmp_path / "runs"
        runs_dir.mkdir()
        (runs_dir / f"{run_id}.jsonl").write_text("")

        events = read_run(run_id)
        assert events == []


class TestListRuns:
    def test_list_runs_empty(self):
        assert list_runs() == []

    def test_list_runs_returns_ids(self):
        ids = []
        for _ in range(3):
            rid = str(uuid.uuid4())
            ids.append(rid)
            log = RunLog(rid)
            log.run_start("test", "goal")

        result = list_runs(limit=10)
        assert len(result) >= 3
        for rid in ids:
            assert rid in result

    def test_list_runs_respects_limit(self):
        for _ in range(5):
            log = RunLog(str(uuid.uuid4()))
            log.run_start("test", "goal")

        result = list_runs(limit=2)
        assert len(result) == 2


class TestCleanupOldRuns:
    def test_no_cleanup_when_under_limits(self):
        for _ in range(3):
            log = RunLog(str(uuid.uuid4()))
            log.run_start("test", "goal")

        deleted = cleanup_old_runs(max_age_days=30, max_count=500)
        assert deleted == 0
        assert len(list_runs(limit=100)) == 3

    def test_max_count_enforced(self):
        for _ in range(5):
            log = RunLog(str(uuid.uuid4()))
            log.run_start("test", "goal")

        deleted = cleanup_old_runs(max_age_days=30, max_count=3)
        assert deleted == 2
        assert len(list_runs(limit=100)) == 3

    def test_max_age_enforced(self, tmp_path):
        # Create some run files with old timestamps
        runs_dir = tmp_path / "runs"
        runs_dir.mkdir(exist_ok=True)

        # Create 3 "old" files by backdating their mtime
        old_ids = []
        for _ in range(3):
            rid = str(uuid.uuid4())
            old_ids.append(rid)
            log = RunLog(rid)
            log.run_start("test", "old goal")
            path = runs_dir / f"{rid}.jsonl"
            # Set mtime to 60 days ago
            old_time = time.time() - 60 * 86400
            os.utime(path, (old_time, old_time))

        # Create 2 "new" files (current time)
        for _ in range(2):
            log = RunLog(str(uuid.uuid4()))
            log.run_start("test", "new goal")

        deleted = cleanup_old_runs(max_age_days=30, max_count=500)
        assert deleted == 3
        remaining = list_runs(limit=100)
        assert len(remaining) == 2
        for old_id in old_ids:
            assert old_id not in remaining

    def test_config_values_used(self, monkeypatch, tmp_path):
        """Cleanup reads max_age_days and max_count from config."""
        from agentnode_sdk import config

        cfg_path = tmp_path / "config.json"
        cfg_path.write_text(json.dumps({
            "version": "1",
            "run_log": {"max_age_days": 1, "max_count": 2},
        }))
        monkeypatch.setattr(config, "config_path", lambda: cfg_path)

        for _ in range(5):
            log = RunLog(str(uuid.uuid4()))
            log.run_start("test", "goal")

        deleted = cleanup_old_runs()
        assert deleted == 3
        assert len(list_runs(limit=100)) == 2
