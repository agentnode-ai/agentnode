"""Every refusal names one thing the refused party can actually do.

Three separate places, because a refusal passes through three and any of them can drop it:

  * the dispatcher, where a refusal is BUILT;
  * the console's table, where a refusal name becomes words a person reads;
  * the console's renderer, where those words become something on a screen.

The console defect this file exists for is the interesting one: the table was keyed by refusal
NAME and ignored what the server sent, which was fine while every refusal of a given name meant
one thing. It stopped being fine when a suspension started arriving as `gateway_stopped` carrying
the operator's own words -- a suspended customer was shown "the service has been stopped", which
is a different event, is not true of them, and leaves them nothing to do.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from agentnode_sdk import console
from agentnode_sdk.access import contract, dispatch
from agentnode_sdk.gateway import admission


def _app_js() -> str:
    return (Path(console.__file__).parent / "app.js").read_text(encoding="utf-8")


class TestARefusalCannotBeBuiltWithoutOne:

    def test_the_constructor_refuses(self):
        with pytest.raises(ValueError):
            dispatch.Refused("malformed", "something went wrong", "")
        with pytest.raises(ValueError):
            dispatch.Refused("malformed", "something went wrong", "   ")

    def test_and_so_does_admissions(self):
        with pytest.raises(ValueError):
            admission.NotAdmitted("device_rate", "too fast", "")

    def test_a_real_one_carries_both(self):
        made = dispatch.Refused("over_a_ceiling", "you are over it", "wait for the window")
        assert made.because and made.what_to_do
        assert set(made.as_answer()) == {"refused", "because", "what_to_do"}


class TestTheConsoleHasWordsForEveryRefusalTheContractDeclares:

    def test_every_declared_refusal_has_an_entry(self):
        said = _app_js()
        missing = [name for name in contract.REFUSALS
                   if not re.search(r"\b%s\s*:\s*\[" % re.escape(name), said)]
        assert not missing, (
            "the console has no words for %s, so a person meeting one is shown a generic "
            "message. Add an entry to SAYS in console/app.js." % ", ".join(missing))

    def test_and_every_entry_names_an_action(self):
        """Not a list of possibilities: exactly one thing to press."""
        said = _app_js()
        table = said.split("var SAYS = {", 1)[1].split("\n};", 1)[0]
        entries = re.findall(r"\b(\w+)\s*:\s*\[(.*?)\](?=,\s*\n|\s*\n)", table, re.DOTALL)
        assert entries, "the table could not be read, so this test is checking nothing"
        without = []
        for name, body in entries:
            action = body.rstrip().rsplit(",", 1)[-1].strip()
            if action in ('""', "''"):
                without.append(name)
        assert not without, (
            "these refusals leave a person with nothing to press: %s. 'Ask the operator' is "
            "not something a person can do from a browser." % ", ".join(without))

    def test_every_reason_admission_produces_lands_on_one_of_them(self):
        for reason, refusal in admission.AS_A_REFUSAL.items():
            assert re.search(r"\b%s\s*:\s*\[" % re.escape(refusal), _app_js()), (
                "admission can refuse with %s, which renders as %s, which the console has no "
                "words for" % (reason, refusal))


class TestTheConsoleShowsWhatTheSandboxActuallySaid:

    def test_explain_carries_the_servers_own_sentence_through(self):
        said = _app_js()
        block = said.split("function explain(e){", 1)[1].split("\n}", 1)[0]
        assert "e.because" in block and "e.what_to_do" in block, (
            "explain() ignores what the server sent, so two different events arriving under one "
            "refusal name are shown as the same thing")

    def test_and_the_renderer_puts_it_on_the_screen(self):
        said = _app_js()
        block = said.split("function problem(e, retry){", 1)[1].split("\nfunction ", 1)[0]
        assert "w.said" in block, "explain() carries it and problem() drops it again"

    def test_it_is_a_text_node_like_everything_else_on_this_page(self):
        """innerHTML is used to CLEAR a node here and never to fill one.

        Checked as an assignment rather than as the absence of a word. `host.innerHTML = ""`
        is how this page empties a container and is not injection, while a comment claiming
        the word appears nowhere is a comment that will one day be false without anything
        noticing. What matters is that nothing composed elsewhere is ever assigned to it.
        """
        filled = [line.strip() for line in _app_js().splitlines()
                  if "innerHTML" in line
                  and not line.lstrip().startswith(("//", "*", "/*"))
                  and not re.search(r'innerHTML\s*=\s*(""|\'\')\s*;', line)]
        assert not filled, (
            "innerHTML is assigned something other than the empty string: %s. Everything a "
            "person or a gateway supplies goes through textContent on this page." % filled)

    def test_a_suspension_reaches_the_person_it_is_about(self):
        """The event that made this necessary, end to end at the layer it broke."""
        because = "This account is suspended: we need to talk about last Tuesday"
        made = dispatch.Refused("gateway_stopped", because, "Reply to whoever runs this sandbox.")
        answer = made.as_answer()
        assert answer["because"] == because
        assert answer["what_to_do"]
        # And the console's table would otherwise have replaced it with a general sentence.
        said = _app_js()
        general = said.split("gateway_stopped: [", 1)[1].split("]", 1)[0]
        assert "Tuesday" not in general
        assert "e.because" in said, "so the specific sentence has to come from the answer"
