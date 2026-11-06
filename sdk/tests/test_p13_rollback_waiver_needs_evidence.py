"""P13: the worker rollback guard must not be waived by an operator's word alone.

`deploy/separate-worker-host/rollback-one-host.sh` refuses to roll a WORKER back to a build whose
`ERRORS` table lacks "no-lease" (it would send every lease refusal as "internal" and the pair would
stop recovering). The way past it is `--control-plane-already-rolled-back`, which sets PAIRED=yes
from the command line: an assertion that the control plane is already on the same generation. The
script says itself that it "cannot see the other machine, so it cannot check". The review of
crosshost-repair-2 left this as a permanent WARN (F-ROUND7-001); it is not accepted as one here.

What these tests require of the waiver, and what the code has today:

* the decision to waive must rest on a RECORD, not on the flag: a statement of the control plane's
  generation that the worker received authenticated (mutual TLS, the expected gateway identity)
  and kept in its own state;
* the worker must actually RECEIVE such a statement. Today it does not: `take_lease` carries `{}`
  (worker/remote.py), `describe` goes worker -> gateway only, and the trust-list and time-floor
  generations say nothing about which code the control plane runs.

So the first test is red on the script and the second on the protocol: with the present
architecture P13 cannot be verified independently. Both are strict xfails naming the blocker;
the change they need is filed as its own task, not made inside this one.
"""
from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

SDK = Path(__file__).resolve().parents[1]
ROLLBACK = (SDK / "deploy" / "separate-worker-host" / "rollback-one-host.sh").read_text(
    encoding="utf-8")

BLOCKED = pytest.mark.xfail(strict=True, reason=(
    "P13 BLOCKED: the worker holds no authenticated statement of the control plane's generation, "
    "so the waiver cannot be checked; needs a protocol and state change (task #28)"))


@BLOCKED
def test_a_bare_assertion_does_not_waive_the_guard():
    """The waiver must not be set from the command line alone."""
    sets = [line.strip() for line in ROLLBACK.splitlines()
            if re.search(r"\bPAIRED=yes\b", line) and not line.strip().startswith("#")]
    assert sets, "the script no longer has a waiver at all -- read it again"
    for line in sets:
        assert "--control-plane-already-rolled-back" not in line, (
            "the guard is waived by the operator's assertion alone: " + line)


@BLOCKED
def test_the_worker_receives_the_control_planes_generation_authenticated():
    """Something to check against has to exist first: the gateway must state its generation in a
    message the worker receives over the authenticated channel, and the worker must keep it."""
    from agentnode_sdk.worker import remote, service

    lease_request = inspect.getsource(remote)
    taken = re.search(r'_ask\("take_lease",\s*(\{[^}]*\})', lease_request)
    assert taken and taken.group(1).strip() != "{}", "take_lease tells the worker nothing about the gateway"
    assert "control_plane_generation" in inspect.getsource(service), (
        "the worker keeps no record of the control plane's generation")


def test_today_the_waiver_is_exactly_the_flag():
    """The current state, held so the xfails above cannot pass for a different reason."""
    assert '[ "${2:-}" = "--control-plane-already-rolled-back" ] && PAIRED=yes' in ROLLBACK
    assert "cannot see the other machine" in ROLLBACK
