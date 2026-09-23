"""Counter-checks for the commit-range check, and the measurements that go with them.

The check under test reads the commit messages a CI event added and refuses to pass when it
cannot say what it read. Believing that requires two different things:

* MEASUREMENTS -- that the expression it used to read is empty on `main` and that the range it
  reads now is not, taken from git rather than from anybody's memory;
* COUNTER-CHECKS -- that each refusal actually fires. A refusal nobody has seen fire is a
  comment. Each one below removes a mechanism, predicts which named test goes red and why, runs
  it, and then restores the file byte for byte and says so with a digest.

    python scripts/counter_check_commit_range.py            # everything, to stdout
    python scripts/counter_check_commit_range.py --list     # what it would do
    python scripts/counter_check_commit_range.py --out t.txt   # and keep the transcript

Run it from `sdk/`. It writes nothing outside a temporary directory except the file it mutates
and restores, and it refuses to start if that file is not already clean.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
SDK = HERE.parent
REPO = SDK.parent
TARGET = SDK / "tests" / "test_admission.py"
PYTEST = [sys.executable, "-m", "pytest", "-q", "-p", "no:randomly"]

#: The merge this repository actually landed, and the main it landed on. The measurements below
#: are about these two commits: the range that was empty, and the range that is not.
MAIN_BEFORE = "04a528ed91015500264335fcead6d5600677775a"
THE_MERGE = "8fdb42017b9d7fdf99b6df2c5973f02a14148f06"
THE_BRANCH_HEAD = "40d42ff673a6066bed0ade415f31b037119d437f"


def git(*args, cwd=REPO):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=120)


def digest(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_test(node: str, env_extra: dict | None = None):
    env = dict(os.environ)
    env.update(env_extra or {})
    return subprocess.run(PYTEST + [node], cwd=SDK, capture_output=True, text=True, env=env,
                          timeout=900)


# ---------------------------------------------------------------------------------------------
# Measurements
# ---------------------------------------------------------------------------------------------

def measurements(out):
    out("=" * 96)
    out("MEASURED, from git rather than from memory")
    out("=" * 96)

    empty = git("rev-list", "--count", "origin/main..%s" % THE_MERGE)
    body = git("log", "--format=%B", "origin/main..%s" % THE_MERGE)
    out("")
    out("1. What the check used to read, evaluated at the commit that is on main:")
    out("     git rev-list --count origin/main..%s  ->  %r" % (THE_MERGE[:12], empty.stdout.strip()))
    out("     git log --format=%%B origin/main..%s  ->  %d characters" % (THE_MERGE[:12],
                                                                          len(body.stdout.strip())))
    out("   An empty read is what the old assertion received on `main`, and it is why the")
    out("   required lane has been red since 2026-09-17.")

    added = git("rev-list", "--count", "%s..%s" % (MAIN_BEFORE, THE_MERGE))
    messages = git("log", "--format=%B", "%s..%s" % (MAIN_BEFORE, THE_MERGE))
    merge_subject = git("log", "-1", "--format=%s", THE_MERGE).stdout.strip()
    out("")
    out("2. What the check reads now, for that same push (before..after):")
    out("     git rev-list --count %s..%s  ->  %s" % (MAIN_BEFORE[:12], THE_MERGE[:12],
                                                      added.stdout.strip()))
    out("     the merge's own subject: %r" % merge_subject)
    out("     is that subject inside what is read?  %s"
        % (merge_subject.lower() in messages.stdout.lower()))
    one_from_inside = git("log", "-1", "--format=%s", THE_BRANCH_HEAD).stdout.strip()
    out("     a commit the merge brought:  %r" % one_from_inside)
    out("     is it inside what is read?   %s"
        % (one_from_inside.lower() in messages.stdout.lower()))

    from_the_event = set(git("rev-list", "%s..%s" % (MAIN_BEFORE, THE_BRANCH_HEAD)).stdout.split())
    old_today = set(git("rev-list", "origin/main..%s" % THE_BRANCH_HEAD).stdout.split())
    out("")
    out("3. On a pull request the range is base..head, and it is fixed. The expression it")
    out("   replaces was not: it means whatever `origin/main` happens to point at right now.")
    out("   Both evaluated for the head of the pull request that landed this merge:")
    out("     from the event, base %s .. head %s : %d commits"
        % (MAIN_BEFORE[:12], THE_BRANCH_HEAD[:12], len(from_the_event)))
    out("     origin/main..%s, evaluated today               : %d commits"
        % (THE_BRANCH_HEAD[:12], len(old_today)))
    out("   The second is empty now only because `main` has since absorbed that head -- the same")
    out("   accident that made the check read nothing on `main`.")
    out("     commits the event range reads that the old one does not: %d"
        % len(from_the_event - old_today))
    out("     commits the old one reads that the event range does not: %d"
        % len(old_today - from_the_event))
    out("   That comparison is made AFTER main absorbed the branch, so on its own it says nothing")
    out("   about what the old expression read while the pull request was open. The next section")
    out("   measures that moment instead of describing it.")
    _while_the_branch_is_open(out)


def _while_the_branch_is_open(out):
    """The old expression and the new range, both enumerated while a branch is still open.

    Measured in a throwaway repository, because the moment being compared -- `origin/main` still
    pointing at the base -- is gone in this one. The same repository then has `origin/main` moved
    forward, which is what a merge does, and both expressions are read again.
    """
    where = pathlib.Path(tempfile.mkdtemp(prefix="commit-range-"))

    def there(*args):
        return subprocess.run(["git", *args], cwd=where, capture_output=True, text=True,
                              timeout=120)

    def commit(message):
        (where / "a-file").write_text(os.urandom(8).hex(), encoding="utf-8")
        there("add", "-A")
        there("commit", "-q", "-m", message)
        return there("rev-parse", "HEAD").stdout.strip()

    def subject(commit_id):
        return there("log", "-1", "--format=%s", commit_id).stdout.strip()

    out("")
    out("4. A pull request measured WHILE IT IS OPEN, in a repository built for it:")
    there("init", "-q", "-b", "main")
    there("config", "user.email", "a@example.invalid")
    there("config", "user.name", "A Test")

    base = commit("the base of the pull request")
    there("checkout", "-q", "-b", "a-branch")
    commit("one on the branch")
    head = commit("two on the branch")
    there("update-ref", "refs/remotes/origin/main", base)

    old = there("rev-list", "origin/main..HEAD").stdout.split()
    new = there("rev-list", "%s..%s" % (base, head)).stdout.split()
    out("     HEAD is           %s  %r" % (head, subject(head)))
    out("     origin/main is    %s  (the base, where a checkout has it while the branch is open)"
        % base)
    out("     the old expression origin/main..HEAD reads %d commits:" % len(old))
    for commit_id in old:
        out("         %s  %r" % (commit_id[:12], subject(commit_id)))
    out("     the event range %s..%s reads %d commits:" % (base[:12], head[:12], len(new)))
    for commit_id in new:
        out("         %s  %r" % (commit_id[:12], subject(commit_id)))
    out("     old minus new: %s" % (sorted(set(old) - set(new)) or "nothing"))
    out("     new minus old: %s" % (sorted(set(new) - set(old)) or "nothing"))

    there("update-ref", "refs/remotes/origin/main", head)
    after = there("rev-list", "origin/main..HEAD").stdout.split()
    again = there("rev-list", "%s..%s" % (base, head)).stdout.split()
    out("")
    out("     then origin/main is moved to the branch head, which is what a merge does:")
    out("     the old expression now reads  %d commits" % len(after))
    out("     the event range still reads   %d commits" % len(again))
    out("     A range taken from the event does not move when a ref moves. That is the whole")
    out("     difference, and it is why the check went quiet on `main` and not on a branch.")
    # git leaves its object files read-only, which rmtree refuses on Windows until the mode is
    # cleared. Two passes rather than one boolean that quietly says "False".
    for _ in range(2):
        shutil.rmtree(where, ignore_errors=True)
        if not where.exists():
            break
        for child in where.rglob("*"):
            try:
                os.chmod(child, 0o700)
            except OSError:
                pass
    out("     (the throwaway repository is gone again: %s)" % (not where.exists()))


# ---------------------------------------------------------------------------------------------
# Counter-checks that need no mutation: a real range, made now, refused now
# ---------------------------------------------------------------------------------------------

def a_real_forbidden_commit(out) -> bool:
    """Make a commit that carries a forbidden claim, in THIS repository, and watch it refused."""
    out("")
    out("-" * 96)
    out("COUNTER-CHECK A: a real commit, in this repository, whose message makes a claim")
    out("-" * 96)
    start = git("rev-parse", "HEAD").stdout.strip()
    scratch = "counter-check/a-claim-%s" % os.urandom(4).hex()
    made = git("commit-tree", "%s^{tree}" % start, "-p", start, "-m",
               "a subject\n\nthe body of this commit claims malicious intent, which it must not\n")
    if made.returncode != 0:
        out("could not build the commit: %s" % made.stderr.strip())
        return False
    claim = made.stdout.strip()
    git("update-ref", "refs/heads/%s" % scratch, claim)
    try:
        done = run_test(
            "tests/test_admission.py::TestNoClaimToDetectIntent"
            "::test_the_commit_messages_on_this_branch_do_not_claim_it_either",
            {"GITHUB_ACTIONS": "", "GITHUB_EVENT_PATH": "",
             "AGENTNODE_COMMIT_RANGE_BASE": start, "AGENTNODE_COMMIT_RANGE_HEAD": claim})
        said = _the_line_that_says(done.stdout, "claims to determine intent")
        failed_line = _the_line_that_says(done.stdout, "FAILED tests/test_admission.py")
        phrase = "malicious intent"
        red = (done.returncode != 0 and said is not None and phrase in said.lower()
               and failed_line is not None)
        out("the commit's message: %r"
            % git("log", "-1", "--format=%B", claim).stdout.strip())
        out("range %s..%s (one commit, made for this check)" % (start[:12], claim[:12]))
        out("the check exits %d" % done.returncode)
        out("and the failure it raised, verbatim:")
        out("    %s" % (said if said else "(no line naming a claim was printed -- see below)"))
        out("    %s" % (failed_line if failed_line else "(no FAILED line for this test)"))
        out("the failure names the phrase %r: %s" % (phrase, bool(said and phrase in said.lower())))
        out("REFUSED FOR THE PREDICTED REASON: %s" % red)
        if not red:
            out("the whole tail of what it printed:")
            for line in done.stdout.strip().splitlines()[-12:]:
                out("    %s" % line)
        return red
    finally:
        git("update-ref", "-d", "refs/heads/%s" % scratch)
        gone = git("rev-parse", "--verify", "--quiet", "refs/heads/%s" % scratch).returncode != 0
        out("the scratch ref is gone again: %s" % gone)


def an_empty_and_a_broken_range(out) -> bool:
    """Each way of not having a range, handed to the real check, in the real repository."""
    out("")
    out("-" * 96)
    out("COUNTER-CHECK B: a range that is empty, absent, unreal or unrelated")
    out("-" * 96)
    node = ("tests/test_admission.py::TestNoClaimToDetectIntent"
            "::test_the_commit_messages_on_this_branch_do_not_claim_it_either")
    head = git("rev-parse", "HEAD").stdout.strip()
    orphan = git("commit-tree", "%s^{tree}" % head, "-m", "a history that never met this one")
    cases = [
        ("an empty range (base == head)", {"AGENTNODE_COMMIT_RANGE_BASE": head,
                                           "AGENTNODE_COMMIT_RANGE_HEAD": head},
         "holds no commits"),
        ("a commit that is not here", {"AGENTNODE_COMMIT_RANGE_BASE": "b" * 40,
                                       "AGENTNODE_COMMIT_RANGE_HEAD": head},
         "is not a commit in this checkout"),
        ("all zeroes, as a forge sends", {"AGENTNODE_COMMIT_RANGE_BASE": "0" * 40,
                                          "AGENTNODE_COMMIT_RANGE_HEAD": head},
         "all zeroes"),
        ("half a range", {"AGENTNODE_COMMIT_RANGE_HEAD": head}, "half a range"),
        ("a CI job with no event at all", {"GITHUB_ACTIONS": "true"},
         "GITHUB_EVENT_PATH is not set"),
    ]
    if orphan.returncode == 0:
        cases.append(("two histories that never met",
                      {"AGENTNODE_COMMIT_RANGE_BASE": orphan.stdout.strip(),
                       "AGENTNODE_COMMIT_RANGE_HEAD": head}, "no common ancestor"))
    every = True
    for what, env, expected in cases:
        env = dict({"GITHUB_ACTIONS": "", "GITHUB_EVENT_PATH": "",
                    "AGENTNODE_COMMIT_RANGE_BASE": "", "AGENTNODE_COMMIT_RANGE_HEAD": ""}, **env)
        done = run_test(node, env)
        said = _the_line_that_says(done.stdout, expected)
        failed_line = _the_line_that_says(done.stdout, "FAILED tests/test_admission.py")
        red = done.returncode != 0 and said is not None and failed_line is not None
        out("")
        out("%s" % what)
        out("    exit %d, and the reason it gave, verbatim:" % done.returncode)
        out("    %s" % (said or "(nothing it printed contained %r)" % expected))
        out("    RED FOR THE PREDICTED REASON (%r): %s" % (expected, red))
        if not red:
            for line in done.stdout.strip().splitlines()[-10:]:
                out("      %s" % line)
        every = every and red
    out("")
    out("every one of them ended red, each naming its own reason: %s" % every)
    return every


def _the_line_that_says(text: str, marker: str) -> str | None:
    """The line carrying `marker`, or None.

    An earlier version of this file picked "a line that looks like a failure", which printed
    whatever source line pytest happened to echo and proved nothing about WHY a check went red.
    A counter-check that only knows the exit code cannot tell a refusal from an import error, so
    every caller here names the reason it predicts and this returns the line that carries it --
    or nothing, and then the counter-check has not established what it claims.
    """
    matched = [line.strip() for line in text.splitlines() if marker.lower() in line.lower()]
    if not matched:
        return None
    # pytest echoes the source of the failing assertion as well as the message it produced, and
    # the source line carries the format string rather than the values. The line that begins with
    # `E` is the one that says what actually happened, so it wins when both are present.
    for line in matched:
        if line.startswith("E "):
            return line[:300]
    return matched[-1][:300]


# ---------------------------------------------------------------------------------------------
# Counter-checks that remove a mechanism and predict which named test goes red
# ---------------------------------------------------------------------------------------------

MUTATIONS = [
    {
        "name": "the empty-range refusal is removed",
        "find": '    if counted.stdout.strip() in ("", "0"):',
        "replace": '    if False:  # MUTATED: the empty-range refusal is gone',
        "test": ("tests/test_admission.py::TestTheRangeTheCheckAboveReads"
                 "::test_an_empty_range_is_refused_rather_than_passed"),
        "because": "an empty range is exactly what `main` produced, so nothing would be read",
        "expect": "DID NOT RAISE",
    },
    {
        "name": "the relatedness check is removed",
        "find": '    if run("merge-base", base, head).returncode != 0:',
        "replace": '    if False:  # MUTATED: unrelated histories are no longer refused',
        "test": ("tests/test_admission.py::TestTheRangeTheCheckAboveReads"
                 "::test_two_histories_that_never_met_are_refused"),
        "because": "two ids that share no history are not two ends of one range",
        "expect": "DID NOT RAISE",
    },
    {
        "name": "an unknown event falls back to the branch instead of refusing",
        "find": '    raise _NoRange(\n        "the event %r does not describe a range of new commits.',
        "replace": '    return "origin/main", "HEAD"  # MUTATED: guess instead of refuse\n    raise _NoRange(\n        "the event %r does not describe a range of new commits.',
        "test": ("tests/test_admission.py::TestTheRangeTheCheckAboveReads"
                 "::test_an_event_that_describes_no_range_is_refused"),
        "because": "guessing a range is how a check ends up reading something nobody chose",
        # The guess is `origin/main`, which the hermetic repository does not have -- so the
        # refusal that arrives names THAT, which is the proof the fallback was taken.
        "expect": "('origin/main') is not a commit",
    },
    {
        "name": "the check is allowed to skip itself",
        "find": '            pytest.fail("the commits to read could not be established',
        "replace": '            pytest.skip("MUTATED: the commits to read could not be established',
        "test": ("tests/test_admission.py::TestTheRangeTheCheckAboveReads"
                 "::test_this_check_has_no_way_to_skip_itself"),
        "because": "this is the repair that was refused: green by reading nothing",
        "expect": "can skip itself",
    },
    {
        "name": "a push without `after` is filled in from the environment",
        "find": '        head = event.get("after")\n',
        "replace": '        head = event.get("after") or env.get("GITHUB_SHA")  # MUTATED\n',
        "test": ("tests/test_admission.py::TestTheRangeTheCheckAboveReads"
                 "::test_a_push_whose_after_is_missing_is_refused_rather_than_guessed"),
        "because": "an incomplete event is the case to refuse, not the case to repair quietly",
        "expect": "DID NOT RAISE",
    },
    {
        "name": "the shape check on what the forge sent is removed",
        "find": "        return _shaped(base, \"the push's before\"), _shaped(head, \"the push's after\")",
        "replace": "        return base, head  # MUTATED: whatever the payload said is taken",
        "test": ("tests/test_admission.py::TestTheRangeTheCheckAboveReads"
                 "::test_a_malformed_sha_is_refused"),
        "because": "a payload value that is not a commit id should never reach git",
        # Without the shape check the raw payload reaches git, and the refusal quotes it --
        # which is exactly the thing the shape check exists to prevent.
        "expect": "('not-a-sha; rm -rf /') is not a commit",
    },
]


def mutate(out, only=None) -> bool:
    original = TARGET.read_bytes()
    before = hashlib.sha256(original).hexdigest()
    out("")
    out("=" * 96)
    out("COUNTER-CHECKS THAT REMOVE A MECHANISM")
    out("=" * 96)
    out("%s" % TARGET.relative_to(REPO))
    out("  before any mutation: sha256 %s" % before)
    every = True
    try:
        for spec in MUTATIONS:
            if only and only != spec["name"]:
                continue
            text = original.decode("utf-8")
            if spec["find"] not in text:
                out("")
                out("!! %s: the anchor is not in the file, so this counter-check measured "
                    "nothing" % spec["name"])
                every = False
                continue
            mutated = text.replace(spec["find"], spec["replace"], 1)
            TARGET.write_bytes(mutated.encode("utf-8"))
            landed = hashlib.sha256(TARGET.read_bytes()).hexdigest()
            done = run_test(spec["test"])
            # The exit code alone cannot tell a refusal from a syntax error or a failed import.
            # So each mutation names the failure it predicts, and the line carrying it is printed.
            said = _the_line_that_says(done.stdout, spec["expect"])
            failed_line = _the_line_that_says(done.stdout, "FAILED %s" % spec["test"].split("::")[0])
            red = done.returncode != 0 and said is not None and failed_line is not None
            out("")
            out("-- %s" % spec["name"])
            out("   why it should go red: %s" % spec["because"])
            out("   the mutation landed:  sha256 %s (was %s)" % (landed[:16], before[:16]))
            out("   the test that should catch it: %s" % spec["test"].split("::")[-1])
            out("   exit %d, and what it said, verbatim:" % done.returncode)
            out("     %s" % (said or "(nothing it printed contained %r)" % spec["expect"]))
            out("     %s" % (failed_line or "(no FAILED line naming that file)"))
            out("   RED FOR THE PREDICTED REASON (%r): %s" % (spec["expect"], red))
            if not red:
                for line in done.stdout.strip().splitlines()[-10:]:
                    out("     %s" % line)
            every = every and red
            TARGET.write_bytes(original)
            back = hashlib.sha256(TARGET.read_bytes()).hexdigest()
            out("   restored:             sha256 %s  byte for byte: %s" % (back[:16],
                                                                          back == before))
            every = every and back == before
    finally:
        TARGET.write_bytes(original)
    after = hashlib.sha256(TARGET.read_bytes()).hexdigest()
    out("")
    out("  after every mutation: sha256 %s  unchanged: %s" % (after, after == before))
    return every and after == before


FORBIDDEN_BLOCK = re.compile(r"FORBIDDEN = \((?:.|\n)*?\)\n")


def _the_word_list(rev):
    """The FORBIDDEN tuple as it stands at `rev`, so the two can be compared byte for byte.

    Grepping the diff for the word FORBIDDEN answers a different question: the new tests mention
    the list and use one of its phrases as a commit message, and every one of those lines would
    be counted as a change to it. What matters is whether the tuple itself moved.
    """
    shown = git("show", "%s:sdk/tests/test_admission.py" % rev)
    if shown.returncode != 0:
        return None
    found = FORBIDDEN_BLOCK.search(shown.stdout)
    return found.group(0) if found else None


def what_else_changed(out):
    out("")
    out("=" * 96)
    out("WHAT ELSE MOVED")
    out("=" * 96)
    stat = git("diff", "--stat", "%s...HEAD" % THE_MERGE)
    out(stat.stdout.strip() or "(no diff against the merge that is on main)")
    out("")
    before, after = _the_word_list(THE_MERGE), _the_word_list("HEAD")
    out("the FORBIDDEN tuple, compared rather than grepped for:")
    out("   on %s: sha256 %s" % (THE_MERGE[:12],
                                 hashlib.sha256((before or b"").encode()).hexdigest()
                                 if before else "(not found)"))
    out("   on HEAD      : sha256 %s" % (hashlib.sha256((after or b"").encode()).hexdigest()
                                         if after else "(not found)"))
    out("   byte for byte identical: %s" % (before is not None and before == after))
    out("")
    out("files this change touches:")
    for line in git("diff", "--name-only", "%s...HEAD" % THE_MERGE).stdout.split():
        out("   %s" % line)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--only")
    parser.add_argument("--out", help="write the transcript here as well as to stdout")
    args = parser.parse_args()

    if args.list:
        for spec in MUTATIONS:
            print("%-56s -> %s" % (spec["name"], spec["test"].split("::")[-1]))
        return 0

    # Restoring means writing back what was there when this started. If that was already an
    # uncommitted edit, the digests below would be about something nobody has reviewed, and
    # "restored byte for byte" would be true of the wrong bytes.
    dirty = git("status", "--porcelain", "--", str(TARGET.relative_to(REPO))).stdout.strip()
    if dirty:
        print("%s has uncommitted changes (%s). Commit or stash them first: these counter-checks "
              "mutate that file and restore it, and the restore has to land on a known state."
              % (TARGET.relative_to(REPO), dirty.split()[0]))
        return 2

    lines = []

    def out(line=""):
        print(line)
        lines.append(line)

    out("counter-checks for the commit-range check")
    out("repository: %s" % REPO)
    out("target:     %s" % TARGET.relative_to(REPO))
    out("")
    measurements(out)
    ok_a = a_real_forbidden_commit(out)
    ok_b = an_empty_and_a_broken_range(out)
    ok_c = mutate(out, args.only)
    what_else_changed(out)
    out("")
    out("=" * 96)
    out("a real claim was refused:                 %s" % ok_a)
    out("every absent or broken range was refused: %s" % ok_b)
    out("every mutation went red and was restored: %s" % ok_c)
    out("=" * 96)
    if args.out:
        pathlib.Path(args.out).write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        print("transcript written to %s" % args.out)
    return 0 if (ok_a and ok_b and ok_c) else 1


if __name__ == "__main__":
    raise SystemExit(main())
