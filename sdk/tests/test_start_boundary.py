"""How an external run is told where it is happening, checked across a real process boundary.

`EM3C-E5-CLASSIFY-0001`: the fifth external run exported three Linux paths in Git Bash and started
a Windows process. The MSYS layer rewrote them on the way, so the run asked a Linux machine about
``C:/Program Files/Git/home/em3ce1/em3c-state-e5``. Nothing noticed: the runner inspected its
command line, and its command line was fine. The values had already been changed before it existed.

Two things follow, and both are here.

The conversion is REPRODUCED, so that the rest of this file means something: a test sends a Linux
path through the same shell environment the fifth run used and shows it arriving changed. If that
test stopped failing to preserve the value, the correction below would be proving nothing.

And the correction is crossed for real: the launcher writes a configuration, the native Windows
interpreter starts the real entry point with only a local path and a digest, and the child prints
what it read. What is compared is what the CHILD received -- not a value inspected in the parent
before any process started, which is precisely where the fifth run still looked correct.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

import pytest

import agentnode_sdk  # noqa: F401  -- imported so `IMPORTABLE` can be derived
from agentnode_sdk.verification import config


#: The three paths the fifth external run was given, verbatim. Using the real ones means this
#: file fails on the real case rather than on a case chosen to be easy.
E5_PATHS = {
    "gateway_bin": "/home/em3ce1/venv/bin/agentnode",
    "gateway_state": "/home/em3ce1/em3c-state-e5",
    "gateway_log": "/home/em3ce1/gateway.log",
}
ON_WINDOWS = os.name == "nt"
#: The parent of the package, so a child started from anywhere can still import it. Not
#: `os.getcwd()`: a test that only works when pytest was started from one directory is a
#: test that says less than it appears to.
IMPORTABLE = os.path.dirname(os.path.dirname(os.path.abspath(
    sys.modules["agentnode_sdk"].__file__)))


def a_configuration(tmp_path, **changes) -> dict:
    document = {
        "version": config.CONFIG_VERSION,
        "client_home": str(tmp_path / "home"),
        "agentnode": str(tmp_path / "agentnode.exe"),
        "work": str(tmp_path / "work"),
        "evidence": str(tmp_path / "evidence.jsonl"),
        "ssh_key": str(tmp_path / "a-key"),
        "server": "root@a-gateway-machine",
        "gateway_user": "a-service-account",
        "gateway_port": "8099",
        **E5_PATHS,
    }
    document.update(changes)
    return document


def written(tmp_path, document) -> str:
    path = tmp_path / "run-config.json"
    path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    return str(path)


def git_bash() -> str:
    """The shell the fifth run was started from, if this machine has it."""
    for candidate in (shutil.which("bash"),
                      r"C:\Program Files\Git\bin\bash.exe",
                      r"C:\Program Files (x86)\Git\bin\bash.exe"):
        if candidate and os.path.exists(candidate):
            return candidate
    return ""


class TestTheConversionIsReal:
    """Without this, everything below could be passing because nothing was ever converted."""

    @pytest.mark.skipif(not ON_WINDOWS, reason="the conversion is a Windows shell's doing")
    def test_a_linux_path_through_the_shell_environment_arrives_changed(self):
        shell = git_bash()
        if not shell:
            pytest.fail("this Windows machine has no Git Bash, so the conversion this file "
                        "exists to prevent cannot be demonstrated here")
        reader = "import os,sys;sys.stdout.write(os.environ.get('EM3C_GATEWAY_STATE',''))"
        done = subprocess.run(
            [shell, "-lc",
             'EM3C_GATEWAY_STATE=%s "%s" -c "%s"'
             % (E5_PATHS["gateway_state"], sys.executable.replace("\\", "/"), reader)],
            capture_output=True, timeout=120)
        arrived = done.stdout.decode("utf-8", "replace").strip()
        assert arrived, done.stderr.decode("utf-8", "replace")[:400]
        assert arrived != E5_PATHS["gateway_state"], (
            "this shell did not convert the value, so this machine cannot demonstrate the "
            "failure the configuration boundary exists to prevent: " + arrived)
        assert config._DRIVE.match(arrived) or "Program Files" in arrived, arrived

    @pytest.mark.skipif(not ON_WINDOWS, reason="the conversion is a Windows shell's doing")
    def test_and_the_validation_would_have_refused_what_arrived(self):
        """The value that reached the fifth run, put through the check that now runs first."""
        with pytest.raises(config.ConfigError) as caught:
            config.check_remote_path(
                "gateway_state", "C:/Program Files/Git/home/em3ce1/em3c-state-e5")
        assert "drive letter" in str(caught.value)


class TestTheChildReceivesWhatWasWritten:
    """A real child, the real entry point, and what IT got -- not what the parent held."""

    def start(self, config_path, expect="", extra=(), env=None):
        argv = [sys.executable, "-m", "agentnode_sdk.tools.external_run",
                "--config", config_path, *extra]
        if expect:
            argv += ["--expect", expect]
        environment = dict(os.environ if env is None else env)
        environment["PYTHONPATH"] = IMPORTABLE
        return subprocess.run(argv, capture_output=True, timeout=300, env=environment)

    def test_the_three_paths_arrive_exactly(self, tmp_path):
        document = a_configuration(tmp_path)
        settings = config.parse(document)
        done = self.start(written(tmp_path, document), config.digest_of(settings),
                          extra=["--preflight"])
        out = done.stdout.decode("utf-8", "replace")
        assert done.returncode == 0, out + done.stderr.decode("utf-8", "replace")
        for name, value in E5_PATHS.items():
            assert ("%-20s : %s" % (name, value)) in out, (name, out)

    def test_they_arrive_in_the_command_the_far_machine_would_run(self, tmp_path):
        """Not only read: carried through to the bytes that would be sent."""
        document = a_configuration(tmp_path)
        settings = config.parse(document)
        done = self.start(written(tmp_path, document), config.digest_of(settings),
                          extra=["--preflight"])
        out = done.stdout.decode("utf-8", "replace")
        assert done.returncode == 0, out
        line = [ln.strip() for ln in out.splitlines() if all(c in "0123456789abcdef" for c in ln.strip())
                and len(ln.strip()) > 40]
        assert line, out
        script = bytes.fromhex(line[-1]).decode("utf-8")
        for value in E5_PATHS.values():
            if value != E5_PATHS["gateway_log"]:
                assert value in script, (value, script)
        assert "\r" not in script
        assert "C:" not in script and "Program Files" not in script

    def test_nothing_a_shell_could_rewrite_is_on_the_command_line(self, tmp_path):
        document = a_configuration(tmp_path)
        done = self.start(written(tmp_path, document),
                          config.digest_of(config.parse(document)), extra=["--preflight"])
        out = done.stdout.decode("utf-8", "replace")
        assert "arguments a shell could rewrite: none" in out, out

    @pytest.mark.skipif(not ON_WINDOWS, reason="the conversion is a Windows shell's doing")
    def test_the_same_values_survive_being_started_from_that_shell(self, tmp_path):
        """The whole point: Git Bash may hand over the local path, and the values are untouched."""
        shell = git_bash()
        if not shell:
            pytest.fail("this Windows machine has no Git Bash")
        document = a_configuration(tmp_path)
        settings = config.parse(document)
        path = written(tmp_path, document)
        done = subprocess.run(
            [shell, "-lc",
             '"%s" -m agentnode_sdk.tools.external_run --config "%s" --expect %s --preflight'
             % (sys.executable.replace("\\", "/"), path.replace("\\", "/"),
                config.digest_of(settings))],
            capture_output=True, timeout=300,
            env={**os.environ, "PYTHONPATH": IMPORTABLE})
        out = done.stdout.decode("utf-8", "replace")
        assert done.returncode == 0, out + done.stderr.decode("utf-8", "replace")[:400]
        for name, value in E5_PATHS.items():
            assert ("%-20s : %s" % (name, value)) in out, (name, out)


class TestTheRunRefusesBeforeItStarts:

    def start(self, config_path, expect="", env=None):
        argv = [sys.executable, "-m", "agentnode_sdk.tools.external_run",
                "--config", config_path, "--preflight"]
        if expect:
            argv += ["--expect", expect]
        environment = dict(os.environ if env is None else env)
        environment["PYTHONPATH"] = IMPORTABLE
        return subprocess.run(argv, capture_output=True, timeout=300, env=environment)

    @pytest.mark.parametrize("value,because", [
        ("C:/Program Files/Git/home/em3ce1/em3c-state-e5", "drive letter"),
        ("C:/home/em3ce1/state", "drive letter"),
        ("\\\\server\\share\\state", "backslash"),
        ("//server/share/state", "UNC"),
        ("home/em3ce1/state", "relative"),
        ("/home/em3ce1/state/", "normal form"),
        ("/mingw64/home/state", "mingw64"),
    ])
    def test_a_remote_path_that_is_not_one_stops_the_run(self, tmp_path, value, because):
        document = a_configuration(tmp_path, gateway_state=value)
        done = self.start(written(tmp_path, document))
        out = done.stdout.decode("utf-8", "replace")
        assert done.returncode == 2, out
        assert "will not start" in out and because in out, out
        assert "Nothing has run" in out

    def test_a_configuration_that_is_not_the_one_meant_stops_the_run(self, tmp_path):
        document = a_configuration(tmp_path)
        expected = config.digest_of(config.parse(document))
        document["gateway_state"] = "/home/em3ce1/somewhere-else"
        done = self.start(written(tmp_path, document), expected)
        out = done.stdout.decode("utf-8", "replace")
        assert done.returncode == 2 and "not the configuration that was meant" in out, out

    def test_an_unknown_field_stops_the_run(self, tmp_path):
        document = a_configuration(tmp_path)
        document["surprise"] = "something"
        done = self.start(written(tmp_path, document))
        assert done.returncode == 2
        assert "does not describe" in done.stdout.decode("utf-8", "replace")

    def test_a_repeated_field_stops_the_run(self, tmp_path):
        path = tmp_path / "run-config.json"
        body = json.dumps(a_configuration(tmp_path))
        path.write_text(body[:-1] + ', "gateway_state": "/home/elsewhere"}', encoding="utf-8")
        done = self.start(str(path))
        assert done.returncode == 2
        assert "more than once" in done.stdout.decode("utf-8", "replace")

    def test_the_old_way_of_telling_it_stops_the_run(self, tmp_path):
        """`EM3C-E5-CLASSIFY-0001`: refused, not ignored. A setting that is quietly ignored is
        one whose author believes it took effect."""
        document = a_configuration(tmp_path)
        done = self.start(written(tmp_path, document),
                          env={**os.environ, "EM3C_GATEWAY_STATE": "/home/em3ce1/elsewhere"})
        out = done.stdout.decode("utf-8", "replace")
        assert done.returncode == 2 and "EM3C_GATEWAY_STATE" in out, out
        assert "rewrote three of them" in out

    def test_a_configuration_that_is_not_there_stops_the_run(self, tmp_path):
        done = self.start(str(tmp_path / "no-such-file.json"))
        assert done.returncode == 2
        assert "could not be read" in done.stdout.decode("utf-8", "replace")

    def test_a_good_configuration_does_start(self, tmp_path):
        """The control for all of those: they are refused for what they are, not because this
        refuses everything."""
        document = a_configuration(tmp_path)
        done = self.start(written(tmp_path, document),
                          config.digest_of(config.parse(document)))
        assert done.returncode == 0, done.stdout.decode("utf-8", "replace")


class TestNothingIsAskedOfTheShell:

    def test_no_conversion_exclusion_is_relied_on(self):
        import inspect

        from agentnode_sdk.tools import external_run as driver

        for module in (config, driver):
            assert "MSYS2_ARG_CONV_EXCL" not in inspect.getsource(module).replace(
                "`MSYS2_ARG_CONV_EXCL` is a", "")

    def test_the_runner_takes_nothing_from_the_environment(self):
        import inspect

        from agentnode_sdk.tools import external_run as driver

        source = inspect.getsource(driver)
        for name in config.SUPERSEDED_ENVIRONMENT:
            assert 'environ.get("%s"' % name not in source
            assert "environ[%r]" % name not in source
