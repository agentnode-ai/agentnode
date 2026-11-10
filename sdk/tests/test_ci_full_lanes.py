"""Task #30, slice 2 of the test strategy: the full checks run nightly and before a release, not on a PR.

PLAN-2026-11.md, section 2: "PR = smoke + affected unit tests, nightly = everything, release =
everything + manual acceptance of the website". Slice 1 (#13) made the PR lane fast. This holds
the other two lanes:

* `.github/workflows/full-checks.yml` is THE full check, written once and only callable
  (`workflow_call`): the SDK suite on every supported Python, the langchain adapter, backend lint
  and the whole backend suite against Postgres and Redis, web lint + unit tests + build, and the
  smoke suite -- with no path filter and no condition that could skip a part of it;
* `.github/workflows/nightly.yml` runs it on a schedule (and by hand), never on a push or a PR;
* `.github/workflows/release.yml` runs it for a version tag (and by hand), never on a PR, and only
  after it passed asks for the manual website acceptance in its own protected environment.

Neither lane publishes anything; the publish workflows are untouched.
"""

from __future__ import annotations

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"
SUPPORTED = ["3.10", "3.11", "3.12"]
FULL = "./.github/workflows/full-checks.yml"


def _load(name: str) -> dict:
    path = WORKFLOWS / name
    assert path.is_file(), "missing workflow: %s" % name
    body = yaml.safe_load(path.read_text(encoding="utf-8"))
    # YAML 1.1 reads the key `on` as True.
    body["on"] = body.pop(True, body.get("on"))
    return body


def _runs(job: dict) -> list[str]:
    return [str(s.get("run", "")).strip() for s in job.get("steps", [])]


# ---- the full check, once --------------------------------------------------------------------- #


def test_the_full_check_can_only_be_called():
    assert set(_load("full-checks.yml")["on"]) == {"workflow_call"}


def test_the_full_check_skips_nothing():
    jobs = _load("full-checks.yml")["jobs"]
    for name, job in jobs.items():
        assert "if" not in job, "job %s can be skipped: %s" % (name, job.get("if"))
    expected = {
        "sdk",
        "adapter-langchain",
        "backend-lint",
        "backend-test",
        "web",
        "smoke",
    }
    assert expected <= set(jobs), sorted(expected - set(jobs))


def test_the_sdk_runs_whole_on_every_supported_python():
    sdk = _load("full-checks.yml")["jobs"]["sdk"]
    assert sdk["strategy"]["matrix"]["python-version"] == SUPPORTED
    assert sdk["strategy"].get("fail-fast") is False, (
        "one red interpreter must not cancel the others"
    )
    assert "pytest -v" in _runs(sdk)


def test_the_backend_is_linted_and_tested_whole_against_its_services():
    jobs = _load("full-checks.yml")["jobs"]
    lint = _runs(jobs["backend-lint"])
    assert (
        "ruff check app/ tests/" in lint and "ruff format --check app/ tests/" in lint
    )
    test = jobs["backend-test"]
    assert {"postgres", "redis"} <= set(test["services"])
    assert "pytest -v --tb=short" in _runs(test)


def test_the_web_is_linted_unit_tested_and_built():
    runs = _runs(_load("full-checks.yml")["jobs"]["web"])
    for step in ("npm ci", "npm run lint", "npm test", "npm run build"):
        assert step in runs, step


def test_the_smoke_suite_runs_with_both_environments():
    smoke = _load("full-checks.yml")["jobs"]["smoke"]
    assert "postgres" in smoke["services"]
    env = smoke.get("env", {})
    assert env.get("SMOKE_BACKEND_PYTHON") and env.get("SMOKE_SDK_PYTHON")
    assert "python scripts/smoke.py" in _runs(smoke)


# ---- the two lanes ---------------------------------------------------------------------------- #


def _calls_the_full_check(body: dict) -> list[str]:
    return [name for name, job in body["jobs"].items() if job.get("uses") == FULL]


def test_nightly_runs_the_full_check_on_a_schedule_and_never_on_a_pr():
    nightly = _load("nightly.yml")
    assert set(nightly["on"]) == {"schedule", "workflow_dispatch"}
    assert nightly["on"]["schedule"] and nightly["on"]["schedule"][0]["cron"]
    assert len(_calls_the_full_check(nightly)) == 1


def test_release_runs_the_full_check_for_a_version_tag_and_never_on_a_pr():
    release = _load("release.yml")
    assert set(release["on"]) == {"push", "workflow_dispatch"}
    assert set(release["on"]["push"]) == {"tags"}
    assert release["on"]["push"]["tags"] == ["v*"]
    assert len(_calls_the_full_check(release)) == 1


def test_the_website_acceptance_comes_after_the_full_check_and_is_its_own_gate():
    release = _load("release.yml")
    full = _calls_the_full_check(release)[0]
    acceptance = release["jobs"]["website-acceptance"]
    assert acceptance["needs"] == full or acceptance["needs"] == [full]
    assert acceptance.get("environment"), (
        "the manual acceptance needs a protected environment"
    )


@pytest.mark.parametrize("name", ["full-checks.yml", "nightly.yml", "release.yml"])
def test_the_lanes_only_read_and_publish_nothing(name):
    body = _load(name)
    assert body.get("permissions") == {"contents": "read"}
    text = (WORKFLOWS / name).read_text(encoding="utf-8").lower()
    for publishing in ("twine", "npm publish", "pypi", "gh release", "docker push"):
        assert publishing not in text, "%s would publish (%s)" % (name, publishing)
