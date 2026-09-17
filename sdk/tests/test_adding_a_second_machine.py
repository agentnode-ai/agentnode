"""A customer adds their next machine themselves, without meeting an account id.

Before this, adding a second machine meant asking the operator to run
`agentnode gateway pair --account acct-7e7f7df558f58de5`. Two things were wrong with that as a
product: a customer could not do it at all, and it made somebody type a piece of our plumbing.

What makes it safe is not a check somebody remembered to write. The invitation IS the account:
which account it joins comes from the principal that asked, and the operation declares no
parameter for it, so there is no spelling of the call that invites somebody into an account they
are not already in.
"""
from __future__ import annotations

import pytest

from agentnode_sdk.access import contract, dispatch
from agentnode_sdk.gateway import joining
from agentnode_sdk.gateway.identity import GatewayState
from agentnode_sdk.gateway.server import GatewayService
from tests.test_em3c_gateway import StandInBackend, _store_measurement
from tests.test_two_accounts import _a_customer


@pytest.fixture()
def gateway(tmp_path):
    state = GatewayState(str(tmp_path / "state"), version="test")
    service = GatewayService(state, backend=StandInBackend())
    _store_measurement(service)
    try:
        yield service
    finally:
        state.close()


def _join_with(gateway, code, name="a second machine"):
    """What the other machine does: `agentnode remote connect <address> --code <code>`."""
    return dispatch.before_anyone("pair", {"code": code, "client_name": name}, service=gateway)


class TestTheOrdinaryCase:

    def test_a_customer_invites_their_own_next_machine(self, gateway):
        alice = _a_customer(gateway, "alice's laptop")
        made = dispatch.dispatch("devices.invite", {"label": "the desktop"},
                                 alice, service=gateway)
        assert made["code"] and made["expires_at"] and made["what_to_do"]

        joined = dispatch.identify(gateway, _join_with(gateway, made["code"])["token"])
        assert joined.account_id == alice.account_id, "it joined a different customer"
        assert joined.device_id != alice.device_id

        seen = dispatch.dispatch("devices.list", {}, alice, service=gateway)["devices"]
        assert {d["device_id"] for d in seen} == {alice.device_id, joined.device_id}

    def test_and_the_person_never_meets_an_account_id(self, gateway):
        """Declared: the operation takes no account, so no screen can ask for one."""
        declared = contract.find("devices.invite")
        assert [f.name for f in declared.params] == ["label"]
        alice = _a_customer(gateway, "alice")
        made = dispatch.dispatch("devices.invite", {}, alice, service=gateway)
        assert alice.account_id not in made["code"]
        assert alice.account_id not in made["what_to_do"]

    def test_the_code_is_shown_once_and_is_not_stored(self, gateway):
        alice = _a_customer(gateway, "alice")
        made = dispatch.dispatch("devices.invite", {}, alice, service=gateway)
        written = (gateway.state.root / joining.JOINING_NAME).read_text(encoding="utf-8")
        assert made["code"] not in written, (
            "a live invitation is on disk in the clear, so a copy of this directory is a way in")
        listed = dispatch.dispatch("devices.invitations", {}, alice, service=gateway)
        assert listed["invitations"], "it does not appear in the list at all"
        assert made["code"] not in str(listed), (
            "the list can redisplay the code, which makes the list as good as the invitation")

    def test_it_works_exactly_once(self, gateway):
        alice = _a_customer(gateway, "alice")
        made = dispatch.dispatch("devices.invite", {}, alice, service=gateway)
        _join_with(gateway, made["code"])
        with pytest.raises(dispatch.Refused) as refused:
            _join_with(gateway, made["code"], name="a third machine")
        assert refused.value.refusal == "not_authenticated"

    def test_it_expires(self, gateway, monkeypatch):
        alice = _a_customer(gateway, "alice")
        made = dispatch.dispatch("devices.invite", {}, alice, service=gateway)
        gateway.joining._clock = lambda: made["expires_at"] + 1
        with pytest.raises(dispatch.Refused):
            _join_with(gateway, made["code"])

    def test_a_customer_can_take_one_back(self, gateway):
        alice = _a_customer(gateway, "alice")
        made = dispatch.dispatch("devices.invite", {}, alice, service=gateway)
        said = dispatch.dispatch("devices.uninvite", {"invitation": made["invitation"]},
                                 alice, service=gateway)
        assert said["withdrawn"] is True
        with pytest.raises(dispatch.Refused):
            _join_with(gateway, made["code"])

    def test_and_typing_it_the_way_a_person_would_still_works(self, gateway):
        """Lower case, spaces, missing dashes. A code is read aloud and typed by hand."""
        alice = _a_customer(gateway, "alice")
        made = dispatch.dispatch("devices.invite", {}, alice, service=gateway)
        sloppy = made["code"].lower().replace("-", " ")
        joined = dispatch.identify(gateway, _join_with(gateway, sloppy)["token"])
        assert joined.account_id == alice.account_id


class TestItCannotReachAnotherAccount:

    def test_one_customer_cannot_invite_into_another(self, gateway):
        alice, bob = _a_customer(gateway, "alice"), _a_customer(gateway, "bob")
        # There is no parameter for it, so the only way to try is to send one anyway.
        with pytest.raises(dispatch.Refused) as refused:
            dispatch.dispatch("devices.invite", {"account_id": bob.account_id},
                              alice, service=gateway)
        assert refused.value.refusal == "malformed"
        made = dispatch.dispatch("devices.invite", {}, alice, service=gateway)
        joined = dispatch.identify(gateway, _join_with(gateway, made["code"])["token"])
        assert joined.account_id == alice.account_id != bob.account_id

    def test_nor_see_another_accounts_invitations(self, gateway):
        alice, bob = _a_customer(gateway, "alice"), _a_customer(gateway, "bob")
        dispatch.dispatch("devices.invite", {"label": "alice's desktop"}, alice, service=gateway)
        theirs = dispatch.dispatch("devices.invitations", {}, bob, service=gateway)
        assert theirs["invitations"] == []

    def test_nor_withdraw_one(self, gateway):
        alice, bob = _a_customer(gateway, "alice"), _a_customer(gateway, "bob")
        made = dispatch.dispatch("devices.invite", {}, alice, service=gateway)
        said = dispatch.dispatch("devices.uninvite", {"invitation": made["invitation"]},
                                 bob, service=gateway)
        assert said["withdrawn"] is False, "one customer withdrew another's invitation"
        joined = dispatch.identify(gateway, _join_with(gateway, made["code"])["token"])
        assert joined.account_id == alice.account_id, "and it still works, as it should"

    def test_a_model_is_not_offered_it_and_cannot_call_it_by_name(self, gateway):
        """An AI holding a device token must not be able to invite a second device.

        That is how one compromised connection becomes two.
        """
        import json

        from agentnode_sdk.access import mcp, schemas

        alice = _a_customer(gateway, "alice")
        offered = {tool["name"] for tool in mcp.tools_for(alice)}
        for name in ("devices.invite", "devices.uninvite", "devices.invitations"):
            assert schemas.tool_name_for(name) not in offered
            answer = mcp.handle(gateway, {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                          "params": {"name": schemas.tool_name_for(name),
                                                     "arguments": {}}}, alice)
            assert "no tool called" in json.dumps(answer).lower()
        assert dispatch.dispatch("devices.invitations", {}, alice,
                                 service=gateway)["invitations"] == []


class TestItDoesNotCollideWithTheOperatorsOwnInvitation:

    def test_a_customer_inviting_does_not_consume_the_operators_outstanding_code(self, gateway):
        """The operator's is single-use and global. A customer's must not spend it."""
        alice = _a_customer(gateway, "alice")
        operators = gateway.state.start_pairing()
        dispatch.dispatch("devices.invite", {}, alice, service=gateway)
        dispatch.dispatch("devices.invite", {}, alice, service=gateway)

        new_customer = dispatch.identify(gateway, _join_with(gateway, operators,
                                                             name="somebody new")["token"])
        assert new_customer.account_id != alice.account_id, (
            "the operator's invitation was consumed or redirected by a customer's")

    def test_and_presenting_an_account_invitation_does_not_spend_the_operators(self, gateway):
        alice = _a_customer(gateway, "alice")
        operators = gateway.state.start_pairing()
        made = dispatch.dispatch("devices.invite", {}, alice, service=gateway)
        _join_with(gateway, made["code"])
        # The operator's is still there, unspent.
        new_customer = dispatch.identify(gateway, _join_with(gateway, operators,
                                                             name="somebody new")["token"])
        assert new_customer.account_id not in ("", alice.account_id)

    def test_several_customers_can_have_one_open_at_once(self, gateway):
        alice, bob = _a_customer(gateway, "alice"), _a_customer(gateway, "bob")
        hers = dispatch.dispatch("devices.invite", {}, alice, service=gateway)
        his = dispatch.dispatch("devices.invite", {}, bob, service=gateway)
        assert dispatch.identify(gateway, _join_with(gateway, hers["code"])["token"]) \
            .account_id == alice.account_id
        assert dispatch.identify(gateway, _join_with(gateway, his["code"])["token"]) \
            .account_id == bob.account_id


class TestItIsBounded:

    def test_an_account_may_not_have_unlimited_invitations_open(self, gateway):
        alice = _a_customer(gateway, "alice")
        for _ in range(joining.AT_MOST_EACH):
            dispatch.dispatch("devices.invite", {}, alice, service=gateway)
        with pytest.raises(dispatch.Refused) as refused:
            dispatch.dispatch("devices.invite", {}, alice, service=gateway)
        assert refused.value.refusal == "over_a_ceiling"
        assert refused.value.what_to_do

    def test_guessing_one_is_bounded_by_the_same_budget_as_a_pairing_code(self, gateway):
        """Both stores are bounded by the same counters. A code that can be ground at is one.

        Measured by counting how many attempts this gateway will CONSIDER at all before it stops
        considering them, and requiring that to be far below the number of codes there are. The
        first attempt at this test matched the wrong message -- "not accepting pairings right
        now" is what a gateway says when no operator invitation is live, which is not a lockout
        -- and so recorded a bound after one guess. The count is the measurement.
        """
        from agentnode_sdk.gateway.throttle import Budget

        alice = _a_customer(gateway, "alice")
        honest = dispatch.dispatch("devices.invite", {}, alice, service=gateway)["code"]

        considered = 0
        for _ in range(Budget.allowance * 4):
            try:
                _join_with(gateway, "ABCD-EFGH-JKLM")
            except dispatch.Refused as refused:
                if "wait" in refused.because.lower() or "seconds" in refused.because.lower():
                    break
            considered += 1
        assert considered <= Budget.allowance, (
            "%d guesses were considered and the budget is %d: guessing an account invitation "
            "is not bounded by it" % (considered, Budget.allowance))

        # And the budget was spent by the GUESSING, not by the guesser's luck: an honest
        # invitation presented now meets the same wall.
        with pytest.raises(dispatch.Refused) as refused:
            _join_with(gateway, honest)
        assert "wait" in refused.value.because.lower()             or "seconds" in refused.value.because.lower(), refused.value.because

    def test_an_invitation_made_by_a_device_goes_when_that_device_does(self, gateway):
        """An unspent one is a way back into the account the device was withdrawn from."""
        alice = _a_customer(gateway, "alice")
        second = dispatch.identify(gateway, _join_with(
            gateway, dispatch.dispatch("devices.invite", {}, alice,
                                       service=gateway)["code"])["token"])
        made_by_second = dispatch.dispatch("devices.invite", {}, second, service=gateway)

        dispatch.dispatch("devices.revoke", {"device_id": second.device_id},
                          alice, service=gateway)
        with pytest.raises(dispatch.Refused):
            _join_with(gateway, made_by_second["code"], name="a way back in")

    def test_and_deleting_the_customer_takes_every_one_of_theirs(self, gateway):
        from agentnode_sdk.gateway import retention

        alice = _a_customer(gateway, "alice")
        made = dispatch.dispatch("devices.invite", {}, alice, service=gateway)
        went = retention.delete_account(gateway, alice.account_id)
        assert went["invitations"] == 1
        with pytest.raises(dispatch.Refused):
            _join_with(gateway, made["code"])


class TestTheConsoleScreen:
    """Read off app.js: a person does this with buttons, not with an account id."""

    def _app(self):
        import pathlib

        from agentnode_sdk import console

        return (pathlib.Path(console.__file__).parent / "app.js").read_text(encoding="utf-8")

    def test_there_is_a_button_that_makes_one(self):
        said = self._app()
        assert 'id:"invite-device"' in said
        assert "devices.invite" in said

    def test_it_shows_the_code_and_says_it_will_not_be_shown_again(self):
        said = self._app()
        assert 'id:"invitation-code"' in said and 'id:"invitation-command"' in said
        assert "noch einmal zeigen" in said, (
            "a code shown once has to say so on the screen, not in a docstring")

    def test_and_nothing_on_the_page_asks_for_an_account(self):
        said = self._app()
        assert "account_id" not in said, (
            "the console mentions an account id, which is a piece of our plumbing a customer "
            "should never meet")

    def test_open_invitations_can_be_withdrawn_from_the_same_screen(self):
        said = self._app()
        assert "devices.uninvite" in said and "devices.invitations" in said
