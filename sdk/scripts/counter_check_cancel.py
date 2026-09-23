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
CONTRACT = SDK / "agentnode_sdk" / "access" / "contract.py"
IDENTITY = SDK / "agentnode_sdk" / "gateway" / "identity.py"

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
        # What makes the teardown observable is the REPORT, not the flag: removing
        # `cancel_requested.set()` changes nothing a client can see, because `status` derives
        # `stopping` from the pool. Measured, not assumed -- the first version of this
        # counter-check removed the flag and the test went on passing, which said the flag was
        # the wrong thing to call the mechanism.
        "find": '    showing = "stopping" if _is_stopping(service, record.run_id) else record.state\n',
        "replace": "    showing = record.state  # MUTATED: the teardown is never published\n",
        "test": CANCEL_TESTS + "::TestTheCallerIsNotHeld"
                "::test_and_the_run_says_stopping_until_it_is_really_over",
        "because": "a state nobody can observe is a state the client cannot act on",
        "expect": "assert",
    },
    {
        "name": "a run being torn down counts as finished",
        "file": CONTRACT,
        # The other way this property can be lost, and the one the file guards in its first two
        # lines: not the record moving early, but `stopping` being counted among the states that
        # mean a run is over. Making the record terminal early is masked by the report, which is
        # why that mutation is not the one here -- it was tried and the test went on passing.
        "find": ('FINISHED_STATES = ("finished", "refused", "cancelled", "unverified", '
                 '"interrupted")\n'),
        "replace": ('FINISHED_STATES = ("finished", "refused", "cancelled", "unverified", '
                    '"interrupted", "stopping")  # MUTATED\n'),
        "test": CANCEL_TESTS + "::TestCleanupIsStillRequired"
                "::test_the_terminal_state_still_waits_for_the_sandbox_to_be_gone",
        "because": "a terminal state that arrives before the teardown is a promise nobody kept",
        "expect": "assert",
    },
    {
        "name": "an exception in the stop is swallowed and called success",
        "file": POOL,
        # The whole block, not only its first line. Replacing just the `except` left
        # `problem = str(exc) or ...` behind with `exc = None`, so `problem` became the string
        # 'None', which is truthy, and the cancellation went on being reported as failed. That
        # version of this counter-check passed while establishing nothing -- measured, not assumed.
        "find": ("        except Exception as exc:                                  # noqa: BLE001\n"
                 "            # A cancellation that failed must not look like one that worked. "
                 "The run keeps\n"
                 "            # whatever state the gateway gave it, and `status` goes on saying "
                 "what is true.\n"
                 "            problem = str(exc) or exc.__class__.__name__\n"),
        "replace": ("        except Exception:  # MUTATED: the failure is lost and called success\n"
                    "            settled = True\n"),
        # The test named here is the pool's own, not one in the cancellation file: what the
        # cancellation file can see is the run's STATE, and a swallowed exception does not move
        # it -- the run thread is still inside the worker either way. The mechanism that is
        # actually lost lives where the exception is caught, so the test that holds it is the one
        # that reads `settled` and `problem`. Measured: mutating this and running the cancel test
        # left it green, which would have been a counter-check establishing nothing.
        "test": "tests/test_stopping.py::TestAFailedStopDoesNotLookLikeOneThatWorked"
                "::test_a_teardown_that_raises_is_recorded_as_a_problem_and_not_as_settled",
        "because": "work started in the background that loses its exception reports a lie",
        "expect": "assert",
    },
    {
        # THE CLEANUP ITSELF, not the note about it. The mutation below this one removes only the
        # line that records a cleanup for a job that never created anything -- a real mechanism,
        # but bookkeeping: a gateway that never asked whether a sandbox was still there would
        # survive it untouched. This one replaces the ASKING with the answer it would have given,
        # which is the failure worth catching -- not a flag that is missing, but one that is true
        # because somebody assumed it.
        "name": "the sandbox is called gone without anybody asking",
        "file": SERVER,
        "find": ("                record.cleanup_verified = "
                 "self.worker.gone(record.container_name).verified\n"),
        "replace": ("                record.cleanup_verified = True  # MUTATED: nobody asked\n"),
        "test": CANCEL_TESTS + "::TestCleanupIsStillRequired"
                "::test_and_the_terminal_state_arrives_with_the_sandbox_CONFIRMED_gone",
        "because": "a terminal state whose sandbox nobody accounted for is the thing cleanup is for",
        "expect": "nobody asked the worker",
    },
    {
        "name": "a job that created nothing stops saying nothing was left",
        "file": SERVER,
        "find": "        if not record.container_name:\n            record.cleanup_verified = True\n",
        "replace": "        if False:  # MUTATED: nothing is recorded about cleanup\n"
                   "            record.cleanup_verified = True\n",
        "test": "tests/test_capacity_billing.py::TestAgainstARealGateway"
                "::test_and_it_left_nothing_behind_to_clean_up",
        "because": "a job that created nothing still has to SAY nothing was left behind",
        "expect": "assert",
    },
    # The two below are about the resource check the cancellation file now carries. A check of
    # that kind is decoration unless something can make it fail, so each is made to fail here.
    {
        "name": "the gateway stops taking its stopping threads back",
        "file": SERVER,
        "find": ('        pool = getattr(self, "stopping", None)\n'
                 "        left_stopping = list(pool.close() or ()) if pool is not None else []\n"),
        "replace": ("        left_stopping = []  # MUTATED: the hands are never told to stop\n"),
        "test": CANCEL_TESTS + "::TestTheCallerIsNotHeld"
                "::test_asking_twice_does_not_start_a_second_stop",
        "because": "threads that outlive the gateway are the leak the teardown check exists for",
        "expect": "did not go back to what they were",
    },
    {
        "name": "the state directory is never given back",
        "file": IDENTITY,
        "posix_only": (
            "the descriptor this removes is one POSIX holds on a directory, and Windows does not "
            "hold one at all -- so on Windows there is nothing here to take away and a green run "
            "would say nothing. Run on Linux, where the property exists."),
        "find": ("        if fd is not None:\n"
                 "            try:\n"
                 "                import os as _os\n"),
        "replace": ("        if False:  # MUTATED: the descriptor is kept for ever\n"
                    "            try:\n"
                    "                import os as _os\n"),
        "test": CANCEL_TESTS + "::TestCleanupIsStillRequired"
                "::test_the_terminal_state_still_waits_for_the_sandbox_to_be_gone",
        "because": "a descriptor left open on the test's own directory is what that check asks about",
        "expect": "still held open",
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
    not_here = []
    for spec in MUTATIONS:
        if args.only and args.only != spec["name"]:
            continue
        path = spec["file"]
        if spec.get("posix_only") and sys.platform == "win32":
            out("")
            out("-- %s" % spec["name"])
            out("   in %s" % path.relative_to(REPO))
            out("   NOT RUN ON THIS PLATFORM, and therefore NOT ESTABLISHED here: %s"
                % spec["posix_only"])
            not_here.append(spec["name"])
            continue
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
            named = spec["test"].split("::")[0]
            failed_line = (the_line_that_says(done.stdout, "FAILED " + named)
                           or the_line_that_says(done.stdout, "ERROR " + named))
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
    out("every mechanism that was run was missed by nothing: %s" % every)
    out("run on: %s" % sys.platform)
    if not_here:
        out("NOT ESTABLISHED ON THIS PLATFORM, and not counted above: %s" % ", ".join(not_here))
    out("=" * 96)
    if args.out:
        pathlib.Path(args.out).write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        print("transcript written to %s" % args.out)
    return 0 if every else 1


if __name__ == "__main__":
    raise SystemExit(main())
