"""P13: the worker rollback guard must not be waived by an operator's word alone.

`deploy/separate-worker-host/rollback-one-host.sh` refuses to roll a WORKER back to a build whose
`ERRORS` table lacks "no-lease" (it would send every lease refusal as "internal" and the pair would
stop recovering). The way past it used to be `--control-plane-already-rolled-back`, which set
PAIRED=yes from the command line: an assertion the script said itself it "cannot see the other
machine, so it cannot check". The review of crosshost-repair-2 left this as a permanent WARN
(F-ROUND7-001); it was not accepted as one.

Task #28 replaced it: the worker issues a single-use challenge, the gateway signs what it runs
after its own confirmed rollback, and the worker verifies that statement itself before the guard
is waived (`agentnode_sdk/worker/rollback_proof.py`; the cases are in
`tests/test_p13_control_plane_statement.py`). These two tests were strict xfails naming #28
until then; they now hold the result.
"""
from __future__ import annotations

import inspect
import re
from pathlib import Path

SDK = Path(__file__).resolve().parents[1]
ROLLBACK = (SDK / "deploy" / "separate-worker-host" / "rollback-one-host.sh").read_text(
    encoding="utf-8")


def test_a_bare_assertion_does_not_waive_the_guard():
    """The waiver must not be set from the command line alone."""
    sets = [line.strip() for line in ROLLBACK.splitlines()
            if re.search(r"\bPAIRED=yes\b", line) and not line.strip().startswith("#")]
    assert sets, "the script no longer has a waiver at all -- read it again"
    for line in sets:
        assert "--control-plane-already-rolled-back" not in line, (
            "the guard is waived by the operator's assertion alone: " + line)


def test_the_worker_receives_the_control_planes_generation_authenticated():
    """Something to check against exists: a statement of the control plane's generation, signed
    with the gateway's key, verified by the worker against its own trust, and recorded as used."""
    from agentnode_sdk.worker import rollback_proof

    source = inspect.getsource(rollback_proof)
    assert "control_generation" in source
    assert "check_peer(" in source and "verify_directly_issued_by" in source, (
        "the signer is not judged like a gateway on a connection")
    assert '"consumed"' in source, "the worker keeps no record that a statement was used"


def test_the_old_waiver_is_gone():
    """The previous state, held so the tests above cannot pass for a different reason."""
    assert '[ "${2:-}" = "--control-plane-already-rolled-back" ] && PAIRED=yes' not in ROLLBACK
    assert "cannot see the other machine" not in ROLLBACK
