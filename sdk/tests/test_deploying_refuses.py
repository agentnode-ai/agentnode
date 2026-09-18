"""The deployment refuses, and a test says so rather than a person having run it once.

R3 asks whether a deployment carrying the wrong interpreter, an artefact whose digest does not
match, or an artefact not built from the expected commit is refused BEFORE the running service is
touched. `exercise_refusals.sh` shows that happening on the alpha; this shows it in the suite, so
R9 can remove one mechanism at a time and name a test that goes red.

Every case here stops in the script's checking phase. None of them reaches `systemctl`, and the
test that proves that is `test_no_refusal_ever_reaches_the_running_service`.
"""
from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys
import zipfile

import pytest

SCRIPT = pathlib.Path(__file__).resolve().parent.parent / "deploy" / "deploy-pinned.sh"
COMMIT = "04a528ed91015500264335fcead6d5600677775a"
OTHER = "3cf3cb9fcfd4eccd84bee60651248f460a6f7ddd"


def _bash():
    found = shutil.which("bash")
    if not found:
        pytest.skip("no bash on this machine, so the deployment script cannot be driven here")
    return found


def _a_wheel(tmp_path, commit=COMMIT, version="0.0.0"):
    # A VALID WHEEL FILENAME. These used to end "-any-w.whl", which pip rejects outright --
    # so every counter-check that removed a refusal still ended non-zero, at pip, for a reason
    # that had nothing to do with the mechanism removed. `ALPHA-RUNTIME-PIN-0003` caught that.
    # The name is valid now and the install is inert (see `_run`), so what a counter-check
    # changes is the refusal and nothing else.
    wheel = tmp_path / ("agentnode_sdk-%s-py3-none-any.whl" % version)
    with zipfile.ZipFile(wheel, "w") as z:
        z.writestr("agentnode_sdk/__init__.py", "")
        if commit is not None:
            z.writestr("agentnode_sdk/_provenance.json", json.dumps({"commit": commit}))
    return wheel


def _run(tmp_path, wheel, commit, venv=None, env=None):
    """Drive the script. `venv` defaults to the interpreter running this test."""
    import os

    where = dict(os.environ)
    where.update(env or {})
    where["AGENTNODE_STATE"] = str(tmp_path / "state")
    where["AGENTNODE_WORKER_PIN_DIR"] = str(tmp_path / "pin")
    # THIS TEST MUST NOT BE ABLE TO OPERATE THE MACHINE IT RUNS ON, and without this line it
    # could and did: a counter-check removed one of the refusals, the script ran on past the
    # checks into the step that stops the services, and the closed alpha went down while it was
    # serving. The script calls whatever this names; the suite names something inert.
    where.setdefault("AGENTNODE_SYSTEMCTL", "true" if shutil.which("true") else "/bin/true")
    # AND THE SUITE MUST NOT BE ABLE TO INSTALL ANYTHING EITHER. With a refusal removed the
    # script runs on into the install, and that install would put a stand-in wheel over the real
    # agentnode_sdk in the environment running these tests. Until now it could not, only because
    # the stand-in filenames were invalid -- protection by accident, and it also made the
    # counter-check evidence unreadable. Inert here, real in a deployment.
    where.setdefault("AGENTNODE_PIP", "true" if shutil.which("true") else "/bin/true")
    where.setdefault("AGENTNODE_RUNUSER", "true" if shutil.which("true") else "/bin/true")
    return subprocess.run(
        [_bash(), str(SCRIPT), str(wheel), commit, venv or sys.prefix],
        capture_output=True, text=True, env=where, timeout=120)


def _running_is_312():
    return sys.version_info[:2] == (3, 12)


class TestTheInterpreterIsCheckedFirstAndBeforeAnything:

    def test_a_venv_that_is_not_the_tested_family_is_refused(self, tmp_path):
        """Pointed at a directory that is not a 3.12 venv at all."""
        nowhere = tmp_path / "not-a-venv"
        (nowhere / "bin").mkdir(parents=True)
        said = _run(tmp_path, _a_wheel(tmp_path), COMMIT, venv=str(nowhere))
        assert said.returncode != 0
        assert "REFUSED (interpreter)" in said.stdout, said.stdout[-500:]

    def test_a_venv_that_is_a_different_python_family_is_refused(self, tmp_path):
        """A DIFFERENT mechanism from the one above, and the reason it has its own test: "there is
        no python there" and "the python there is 3.11" are two checks, and a counter-check that
        removes the family comparison must make something go red. The test above does not depend
        on it -- its directory has no interpreter at all, so the first check answers.
        """
        import os
        import stat

        venv = tmp_path / "a-real-looking-venv"
        (venv / "bin").mkdir(parents=True)
        fake = venv / "bin" / "python"
        fake.write_text("#!/bin/sh\necho 3.11.9\n", encoding="utf-8")
        os.chmod(fake, os.stat(fake).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

        said = _run(tmp_path, _a_wheel(tmp_path), COMMIT, venv=str(venv))
        assert said.returncode != 0
        assert "REFUSED (interpreter)" in said.stdout, said.stdout[-600:]
        assert "3.11.9" in said.stdout and "tested on 3.12" in said.stdout

    def test_and_it_says_what_to_do_about_it(self, tmp_path):
        nowhere = tmp_path / "still-not-a-venv"
        (nowhere / "bin").mkdir(parents=True)
        said = _run(tmp_path, _a_wheel(tmp_path), COMMIT, venv=str(nowhere))
        assert "python3.12 -m venv" in said.stdout


class TestTheArtefactIsCheckedByItsDigest:

    def test_a_wheel_that_is_not_there_is_refused(self, tmp_path):
        said = _run(tmp_path, tmp_path / "nothing.whl", COMMIT)
        assert said.returncode != 0
        assert "REFUSED (artefact)" in said.stdout

    @pytest.mark.skipif(not _running_is_312(), reason="the interpreter check answers first")
    def test_a_digest_that_is_not_the_expected_one_is_refused(self, tmp_path):
        said = _run(tmp_path, _a_wheel(tmp_path), COMMIT,
                    env={"AGENTNODE_EXPECT_DIGEST": "b" * 64})
        assert said.returncode != 0
        assert "REFUSED (artefact)" in said.stdout
        assert "which is exactly why the digest is what is compared" in said.stdout


class TestTheCommitComesOutOfTheArtefact:

    @pytest.mark.skipif(not _running_is_312(), reason="the interpreter check answers first")
    def test_a_wheel_that_does_not_say_where_it_came_from_is_refused(self, tmp_path):
        said = _run(tmp_path, _a_wheel(tmp_path, commit=None), COMMIT)
        assert said.returncode != 0
        assert "REFUSED (commit)" in said.stdout
        assert "does not say which source it was built from" in said.stdout

    @pytest.mark.skipif(not _running_is_312(), reason="the interpreter check answers first")
    def test_a_wheel_built_from_another_commit_is_refused(self, tmp_path):
        """The finding this exists for: the commit used to be whatever the caller said."""
        said = _run(tmp_path, _a_wheel(tmp_path, commit=OTHER), COMMIT)
        assert said.returncode != 0
        assert "REFUSED (commit)" in said.stdout
        assert OTHER[:12] in said.stdout and COMMIT[:12] in said.stdout

    @pytest.mark.skipif(not _running_is_312(), reason="the interpreter check answers first")
    def test_and_the_out_of_band_expectation_still_refuses_too(self, tmp_path):
        """Two independent statements, both checked. The wheel is internally consistent here and
        is still not the one this deployment was told to install."""
        said = _run(tmp_path, _a_wheel(tmp_path, commit=OTHER), OTHER,
                    env={"AGENTNODE_EXPECT_COMMIT": COMMIT})
        assert said.returncode != 0
        assert "REFUSED (commit)" in said.stdout


class TestTheSuiteCannotOperateTheMachine:

    @pytest.mark.parametrize("command,variable",
                             [("systemctl", "SYSTEMCTL="),
                              ("runuser", "RUNUSER=")])
    def test_the_script_calls_what_it_is_told_to_call(self, command, variable):
        """The guard itself. `deploy-pinned.sh` must never name these at a call site -- only in
        the default of their one variable -- or a test that forgets to override one operates the
        host. `pip` is the third of the same kind and is checked below, separately, because it is
        reached through a path rather than by name."""
        said = SCRIPT.read_text(encoding="utf-8")
        for number, line in enumerate(said.splitlines(), 1):
            bare = line.strip()
            if bare.startswith("#") or bare.startswith(variable):
                continue
            assert not bare.startswith(command + " "), (
                "deploy-pinned.sh line %d calls %s directly: %r" % (number, command, bare))

    def test_and_it_installs_only_through_the_one_variable(self):
        """`pip` is invoked as `$VENV/bin/pip`, so the guard above cannot see it. What is checked
        instead is that exactly one line builds that path, and that the install uses the variable.

        This exists because the suite could install: with a refusal removed the script runs on
        into the install, and until `AGENTNODE_PIP` there was nothing to stop a stand-in wheel
        going over the real package. It never did, only because those stand-ins had filenames pip
        rejects -- which also made the counter-check evidence unreadable."""
        said = SCRIPT.read_text(encoding="utf-8")
        builds = [ln for ln in said.splitlines()
                  if "bin/pip" in ln and not ln.strip().startswith("#")]
        assert builds == ['PIP="${AGENTNODE_PIP:-$VENV/bin/pip}"'], (
            "more than one line reaches for pip, or the one that does changed: %r" % builds)
        installs = [ln.strip() for ln in said.splitlines()
                    if " install " in ln and not ln.strip().startswith("#")]
        assert installs and all(ln.startswith('"$PIP"') for ln in installs), (
            "something installs without going through $PIP: %r" % installs)

    @pytest.mark.skipif(not _running_is_312(),
                        reason="the interpreter check answers first on 3.10 and 3.11, so nothing "
                               "here gets as far as the step this is about")
    def test_and_a_run_that_gets_past_the_checks_still_touches_nothing(self, tmp_path):
        """Deliberately NOT a refusal: a wheel whose provenance agrees, so the script runs on
        past every check. The point is that it then operates nothing -- the step that stops the
        services ran `true`, and so did the install."""
        said = _run(tmp_path, _a_wheel(tmp_path), COMMIT)
        assert "3. the previous installation stays up" in said.stdout, said.stdout[-400:]
        assert "the installer refused the wheel" not in said.stdout, (
            "the inert installer refused, so this run did not actually get past the install and "
            "the counter-checks below would be measuring pip rather than the pin")


class TestNothingRunningIsTouchedByARefusal:

    @pytest.mark.skipif(not _running_is_312(), reason="the interpreter check answers first")
    @pytest.mark.parametrize("case", ["no-provenance", "other-commit", "wrong-digest"])
    def test_no_refusal_ever_reaches_the_running_service(self, tmp_path, case):
        """The ORDER is the property R3 asks about: a deployment that is going to fail must leave
        the previous one serving. `systemctl stop` lives in step 4; every refusal above is in
        steps 0 to 2, so a refused deployment never prints step 3's heading."""
        wheel = _a_wheel(tmp_path, commit={"no-provenance": None}.get(case, OTHER))
        env = {"AGENTNODE_EXPECT_DIGEST": "c" * 64} if case == "wrong-digest" else {}
        said = _run(tmp_path, wheel, COMMIT, env=env)
        assert said.returncode != 0
        assert "3. the previous installation stays up" not in said.stdout, (
            "a refusal got as far as the step that stops the service")
        assert "systemctl" not in said.stdout
