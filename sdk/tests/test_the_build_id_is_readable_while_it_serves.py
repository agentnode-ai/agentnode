"""The line that says which build is serving arrived four minutes late.

Measured on the pair: the gateway was started at 07:57:20 and its
`Running as managed-f7aade11d493+d3f22717fae2` reached the journal at 08:01:16 -- in the same
second it was stopped. Under systemd stdout is a pipe, so Python block-buffers it, and that
line sat in the buffer until the process exited and flushed it.

Why it matters beyond tidiness. That string is the only thing that says which build a running
service actually is; a version number explicitly is not, because a development wheel keeps its
number while its contents change. It is what an operator greps for, and it is what
`upgrade.sh` and `rollback.sh` compare to prove the right code came up. Both of them read a
line belonging to the OUTGOING process, because the incoming one had not flushed yet -- so the
upgrade reported the old build as serving, and the rollback reported that nothing had said
anything at all. Both were false, about operations that had worked.

The worker looked fine only by luck: it prints a lot of other flushed output immediately
after, which pushed this line out with it.

NOT A HOST-ISOLATION TEST: one process, one kernel.
"""
from __future__ import annotations

import inspect
import subprocess
import sys

from agentnode_sdk.gateway import runtime_pin


class TestTheDefaultSaysItAtOnce:

    def test_the_shared_rule_flushes(self):
        source = inspect.getsource(runtime_pin._said_at_once)
        assert "flush=True" in source

    def test_and_is_what_refuse_unless_pinned_uses_by_default(self):
        signature = inspect.signature(runtime_pin.refuse_unless_pinned)
        assert signature.parameters["say"].default is runtime_pin._said_at_once

    def test_neither_wrapper_hands_it_a_bare_print(self):
        """Both used to pass `say=print`, so fixing the default alone would have changed
        nothing on either surface that actually runs."""
        from agentnode_sdk.cli import gateway_commands, worker_commands

        for module in (gateway_commands, worker_commands):
            source = inspect.getsource(module._refuse_unless_pinned)
            assert "say=print" not in source

    def test_a_caller_can_still_supply_its_own(self):
        """The parameter exists so a surface can capture the text; that must keep working."""
        said = []
        runtime_pin.refuse_unless_pinned("/nowhere-at-all", "gateway", say=said.append)
        assert said, "nothing was said at all"


class TestItReallyReachesAPipeBeforeTheProcessEnds:
    """The unit tests above are about the code. This one is about the behaviour that broke:
    a line written to a PIPE, read by somebody else, before the writer exits."""

    def test_the_line_arrives_while_the_process_is_still_running(self):
        program = (
            "import sys, time\n"
            "sys.path.insert(0, %r)\n"
            "from agentnode_sdk.gateway import runtime_pin\n"
            "runtime_pin._said_at_once('  Running as managed-deadbeefcafe+0123456789ab')\n"
            "time.sleep(30)\n" % (str(__import__('pathlib').Path(
                runtime_pin.__file__).resolve().parent.parent.parent),)
        )
        child = subprocess.Popen([sys.executable, "-c", program],
                                 stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        try:
            # If the line were block-buffered this would block until the sleep ended.
            line = child.stdout.readline()
            assert "managed-deadbeefcafe" in line
        finally:
            child.kill()
            child.wait(timeout=10)

    def test_and_an_unflushed_print_does_not(self):
        """The control. Without this, the test above could pass on a platform that
        line-buffers pipes anyway, and would be proving nothing about the repair."""
        program = "import sys, time\nprint('  Running as managed-x+y')\ntime.sleep(30)\n"
        child = subprocess.Popen([sys.executable, "-c", program],
                                 stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        try:
            import threading

            got = []
            reader = threading.Thread(target=lambda: got.append(child.stdout.readline()))
            reader.daemon = True
            reader.start()
            reader.join(timeout=4.0)
            assert not got, (
                "an unflushed print reached the pipe straight away, so this platform "
                "buffers differently and the test above does not discriminate here")
        finally:
            child.kill()
            child.wait(timeout=10)
