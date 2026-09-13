"""Two things a model must not be able to do: approve on your behalf, and extend its own reach.

They look unrelated and they are the same mistake twice. Both are cases where the party being
protected against is handed the means of granting itself the permission.

**The consent gate.** `prepare` shows a person what would happen and hands back a proof that it
was shown. `submit` spends it. That only means something if the proof is bound to the job it
described -- otherwise a caller can have one job approved and run another, and the check passes
while establishing nothing. It used to do exactly that, and every test in the suite passed while
preparing for a made-up digest and submitting real code.

**The tool list.** An operation reaching the dispatcher and an operation being offered to a model
are different questions. A model handed a device token has every incentive to extend its own
access, remove what could stop it, or raise its own limits -- and no way to be asked whether it
should. So some operations are a person's business, and that has to be enforced against the
generated schemas rather than asserted in a docstring.
"""
from __future__ import annotations

import base64
import hashlib
import time

import pytest

from agentnode_sdk.access import contract, dispatch, schemas
from agentnode_sdk.gateway.identity import GatewayState
from agentnode_sdk.gateway.server import GatewayService
from tests.test_em3c_gateway import StandInBackend, _store_measurement

CODE = b"print('hello')"
OTHER = b"print('something else entirely')"


@pytest.fixture()
def sandbox(tmp_path):
    state = GatewayState(str(tmp_path / "state"), version="test")
    service = GatewayService(state, backend=StandInBackend())
    _store_measurement(service)
    token = state.redeem_pairing(state.start_pairing(), client_name="a laptop")
    try:
        yield service, dispatch.identify(service, token)
    finally:
        service.close()
        state.close()


def about(code=CODE, command=None, wall_clock_s=30, network=None):
    asked = {"command": command or ["python", "-c", code.decode()],
             "artifact_sha256": hashlib.sha256(code).hexdigest(),
             "artifact_bytes": len(code), "wall_clock_s": wall_clock_s}
    if network:
        asked["network"] = network
    return asked


def disclosure_for(service, who, **kw):
    return dispatch.dispatch("prepare", about(**kw), who, service=service)["accepted_disclosure"]


def submit(service, who, proof, code=CODE, command=None, wall_clock_s=30, run_id="a" * 32,
           **extra):
    asked = {"run_id": run_id, "artifact": base64.b64encode(code).decode("ascii"),
             "command": command or ["python", "-c", code.decode()],
             "wall_clock_s": wall_clock_s, "accepted_disclosure": proof}
    asked.update(extra)
    return dispatch.dispatch("submit", asked, who, service=service)


class TestTheConsentIsForTheJobThatWasDescribed:

    def test_the_ordinary_case_still_works(self, sandbox):
        service, who = sandbox
        started = submit(service, who, disclosure_for(service, who))
        assert started["run_id"] == "a" * 32

    def test_a_disclosure_for_one_job_will_not_run_another(self, sandbox):
        """The hole, named. Approve a harmless job, run whatever you like."""
        service, who = sandbox
        approved = disclosure_for(service, who, code=CODE)
        with pytest.raises(dispatch.Refused) as refused:
            submit(service, who, approved, code=OTHER)
        assert refused.value.refusal == "disclosure_required"
        assert "not the job that was disclosed" in refused.value.because

    @pytest.mark.parametrize("changed", [
        {"command": ["python", "-c", "print('hi')", "--and-something-else"]},
        {"wall_clock_s": 600},
    ], ids=["the command", "how long it may run"])
    def test_nor_will_it_survive_a_change_to_what_would_happen(self, sandbox, changed):
        service, who = sandbox
        approved = disclosure_for(service, who)
        with pytest.raises(dispatch.Refused) as refused:
            submit(service, who, approved, **changed)
        assert refused.value.refusal == "disclosure_required"

    def test_asking_for_the_network_after_approval_is_refused(self, sandbox):
        """The one that would matter most: approved offline, run online."""
        service, who = sandbox
        approved = disclosure_for(service, who)
        with pytest.raises(dispatch.Refused) as refused:
            submit(service, who, approved, network="allowlist",
                   allowed_domains=["example.invalid"])
        assert refused.value.refusal == "disclosure_required"

    def test_one_approval_is_one_run(self, sandbox):
        service, who = sandbox
        approved = disclosure_for(service, who)
        submit(service, who, approved, run_id="a" * 32)
        with pytest.raises(dispatch.Refused) as refused:
            submit(service, who, approved, run_id="b" * 32)
        assert refused.value.refusal == "disclosure_required"
        assert "already been used" in refused.value.because

    def test_two_identical_jobs_get_two_separate_approvals(self, sandbox):
        """Without a nonce these would be the same string, so spending one would spend both --
        and a person who approved twice would have approved once."""
        service, who = sandbox
        first = disclosure_for(service, who)
        second = disclosure_for(service, who)
        assert first != second
        submit(service, who, first, run_id="a" * 32)
        submit(service, who, second, run_id="b" * 32)

    def test_an_approval_shown_to_another_device_is_not_yours(self, sandbox):
        service, who = sandbox
        approved = disclosure_for(service, who)
        other = dispatch.identify(service, service.state.redeem_pairing(
            service.state.start_pairing(), client_name="somebody else"))
        with pytest.raises(dispatch.Refused) as refused:
            submit(service, other, approved)
        assert refused.value.refusal == "disclosure_required"

    def test_an_old_approval_is_not_an_approval(self, sandbox, monkeypatch):
        service, who = sandbox
        approved = disclosure_for(service, who)
        later = time.time() + dispatch.DISCLOSURE_GOOD_FOR_SECONDS + 60
        monkeypatch.setattr(dispatch.time, "time", lambda: later)
        with pytest.raises(dispatch.Refused) as refused:
            submit(service, who, approved)
        assert refused.value.refusal == "disclosure_required"

    def test_an_invented_one_is_refused(self, sandbox):
        service, who = sandbox
        for made_up in ("", "nonsense", "a" * 32 + "." + "b" * 64):
            with pytest.raises(dispatch.Refused) as refused:
                submit(service, who, made_up)
            assert refused.value.refusal == "disclosure_required"

    def test_and_the_refusal_tells_a_client_what_to_do_rather_than_doing_it_for_them(
            self, sandbox):
        """The whole point of refusing rather than calling prepare on the caller's behalf.

        A gateway that obtains the consent it requires, on behalf of the party it is protecting
        the person from, has not obtained consent.
        """
        service, who = sandbox
        with pytest.raises(dispatch.Refused) as refused:
            submit(service, who, "")
        assert refused.value.refusal == "disclosure_required"
        assert "prepare" in refused.value.what_to_do
        assert not service.runs, "something ran without anything having been disclosed"


class TestSomeThingsAreNotOfferedToAModel:

    def tools(self):
        made = list(schemas.mcp_tools())
        for other in ("function_tools", "tool_schemas", "generic_tools"):
            renderer = getattr(schemas, other, None)
            if callable(renderer):
                made.extend(renderer())
        return made

    def test_there_are_tools_to_check(self):
        assert self.tools(), "nothing was rendered, so the check below establishes nothing"

    @pytest.mark.parametrize("concept,fragments", contract.NEVER_A_TOOL,
                             ids=[c for c, _ in contract.NEVER_A_TOOL])
    def test_no_generated_tool_offers_it(self, concept, fragments):
        offered = [str(tool.get("name", "")).lower() for tool in self.tools()]
        for fragment in fragments:
            hit = [name for name in offered if fragment in name]
            assert not hit, (
                "a model is offered %s: %s. Operations like this reach the dispatcher like "
                "everything else, and are not handed to something that cannot be asked whether "
                "it should." % (concept, hit))

    def test_every_operation_that_does_one_is_marked(self):
        """Catches the next one, not just the current ones. An operation whose own name says it
        rotates a credential and is NOT marked would be rendered as a tool."""
        for op in contract.OPERATIONS:
            for concept, fragments in contract.NEVER_A_TOOL:
                if any(f in op.name.lower().replace(".", "_") for f in fragments):
                    assert op.for_people_not_tools, (
                        "%s is %s and would be offered to a model" % (op.name, concept))

    def test_but_they_are_still_reachable_through_the_dispatcher(self):
        """Not offered is not the same as not available. A person's client can still call them,
        which is what stops this being a second place decisions are made."""
        for op in contract.OPERATIONS:
            if op.for_people_not_tools:
                assert op.name in dispatch.HANDLERS, (
                    "%s is declared but nothing carries it out" % op.name)

    def test_and_they_need_a_capability_a_job_does_not_have(self):
        for op in contract.OPERATIONS:
            if op.for_people_not_tools:
                assert op.needs == contract.MANAGE_DEVICES, (
                    "%s is a person's business but asks only for %s" % (op.name, op.needs))
