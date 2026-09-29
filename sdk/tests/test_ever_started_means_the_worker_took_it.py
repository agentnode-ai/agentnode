"""One signed line that contradicted itself.

Run `aae60f46` on the two test machines produced this, all in the same line:

    "ever_started": true,
    "sandbox": "not_established",
    "termination_reason": "transport_lost",
    "seconds": 0.069

and the client was told, correctly, "The worker has no record of it, so it did not run".
Both cannot be true.

`ever_started` is derived in the meter from `started_at`, and the gateway sets `started_at`
when IT grants a local slot -- `# THE BILLED CLOCK STARTS HERE` -- which happens before
anything is sent to the worker. So it never meant "the worker took it". In the one branch
where the worker's own journal says it never began, the slot grant is now retracted, and
`seconds` goes to zero with it, which is what that branch's sentence already promised.

Deliberately narrow: only the branch where `keeps_a_record` makes "it did not run" a fact.
A worker that keeps no journal answers `known=False` about everything, and guessing from
that would be the same conflation with the sign flipped.

WHICH OF THESE DISCRIMINATE. One: `test_the_slot_grant_is_retracted`. The defect is a single
missing line, so a single test goes red against the unfixed code and the count is not padded
to look better. The rest are characterisation -- they pin what `ever_started` and `seconds`
mean so that a later change to the meter cannot quietly re-break this, and they were green
before the repair because the meter's derivation was never the faulty part.

NOT A HOST-ISOLATION TEST: one process, one kernel.
"""
from __future__ import annotations

import inspect
import json

from agentnode_sdk.gateway import meter
from agentnode_sdk.gateway import server as S


def a_line(tmp_path, **overrides):
    fields = dict(
        run_id="r1", client_id="c1", started_at=1000.0, finished_at=1000.5, queued_at=999.9,
        cpu=1.0, memory_mb=512, wall_clock_s=60, state="finished", outcome="succeeded",
        bytes_out=10, worker_topology="separate-worker-host", allowance_sha256="a" * 64,
        account_id="acct-1", worker_id="w2", operator_policy_sha256="b" * 64,
        operator_policy_version=1,
    )
    fields.update(overrides)
    meter.record(tmp_path, **fields)
    lines = [ln for ln in (tmp_path / "use-log.jsonl").read_text(
        encoding="utf-8").splitlines() if ln.strip()]
    return json.loads(lines[-1])


class TestTheFieldFollowsTheSlotGrant:
    """What it has always meant, kept explicit so the change below is visible."""

    def test_a_run_that_held_a_slot_says_so(self, tmp_path):
        assert a_line(tmp_path, started_at=1000.0)["ever_started"] is True

    def test_and_one_that_never_did_says_that(self, tmp_path):
        assert a_line(tmp_path, started_at=0.0)["ever_started"] is False

    def test_seconds_goes_with_it(self, tmp_path):
        line = a_line(tmp_path, started_at=0.0, finished_at=1000.5)
        assert line["seconds"] == 0.0, (
            "a run that never started cannot have been running for a while")


class TestTheBranchThatKnowsItNeverBegan:
    """The repair itself: the one place the gateway learns, from the worker, that its own
    slot grant was never taken up."""

    def test_the_slot_grant_is_retracted(self):
        source = inspect.getsource(S.GatewayService._run)
        branch = source[source.index("It never reached the worker"):]
        upto = branch[:branch.index("else:")]
        assert "record.started_at = 0.0" in upto

    def test_and_only_where_the_worker_keeps_a_record(self):
        """A worker with no journal says `known=False` about everything. Retracting on that
        would be the same conflation with the sign flipped."""
        source = inspect.getsource(S.GatewayService._run)
        head = source[:source.index("It never reached the worker")]
        assert 'keeps_a_record' in head.rsplit("elif", 1)[-1]

    def test_the_other_branch_stays_unverified(self):
        """Not knowing is not the same as knowing it did not run, and must not become it."""
        source = inspect.getsource(S.GatewayService._run)
        after = source[source.index("It never reached the worker"):]
        tail = after[after.index("else:"):]
        assert 'terminal = "unverified"' in tail
        assert "record.started_at = 0.0" not in tail[:tail.index("refusal")]


class TestTheLineCannotContradictItself:
    """The invariant the defect broke, stated as one."""

    def test_never_established_a_sandbox_and_never_started(self, tmp_path):
        line = a_line(tmp_path, started_at=0.0, sandbox="not_established",
                      termination_reason="transport_lost", outcome="unverified",
                      state="refused", bytes_out=0, exit_code=None)
        assert line["ever_started"] is False
        assert line["sandbox"] == "not_established"
        assert line["seconds"] == 0.0
        assert line["bytes_out"] == 0

    def test_the_pair_that_was_written_down_is_now_impossible_from_this_branch(self, tmp_path):
        """`ever_started: true` beside `sandbox: not_established` was the observed record."""
        line = a_line(tmp_path, started_at=0.0, sandbox="not_established")
        assert not (line["ever_started"] and line["sandbox"] == "not_established")
