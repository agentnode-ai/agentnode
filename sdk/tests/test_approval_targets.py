"""Approving in one place, for something that runs in another.

This is the arrangement the product exists for, and an earlier version of the consent binding
forbade it. Binding a single "channel" meant prepare and submit had to arrive the same way, so a
person confirming comfortably in a browser could never approve a job their AI would then run over
MCP -- which is the ordinary case, not an edge one.

The fix is to stop treating one fact as two. What the gateway OBSERVES is who approved something
and where they were when they did it. What the person CHOOSES is the target: one paired
connection, over one channel, shown to them by a name they recognise. Both are bound; only the
second is chosen; neither is ever taken from something a caller said about itself.

The result is the property that makes this usable and still safe: you confirm in a browser, and
what you confirmed is *that* AI connection running *that* job -- not an approval at large.
"""
from __future__ import annotations

import json

import pytest

from agentnode_sdk.access import contract, dispatch
from tests.test_consent_and_tools import about, disclosure_for, sandbox, submit  # noqa: F401


def a_second_connection(service, name="Claude Code on my laptop", via="mcp"):
    token = service.state.redeem_pairing(service.state.start_pairing(), client_name=name)
    return token, dispatch.identify(service, token, via=via)


def approve_for(service, approver, target, channel="mcp", **kw):
    asked = about(**kw)
    asked.update({"execution_channel": channel, "execution_device": target.client_id})
    return dispatch.dispatch("prepare", asked, approver, service=service)


@pytest.fixture()
def in_a_browser(sandbox):  # noqa: F811
    service, who = sandbox
    return service, dispatch.identify(service, who.token, via="browser")


class TestTheOrdinaryCase:

    def test_a_browser_approval_lets_the_named_connection_run_it(self, in_a_browser):
        service, person = in_a_browser
        _token, the_ai = a_second_connection(service)

        shown = approve_for(service, person, the_ai)
        assert shown["approved_by"]["channel"] == "browser"
        assert shown["will_run_as"]["channel"] == "mcp"
        assert shown["will_run_as"]["shown_as"] == "Claude Code on my laptop", (
            "a person has to be told WHICH connection they are approving")

        assert submit(service, the_ai, shown["accepted_disclosure"])["run_id"]

    def test_and_the_same_channel_case_stays_as_simple_as_it_was(self, sandbox):  # noqa: F811
        """Leaving both out means "the connection I am using", which is most callers."""
        service, who = sandbox
        assert submit(service, who, disclosure_for(service, who))["run_id"]


class TestAnApprovalIsForOneConnection:

    def test_not_another_channel_even_from_the_same_device(self, in_a_browser):
        service, person = in_a_browser
        token, the_ai = a_second_connection(service)
        shown = approve_for(service, person, the_ai)

        with pytest.raises(dispatch.Refused) as refused:
            submit(service, dispatch.identify(service, token, via="rest"),
                   shown["accepted_disclosure"])
        assert refused.value.refusal == "disclosure_required"
        assert "approval is for one connection" in refused.value.because

    def test_nor_another_device_on_the_same_channel(self, in_a_browser):
        service, person = in_a_browser
        _token, the_ai = a_second_connection(service)
        _other, somebody_else = a_second_connection(service, name="A different laptop")
        shown = approve_for(service, person, the_ai)

        with pytest.raises(dispatch.Refused) as refused:
            submit(service, somebody_else, shown["accepted_disclosure"])
        assert refused.value.refusal == "disclosure_required"

    def test_a_caller_cannot_rename_the_channel_it_arrived_on(self, in_a_browser):
        """The execution channel is a choice made at prepare and shown to a person. It is not a
        claim a submission gets to make about itself."""
        service, person = in_a_browser
        token, the_ai = a_second_connection(service)
        shown = approve_for(service, person, the_ai)

        with pytest.raises(dispatch.Refused) as refused:
            submit(service, dispatch.identify(service, token, via="rest"),
                   shown["accepted_disclosure"], execution_channel="mcp")
        # Refused for saying a thing submit does not take at all, rather than for its value.
        assert refused.value.refusal == "malformed"

    def test_a_refused_attempt_does_not_burn_the_approval(self, in_a_browser):
        """A refusal must not cost the thing being protected. Otherwise one wrong-channel
        attempt -- or one stolen approval string used from the wrong place -- makes a person
        agree to everything all over again."""
        service, person = in_a_browser
        token, the_ai = a_second_connection(service)
        shown = approve_for(service, person, the_ai)

        with pytest.raises(dispatch.Refused):
            submit(service, dispatch.identify(service, token, via="rest"),
                   shown["accepted_disclosure"])
        assert submit(service, the_ai, shown["accepted_disclosure"])["run_id"], (
            "a refused attempt consumed an approval it was never allowed to use")

    def test_and_it_is_still_only_good_once(self, in_a_browser):
        service, person = in_a_browser
        _token, the_ai = a_second_connection(service)
        shown = approve_for(service, person, the_ai)
        submit(service, the_ai, shown["accepted_disclosure"], run_id="a" * 32)
        with pytest.raises(dispatch.Refused):
            submit(service, the_ai, shown["accepted_disclosure"], run_id="b" * 32)


class TestWhoMayApproveForWhom:

    def test_a_device_that_cannot_manage_devices_may_not_approve_for_another(
            self, sandbox):  # noqa: F811
        """Nominating somebody else's connection is a device-management act, not sandbox use.
        Without this rule, anything holding a device token could have a person approve a job
        "for" a connection of its own choosing."""
        service, who = sandbox
        _token, the_ai = a_second_connection(service)
        # Built by hand to hold RUN and READ and NOT manage_devices, which is the whole point
        # of the test. The account is copied from the real principal because every principal the
        # product builds has one -- a device with no account is not a narrower caller, it is a
        # caller this gateway refuses before it looks at capabilities at all.
        just_a_job = dispatch.Principal(token=who.token, device_id=who.device_id,
                                        client_id=who.client_id, via="browser",
                                        account_id=who.account_id,
                                        capabilities=(contract.RUN, contract.READ))
        with pytest.raises(dispatch.Refused) as refused:
            approve_for(service, just_a_job, the_ai)
        assert refused.value.refusal == "not_permitted"

    def test_approving_for_a_connection_that_does_not_exist_is_refused_at_prepare(
            self, in_a_browser):
        service, person = in_a_browser
        asked = about()
        asked.update({"execution_channel": "mcp", "execution_device": "no-such-connection"})
        with pytest.raises(dispatch.Refused) as refused:
            dispatch.dispatch("prepare", asked, person, service=service)
        assert refused.value.refusal == "malformed"

    def test_a_channel_this_gateway_does_not_have_is_refused_at_prepare(self, in_a_browser):
        service, person = in_a_browser
        _token, the_ai = a_second_connection(service)
        with pytest.raises(dispatch.Refused):
            approve_for(service, person, the_ai, channel="carrier pigeon")


class TestWhenTheTargetChanges:

    def test_revoking_the_approved_connection_before_it_runs_refuses_it(self, in_a_browser):
        service, person = in_a_browser
        _token, the_ai = a_second_connection(service)
        shown = approve_for(service, person, the_ai)

        service.state.revoke_client(the_ai.client_id)
        with pytest.raises(dispatch.Refused) as refused:
            submit(service, the_ai, shown["accepted_disclosure"])
        assert refused.value.refusal == "not_authenticated"

    def test_pairing_again_is_a_different_connection_and_cannot_use_the_old_approval(
            self, in_a_browser):
        """Same name, same person, same machine -- and a different identity, so the approval
        that named the old one does not name this one."""
        service, person = in_a_browser
        _token, the_ai = a_second_connection(service)
        shown = approve_for(service, person, the_ai)

        service.state.revoke_client(the_ai.client_id)
        _again, paired_again = a_second_connection(service, name="Claude Code on my laptop")
        assert paired_again.client_id != the_ai.client_id

        with pytest.raises(dispatch.Refused) as refused:
            submit(service, paired_again, shown["accepted_disclosure"])
        assert refused.value.refusal == "disclosure_required"


class TestWhatNeverReachesADisclosure:

    def test_no_token_and_no_session_identifier(self, in_a_browser):
        """Only stable server-side identifiers. A person is shown a name; what is bound is an id
        this gateway assigned, so a disclosure can be displayed, logged and compared without
        ever carrying a credential."""
        service, person = in_a_browser
        token, the_ai = a_second_connection(service)
        shown = approve_for(service, person, the_ai)
        said = json.dumps(shown)
        assert person.token not in said
        assert token not in said

        # And nothing else credential-SHAPED either, checked over the values rather than the
        # prose: the disclosure explains in words what a token is, and a substring search for
        # "token" finds that sentence. What matters is that no VALUE in it could be presented
        # to this gateway as one.
        def every_value(thing):
            if isinstance(thing, dict):
                for v in thing.values():
                    yield from every_value(v)
            elif isinstance(thing, list):
                for v in thing:
                    yield from every_value(v)
            elif isinstance(thing, str):
                yield thing

        for value in every_value(shown):
            assert service.state.client_id_for(value) is None, (
                "a disclosure carried something this gateway would accept as a credential")
