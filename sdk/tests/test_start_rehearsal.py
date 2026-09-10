"""The whole start, rehearsed: launcher, configuration, runner, first remote command.

`EM3C-E5-CLASSIFY-0001` was not caught by any test because no test ran the sequence the run runs.
The pieces were each fine. What was wrong was between them, and between them was where nobody
looked.

So this looks there. The real launcher starts the real runner as a real child process, the runner
reads the real configuration and builds the command the far machine would really be given, and
what is compared is the bytes of that command against the three paths the configuration carried.
Nothing is stood in for except the far machine itself, which is never contacted: the rehearsal
stops at the point where those bytes would go onto the wire.

What that leaves outside: the network, the gateway, and the far machine's own answer. The class of
failure this exists to catch happens before any of those, which is exactly why it survived five
runs that all had them.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

from agentnode_sdk.verification import config
from agentnode_sdk.verification import launcher

#: The three the fifth external run was given, verbatim.
E5_PATHS = {
    "gateway_bin": "/home/em3ce1/venv/bin/agentnode",
    "gateway_state": "/home/em3ce1/em3c-state-e5",
    "gateway_log": "/home/em3ce1/gateway.log",
}
#: The parent of the package, so a child started from anywhere can still import it.
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


@pytest.fixture
def written(tmp_path):
    def write(document) -> str:
        path = tmp_path / "run-config.json"
        path.write_text(json.dumps(document, indent=2), encoding="utf-8")
        return str(path)
    return write


def rehearse(config_path, environ=None):
    """Launcher to runner to first remote command, for real, without a network."""
    base = dict(os.environ if environ is None else environ)
    base["PYTHONPATH"] = IMPORTABLE
    return launcher.start(config_path, ["--preflight"], environ=base, timeout=300)


def the_remote_command(out: str) -> str:
    """The bytes the runner said it would send, read back out of what it printed."""
    for line in out.splitlines():
        stripped = line.strip()
        if len(stripped) > 40 and all(c in "0123456789abcdef" for c in stripped):
            return bytes.fromhex(stripped).decode("utf-8")
    raise AssertionError("the run printed no remote command:\n" + out)


class TestTheSequenceRuns:

    def test_the_launcher_starts_the_runner_and_it_gets_that_far(self, tmp_path, written):
        code, out, err, settings, digest, argv = rehearse(written(a_configuration(tmp_path)))
        assert code == 0, out + err
        assert digest in " ".join(argv), argv
        assert "This run was told" in out

    def test_the_launcher_hands_over_a_path_and_a_digest_and_nothing_else(self, tmp_path,
                                                                         written):
        _, _, _, settings, digest, argv = rehearse(written(a_configuration(tmp_path)))
        assert argv[1:3] == ["-m", "agentnode_sdk.tools.external_run"]
        for value in E5_PATHS.values():
            assert not any(value in arg for arg in argv), (value, argv)
        assert digest in argv and len(digest) == 64

    def test_the_interpreter_it_uses_is_this_machines_own(self):
        assert launcher.native_interpreter() == sys.executable

    @pytest.mark.skipif(os.name != "nt", reason="only Windows has the other kind of path")
    def test_an_interpreter_named_the_way_a_shell_names_one_is_refused(self):
        with pytest.raises(launcher.LaunchError) as caught:
            launcher.native_interpreter("/usr/bin/python3")
        assert "not how this machine names a program" in str(caught.value)

    def test_a_run_is_not_started_without_a_digest(self):
        with pytest.raises(launcher.LaunchError):
            launcher.runner_argv("C:/x/run-config.json", "")


class TestTheThreePathsArriveWhereTheyAreUsed:

    def test_they_are_in_the_command_the_far_machine_would_run(self, tmp_path, written):
        code, out, err, _, _, _ = rehearse(written(a_configuration(tmp_path)))
        assert code == 0, out + err
        script = the_remote_command(out)
        assert E5_PATHS["gateway_bin"] in script
        assert E5_PATHS["gateway_state"] in script

    def test_exactly_as_the_configuration_wrote_them(self, tmp_path, written):
        """Not "contains something like it": the bytes are compared."""
        code, out, _, settings, _, _ = rehearse(written(a_configuration(tmp_path)))
        assert code == 0
        raw = the_remote_command(out).encode("utf-8")
        for name in ("gateway_bin", "gateway_state"):
            assert getattr(settings, name).encode("utf-8") in raw, name

    def test_and_nothing_of_this_machine_is_in_them(self, tmp_path, written):
        code, out, _, _, _, _ = rehearse(written(a_configuration(tmp_path)))
        assert code == 0
        script = the_remote_command(out)
        assert not config._DRIVE_ANYWHERE.search(script), script
        assert chr(92) not in script.replace(chr(92) + "n", ""), script
        assert "Program Files" not in script
        assert chr(13) not in script

    def test_the_run_also_says_which_configuration_it_read(self, tmp_path, written):
        _, out, _, settings, _, _ = rehearse(written(a_configuration(tmp_path)))
        assert config.digest_of(settings) in out

    @pytest.mark.parametrize("state", [
        "/home/em3ce1/em3c-state-e6",
        "/home/em3ce1/state with a space",
        "/opt/agentnode/state",
    ])
    def test_whatever_the_configuration_says_is_what_arrives(self, tmp_path, written, state):
        code, out, err, _, _, _ = rehearse(
            written(a_configuration(tmp_path, gateway_state=state)))
        assert code == 0, out + err
        assert state in the_remote_command(out)


class TestTheSequenceStopsWhenItShould:
    """Fail-closed, and closed BEFORE the first step rather than during it."""

    def test_a_converted_path_stops_it_at_the_launcher(self, tmp_path, written):
        path = written(a_configuration(
            tmp_path, gateway_state="C:/Program Files/Git/home/em3ce1/em3c-state-e5"))
        with pytest.raises(config.ConfigError) as caught:
            launcher.prepare(path, environ={})
        assert "drive letter" in str(caught.value)

    def test_and_the_runner_would_have_stopped_too_if_it_had_been_reached(self, tmp_path,
                                                                         written):
        """The launcher and the runner check separately, so neither is the only thing between a
        converted value and the wire."""
        path = written(a_configuration(tmp_path, gateway_state="C:/home/em3ce1/state"))
        import subprocess
        done = subprocess.run(
            [sys.executable, "-m", "agentnode_sdk.tools.external_run",
             "--config", path, "--preflight"],
            capture_output=True, timeout=300, env={**os.environ, "PYTHONPATH": IMPORTABLE})
        assert done.returncode == 2
        assert "drive letter" in done.stdout.decode("utf-8", "replace")

    def test_a_swapped_configuration_stops_it(self, tmp_path, written):
        document = a_configuration(tmp_path)
        digest = config.digest_of(config.parse(document))
        path = written(a_configuration(tmp_path, gateway_state="/home/em3ce1/elsewhere"))
        import subprocess
        done = subprocess.run(
            [sys.executable, "-m", "agentnode_sdk.tools.external_run",
             "--config", path, "--expect", digest, "--preflight"],
            capture_output=True, timeout=300, env={**os.environ, "PYTHONPATH": IMPORTABLE})
        assert done.returncode == 2
        assert "not the configuration that was meant" in done.stdout.decode("utf-8", "replace")

    def test_the_old_environment_stops_it_at_the_launcher(self, tmp_path, written):
        with pytest.raises(config.ConfigError) as caught:
            launcher.prepare(written(a_configuration(tmp_path)),
                             environ={"EM3C_GATEWAY_STATE": "/home/x"})
        assert "EM3C_GATEWAY_STATE" in str(caught.value)

    def test_nothing_of_the_old_environment_is_passed_on(self, tmp_path, written):
        """Even if this process itself has one set for some other reason, the child does not
        inherit it -- and would refuse if it did."""
        import subprocess
        path = written(a_configuration(tmp_path))
        settings, digest = launcher.prepare(path, environ={})
        argv = launcher.runner_argv(path, digest, ["--preflight"])
        child = {k: v for k, v in os.environ.items()}
        child["PYTHONPATH"] = IMPORTABLE
        for name in config.SUPERSEDED_ENVIRONMENT:
            child.pop(name, None)
        done = subprocess.run(argv, capture_output=True, timeout=300, env=child)
        assert done.returncode == 0, done.stdout.decode("utf-8", "replace")


class TestWhatTheRunnerChecksBeforeItStarts:
    """`check_start` is the runner's own last look, and it looks in four places because the
    fifth run proved that looking in one was looking in the wrong one."""

    def test_a_clean_start_finds_nothing(self, tmp_path):
        from agentnode_sdk.tools import external_run as driver

        driver.configure(config.parse(a_configuration(tmp_path)))
        assert driver.check_start(argv=["--config", "C:/x/run-config.json"]) == []

    def test_a_remote_value_on_its_own_command_line_is_refused(self, tmp_path):
        from agentnode_sdk.tools import external_run as driver

        driver.configure(config.parse(a_configuration(tmp_path)))
        found = driver.check_start(argv=["--state", E5_PATHS["gateway_state"]])
        assert found and "on this run's own command line" in found[0]

    def test_a_value_changed_after_it_was_read_is_refused(self, tmp_path):
        """Between reading the configuration and using it is its own boundary, so it is its own
        check. Nothing is trusted because it was fine earlier."""
        from agentnode_sdk.tools import external_run as driver

        driver.configure(config.parse(a_configuration(tmp_path)))
        driver.STATE = "C:/Program Files/Git/home/em3ce1/em3c-state-e5"
        try:
            found = driver.check_start(argv=[])
        finally:
            driver.configure(config.parse(a_configuration(tmp_path)))
        assert any("drive" in complaint for complaint in found), found
