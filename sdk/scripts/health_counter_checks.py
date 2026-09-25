"""Counter-checks for the health state machine (profile `mtls-default-r2`).

Every test in `test_what_the_gateway_says_about_its_worker.py` and
`test_the_gateway_refuses_while_its_worker_is_gone.py` is new, so all of them are trivially red
against the parent -- the module they import does not exist there. That establishes nothing about
whether they would NOTICE the defect coming back, which is the only thing a test is for.

So each check here takes one property away from the code that has it, and requires: the control
is green first; the mutation LANDS (the file's digest moves and the new text is present); a NAMED
test fails, non-zero, with the reason the mutation predicts in its output; named tests that must
not be affected stay green; and the file is restored byte for byte, verified by digest.

Run from `sdk/`:  python scripts/health_counter_checks.py [--only ID ...] [--out DIR]

A check that could not be carried out is reported as NOT RUN with the reason, never as passed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent

W = "tests/test_what_the_gateway_says_about_its_worker.py::"
G = "tests/test_the_gateway_refuses_while_its_worker_is_gone.py::"

CHECKS = [
    dict(id="admission-consults-the-live-state",
         area="H1/H3 -- a valid measurement about an absent worker is not permission",
         file="agentnode_sdk/gateway/server.py",
         edits=[("        live = self.health.now()\n"
                 "        measured = self._what_the_measurement_proves()\n"
                 "        if live.may_admit:\n"
                 "            return measured\n",
                 "        live = self.health.now()\n"
                 "        measured = self._what_the_measurement_proves()\n"
                 "        if True:\n"
                 "            return measured\n")],
         test=G + "test_a_measured_gateway_is_ready_until_its_worker_stops_answering",
         expect="assert not True",
         # NOT `test_the_measurement_itself_is_untouched_by_the_worker_going_away`, which the
         # first run of this harness used and which failed too. It asserts `not
         # readiness_now().ready` as its own precondition, so it rests on the very property this
         # mutation removes -- a test that SHOULD go red, not one that must stay green.
         green=[G + "test_what_a_client_is_told_carries_the_cause"]),

    dict(id="a-loss-blocks-until-a-new-measurement",
         area="H6 -- reachability returning must not restore `protected` by itself",
         file="agentnode_sdk/gateway/health.py",
         edits=[("        # It answers again AFTER A LOSS. That is not permission: between going "
                 "and coming back\n"
                 "        # it may be a different worker, a different image, or the same one "
                 "with less of a\n"
                 "        # ceiling, and none of that would show in a report bound before it went.\n"
                 "        return self._publish(\n"
                 "            MEASURING, MEASUREMENT_RUNNING,\n",
                 "        return self._publish(\n"
                 "            PROTECTED, OK,\n")],
         test=W + "test_a_worker_that_comes_back_does_not_restore_protected_by_itself",
         expect="assert 'protected' == 'measuring'",
         green=[W + "test_a_worker_that_does_not_answer_makes_the_gateway_say_unavailable_and_not_protected"]),

    dict(id="a-stale-permissive-statement-is-not-believed",
         area="H1 -- the exact defect: a `Protected` verdict carried forward",
         file="agentnode_sdk/gateway/health.py",
         edits=[("    if health.at <= 0.0 or age > allowed:\n", "    if False:\n")],
         test=W + "test_a_protected_statement_that_stopped_being_refreshed_stops_being_believed",
         expect="assert 'protected' == 'unavailable'",
         green=[W + "test_a_refusal_is_carried_forward_however_old_it_is"]),

    dict(id="a-probe-that-overruns-is-a-failure",
         area="H2 -- the window is only as good as the time one probe may take",
         file="agentnode_sdk/gateway/health.py",
         edits=[("        if attempt.reached and attempt.overran:\n", "        if False:\n")],
         test=W + "test_a_worker_that_accepts_and_then_stalls_counts_as_not_answering",
         expect="assert 'protected' == 'unavailable'",
         green=[W + "test_the_probe_is_given_the_deadline_rather_than_left_to_the_transport"]),

    dict(id="a-measurement-across-a-loss-cannot-publish-protected",
         area="H6 -- the worker it measured may not be the worker that is there now",
         file="agentnode_sdk/gateway/health.py",
         edits=[("            if self.now().generation != began_under:\n"
                 "                # The worker went away while this was running. What it measured "
                 "is about a\n"
                 "                # machine that no longer exists.\n"
                 "                return self.now()\n", "            pass\n"),
                ("            if not final.reached or self.now().generation != began_under:\n",
                 "            if False:\n")],
         test=W + "test_a_measurement_that_began_before_a_loss_cannot_publish_protected",
         expect="assert 'protected' == 'unavailable'",
         green=[W + "test_protected_comes_back_only_after_the_new_measurement_succeeds"]),

    dict(id="the-refusal-says-which-cause",
         area="H4 -- one word for every cause is what a client got before",
         file="agentnode_sdk/gateway/server.py",
         edits=[("            blocked.refusal_cause = \"\" if live.may_admit else live.code\n",
                 "            blocked.refusal_cause = \"\"\n")],
         test=G + "test_a_job_submitted_while_the_worker_is_gone_is_refused_with_a_cause",
         expect="assert '' == 'worker_unreachable'",
         green=[G + "test_what_a_client_is_told_carries_the_cause"]),

    dict(id="the-operator-check-reads-the-published-statement",
         area="H1 -- an indicator computed in a process that never probed anything",
         file="agentnode_sdk/gateway/observability.py",
         # NOT the `if not live.may_admit` override, which the first run of this harness took
         # away and which changed nothing: `readiness_now()` had already set `measured` false
         # through the in-process object, so the override was redundant for that assertion. What
         # this check is about is the operator's check READING THE PUBLISHED STATEMENT at all,
         # so that is what the mutation takes.
         edits=[("    live = service.published_health()\n",
                 "    from agentnode_sdk.gateway import health as _h0\n"
                 "    live = _h0.starting()\n")],
         test=G + "test_the_operator_health_check_stops_reporting_a_measured_machine",
         expect="assert 'starting' == 'unavailable'",
         green=[G + "test_hello_says_what_is_measured_and_what_is_live_separately"]),

    dict(id="absent-is-not-unreadable",
         area="H1 -- a statement that exists and cannot be read is not a clean slate",
         file="agentnode_sdk/gateway/health.py",
         edits=[("    except (OSError, ValueError) as unreadable:\n",
                 "    except (OSError, ValueError) as unreadable:  # noqa: F841\n"
                 "        return Health(STARTING, NO_STATEMENT, \"\", 0, 0.0, 0.0)\n"
                 "    if False:\n")],
         test=W + "test_nothing_published_is_not_the_same_as_something_unreadable",
         expect="assert 'starting' == 'unavailable'",
         green=[W + "test_a_reader_in_another_process_sees_the_state_the_gateway_published"]),

    dict(id="before-the-first-probe-it-does-not-claim-protected",
         area="`starting` is not `protected`",
         file="agentnode_sdk/gateway/health.py",
         edits=[("    return Health(STARTING, NOT_YET_PROBED,\n",
                 "    return Health(PROTECTED, NOT_YET_PROBED,\n")],
         test=W + "test_before_the_first_probe_the_machine_does_not_claim_to_be_protected",
         expect="assert 'protected' == 'starting'",
         green=[W + "test_a_worker_that_does_not_answer_makes_the_gateway_say_unavailable_and_not_protected"]),
]

#: Run pytest in a subprocess whose sys.path does not contain the OTHER checkout.
#:
#: The venv that carries the dependencies belongs to the main checkout, and its editable .pth
#: puts that checkout's `sdk` on sys.path. The main checkout has a `tests/__init__.py` and this
#: tree does not, so `tests` resolves there -- a regular package always beats a namespace one,
#: whatever the order -- and a counter-check would have mutated THIS tree and measured that one,
#: which is the worst possible failure for a harness whose whole job is to discriminate.
#: On CI there is one checkout and this is a no-op.
_BOOT = """
import os, sys
here = %r
sys.path = [p for p in sys.path
            if os.path.normcase(os.path.abspath(p or '.')) != os.path.normcase(here)
            or os.path.normcase(here) == os.path.normcase(%r)]
sys.path.insert(0, %r)
import agentnode_sdk
assert os.path.normcase(os.path.dirname(os.path.dirname(agentnode_sdk.__file__))) \
    == os.path.normcase(%r), agentnode_sdk.__file__
import pytest
sys.exit(pytest.main(sys.argv[1:]))
"""


def _boot(foreign: str) -> str:
    me = str(HERE)
    return _BOOT % (foreign, me, me, me)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def pytest(nodeids: list[str]) -> tuple[int, str]:
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    foreign = os.environ.get("AGENTNODE_OTHER_CHECKOUT", "")
    argv = [sys.executable]
    argv += (["-c", _boot(foreign)] if foreign else ["-m", "pytest"])
    done = subprocess.run([*argv, "-q", "-p", "no:cacheprovider", "-o", "addopts=", *nodeids],
                          cwd=HERE, env=env, capture_output=True, text=True, timeout=900)
    return done.returncode, done.stdout + done.stderr


def run_one(check: dict) -> dict:
    path = HERE / check["file"]
    result = {"id": check["id"], "area": check["area"], "file": check["file"],
              "test": check["test"], "expect": check["expect"], "green": check["green"]}
    original = path.read_bytes()
    result["before_sha256"] = sha(original)

    code, out = pytest([check["test"], *check["green"]])
    result["control_exit"] = code
    if code != 0:
        result.update(verdict="NOT RUN", why="the control was not green", control_tail=out[-1500:])
        return result

    text = original.decode("utf-8")
    for old, new in check["edits"]:
        if text.count(old) != 1:
            result.update(verdict="NOT RUN",
                          why="the mutation's anchor occurs %d times" % text.count(old),
                          anchor=old[:200])
            return result
        text = text.replace(old, new)
    try:
        path.write_bytes(text.encode("utf-8"))
        mutated = path.read_bytes()
        result["mutated_sha256"] = sha(mutated)
        result["landed"] = (result["mutated_sha256"] != result["before_sha256"]
                            and all(new in mutated.decode("utf-8") for _, new in check["edits"]))
        code, out = pytest([check["test"]])
        result["mutated_exit"] = code
        result["mutated_tail"] = out[-2500:]
        result["failed_for_the_predicted_reason"] = code != 0 and check["expect"] in out
        green_code, green_out = pytest(check["green"])
        result["others_stayed_green"] = green_code == 0
        if green_code != 0:
            result["green_tail"] = green_out[-1500:]
    finally:
        path.write_bytes(original)
    result["restored_sha256"] = sha(path.read_bytes())
    result["restored_byte_exactly"] = result["restored_sha256"] == result["before_sha256"]
    ok = (result["landed"] and result["mutated_exit"] != 0
          and result["failed_for_the_predicted_reason"] and result["others_stayed_green"]
          and result["restored_byte_exactly"])
    result["verdict"] = "RED AS PREDICTED" if ok else "DID NOT DISCRIMINATE"
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", nargs="*", default=None)
    parser.add_argument("--out", default=str(HERE / "health-counter-check-results"))
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    chosen = [c for c in CHECKS if not args.only or c["id"] in args.only]
    results = []
    for check in chosen:
        started = time.time()
        r = run_one(check)
        r["seconds"] = round(time.time() - started, 1)
        results.append(r)
        print("%-46s %s" % (r["id"], r["verdict"]), flush=True)
    (out / "counter-checks.json").write_text(json.dumps(results, indent=1), encoding="utf-8")
    lines = []
    for r in results:
        lines.append("%s  [%s]  %s" % (r["verdict"], r["id"], r["area"]))
        lines.append("    file %s  before %s" % (r["file"], r.get("before_sha256", "")[:16]))
        if "mutated_sha256" in r:
            lines.append("    mutated %s  landed=%s" % (r["mutated_sha256"][:16], r.get("landed")))
            lines.append("    named test exit=%s  predicted reason %r present=%s"
                         % (r.get("mutated_exit"), r["expect"],
                            r.get("failed_for_the_predicted_reason")))
            lines.append("    others stayed green=%s" % r.get("others_stayed_green"))
            lines.append("    restored %s  byte-exact=%s"
                         % (r["restored_sha256"][:16], r.get("restored_byte_exactly")))
        else:
            lines.append("    " + r.get("why", ""))
    (out / "counter-checks.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    bad = [r for r in results if r["verdict"] != "RED AS PREDICTED"]
    print("%d of %d red as predicted" % (len(results) - len(bad), len(results)))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
