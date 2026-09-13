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

#: A route that goes through the dispatcher: it authenticates, then carries nothing out itself.
THROUGH_THE_DISPATCHER = "through_the_dispatcher"
#: A route that runs before anybody has a credential. Cannot go through the dispatcher by nature.
BEFORE_ANYONE_IS_ANYBODY = "before_anyone_is_anybody"
#: A route that still decides for itself. Each one is a second place the rules live.
STILL_DECIDES_FOR_ITSELF = "still_decides_for_itself"


@dataclass(frozen=True)
class Route:
    path: str
    kind: str
    why: str
    #: For the ones not yet migrated: what it would cost a caller if it were migrated today.
    what_migrating_would_break: str = ""


REGISTER = (
    Route("/v1/op/", THROUGH_THE_DISPATCHER,
          "The contract. Every operation, every door, one decision point."),
    Route("/v1/openapi.json", THROUGH_THE_DISPATCHER,
          "The schema, read-only and deliberately unauthenticated: a client cannot write the "
          "request that gets it a credential without first knowing the shape of one."),
    Route("/v1/mcp", THROUGH_THE_DISPATCHER,
          "The MCP door. Same authentication, same dispatcher, different vocabulary."),

    Route("/v1/hello", BEFORE_ANYONE_IS_ANYBODY,
          "What a client reads before it has paired, to learn which gateway it has reached and "
          "whether that gateway is ready. There is nobody to authenticate yet."),
    Route("/v1/pair", BEFORE_ANYONE_IS_ANYBODY,
          "Redeeming an invitation. This is how somebody comes to have a credential, so it "
          "cannot require one. It has its own single-use claim, its own expiry and its own "
          "attempt budget, none of which the dispatcher would add."),

    Route("/v1/token/rotate", STILL_DECIDES_FOR_ITSELF,
          "Replacing a credential while keeping the identity behind it. Not in the contract "
          "because it is credential management rather than sandbox use, and an AI holding a "
          "device's token should not be able to mint its successor as one tool call.",
          "Nothing yet: no contract operation covers it, so migrating it means declaring one."),
    Route("/v1/jobs", STILL_DECIDES_FOR_ITSELF,
          "The older submit. Carries required_properties, mandatory and optional policy shapes "
          "that the contract's `submit` does not yet declare.",
          "Migrating it today would silently NARROW what a caller can ask for -- the property "
          "requirements would be dropped rather than refused, which is worse than not migrating. "
          "The contract has to grow those fields first."),
    Route("/v1/jobs/<run>", STILL_DECIDES_FOR_ITSELF,
          "The older status. Returns the whole run record, signed; the contract's `status` "
          "returns a narrower shape on purpose.",
          "Existing clients read fields the contract's status does not return. Migrating it "
          "would break them silently, which is the one thing the instruction forbids."),
    Route("/v1/jobs/<run>/cancel", STILL_DECIDES_FOR_ITSELF,
          "The older cancel. Calls the gateway's cancel inline, so its callers still wait for "
          "the settle window -- the very thing the contract's cancel stopped doing.",
          "Its answer carries `settled`, which only means something for a cancel that waited. "
          "Migrating it changes what that field can say, so the clients reading it have to be "
          "moved first."),
)

BY_PATH = {route.path: route for route in REGISTER}


def still_deciding() -> tuple:
    """The ones that are still a second place the rules live. Shrinking this is the work."""
    return tuple(r for r in REGISTER if r.kind == STILL_DECIDES_FOR_ITSELF)


def what_a_reader_should_know() -> str:
    """For the deployment notes and the migration guide: plain, and not reassuring."""
    outstanding = still_deciding()
    if not outstanding:
        return ("Every address this gateway answers on goes through one decision point, or runs "
                "before anybody has a credential.")
    return (
        "%d of this gateway's addresses still decide for themselves rather than going through "
        "the contract's one decision point:\n%s\n"
        "They are older routes with callers that have not moved yet. Until they do, a change to "
        "the rules has to be made in more than one place, and that is exactly the risk the "
        "contract exists to remove." % (
            len(outstanding),
            "\n".join("  %-24s %s" % (r.path, r.what_migrating_would_break) for r in outstanding))
    )
