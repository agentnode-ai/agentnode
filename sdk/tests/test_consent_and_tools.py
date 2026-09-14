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
import json
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


#: A valid declaration, to vary one field of at a time.
WHOLE = dict(name="something.new", since="2", needs=contract.READ, summary="A new thing.",
             audience=contract.PERSON, risk=contract.READS, confirms_with_a_person=False)


def smuggled(**broken):
    """An operation that got past the constructor. Something built another way -- a loader, a
    plugin, a future refactor -- and the point of checking again downstream."""
    op = contract.Operation(**WHOLE)
    for field, value in broken.items():
        object.__setattr__(op, field, value)
    return op


def everything_refuses(monkeypatch, tmp_path, op, because=""):
    """The whole contract is rejected -- not one rendering quietly made shorter.

    Each of these is a separate way the operation could otherwise have leaked out: a schema a
    model reads, a schema a person's client reads, the capabilities answer, the gateway agreeing
    to serve at all, and the dispatcher agreeing to carry it out.
    """
    monkeypatch.setattr(contract, "OPERATIONS", contract.OPERATIONS + (op,))
    for render in (contract.describe, schemas.openapi_document, schemas.mcp_tools,
                   schemas.tool_calling_schema, schemas.every_rendering,
                   contract.check_classifications):
        with pytest.raises(contract.NotClassified) as refused:
            render()
        if because:
            assert because in str(refused.value), (render.__name__, str(refused.value))

    state = GatewayState(str(tmp_path / "wont-start"), version="test")
    try:
        with pytest.raises(contract.NotClassified):
            GatewayService(state, backend=StandInBackend())
    finally:
        state.close()


class TestClassificationIsMandatoryNotDefaulted:
    """An earlier version defaulted a missing audience to `person`.

    That kept an unclassified operation out of the tool schemas, and was described as making
    classification mandatory. It did not: it made classification OPTIONAL with a safe fallback,
    which is a weaker and different claim -- and it left the operation reachable over REST,
    classified by nobody and refused by nothing. A default standing in for a decision is a
    decision nobody made.

    Each case below must reject the WHOLE contract. A generator that quietly dropped what it
    could not classify would publish a shorter schema and exit zero, and a shorter schema that
    looks like a successful build is exactly how something ends up reachable on one transport
    and invisible on another.
    """

    def test_a_declaration_that_says_nothing_cannot_be_built_at_all(self):
        with pytest.raises(ValueError) as refused:
            contract.Operation(name="something.new", since="2", needs=contract.READ,
                               summary="Somebody added this and thought about nothing else.")
        assert "does not declare its" in str(refused.value)

    @pytest.mark.parametrize("missing", ["audience", "risk", "confirms_with_a_person"],
                             ids=["no audience", "no risk", "no human confirmation"])
    def test_nor_can_one_that_leaves_a_single_field_out(self, missing):
        shape = dict(WHOLE)
        shape.pop(missing)
        with pytest.raises(ValueError) as refused:
            contract.Operation(**shape)
        assert "does not declare its" in str(refused.value)

    def test_nor_can_one_without_a_required_permission(self):
        shape = dict(WHOLE, needs=None)
        with pytest.raises(ValueError):
            contract.Operation(**shape)

    def test_but_declaring_person_explicitly_is_perfectly_fine(self):
        """What is refused is saying nothing -- not choosing the cautious answer."""
        fine = contract.Operation(**WHOLE)
        assert fine.audience == contract.PERSON
        assert fine not in contract.for_a_model()

    # --- and the same seven, against something that got past the constructor ------------------

    def test_completely_unclassified(self, monkeypatch, tmp_path):
        everything_refuses(monkeypatch, tmp_path,
                           smuggled(audience=None, risk=None, confirms_with_a_person=None),
                           because="does not declare its")

    def test_a_missing_audience(self, monkeypatch, tmp_path):
        everything_refuses(monkeypatch, tmp_path, smuggled(audience=None), because="audience")

    def test_a_missing_risk(self, monkeypatch, tmp_path):
        everything_refuses(monkeypatch, tmp_path, smuggled(risk=None), because="risk")

    def test_a_missing_permission(self, monkeypatch, tmp_path):
        everything_refuses(monkeypatch, tmp_path, smuggled(needs=None),
                           because="required permission")

    def test_a_missing_human_confirmation(self, monkeypatch, tmp_path):
        everything_refuses(monkeypatch, tmp_path, smuggled(confirms_with_a_person=None),
                           because="whether a person")

    def test_a_contradictory_combination(self, monkeypatch, tmp_path):
        everything_refuses(monkeypatch, tmp_path,
                           smuggled(audience=contract.TOOL, risk=contract.CHANGES_ACCESS,
                                    needs=contract.MANAGE_DEVICES),
                           because="cannot be asked whether it should")

    def test_an_enum_value_nobody_declared(self, monkeypatch, tmp_path):
        everything_refuses(monkeypatch, tmp_path, smuggled(audience="everyone"),
                           because="which is not one of")

    def test_renaming_a_blocked_operation_does_not_unblock_it(self, monkeypatch, tmp_path):
        """Nothing reads the name, so calling it something harmless changes nothing."""
        with pytest.raises(ValueError):
            contract.Operation(name="housekeeping", since="2", needs=contract.MANAGE_DEVICES,
                               summary="Tidy up.", audience=contract.TOOL,
                               risk=contract.CHANGES_ACCESS, confirms_with_a_person=False)
        everything_refuses(monkeypatch, tmp_path,
                           smuggled(name="housekeeping", audience=contract.TOOL,
                                    risk=contract.CHANGES_ACCESS,
                                    needs=contract.MANAGE_DEVICES),
                           because="cannot be asked whether it should")

    def test_and_it_is_reachable_over_no_transport(self, sandbox, monkeypatch):
        """Not merely absent from the schemas -- refused by the dispatcher, which is what every
        door goes through."""
        service, who = sandbox
        monkeypatch.setattr(contract, "OPERATIONS",
                            contract.OPERATIONS + (smuggled(audience=None),))
        monkeypatch.setitem(dispatch.HANDLERS, "something.new", lambda *a, **k: {"ok": True})
        with pytest.raises(dispatch.Refused) as refused:
            dispatch.dispatch("something.new", {}, who, service=service)
        assert refused.value.refusal == "unknown_operation"


class TestWhatAModelIsOfferedIsDeclared:
    """What decides tool exposure is the declared audience. Nothing reads the name."""

    def tools(self):
        return list(schemas.mcp_tools()) + list(schemas.tool_calling_schema())

    def names(self):
        return [str(t.get("name") or t.get("function", {}).get("name", "")).lower()
                for t in self.tools()]

    def test_there_are_tools_to_check(self):
        assert self.tools(), "nothing was rendered, so everything below establishes nothing"

    @pytest.mark.parametrize("concept,fragments", contract.NAMES_THAT_SHOULD_NEVER_BE_TOOLS,
                             ids=[c for c, _ in contract.NAMES_THAT_SHOULD_NEVER_BE_TOOLS])
    def test_the_named_dangerous_things_are_not_offered(self, concept, fragments):
        """A tripwire over the declarations, not a rule -- nothing reads it at runtime. If it
        fires, something was declared a tool that should not have been."""
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
        for op in contract.OPERATIONS:
            if op.audience != contract.TOOL:
                assert op.name in dispatch.HANDLERS, op.name

    def test_submit_is_the_one_that_needs_a_person_to_have_agreed(self):
        assert contract.BY_NAME["submit"].confirms_with_a_person
        assert contract.BY_NAME["submit"].audience == contract.TOOL, (
            "a model must be able to submit -- what it may not do is agree on your behalf")


class TestWhatTheApprovalIsBoundTo:
    """Everything that would change what happens, or what a person was told about the decision.

    The list is `dispatch.BOUND_BY_THE_DISCLOSURE`, and the first test here is the one that keeps
    it honest: a field can be added to that list and quietly never reach the digest, at which
    point the list describes an intention rather than a mechanism.
    """

    def an_answer(self, service, who):
        return dispatch._what_would_happen(service, who, about())

    def test_the_disclosure_names_who_approved_it_and_which_connection_it_is_for(self, sandbox):
        service, who = sandbox
        shown = self.an_answer(service, who)
        assert shown["approved_by"]["account"] == who.client_id
        assert "channel" in shown["approved_by"]
        assert shown["will_run_as"]["device"] == who.client_id
        assert "shown_as" in shown["will_run_as"], (
            "a person has to be shown a name they recognise, not an identifier")

    @pytest.mark.parametrize("path", dispatch.BOUND_BY_THE_DISCLOSURE,
                             ids=[".".join(p) for p in dispatch.BOUND_BY_THE_DISCLOSURE])
    def test_changing_any_bound_field_changes_the_approval(self, sandbox, path):
        """A field on the list that does not reach the digest is a field nobody is bound to."""
        service, who = sandbox
        shown = self.an_answer(service, who)
        before = dispatch._what_was_disclosed(shown)

        changed = json.loads(json.dumps(shown))
        here = changed
        for step in path[:-1]:
            here = here[step]
        was = here.get(path[-1])
        here[path[-1]] = "something else" if was != "something else" else "different again"

        assert dispatch._what_was_disclosed(changed) != before, (
            "%s is listed as bound and changing it changed nothing" % ".".join(path))

    def test_an_approval_given_over_one_door_is_not_usable_from_another(self, sandbox):
        """The binding that matters once there is a web console.

        Agreeing to something while setting up, in a browser, is not agreeing to whatever calls
        the API afterwards. Same person, same device, same job -- different door, so it is a
        different approval.
        """
        service, who = sandbox
        in_a_browser = dispatch.identify(service, who.token, via="rest")
        as_a_tool = dispatch.identify(service, who.token, via="mcp")

        approved = dispatch.dispatch("prepare", about(), in_a_browser,
                                     service=service)["accepted_disclosure"]
        with pytest.raises(dispatch.Refused) as refused:
            submit(service, as_a_tool, approved)
        assert refused.value.refusal == "disclosure_required"
        # ... and it still works from the door it was actually shown through.
        assert submit(service, in_a_browser, approved)["run_id"]

    def test_it_says_which_named_secrets_would_be_released_and_never_a_value(self, sandbox):
        service, who = sandbox
        shown = self.an_answer(service, who)
        assert shown["secrets"]["names_released"] == []
        assert "never" in shown["secrets"]["values"]

    def test_and_it_says_plainly_what_it_does_not_cover(self, sandbox):
        """A silence a person reads as "none" is worse than a sentence saying it is not modelled."""
        service, who = sandbox
        said = " ".join(self.an_answer(service, who)["not_modelled"]).lower()
        for subject in ("data class", "region", "retention", "price", "effective policy"):
            assert subject in said, subject

    def test_the_policy_asked_for_and_the_one_in_force_are_both_named(self, sandbox):
        service, who = sandbox
        shown = self.an_answer(service, who)
        assert len(shown["requested_policy_sha256"]) == 64
        assert "operator_policy_sha256" in shown

    def test_the_bound_list_itself_contains_what_it_must(self):
        """The test above is parametrised OVER the list, so deleting an entry deletes its own
        check -- the counter-check for it ran nothing and said so. This is the half that cannot
        be removed by removing something: the required set, written out.
        """
        must_bind = {
            ("approved_by",),                 # who was shown it, and where
            ("will_run_as",),                 # the one connection it is an approval for
            ("runs_at",),                     # backend and where it runs
            ("transfers",),                   # artifact digest, size, command
            ("network",),                     # mode and allowlist
            ("limits",),                      # resources and ceilings
            ("requested_policy_sha256",),
            ("operator_policy_sha256",),
            ("secrets",),
            ("expected_use", "this_would_add_seconds"),
            ("good_for_seconds",),
        }
        missing = must_bind - set(dispatch.BOUND_BY_THE_DISCLOSURE)
        assert not missing, "no longer bound: %s" % sorted(missing)
