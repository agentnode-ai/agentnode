"""Counter-checks for the cancellation tests: does anything here still bite?

The repair in `test_cancel_is_not_a_wait.py` removed a race. A repair like that is one edit away
from also removing the proof -- a test that waits for the right moment and then asserts nothing
in particular passes on a machine where the product is broken. So each mechanism the file is
supposed to hold is taken out of the PRODUCT, one at a time, and a named test has to go red for
the reason predicted here. A red that arrives for some other reason establishes nothing, so each
mutation names the message it expects and the run is only counted when that message appears.

Every mutated file is restored byte for byte, with the digest printed before and after.

    python scripts/counter_check_cancel.py            # everything, to stdout
    python scripts/counter_check_cancel.py --list     # what it would do
    python scripts/counter_check_cancel.py --out t.txt

Run it from `sdk/`. It refuses to start if a file it mutates already has uncommitted changes.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
SDK = HERE.parent
REPO = SDK.parent
PYTEST = [sys.executable, "-m", "pytest", "-q", "-p", "no:randomly"]

DISPATCH = SDK / "agentnode_sdk" / "access" / "dispatch.py"
SERVER = SDK / "agentnode_sdk" / "gateway" / "server.py"
POOL = SDK / "agentnode_sdk" / "access" / "stopping.py"

CANCEL_TESTS = "tests/test_cancel_is_not_a_wait.py"

MUTATIONS = [
    {
        "name": "cancelling is done on the caller's thread again",
        "file": DISPATCH,
        "find": "        stop = service.stopping.ask(record.run_id, by=principal.client_id)\n",
        "replace": ("        service.cancel(record.run_id)  # MUTATED: the old synchronous shape\n"
                    "        stop = service.stopping.ask(record.run_id, by=principal.client_id)\n"),
        "test": CANCEL_TESTS + "::TestTheCallerIsNotHeld"
                "::test_the_answer_comes_back_while_the_stop_is_still_in_progress",
        "because": "the caller would be held until the stop finished, which is the whole claim",
        "expect": "the stop finished",
    },
    {
        "name": "the run never says it is stopping",
        "file": DISPATCH,
        # Anchored on the comment above it: this line appears twice in the file, and only this
        # one is the cancellation path a client walks.
        "find": ("    # to stop and the stopping beginning.\n"
                 "    record.cancel_requested.set()\n"),
        "replace": ("    # to stop and the stopping beginning.\n"
                    "    pass  # MUTATED: nothing is published about this run being stopped\n"),
        "test": CANCEL_TESTS + "::TestTheCallerIsNotHeld"
                "::test_and_the_run_says_stopping_until_it_is_really_over",
        "because": "a state nobody can observe is a state the client cannot act on",
        "expect": "assert",
    },
    {
        "name": "the run is called terminal before its sandbox is gone",
        "file": SERVER,
        # Anchored on the comment above it, for the same reason as the one before.
        "find": ('        # absence of a container name.\n'
                 '        self.slots.drop(record.run_id, "cancelled")\n'),
        "replace": ('        # absence of a container name.\n'
                    '        self.slots.drop(record.run_id, "cancelled")\n'
                    '        record.move_to("cancelled")  # MUTATED: terminal before cleanup\n'),
        "test": CANCEL_TESTS + "::TestCleanupIsStillRequired"
                "::test_the_terminal_state_still_waits_for_the_sandbox_to_be_gone",
        "because": "a terminal state that arrives before the teardown is a promise nobody kept",
        "expect": "while its sandbox was still being torn down",
    },
    {
        "name": "an exception in the stop is swallowed and called success",
        "file": POOL,
        "find": ("        except Exception as exc:                                  # noqa: BLE001\n"),
        "replace": ("        except Exception:  # MUTATED: the failure is lost\n"
                    "            settled = True\n"
                    "            exc = None\n"),
        "test": CANCEL_TESTS + "::TestCleanupIsStillRequired"
                "::test_a_stop_that_fails_does_not_look_like_one_that_worked",
        "because": "work started in the background that loses its exception reports a lie",
        "expect": "a cancellation that failed was reported as one that worked",
    },
    {
        "name": "cleanup is neither done nor recorded",
        "file": SERVER,
        "find": "        if not record.container_name:\n            record.cleanup_verified = True\n",
        "replace": "        if False:  # MUTATED: nothing is recorded about cleanup\n"
                   "            record.cleanup_verified = True\n",
        "test": "tests/test_capacity_billing.py::TestAgainstARealGateway"
                "::test_and_it_left_nothing_behind_to_clean_up",
        "because": "a job that created nothing still has to SAY nothing was left behind",
        "expect": "assert",
    },
]


def digest(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(*args):
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, timeout=120)


def run_test(node: str):
    return subprocess.run(PYTEST + [node], cwd=SDK, capture_output=True, text=True,
                          env=dict(os.environ), timeout=1800)


def the_line_that_says(text: str, marker: str):
    """The line carrying `marker`, preferring the one pytest prefixes with `E`.

    An exit code alone cannot tell a refusal from an import error, so every counter-check names
    the message it predicts and this finds it -- or does not, and then nothing was established.
    """
    matched = [line.strip() for line in text.splitlines() if marker.lower() in line.lower()]
    if not matched:
        return None
    for line in matched:
        if line.startswith("E "):
            return line[:300]
    return matched[-1][:300]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--only")
    parser.add_argument("--out")
    args = parser.parse_args()

    if args.list:
        for spec in MUTATIONS:
            print("%-52s -> %s" % (spec["name"], spec["test"].split("::")[-1]))
        return 0

    lines = []

    def out(line=""):
        print(line)
        lines.append(line)

    for path in {spec["file"] for spec in MUTATIONS}:
        dirty = git("status", "--porcelain", "--", str(path.relative_to(REPO))).stdout.strip()
        if dirty:
            print("%s has uncommitted changes (%s). Commit or stash first: these counter-checks "
                  "mutate it and restore it, and the restore has to land on a known state."
                  % (path.relative_to(REPO), dirty.split()[0]))
            return 2

    out("counter-checks for the cancellation tests")
    out("repository: %s" % REPO)
    out("")
    out("=" * 96)
    out("EACH MECHANISM REMOVED FROM THE PRODUCT, ONE AT A TIME")
    out("=" * 96)

    every = True
    for spec in MUTATIONS:
        if args.only and args.only != spec["name"]:
            continue
        path = spec["file"]
        original = path.read_bytes()
        before = hashlib.sha256(original).hexdigest()
        text = original.decode("utf-8")
        out("")
        out("-- %s" % spec["name"])
        out("   in %s" % path.relative_to(REPO))
        out("   why it should go red: %s" % spec["because"])
        if spec["find"] not in text:
            out("   !! the anchor is not in the file, so this counter-check measured nothing")
            every = False
            continue
        try:
            path.write_bytes(text.replace(spec["find"], spec["replace"], 1).encode("utf-8"))
            landed = digest(path)
            done = run_test(spec["test"])
            said = the_line_that_says(done.stdout, spec["expect"])
            failed_line = the_line_that_says(done.stdout, "FAILED " + spec["test"].split("::")[0])
            red = done.returncode != 0 and said is not None and failed_line is not None
            out("   the mutation landed:  sha256 %s (was %s)" % (landed[:16], before[:16]))
            out("   the test that should catch it: %s" % spec["test"].split("::")[-1])
            out("   exit %d, and what it said, verbatim:" % done.returncode)
            out("     %s" % (said or "(nothing it printed contained %r)" % spec["expect"]))
            out("     %s" % (failed_line or "(no FAILED line naming that file)"))
            out("   RED FOR THE PREDICTED REASON (%r): %s" % (spec["expect"], red))
            if not red:
                for line in done.stdout.strip().splitlines()[-12:]:
                    out("     %s" % line)
            every = every and red
        finally:
            path.write_bytes(original)
        back = digest(path)
        out("   restored:             sha256 %s  byte for byte: %s" % (back[:16], back == before))
        every = every and back == before

    out("")
    out("=" * 96)
    out("every mechanism was missed by nothing: %s" % every)
    out("=" * 96)
    if args.out:
        pathlib.Path(args.out).write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        print("transcript written to %s" % args.out)
    return 0 if every else 1


if __name__ == "__main__":
    raise SystemExit(main())
