"""#35: when the worker starts is ONE contract, and the installer, the README and the runbook state it alike.

r4 found them disagreeing (deviation 9): `install-worker-host.sh` has started the worker itself at the
end of its third pass since it was written (216cbeda), after the same preflight the unit runs, and
then waits until the worker is actually serving -- while the README told the operator to run
`systemctl start agentnode-worker` as a separate step 3, and the acceptance runbook's M6 said "start the
worker, then install --verify". The Chef accepted the installer's behaviour as the product's. So:

  * the installer starts the worker exactly once on its normal path, at the end, and only AFTER the
    preflight that refuses a bad configuration -- never in the passes that stop for the enrolment
    handover, and never with `enable --now` somewhere earlier;
  * the README's procedure says the third pass starts the worker and asks for no manual start;
  * the runbook's M6 does not tell anyone to start the worker; it begins with `install.sh --verify`.
"""
from __future__ import annotations

import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "sdk" / "deploy" / "separate-worker-host"
INSTALL = DEPLOY / "install-worker-host.sh"
README = DEPLOY / "README.md"
REQUEST = ROOT / "docs" / "testing" / "crosshost-rebuild-request.md"

STARTS_THE_WORKER = re.compile(
    r"^\s*systemctl\s+(start|restart|enable\s+--now|reload-or-restart|try-restart)\s+agentnode-worker(\.service)?\s*$")


def _code_lines(path):
    return [(n, l) for n, l in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
            if not l.lstrip().startswith("#")]


def test_the_installer_starts_the_worker_only_after_the_preflight():
    lines = _code_lines(INSTALL)
    preflight = [n for n, l in lines if 'die "preflight refused this configuration' in l]
    assert len(preflight) == 1, "the preflight refusal is the gate the start must come after"
    starts = [n for n, l in lines if STARTS_THE_WORKER.match(l)]
    assert starts, "the installer no longer starts the worker; then README and runbook must change back"
    assert min(starts) > preflight[0], "the worker is started before the preflight could refuse"


def test_no_pass_that_stops_for_the_handover_has_started_the_worker():
    lines = _code_lines(INSTALL)
    first_start = min(n for n, l in lines if STARTS_THE_WORKER.match(l))
    # the passes stop through `stop_here "..."` (defined as printf + exit 0), not through a bare exit
    handover_stops = [n for n, l in lines if re.match(r"^\s*stop_here\s", l)]
    assert len(handover_stops) >= 3, "the enrolment passes stop through stop_here"
    assert max(handover_stops) < first_start, "a pass that stops for the handover starts the worker"


def test_the_worker_unit_is_not_enabled_now_anywhere():
    for _n, line in _code_lines(INSTALL):
        assert not re.search(r"enable\s+--now\s+agentnode-worker(\.service)?\b", line), line


def test_the_readme_says_the_third_pass_starts_the_worker_and_asks_for_no_manual_start():
    said = README.read_text(encoding="utf-8")
    assert "systemctl start agentnode-worker" not in said
    assert "the third pass starts the worker" in said


def test_the_runbook_does_not_ask_anyone_to_start_the_worker():
    text = REQUEST.read_text(encoding="utf-8")
    plan = json.loads(text[text.index("```json") + 7:text.index("```", text.index("```json") + 7)])
    m6 = [s for s in plan["steps"] if s["id"] == "M6"][0]["what"]
    assert not re.search(r"\bstart the worker\b", m6), m6
    assert m6.startswith("install --verify on the control plane"), m6
    assert "started by its own install" in m6, m6
