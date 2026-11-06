"""What every page says about the two-machine measurement of `separate-worker-host` (task #29).

One wording, held in one place, so the four places that state it (deploy/README.md, deploy/RISKS.md,
deploy/separate-worker-host/README.md and the `agentnode_sdk.worker` docstring) cannot drift apart.

Two facts, and they must stay two:

* run `remote-worker-cross-host-r3` passed X1-X10 on 2026-10-02 for build df821f65, and its
  independent review passed -- so "has never passed" is no longer true;
* the CURRENT build, which carries the P13 change (task #28), has not been accepted by r4 on two
  freshly rebuilt machines -- so nothing may carry r3's result over to it.
"""
from __future__ import annotations

#: Both must be present, verbatim after flowing (whitespace collapsed, lower case, no backticks).
R3_PASSED = "remote-worker-cross-host-r3 passed x1-x10 on 2026-10-02 for build df821f65"
CURRENT_NOT_ACCEPTED = "the current build has not been accepted by r4"

#: None may be present: the stale sentence, and every way of carrying r3 over to the current build.
FORBIDDEN = (
    "has never passed",
    "nothing here has been run across two machines",
    "the current build passed",
    "the current build has been accepted",
    "this build has been accepted",
    "this build passed",
    "two-machine measurement passed",
    "has been measured on two machines",
    "carries over to the current build",
)


def flow(text: str) -> str:
    return " ".join(text.replace("`", "").split()).lower()


def problems(text: str) -> list[str]:
    """Everything wrong with what `text` says about the measurement; empty when it is right."""
    said = flow(text)
    found = []
    for needed in (R3_PASSED, CURRENT_NOT_ACCEPTED):
        if needed not in said:
            found.append("missing: " + needed)
    for claim in FORBIDDEN:
        if claim in said:
            found.append("forbidden: " + claim)
    return found
