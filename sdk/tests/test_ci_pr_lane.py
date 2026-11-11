"""Task #13, slice 1: what a pull request runs on the SDK/web track (.github/workflows/sdk.yml).

The plan (PLAN-2026-11.md, section 2) asks for fast, meaningful PR feedback: on a pull request the
SDK suite runs once, on the newest supported Python, and the web unit tests run -- they existed
(vitest, `npm test`) and no workflow ran them. On a push to main the full interpreter matrix still
runs, so nothing that was tested before is tested less often on main; it is only no longer run
three times before a merge.

What this holds:

* the SDK job's interpreter list is ["3.12"] for a pull request and ["3.10", "3.11", "3.12"]
  otherwise -- every supported version is still exercised on main;
* the SDK job still runs the whole suite (`pytest`), not a hand-picked subset;
* the web job runs `npm test` after installing, beside lint and build;
* the always-run gate still waits for every job, so a failing web test fails the required check.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

REPO = Path(__file__).resolve().parents[2]
WORKFLOW = REPO / ".github" / "workflows" / "sdk.yml"
SUPPORTED = ["3.10", "3.11", "3.12"]


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _runs(job: dict) -> list[str]:
    return [str(s.get("run", "")) for s in job.get("steps", [])]


def _interpreters_for(expression: str, event: str) -> list[str]:
    """Evaluate the one expression shape this workflow may use for the matrix:
    ${{ github.event_name == 'pull_request' && fromJSON('[...]') || fromJSON('[...]') }}"""
    m = re.fullmatch(
        r"\$\{\{\s*github\.event_name\s*==\s*'pull_request'\s*&&\s*fromJSON\('(\[[^']*\])'\)"
        r"\s*\|\|\s*fromJSON\('(\[[^']*\])'\)\s*\}\}",
        expression.strip(),
    )
    assert m, (
        "the interpreter matrix is not the expected pull-request/other expression: "
        + expression
    )
    import json

    return json.loads(m.group(1) if event == "pull_request" else m.group(2))


def test_a_pull_request_runs_the_sdk_suite_once_on_the_newest_python():
    sdk = _workflow()["jobs"]["sdk"]
    expression = sdk["strategy"]["matrix"]["python-version"]
    assert isinstance(expression, str), (
        "the matrix is a fixed list, so a PR still runs it three times"
    )
    assert _interpreters_for(expression, "pull_request") == ["3.12"]


def test_main_still_runs_every_supported_python():
    expression = _workflow()["jobs"]["sdk"]["strategy"]["matrix"]["python-version"]
    assert _interpreters_for(expression, "push") == SUPPORTED


def test_the_sdk_job_still_runs_the_whole_suite():
    runs = _runs(_workflow()["jobs"]["sdk"])
    assert any(re.fullmatch(r"pytest(\s+-v)?", r.strip()) for r in runs), runs


def test_the_web_unit_tests_run_on_every_sdk_web_change():
    jobs = _workflow()["jobs"]
    web = jobs["web-lint-build"]
    runs = [r.strip() for r in _runs(web)]
    assert "npm ci" in runs
    assert "npm test" in runs, "the web unit tests (vitest) are not run by any workflow"
    assert runs.index("npm ci") < runs.index("npm test")


def test_the_required_gate_still_waits_for_every_job():
    jobs = _workflow()["jobs"]
    gate = jobs["sdk-web-required"]
    assert gate["if"] == "always()"
    assert set(gate["needs"]) == {
        "changes",
        "sdk",
        "adapter-langchain",
        "web-lint-build",
    }
