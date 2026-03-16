"""Tests for scheduler-pack."""


def test_run_parse_cron():
    from scheduler_pack.tool import run

    result = run(operation="parse_cron", expression="0 9 * * *", count=3)
    assert "next_runs" in result
    assert len(result["next_runs"]) == 3


def test_run_create_and_list_events():
    from scheduler_pack.tool import run

    run(operation="create_event", title="Test Event", datetime_str="2026-04-01T10:00:00")
    result = run(operation="list_events")
    assert "events" in result
    assert result["total"] >= 1
