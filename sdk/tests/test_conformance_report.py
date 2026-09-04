"""EM-3B: the rules that keep an unmeasured property from looking like one that holds."""
from __future__ import annotations

import pytest

from agentnode_sdk.conformance.report import (
    Assurance,
    CheckResult,
    ConformanceReport,
    Outcome,
    Vantage,
)


def _ok(check_id="x", **kw):
    kw.setdefault("required", True)
    return CheckResult.measured(check_id, "t", "f", True, Vantage.INSIDE, "measured it", **kw)


def _report(*results, double=False):
    return ConformanceReport(backend_identity="B", backend_version="1", runtime="docker",
                             image="img@sha256:0", generated_at="2026-09-03T00:00:00Z",
                             results=tuple(results), is_test_double=double)


class TestTheVocabularyIsClosed:
    def test_the_forbidden_word_is_rejected_in_evidence(self):
        with pytest.raises(ValueError, match="self-reported, observed, attested"):
            CheckResult.measured("x", "t", "f", True, Vantage.INSIDE,
                                 "this backend is certified by us")

    def test_the_forbidden_word_is_rejected_in_a_title(self):
        with pytest.raises(ValueError):
            CheckResult.measured("x", "certification holds", "f", True, Vantage.INSIDE, "e")

    def test_the_forbidden_word_is_rejected_inside_detail(self):
        with pytest.raises(ValueError):
            CheckResult.measured("x", "t", "f", True, Vantage.INSIDE, "e",
                                 detail={"note": "certifies the backend"})

    def test_the_forbidden_word_is_rejected_in_a_report_header(self):
        with pytest.raises(ValueError):
            ConformanceReport(backend_identity="the certified backend", backend_version="1",
                              runtime="docker", image="i", generated_at="t", results=())

    def test_attested_needs_an_attestation_document(self):
        with pytest.raises(ValueError, match="external attestation document"):
            CheckResult("x", "t", "f", Outcome.PASS, Assurance.ATTESTED, Vantage.INSIDE, "e")

    def test_attested_is_accepted_only_with_one(self):
        r = CheckResult("x", "t", "f", Outcome.PASS, Assurance.ATTESTED, Vantage.INSIDE, "e",
                        attestation="signed report 2026-09-03, auditor XY")
        assert r.assurance is Assurance.ATTESTED


class TestEvidenceCannotBePromoted:
    def test_host_side_evidence_cannot_be_observed(self):
        with pytest.raises(ValueError, match="not the boundary"):
            CheckResult("x", "t", "f", Outcome.PASS, Assurance.OBSERVED, Vantage.HOST_SDK, "e")

    def test_no_vantage_cannot_be_observed(self):
        with pytest.raises(ValueError, match="not the boundary"):
            CheckResult("x", "t", "f", Outcome.PASS, Assurance.OBSERVED, Vantage.NONE, "e")

    def test_the_measured_constructor_refuses_a_host_vantage(self):
        with pytest.raises(ValueError, match="not a measurement vantage"):
            CheckResult.measured("x", "t", "f", True, Vantage.HOST_SDK, "e")

    def test_claimed_evidence_stays_self_reported(self):
        assert CheckResult.claimed("x", "t", "f", True, "the argv carries the flag").assurance \
            is Assurance.SELF_REPORTED

    def test_an_unmeasured_outcome_cannot_carry_assurance(self):
        with pytest.raises(ValueError, match="nothing was measured"):
            CheckResult("x", "t", "f", Outcome.NOT_CHECKED, Assurance.OBSERVED, Vantage.INSIDE, "r")

    def test_every_outcome_needs_its_reason(self):
        with pytest.raises(ValueError, match="evidence or its reason"):
            CheckResult.not_checked("x", "t", "f", "   ")


class TestARequiredGapBlocksTheReport:
    def test_all_measured_and_passing_is_conformant(self):
        assert _report(_ok("a"), _ok("b")).is_conformant

    @pytest.mark.parametrize("gap", [
        CheckResult.not_checked("g", "t", "f", "the runtime was not reachable"),
        CheckResult.probe_error("g", "t", "f", "the probe produced no readings"),
        CheckResult.measured("g", "t", "f", False, Vantage.INSIDE, "it did not hold"),
    ])
    def test_a_required_gap_blocks_it(self, gap):
        report = _report(_ok("a"), gap)
        assert not report.is_conformant
        assert gap.check_id in report.summary_line()
        assert "unproven" in report.summary_line()

    def test_an_inapplicable_required_property_does_not_block_it(self):
        report = _report(_ok("a"),
                         CheckResult.not_applicable("g", "t", "f", "no content leaves this machine"))
        assert report.is_conformant
        assert "1 not applicable" in report.summary_line()

    def test_an_optional_gap_does_not_block_it(self):
        report = _report(_ok("a"), CheckResult.not_checked("g", "t", "f", "skipped", required=False))
        assert report.is_conformant

    def test_a_test_double_can_never_be_conformant(self):
        report = _report(_ok("a"), _ok("b"), double=True)
        assert not report.is_conformant
        assert "TEST DOUBLE" in report.summary_line()
        assert "not evidence about a backend" in report.summary_line()

    def test_duplicate_check_ids_are_refused(self):
        with pytest.raises(ValueError, match="duplicate check id"):
            _report(_ok("a"), _ok("a"))

    def test_the_json_form_carries_the_verdict_and_the_counts(self):
        import json
        d = json.loads(_report(_ok("a"), CheckResult.not_checked("g", "t", "f", "why")).to_json())
        assert d["is_conformant"] is False
        assert d["counts"]["not_checked"] == 1
        assert d["results"][0]["assurance"] == "observed"
        assert d["results"][1]["outcome"] == "not_checked"
