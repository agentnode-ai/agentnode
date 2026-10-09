"""The worker install's one named self-repair has to be able to fire.

WHAT WAS MEASURED, and it is why this file exists. On a host whose rootless pause helper had gone stale,
the second run of the shipped worker install path failed:

    This worker will not serve: its limits do not bind.
    Error: crun: mount `proc` to `proc`: Operation not permitted: OCI permission denied

The install path has a repair for exactly that, named out loud in its own comments: restart
`agentnode-worker-runtime.service`, whose whole job is `podman system migrate`, and try the worker once
more. On the measured host that repair NEVER RAN -- its message does not appear anywhere in the install's
own 163-line log -- and the install died instead. Rebuilding the helper by hand afterwards made the
worker serve again in three seconds, so the repair would have worked.

THE CAUSE, in one line of shell. The guard is

    if journalctl -u agentnode-worker.service -n 60 --no-pager 2>/dev/null \\
         | grep -q 'limits do not bind'; then

in a script that runs under `set -euo pipefail`. `grep -q` exits as soon as it matches; the match is
near the start of sixty journal lines, so `journalctl` is still writing when the pipe closes and dies of
SIGPIPE. Measured on the host:

    pipestatus: 141 0

`grep` found it -- 0 -- and `journalctl` was killed -- 141 -- and `pipefail` takes the leftmost failure,
so the condition is FALSE precisely because the thing it was looking for was there. The more certainly
the phrase is present, the earlier grep exits, and the more reliably the guard does not fire.

WHY A TEST AND NOT JUST A FIX. A guard that cannot fire looks exactly like a guard that was not needed,
in every log, forever. Only the shape is checkable, so the shape is what is pinned here.
"""
from __future__ import annotations

import pathlib
import re
import subprocess

import pytest

HERE = pathlib.Path(__file__).resolve()
DEPLOY = HERE.parent.parent / "deploy" / "separate-worker-host"
INSTALL = DEPLOY / "install-worker-host.sh"

#: The condition the repair is guarded by, as the product words it.
THE_CONDITION = "limits do not bind"

#: The repair itself. If a change makes the guard fireable by deleting what it guards, these have to go
#: red too, so they are asserted separately.
THE_REPAIR = "agentnode-worker-runtime.service"


def the_script() -> str:
    assert INSTALL.is_file(), "the worker install path is not where this test looks: %s" % INSTALL
    return INSTALL.read_text(encoding="utf-8")


def test_the_install_path_really_does_run_under_pipefail():
    """The premise. Without pipefail the shape below is harmless, so the premise is pinned first."""
    assert re.search(r"^set -[a-z]*o pipefail|^set -euo pipefail", the_script(), re.M), (
        "this test is about a trap that only exists under pipefail, and the script no longer sets it"
    )


def test_the_self_repair_guard_is_not_a_pipeline_that_can_sigpipe():
    """The defect. The guard must not be `<producer> | grep -q`."""
    text = the_script()
    # The guard, as one logical line: backslash-newline continuations joined, so a condition split over
    # two lines is read the way the shell reads it rather than the way a file happens to be wrapped.
    joined = text.replace("\\\n", " ")
    # EVERY line that mentions the phrase, not only the ones beginning with `if`. The first version
    # looked for `if`/`elif`/`while` and went red after the repair moved the test into a `case` --
    # correctly, because its own vacuity guard below refuses to pass when it cannot find the condition
    # at all. A finder narrow enough to miss a legitimate rewrite is a finder that will one day miss
    # the defect coming back in a shape it does not recognise.
    # COMMENTS ARE NOT GUARDS. The repair's own comment quotes the broken pipeline, to say what it was
    # and why it could not work, and the widened finder above matched that quotation -- so the test went
    # red at the sentence explaining the fix. A shell comment cannot be a condition, so lines whose
    # first character is `#` are not read as one.
    guards = [line.strip() for line in joined.splitlines()
              if THE_CONDITION in line and not line.strip().startswith("#")]
    assert guards, (
        "nothing in the install path tests for %r any more, so either the repair is gone or it is "
        "guarded somewhere this test cannot see" % THE_CONDITION
    )
    for guard in guards:
        assert not re.search(r"\|\s*grep\s+-[a-zA-Z]*q", guard), (
            "a guard under pipefail that can never fire: %r. grep -q exits on the first match, the "
            "producer on its left is still writing, it dies of SIGPIPE, and pipefail takes that as the "
            "pipeline's status -- so the condition is false exactly when the phrase IS present. Read "
            "the producer's output once, into a variable or a file, and test that." % guard
        )


def test_the_repair_the_guard_guards_is_still_there():
    """Must stay green. A guard made fireable by deleting the repair is not a fix."""
    text = the_script()
    assert THE_REPAIR in text, (
        "the install path no longer restarts %s, which is the repair for a stale rootless namespace "
        "and the only thing the guard above exists to reach" % THE_REPAIR
    )


@pytest.mark.skipif(not pathlib.Path("/bin/bash").exists(), reason="needs a POSIX shell")
def test_the_mechanism_itself_so_the_reason_is_in_the_suite():
    """Not about our code: that this trap is real, with the shell that runs the install path.

    A producer that keeps writing after an early match, piped into `grep -q`:
      * under pipefail the condition is false although grep matched;
      * without pipefail it is true.
    If a future shell stops killing the producer, this goes red and the pin above can be revisited --
    which is better than carrying a rule whose reason has quietly expired.
    """
    # PIPESTATUS IS READ INSIDE THE BRANCH, and the first version of this test is why. It wrote the
    # pipeline a second time, bare, to read PIPESTATUS after it -- and a bare failing pipeline under
    # `set -e` ENDS THE SHELL, so the shell exited 141 and never printed the line this test asserts on.
    # The test had never run: on this workstation there is no /bin/bash, so it was skipped, and the
    # first machine to execute it was CI, which went red. Measured both ways afterwards:
    #
    #   the shape as written : DID-NOT-FIRE            shell exit 141   (no pipestatus line at all)
    #   the shape below      : DID-NOT-FIRE pipestatus 141 0   shell exit 0
    #
    # Reading PIPESTATUS as the first thing inside the branch gets the CONDITION's statuses, because
    # `then` and `else` run no pipeline of their own and an assignment is not a pipeline.
    shape = (
        "if seq 1 200000 | grep -q '^5$'; then PS=\"${PIPESTATUS[*]}\"; "
        "echo \"FIRED pipestatus $PS\"; else PS=\"${PIPESTATUS[*]}\"; "
        "echo \"DID-NOT-FIRE pipestatus $PS\"; fi"
    )
    with_pipefail = subprocess.run(["/bin/bash", "-c", "set -euo pipefail; " + shape],
                                   capture_output=True, text=True)
    without = subprocess.run(["/bin/bash", "-c", "set -eu; set +o pipefail; " + shape],
                             capture_output=True, text=True)
    assert "DID-NOT-FIRE" in with_pipefail.stdout, (
        "under pipefail this shape fired, so the trap this file pins is not present in this shell: %r"
        % with_pipefail.stdout
    )
    assert "pipestatus 141 0" in with_pipefail.stdout, (
        "the producer was expected to die of SIGPIPE while grep matched; it said %r"
        % with_pipefail.stdout
    )
    assert "FIRED" in without.stdout and "DID-NOT-FIRE" not in without.stdout, (
        "without pipefail the same shape must fire, or the difference is not pipefail: %r"
        % without.stdout
    )
