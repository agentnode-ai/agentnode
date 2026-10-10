"""A host whose installation was interrupted has to be able to SAY it is not ready.

WHAT WAS MEASURED, by the third beta acceptance and not by a thought experiment. The control-plane install
genuinely failed part-way through that run, leaving the package installed and its dependencies not. The
readiness path was then asked -- `agentnode gateway doctor`, which is the published way to ask -- and it
answered:

    ModuleNotFoundError: No module named 'httpx'

That is `PR7` of the beta profile at WARN: *"A partial failure leaves no host that looks ready. After an
interrupted run, the preflight or readiness path reports the host as not ready, and the evidence shows
which step was interrupted."* The safety half held -- a traceback is not a host that looks ready -- and the
second clause did not: a traceback shows which import failed, which is not the same statement as which
step was interrupted, and it is not something an operator's tooling can read.

WHERE IT COMES FROM, and it is three lines deep. `cli/main.py` does `import agentnode_sdk`, and
`agentnode_sdk/__init__.py` imports the whole public API -- the client, the async client and the installer
-- each of which imports `httpx` at module scope. Importing ANY `agentnode_sdk.*` name executes that
`__init__`, so on a host whose dependencies never arrived the entire CLI dies before argparse exists.
There is no command to reach, including the one whose job is to say what is wrong.

HOW THESE TESTS ARRANGE IT. `httpx` is blocked by a meta-path finder in a SUBPROCESS, so the absence is
real for the whole import graph rather than patched into one module. Nothing here uninstalls anything.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

#: Installed at the front of `sys.meta_path` in the child, so `import httpx` raises exactly what it
#: raises on a host whose dependencies were never installed -- including the `name` attribute, which
#: is what the repair reads to say WHICH package is missing.
BLOCK = "\n".join([
    "import sys",
    "class Blocked:",
    "    def find_spec(self, name, path=None, target=None):",
    "        if name == %r or name.startswith(%r):" % ("httpx", "httpx."),
    "            raise ModuleNotFoundError(\"No module named 'httpx'\", name='httpx')",
    "        return None",
    "sys.meta_path.insert(0, Blocked())",
])


def _in_a_child(body: str, *, block_httpx: bool = True):
    """Run `body` in a fresh interpreter, with or without `httpx` reachable."""
    code = (BLOCK + "\n" if block_httpx else "") + body
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run([sys.executable, "-c", code], stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, env=env, timeout=300)


def _said(done) -> str:
    return done.stdout.decode("utf-8", "replace")


class TestThePackageItselfSurvivesAnIncompleteInstallation:

    def test_1_importing_the_package_does_not_raise_when_a_dependency_is_missing(self):
        done = _in_a_child("import agentnode_sdk; print('imported', agentnode_sdk.__version__)")
        said = _said(done)
        assert done.returncode == 0, (
            "importing the package on an incomplete installation raised instead of reporting it:\n"
            + said[-1500:])
        assert "imported" in said, said[-500:]

    def test_2_and_it_says_which_package_is_missing(self):
        done = _in_a_child(
            "import agentnode_sdk\n"
            "print('MISSING=' + agentnode_sdk.what_this_installation_is_missing())")
        said = _said(done)
        assert "MISSING=httpx" in said, (
            "the package did not name the missing dependency; it said:\n" + said[-1500:])

    def test_3_and_a_missing_module_of_our_own_still_raises(self):
        """The guard is about an INCOMPLETE INSTALLATION, not about a broken build.

        A missing third-party package means the dependencies never arrived. A missing
        `agentnode_sdk` submodule means this build is wrong, and swallowing that would hide the
        one class of error that must never be hidden.
        """
        done = _in_a_child(
            "import sys\n"
            "class Ours:\n"
            "    def find_spec(self, name, path=None, target=None):\n"
            "        if name == 'agentnode_sdk.client':\n"
            "            raise ModuleNotFoundError('No module named %r' % name,"
            " name='agentnode_sdk.client')\n"
            "        return None\n"
            "sys.meta_path.insert(0, Ours())\n"
            "import agentnode_sdk\n",
            block_httpx=False)
        said = _said(done)
        assert done.returncode != 0, (
            "a missing module of our own was swallowed as an incomplete installation:\n"
            + said[-1500:])
        assert "agentnode_sdk.client" in said, said[-1000:]

    def test_4_and_using_the_part_that_is_missing_says_why(self):
        """A stub that fails silently would be worse than the traceback it replaced."""
        done = _in_a_child(
            "import agentnode_sdk\n"
            "try:\n"
            "    agentnode_sdk.AgentNode()\n"
            "except Exception as exc:\n"
            "    print('REFUSED=' + type(exc).__name__ + ': ' + str(exc))\n")
        said = _said(done)
        assert "REFUSED=" in said, (
            "using a name that needs the missing package did not refuse with a reason:\n"
            + said[-1500:])
        assert "httpx" in said, (
            "the refusal did not name the missing package: " + said[-600:])


class TestTheReadinessPathAnswersInsteadOfDying:

    def _doctor(self, tmp_path, *extra, block_httpx=True):
        body = "\n".join([
            "from agentnode_sdk.cli import main as _main",
            "import sys",
            "sys.argv = ['agentnode', 'gateway', 'doctor', '--dir', %r] + list(%r)"
            % (str(tmp_path), list(extra)),
            "raise SystemExit(_main.main(sys.argv[1:]))",
        ])
        return _in_a_child(body, block_httpx=block_httpx)

    def test_5_the_doctor_reports_not_ready_rather_than_a_traceback(self, tmp_path):
        done = self._doctor(tmp_path)
        said = _said(done)
        assert "Traceback" not in said, (
            "the readiness path answered with a traceback:\n" + said[-2000:])
        assert "ModuleNotFoundError" not in said, (
            "the readiness path answered with a traceback:\n" + said[-2000:])
        assert done.returncode != 0, (
            "the readiness path said nothing was wrong on an incomplete installation")
        # AND IT MUST BE ABOUT THIS. A counter-check that removed the doctor's own check left this
        # test GREEN: with the check gone the command reached its ordinary path and answered "No
        # gateway is set up here" -- non-zero, traceback-free, and the wrong answer. "Not a
        # traceback" is not the same statement as "a report of what is wrong", and B1 asks for the
        # second one.
        assert "installation never finished" in said or "httpx" in said, (
            "the readiness path did not report the incomplete installation; it answered about "
            "something else entirely:\n" + said[-2000:])

    def test_6_and_it_names_the_missing_step_and_a_next_step(self, tmp_path):
        said = _said(self._doctor(tmp_path))
        # THE SHAPE BEFORE THE CONTENT. `httpx` appears in the traceback too, so asking whether the
        # output names the missing package tells us nothing until we know the output is a report and
        # not a traceback. Asked the other way round, the red landed on the next-step clause and read
        # as "it named the package but gave no next step", which was false in both halves.
        assert "Traceback" not in said, (
            "the report did not name the missing package, because it is not a report: it is a "
            "traceback\n" + said[-2000:])
        assert "httpx" in said, (
            "the report did not name the missing package:\n" + said[-2000:])
        assert "Next:" in said or "next" in said.lower(), (
            "the report did not give a next step:\n" + said[-2000:])

    def test_7_and_there_is_a_machine_readable_form_of_the_same_answer(self, tmp_path):
        """`PR7` asks for a report an operator's tooling can read, not only a person."""
        done = self._doctor(tmp_path, "--json")
        said = _said(done)
        start = said.find("{")
        assert start >= 0, (
            "the machine-readable report is not there at all; it said:\n" + said[-2000:])
        try:
            answer = json.loads(said[start:])
        except Exception as exc:                              # noqa: BLE001
            raise AssertionError(
                "the machine-readable report is not valid JSON (%s):\n%s"
                % (type(exc).__name__, said[-2000:]))
        assert answer.get("ready") is False, (
            "the machine-readable report did not say ready=false: %r" % answer)
        assert "httpx" in json.dumps(answer), (
            "the machine-readable report did not name the missing package: %r" % answer)
        assert answer.get("next_step"), (
            "the machine-readable report carried no next step: %r" % answer)

    def test_8_and_a_healthy_installation_is_unchanged(self, tmp_path):
        """Green before the repair as well as after: the repair must not change this path.

        With `httpx` reachable there is no incomplete installation, so the doctor reaches its
        ordinary answer about this machine -- which on a bare directory is that there is no
        gateway here. What matters is that the incomplete-installation report does NOT appear.
        """
        said = _said(self._doctor(tmp_path, block_httpx=False))
        assert "Traceback" not in said, said[-1500:]
        assert "httpx" not in said, (
            "a healthy installation was reported as missing a package:\n" + said[-1500:])


class TestTheWorkersPreflightAnswersToo:
    """`PR7` says "the preflight OR readiness path", and the worker's half is the preflight."""

    def test_9_the_worker_preflight_reports_not_ready_rather_than_a_traceback(self, tmp_path):
        body = "\n".join([
            "from agentnode_sdk.cli import main as _main",
            "raise SystemExit(_main.main(['worker', 'preflight']))",
        ])
        said = _said(_in_a_child(body))
        assert "Traceback" not in said, (
            "the worker's preflight answered with a traceback:\n" + said[-2000:])
        assert "httpx" in said, (
            "the worker's preflight did not name the missing package:\n" + said[-2000:])
