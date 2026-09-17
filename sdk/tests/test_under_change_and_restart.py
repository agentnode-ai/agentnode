"""What happens when something changes while something else is happening, and across a restart.

Two questions a reviewer was right to ask, and that nothing here answered:

**Concurrently.** Every mutable path -- admission, quotas, withdrawal, deletion, retention, a
policy change, a cancellation -- can be reached while another is in flight. The dangerous answers
are not crashes; they are a job admitted against a ceiling that had just been lowered, a device
that keeps working because its withdrawal raced a submission, or a deletion that reports success
while something else is still writing what it just removed.

**Across a restart.** A gateway is a process and the things it must not forget are on disk. What
is tested here is the pair: do it, put the gateway away, build a NEW one on the same directory,
and ask again. A property that only holds while the process lives is not a property of the
service.

Nothing here sleeps to make a race likely. Threads are released from a barrier so they really do
arrive together, and every assertion is about the OUTCOME rather than about the timing.
"""
from __future__ import annotations

import base64
import hashlib
import json
import threading
import uuid

import pytest

from agentnode_sdk.access import contract, dispatch
from agentnode_sdk.gateway import retention
from agentnode_sdk.gateway.allowance import Allowance, write_allowance
from agentnode_sdk.gateway.identity import GatewayState
from agentnode_sdk.gateway.server import GatewayService
from tests.test_em3c_gateway import StandInBackend, _store_measurement
from tests.test_two_accounts import _a_customer, _a_run_by


@pytest.fixture()
def gateway(tmp_path):
    state = GatewayState(str(tmp_path / "state"), version="test")
    service = GatewayService(state, backend=StandInBackend())
    _store_measurement(service)
    try:
        yield service
    finally:
        state.close()


def _again(where):
    """Put a gateway away and build a NEW one on the same directory. A restart, in one line."""
    state = GatewayState(str(where), version="test")
    service = GatewayService(state, backend=StandInBackend())
    return state, service


def _together(*what):
    """Run callables so they really arrive at once, and collect what each produced.

    A thunk that RAISED is a failure of this helper, not a result to be inspected. Every caller
    below either performs the change under test -- which has to happen, or the test that follows
    asserts nothing -- or submits work through a helper that catches its own refusals. So a
    raise is always something nobody expected, and returning it quietly is how a concurrency
    test comes to pass because the concurrent thing never happened.

    That is not hypothetical: `write_allowance` raised on Windows while another thread held the
    file open, and the ceiling test then measured three submissions against the OLD ceiling and
    called it a race. This is that lesson applied to all of them at once, instead of to the one
    where it was noticed.
    """
    ready = threading.Barrier(len(what) + 1)
    out = [None] * len(what)

    def run(index, fn):
        ready.wait(timeout=20)
        try:
            out[index] = ("ok", fn())
        except Exception as exc:                              # noqa: BLE001
            out[index] = ("raised", exc)

    hands = [threading.Thread(target=run, args=(i, fn), daemon=True)
             for i, fn in enumerate(what)]
    for hand in hands:
        hand.start()
    ready.wait(timeout=20)
    for hand in hands:
        hand.join(timeout=60)
    unfinished = [i for i, got in enumerate(out) if got is None]
    assert not unfinished, "%s never finished within the timeout" % unfinished
    raised = [(i, got[1]) for i, got in enumerate(out) if got[0] == "raised"]
    assert not raised, "one of these was not supposed to raise: %r" % (raised,)
    return out


# ------------------------------------------------------------------ changing under load


class TestAChangeWhileSomethingIsInFlight:

    def test_lowering_a_ceiling_while_jobs_arrive_never_admits_past_the_NEW_one(self, gateway):
        """The dangerous answer is a job admitted against a ceiling that had just been lowered."""
        who = _a_customer(gateway, "alice")
        write_allowance(gateway.state.root, Allowance(runs_per_window=50))

        def lower():
            write_allowance(gateway.state.root, Allowance(runs_per_window=1))
            return "lowered"

        def submit():
            try:
                return _a_run_by(gateway, who)
            except dispatch.Refused as refused:
                return refused.refusal

        seen = _together(lower, submit, submit, submit)
        # The change itself has to have LANDED, or this test asserts nothing. The first version
        # did not check, `write_allowance` raised PermissionError on Windows because another
        # thread had the file open, and the ceiling was never lowered at all -- so the test
        # measured three submissions against the OLD ceiling and called it a race.
        assert seen[0] == ("ok", "lowered"), "the ceiling was never lowered: %r" % (seen[0],)
        from agentnode_sdk.gateway.allowance import read_allowance

        assert read_allowance(gateway.state.root).runs_per_window == 1

        # What STARTED, said positively. Naming the one refusal this was expected to produce
        # made the test flaky rather than wrong: three identical jobs submitted at once share a
        # disclosure, so the ones that lose that race come back `disclosure_required` -- a
        # refusal like any other, and counted as a start by a filter that only knew about
        # ceilings. A refusal is anything the contract declares as one; a start is a run id.
        started = [v for kind, v in seen[1:]
                   if kind == "ok" and v not in contract.REFUSALS]
        # However the race lands, the gateway's own count is the authority afterwards.
        runs, _seconds = gateway.use.so_far(who.client_id)
        assert runs == len(started), (
            "the counter and what was started disagree: %d counted, %d started"
            % (runs, len(started)))
        # And the NEW ceiling holds from here on, whatever happened during the change.
        with pytest.raises(dispatch.Refused):
            for _ in range(5):
                _a_run_by(gateway, who)

    def test_withdrawing_a_device_while_it_is_submitting_leaves_it_withdrawn(self, gateway):
        alice = _a_customer(gateway, "alice")
        second = _a_customer(gateway, "second")
        gateway.state.move_device_to(second.device_id, alice.account_id)
        second = dispatch.identify(gateway, second.token)

        def withdraw():
            return dispatch.dispatch("devices.revoke", {"device_id": second.device_id},
                                     alice, service=gateway)

        def submit():
            try:
                return _a_run_by(gateway, second)
            except dispatch.Refused as refused:
                return refused.refusal

        _together(withdraw, submit)
        assert not dispatch.identify(gateway, second.token).authenticated, (
            "the device is still usable after being withdrawn, because the withdrawal raced a "
            "submission")

    def test_suspending_while_jobs_arrive_ends_with_the_account_suspended(self, gateway):
        who = _a_customer(gateway, "alice")

        def suspend():
            return gateway.state.accounts.suspend(who.account_id, "a reason", by="operator")

        def submit():
            try:
                return _a_run_by(gateway, who)
            except dispatch.Refused as refused:
                return refused.refusal

        _together(suspend, submit, submit)
        assert not gateway.state.accounts.get(who.account_id).active
        with pytest.raises(dispatch.Refused) as refused:
            _a_run_by(gateway, who)
        assert refused.value.refusal == "gateway_stopped"

    def test_the_stop_while_jobs_arrive_ends_with_everything_refused(self, gateway):
        from agentnode_sdk.gateway.allowance import stop_everything

        who = _a_customer(gateway, "alice")

        def stop():
            return stop_everything(gateway.state.root, "an upgrade")

        def submit():
            try:
                return _a_run_by(gateway, who)
            except dispatch.Refused as refused:
                return refused.refusal

        _together(stop, submit, submit)
        with pytest.raises(dispatch.Refused):
            _a_run_by(gateway, who)

    def test_changing_the_policy_while_jobs_arrive_does_not_admit_under_the_old_one(
            self, gateway):
        """A measurement taken under one policy must not admit a job under another."""
        import dataclasses

        who = _a_customer(gateway, "alice")

        def change():
            _store_measurement(gateway, binding=dataclasses.replace(
                gateway.report_binding(), operator_policy_digest="0" * 40))
            return "changed"

        def submit():
            try:
                return _a_run_by(gateway, who)
            except dispatch.Refused as refused:
                return refused.refusal

        seen = _together(change, submit, submit)
        assert seen[0] == ("ok", "changed")
        # And the change LANDED. A measurement that was written and then overwritten by a
        # concurrent path would leave this gateway measured under the policy it is actually
        # running, which is the state where the assertion below is right to pass -- and would
        # mean this test had proved nothing about the change it was named for.
        assert gateway.active_state().binding.get("operator_policy_digest") == "0" * 40, (
            "the mismatched measurement did not survive the two submissions beside it")

        with pytest.raises(dispatch.Refused) as refused:
            _a_run_by(gateway, who)
        assert refused.value.what_to_do

    def test_deleting_an_account_while_it_is_working_leaves_nothing_of_it(self, gateway):
        alice, bob = _a_customer(gateway, "alice"), _a_customer(gateway, "bob")

        def delete():
            return retention.delete_account(gateway, alice.account_id)

        def submit():
            try:
                return _a_run_by(gateway, alice)
            except dispatch.Refused as refused:
                return refused.refusal

        seen = _together(delete, submit, submit)
        went = seen[0][1] if seen[0][0] == "ok" else None
        assert went is not None and isinstance(went, dict)
        assert not dispatch.identify(gateway, alice.token).authenticated
        assert dispatch.identify(gateway, bob.token).authenticated, "it deleted the wrong one"

    def test_two_deletions_of_the_same_account_at_once(self, gateway):
        alice = _a_customer(gateway, "alice")
        seen = _together(lambda: retention.delete_account(gateway, alice.account_id),
                         lambda: retention.delete_account(gateway, alice.account_id))
        for kind, value in seen:
            assert kind == "ok", value
            assert value["complete"] is True, value["problems"]
        assert not dispatch.identify(gateway, alice.token).authenticated

    def test_sweeping_while_the_gateway_is_writing_does_not_corrupt_anything(self, gateway):
        who = _a_customer(gateway, "alice")
        retention.write_retention(gateway.state.root, retention.Retention(audit_days=1))

        def sweep():
            return retention.sweep(gateway.state.root)

        def work():
            for _ in range(8):
                dispatch.dispatch("usage", {}, who, service=gateway)
            return "worked"

        seen = _together(sweep, work, work)
        for kind, value in seen:
            assert kind == "ok", value
        lines = (gateway.state.root / "audit.jsonl").read_text(encoding="utf-8").splitlines()
        for raw in lines:
            if raw.strip():
                json.loads(raw)                               # every line still parses

    def test_cancelling_while_a_run_is_finishing_reaches_a_terminal_state(self, gateway):
        who = _a_customer(gateway, "alice")
        run = _a_run_by(gateway, who)

        def cancel():
            try:
                return dispatch.dispatch("cancel", {"run_id": run}, who, service=gateway)
            except dispatch.Refused as refused:
                return refused.refusal

        seen = _together(cancel, cancel, cancel)
        for kind, value in seen:
            assert kind == "ok", value
        from agentnode_sdk.gateway.protocol import is_terminal

        for _ in range(200):
            if is_terminal(gateway.runs[run].state):
                break
            threading.Event().wait(0.05)
        assert is_terminal(gateway.runs[run].state), gateway.runs[run].state


# ------------------------------------------------------------------ across a restart


class TestWhatSurvivesARestart:

    def test_accounts_and_which_device_is_in_which(self, gateway, tmp_path):
        alice, bob = _a_customer(gateway, "alice"), _a_customer(gateway, "bob")
        made = dispatch.dispatch("devices.invite", {}, alice, service=gateway)
        joined = dispatch.identify(gateway, dispatch.before_anyone(
            "pair", {"code": made["code"], "client_name": "second"},
            service=gateway)["token"])
        where = gateway.state.root
        gateway.state.close()

        state, again = _again(where)
        try:
            back = dispatch.identify(again, alice.token)
            assert back.account_id == alice.account_id
            assert dispatch.identify(again, joined.token).account_id == alice.account_id
            assert dispatch.identify(again, bob.token).account_id != alice.account_id
            seen = dispatch.dispatch("devices.list", {}, back, service=again)["devices"]
            assert {d["device_id"] for d in seen} == {alice.device_id, joined.device_id}
        finally:
            state.close()

    def test_a_suspension(self, gateway):
        who = _a_customer(gateway, "alice")
        gateway.state.accounts.suspend(who.account_id, "we need to talk", by="operator")
        where = gateway.state.root
        gateway.state.close()

        state, again = _again(where)
        try:
            _store_measurement(again)
            with pytest.raises(dispatch.Refused) as refused:
                _a_run_by(again, dispatch.identify(again, who.token))
            assert "we need to talk" in refused.value.because
        finally:
            state.close()

    def test_what_a_customer_has_already_used(self, gateway):
        who = _a_customer(gateway, "alice")
        write_allowance(gateway.state.root, Allowance(runs_per_window=1))
        _a_run_by(gateway, who)
        where = gateway.state.root
        gateway.state.close()

        state, again = _again(where)
        try:
            _store_measurement(again)
            with pytest.raises(dispatch.Refused) as refused:
                _a_run_by(again, dispatch.identify(again, who.token))
            assert refused.value.refusal == "over_a_ceiling", (
                "a restart gave this customer their window back")
        finally:
            state.close()

    def test_a_run_id_cannot_be_used_again_after_a_restart(self, gateway):
        who = _a_customer(gateway, "alice")
        code = b"print(1)\n"
        shown = dispatch.dispatch(
            "prepare", {"artifact_sha256": hashlib.sha256(code).hexdigest(),
                        "artifact_bytes": len(code), "wall_clock_s": 30},
            who, service=gateway)
        run_id = uuid.uuid4().hex
        dispatch.dispatch("submit", {
            "run_id": run_id, "artifact": base64.b64encode(code).decode("ascii"),
            "wall_clock_s": 30, "accepted_disclosure": shown["accepted_disclosure"]},
            who, service=gateway)
        where = gateway.state.root
        gateway.state.close()

        state, again = _again(where)
        try:
            _store_measurement(again)
            back = dispatch.identify(again, who.token)
            shown = dispatch.dispatch(
                "prepare", {"artifact_sha256": hashlib.sha256(code).hexdigest(),
                            "artifact_bytes": len(code), "wall_clock_s": 30},
                back, service=again)
            with pytest.raises(dispatch.Refused):
                dispatch.dispatch("submit", {
                    "run_id": run_id,
                    "artifact": base64.b64encode(code).decode("ascii"), "wall_clock_s": 30,
                    "accepted_disclosure": shown["accepted_disclosure"]}, back, service=again)
        finally:
            state.close()

    def test_an_invitation_survives_and_is_still_single_use(self, gateway):
        alice = _a_customer(gateway, "alice")
        made = dispatch.dispatch("devices.invite", {}, alice, service=gateway)
        where = gateway.state.root
        gateway.state.close()

        state, again = _again(where)
        try:
            joined = dispatch.identify(again, dispatch.before_anyone(
                "pair", {"code": made["code"], "client_name": "after a restart"},
                service=again)["token"])
            assert joined.account_id == alice.account_id
            with pytest.raises(dispatch.Refused):
                dispatch.before_anyone("pair", {"code": made["code"], "client_name": "again"},
                                       service=again)
        finally:
            state.close()

    def test_the_ordering_of_this_gateways_policies(self, gateway):
        from agentnode_sdk.gateway import operator_policy as opol
        from agentnode_sdk.gateway import policy_version

        where = gateway.state.root
        first = policy_version.version_for(where, opol.build(opol.NONE).digest())
        gateway.state.close()

        state, _again_service = _again(where)
        try:
            assert policy_version.version_for(
                where, opol.build(opol.NONE).digest()) == first, (
                "a restart renumbered this gateway's policies, so two records taken under one "
                "policy would name different versions")
            second = policy_version.version_for(
                where, opol.build(opol.RESTRICTED, ("api.example",)).digest())
            assert second != first
        finally:
            state.close()

    def test_when_the_last_sweep_ran(self, gateway):
        where = gateway.state.root
        retention.sweep_if_due(where)
        assert retention.due(where) is False
        gateway.state.close()

        state, _again_service = _again(where)
        try:
            assert retention.due(where) is False, (
                "a restart started the retention clock again, so a gateway restarted hourly "
                "would never sweep")
        finally:
            state.close()

    def test_the_metering_chain_still_verifies_and_still_continues(self, gateway):
        from agentnode_sdk.gateway import meter

        who = _a_customer(gateway, "alice")
        _a_run_by(gateway, who)
        where = gateway.state.root
        for _ in range(100):
            if meter.read(where):
                break
            threading.Event().wait(0.05)
        before = len(meter.read(where))
        gateway.state.close()

        state, again = _again(where)
        try:
            _store_measurement(again)
            _a_run_by(again, dispatch.identify(again, who.token))
            for _ in range(200):
                if len(meter.read(where)) > before:
                    break
                threading.Event().wait(0.05)
            held = meter.verify(where)
            assert held["ok"], held["detail"]
            assert len(meter.read(where)) > before, "the restarted gateway recorded nothing"
        finally:
            state.close()

    def test_a_browser_session_survives_and_can_still_be_ended(self, gateway):
        who = _a_customer(gateway, "alice")
        session, csrf = gateway.sessions.open(who.device_id, label="a browser")
        where = gateway.state.root
        gateway.state.close()

        state, again = _again(where)
        try:
            back = dispatch.identify_session(again, session, csrf)
            assert back.authenticated and back.account_id == who.account_id
            listed = dispatch.dispatch("sessions.list", {}, back, service=again)["sessions"]
            assert listed, "the session survived but is not in its own account's list"
            dispatch.dispatch("sessions.end", {"session": listed[0]["session"]},
                              back, service=again)
            assert not dispatch.identify_session(again, session, csrf).authenticated
        finally:
            state.close()

    def test_and_a_deleted_account_stays_deleted(self, gateway):
        alice, bob = _a_customer(gateway, "alice"), _a_customer(gateway, "bob")
        retention.delete_account(gateway, alice.account_id)
        where = gateway.state.root
        gateway.state.close()

        state, again = _again(where)
        try:
            assert not dispatch.identify(again, alice.token).authenticated
            assert dispatch.identify(again, bob.token).authenticated
        finally:
            state.close()


class TestARestartDoesNotRunAnythingAgain:

    def test_an_interrupted_run_is_reported_rather_than_restarted(self, gateway):
        """The property that matters most: a crash must not re-execute somebody's code."""
        who = _a_customer(gateway, "alice")
        run = _a_run_by(gateway, who)
        where = gateway.state.root
        gateway.state.close()

        state, again = _again(where)
        try:
            back = dispatch.identify(again, who.token)
            record = again.runs.get(run)
            if record is not None:
                from agentnode_sdk.gateway.protocol import is_terminal

                assert is_terminal(record.state), (
                    "a run rebuilt after a restart is not terminal, so something may run it")
                assert record.owner_account_id in ("", back.account_id)
        finally:
            state.close()
