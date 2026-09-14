"""Submitting a job the way a client must now do it: ask, be shown, agree, then send.

Every test that submits work goes through here rather than calling `gc.submit` directly, and the
reason is not tidiness. `submit` will not run anything without proof that a person was shown what
would happen, and a test that quietly bypassed that would be testing a gateway nobody ships.

What this stands in for is the person. It calls `prepare`, looks at what came back, and passes
that proof to `submit` -- the same three steps a human takes in the console and the same three the
command line walks somebody through. What it deliberately does NOT do is live inside the client
library: a client that fetches the consent it is required to present has not obtained consent, and
keeping this helper in the tests is what stops that convenience from creeping into the SDK.
"""
from __future__ import annotations

from agentnode_sdk.gateway import client as gc

#: What `prepare` needs to describe a job. Everything else is submission detail.
WHAT_IT_WOULD_DO = ("command", "network", "allowed_domains", "wall_clock_s")


def shown(connection, artifact: bytes, **job) -> dict:
    """What a person would be shown for this job, with the proof that it was shown."""
    return gc.prepare(connection, artifact,
                      **{k: v for k, v in job.items() if k in WHAT_IT_WOULD_DO})


def submit(connection, artifact: bytes, **job) -> dict:
    """Prepare, take what it says as having been agreed to, and submit against exactly that."""
    return gc.submit(connection, artifact,
                     accepted_disclosure=shown(connection, artifact, **job)["accepted_disclosure"],
                     **job)


def proof(connection, artifact: bytes = b"", **job) -> str:
    """Just the single-use proof, for a test that builds its own request body by hand."""
    return shown(connection, artifact, **job)["accepted_disclosure"]


def agreed(connection, request, artifact: bytes) -> dict:
    """A payload for the older door, with proof that somebody agreed to this exact job.

    Tests that hand-build a request are testing what the gateway does with a malformed, replayed
    or tampered one -- not what it does when nobody agreed. Without this they would all be
    refused at the consent gate instead, and would stop testing the thing they are named after.
    """
    payload = request.to_payload()
    payload["accepted_disclosure"] = proof(
        connection, artifact, command=list(request.command), network=request.network,
        allowed_domains=list(request.allowed_domains), wall_clock_s=int(request.wall_clock_s))
    return payload


def agreed_again(connection, payload: dict, artifact: bytes, **changes) -> dict:
    """The same job with something changed, carrying its own fresh approval.

    An approval is good once, so a second body needs a second one. That is not the test being
    worked around: a replay is still a replay, and this makes sure it is refused FOR being a
    replay rather than for reusing an approval, which would prove something else entirely.
    """
    made = dict(payload, **changes)
    made["accepted_disclosure"] = proof(
        connection, artifact, command=list(payload.get("command") or ()),
        network=payload.get("network", "none"),
        allowed_domains=list(payload.get("allowed_domains") or ()),
        wall_clock_s=int(payload.get("wall_clock_s") or 60))
    return made
