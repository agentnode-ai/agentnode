"""Tests for calendar-manager-pack."""

import os
import tempfile

from calendar_manager_pack.tool import run, _dt_to_ical, _ical_to_iso


def test_dt_to_ical():
    assert _dt_to_ical("2026-04-01T10:00:00") == "20260401T100000"


def test_ical_to_iso():
    assert _ical_to_iso("20260401T100000") == "2026-04-01T10:00:00"
    assert _ical_to_iso("20260401T100000Z") == "2026-04-01T10:00:00"


def test_run_create_event():
    with tempfile.TemporaryDirectory() as tmpdir:
        cal_file = os.path.join(tmpdir, "calendar.ics")
        result = run(
            operation="create_event",
            calendar_file=cal_file,
            title="Test Meeting",
            start="2026-04-01T10:00:00",
            end="2026-04-01T11:00:00",
        )
        assert result["status"] == "created"
        assert result["title"] == "Test Meeting"
        assert os.path.isfile(cal_file)


def test_run_create_ical():
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
        assert result["status"] == "created"
        assert result["events_count"] == 1
        assert os.path.isfile(out)


def test_run_list_events():
    with tempfile.TemporaryDirectory() as tmpdir:
        cal_file = os.path.join(tmpdir, "calendar.ics")
        run(
            operation="create_event",
            calendar_file=cal_file,
            title="Event A",
            start="2026-04-01T10:00:00",
            end="2026-04-01T11:00:00",
        )
        result = run(operation="list_events", calendar_file=cal_file)
        assert result["total"] >= 1
        assert result["events"][0]["title"] == "Event A"


def test_run_parse_ical():
    with tempfile.TemporaryDirectory() as tmpdir:
        cal_file = os.path.join(tmpdir, "calendar.ics")
        run(
            operation="create_ical",
            calendar_file=cal_file,
            events=[
                {"title": "Ev1", "start": "2026-04-01T09:00:00", "end": "2026-04-01T10:00:00"},
                {"title": "Ev2", "start": "2026-04-02T09:00:00", "end": "2026-04-02T10:00:00"},
            ],
        )
        result = run(operation="parse_ical", calendar_file=cal_file)
        assert result["total"] == 2
