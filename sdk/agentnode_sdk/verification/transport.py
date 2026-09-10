"""How work reaches the far machine: as bytes, over stdin, with nothing path-like on a command line.

Carried over from what the previous arc established, deliberately and unchanged in effect. Two
external runs paid for these two rules and neither of them is the rule that failed:

`EM3C-E3-CLASSIFY-0001` -- an absolute POSIX path as the last ssh argument was rewritten by the
MSYS layer before ssh.exe was started, so the far side was asked about a directory that does not
exist there. The far side is given no command at all now: the login shell reads its work from
stdin, and the only remaining argument that could be rewritten is refused if it is path-like.

`EM3C-E4-CLASSIFY-0001` -- `subprocess.run(input=<str>, text=True)` on Windows writes through a
wrapper that turns every LF into CRLF, and the far side's shell received `$'hostname\\r'`. Nothing
here is text: the script is encoded once, deliberately, to an encoding this end chose, and both
streams come back as bytes and are decoded once, deliberately.

What is NOT here, and is the reason this package exists: any way to ask this channel about a run.
`EM3C-E6-RECORD-0001` found the previous tool grepping the gateway's log file for a sandbox job's
output. A shell on that machine can say what the machine is. It cannot say what a run printed, and
there is nothing here that lets anybody ask it to.
"""
from __future__ import annotations

import os
import subprocess

#: Everything that crosses to the far machine and everything that comes back, in this encoding.
#: Chosen here and applied by hand, because the alternative is whatever the platform would have
#: chosen -- and on Windows that includes rewriting every line ending on the way out.
WIRE = "utf-8"

#: A marker every answer ends with, so an empty answer and a truncated one are different.
MARKER = "V2-END-OF-ANSWER"


def decode(raw) -> str:
    """Bytes from the far side, read as the encoding this end chose."""
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    return raw.decode(WIRE, "replace")


def path_like(argv) -> list[str]:
    """Which of these arguments have the shape a shell rewrites. Says nothing about where."""
    return [a for a in argv if a.startswith("/") or a.startswith("\\\\")]


def launch(argv, timeout=600.0, script=None):
    """Run a command and always return (exit_code, stdout, stderr, error_class). Never raises."""
    payload = None if script is None else script.encode(WIRE)
    try:
        done = subprocess.run(list(argv), capture_output=True,
                              timeout=timeout, check=False, input=payload)
        return done.returncode, decode(done.stdout), decode(done.stderr), ""
    except FileNotFoundError as exc:
        return None, "", str(exc), "FileNotFoundError"
    except subprocess.TimeoutExpired as exc:
        return None, decode(exc.stdout), decode(exc.stderr), "TimeoutExpired"
    except OSError as exc:
        return None, "", str(exc), type(exc).__name__


def one_command(command: str) -> str:
    """A script that runs ONE command, keeps that command's own status, and says it finished.

    Not `cmd; echo MARKER`: the status of that is the echo's. The command's status is taken first,
    the marker is printed, and the script exits with the status that was taken.
    """
    return (command + chr(10)
            + "__status=$?" + chr(10)
            + "printf '%s" + chr(92) + "n' " + repr(MARKER).replace("'", '"') + chr(10)
            + "exit $__status" + chr(10))


class OverSsh:
    """A shell on the far machine, reached with nothing on the command line a shell would rewrite.

    `ask` returns `(ran, exit_code, stdout, stderr)`, which is the shape a channel wants: a
    command that could not run at all and a command that ran and found nothing are different
    answers, and collapsing them is what `EM3C-EVIDENCE-0002` cost an external run to.
    """

    def __init__(self, settings, timeout: float = 300.0) -> None:
        self.key = settings.ssh_key
        self.server = settings.server
        self.timeout = timeout

    def argv(self) -> list[str]:
        return ["ssh", "-T", "-i", self.key, "-o", "BatchMode=yes", "-o", "ConnectTimeout=20",
                "-o", "StrictHostKeyChecking=yes", self.server]

    def rewritable(self) -> list[str]:
        """Arguments this platform's shell could rewrite. Empty on a platform that rewrites none."""
        if os.name != "nt":
            return []
        return path_like(self.argv())

    def ask(self, command: str, timeout: float | None = None):
        """One command on the far machine. Returns (ran, exit_code, stdout, stderr)."""
        rewritable = self.rewritable()
        if rewritable:
            return False, None, "", ("this run would be sent with an argument a shell could "
                                     "rewrite: " + ", ".join(rewritable))
        code, out, err, trouble = launch(
            self.argv(), timeout=self.timeout if timeout is None else timeout,
            script=one_command(command))
        if trouble:
            return False, None, out, (err or trouble)
        if MARKER not in out:
            return False, code, out, (err or "the answer stopped before the far side said it had "
                                             "finished, so what came back is a piece of one")
        return True, code, out.replace(MARKER + "\n", "").replace(MARKER, ""), err
