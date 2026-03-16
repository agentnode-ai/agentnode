"""Tests for calendar-manager-pack."""

import os
import tempfile


def test_run_create_event():
    from calendar_manager_pack.tool import run

    result = run(
        operation="create_event",
        title="Test Meeting",
        start="2026-04-01T10:00:00",
        end="2026-04-01T11:00:00",
    )
    assert "event" in result or "status" in result or "title" in result


def test_run_create_ical():
    from calendar_manager_pack.tool import run

    with tempfile.TemporaryDirectory() as tmpdir:
        out = os.path.join(tmpdir, "test.ics")
        result = run(
            operation="create_ical",
            calendar_file=out,
            events=[{
                "title": "Test",
                "start": "2026-04-01T10:00:00",
                "end": "2026-04-01T11:00:00",
            }],
        )
        assert isinstance(result, dict)
