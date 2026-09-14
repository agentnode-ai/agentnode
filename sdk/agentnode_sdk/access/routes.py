"""Every address this gateway answers on, and which of them still decide anything themselves.

The contract's own operations live under `/v1/op/` and all end in `dispatch`. The gateway also
answers on older addresses that predate the contract, and those are the risk Codex named when it
chose this shape: a door that still decides for itself is a second place where the rules live,
and the whole point of the arrangement is that there is one.

This file is the register. It is not documentation of intent -- `test_routes_register.py` reads it
and compares it against the handler's actual source, so a route that quietly starts or stops
deciding for itself makes the register wrong and the test red. A list that cannot go stale is
worth having; a list that can is worse than none, because it is believed.

## Why some of them are not going through the dispatcher

Two of them cannot, and saying so is not an excuse:

`/v1/hello` and `/v1/pair` happen BEFORE anybody has a credential. The dispatcher's first act is
to establish who is asking, and these are how somebody comes to be anybody. A pre-authentication
route is not a bypass of authentication; it is the thing authentication is built out of.

The rest are migrations that have not happened yet, each with a named reason and a named cost.
"""
from __future__ import annotations

from dataclasses import dataclass

#: A route that IS the contract: declared operations, addressed by name.
THROUGH_THE_DISPATCHER = "through_the_dispatcher"
#: An older address, kept for the clients that use it, that translates and decides nothing. It
#: reads the shape those clients send, hands the request to the dispatcher, and renders the
#: answer in the envelope they have always read.
TRANSLATES = "translates_to_the_dispatcher"
#: A route that runs before anybody has a credential. Cannot go through the dispatcher by nature.
BEFORE_ANYONE_IS_ANYBODY = "before_anyone_is_anybody"
#: A route that hands back a file and makes no decision at all.
SERVES_A_PAGE = "serves_a_page_and_decides_nothing"
#: A route that still decides something for itself. Every one of these is a second place the
#: rules live. The number is currently zero and the test below is what keeps it there.
STILL_DECIDES_FOR_ITSELF = "still_decides_for_itself"


@dataclass(frozen=True)
class Route:
    path: str
    kind: str
    why: str
    #: For a translator: what its clients read, and therefore what the translation must preserve.
    keeps: str = ""


REGISTER = (
    Route("/v1/op/", THROUGH_THE_DISPATCHER,
          "The contract. Every operation, every door, one decision point."),
    Route("/v1/openapi.json", THROUGH_THE_DISPATCHER,
          "The schema, read-only and deliberately unauthenticated: a client cannot write the "
          "request that gets it a credential without first knowing the shape of one."),
    Route("/v1/mcp", THROUGH_THE_DISPATCHER,
          "The MCP door. Same authentication, same dispatcher, different vocabulary."),

    Route("/console/confirm", BEFORE_ANYONE_IS_ANYBODY,
          "What a reloaded page asks for: a fresh confirmation value for the session whose "
          "cookie it already holds. Listed here because it answers before a page has anything "
          "in memory; it gives nothing to somebody who does not already have the cookie, and "
          "SameSite is what stops another site asking on their behalf."),
    Route("/console/setup", BEFORE_ANYONE_IS_ANYBODY,
          "Collecting a setup file. Not anonymous in fact -- it needs a signed-in session and "
          "that session's confirmation value -- but it is listed here rather than as a "
          "dispatcher route because it answers with a FILE rather than with an operation's "
          "result, and because it is the one place a device credential is ever written out. It "
          "is a form POST so the ticket never reaches an address bar, a history or a referrer."),
    Route("/console", SERVES_A_PAGE,
          "The page a person uses. It reads one file off disk and writes it back -- no token, "
          "no state, nothing behind it. Everything the page then does, it does by calling the "
          "contract like any other client."),
    Route("/console/app.js", SERVES_A_PAGE,
          "The page's code, in a file rather than inline. That is what lets the content "
          "security policy say script-src 'self' and mean it: a policy that has to allow "
          "inline code allows ANY inline code, which is most of what an injection wants."),
    Route("/console/app.css", SERVES_A_PAGE,
          "The page's styling, in a file for the same reason."),

    Route("/v1/hello", BEFORE_ANYONE_IS_ANYBODY,
          "What a client reads before it has paired, to learn which gateway it has reached and "
          "whether that gateway is ready. There is nobody to authenticate yet."),
    Route("/v1/session", BEFORE_ANYONE_IS_ANYBODY,
          "Redeeming an invitation as a BROWSER. The same single-use claim as pairing, and the "
          "credential never leaves this gateway: what goes back is a session identifier in a "
          "cookie the page's own scripts cannot read, so there is no durable bearer token in "
          "the browser to steal because none was ever sent there."),
    Route("/v1/pair", BEFORE_ANYONE_IS_ANYBODY,
          "Redeeming an invitation: how somebody comes to have a credential, so it cannot "
          "require one. It has its own single-use claim, its own expiry and its own attempt "
          "budget, none of which the dispatcher would add."),

    Route("/v1/jobs", TRANSLATES,
          "The older submit. Parses with the wire format's own reader -- the parser owns the "
          "protocol version and the shape of every field -- then hands everything to the "
          "dispatcher as claims to be checked.",
          "A signed request and a signed answer, and the whole run record rather than the "
          "narrow one. Its clients verify the binding, which is not decoration: an outcome "
          "could otherwise be changed in transit and the binding recomputed over it."),
    Route("/v1/jobs/<run>", TRANSLATES,
          "The older status. Renders the whole signed record; the contract's own `status` "
          "returns a narrower shape on purpose.",
          "The full record, signed. A run this caller may not see is answered exactly as it "
          "always was -- four words and nothing else, because telling a stranger that a run "
          "exists but is not theirs tells them it exists."),
    Route("/v1/jobs/<run>/cancel", TRANSLATES,
          "The older cancel. Asks the dispatcher and answers at once; it holds nobody.",
          "202, meaning what it always meant. 200 is gone, because only a route that waited "
          "could say it -- so nothing changed meaning quietly, a value stopped being sent, and "
          "the protocol version says so. The waiting moved into the client."),
    Route("/v1/token/rotate", TRANSLATES,
          "Replacing a credential, onto `devices.rotate`.",
          "Its answer shape. The operation is declared for a person rather than a model, so it "
          "reaches the dispatcher and is never offered as a tool: an AI holding a device's "
          "token must not be able to mint its successor in one call."),
)


BY_PATH = {route.path: route for route in REGISTER}


def still_deciding() -> tuple:
    """The ones that are still a second place the rules live. Shrinking this is the work."""
    return tuple(r for r in REGISTER if r.kind == STILL_DECIDES_FOR_ITSELF)


def translators() -> tuple:
    """The older addresses that are kept, and decide nothing."""
    return tuple(r for r in REGISTER if r.kind == TRANSLATES)


def what_a_reader_should_know() -> str:
    """For the deployment notes and the migration guide: plain, and not reassuring."""
    outstanding = still_deciding()
    if outstanding:
        return (
            "%d of this gateway's addresses still decide for themselves rather than going "
            "through the contract's one decision point:\n%s" % (
                len(outstanding),
                "\n".join("  %-24s %s" % (r.path, r.why) for r in outstanding)))
    return (
        "Every address this gateway answers on ends in one decision point, runs before "
        "anybody has a credential, or hands back a file. The older addresses are kept and "
        "translate; what each of their clients reads, and what the translation therefore "
        "must not lose:\n%s" % (
            "\n".join("  %-24s %s" % (r.path, r.keeps) for r in translators())))
