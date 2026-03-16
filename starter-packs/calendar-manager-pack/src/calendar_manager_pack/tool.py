"""iCalendar (.ics) file management using stdlib only."""

from __future__ import annotations

import os
import re
import uuid
from datetime import datetime
from pathlib import Path


def run(operation: str, calendar_file: str = "", **kwargs) -> dict:
    """Manage iCalendar events and .ics files.

    Args:
        operation: One of "create_event", "parse_ical", "list_events", "create_ical".
        calendar_file: Path to an .ics file (used by most operations).
        **kwargs:
            title (str): Event title (for "create_event").
            start (str): ISO start datetime (for "create_event").
            end (str): ISO end datetime (for "create_event").
            location (str): Event location (for "create_event").
            events (list[dict]): List of event dicts for "create_ical".

    Returns:
        dict varying by operation.
    """
    ops = {
        "create_event": _create_event,
        "parse_ical": _parse_ical,
        "list_events": _list_events,
        "create_ical": _create_ical,
    }

    if operation not in ops:
        raise ValueError(f"Unknown operation: {operation}. Choose from {list(ops)}")

    return ops[operation](calendar_file, **kwargs)


def _dt_to_ical(dt_str: str) -> str:
    """Convert an ISO datetime string to iCalendar DTSTART/DTEND format."""
    dt = datetime.fromisoformat(dt_str)
    return dt.strftime("%Y%m%dT%H%M%S")


def _ical_to_iso(ical_dt: str) -> str:
    """Convert iCalendar datetime string to ISO format."""
    # Handle YYYYMMDDTHHMMSS and YYYYMMDDTHHMMSSZ
    clean = ical_dt.strip().rstrip("Z")
    try:
        dt = datetime.strptime(clean, "%Y%m%dT%H%M%S")
        return dt.isoformat()
    except ValueError:
        try:
            dt = datetime.strptime(clean, "%Y%m%d")
            return dt.isoformat()
        except ValueError:
            return ical_dt


def _build_vevent(
    title: str,
    start: str,
    end: str,
    location: str = "",
    description: str = "",
) -> str:
    """Build a VEVENT block."""
    uid = str(uuid.uuid4())
    now = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{now}",
        f"DTSTART:{_dt_to_ical(start)}",
        f"DTEND:{_dt_to_ical(end)}",
        f"SUMMARY:{title}",
    ]
    if location:
        lines.append(f"LOCATION:{location}")
    if description:
        lines.append(f"DESCRIPTION:{description}")
    lines.append("END:VEVENT")
    return "\r\n".join(lines)


def _build_vcalendar(vevents: list[str]) -> str:
    """Wrap VEVENT blocks in a VCALENDAR."""
    header = "\r\n".join([
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//AgentNode//calendar-manager-pack//EN",
        "CALSCALE:GREGORIAN",
    ])
    footer = "END:VCALENDAR"
    body = "\r\n".join(vevents)
    return f"{header}\r\n{body}\r\n{footer}\r\n"


def _create_event(calendar_file: str, **kwargs) -> dict:
    title = kwargs.get("title", "Untitled Event")
    start = kwargs.get("start", "")
    end = kwargs.get("end", "")
    location = kwargs.get("location", "")

    if not start:
        raise ValueError("start datetime is required (ISO format)")
    if not end:
        raise ValueError("end datetime is required (ISO format)")

    if not calendar_file:
        calendar_file = "calendar.ics"

    vevent = _build_vevent(title, start, end, location)

    # If file exists, insert the event before END:VCALENDAR
    if os.path.isfile(calendar_file):
        content = Path(calendar_file).read_text(encoding="utf-8")
        content = content.replace("END:VCALENDAR", f"{vevent}\r\nEND:VCALENDAR")
    else:
        content = _build_vcalendar([vevent])

    os.makedirs(os.path.dirname(calendar_file) or ".", exist_ok=True)
    Path(calendar_file).write_text(content, encoding="utf-8")

    return {
        "status": "created",
        "title": title,
        "start": start,
        "end": end,
        "location": location,
        "file": os.path.abspath(calendar_file),
    }


def _parse_ical(calendar_file: str, **kwargs) -> dict:
    file_path = kwargs.get("file_path", calendar_file)
    if not file_path:
        raise ValueError("calendar_file or file_path is required")
    if not os.path.isfile(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")

    content = Path(file_path).read_text(encoding="utf-8")
    events = _extract_events(content)

    return {"file": file_path, "events": events, "total": len(events)}


def _list_events(calendar_file: str, **kwargs) -> dict:
    file_path = kwargs.get("file_path", calendar_file)
    if not file_path:
        raise ValueError("calendar_file or file_path is required")
    if not os.path.isfile(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")

    content = Path(file_path).read_text(encoding="utf-8")
    events = _extract_events(content)

    return {"file": file_path, "events": events, "total": len(events)}


def _create_ical(calendar_file: str, **kwargs) -> dict:
    events = kwargs.get("events", [])
    if not events:
        raise ValueError("events list is required for create_ical")

    if not calendar_file:
        calendar_file = "calendar.ics"

    vevents = []
    for ev in events:
        vevent = _build_vevent(
            title=ev.get("title", "Untitled"),
            start=ev.get("start", ""),
            end=ev.get("end", ""),
            location=ev.get("location", ""),
            description=ev.get("description", ""),
        )
        vevents.append(vevent)

    content = _build_vcalendar(vevents)
    os.makedirs(os.path.dirname(calendar_file) or ".", exist_ok=True)
    Path(calendar_file).write_text(content, encoding="utf-8")

    return {
        "status": "created",
        "file": os.path.abspath(calendar_file),
        "events_count": len(events),
    }


def _extract_events(ical_content: str) -> list[dict]:
    """Parse VEVENT blocks from iCalendar content."""
    events = []
    # Split on VEVENT blocks
    pattern = re.compile(r"BEGIN:VEVENT(.*?)END:VEVENT", re.DOTALL)
    for match in pattern.finditer(ical_content):
        block = match.group(1)
        event: dict = {}
        for line in block.splitlines():
            line = line.strip()
            if line.startswith("SUMMARY:"):
                event["title"] = line[len("SUMMARY:"):]
            elif line.startswith("DTSTART"):
                # Handle DTSTART;VALUE=DATE:20240101 or DTSTART:20240101T120000
                val = line.split(":", 1)[-1]
                event["start"] = _ical_to_iso(val)
            elif line.startswith("DTEND"):
                val = line.split(":", 1)[-1]
                event["end"] = _ical_to_iso(val)
            elif line.startswith("LOCATION:"):
                event["location"] = line[len("LOCATION:"):]
            elif line.startswith("DESCRIPTION:"):
                event["description"] = line[len("DESCRIPTION:"):]
            elif line.startswith("UID:"):
                event["uid"] = line[len("UID:"):]
        events.append(event)
    return events
