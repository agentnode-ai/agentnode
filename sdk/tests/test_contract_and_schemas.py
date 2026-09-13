"""One declaration, three renderings, and no way for a transport to grow a door of its own.

`MANAGED-ACCESS-DECISION-0001` chose the declared-contract shape and named the risk that comes
with it: the declaration is itself security-critical, so its completeness and the agreement of
what is generated from it have to be MECHANICALLY checked, not assumed. Assuming is how three
hand-maintained schemas end up allowing three different things, and a client that can ask one
door for something another refuses has found a security difference, not a documentation one.

These are the checks that risk turns into.
"""
from __future__ import annotations

import pytest

from agentnode_sdk.access import contract, schemas


class TestTheDeclarationIsWellFormed:

    def test_every_operation_needs_a_capability_that_exists(self):
        for op in contract.OPERATIONS:
            assert op.needs in contract.CAPABILITIES, op.name

    def test_every_refusal_an_operation_names_is_declared(self):
        for op in contract.OPERATIONS:
            for refusal in op.errors:
                assert refusal in contract.REFUSALS, (op.name, refusal)

    def test_an_operation_cannot_be_declared_with_an_invented_capability(self):
        with pytest.raises(ValueError):
            contract.Operation(name="x", since="1", needs="sudo", summary="")

    def test_or_with_an_invented_refusal(self):
        with pytest.raises(ValueError):
            contract.Operation(name="x", since="1", needs=contract.READ, summary="",
                               errors=("catch_fire",))

    def test_or_with_the_same_parameter_twice(self):
        twice = (contract.Field("run_id", "string", "a"), contract.Field("run_id", "string", "b"))
        with pytest.raises(ValueError):
            contract.Operation(name="x", since="1", needs=contract.READ, summary="", params=twice)

    def test_every_operation_says_when_it_arrived(self):
        """Per operation, not one number for the service: a client older than one operation can
        then tell, instead of finding out by being refused."""
        for op in contract.OPERATIONS:
            assert op.since, op.name

    def test_every_operation_can_refuse_the_things_that_apply_to_all_of_them(self):
        for op in contract.OPERATIONS:
            for always in ("not_authenticated", "not_permitted", "malformed"):
                assert always in op.errors, (op.name, always)

    def test_the_operations_the_service_promises_are_all_here(self):
        """The list the decision named, plus the device management the journey needs."""
        for wanted in ("capabilities", "prepare", "submit", "status", "result", "cancel",
                       "usage", "devices.list", "devices.revoke"):
            assert contract.find(wanted) is not None, wanted


class TestEveryRenderingSaysTheSameThing:

    def test_they_offer_exactly_the_declared_operations_and_no_others(self):
        """The renderings may not disagree -- but two of them deliberately offer LESS.

        A schema handed to a model is an offer. Some operations are a person\'s business:
        rotating a credential, withdrawing a device, making an invitation, changing the
        operator\'s policy, working the kill switch. A model given a device token has every
        incentive to do them and no way to be asked whether it should, so they are left out of
        the renderings a model reads -- while remaining reachable through the dispatcher, which
        is what stops this becoming a second place decisions are made.

        This asserts the withheld set EXACTLY, in both directions. A rendering that quietly
        started offering one of them fails; so does a rendering that quietly stopped offering
        something it should.
        """
        declared = {op.name for op in contract.OPERATIONS}
        for_people = {op.name for op in contract.OPERATIONS if op.for_people_not_tools}
        assert for_people, "nothing is withheld, so this test establishes nothing"

        every = schemas.every_rendering()
        # The HTTP surface is what a person\'s own client talks to, so it carries everything;
        # reaching it still needs the capability the operation declares.
        assert schemas.operations_named_by(every["openapi"], "openapi") == declared

        for which in ("mcp", "tool_calling"):
            offered = schemas.operations_named_by(every[which], which)
            assert offered == declared - for_people, (which, offered ^ (declared - for_people))

    def test_and_each_accepts_exactly_the_declared_parameters(self):
        document = schemas.openapi_document()
        for op in contract.OPERATIONS:
            path = schemas.NAMESPACE + op.name.replace(".", "/")
            entry = list(document["paths"][path].values())[0]
            if not op.params:
                assert "requestBody" not in entry, op.name
                continue
            shape = entry["requestBody"]["content"]["application/json"]["schema"]
            assert set(shape["properties"]) == {f.name for f in op.params}, op.name
            assert set(shape["required"]) == {f.name for f in op.params if f.required}, op.name

    def test_and_none_of_them_accepts_anything_else(self):
        """`additionalProperties: false` everywhere. A parameter nobody declared must be refused,
        not ignored -- ignored input is input somebody believes was used."""
        for tool in schemas.mcp_tools():
            assert tool["inputSchema"]["additionalProperties"] is False, tool["name"]
        for tool in schemas.tool_calling_schema():
            assert tool["function"]["parameters"]["additionalProperties"] is False
        document = schemas.openapi_document()
        for path in document["paths"].values():
            for entry in path.values():
                body = entry.get("requestBody")
                if body:
                    shape = body["content"]["application/json"]["schema"]
                    assert shape["additionalProperties"] is False, entry["operationId"]

    def test_a_new_operation_appears_in_all_three_without_anyone_editing_them(self):
        """The property that makes this worth the indirection: add one declaration, and every
        door offers it. Nobody can forget a transport, because nobody touches a transport."""
        extra = contract.Operation(
            name="weather", since="99", needs=contract.READ,
            summary="What it is like outside.",
            params=(contract.Field("where", "string", "which sky"),),
            errors=("not_authenticated", "not_permitted", "malformed"))
        original = contract.OPERATIONS
        try:
            contract.OPERATIONS = original + (extra,)
            every = schemas.every_rendering()
            for which in ("openapi", "mcp", "tool_calling"):
                assert "weather" in schemas.operations_named_by(every[which], which), which
        finally:
            contract.OPERATIONS = original
        # ... and it is gone again once the declaration is, which is the other half of the claim.
        every = schemas.every_rendering()
        for which in ("openapi", "mcp", "tool_calling"):
            assert "weather" not in schemas.operations_named_by(every[which], which), which

    def test_the_refusal_shape_is_named_and_closed(self):
        """A client must be able to tell "over a ceiling" from "malformed" without reading prose."""
        refusal = schemas.openapi_document()["components"]["schemas"]["Refusal"]
        assert set(refusal["properties"]["refused"]["enum"]) == set(contract.REFUSALS)
        assert refusal["additionalProperties"] is False
        assert "what_to_do" in refusal["properties"], (
            "a refusal with nothing to do about it leaves somebody stuck")


class TestCapabilityDiscovery:

    def test_a_caller_is_told_only_what_it_may_ask_for(self):
        read_only = {op.name for op in contract.for_capabilities([contract.READ])}
        assert "status" in read_only and "usage" in read_only
        assert "submit" not in read_only, "a read-only device was offered a way to run code"
        assert "devices.revoke" not in read_only

    def test_holding_nothing_offers_nothing(self):
        assert contract.for_capabilities([]) == ()

    def test_and_the_whole_contract_can_be_described_as_data(self):
        described = contract.describe()
        assert described["protocol"] == contract.PROTOCOL_VERSION
        assert {o["name"] for o in described["operations"]} == {op.name for op in contract.OPERATIONS}
        for operation in described["operations"]:
            assert operation["since"], operation["name"]
            assert operation["errors"], operation["name"]


class TestWhatThePreExecutionDisclosureMustCarry:
    """Item 7 of the instruction, as a property of the contract rather than of one screen.

    The same five things must come back before anything runs, whichever door was used -- so they
    are declared once, here, and every transport renders that.
    """

    def test_prepare_answers_all_five_questions(self):
        prepare = contract.find("prepare")
        returned = {f.name for f in prepare.returns}
        for must in ("runs_at", "transfers", "network", "limits", "expected_use"):
            assert must in returned, must

    def test_and_carries_what_the_arrangement_does_not_establish(self):
        """A disclosure that says where code runs without saying what that does not protect
        against is a disclosure somebody will read as a guarantee."""
        prepare = contract.find("prepare")
        assert "what_this_does_not_establish" in {f.name for f in prepare.returns}

    def test_and_submit_can_say_which_disclosure_it_was_started_against(self):
        submit = contract.find("submit")
        assert "accepted_disclosure" in {f.name for f in submit.params}
