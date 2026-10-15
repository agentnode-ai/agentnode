"""Deterministic tool logic, in a SEPARATE module from the entry.

This file exists so the capability tests exercise genuine MULTI-FILE delivery: the entry
(`server.py`) imports this sibling, so a run only works if the whole tree was materialized
in the sandbox, not just a single file.
"""


def compute_sum(a, b):
    if not isinstance(a, int) or not isinstance(b, int) or isinstance(a, bool) or isinstance(b, bool):
        raise ValueError("echo_sum requires two integers a and b")
    return a + b
