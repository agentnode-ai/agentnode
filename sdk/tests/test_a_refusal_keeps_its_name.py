"""A refusal code that is raised but cannot cross the wire.

`refusal()` rewrites any code not in `ERRORS` to `INTERNAL`, under a guard marked
`pragma: no cover`. `NO_LEASE` and `NO_COMMON_PROTOCOL` were both defined, both raised, and
neither was in `ERRORS`. So every lease refusal -- the ordinary, expected, recoverable kind --
reached the gateway as "internal".

What that cost, measured on two machines:

    refused the request (internal): this instruction names epoch 1 and the live lease is
    epoch 3

Three things at once. The operator is told the worker had an internal error while it was
doing exactly its job. The client cannot tell a recoverable refusal from a broken worker. And
the re-acquisition path added in this very repair keys on the cause, so it never fired on the
real wire while its own unit tests passed -- because those tests synthesised the cause
instead of taking it from a refusal.

That last part is why the general guard below exists rather than two more assertions.

NOT A HOST-ISOLATION TEST: one process, one kernel.
"""
from __future__ import annotations

import pathlib
import re

from agentnode_sdk.worker import protocol as wire


class TestTheTwoThatWereLost:

    def test_no_lease_can_cross_the_wire(self):
        assert wire.NO_LEASE in wire.ERRORS

    def test_and_so_can_no_common_protocol(self):
        assert wire.NO_COMMON_PROTOCOL in wire.ERRORS

    def test_a_lease_refusal_keeps_its_name(self):
        said = wire.refusal("r1", wire.NO_LEASE, "this worker holds no lease")
        assert said["error"] == wire.NO_LEASE, (
            "a lease refusal arrived as %r, so nothing can branch on it" % said["error"])

    def test_and_is_not_rewritten_to_internal(self):
        said = wire.refusal("r1", wire.NO_LEASE, "")
        assert said["error"] != wire.INTERNAL


class TestTheGuardAgainstTheNextOne:
    """The specific assertions above would not have caught this before it happened. This
    one would: every code the package actually raises must be able to cross the wire."""

    def _raised_codes(self):
        root = pathlib.Path(wire.__file__).resolve().parent.parent
        pattern = re.compile(r"ProtocolError\(\s*(?:wire\.)?([A-Z][A-Z_0-9]+)")
        found = set()
        for path in sorted(root.rglob("*.py")):
            for name in pattern.findall(path.read_text(encoding="utf-8", errors="replace")):
                found.add(name)
        return found

    def test_every_code_that_is_raised_can_cross_the_wire(self):
        missing = []
        for name in sorted(self._raised_codes()):
            value = getattr(wire, name, None)
            if value is None:
                continue                                      # not one of the wire's codes
            if value not in wire.ERRORS:
                missing.append(name)
        assert not missing, (
            "these codes are raised but are not in ERRORS, so `refusal` rewrites them to "
            "INTERNAL and the caller is told the worker broke: %s" % ", ".join(missing))

    def test_the_scan_actually_finds_things(self):
        """A guard that matched nothing would pass forever."""
        found = self._raised_codes()
        assert len(found) >= 5, found
        assert "NO_LEASE" in found
