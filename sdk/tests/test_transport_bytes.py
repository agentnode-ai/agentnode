"""What crosses to the far machine, checked against a real process on this one.

`EM3C-E4-CLASSIFY-0001`: the external run E4 died because `subprocess.run(input=<str>, text=True)`
on Windows writes through a wrapper that turns every LF into CRLF. The far side's shell received
`$'hostname\r'` and said so, and the whole observation channel went with it.

Every test that was supposed to establish "remote work arrives unchanged" stopped at the argument
handed to a replaced transport. The string never crossed a real process boundary, and the boundary
was where it was rewritten.

So these tests start a real child, hand it the real payload through the real code, and compare the
bytes that child received with the bytes that were meant to arrive. On Linux they pass either way
-- there is no translation to catch -- which is why they are also required on a Windows runner.
"""
from __future__ import annotations

import subprocess
import sys

import pytest

from agentnode_sdk.tools import external_run as driver


#: A child that answers with exactly what it was given, as bytes, and never as text.
ECHO = "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())"
#: A child that answers with the hex of what it was given, so a difference is visible as one.
HEX = "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read().hex().encode())"


def a_child(code: str) -> list[str]:
    return [sys.executable, "-c", code]


class TestTheBytesArriveAsTheyWereSent:

    def test_a_newline_is_a_newline(self):
        """The defect itself, in one line. On Windows this failed before the correction."""
        sent = "one\ntwo\n"
        code, out, err, cls = driver.launch(a_child(HEX), script=sent)
        assert code == 0 and cls == "" and err == ""
        assert out == sent.encode("utf-8").hex(), out
        assert "0d" not in out, "a carriage return was added on the way"

    def test_the_script_a_remote_step_sends_arrives_whole(self):
        script = driver.one_command("ls -la /home/a-service-account/em3c-state")
        code, out, _err, _cls = driver.launch(a_child(HEX), script=script)
        assert code == 0
        assert bytes.fromhex(out) == script.encode("utf-8")

    def test_the_path_in_it_is_the_path_that_was_asked_for(self):
        path = "/home/a-service-account/em3c-state"
        code, out, _err, _cls = driver.launch(a_child(HEX), script=driver.one_command("ls " + path))
        assert code == 0
        assert path in bytes.fromhex(out).decode("utf-8")

    @pytest.mark.parametrize("payload", [
        "\n", "a\n\nb\n", "\r\n", "a\rb\n", "ä ö ü\n", "é\n", "tab\there\n",
    ])
    def test_whatever_is_sent_is_what_arrives(self, payload):
        """Including a carriage return that was MEANT: the rule is that nothing is changed,
        not that carriage returns are removed."""
        code, out, _err, _cls = driver.launch(a_child(HEX), script=payload)
        assert code == 0
        assert bytes.fromhex(out) == payload.encode("utf-8")

    def test_nothing_is_sent_when_there_is_nothing_to_send(self):
        code, out, _err, _cls = driver.launch(a_child(HEX))
        assert code == 0 and out == ""

    def test_what_comes_back_is_decoded_here_and_not_by_the_platform(self):
        """Both streams, as bytes, decoded once by this end."""
        code, out, err, _cls = driver.launch(a_child(
            "import sys;"
            "sys.stdout.buffer.write(b'out\\n');"
            "sys.stderr.buffer.write(b'err\\n')"))
        assert code == 0
        assert out == "out\n" and err == "err\n"
        assert "\r" not in out and "\r" not in err

    def test_output_that_is_not_utf8_does_not_stop_the_record(self):
        code, out, _err, cls = driver.launch(a_child(
            "import sys; sys.stdout.buffer.write(b'\\xff\\xfe ok\\n')"))
        assert code == 0 and cls == ""
        assert "ok" in out


class TestTheCodeItselfAsksForBytes:
    """Not only that it behaves: that nothing on this path asks the platform to help."""

    def test_no_subprocess_in_the_driver_asks_for_text(self):
        """Reading a FILE with an encoding is fine and deliberate. What must never happen is
        asking the platform to translate a stream that carries work to another machine."""
        import inspect
        import re

        source = inspect.getsource(driver)
        calls = re.findall(r"subprocess\.(?:run|Popen|call|check_output)\((?:[^()]|\([^()]*\))*\)",
                           source, re.S)
        assert calls, "no subprocess call was found at all"
        offending = [c for c in calls
                     if "text=" in c or "universal_newlines" in c or "encoding=" in c]
        assert not offending, offending

    def test_what_is_written_is_bytes_and_what_is_read_is_decoded_once(self):
        import inspect

        source = inspect.getsource(driver.launch)
        assert "script.encode(WIRE)" in source
        assert "decode(done.stdout)" in source and "decode(done.stderr)" in source

    def test_the_encoding_is_chosen_here(self):
        assert driver.WIRE == "utf-8"
        assert driver.decode(b"a\nb") == "a\nb"
        assert driver.decode(None) == ""


class TestTheChildReallyRan:
    """The control. Without it every test above would pass against a child that never started."""

    def test_the_echo_child_answers_what_it_is_given(self):
        done = subprocess.run(a_child(ECHO), input=b"proof\n", capture_output=True)
        assert done.returncode == 0 and done.stdout == b"proof\n"

    def test_a_child_that_fails_is_seen_to_fail(self):
        code, _out, _err, cls = driver.launch(a_child("raise SystemExit(3)"))
        assert code == 3 and cls == ""

    def test_a_child_that_is_not_there_is_an_outcome(self):
        code, _out, err, cls = driver.launch(["definitely-not-a-real-binary-xyz"])
        assert code is None and cls == "FileNotFoundError" and err
