"""EM-3B: every check, against a good context, a bad one, and a context with nothing in it.

The third case is the one that matters most. A suite that cannot tell "the backend did not do this"
from "I could not see whether the backend did this" will eventually accuse a correct backend and,
far worse, eventually clear a broken one. So each check is shown to report `probe_error` -- not
`fail` -- when its own reading is missing or itself failed.
"""
from __future__ import annotations

import copy

import pytest

from agentnode_sdk.conformance import doubles
from agentnode_sdk.conformance.checks import REGISTRY, Context, run_all
from agentnode_sdk.conformance.report import Outcome, Vantage
from agentnode_sdk.conformance.runner import SuiteOptions, run_conformance

GOOD_DECLARED = {
    "memory": "536870912", "pids": "256", "cpus": "1", "tmp_size": "64m",
    "home_size": "16m", "home_path": "/sandbox-home", "network": "none",
    "mount_targets": [], "env_names": [],
}
GOOD_HOST = {
    "runtime_version": "docker 28.0.4", "image": "ghcr.io/x@sha256:abc",
    "passthrough_refused": True,
    "leftovers": {"containers": [], "networks": [], "filtered_on": "agentnode-conformance-x"},
    "backend_loss": {"available": False, "refused": True, "error_type": "SandboxRequiredError",
                     "reason": "no container runtime found"},
    "refusal_message": "no container runtime found. Install Docker or Podman, then run "
                       "agentnode sandbox pull",
    "egress_matrix": {"direct_1_1_1_1": "blocked:OSError", "direct_8_8_8_8": "blocked:OSError",
                      "allowed_via_proxy": "ALLOWED:200", "denied_via_proxy": "refused:HTTPError"},
}
GOOD_STRESS = {
    "wallclock": {"sleep": 30, "timeout": 5.0, "elapsed": 5.1, "rc": -1, "killed": True},
    "memory": {"requested_mb": 768, "rc": 137, "killed": True, "stdout_tail": ""},
}


def good_context():
    return Context(readings=copy.deepcopy(doubles.GOOD_READINGS), declared=dict(GOOD_DECLARED),
                   stress=copy.deepcopy(GOOD_STRESS), host=copy.deepcopy(GOOD_HOST),
                   argv=["docker", "run", "--rm"])


def bad_context():
    return Context(
        readings=copy.deepcopy(doubles.BAD_READINGS),
        declared={**GOOD_DECLARED, "memory": "max", "pids": "max", "tmp_size": "500g",
                  "home_path": "/root", "home_size": None},
        stress={"wallclock": {"sleep": 30, "timeout": 5.0, "elapsed": 30.2, "rc": 0,
                              "killed": False},
                "memory": {"requested_mb": 768, "rc": 0, "killed": False,
                           "stdout_tail": "ALLOCATED 768"}},
        host={"runtime_version": "docker 28.0.4", "image": "img", "passthrough_refused": False,
              "leftovers": {"containers": ["agentnode-conformance-x-probe"], "networks":
                            ["agentnode-egress-1"], "filtered_on": "x"},
              "backend_loss": {"available": True, "refused": False, "error_type": None,
                               "reason": ""},
              "refusal_message": "error",
              "egress_matrix": {"direct_1_1_1_1": "BYPASS", "allowed_via_proxy": "refused:X",
                                "denied_via_proxy": "ALLOWED:200"}},
        argv=["docker", "run"])


ALL_IDS = [check_id for check_id, _fn, _req in REGISTRY]
#: These read host-side or runtime-side observations rather than the probe, so an empty probe does
#: not make them unmeasurable -- they become not_checked from their own missing input instead.
NOT_PROBE_BACKED = {"identity", "egress-allowlist", "limit-wallclock", "credentials-destroyed",
                    "cancel-and-kill", "backend-loss", "log-retention", "errors-are-usable",
                    "secrets-refused-without-egress"}


class TestTheChecksSeparateFailureFromBlindness:
    def test_a_good_context_passes_every_check(self):
        results = run_all(good_context())
        bad = [(r.check_id, r.outcome.value, r.evidence) for r in results
               if r.outcome not in (Outcome.PASS, Outcome.NOT_APPLICABLE)]
        assert bad == []

    def test_a_bad_context_fails_and_never_silently_passes(self):
        results = {r.check_id: r for r in run_all(bad_context())}
        for check_id in ("not-root", "read-only-root", "no-runtime-socket",
                         "capabilities-dropped", "no-new-privileges", "network-mode",
                         "egress-allowlist", "clean-home", "declared-mounts-only",
                         "secrets-only-by-release", "limit-wallclock", "limit-cpu",
                         "credentials-destroyed", "cancel-and-kill", "backend-loss",
                         "secrets-refused-without-egress", "outside-host-process"):
            assert results[check_id].outcome is Outcome.FAIL, (
                f"{check_id} did not report a failure on a backend that is not isolating: "
                f"{results[check_id].outcome.value} -- {results[check_id].evidence}")

    @pytest.mark.parametrize("check_id", sorted(set(ALL_IDS) - NOT_PROBE_BACKED))
    def test_a_missing_probe_is_a_probe_error_not_a_failure(self, check_id):
        results = {r.check_id: r for r in run_all(Context(probe_failure="the probe never ran"))}
        assert results[check_id].outcome is Outcome.PROBE_ERROR
        assert "probe" in results[check_id].evidence.lower()

    @pytest.mark.parametrize("check_id", sorted(set(ALL_IDS) - NOT_PROBE_BACKED))
    def test_a_reading_that_failed_inside_is_a_probe_error(self, check_id):
        readings = {k: {"_error": "PermissionError", "_detail": "denied"}
                    for k in doubles.GOOD_READINGS}
        results = {r.check_id: r for r in run_all(Context(readings=readings,
                                                          declared=dict(GOOD_DECLARED)))}
        assert results[check_id].outcome is Outcome.PROBE_ERROR

    @pytest.mark.parametrize("check_id", sorted(NOT_PROBE_BACKED - {"log-retention"}))
    def test_a_host_backed_check_without_its_input_is_not_checked(self, check_id):
        results = {r.check_id: r for r in run_all(Context(readings=doubles.GOOD_READINGS,
                                                          declared=dict(GOOD_DECLARED)))}
        assert results[check_id].outcome is Outcome.NOT_CHECKED

    def test_a_check_that_raises_becomes_a_suite_defect_not_a_verdict(self, monkeypatch):
        import agentnode_sdk.conformance.checks as mod

        def boom(_ctx):
            raise RuntimeError("the check itself is broken")

        monkeypatch.setattr(mod, "REGISTRY", (("not-root", boom, True),))
        result = mod.run_all(Context())[0]
        assert result.outcome is Outcome.PROBE_ERROR
        assert "defect in the suite" in result.evidence

    def test_no_check_claims_observed_without_a_measurement_vantage(self):
        for r in run_all(good_context()):
            if r.assurance.value == "observed":
                assert r.vantage in (Vantage.INSIDE, Vantage.OUTSIDE, Vantage.BOTH)

    def test_the_sdk_refusal_is_only_ever_self_reported(self):
        results = {r.check_id: r for r in run_all(good_context())}
        for check_id in ("secrets-refused-without-egress", "backend-loss", "errors-are-usable"):
            assert results[check_id].assurance.value == "self-reported", (
                f"{check_id} claims more than the Python layer can carry")


class TestTheRunnerAgainstDoubles:
    def test_a_good_double_passes_every_check_it_can_reach(self):
        report = run_conformance(doubles.GoodBackendDouble(), generated_at="t",
                                 options=SuiteOptions(include_outside=False),
                                 egress_matrix=GOOD_HOST["egress_matrix"])
        unproven = [(r.check_id, r.outcome.value, r.evidence) for r in report.unproven]
        assert unproven == []

    def test_but_a_double_is_never_conformant(self):
        report = run_conformance(doubles.GoodBackendDouble(), generated_at="t",
                                 options=SuiteOptions(include_outside=False),
                                 egress_matrix=GOOD_HOST["egress_matrix"])
        assert report.is_test_double
        assert not report.is_conformant
        assert "TEST DOUBLE" in report.summary_line()

    def test_a_bad_double_is_reported_as_failing(self):
        report = run_conformance(doubles.BadBackendDouble(), generated_at="t",
                                 options=SuiteOptions(include_outside=False),
                                 egress_matrix=bad_context().host["egress_matrix"])
        failed = {r.check_id for r in report.results if r.outcome is Outcome.FAIL}
        for check_id in ("not-root", "read-only-root", "no-runtime-socket",
                         "capabilities-dropped", "no-new-privileges", "clean-home"):
            assert check_id in failed
        assert not report.is_conformant

    def test_a_silent_backend_yields_probe_errors_not_failures(self):
        report = run_conformance(doubles.SilentBackendDouble(), generated_at="t",
                                 options=SuiteOptions(include_outside=False, include_stress=False))
        outcomes = {r.check_id: r.outcome for r in report.results}
        assert outcomes["not-root"] is Outcome.PROBE_ERROR
        assert outcomes["capabilities-dropped"] is Outcome.PROBE_ERROR
        assert not report.is_conformant

    def test_an_unavailable_backend_measures_nothing_and_says_so(self):
        report = run_conformance(doubles.GoodBackendDouble(available=False), generated_at="t")
        assert not report.is_conformant
        # Nothing was measured: no result may carry the observed level. The refusal message is
        # still inspectable, and that check is self-reported by construction.
        assert all(r.assurance.value == "self-reported" for r in report.results)
        assert any("not available" in r.evidence for r in report.results)

    def test_a_backend_that_is_not_a_double_cannot_supply_its_own_observations(self):
        class PretendingBackend(doubles.GoodBackendDouble):
            IS_TEST_DOUBLE = False

            def conformance_host_observations(self):
                return {"runtime_version": "whatever I say", "image": "mine",
                        "leftovers": {"containers": [], "networks": []}}

        report = run_conformance(PretendingBackend(), generated_at="t",
                                 options=SuiteOptions(include_outside=False))
        identity = next(r for r in report.results if r.check_id == "identity")
        assert identity.outcome is Outcome.NOT_CHECKED
        assert "whatever I say" not in identity.evidence

    def test_the_report_serialises(self):
        import json
        report = run_conformance(doubles.GoodBackendDouble(), generated_at="t",
                                 options=SuiteOptions(include_outside=False))
        d = json.loads(report.to_json())
        assert d["suite_version"]
        assert len(d["results"]) == len(REGISTRY)
