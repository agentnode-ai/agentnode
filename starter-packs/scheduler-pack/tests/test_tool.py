"""Tests for scheduler-pack."""

import os
import tempfile

from scheduler_pack.tool import run, _expand_field


def test_expand_field_star():
    assert _expand_field("*", 0, 5) == [0, 1, 2, 3, 4, 5]


def test_expand_field_step():
    assert _expand_field("*/15", 0, 59) == [0, 15, 30, 45]


def test_expand_field_range():
    assert _expand_field("1-3", 0, 6) == [1, 2, 3]


def test_run_parse_cron():
    result = run(operation="parse_cron", expression="0 9 * * *", count=3)
    assert "next_runs" in result
    assert len(result["next_runs"]) == 3


def test_run_parse_cron_every_5_min():
    result = run(operation="parse_cron", expression="*/5 * * * *", count=5)
    assert len(result["next_runs"]) == 5


def test_run_create_and_list_events():
    with tempfile.TemporaryDirectory() as tmpdir:
        ef = os.path.join(tmpdir, "events.json")
        run(
            operation="create_event",
            title="Test Event",
            datetime_str="2026-04-01T10:00:00",
            events_file=ef,
        )
        result = run(operation="list_events", events_file=ef)
        assert "events" in result
        assert result["total"] >= 1
        assert result["events"][0]["title"] == "Test Event"


def test_run_next_occurrence():
    result = run(operation="next_occurrence", cron_expression="30 14 * * *")
    assert "next_occurrence" in result
