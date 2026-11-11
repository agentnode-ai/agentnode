"""Task #33: the committed root agentnode.lock passes the integrity check in STRICT mode.

The compatibility smoke test (.github/workflows/compatibility-check.yml, sdk/scripts/ci_smoke_test.py)
runs against the repository's own agentnode.lock. That file predates lockfile integrity, so every
run logged "Lockfile integrity for 'word-counter-pack': entry=missing, structure=invalid" -- allowed
in normal mode, DENIED under AGENTNODE_GUARD_STRICT. The smoke test passed on a state a strict
operator refuses.

The file is now sealed with the product's own `agentnode lock seal`. This holds:

* every package in it is allowed in strict mode, with both statuses "verified";
* the seal is reproducible: stripping the integrity fields and sealing again with the same core
  functions gives back exactly the committed file, so the seal added nothing but integrity and
  changed no other field.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from agentnode_sdk import lock_integrity as li

LOCK = Path(__file__).resolve().parents[2] / "agentnode.lock"


def _lock() -> dict:
    return json.loads(LOCK.read_text(encoding="utf-8"))


def test_the_root_lockfile_has_packages():
    assert _lock()["packages"], "the smoke test's lockfile is empty"


@pytest.mark.parametrize("slug", sorted(json.loads(LOCK.read_text(encoding="utf-8"))["packages"]))
def test_every_package_is_allowed_in_strict_mode(slug):
    report = li.evaluate_lock_integrity(slug, _lock(), strict=True)
    assert (report.entry_status, report.structure_status) == ("verified", "verified"), report.reason
    assert report.allowed


def test_the_seal_is_reproducible_and_changed_nothing_else():
    committed = _lock()
    stripped = copy.deepcopy(committed)
    stripped.pop("structure_digest", None)
    for entry in stripped["packages"].values():
        entry.pop("_integrity", None)
    resealed = copy.deepcopy(stripped)
    resealed["packages"] = {slug: li.seal_entry(entry) for slug, entry in stripped["packages"].items()}
    resealed = li.seal_structure(resealed)
    assert resealed == committed
