"""#31, slice 3 step 1: the one-off trigger that runs the full check on `ci/slice3-container-job`.

GitHub starts `workflow_dispatch` only for workflows on the default branch, and `nightly.yml` is not on
`main` yet, so the full check cannot be dispatched on this branch. The Projektleiter decided (option A):
a small caller that starts ONLY on a push to exactly this branch and does nothing but call
`full-checks.yml`, with read-only rights. This test holds it to that and nothing more: no other branch,
no pull request, no schedule or dispatch, no rights beyond reading, no secrets passed on, no second job.
"""

from __future__ import annotations

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"
TRIGGER = WORKFLOWS / "slice3-container-check.yml"
BRANCH = "ci/slice3-container-job"


def _load() -> dict:
    assert TRIGGER.is_file(), "missing: %s" % TRIGGER.name
    body = yaml.safe_load(TRIGGER.read_text(encoding="utf-8"))
    body["on"] = body.pop(True, body.get("on"))  # YAML 1.1 reads `on` as True
    return body


def test_it_starts_only_on_a_push_to_exactly_this_branch():
    on = _load()["on"]
    assert set(on) == {"push"}, "only push; no pull_request, schedule or dispatch: %s" % sorted(on)
    assert set(on["push"]) == {"branches"}, "no paths or tags filters, nothing else"
    assert on["push"]["branches"] == [BRANCH], "exactly this branch, no pattern"
    assert not any(ch in BRANCH for ch in "*?[!"), "the branch is a literal name"


def test_it_has_read_only_rights_and_nothing_more():
    body = _load()
    assert body.get("permissions") == {"contents": "read"}
    for name, job in body["jobs"].items():
        assert "permissions" not in job, "job %s widens the rights" % name


def test_it_only_calls_the_full_check_and_has_no_other_job():
    jobs = _load()["jobs"]
    assert len(jobs) == 1, "exactly one job: %s" % sorted(jobs)
    (job,) = jobs.values()
    assert set(job) == {"uses"}, "the caller does nothing but call: %s" % sorted(job)
    assert job["uses"] == "./.github/workflows/full-checks.yml"


def test_it_passes_no_secrets_and_publishes_nothing():
    text = TRIGGER.read_text(encoding="utf-8").lower()
    for forbidden in ("secrets", "inherit", "twine", "npm publish", "docker push", "gh release",
                      "pull_request", "workflow_dispatch", "schedule"):
        assert forbidden not in text, forbidden
