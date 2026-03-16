"""Scheduler tool with cron parsing and event management (stdlib only)."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path


def run(operation: str, **kwargs) -> dict:
    """Schedule operations: parse cron, manage events, compute occurrences.

    Args:
        operation: One of "parse_cron", "create_event", "list_events", "next_occurrence".
        **kwargs: Operation-specific arguments (see below).

    Operations:
        parse_cron:
            expression (str): Cron expression (e.g. "*/5 * * * *").
            count (int): Number of upcoming run times to compute (default 5).
        create_event:
            title (str): Event title.
            datetime_str (str): ISO-format datetime string.
            recurrence (str): "none", "daily", "weekly", "monthly".
            events_file (str): Path to JSON file for storing events (default "events.json").
        list_events:
            events_file (str): Path to JSON file (default "events.json").
        next_occurrence:
            cron_expression (str): Cron expression.

    Returns:
        dict varying by operation.
    """
    ops = {
        "parse_cron": _parse_cron,
        "create_event": _create_event,
        "list_events": _list_events,
        "next_occurrence": _next_occurrence,
    }
    if operation not in ops:
        raise ValueError(f"Unknown operation: {operation}. Choose from {list(ops)}")
    return ops[operation](**kwargs)


# ---------------------------------------------------------------------------
# Cron parsing helpers (basic 5-field: minute hour dom month dow)
# ---------------------------------------------------------------------------

def _expand_field(field: str, min_val: int, max_val: int) -> list[int]:
    """Expand a single cron field into a sorted list of integer values."""
    values: set[int] = set()
    for part in field.split(","):
        if "/" in part:
            range_part, step_str = part.split("/", 1)
            step = int(step_str)
            if range_part == "*":
                start, end = min_val, max_val
            elif "-" in range_part:
                start, end = (int(x) for x in range_part.split("-", 1))
            else:
                start, end = int(range_part), max_val
            values.update(range(start, end + 1, step))
        elif part == "*":
            values.update(range(min_val, max_val + 1))
        elif "-" in part:
            start, end = (int(x) for x in part.split("-", 1))
            values.update(range(start, end + 1))
        else:
            values.add(int(part))
    return sorted(values)


def _parse_cron_expression(expression: str) -> dict[str, list[int]]:
    """Parse a 5-field cron expression into expanded value lists."""
    fields = expression.strip().split()
    if len(fields) != 5:
        raise ValueError(f"Expected 5-field cron expression, got {len(fields)} fields")
    return {
        "minutes": _expand_field(fields[0], 0, 59),
        "hours": _expand_field(fields[1], 0, 23),
        "days": _expand_field(fields[2], 1, 31),
        "months": _expand_field(fields[3], 1, 12),
        "weekdays": _expand_field(fields[4], 0, 6),
    }


def _next_cron_times(expression: str, count: int, start: datetime | None = None) -> list[str]:
    """Compute the next *count* datetimes matching a cron expression."""
    parsed = _parse_cron_expression(expression)
    current = (start or datetime.now()).replace(second=0, microsecond=0) + timedelta(minutes=1)
    results: list[str] = []

    # Safety cap to avoid infinite loops on impossible expressions
    max_iterations = count * 525960  # ~1 year of minutes
    iterations = 0

    while len(results) < count and iterations < max_iterations:
        iterations += 1
        if (
            current.month in parsed["months"]
            and current.day in parsed["days"]
            and current.weekday() in _convert_weekdays(parsed["weekdays"])
            and current.hour in parsed["hours"]
            and current.minute in parsed["minutes"]
        ):
            results.append(current.isoformat())
            current += timedelta(minutes=1)
        else:
            # Fast-forward logic: skip to next valid minute/hour when possible
            if current.minute not in parsed["minutes"]:
                current += timedelta(minutes=1)
            elif current.hour not in parsed["hours"]:
                current = current.replace(minute=0) + timedelta(hours=1)
            elif current.weekday() not in _convert_weekdays(parsed["weekdays"]):
                current = current.replace(hour=0, minute=0) + timedelta(days=1)
            elif current.day not in parsed["days"]:
                current = current.replace(hour=0, minute=0) + timedelta(days=1)
            elif current.month not in parsed["months"]:
                # Jump to the first day of the next month
                if current.month == 12:
                    current = current.replace(year=current.year + 1, month=1, day=1, hour=0, minute=0)
                else:
                    current = current.replace(month=current.month + 1, day=1, hour=0, minute=0)
            else:
                current += timedelta(minutes=1)

    return results


def _convert_weekdays(cron_weekdays: list[int]) -> list[int]:
    """Convert cron weekdays (0=Sunday) to Python weekdays (0=Monday)."""
    mapping = {0: 6, 1: 0, 2: 1, 3: 2, 4: 3, 5: 4, 6: 5}
    return [mapping[d] for d in cron_weekdays]


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------

def _parse_cron(**kwargs) -> dict:
    expression: str = kwargs.get("expression", "")
    count: int = int(kwargs.get("count", 5))
    if not expression:
        raise ValueError("expression is required")
    times = _next_cron_times(expression, count)
    return {"expression": expression, "next_runs": times, "count": len(times)}


def _next_occurrence(**kwargs) -> dict:
    expression: str = kwargs.get("cron_expression", "")
    if not expression:
        raise ValueError("cron_expression is required")
    times = _next_cron_times(expression, 1)
    return {"cron_expression": expression, "next_occurrence": times[0] if times else None}


def _create_event(**kwargs) -> dict:
    title: str = kwargs.get("title", "Untitled")
    datetime_str: str = kwargs.get("datetime_str", "")
    recurrence: str = kwargs.get("recurrence", "none")
    events_file: str = kwargs.get("events_file", "events.json")

    if not datetime_str:
        raise ValueError("datetime_str is required (ISO format)")

    # Validate datetime
    dt = datetime.fromisoformat(datetime_str)

    events = _load_events(events_file)
    event = {
        "title": title,
        "datetime": dt.isoformat(),
        "recurrence": recurrence,
        "created_at": datetime.now().isoformat(),
    }
    events.append(event)
    _save_events(events_file, events)

    return {"status": "created", "event": event, "total_events": len(events)}


def _list_events(**kwargs) -> dict:
    events_file: str = kwargs.get("events_file", "events.json")
    events = _load_events(events_file)
    return {"events": events, "total": len(events)}


def _load_events(path: str) -> list[dict]:
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def _save_events(path: str, events: list[dict]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(events, f, indent=2, default=str)
