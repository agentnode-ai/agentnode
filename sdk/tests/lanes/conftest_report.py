"""Emit the per-node outcome report the R6 lane verifier consumes.

Enabled with ``-p tests.lanes.conftest_report --lane-report <path>``. It records one
outcome per collected node id plus the skip reason, which is what
``freeze_inventory verify`` needs to tell an intended R5 skip from a silent omission.
"""
from __future__ import annotations

import json

_OUTCOMES: dict[str, str] = {}
_REASONS: dict[str, str] = {}


def pytest_addoption(parser):
    parser.addoption("--lane-report", action="store", default=None,
                     help="write the per-node outcome report here")


def pytest_collection_modifyitems(items):
    for it in items:
        _OUTCOMES.setdefault(it.nodeid, "collected")


def pytest_runtest_logreport(report):
    if report.when == "call" or (report.when == "setup" and report.outcome in ("skipped", "failed")):
        prev = _OUTCOMES.get(report.nodeid)
        outcome = "error" if (report.when == "setup" and report.outcome == "failed") else report.outcome
        if prev in (None, "collected") or outcome in ("failed", "error"):
            _OUTCOMES[report.nodeid] = outcome
        if report.outcome == "skipped" and report.longrepr:
            try:
                _REASONS[report.nodeid] = str(report.longrepr[2])
            except Exception:
                _REASONS[report.nodeid] = str(report.longrepr)


def pytest_sessionfinish(session, exitstatus):
    path = session.config.getoption("--lane-report")
    if not path:
        return
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"outcomes": _OUTCOMES, "skip_reasons": _REASONS}, fh, indent=2)
