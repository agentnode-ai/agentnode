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
    "env_baseline": ["HOME", "HOSTNAME", "LANG", "PATH", "PYTHON_VERSION"],
    "cancel": {"name": "c", "was_running": True, "gone_after": True, "still_listed": ""},
    "credential_lifecycle": {"name": "AGENTNODE_CONFORMANCE_RELEASED",
                             "present_when_released": True, "present_afterwards": False,
                             "leftovers": []},
}
GOOD_STRESS = {
    "wallclock": {"sleep": 30, "timeout": 5.0, "elapsed": 5.1, "rc": -1,
                  "timeout_marker_seen": True, "timeout_signal": True,
                  "stderr_tail": "[sandbox timed out after 5.0s]"},
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
                              "timeout_marker_seen": False, "timeout_signal": False,
                              "stderr_tail": ""},
                "memory": {"requested_mb": 768, "rc": 0, "killed": False,
                           "stdout_tail": "ALLOCATED 768"}},
        host={"runtime_version": "docker 28.0.4", "image": "img", "passthrough_refused": False,
              "leftovers": {"containers": ["agentnode-conformance-x-probe"], "networks":
                            ["agentnode-egress-1"], "filtered_on": "x"},
              "backend_loss": {"available": True, "refused": False, "error_type": None,
                               "reason": ""},
              "refusal_message": "error",
              "egress_matrix": {"direct_1_1_1_1": "BYPASS", "allowed_via_proxy": "refused:X",
                                "denied_via_proxy": "ALLOWED:200"},
              "env_baseline": ["HOME", "PATH"],
              "cancel": {"name": "c", "was_running": True, "gone_after": False,
                         "still_listed": "agentnode-conformance-x-cancel"},
              "credential_lifecycle": {"name": "AGENTNODE_CONFORMANCE_RELEASED",
                                       "present_when_released": True, "present_afterwards": True,
                                       "leftovers": ["agentnode-egress-1"]}},
        argv=["docker", "run"])


ALL_IDS = [check_id for check_id, _fn, _req in REGISTRY]
#: These read host-side or runtime-side observations rather than the probe, so an empty probe does
#: not make them unmeasurable -- they become not_checked from their own missing input instead.
NOT_PROBE_BACKED = {"identity", "egress-allowlist", "limit-wallclock", "run-leaves-nothing",
                    "credentials-not-persisted", "cancel-and-kill", "backend-loss",
                    "log-retention", "errors-are-usable", "secrets-refused-without-egress"}


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
                         "run-leaves-nothing", "credentials-not-persisted",
                         "cancel-and-kill", "backend-loss",
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


class TestTheNegativesTheReviewAskedFor:
    """EM3B-IMPLEMENTATION-0001: each of these passed before, and must not."""

    def test_a_processor_ceiling_larger_than_declared_fails(self):
        ctx = good_context()
        # eight processors enforced where one was declared: finite, and wrong.
        ctx.readings["cgroup"]["cpu_max"] = "800000 100000"
        r = {x.check_id: x for x in run_all(ctx)}["limit-cpu"]
        assert r.outcome is Outcome.FAIL
        assert "do not agree" in r.evidence

    def test_an_unlimited_processor_setting_fails(self):
        ctx = good_context()
        ctx.readings["cgroup"]["cpu_max"] = "max 100000"
        assert {x.check_id: x for x in run_all(ctx)}["limit-cpu"].outcome is Outcome.FAIL

    def test_the_declared_processor_count_is_what_is_compared(self):
        ctx = good_context()
        ctx.readings["cgroup"]["cpu_max"] = "50000 100000"        # half a processor
        assert {x.check_id: x for x in run_all(ctx)}["limit-cpu"].outcome is Outcome.FAIL
        ctx2 = Context(readings=ctx.readings, declared={**GOOD_DECLARED, "cpus": "0.5"},
                       stress=ctx.stress, host=ctx.host)
        assert {x.check_id: x for x in run_all(ctx2)}["limit-cpu"].outcome is Outcome.PASS

    def test_an_unrelated_early_nonzero_exit_is_not_a_timeout(self):
        ctx = good_context()
        ctx.stress["wallclock"] = {"sleep": 30, "timeout": 5.0, "elapsed": 0.2, "rc": 1,
                                   "timeout_marker_seen": False, "timeout_signal": False,
                                   "stderr_tail": "ModuleNotFoundError: no module named time"}
        r = {x.check_id: x for x in run_all(ctx)}["limit-wallclock"]
        assert r.outcome is Outcome.FAIL
        assert "nothing attributes the ending to the ceiling" in r.evidence

    def test_the_marker_without_the_documented_return_code_is_not_a_timeout(self):
        ctx = good_context()
        ctx.stress["wallclock"] = {"sleep": 30, "timeout": 5.0, "elapsed": 5.0, "rc": 0,
                                   "timeout_marker_seen": True, "timeout_signal": False,
                                   "stderr_tail": "[sandbox timed out after 5.0s]"}
        assert {x.check_id: x for x in run_all(ctx)}["limit-wallclock"].outcome is Outcome.FAIL

    def test_a_credential_that_outlives_its_run_fails_even_with_everything_cleaned_up(self):
        ctx = good_context()
        ctx.host["credential_lifecycle"] = {"name": "N", "present_when_released": True,
                                            "present_afterwards": True, "leftovers": []}
        ctx.host["leftovers"] = {"containers": [], "networks": [], "filtered_on": "x"}
        results = {x.check_id: x for x in run_all(ctx)}
        assert results["credentials-not-persisted"].outcome is Outcome.FAIL
        # and the infrastructure check, which used to stand in for this one, is content
        assert results["run-leaves-nothing"].outcome is Outcome.PASS

    def test_a_credential_never_released_is_not_a_pass_either(self):
        ctx = good_context()
        ctx.host["credential_lifecycle"] = {"name": "N", "present_when_released": False,
                                            "present_afterwards": False, "leftovers": []}
        assert {x.check_id: x for x in run_all(ctx)}["credentials-not-persisted"].outcome \
            is Outcome.FAIL

    def test_a_timeout_that_fired_is_not_a_cancellation(self):
        ctx = good_context()
        ctx.host.pop("cancel")
        r = {x.check_id: x for x in run_all(ctx)}["cancel-and-kill"]
        assert r.outcome is Outcome.NOT_CHECKED
        assert "no cancellation was performed" in r.evidence

    def test_a_payload_that_survives_cancellation_fails(self):
        ctx = good_context()
        ctx.host["cancel"] = {"name": "c", "was_running": True, "gone_after": False,
                              "still_listed": "agentnode-conformance-x-cancel"}
        assert {x.check_id: x for x in run_all(ctx)}["cancel-and-kill"].outcome is Outcome.FAIL

    def test_an_unknown_image_variable_is_not_smuggled_in_by_a_hand_written_allowlist(self):
        ctx = good_context()
        ctx.readings["env_names"] = sorted(ctx.readings["env_names"] + ["NODE_VERSION"])
        r = {x.check_id: x for x in run_all(ctx)}["secrets-only-by-release"]
        assert r.outcome is Outcome.FAIL, "a variable outside the measured baseline must be seen"
        ctx.host["env_baseline"] = ctx.readings["env_names"]
        assert {x.check_id: x for x in run_all(ctx)}["secrets-only-by-release"].outcome \
            is Outcome.PASS

    def test_a_released_name_that_never_arrived_fails(self):
        ctx = Context(readings=good_context().readings,
                      declared={**GOOD_DECLARED, "env_names": ["AGENTNODE_RELEASED"]},
                      stress=copy.deepcopy(GOOD_STRESS), host=copy.deepcopy(GOOD_HOST))
        r = {x.check_id: x for x in run_all(ctx)}["secrets-only-by-release"]
        assert r.outcome is Outcome.FAIL
        assert "released but absent" in r.evidence

    def test_without_a_baseline_nothing_is_concluded(self):
        ctx = good_context()
        ctx.host.pop("env_baseline")
        assert {x.check_id: x for x in run_all(ctx)}["secrets-only-by-release"].outcome \
            is Outcome.NOT_CHECKED


class TestACleanupQueryThatFailsIsNotACleanResult:
    """EM3B-IMPLEMENTATION-0002 / F1: the third time this project has written the same bug.

    An exception while asking the runtime what remained was recorded as an empty list, and an
    empty list is how "nothing was left behind" looks. A question the runtime would not answer
    cannot be the evidence that the credential-carrying network is gone.
    """

    def test_an_exception_while_asking_is_not_an_empty_result(self, monkeypatch):
        import agentnode_sdk.conformance.runner as runner

        def boom(*a, **k):
            raise OSError("the socket went away")

        monkeypatch.setattr(runner.subprocess, "run", boom)
        remaining, problem = runner._egress_leftovers("docker")
        assert remaining is None
        assert "raised OSError" in problem

    def test_a_refused_listing_is_not_an_empty_result(self, monkeypatch):
        import types as _types

        import agentnode_sdk.conformance.runner as runner

        monkeypatch.setattr(runner.subprocess, "run",
                            lambda *a, **k: _types.SimpleNamespace(
                                returncode=1, stdout="", stderr="permission denied"))
        remaining, problem = runner._egress_leftovers("docker")
        assert remaining is None and "refused" in problem

    def test_no_runtime_is_not_an_empty_result(self):
        import agentnode_sdk.conformance.runner as runner

        remaining, problem = runner._egress_leftovers("")
        assert remaining is None and problem

    def test_an_answered_empty_listing_is_an_empty_result(self, monkeypatch):
        import types as _types

        import agentnode_sdk.conformance.runner as runner

        monkeypatch.setattr(runner.subprocess, "run",
                            lambda *a, **k: _types.SimpleNamespace(
                                returncode=0, stdout="\n", stderr=""))
        assert runner._egress_leftovers("docker") == ([], None)

    def test_the_check_cannot_pass_on_an_unanswered_cleanup(self):
        ctx = good_context()
        ctx.host["credential_lifecycle"] = {
            "name": "N", "present_when_released": True, "present_afterwards": False,
            "_error": "asking the runtime what remained raised OSError: the socket went away"}
        r = {x.check_id: x for x in run_all(ctx)}["credentials-not-persisted"]
        assert r.outcome is Outcome.PROBE_ERROR
        assert "socket went away" in r.evidence

    def test_and_such_a_report_is_not_conformant(self):
        from agentnode_sdk.conformance.report import CheckResult, ConformanceReport

        report = ConformanceReport(
            backend_identity="B", backend_version="1", runtime="docker", image="i",
            generated_at="t",
            results=(CheckResult.probe_error("credentials-not-persisted", "t", "credentials",
                                             "the cleanup query was not answered"),))
        assert not report.is_conformant
        assert "credentials-not-persisted" in report.summary_line()


#: Every check this suite is required to make, written out rather than read from the registry.
#: EM3B-SUITE-0001 / F1: the serialisation test compared the produced result count with
#: `len(REGISTRY)`, so both sides of it moved together. Deleting a check removed it from the
#: expectation as well as from the run, the remaining checks still passed, and the report was
#: still "conformant" -- coverage could shrink without anything going red. This list is the
#: independent side of that comparison, and changing what the suite covers means changing it here
#: too, deliberately.
REQUIRED_CHECK_IDS = (
    "identity",
    "outside-host-process",
    "not-root",
    "read-only-root",
    "declared-mounts-only",
    "no-runtime-socket",
    "capabilities-dropped",
    "no-new-privileges",
    "network-mode",
    "egress-allowlist",
    "limit-memory",
    "limit-pids",
    "limit-cpu",
    "limit-disk",
    "limit-wallclock",
    "clean-home",
    "secrets-only-by-release",
    "secrets-refused-without-egress",
    "run-leaves-nothing",
    "credentials-not-persisted",
    "cancel-and-kill",
    "backend-loss",
    "log-retention",
    "errors-are-usable",
)


class TestTheCheckInventoryCannotShrinkQuietly:
    def test_the_registry_is_exactly_the_required_checks(self):
        registry_ids = [check_id for check_id, _fn, _required in REGISTRY]
        assert len(REQUIRED_CHECK_IDS) == 24, "the required inventory is 24 checks"
        missing = [c for c in REQUIRED_CHECK_IDS if c not in registry_ids]
        extra = [c for c in registry_ids if c not in REQUIRED_CHECK_IDS]
        assert not missing, f"the suite no longer makes these checks: {missing}"
        assert not extra, (
            f"the suite makes checks the required inventory does not list: {extra}. Adding one is "
            "fine -- add it to REQUIRED_CHECK_IDS in the same change, so the two stay deliberate")
        assert len(registry_ids) == len(set(registry_ids)), "a check id appears twice"
        assert registry_ids == list(REQUIRED_CHECK_IDS), (
            "the registry order differs from the required inventory; the report is read in order")

    def test_every_required_check_is_marked_required(self):
        for check_id, _fn, required in REGISTRY:
            assert required, f"{check_id} is in the required inventory but is not marked required"

    def test_a_report_covers_every_required_check(self):
        report = run_conformance(doubles.GoodBackendDouble(), generated_at="t",
                                 options=SuiteOptions(include_outside=False),
                                 egress_matrix=GOOD_HOST["egress_matrix"])
        produced = [r.check_id for r in report.results]
        assert produced == list(REQUIRED_CHECK_IDS), (
            "a run produced a different set of checks than the suite is required to make")

    def test_a_shortened_registry_is_caught(self, monkeypatch):
        """The failure this guard exists for: a check quietly removed."""
        import agentnode_sdk.conformance.checks as mod

        monkeypatch.setattr(mod, "REGISTRY", tuple(r for r in mod.REGISTRY
                                                   if r[0] != "limit-memory"))
        produced = [r.check_id for r in mod.run_all(good_context())]
        assert "limit-memory" not in produced
        assert produced != list(REQUIRED_CHECK_IDS), (
            "the guard must notice a missing check")
