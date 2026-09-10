"""FROZEN. This is the external verification tool that ran E1 to E6, and it does not run.

`EM3C-E6-RECORD-0001` found it confirming a sentinel by opening an ssh session to the gateway and
running `grep -c -- <value> /home/.../gateway.log`. A sandbox job's standard output is not written
to the gateway's log, so the answer was always `0`, and the tool reported a failure about its own
search. The same step had failed in the fourth and fifth external runs, where louder failures hid
it. Five of six lost runs were the tool rather than the thing it was measuring.

The founder's decision after that run was that it would not be patched again. It is kept here
because six external records were produced by it and a reader of those records has to be able to
find what produced them -- the code is in this file's history, at
`0a86da8a21561792b25e917b896ac78e5ea4335d` and before.

What replaced it is `agentnode_sdk.verification`, whose own identity is `verification/2`.
"""
from __future__ import annotations

WHY = (
    "This is the external verification tool that ran E1 to E6. It is frozen and does not run.\n"
    "  EM3C-E6-RECORD-0001 found it confirming a sentinel by searching the gateway's log file\n"
    "  for a sandbox job's standard output, which is not written there.\n"
    "  What replaced it is agentnode_sdk.verification -- start that instead."
)


class Frozen(RuntimeError):
    """This tool is not run. It is kept so the records it produced can be traced to it."""


def main(path=None) -> int:
    print(WHY)
    return 2


def run(argv=None) -> int:
    print(WHY)
    return 2


if __name__ == "__main__":
    raise SystemExit(run())
