"""The cross-host rebuild request is checked, not trusted (docs/testing/crosshost-rebuild-request.md).

It is the plan the Chef and the next acceptance run work from, so a wrong target, a quietly edited
criterion or a hash written down before the profile exists would send people to do the wrong thing.
The request carries a JSON block; this file holds it to the decisions made for it:

* exactly two targets: control plane 2.28.194.41, worker 128.140.87.58 -- no other public address
  anywhere in the document;
* the acceptance catalog is `remote-worker-cross-host-r2` (X1-X10, unchanged, with r2's full
  sha256) -- decided for this rebuild; r3, which carried the same criteria over verbatim, is named
  only as the historical frozen state and stays unchanged;
* the profile the run is judged against is `remote-worker-cross-host-r4`, created AFTER the
  rebuild, bound to both targets and the build actually tested, validated, filed and frozen BEFORE
  any criterion runs -- and no r4 hash is written down in advance;
* every criterion maps to at least one `remote-worker-r1` criterion and one step, and every step
  is used; each rebuild is followed by a single-machine validation before the next rebuild;
* the rebuild is a Chef step; abort conditions, rollback, evidence folder and the cost cap are there.

Where the r2 and r3 profiles are on this machine they are read too: their criteria must equal the
ones named here and their digests must still be the ones named here.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
REQUEST = REPO / "docs" / "testing" / "crosshost-rebuild-request.md"
#: Where the frozen r3 profile lives on this machine. Overridable so a counter-check can point at a
#: CHANGED COPY -- the real profile is never touched.
R3_FILE = Path(os.environ.get("AGENTNODE_R3_PROFILE")
               or r"H:\Claude Projekte\agentnode-Tests\a1e_review\crosshost-r3\frozen"
                  r"\remote-worker-cross-host-r3.json")
R2_FILE = Path(os.environ.get("AGENTNODE_R2_PROFILE")
               or r"H:\Claude Projekte\agentnode-Tests\a1e_review\crosshost-final"
                  r"\remote-worker-cross-host-r2.json")

CONTROL, WORKER = "2.28.194.41", "128.140.87.58"
R3 = "remote-worker-cross-host-r3"
R3_SHA = "3aaee465933f2a777d8fed46b17ab4874970209b7d929de7a8d17b7e7bc02a10"
R2 = "remote-worker-cross-host-r2"
R2_SHA = "5fc1cc507413df18a6fbf22f5c09df63d6443f1409a7cc92b57eef2f3738e56d"
R1 = "remote-worker-r1"
R1_SHA = "e480c2f02382bad9432706561a49add218f4f9f03cb9a368b83804fcf2108d3a"
R4 = "remote-worker-cross-host-r4"
X = ["X1-A-WORK-CROSSES-THE-KERNEL-BOUNDARY",
     "X2-B-THE-WORKER-PORT-IS-PRIVATE-AND-THAT-IS-MEASURED-NEGATIVELY",
     "X3-C-A-REAL-NETWORK-BREAK-DURING-A-JOB", "X4-D-BOTH-SIDES-SURVIVE-A-RESTART",
     "X5-E-INSTALL-UPGRADE-ROLLBACK-ON-TWO-HOSTS", "X6-F-WHAT-IS-NOT-ON-THE-WORKER-DISK",
     "X7-G-EXHAUSTION-ON-THE-WORKER-LEAVES-THE-CONTROL-PLANE-HONEST", "X8-R16-TWO-REAL-MACHINES",
     "X9-NO-RESULT-WAS-REPEATED-INTO-EXISTENCE", "X10-THE-RECORD-DOES-NOT-OVERCLAIM"]
R1_CRITERIA = {
    "R1-REMOTE-IS-A-REAL-TOPOLOGY-AND-THERE-IS-NO-WAY-BACK", "R2-WORKER-IDENTITY-IS-NOT-A-STRING-IT-SENDS",
    "R3-ONE-WORKER-ONE-PROOF", "R4-FAIL-CLOSED-ON-LOSS-REVOCATION-EXPIRY-AND-TIME",
    "R5-WHAT-THE-WORKER-IS-NOT-GIVEN", "R6-EXACTLY-ONCE-OR-HONESTLY-UNKNOWN",
    "R7-NOTHING-IS-BILLED-FOR-WAITING-OR-FOR-NOT-KNOWING", "R8-CLEANUP-IS-DONE-OR-HONESTLY-UNPROVEN",
    "R9-A-STOP-REACHES-WORK-THAT-HAS-NOT-STARTED", "R10-THE-SIGNED-PROOF-BINDS-THE-WHOLE-SITUATION",
    "R11-NO-SECRET-WHERE-A-SECRET-DOES-NOT-BELONG", "R12-A-REFUSAL-SAYS-WHICH-CAUSE-AND-WHAT-TO-DO",
    "R13-THE-ACCESS-LAYER-IS-NOT-NARROWED", "R14-INSTALL-UPGRADE-ROLLBACK-AND-DIAGNOSE",
    "R15-THE-COUNTER-CHECKS-DISCRIMINATE", "R16-TWO-REAL-MACHINES", "R17-THE-BUNDLE-DOES-NOT-OVERCLAIM"}
PROFILE_PHASE = ("profile-create", "profile-validate", "profile-file", "profile-freeze")


def _text() -> str:
    assert REQUEST.is_file(), "the rebuild request is missing: %s" % REQUEST
    return REQUEST.read_text(encoding="utf-8")


def _plan() -> dict:
    blocks = re.findall(r"```json\n(.*?)```", _text(), flags=re.S)
    assert len(blocks) == 1, "the request must carry exactly one JSON plan"
    return json.loads(blocks[0])


# ---- targets ------------------------------------------------------------------------------------- #


def test_exactly_the_two_targets_with_their_roles():
    t = _plan()["targets"]
    assert set(t) == {"control", "worker"}
    assert (t["control"]["public_ipv4"], t["control"]["role"]) == (CONTROL, "control-plane")
    assert (t["worker"]["public_ipv4"], t["worker"]["role"]) == (WORKER, "worker")


def test_no_other_public_address_appears_anywhere():
    for found in set(re.findall(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", _text())):
        ip = ipaddress.ip_address(found)
        assert ip.is_private or found in (CONTROL, WORKER), "a third machine is named: " + found


# ---- the catalog ---------------------------------------------------------------------------------- #


def test_the_acceptance_catalog_is_r2_unchanged_with_its_full_hash():
    c = _plan()["acceptance_criteria"]
    assert c["catalog_profile"] == R2 and c["catalog_sha256"] == R2_SHA
    assert c["criteria"] == X


def test_r3_is_the_historical_frozen_state_and_never_the_catalog():
    plan = _plan()
    assert plan["acceptance_criteria"]["catalog_profile"] != R3
    assert plan["history"] == [{"profile": R3, "sha256": R3_SHA,
                                "role": "historical frozen state, unchanged"}]


@pytest.mark.skipif(not R2_FILE.is_file(), reason="the r2 profile is not on this machine")
def test_the_r2_catalog_is_unchanged_and_says_the_same():
    raw = R2_FILE.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == R2_SHA, "the r2 catalog has changed"
    assert [c["id"] for c in json.loads(raw)["criteria"]] == X


@pytest.mark.skipif(not R3_FILE.is_file(), reason="the frozen r3 profile is not on this machine")
def test_the_frozen_r3_profile_is_unchanged_and_says_the_same():
    raw = R3_FILE.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == R3_SHA, "the frozen r3 profile has changed"
    assert [c["id"] for c in json.loads(raw)["criteria"]] == X


# ---- the profile the run will be judged against --------------------------------------------------- #


def test_the_new_profile_is_r4_and_its_hash_is_not_invented():
    new = _plan()["new_profile"]
    assert new["name"] == R4
    assert new["supersedes"] == {"profile": R3, "sha256": R3_SHA}
    assert "sha256" not in new
    known = {R1_SHA, R2_SHA, R3_SHA}
    for digest in re.findall(r"\b[0-9a-f]{64}\b", _text()):
        assert digest in known, "a digest nobody can know yet is written down: " + digest


def test_the_new_profile_binds_both_targets_and_the_build_actually_tested():
    binds = _plan()["new_profile"]["binds"]
    assert binds["control"] == CONTROL and binds["worker"] == WORKER
    assert binds["build"] and not re.fullmatch(r"[0-9a-f]{7,64}", binds["build"]), (
        "the build is named by what will be tested, not by a commit picked in advance")


def test_the_new_profile_is_made_checked_filed_and_frozen_before_any_criterion_runs():
    steps = _plan()["steps"]
    phases = [s["phase"] for s in steps]
    for phase in PROFILE_PHASE:
        assert phase in phases, phase
    freeze = phases.index("profile-freeze")
    assert [phases.index(p) for p in PROFILE_PHASE] == sorted(phases.index(p) for p in PROFILE_PHASE)
    first_criterion = min(i for i, p in enumerate(phases) if p == "criterion")
    assert freeze < first_criterion
    last_rebuild = max(i for i, p in enumerate(phases) if p == "chef-rebuild")
    assert last_rebuild < phases.index("profile-create"), "r4 is made after the rebuild"


# ---- map, order, roles ----------------------------------------------------------------------------- #


def test_every_criterion_maps_to_r1_and_to_real_steps_and_every_step_is_used():
    plan = _plan()
    ids = {s["id"] for s in plan["steps"]}
    assert len(ids) == len(plan["steps"]), "two steps share an id"
    cmap = plan["criteria_map"]
    assert list(cmap) == X, "the map must name exactly X1-X10, in order"
    used = set()
    for criterion, entry in cmap.items():
        assert entry["r1"] and set(entry["r1"]) <= R1_CRITERIA, (criterion, entry["r1"])
        assert entry["steps"] and set(entry["steps"]) <= ids, (criterion, entry["steps"])
        used |= set(entry["steps"])
    structural = {s["id"] for s in plan["steps"]
                  if s["phase"] in ("chef-rebuild", "validate-machine", "park") + PROFILE_PHASE}
    assert ids == used | structural, "unused steps: %s" % sorted(ids - used - structural)
    for s in plan["steps"]:
        if s["phase"] == "criterion":
            assert s["id"] in used, s["id"]


def test_each_rebuild_is_validated_on_its_own_before_the_next():
    steps = _plan()["steps"]
    order = [(s["phase"], s["machine"]) for s in steps]
    rc, vc = order.index(("chef-rebuild", "control")), order.index(("validate-machine", "control"))
    rw, vw = order.index(("chef-rebuild", "worker")), order.index(("validate-machine", "worker"))
    assert rc < vc < rw < vw
    assert min(i for i, (p, _m) in enumerate(order) if p == "install") > vw


def test_the_rebuild_is_the_chefs_and_everything_else_is_not():
    plan = _plan()
    for s in plan["steps"]:
        if s["phase"] == "chef-rebuild":
            assert s["who"] == "chef", s
        else:
            assert s["who"] == "mitarbeiter", s
    assert plan["build_up_procedure"] == {"profile": R1, "sha256": R1_SHA}


def test_abort_rollback_evidence_and_cost_cap_are_there():
    plan = _plan()
    aborts = " ".join(plan["abort_conditions"]).lower()
    for must in ("hostname", "host key", "other than the two targets", "cost cap",
                 "before the worker is rebuilt", "freeze"):
        assert must in aborts, must
    assert len(plan["rollback"]) > 80
    assert plan["evidence_dir"].endswith("crosshost-r4")
    assert plan["cost_cap_eur_per_month"] == 20
