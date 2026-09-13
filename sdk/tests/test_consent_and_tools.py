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


class TestWhatAModelIsOfferedIsDeclared:
    """The rule, and the reason it is a declaration rather than a guess.

    This used to work by matching fragments of operation NAMES -- "rotate", "revoke", "invite".
    That is fail-open and obviously so once written down: an operation called `account.limits` or
    `credentials.refresh` matches nothing on the list and ships to every model on the next
    release. The list described the operations that happened to exist when it was written, and
    was doing duty as a rule.

    Now every operation declares who it is for, the default is `person`, and a declaration that
    contradicts itself cannot be constructed at all.
    """

    def tools(self):
        made = list(schemas.mcp_tools())
        made.extend(schemas.tool_calling_schema())
        return made

    def names(self):
        out = []
        for tool in self.tools():
            out.append(str(tool.get("name") or tool.get("function", {}).get("name", "")).lower())
        return out

    def test_there_are_tools_to_check(self):
        assert self.tools(), "nothing was rendered, so everything below establishes nothing"

    def test_the_default_is_not_a_tool(self):
        """The whole safety property in one line: forget to classify, and it is not published."""
        quiet = contract.Operation(name="something.new", since="2", needs=contract.READ,
                                   summary="Somebody added this and thought about nothing else.")
        assert quiet.audience == contract.PERSON
        assert quiet not in contract.for_a_model()

    def test_an_operation_nobody_classified_appears_in_no_tool_schema(self, monkeypatch):
        """The counter-check for it: add one, render everything, look for it."""
        quiet = contract.Operation(name="account.limits", since="2", needs=contract.READ,
                                   summary="Raise this account's ceilings.")
        monkeypatch.setattr(contract, "OPERATIONS", contract.OPERATIONS + (quiet,))
        assert "account.limits" not in [op.name for op in contract.for_a_model()]
        assert not [n for n in self.names() if "limits" in n], self.names()

    def test_renaming_it_to_something_harmless_does_not_get_it_published(self):
        """Because nothing reads the name. A declaration that says it changes access cannot also
        say a model may call it, whatever it is called."""
        with pytest.raises(ValueError) as refused:
            contract.Operation(name="housekeeping", since="2", needs=contract.MANAGE_DEVICES,
                               summary="Tidy up.", audience=contract.TOOL,
                               risk=contract.CHANGES_ACCESS)
        assert "cannot be asked whether it should" in str(refused.value)

    @pytest.mark.parametrize("bad", [
        {"audience": "anyone"},
        {"risk": "mild"},
        {"audience": ""},
    ], ids=["unknown audience", "unknown risk", "missing audience"])
    def test_a_classification_that_is_not_one_is_refused_at_the_declaration(self, bad):
        with pytest.raises(ValueError):
            contract.Operation(name="x.y", since="2", needs=contract.READ, summary="s", **bad)

    def test_and_the_generators_refuse_rather_than_quietly_render_less(self, monkeypatch):
        """A generator that skipped what it could not classify would publish a shorter list and
        look like it had succeeded."""
        broken = contract.Operation(name="x.y", since="2", needs=contract.READ, summary="s")
        object.__setattr__(broken, "audience", "nonsense")
        monkeypatch.setattr(contract, "OPERATIONS", contract.OPERATIONS + (broken,))
        for render in (schemas.mcp_tools, schemas.tool_calling_schema):
            with pytest.raises(ValueError) as refused:
                render()
            assert "not classified" in str(refused.value)

    @pytest.mark.parametrize("concept,fragments", contract.NAMES_THAT_SHOULD_NEVER_BE_TOOLS,
                             ids=[c for c, _ in contract.NAMES_THAT_SHOULD_NEVER_BE_TOOLS])
    def test_the_named_dangerous_things_are_still_not_offered(self, concept, fragments):
        """A tripwire over the declarations, not a rule. If this ever fires, something was
        declared a tool that should not have been -- the name is the hint, not the mechanism."""
        for fragment in fragments:
            hit = [n for n in self.names() if fragment in n]
            assert not hit, "a model is offered %s: %s" % (concept, hit)

    def test_rotate_and_revoke_specifically(self):
        for name in ("devices.rotate", "devices.revoke"):
            op = contract.BY_NAME[name]
            assert op.audience == contract.PERSON
            assert op.risk == contract.CHANGES_ACCESS
            assert op.confirms_with_a_person
            assert op not in contract.for_a_model()

    def test_anything_that_changes_access_or_policy_needs_the_right_permission(self):
        """REST still reaches them -- a person's client must be able to -- but only with the
        capability a person's client holds."""
        for op in contract.OPERATIONS:
            if op.risk in contract.NEVER_FOR_A_MODEL:
                assert op.needs == contract.MANAGE_DEVICES, op.name

    def test_a_device_without_that_permission_is_refused_at_the_dispatcher(self, sandbox):
        service, who = sandbox
        only_runs = dispatch.Principal(token=who.token, device_id=who.device_id,
                                       client_id=who.client_id,
                                       capabilities=(contract.RUN, contract.READ))
        with pytest.raises(dispatch.Refused) as refused:
            dispatch.dispatch("devices.revoke", {"device_id": "whoever"}, only_runs,
                              service=service)
        assert refused.value.refusal == "not_permitted"

    def test_but_they_are_still_reachable_through_the_dispatcher(self):
        """Not offered is not the same as not available. Withholding them from a model must not
        turn them into a second place decisions are made."""
        for op in contract.OPERATIONS:
            if op.audience != contract.TOOL:
                assert op.name in dispatch.HANDLERS, op.name

    def test_submit_is_the_one_that_needs_a_person_to_have_agreed(self):
        assert contract.BY_NAME["submit"].confirms_with_a_person
        assert contract.BY_NAME["submit"].audience == contract.TOOL, (
            "a model must be able to submit -- what it may not do is agree on your behalf")
