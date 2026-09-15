"""What has to happen before this product will say an AI can use it.

The tempting way to decide compatibility is to read the AI's documentation, see that it supports
tool calling, and write COMPATIBLE. That establishes nothing. Documentation is a claim about a
product, not an observation of one, and the interesting failures are exactly the ones where a tool
interface exists and does not work.

So the verdict comes from one thing only: this gateway recorded that connection carrying out the
operation it was asked to. These tests are mostly about the ways that could be faked, and each one
is a thing somebody would reach for if they wanted a green tick without a working connection.
"""
from __future__ import annotations

import base64
import hashlib
import time

import pytest

from agentnode_sdk.access import dispatch
from agentnode_sdk.access import enrolment as setting_up
from agentnode_sdk.gateway.identity import GatewayState
from agentnode_sdk.gateway.server import GatewayService
from tests.test_em3c_gateway import StandInBackend, _store_measurement

CODE = b"print('hello')"


@pytest.fixture()
def account(tmp_path):
    state = GatewayState(str(tmp_path / "state"), version="test")
    service = GatewayService(state, backend=StandInBackend())
    _store_measurement(service)
    token = state.redeem_pairing(state.start_pairing(), client_name="the browser")
    try:
        yield service, dispatch.identify(service, token, via="browser")
    finally:
        service.close()
        state.close()


def enrol(service, who, way_in="mcp", label="Claude Code on my laptop"):
    return dispatch.dispatch("connections.enrol", {"way_in": way_in, "label": label}, who,
                             service=service)


def collect(service, who, begun, label="Claude Code on my laptop"):
    """What the download route does: create the credential and bind it to the challenge.

    The account comes from the SESSION that set the connection up, exactly as it does in
    `_hand_over_a_setup_file`. Leaving it out is how every AI a person added from their own
    console became a separate customer with its own ceilings and its own bill.
    """
    token = service.state.redeem_for_connection(label, account_id=who.account_id)
    service.connections.spend_the_ticket(begun["challenge"], begun["ticket"],
                                         service.state.client_id_for(token))
    return token


def check(service, who, begun):
    return dispatch.dispatch("connections.check", {"challenge": begun["challenge"]}, who,
                             service=service)


def run_something(service, who):
    """A real job, carried out by `who`, through whatever channel `who` arrived on."""
    told = dispatch.dispatch("prepare", {
        "command": ["python", "-c", CODE.decode()],
        "artifact_sha256": hashlib.sha256(CODE).hexdigest(),
        "artifact_bytes": len(CODE), "wall_clock_s": 30}, who, service=service)
    return dispatch.dispatch("submit", {
        "run_id": hashlib.sha256(str(time.time()).encode()).hexdigest()[:32],
        "artifact": base64.b64encode(CODE).decode("ascii"),
        "command": ["python", "-c", CODE.decode()], "wall_clock_s": 30,
        "accepted_disclosure": told["accepted_disclosure"]}, who, service=service)


class TestTheOrdinaryCase:

    def test_a_connection_that_actually_runs_something_is_believed(self, account):
        service, who = account
        begun = enrol(service, who)
        assert check(service, who, begun)["satisfied"] is False

        token = collect(service, who, begun)
        the_ai = dispatch.identify(service, token, via="mcp")
        run_something(service, the_ai)

        said = check(service, who, begun)
        assert said["satisfied"] is True
        assert said["way_in"] == "mcp" and said["operation"] == "submit"

    def test_and_before_it_does_anything_the_answer_says_what_is_missing(self, account):
        service, who = account
        begun = enrol(service, who)
        assert "not been collected" in check(service, who, begun)["why"]
        collect(service, who, begun)
        assert "nothing has been recorded" in check(service, who, begun)["why"]


class TestWhatWillNotSatisfyIt:

    def test_not_a_job_the_person_ran_from_their_own_browser(self, account):
        """The one somebody would hit by accident, and the one that would quietly make every AI
        look compatible."""
        service, who = account
        begun = enrol(service, who)
        collect(service, who, begun)
        run_something(service, who)               # the browser itself, not the enrolled connection
        assert check(service, who, begun)["satisfied"] is False

    def test_not_another_connection_on_the_same_account(self, account):
        service, who = account
        begun = enrol(service, who)
        collect(service, who, begun)
        somebody_else = dispatch.identify(
            service, service.state.redeem_for_connection("a different AI",
                                                         account_id=who.account_id), via="mcp")
        run_something(service, somebody_else)
        assert check(service, who, begun)["satisfied"] is False

    def test_not_the_right_connection_over_the_wrong_channel(self, account):
        """Calling over REST does not establish that the MCP connection works."""
        service, who = account
        begun = enrol(service, who, way_in="mcp")
        token = collect(service, who, begun)
        run_something(service, dispatch.identify(service, token, via="rest"))
        assert check(service, who, begun)["satisfied"] is False
        # ... and the same connection over the channel it was set up for does satisfy it.
        run_something(service, dispatch.identify(service, token, via="mcp"))
        assert check(service, who, begun)["satisfied"] is True

    def test_not_the_right_connection_doing_something_easier(self, account):
        """Reading `capabilities` is not evidence that a job can be run."""
        service, who = account
        begun = enrol(service, who)
        token = collect(service, who, begun)
        the_ai = dispatch.identify(service, token, via="mcp")
        dispatch.dispatch("capabilities", {}, the_ai, service=service)
        assert check(service, who, begun)["satisfied"] is False

    def test_not_a_job_that_happened_before_the_challenge_existed(self, account):
        """A challenge cannot be answered by something that had already happened."""
        service, who = account
        early = dispatch.identify(
            service, service.state.redeem_for_connection("early", account_id=who.account_id),
            via="mcp")
        run_something(service, early)
        time.sleep(0.01)

        begun = enrol(service, who)
        service.connections.spend_the_ticket(begun["challenge"], begun["ticket"],
                                             early.client_id)
        assert check(service, who, begun)["satisfied"] is False

    def test_not_an_attempt_that_was_refused(self, account):
        service, who = account
        begun = enrol(service, who)
        token = collect(service, who, begun)
        the_ai = dispatch.identify(service, token, via="mcp")
        with pytest.raises(dispatch.Refused):
            dispatch.dispatch("submit", {"run_id": "x" * 32, "artifact": "", "command": [],
                                         "accepted_disclosure": ""}, the_ai, service=service)
        assert check(service, who, begun)["satisfied"] is False

    def test_and_nothing_at_all_before_the_setup_is_collected(self, account):
        service, who = account
        begun = enrol(service, who)
        # No download, so no connection exists that could have called -- whatever else ran.
        run_something(service, who)
        assert check(service, who, begun)["satisfied"] is False


class TestTheChallengeItself:

    def test_it_belongs_to_one_account(self, account, tmp_path):
        service, who = account
        begun = enrol(service, who)
        stranger = dispatch.identify(
            service, service.state.redeem_pairing(service.state.start_pairing(),
                                                  client_name="somebody else"), via="browser")
        with pytest.raises(dispatch.Refused) as refused:
            check(service, stranger, begun)
        # The same answer as one that does not exist. A challenge is not a thing to enumerate.
        assert refused.value.refusal == "no_such_run"

    def test_it_expires(self, account, monkeypatch):
        service, who = account
        begun = enrol(service, who)
        collect(service, who, begun)
        later = time.time() + setting_up.GOOD_FOR_SECONDS + 60
        monkeypatch.setattr(service.connections, "_clock", lambda: later)
        with pytest.raises(dispatch.Refused):
            check(service, who, begun)

    def test_a_made_up_one_is_not_a_challenge(self, account):
        service, who = account
        with pytest.raises(dispatch.Refused):
            dispatch.dispatch("connections.check", {"challenge": "invented"}, who,
                              service=service)

    def test_the_download_happens_once(self, account):
        service, who = account
        begun = enrol(service, who)
        collect(service, who, begun)
        with pytest.raises(setting_up.NoSuchChallenge):
            collect(service, who, begun)

    def test_a_wrong_ticket_collects_nothing(self, account):
        service, who = account
        begun = enrol(service, who)
        with pytest.raises(setting_up.NoSuchChallenge):
            service.connections.spend_the_ticket(begun["challenge"], "not-the-ticket", "device")

    def test_the_download_window_is_short(self, account, monkeypatch):
        service, who = account
        begun = enrol(service, who)
        later = time.time() + setting_up.DOWNLOAD_SECONDS + 10
        monkeypatch.setattr(service.connections, "_clock", lambda: later)
        with pytest.raises(setting_up.NoSuchChallenge):
            service.connections.spend_the_ticket(begun["challenge"], begun["ticket"], "device")

    def test_each_one_is_its_own(self, account):
        service, who = account
        first, second = enrol(service, who), enrol(service, who)
        assert first["challenge"] != second["challenge"]
        assert first["ticket"] != second["ticket"]


class TestWhatTheVerdictRestsOn:

    def test_only_this_gateways_own_record(self, account):
        """Not a callable the claimant supplies, and not anything in the request. The audit is
        read from this gateway's own file."""
        service, who = account
        begun = enrol(service, who)
        token = collect(service, who, begun)
        run_something(service, dispatch.identify(service, token, via="mcp"))

        nothing_recorded = service.connections.satisfied_by(begun["challenge"], lambda: iter(()))
        assert nothing_recorded["satisfied"] is False, (
            "the verdict did not depend on what the gateway actually recorded")

    def test_and_the_operation_that_must_be_recorded_is_running_something(self, account):
        service, who = account
        begun = enrol(service, who)
        assert service.connections.about(begun["challenge"])["operation"] == "submit"

    def test_a_connection_still_has_to_be_told_apart_from_the_account(self, account):
        service, who = account
        begun = enrol(service, who)
        token = collect(service, who, begun)
        assert service.state.client_id_for(token) != who.client_id, (
            "the enrolled connection is the account itself, so nothing distinguishes them")


class TestWithdrawingADeviceTakesBackWhatItAlreadyHad:
    """Removing a credential stops the NEXT request. That is not the whole of revocation.

    A review refused this branch for exactly that: the implementation showed the credential and
    the sessions going, and nothing reaching the authority a device had already been given. An
    unspent download ticket is the sharpest case, because collecting one MINTS A FRESH
    CREDENTIAL -- so a withdrawal that left one standing is a way to walk straight back in.
    """

    def test_an_unspent_download_is_gone(self, account):
        service, who = account
        begun = enrol(service, who)
        dispatch.dispatch("devices.revoke", {"device_id": who.client_id}, who, service=service)
        with pytest.raises(setting_up.NoSuchChallenge):
            service.connections.about(begun["challenge"])

    def test_and_so_is_one_that_named_the_withdrawn_connection_as_its_target(self, account):
        service, who = account
        begun = enrol(service, who)
        token = collect(service, who, begun)
        the_ai = dispatch.identify(service, token, via="mcp")

        dispatch.dispatch("devices.revoke", {"device_id": the_ai.client_id}, who,
                          service=service)
        with pytest.raises(setting_up.NoSuchChallenge):
            service.connections.about(begun["challenge"])

    def test_work_it_had_in_flight_is_stopped(self, account):
        """A job a withdrawn device started is its work, still executing, in a sandbox nobody may
        now ask about. Leaving it would mean a withdrawn device's code ran to completion."""
        service, who = account
        started = run_something(service, who)
        service.runs[started["run_id"]].state = "running"     # still going when it is withdrawn

        said = dispatch.dispatch("devices.revoke", {"device_id": who.client_id}, who,
                                 service=service)
        assert started["run_id"] in said["runs_stopping"]
        assert service.stopping.about(started["run_id"]) is not None

    def test_and_a_run_that_had_already_finished_is_not_disturbed(self, account):
        service, who = account
        started = run_something(service, who)
        _poll(service, started["run_id"])
        said = dispatch.dispatch("devices.revoke", {"device_id": who.client_id}, who,
                                 service=service)
        assert started["run_id"] not in said["runs_stopping"]

    def test_nor_is_somebody_elses(self, account):
        service, who = account
        # Deliberately a DIFFERENT customer: "somebody else's" is the whole point of the test,
        # and a connection in the same account would be this person's own other machine.
        elsewhere = service.state.accounts.create(name="somebody else").account_id
        other = dispatch.identify(
            service, service.state.redeem_for_connection("not mine", account_id=elsewhere),
            via="mcp")
        theirs = run_something(service, other)
        service.runs[theirs["run_id"]].state = "running"

        said = dispatch.dispatch("devices.revoke", {"device_id": who.client_id}, who,
                                 service=service)
        assert theirs["run_id"] not in said["runs_stopping"]


def _poll(service, run_id, tries=200):
    from agentnode_sdk.gateway.protocol import is_terminal

    for _ in range(tries):
        record = service.runs.get(run_id)
        if record is not None and is_terminal(record.state):
            return record
        time.sleep(0.02)
    raise AssertionError("the run never finished")
