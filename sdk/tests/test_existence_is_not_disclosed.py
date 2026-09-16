"""One account cannot learn that another account's object exists. Three parts, all falsifiable.

FROZEN BEFORE THE IMPLEMENTATION. `EXISTENCE-ISOLATION-DECISION-0001` chose Option A and made
the order binding: freeze these tests, pre-register the timing measurement, then change the
architecture. The git history is the evidence for that order.

## What was wrong

`_a_run_of_this_caller` looked a run up in `service.runs` -- EVERY run on the gateway, all
accounts -- and rejected it afterwards. The ANSWER was already identical for a foreign run and a
run that never existed; `test_tenancy_on_every_surface.py` asserts that byte for byte. The WORK
was not: a foreign run is found and then rejected, an absent one is not found at all. The branch
structure depended on whether another account's object existed. The same shape was in
`Joining.withdraw` and in the setup door's use of `Connections.about`.

## The three parts

**(a)** No intentional or structurally existence-dependent branching. Resolution happens inside
the caller's own namespace, so a foreign identifier is not found and rejected -- it is not found.

**(b)** Identical protocol-visible results: refusal name, wording, remedy, response shape, and
the same visible side effects, including what is written to the audit and what is not.

**(c)** No statistically robust timing difference above a threshold fixed BEFORE the measurement,
against a null calibration taken in the same run on the same machine.

## What is deliberately NOT claimed

Constant-time execution in CPython. Garbage collection, dictionary probing, the allocator and the
interpreter's own scheduling all remain. `EXISTENCE-ISOLATION-DECISION-0001` finding
F2-TIMING-BOUND-LIMIT says so in the reviewer's own words, and it is right: part (c) establishes
that no difference survives above the noise of the measurement, and that the measurement is
sensitive enough to catch the regression it was built from. It does not establish the absence of
every timing channel, and nothing here should be read as saying it does.
"""
from __future__ import annotations

import gc
import inspect
import json
import time
import uuid

import pytest

from agentnode_sdk.access import contract, dispatch
from agentnode_sdk.gateway import joining as joining_module
from agentnode_sdk.gateway.identity import GatewayState
from agentnode_sdk.gateway.server import GatewayService
from tests.test_em3c_gateway import StandInBackend, _store_measurement
from tests.test_two_accounts import _a_customer, _a_run_by

# ---------------------------------------------------------------- pre-registered, before the fix

#: Samples per group. Fixed here, before the measurement was written, per the binding next action
#: of EXISTENCE-ISOLATION-DECISION-0001.
SAMPLES = 2000

#: How many DIFFERENT identifiers each group draws from. One foreign id and one absent id would
#: characterise those two ids rather than the two situations -- finding F2 names exactly that.
IDENTIFIERS = 16

#: How much better than the null an attacker's best single-threshold guess may do. Fixed before
#: the measurement. 0.5 is chance.
MARGIN = 0.05


@pytest.fixture()
def two_accounts(tmp_path):
    state = GatewayState(str(tmp_path / "state"), version="test")
    service = GatewayService(state, backend=StandInBackend())
    _store_measurement(service)
    alice, bob = _a_customer(service, "alice"), _a_customer(service, "bob")
    try:
        yield service, alice, bob
    finally:
        state.close()


def _absent_id() -> str:
    return uuid.uuid4().hex


def _best_threshold_accuracy(one, other) -> float:
    """The best an attacker with a stopwatch can do: one threshold, both orientations.

    0.5 is chance. Computed the same way for the real comparison and for the null, by this same
    function, so the two numbers are comparable by construction.
    """
    marked = sorted([(v, 0) for v in one] + [(v, 1) for v in other])
    total = len(one) + len(other)
    best = 0.0
    below_one = 0
    below_other = 0
    for value, which in marked:
        below_one += which == 0
        below_other += which == 1
        # "at or below the threshold is `one`" and the mirror of it.
        forward = below_one + (len(other) - below_other)
        backward = below_other + (len(one) - below_one)
        best = max(best, forward / total, backward / total)
    return best


# ------------------------------------------------------------------ (a) the code path


class TestNothingBranchesOnWhetherAnotherAccountsObjectExists:
    """Read as code. A property about which lookup is performed cannot be seen from outside."""

    def test_a_run_is_resolved_inside_the_callers_namespace(self):
        source = inspect.getsource(dispatch._a_run_of_this_caller)
        assert "owned_by(" in source, (
            "a run is still resolved against every run on the gateway and rejected afterwards. "
            "A foreign id must not be FOUND and refused; it must not be found.")
        assert "service.runs.get(" not in source, source

    def test_an_invitation_is_withdrawn_inside_the_callers_namespace(self):
        source = inspect.getsource(joining_module.Joining.withdraw)
        assert "of_account(" in source or "mine" in source, (
            "withdraw still walks every invitation on the gateway and compares the account "
            "afterwards")

    def test_and_the_setup_door_resolves_inside_it_too(self):
        from agentnode_sdk.gateway import server as server_module

        source = inspect.getsource(server_module)
        hand_over = source[source.index("_hand_over_a_setup_file"):]
        hand_over = hand_over[:hand_over.index("\n    def ", 10)]
        assert "about_for(" in hand_over, (
            "the setup door still looks a challenge up globally and compares the account after")


# ------------------------------------------------------------------ (b) what comes back


class TestAForeignIdentifierIsAnsweredExactlyLikeAnAbsentOne:
    """Every customer-facing operation that takes an identifier, both cases, compared whole."""

    def _refusal(self, service, who, operation, params):
        try:
            return ("carried_out", dispatch.dispatch(operation, params, who, service=service))
        except dispatch.Refused as refused:
            return (refused.refusal, {"because": refused.because,
                                      "what_to_do": refused.what_to_do})

    def _without(self, said, *supplied):
        text = json.dumps(said, sort_keys=True, default=str)
        for one in supplied:
            text = text.replace(str(one), "<what the caller supplied>")
        return text

    def _audit_after(self, service, doing):
        path = service.state.root / "audit.jsonl"
        before = len(path.read_text(encoding="utf-8").splitlines()) if path.exists() else 0
        doing()
        lines = path.read_text(encoding="utf-8").splitlines()[before:]
        return [json.loads(raw) for raw in lines if raw.strip()]

    def test_a_run(self, two_accounts):
        service, alice, bob = two_accounts
        hers, absent = _a_run_by(service, alice), _absent_id()
        for operation in ("status", "result", "cancel"):
            foreign = self._refusal(service, bob, operation, {"run_id": hers})
            missing = self._refusal(service, bob, operation, {"run_id": absent})
            assert foreign[0] == missing[0] == "no_such_run", operation
            assert self._without(foreign[1], hers) == self._without(missing[1], absent), operation

    def test_and_the_audit_line_is_the_same_too(self, two_accounts):
        """A side effect is visible. A record written for one and not the other would tell an
        operator -- and anybody who can read the log -- which of the two it was."""
        service, alice, bob = two_accounts
        hers, absent = _a_run_by(service, alice), _absent_id()

        foreign = self._audit_after(service, lambda: self._refusal(
            service, bob, "status", {"run_id": hers}))
        missing = self._audit_after(service, lambda: self._refusal(
            service, bob, "status", {"run_id": absent}))

        assert len(foreign) == len(missing) == 1, (foreign, missing)
        for line in (foreign[0], missing[0]):
            line.pop("at", None)
        assert foreign[0] == missing[0], (foreign[0], missing[0])

    def test_a_session(self, two_accounts):
        service, alice, bob = two_accounts
        hers, _csrf = service.sessions.open(alice.device_id, label="her browser")
        named = dispatch.dispatch("sessions.list", {}, alice, service=service)["sessions"][0]
        absent = _absent_id()[:16]

        foreign = self._refusal(service, bob, "sessions.end", {"session": named["session"]})
        missing = self._refusal(service, bob, "sessions.end", {"session": absent})
        assert self._without(foreign[1], named["session"]) == self._without(missing[1], absent)
        assert service.sessions.whose(hers) is not None

    def test_an_invitation(self, two_accounts):
        service, alice, bob = two_accounts
        made = dispatch.dispatch("devices.invite", {}, alice, service=service)
        absent = "ZZZZZZZZ"

        foreign = self._refusal(service, bob, "devices.uninvite",
                                {"invitation": made["invitation"]})
        missing = self._refusal(service, bob, "devices.uninvite", {"invitation": absent})
        assert self._without(foreign[1], made["invitation"]) == self._without(missing[1], absent)

    def test_a_device(self, two_accounts):
        service, alice, bob = two_accounts
        absent = _absent_id()
        foreign = self._refusal(service, bob, "devices.revoke", {"device_id": alice.device_id})
        missing = self._refusal(service, bob, "devices.revoke", {"device_id": absent})
        assert self._without(foreign[1], alice.device_id) == self._without(missing[1], absent)

    def test_an_enrolment_challenge(self, two_accounts):
        service, alice, bob = two_accounts
        hers = dispatch.dispatch("connections.enrol",
                                 {"way_in": contract.MCP, "label": "her AI"},
                                 alice, service=service)
        absent = _absent_id()
        foreign = self._refusal(service, bob, "connections.check",
                                {"challenge": hers["challenge"]})
        missing = self._refusal(service, bob, "connections.check", {"challenge": absent})
        assert self._without(foreign[1], hers["challenge"]) == self._without(missing[1], absent)


# ------------------------------------------------------------------ (c) the measurement


class TestNoTimingDifferenceSurvivesAboveTheNoiseOfTheMeasurement:
    """Pre-registered: 2000 interleaved samples per group, 16 identifiers each, margin 0.05.

    Measured at the FUNCTION rather than at the door, and that is the stronger choice: a
    dispatch appends to the audit file, and a millisecond of disk noise would bury a channel
    rather than disprove one. `_a_run_of_this_caller` is where the branch was. A null here is a
    null at the door, because the door's extra work is identical for both cases and only adds
    noise on top.
    """

    def _samples(self, service, who, foreign, absent, other_absent):
        """Interleaved round-robin, so drift and scheduling hit all three groups equally."""
        groups = {"foreign": [], "absent": [], "null": []}
        pick = [("foreign", foreign), ("absent", absent), ("null", other_absent)]
        was_enabled = gc.isenabled()
        gc.disable()
        try:
            for i in range(SAMPLES):
                for name, pool in pick:
                    run_id = pool[i % len(pool)]
                    started = time.perf_counter_ns()
                    try:
                        dispatch._a_run_of_this_caller(service, who, run_id)
                    except dispatch.Refused:
                        pass
                    groups[name].append(time.perf_counter_ns() - started)
        finally:
            if was_enabled:
                gc.enable()
        return groups

    def test_an_attacker_with_a_stopwatch_does_no_better_than_chance(self, two_accounts):
        service, alice, bob = two_accounts
        foreign = [_a_run_by(service, alice) for _ in range(IDENTIFIERS)]
        absent = [_absent_id() for _ in range(IDENTIFIERS)]
        other_absent = [_absent_id() for _ in range(IDENTIFIERS)]

        groups = self._samples(service, bob, foreign, absent, other_absent)

        # The null: two situations that ARE identical, measured the same way in the same run.
        # Whatever separation this shows is what this machine's noise is worth today.
        null = _best_threshold_accuracy(groups["absent"], groups["null"])
        real = _best_threshold_accuracy(groups["foreign"], groups["absent"])

        assert real <= null + MARGIN, (
            "telling a FOREIGN run from an ABSENT one by timing alone succeeds %.3f of the time, "
            "against a null of %.3f on the same machine in the same run. The margin fixed before "
            "this measurement was %.2f." % (real, null, MARGIN))

    def test_and_the_measurement_can_tell_when_there_IS_a_difference(self, two_accounts):
        """Sensitivity, in the same run, so a passing result above is not a broken instrument.

        A deliberate existence-dependent branch -- the shape that was removed -- must be caught
        by the same statistic with the same margin. Without this, a measurement that always
        returns chance would pass the test above for ever.
        """
        service, alice, bob = two_accounts
        foreign = [_a_run_by(service, alice) for _ in range(IDENTIFIERS)]
        absent = [_absent_id() for _ in range(IDENTIFIERS)]
        other_absent = [_absent_id() for _ in range(IDENTIFIERS)]

        def with_the_branch_back(run_id):
            record = service.runs.get(str(run_id))          # the global lookup, restored
            if record is not None:
                # What the old code did with what it found: read two fields and compare them.
                if (record.owner_client_id, record.owner_account_id) != ("", ""):
                    raise dispatch.Refused("no_such_run", "no", "no")
            raise dispatch.Refused("no_such_run", "no", "no")

        groups = {"foreign": [], "absent": [], "null": []}
        pick = [("foreign", foreign), ("absent", absent), ("null", other_absent)]
        was_enabled = gc.isenabled()
        gc.disable()
        try:
            for i in range(SAMPLES):
                for name, pool in pick:
                    started = time.perf_counter_ns()
                    try:
                        with_the_branch_back(pool[i % len(pool)])
                    except dispatch.Refused:
                        pass
                    groups[name].append(time.perf_counter_ns() - started)
        finally:
            if was_enabled:
                gc.enable()

        null = _best_threshold_accuracy(groups["absent"], groups["null"])
        real = _best_threshold_accuracy(groups["foreign"], groups["absent"])
        assert real > null + MARGIN, (
            "the instrument did not notice a deliberate existence-dependent branch: %.3f against "
            "a null of %.3f. A measurement that cannot see the defect it was built from is not "
            "evidence that the defect is gone." % (real, null))


# ------------------------------------------------------------------ the index cannot drift


class TestTheOwnerIndexSaysExactlyWhatTheContentsSay:
    """`EXISTENCE-ISOLATION-DECISION-0001` finding F1-INDEX-CONSISTENCY, answered.

    The worry is right and it is the one that matters: an index maintained by whoever remembers
    to maintain it drifts, and the way it drifts is that a customer's OWN run stops being
    reachable. So every mutation goes through one class, and `everything_matches()` rebuilds the
    index from the contents -- these tests drive the paths the finding names and assert they
    still agree afterwards.
    """

    def _drive_everything(self, service, alice, bob):
        """Creation, ownership, replacement, cancellation, deletion, and recovery."""
        from agentnode_sdk.gateway.server import RunRecord

        made = [_a_run_by(service, alice), _a_run_by(service, bob), _a_run_by(service, alice)]
        # Replacement in place, which is what a restart's recovery does to a record it re-reads.
        again = RunRecord(run_id=made[0], job_id="", state="finished")
        again.owner_client_id = alice.client_id
        again.owner_account_id = alice.account_id
        service.runs[made[0]] = again
        # A record with no owner at all -- the older-door and pre-accounts shape.
        orphan = _absent_id()
        service.runs[orphan] = RunRecord(run_id=orphan, job_id="", state="finished")
        # And one taken away again.
        del service.runs[made[2]]
        return made, orphan

    def test_after_every_kind_of_change(self, two_accounts):
        service, alice, bob = two_accounts
        self._drive_everything(service, alice, bob)
        assert service.runs.everything_matches(), (
            "the owner index and the runs disagree, which is how a customer's own run becomes "
            "unreachable")

    def test_and_a_run_with_no_owner_is_in_nobodys_namespace(self, two_accounts):
        service, alice, bob = two_accounts
        _made, orphan = self._drive_everything(service, alice, bob)
        assert orphan in service.runs
        for who in (alice, bob):
            assert orphan not in service.runs.owned_by(who.account_id, who.client_id)
        assert service.runs.everything_matches()

    def test_and_each_account_sees_exactly_its_own(self, two_accounts):
        service, alice, bob = two_accounts
        made, _orphan = self._drive_everything(service, alice, bob)
        hers = service.runs.owned_by(alice.account_id, alice.client_id)
        his = service.runs.owned_by(bob.account_id, bob.client_id)
        assert made[0] in hers and made[0] not in his
        assert made[1] in his and made[1] not in hers
        assert made[2] not in hers and made[2] not in his, "a deleted run is still in a namespace"

    def test_and_an_owner_with_nothing_costs_what_an_unknown_owner_costs(self, two_accounts):
        """Both are the same empty mapping. An owner who happens to have no runs must not be
        distinguishable from a name nobody has ever had."""
        service, _alice, _bob = two_accounts
        nobody = service.runs.owned_by("acct-" + "0" * 16, "c" * 16)
        also = service.runs.owned_by("acct-" + "1" * 16, "d" * 16)
        assert nobody == {} and also == {}
        # This asserted `nobody is also` when it was frozen, because one shared empty mapping was
        # how "the same cost" was expressed. `TIMING-PROTOCOL-V2-DECISION-0001` then made the
        # fold OWNER-BOUND, and a single shared namespace would mean the fold stopped being
        # owner-bound for precisely the owners who have nothing. The property being asserted is
        # unchanged -- the same work, whoever is asking -- and what changed is that identity is
        # no longer the way to say it. Recorded rather than quietly edited.
        assert nobody._runs is also._runs, "an empty owner is not the shared empty mapping"
        assert len(nobody) == len(also) == 0

    def test_and_reindexing_a_record_whose_owner_changed_keeps_them_in_step(self, two_accounts):
        """Nothing in this product changes a run's owner after it is created. If something
        starts to, `reindex` is what it must call -- so it has to work."""
        service, alice, bob = two_accounts
        run = _a_run_by(service, alice)
        service.runs[run].owner_account_id = bob.account_id
        service.runs[run].owner_client_id = bob.client_id
        assert not service.runs.everything_matches(), (
            "a field mutated behind the mapping's back went unnoticed, so this test proves "
            "nothing about reindex")
        service.runs.reindex(run)
        assert service.runs.everything_matches()
        assert run in service.runs.owned_by(bob.account_id, bob.client_id)
        assert run not in service.runs.owned_by(alice.account_id, alice.client_id)


class TestACallerSuppliedIdentifierIsNormalisedBeforeItIsAKey:
    """`TIMING-PROTOCOL-V2-DECISION-0001`, Option A, first half.

    A caller's identifier is folded into a fixed-width, owner-bound keyed digest before it is
    used to probe anything. What that buys is structural and is asserted structurally; none of
    it is a timing claim, and `docs/review/TIMING-PROTOCOL-V2.md` says so in the same words.
    """

    def test_the_width_of_what_the_caller_sent_does_not_reach_the_lookup(self, two_accounts):
        """A four-character identifier and a four-kilobyte one become the same-sized key."""
        from agentnode_sdk.gateway import runs as runs_module

        service, _alice, bob = two_accounts
        where = service.runs.owned_by(bob.account_id, bob.client_id)
        for sent in ("x", "y" * 31, "z" * 4096, ""):
            assert len(where._fold(sent)) == runs_module.KEY_WIDTH

    def test_and_it_is_bound_to_the_OWNER(self, two_accounts):
        """The same string is a different key in a different account, so bucket structure in
        one namespace says nothing about another's."""
        service, alice, bob = two_accounts
        same = "the-same-identifier"
        hers = service.runs.owned_by(alice.account_id, alice.client_id)._fold(same)
        his = service.runs.owned_by(bob.account_id, bob.client_id)._fold(same)
        assert hers != his

    def test_and_the_key_that_binds_it_is_never_written_down(self, two_accounts):
        """F1-HOT-PATH-AND-SECRET asked what its lifecycle is. It has none: it lives in this
        process's memory, it authenticates nothing, and a restart makes a new one."""
        service, _alice, _bob = two_accounts
        secret = service.runs._secret
        assert len(secret) == 32
        for path in sorted(service.state.root.iterdir()):
            if path.is_file():
                assert secret not in path.read_bytes(), path.name
                assert secret.hex().encode() not in path.read_bytes(), path.name

    def test_and_two_gateways_do_not_share_it(self, two_accounts, tmp_path):
        from agentnode_sdk.gateway.runs import Runs

        service, _alice, _bob = two_accounts
        assert service.runs._secret != Runs()._secret

    def test_and_a_run_is_still_reachable_by_the_identifier_its_owner_was_given(self,
                                                                               two_accounts):
        """The control. A fold that made every lookup miss would satisfy everything above."""
        service, alice, _bob = two_accounts
        mine = _a_run_by(service, alice)
        hers = service.runs.owned_by(alice.account_id, alice.client_id)
        assert hers.get(mine) is not None
        assert mine in hers
        assert hers[mine].run_id == mine
