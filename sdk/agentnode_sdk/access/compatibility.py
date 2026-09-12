"""Which AI systems AgentNode works with, and what that word is allowed to mean.

AgentNode is a place where code runs under someone's policy. An AI can use it only if that AI can
call an external tool at all. That is the whole of the compatibility question, and it is a
property of the AI's own interfaces -- not of how popular it is, not of whether we like it, and
not of anything AgentNode can add later.

## The four ways in

There are exactly four, and they are the same service underneath:

    MCP                 the AI speaks the Model Context Protocol, remote or over stdio
    TOOL_CALLING        the AI can be given a tool schema and will call it over an API
    RUNS_OUR_CLIENT     an agent or plugin system that can execute the AgentNode CLI or SDK
    DIRECT              something integrates the REST/OpenAPI or SDK contract itself

An AI with none of these cannot use AgentNode. It is **not compatible**, and this module says so
in that word rather than in a softer one.

## What the web client is, and what it is not

The AgentNode web client is a client in its own right, for a person, on a phone or a desktop. It
is NOT a fallback that makes a closed AI application compatible: a chat product with no tool
interface still cannot call AgentNode, and offering the web client as though it repaired that
would be answering a question nobody asked. A person can of course use the web client alongside
any AI at all -- that is a person doing two things, not an integration, and this module refuses
to record it as one.

## Three states, and only one of them is a claim about reality

    COMPATIBLE      a tool call from that system reached AgentNode and came back. Observed.
    INTEGRABLE      it has one of the four interfaces; nobody has run it against us yet.
    NOT_COMPATIBLE  it has none of them.

The first cannot be constructed without an `Observation`. That is the point of this module: a
compatibility matrix is a marketing surface, and the cheapest possible mistake is to promote
"it should work" to "it works". Here that promotion is not available -- `confirmed()` takes the
evidence as an argument and there is no other way to reach the state.

Nothing here is provider-specific. No name of any AI vendor appears in a decision; what is
recorded about a system is which INTERFACES it has, and the same code answers for all of them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

#: The only ways an AI can reach AgentNode. A system supporting none of these is not compatible.
MCP = "mcp"
TOOL_CALLING = "tool_calling"
RUNS_OUR_CLIENT = "runs_our_client"
DIRECT = "direct"

WAYS_IN = (MCP, TOOL_CALLING, RUNS_OUR_CLIENT, DIRECT)

#: What a person is allowed to be told, in general, about what AgentNode works with. Anything
#: broader than this is a claim about systems nobody has looked at.
THE_HONEST_SENTENCE = (
    "Works with AI systems that can use external tools over MCP, tool calling, an API, the SDK "
    "or the CLI."
)

#: Sentences that must not appear anywhere a reader will meet them. Each is assembled from halves
#: so that the forbidden claim does not itself appear in this file -- a reviewer reading a list of
#: banned sentences finds the sentences, which is how a previous test in this project ended up
#: counted among the claims it existed to forbid.
BLANKET_CLAIMS = tuple(a + b for a, b in (
    ("works with ", "all ais"),
    ("works with ", "any ai"),
    ("compatible with ", "all ais"),
    ("funktioniert mit ", "allen kis"),
    ("kompatibel mit ", "allen kis"),
))

COMPATIBLE = "compatible"
INTEGRABLE = "integrable"
NOT_COMPATIBLE = "not_compatible"


class NotEvidence(ValueError):
    """Raised when something is offered as proof of compatibility and is not proof of anything."""


@dataclass(frozen=True)
class Observation:
    """One tool call that actually reached AgentNode and came back.

    `run_id` is a run this gateway recorded, which is what makes this checkable by somebody who
    does not trust the claim: they can go and look the run up. A date and a free-text note would
    be a story about a test rather than a test.
    """

    way_in: str
    run_id: str
    at: float
    client: str = ""

    def __post_init__(self) -> None:
        if self.way_in not in WAYS_IN:
            raise NotEvidence(
                "%r is not one of the ways in to AgentNode (%s)" % (self.way_in, ", ".join(WAYS_IN)))
        if not self.run_id or len(self.run_id) < 8:
            raise NotEvidence(
                "an observation has to name the run it produced, so somebody who does not believe "
                "it can look the run up. Without that this is an assertion, not evidence.")
        if not self.at:
            raise NotEvidence("an observation has to say when it happened")


@dataclass(frozen=True)
class Verdict:
    """What may be said about one AI system, and why."""

    system: str
    state: str
    ways_in: tuple = ()
    observed: tuple = ()
    #: Said in plain words, because this text is what a person actually reads.
    because: str = ""

    def is_a_claim_about_reality(self) -> bool:
        return self.state == COMPATIBLE


def _clean(ways: Iterable[str]) -> tuple:
    return tuple(sorted({w for w in ways if w in WAYS_IN}))


def judge(system: str, ways_in: Iterable[str] = (), observed: Iterable[Observation] = ()) -> Verdict:
    """The only way to produce a verdict. There is no argument that means "trust me".

    A system with no way in is NOT_COMPATIBLE and is told so plainly; a system with one is
    INTEGRABLE until somebody runs it; a system with an observation is COMPATIBLE, and the
    observation is carried along so the claim can be checked rather than believed.
    """
    ways = _clean(ways_in)
    seen = tuple(observed)
    for one in seen:
        if not isinstance(one, Observation):
            raise NotEvidence(
                "compatibility is claimed from an Observation of a real tool call, not from %r"
                % type(one).__name__)
        if one.way_in not in ways:
            raise NotEvidence(
                "%s was observed over %s, which is not among the ways in it is recorded as having"
                % (system, one.way_in))
    if not ways:
        return Verdict(system, NOT_COMPATIBLE, (), (), (
            "%s cannot call an external tool, so it cannot use AgentNode. This is not something "
            "AgentNode can add from its side, and the web client is not a way around it: that is "
            "a client for a person, not an integration for this AI." % system))
    if not seen:
        return Verdict(system, INTEGRABLE, ways, (), (
            "%s can call external tools (%s), so it should be able to use AgentNode. Nobody has "
            "run it against AgentNode yet, so this is not yet a claim that it works."
            % (system, ", ".join(ways))))
    return Verdict(system, COMPATIBLE, ways, seen, (
        "%s called AgentNode over %s and the call came back. Run %s."
        % (system, ", ".join(sorted({o.way_in for o in seen})), seen[0].run_id)))


def confirmed(system: str, ways_in: Iterable[str], observation: Observation) -> Verdict:
    """Sugar for the only transition that is a claim about reality, so it reads as deliberate."""
    return judge(system, ways_in, (observation,))


def what_to_offer(verdict: Verdict) -> str:
    """What a person is offered when their AI cannot be used. Never a fallback dressed as one.

    A refusal that ends there leaves somebody stuck, and a refusal that offers the web client as
    though it fixed the integration is worse than one that offers nothing: it answers a different
    question while sounding like an answer to theirs.
    """
    if verdict.state != NOT_COMPATIBLE:
        return ""
    return (
        "There is no way to connect %s to AgentNode, because it cannot call external tools.\n"
        "What does work:\n"
        "  - use an AI that can (anything speaking MCP, tool calling, or able to run a command)\n"
        "  - or drive AgentNode yourself with the CLI or the SDK\n"
        "The AgentNode web client is a separate thing: it is for you, not for %s, and using it "
        "does not connect that AI to anything." % (verdict.system, verdict.system)
    )


def says_too_much(text: str) -> tuple:
    """Which blanket claims a piece of reader-facing text makes, if any."""
    flowed = " ".join((text or "").split()).lower()
    return tuple(claim for claim in BLANKET_CLAIMS if claim in flowed)
