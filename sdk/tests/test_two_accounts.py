"""Two customers on one gateway, and everything one of them must not be able to reach.

These are written as a pair of REAL accounts rather than as calls to a filtering helper. A test
that asserts `devices_in(A)` returns only A's devices passes whether or not anything calls
`devices_in`, and the defect this file exists for was exactly that: the filtering existed
somewhere and the operation did not use it.

So every test here goes through `dispatch.dispatch`, as a door does, and asserts on what the
second account is ABLE to do -- not on what a helper returns.
"""
from __future__ import annotations

import json

import pytest

from agentnode_sdk.access import contract, dispatch
from agentnode_sdk.gateway import accounts as accounts_module
from agentnode_sdk.gateway.identity import GatewayState
from agentnode_sdk.gateway.server import GatewayService
from tests.test_em3c_gateway import StandInBackend, _store_measurement


@pytest.fixture()
def gateway(tmp_path):
    state = GatewayState(str(tmp_path / "state"), version="test")
    service = GatewayService(state, backend=StandInBackend())
    _store_measurement(service)
    try:
        yield service
    finally:
        state.close()


def _a_customer(gateway, name):
    """One invitation, redeemed. Every invitation that names no account makes a new customer."""
    token = gateway.state.redeem_pairing(gateway.state.start_pairing(), client_name=name)
    return dispatch.identify(gateway, token)


def _their_second_machine(gateway, whose):
    """An invitation issued FOR an existing account, which is how a customer adds a device."""
    code = gateway.state.start_pairing(for_account=whose.account_id)
    token = gateway.state.redeem_pairing(code, client_name="a second machine")
    return dispatch.identify(gateway, token)


class TestEveryPrincipalBelongsToExactlyOneAccount:

    def test_two_invitations_make_two_customers(self, gateway):
        alice, bob = _a_customer(gateway, "alice"), _a_customer(gateway, "bob")
        assert alice.account_id and bob.account_id
        assert alice.account_id != bob.account_id, (
            "redeeming a separate invitation has to produce a separate customer, or the first "
            "person on a gateway silently owns everybody who joins after them")

    def test_an_invitation_for_an_account_joins_it(self, gateway):
        alice = _a_customer(gateway, "alice")
        second = _their_second_machine(gateway, alice)
        assert second.account_id == alice.account_id
        assert second.device_id != alice.device_id, "a second device is not the same device"

    def test_the_redeemer_cannot_choose_which_account_to_join(self, gateway):
        """The account comes from the INVITATION. Nothing a caller sends can name one."""
        alice = _a_customer(gateway, "alice")
        # The bootstrap operation is what an unpaired stranger reaches. Every parameter it
        # accepts is declared here; an account is not one of them, and there is no spelling of
        # this call that would put a new device into somebody else's account.
        answer = dispatch.before_anyone(
            "pair", {"code": gateway.state.start_pairing(), "client_name": "a stranger",
                     "account_id": alice.account_id},
            service=gateway)
        joined = dispatch.identify(gateway, answer["token"])
        assert joined.account_id != alice.account_id

    def test_a_device_from_before_accounts_is_its_own_account(self, gateway):
        """The restrictive reading of an upgrade, asserted rather than assumed."""
        alice = _a_customer(gateway, "alice")
        bob = _a_customer(gateway, "bob")
        # What the token file looked like before this existed: no account against either.
        raw = json.loads(gateway.state._read_private("tokens.json"))
        for entry in raw.values():
            entry.pop("account_id", None)
        gateway.state._write_private("tokens.json", json.dumps(raw))

        one = gateway.state.account_of_client(alice.device_id)
        two = gateway.state.account_of_client(bob.device_id)
        assert one and two and one != two, (
            "two devices that predate accounts must not be put into one shared account by an "
            "upgrade -- that would CREATE the cross-customer visibility accounts remove")
        assert one.startswith(accounts_module.SOLO_PREFIX)

    def test_an_account_id_that_is_not_ours_is_refused_rather_than_stored(self, gateway):
        from agentnode_sdk.gateway.identity import PairingError

        with pytest.raises(PairingError):
            gateway.state.start_pairing(for_account="../../etc/passwd")
        with pytest.raises(PairingError):
            gateway.state.redeem_for_connection("an AI", account_id="acct-not-a-digest")


class TestNoCrossAccountRead:

    def test_a_device_list_is_the_askers_own(self, gateway):
        alice, bob = _a_customer(gateway, "alice"), _a_customer(gateway, "bob")
        _their_second_machine(gateway, alice)

        seen = dispatch.dispatch("devices.list", {}, bob, service=gateway)["devices"]
        theirs = {d["device_id"] for d in seen}
        assert theirs == {bob.device_id}, (
            "this returned everything the gateway held, which on a two-customer gateway is a "
            "customer list handed to whoever asks")

        mine = dispatch.dispatch("devices.list", {}, alice, service=gateway)["devices"]
        assert {d["device_id"] for d in mine} == {
            d.get("client_id") for d in gateway.state.devices_in(alice.account_id)}
        assert len(mine) == 2, "a customer does see their own second machine"

    def test_another_accounts_run_does_not_exist(self, gateway):
        alice, bob = _a_customer(gateway, "alice"), _a_customer(gateway, "bob")
        run = _a_run_by(gateway, alice)

        for operation in ("status", "result", "cancel"):
            with pytest.raises(dispatch.Refused) as refused:
                dispatch.dispatch(operation, {"run_id": run}, bob, service=gateway)
            assert refused.value.refusal == "no_such_run", (
                "%s told another account something about a run that is not theirs" % operation)

    def test_and_the_answer_is_shaped_like_the_one_a_stranger_gets(self, gateway):
        alice, bob = _a_customer(gateway, "alice"), _a_customer(gateway, "bob")
        run = _a_run_by(gateway, alice)
        with pytest.raises(dispatch.Refused) as about_theirs:
            dispatch.dispatch("status", {"run_id": run}, bob, service=gateway)
        with pytest.raises(dispatch.Refused) as about_nothing:
            dispatch.dispatch("status", {"run_id": "f" * 32}, bob, service=gateway)
        assert about_theirs.value.refusal == about_nothing.value.refusal
        # Identical once each is stripped of the id the CALLER supplied. Echoing back what
        # somebody just sent tells them nothing; saying anything else about it would.
        assert (about_theirs.value.because.replace(run, "?")
                == about_nothing.value.because.replace("f" * 32, "?"))
        assert about_theirs.value.what_to_do == about_nothing.value.what_to_do

    def test_usage_counts_only_the_asker(self, gateway):
        alice, bob = _a_customer(gateway, "alice"), _a_customer(gateway, "bob")
        _a_run_by(gateway, alice)
        _a_run_by(gateway, alice)
        theirs = dispatch.dispatch("usage", {}, bob, service=gateway)
        assert theirs["runs"] == 0, "one account's use appeared in another's figures"

    def test_a_session_of_another_account_is_not_listed(self, gateway):
        alice, bob = _a_customer(gateway, "alice"), _a_customer(gateway, "bob")
        session, _csrf = gateway.sessions.open(alice.device_id, label="alice's browser")
        listed = dispatch.dispatch("sessions.list", {}, bob, service=gateway)["sessions"]
        assert listed == []
        assert gateway.sessions.whose(session) is not None, "alice's session is still there"

    def test_a_challenge_of_another_account_does_not_exist(self, gateway):
        alice, bob = _a_customer(gateway, "alice"), _a_customer(gateway, "bob")
        begun = dispatch.dispatch(
            "connections.enrol", {"way_in": contract.MCP, "label": "alice's AI"},
            alice, service=gateway)
        with pytest.raises(dispatch.Refused) as refused:
            dispatch.dispatch("connections.check", {"challenge": begun["challenge"]},
                              bob, service=gateway)
        assert refused.value.refusal == "no_such_run"


class TestNoCrossAccountWriteOrWithdrawal:

    def test_one_account_cannot_withdraw_anothers_device(self, gateway):
        alice, bob = _a_customer(gateway, "alice"), _a_customer(gateway, "bob")
        answer = dispatch.dispatch("devices.revoke", {"device_id": alice.device_id},
                                   bob, service=gateway)
        assert answer["withdrawn"] is False
        assert dispatch.identify(gateway, alice.token).authenticated, (
            "a device belonging to another customer was withdrawn by somebody who is not "
            "its owner -- this is the defect the account scoping exists for")

    def test_nor_end_its_sessions_as_a_side_effect_of_trying(self, gateway):
        alice, bob = _a_customer(gateway, "alice"), _a_customer(gateway, "bob")
        session, _csrf = gateway.sessions.open(alice.device_id, label="alice's browser")
        dispatch.dispatch("devices.revoke", {"device_id": alice.device_id}, bob, service=gateway)
        assert gateway.sessions.whose(session) is not None, (
            "the withdrawal was refused at the end and everything before it still happened")

    def test_nor_stop_its_runs_as_a_side_effect_of_trying(self, gateway):
        alice, bob = _a_customer(gateway, "alice"), _a_customer(gateway, "bob")
        run = _a_run_by(gateway, alice)
        answer = dispatch.dispatch("devices.revoke", {"device_id": alice.device_id},
                                   bob, service=gateway)
        assert answer["runs_stopping"] == []
        assert not gateway.runs[run].cancel_requested.is_set()

    def test_nor_take_its_unspent_enrolment(self, gateway):
        alice, bob = _a_customer(gateway, "alice"), _a_customer(gateway, "bob")
        begun = dispatch.dispatch(
            "connections.enrol", {"way_in": contract.MCP, "label": "alice's AI"},
            alice, service=gateway)
        dispatch.dispatch("devices.revoke", {"device_id": alice.device_id}, bob, service=gateway)
        still = gateway.connections.about(begun["challenge"])
        assert still["account"] == alice.account_id

    def test_and_the_credential_layer_refuses_on_its_own(self, gateway):
        """The second layer, exercised where it actually sits.

        `_devices_revoke` returns before it ever reaches `revoke_client`, so nothing driven
        through the dispatcher can show this layer working -- a counter-check that removed it
        and ran the dispatcher test came back GREEN, which is how it was found. It is here for
        every OTHER caller of the state object: an operator command, a future path, anything
        that holds the state rather than a principal. A layer whose only evidence is the layer
        above it is not a layer.
        """
        alice, bob = _a_customer(gateway, "alice"), _a_customer(gateway, "bob")
        assert gateway.state.revoke_client(alice.device_id,
                                           within_account=bob.account_id) is False
        assert dispatch.identify(gateway, alice.token).authenticated, (
            "the state object withdrew a device belonging to another account")
        # And it is not simply refusing everything, which would pass the line above.
        assert gateway.state.revoke_client(alice.device_id,
                                           within_account=alice.account_id) is True
        assert not dispatch.identify(gateway, alice.token).authenticated

    def test_and_the_operator_is_not_bound_by_it(self, gateway):
        """Naming no account is the operator's case, and it still works.

        `agentnode gateway revoke` is run by whoever can log in to the machine. Making the
        account mandatory here would mean the operator could not withdraw a device without
        first working out whose it was.
        """
        alice = _a_customer(gateway, "alice")
        assert gateway.state.revoke_client(alice.device_id) is True

    def test_a_customer_can_withdraw_their_own_second_machine(self, gateway):
        """The counterpart. A scoping that refused everything would pass every test above."""
        alice = _a_customer(gateway, "alice")
        second = _their_second_machine(gateway, alice)
        answer = dispatch.dispatch("devices.revoke", {"device_id": second.device_id},
                                   alice, service=gateway)
        assert answer["withdrawn"] is True
        assert not dispatch.identify(gateway, second.token).authenticated


class TestTheOperatorIsNotAnAccount:

    def test_no_account_capability_reaches_a_suspension(self, gateway):
        alice = _a_customer(gateway, "alice")
        bob = _a_customer(gateway, "bob")
        declared = {op.name for op in contract.OPERATIONS}
        assert not {name for name in declared
                    if "suspend" in name or "restore" in name or name.startswith("accounts.")}, (
            "suspension is an operator action. Declaring it as a contract operation would make "
            "it reachable by whoever holds a capability, which is a customer")
        gateway.state.accounts.suspend(bob.account_id, "a test", by="the operator")
        # And a customer cannot undo it by any operation this contract has.
        with pytest.raises(dispatch.Refused) as refused:
            _a_run_by(gateway, bob)
        assert refused.value.refusal == "gateway_stopped"
        assert "a test" in refused.value.because
        assert dispatch.dispatch("usage", {}, bob, service=gateway)["runs"] == 0, (
            "a suspended customer must still be able to read what they did")
        # Nobody else is affected.
        _a_run_by(gateway, alice)

    def test_a_suspension_has_to_say_why(self, gateway):
        alice = _a_customer(gateway, "alice")
        with pytest.raises(ValueError):
            gateway.state.accounts.suspend(alice.account_id, "   ")

    def test_an_unreadable_accounts_record_is_not_good_standing(self, gateway):
        alice = _a_customer(gateway, "alice")
        gateway.state._write_private(accounts_module.ACCOUNTS_NAME, "{ this is not json")
        with pytest.raises(dispatch.Refused) as refused:
            _a_run_by(gateway, alice)
        assert refused.value.refusal == "gateway_stopped"
        assert refused.value.what_to_do


class TestNothingAModelCanCallManagesTenancy:

    def test_the_tool_surface_carries_no_account_or_access_management(self, gateway):
        from agentnode_sdk.access import mcp, schemas

        alice = _a_customer(gateway, "alice")
        for tool in mcp.tools_for(alice):
            op = next(o for o in contract.OPERATIONS
                      if schemas.tool_name_for(o.name) == tool["name"])
            assert op.audience == contract.TOOL
            assert op.risk not in contract.NEVER_FOR_A_MODEL

    def test_and_naming_one_exactly_right_does_not_carry_it_out(self, gateway):
        """Where a name becomes an operation, not only where the list is built."""
        from agentnode_sdk.access import mcp, schemas

        alice = _a_customer(gateway, "alice")
        bob = _a_customer(gateway, "bob")
        for name in ("devices.revoke", "devices.rotate", "sessions.end", "connections.enrol"):
            answer = mcp.handle(
                gateway,
                {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                 "params": {"name": schemas.tool_name_for(name),
                            "arguments": {"device_id": alice.device_id}}},
                bob)
            said = json.dumps(answer)
            assert "no tool called" in said.lower() or '"isError": true' in said, said[:200]
        assert dispatch.identify(gateway, alice.token).authenticated


class TestCountersAreNotSharedByAccident:

    def test_an_account_ceiling_bounds_the_customer_and_not_one_credential(self, gateway):
        from agentnode_sdk.gateway.allowance import Allowance, write_allowance

        alice = _a_customer(gateway, "alice")
        second = _their_second_machine(gateway, alice)
        bob = _a_customer(gateway, "bob")
        write_allowance(gateway.state.root, Allowance(account_runs_per_window=1))

        _a_run_by(gateway, alice)
        # The SECOND MACHINE is a different credential with its own device counter, and it is
        # over the account's ceiling. Pairing another device must not be a way to raise it.
        with pytest.raises(dispatch.Refused) as refused:
            _a_run_by(gateway, second)
        assert refused.value.refusal == "over_a_ceiling"
        # And it bounds this customer only.
        _a_run_by(gateway, bob)

    def test_one_accounts_use_is_not_visible_in_anothers_refusal(self, gateway):
        from agentnode_sdk.gateway.allowance import Allowance, write_allowance

        alice, bob = _a_customer(gateway, "alice"), _a_customer(gateway, "bob")
        write_allowance(gateway.state.root, Allowance(account_runs_per_window=1))
        _a_run_by(gateway, alice)
        _a_run_by(gateway, bob)
        with pytest.raises(dispatch.Refused) as refused:
            _a_run_by(gateway, bob)
        assert alice.account_id not in refused.value.because
        assert alice.device_id not in refused.value.because

    def test_the_audit_says_which_customer(self, gateway):
        alice, bob = _a_customer(gateway, "alice"), _a_customer(gateway, "bob")
        dispatch.dispatch("usage", {}, alice, service=gateway)
        dispatch.dispatch("usage", {}, bob, service=gateway)
        lines = [json.loads(line) for line in
                 (gateway.state.root / "audit.jsonl").read_text(encoding="utf-8").splitlines()
                 if line.strip()]
        for line in lines:
            assert "account" in line, (
                "an audit that cannot be read per customer cannot answer the one question an "
                "operator asks of it")
        assert {line["account"] for line in lines} >= {alice.account_id, bob.account_id}


class TestWhatTheTwoRealModelsHit:
    """Both of them, on the deployed build, within a minute of each other."""

    def test_a_submission_that_never_started_does_not_burn_the_agreement(self, gateway):
        """Claude Code: "nothing started. That refusal still used up the approval."."""
        import base64
        import hashlib
        import uuid

        who = _a_customer(gateway, "alice")
        code = b"print(1)\n"
        taken = uuid.uuid4().hex
        _a_run_by(gateway, who)                       # something to collide with
        taken = next(iter(gateway.runs))

        shown = dispatch.dispatch(
            "prepare",
            {"artifact_sha256": hashlib.sha256(code).hexdigest(), "artifact_bytes": len(code),
             "wall_clock_s": 30},
            who, service=gateway)
        sending = {"run_id": taken, "artifact": base64.b64encode(code).decode("ascii"),
                   "wall_clock_s": 30,
                   "accepted_disclosure": shown["accepted_disclosure"]}
        with pytest.raises(dispatch.Refused) as refused:
            dispatch.dispatch("submit", dict(sending), who, service=gateway)
        assert refused.value.refusal != "disclosure_required", (
            "this refusal is about the run id, not about the agreement")

        # The SAME agreement, a fresh run id. Nothing ran, so nothing was agreed away.
        sending["run_id"] = uuid.uuid4().hex
        answer = dispatch.dispatch("submit", sending, who, service=gateway)
        assert answer["state"] in ("accepted", "running", "finished")

    def test_but_an_agreement_spent_on_a_run_that_started_is_gone(self, gateway):
        """The property the single use exists for, unchanged."""
        import base64
        import hashlib
        import uuid

        who = _a_customer(gateway, "alice")
        code = b"print(1)\n"
        shown = dispatch.dispatch(
            "prepare",
            {"artifact_sha256": hashlib.sha256(code).hexdigest(), "artifact_bytes": len(code),
             "wall_clock_s": 30},
            who, service=gateway)
        sending = {"run_id": uuid.uuid4().hex,
                   "artifact": base64.b64encode(code).decode("ascii"), "wall_clock_s": 30,
                   "accepted_disclosure": shown["accepted_disclosure"]}
        dispatch.dispatch("submit", dict(sending), who, service=gateway)
        sending["run_id"] = uuid.uuid4().hex
        with pytest.raises(dispatch.Refused) as refused:
            dispatch.dispatch("submit", sending, who, service=gateway)
        assert refused.value.refusal == "disclosure_required"

    def test_the_policy_digest_a_person_approves_is_the_one_the_run_reports(self, gateway):
        """Claude Code: "approved at a check with the fingerprint 0c1c..., but the submission
        reported bb53...". It was right, and the cause was worse than a mismatch: prepare was
        digesting an INTEGER, so the field was one constant for every job ever disclosed."""
        import base64
        import hashlib
        import uuid

        who = _a_customer(gateway, "alice")
        code = b"print(1)\n"

        def prepared(**extra):
            asking = {"artifact_sha256": hashlib.sha256(code).hexdigest(),
                      "artifact_bytes": len(code), "wall_clock_s": 30}
            asking.update(extra)
            return dispatch.dispatch("prepare", asking, who, service=gateway)

        plain = prepared()
        assert plain["requested_policy_sha256"] != prepared(
            wall_clock_s=99)["requested_policy_sha256"], "it does not vary with the wall clock"
        assert plain["requested_policy_sha256"] != prepared(
            network="unrestricted")["requested_policy_sha256"], (
            "a job with no network and a job with unrestricted network digest the same -- so "
            "this field binds nothing, which is the whole of what it is for")

        shown = prepared()
        answer = dispatch.dispatch(
            "submit",
            {"run_id": uuid.uuid4().hex, "artifact": base64.b64encode(code).decode("ascii"),
             "wall_clock_s": 30, "accepted_disclosure": shown["accepted_disclosure"]},
            who, service=gateway)
        assert shown["requested_policy_sha256"] == answer["request_policy_sha256"], (
            "the number a person approves and the number the run reports are the same fact "
            "under two names, and comparing them is the obvious way to check that what was "
            "approved is what ran")

    def test_a_device_list_says_when_each_was_last_used(self, gateway):
        """Claude Code: "it says the device has never been used, even though I had just used it"."""
        who = _a_customer(gateway, "alice")
        dispatch.dispatch("usage", {}, who, service=gateway)
        listed = dispatch.dispatch("devices.list", {}, who, service=gateway)["devices"]
        assert listed and listed[0]["last_used"], (
            "a field that is always absent is a promise the product does not keep")
        assert listed[0]["last_used"] >= int(listed[0]["paired_at"])


# ------------------------------------------------------------------ getting a run to exist


def _a_run_by(gateway, who):
    """Submit something the stand-in backend will accept, through the dispatcher."""
    import base64
    import hashlib
    import uuid

    code = b"print(1)\n"
    run_id = uuid.uuid4().hex
    shown = dispatch.dispatch(
        "prepare",
        {"artifact_sha256": hashlib.sha256(code).hexdigest(), "artifact_bytes": len(code),
         "wall_clock_s": 30},
        who, service=gateway)
    answer = dispatch.dispatch(
        "submit",
        {"run_id": run_id, "artifact": base64.b64encode(code).decode("ascii"),
         "wall_clock_s": 30, "accepted_disclosure": shown["accepted_disclosure"]},
        who, service=gateway)
    return answer["run_id"]
