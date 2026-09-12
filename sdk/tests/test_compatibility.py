"""What "compatible" is allowed to mean.

The cheapest mistake a matrix like this can make is promoting "it should work" to "it works", so
that promotion is not available: the only state that claims reality takes the evidence as an
argument.
"""
from __future__ import annotations

import time

import pytest

from agentnode_sdk.access import compatibility as compat


def an_observation(way_in=compat.MCP, run_id="a7fd4ca2188643538f000f9c031b67a7"):
    return compat.Observation(way_in=way_in, run_id=run_id, at=time.time(), client="a test")


class TestTheThreeStates:

    def test_no_way_in_is_not_compatible_and_says_so_plainly(self):
        said = compat.judge("a chat product with no tools")
        assert said.state == compat.NOT_COMPATIBLE
        assert "cannot call an external tool" in said.because
        assert not said.is_a_claim_about_reality()

    def test_a_way_in_but_nobody_has_run_it_is_not_yet_a_claim(self):
        said = compat.judge("something that speaks MCP", [compat.MCP])
        assert said.state == compat.INTEGRABLE
        assert "not yet a claim that it works" in said.because
        assert not said.is_a_claim_about_reality()

    def test_only_an_observed_call_is_compatible(self):
        said = compat.confirmed("something that speaks MCP", [compat.MCP], an_observation())
        assert said.state == compat.COMPATIBLE
        assert said.is_a_claim_about_reality()
        assert said.observed[0].run_id in said.because, "the claim does not carry what backs it"


class TestCompatibleCannotBeReachedWithoutEvidence:
    """The property the whole module exists for."""

    def test_there_is_no_argument_that_means_trust_me(self):
        import inspect

        source = inspect.getsource(compat)
        assert "def judge(" in source
        # Every route to COMPATIBLE goes through an Observation. If a second one appears, this
        # notices: the state is returned in exactly one place.
        assert source.count("return Verdict(system, COMPATIBLE") == 1

    def test_a_story_about_a_test_is_not_evidence(self):
        with pytest.raises(compat.NotEvidence):
            compat.judge("x", [compat.MCP], ["we tried it and it was fine"])

    def test_an_observation_has_to_name_a_run_that_can_be_looked_up(self):
        with pytest.raises(compat.NotEvidence):
            compat.Observation(way_in=compat.MCP, run_id="", at=time.time())
        with pytest.raises(compat.NotEvidence):
            compat.Observation(way_in=compat.MCP, run_id="short", at=time.time())

    def test_and_it_has_to_be_over_a_way_in_that_system_actually_has(self):
        with pytest.raises(compat.NotEvidence):
            compat.judge("speaks only MCP", [compat.MCP], [an_observation(compat.TOOL_CALLING)])

    def test_and_over_a_way_in_that_exists_at_all(self):
        with pytest.raises(compat.NotEvidence):
            compat.Observation(way_in="telepathy", run_id="a" * 32, at=time.time())


class TestTheWebClientIsNotAFallbackForAnAI:
    """A chat product with no tool interface is not repaired by us having a web page."""

    def test_what_is_offered_does_not_pretend_the_web_client_connects_it(self):
        said = compat.judge("a closed chat product")
        offered = compat.what_to_offer(said)
        assert "does not connect that AI to anything" in offered
        assert "for you, not for" in offered

    def test_and_something_usable_is_offered_rather_than_only_a_refusal(self):
        offered = compat.what_to_offer(compat.judge("a closed chat product"))
        assert "an AI that can" in offered
        assert "CLI or the SDK" in offered

    def test_nothing_is_offered_when_there_is_no_problem(self):
        assert compat.what_to_offer(compat.judge("x", [compat.MCP])) == ""


class TestNoBlanketClaims:

    def test_the_honest_sentence_is_not_one(self):
        assert compat.says_too_much(compat.THE_HONEST_SENTENCE) == ()
        assert "can use external tools" in compat.THE_HONEST_SENTENCE

    def test_and_the_detector_sees_one_when_handed_one(self):
        """The counter-case: a check that cannot fire is not a check."""
        assert compat.says_too_much("AgentNode works with all AIs, everywhere.")
        assert compat.says_too_much("Funktioniert mit allen KIs.")

    def test_no_reader_facing_text_in_this_module_makes_one(self):
        import inspect

        for text in (inspect.getdoc(compat), compat.THE_HONEST_SENTENCE,
                     compat.what_to_offer(compat.judge("a closed chat product"))):
            assert compat.says_too_much(text) == (), text[:120]


class TestTheDecisionIsNotAboutWhoTheVendorIs:

    def test_two_systems_with_the_same_interfaces_get_the_same_answer(self):
        one = compat.judge("Vendor A's assistant", [compat.TOOL_CALLING])
        two = compat.judge("Vendor B's assistant", [compat.TOOL_CALLING])
        assert one.state == two.state == compat.INTEGRABLE

    def test_and_no_vendor_is_named_in_any_decision(self):
        import inspect

        source = inspect.getsource(compat)
        decisions = source.split("def judge(", 1)[1]
        for vendor in ("openai", "anthropic", "claude", "chatgpt", "gemini", "copilot"):
            assert vendor not in decisions.lower(), vendor
