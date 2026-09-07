"""Negative controls for the frozen-inventory gate.

The R6 lane's whole value is that it refuses a run whose collection does not match the inventory
pinned beside it. A gate that cannot be made to fail is not evidence that anything matched, so
these deliberately break the pairing in each way it can break and require a non-zero exit.

They also cover the specific way the pairing came apart on this branch: the pin named one tree
and the inventory described another, which surfaced as node ids the inventory had never heard of.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.lanes import freeze_inventory as fi

SDK = Path(__file__).resolve().parents[1]
FROZEN = SDK / "tests" / "lanes" / "FROZEN_INVENTORY.json"


@pytest.fixture()
def frozen():
    return json.loads(FROZEN.read_text(encoding="utf-8"))


def _report(node_ids, outcome="passed", reasons=None):
    return {"outcomes": {n: outcome for n in node_ids}, "skip_reasons": reasons or {}}


def _run(tmp_path, frozen_doc, report_doc, lane="ordinary"):
    f = tmp_path / "frozen.json"
    r = tmp_path / "report.json"
    f.write_text(json.dumps(frozen_doc), encoding="utf-8")
    r.write_text(json.dumps(report_doc), encoding="utf-8")
    return fi.cmd_verify(str(f), str(r), lane)


class TestTheGateAcceptsAMatchingRun:
    def test_an_exact_match_is_clean(self, tmp_path, frozen):
        assert _run(tmp_path, frozen, _report(frozen["node_ids"])) == 0


class TestTheGateRejectsAMismatchedPairing:
    def test_a_missing_node_id_is_not_clean(self, tmp_path, frozen):
        """Collection lost a test the inventory expects."""
        assert _run(tmp_path, frozen, _report(frozen["node_ids"][:-1])) == 1

    def test_an_unknown_node_id_is_not_clean(self, tmp_path, frozen):
        """The shape the wrong pin produced: tests exist that the inventory never saw."""
        assert _run(tmp_path, frozen,
                    _report(frozen["node_ids"] + ["tests/test_invented.py::test_nobody_froze"])) == 1

    def test_a_wholesale_different_tree_is_not_clean(self, tmp_path, frozen):
        """A pin naming another revision entirely."""
        assert _run(tmp_path, frozen,
                    _report([f"tests/test_other.py::test_{i}" for i in range(50)])) == 1

    def test_an_empty_collection_is_not_clean(self, tmp_path, frozen):
        """A run that collected nothing must not read as 'nothing failed'."""
        assert _run(tmp_path, frozen, _report([])) == 1

    def test_a_truncated_inventory_is_not_clean(self, tmp_path, frozen):
        """The inventory, not the run, is the thing that is wrong here."""
        short = dict(frozen, node_ids=frozen["node_ids"][:10])
        assert _run(tmp_path, short, _report(frozen["node_ids"])) == 1


class TestTheGateRejectsABadRun:
    def test_a_failure_is_not_clean(self, tmp_path, frozen):
        rep = _report(frozen["node_ids"])
        rep["outcomes"][frozen["node_ids"][0]] = "failed"
        assert _run(tmp_path, frozen, rep) == 1

    def test_an_error_is_not_clean(self, tmp_path, frozen):
        rep = _report(frozen["node_ids"])
        rep["outcomes"][frozen["node_ids"][0]] = "error"
        assert _run(tmp_path, frozen, rep) == 1

    def test_a_skipped_mandatory_test_is_not_clean(self, tmp_path, frozen):
        prefix = next(p for p, lane in frozen["mandatory"].items() if lane == "ordinary")
        rep = _report(frozen["node_ids"])
        hit = next(n for n in frozen["node_ids"] if n.startswith(prefix))
        rep["outcomes"][hit] = "skipped"
        assert _run(tmp_path, frozen, rep) == 1

    def test_an_optional_skip_without_a_named_precondition_is_not_clean(self, tmp_path, frozen):
        prefixes = tuple(frozen["optional_provider_prefixes"])
        hit = next((n for n in frozen["node_ids"] if n.startswith(prefixes)), None)
        if hit is None:
            pytest.skip("no optional-provider case in the inventory to exercise")
        rep = _report(frozen["node_ids"])
        rep["outcomes"][hit] = "skipped"
        rep["skip_reasons"][hit] = "because"
        assert _run(tmp_path, frozen, rep) == 1


class TestTheInventoryDescribesThisTree:
    def test_the_pinned_inventory_is_not_empty_and_covers_the_new_policy_tests(self, frozen):
        """A pairing that silently lost the material this branch adds would still be 'clean'."""
        assert frozen["count"] == len(frozen["node_ids"]) > 0
        assert any(n.startswith("tests/test_policy_composition.py") for n in frozen["node_ids"])
        assert any(n.startswith("tests/test_em3_contract.py") for n in frozen["node_ids"])
        assert any(n.startswith("tests/test_frozen_inventory_controls.py")
                   for n in frozen["node_ids"])

    def test_no_duplicate_node_ids(self, frozen):
        assert len(frozen["node_ids"]) == len(set(frozen["node_ids"]))
