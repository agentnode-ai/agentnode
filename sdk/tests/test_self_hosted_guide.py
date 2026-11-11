"""Task #10: the self-hosted guide must be followable as written.

``docs/self-hosted-sandbox.md`` is the installation guide for running your own gateway (a Linux
machine with Docker or Podman, and a laptop). Two things are checked:

* every ``agentnode ...`` command it shows exists in the real CLI with the flags it shows --
  parsed by the CLI's own parser, nothing is executed;
* a person following it on a pip installation is not stopped by a requirement the guide never
  mentions. Measured on 2026-11-06: after ``agentnode gateway init``, ``agentnode gateway start``
  answers "Not started. This gateway has no runtime pin ..." and points to
  ``deploy/deploy-pinned.sh``, which is not part of the installed package; the guide says
  nothing about a runtime pin.

The CLI runs in a temporary directory on loopback; no container runtime is needed or touched.
"""
from __future__ import annotations

import argparse
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

SDK = Path(__file__).resolve().parents[1]
GUIDE = SDK / "docs" / "self-hosted-sandbox.md"

#: placeholders the guide uses, replaced by something the parser accepts
PLACEHOLDERS = {"<id>": "client-1", "<address>": "http://127.0.0.1:8099",
                "<code>": "KQ7M-3PXA-9TDF"}


def _guide() -> str:
    return GUIDE.read_text(encoding="utf-8")


def _guide_commands() -> list[str]:
    cmds = []
    for block in re.findall(r"```[a-z]*\n(.*?)```", _guide(), flags=re.S):
        for line in block.splitlines():
            line = line.split("  #")[0].strip()
            if line.startswith("agentnode "):
                cmds.append(line)
    return cmds


def _parser() -> argparse.ArgumentParser:
    """The CLI's real top-level parser, captured as main() builds it."""
    from agentnode_sdk.cli import main as cli_main

    captured = {}

    class _Stop(Exception):
        pass

    real = argparse.ArgumentParser.parse_args

    def capture(self, *a, **k):
        captured.setdefault("parser", self)
        raise _Stop

    argparse.ArgumentParser.parse_args = capture
    try:
        with pytest.raises(_Stop):
            cli_main.main(["--help"])
    finally:
        argparse.ArgumentParser.parse_args = real
    return captured["parser"]


def test_the_guide_is_plain_text():
    """It was committed with `\\r\\r\\r\\r\\n` line ends, so git treated it as binary (no diffs)."""
    assert b"\r" not in GUIDE.read_bytes()


def test_the_guide_shows_commands():
    assert len(_guide_commands()) >= 15, _guide_commands()


@pytest.mark.parametrize("command", _guide_commands())
def test_every_command_in_the_guide_exists_with_its_flags(command):
    for k, v in PLACEHOLDERS.items():
        command = command.replace(k, v)
    argv = shlex.split(command)[1:]
    try:
        _parser().parse_args(argv)
    except SystemExit as exc:
        pytest.fail("the guide shows `%s`, which the CLI rejects (exit %s)" % (command, exc.code))


def _cli(args, env, timeout=60):
    return subprocess.run([sys.executable, "-m", "agentnode_sdk.cli", *args], env=env, cwd=str(SDK),
                          capture_output=True, text=True, timeout=timeout)


def _env(tmp_path, **extra):
    env = dict(os.environ, PYTHONPATH=str(SDK), HOME=str(tmp_path), USERPROFILE=str(tmp_path),
               AGENTNODE_CONFIG=str(tmp_path / "config.json"), **extra)
    if "AGENTNODE_ALLOW_UNPINNED" not in extra:
        env.pop("AGENTNODE_ALLOW_UNPINNED", None)
    return env


def _initialised(tmp_path, env):
    gw = tmp_path / "gw"
    init = _cli(["gateway", "init", "--dir", str(gw)], env)
    assert init.returncode == 0, init.stdout + init.stderr
    return gw


# ---- the managed path keeps its pin requirement --------------------------------------------- #


def test_without_a_pin_and_without_saying_so_start_still_refuses(tmp_path):
    """The managed service's rule is unchanged: no pin, no explicit choice -> not started."""
    env = _env(tmp_path)
    start = _cli(["gateway", "start", "--dir", str(_initialised(tmp_path, env)), "--port", "0"],
                 env, timeout=30)
    said = start.stdout + start.stderr
    assert start.returncode != 0 and "Not started" in said and "runtime pin" in said, said
    assert "deploy-pinned.sh" in said                      # how the managed service gets one
    # ... and a pip self-hoster is told their way, not only a script they do not have.
    assert "self-hosted" in said and "AGENTNODE_ALLOW_UNPINNED=1" in said, said
    assert "made before pinning existed" not in said, said


# ---- the self-hosted beta path: explicit, said out loud, documented -------------------------- #


def test_the_guide_documents_the_runtime_pin_and_the_beta_switch():
    guide = _guide()
    assert "runtime pin" in guide.lower()
    assert "AGENTNODE_ALLOW_UNPINNED=1 agentnode gateway start" in guide
    assert "deploy-pinned.sh" in guide                    # and that the managed path pins


def test_the_beta_switch_starts_it_and_says_so(tmp_path):
    """The documented beta start, for real: it serves on loopback and says it is unpinned."""
    import socket
    import time
    import urllib.request

    env = _env(tmp_path, AGENTNODE_ALLOW_UNPINNED="1")
    gw = _initialised(tmp_path, env)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    proc = subprocess.Popen([sys.executable, "-m", "agentnode_sdk.cli", "gateway", "start",
                             "--dir", str(gw), "--port", str(port)], env=env, cwd=str(SDK),
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        deadline, up = time.monotonic() + 30, False
        while time.monotonic() < deadline and not up:
            try:
                up = urllib.request.urlopen("http://127.0.0.1:%d/v1/hello" % port, timeout=2).status == 200
            except OSError:
                time.sleep(0.3)
        assert up, "the gateway did not come up with the beta switch"
    finally:
        proc.terminate()
        said = proc.communicate(timeout=20)[0]
    assert "starting anyway because somebody said so" in said, said


# ---- a machine without a usable runtime gets a clear refusal, as the guide says --------------- #


def test_without_a_runtime_nothing_is_offered_and_the_guide_says_so(tmp_path):
    from agentnode_sdk.sandbox.container_backend import ContainerBackend

    if ContainerBackend().check_available().available:
        pytest.skip("this machine has a container runtime; the refusal is the case without one")
    env = _env(tmp_path, AGENTNODE_ALLOW_UNPINNED="1")
    gw = _initialised(tmp_path, env)
    doctor = _cli(["gateway", "doctor", "--dir", str(gw)], env)
    assert doctor.returncode != 0 and "no container runtime" in doctor.stdout + doctor.stderr
    status = _cli(["gateway", "status", "--dir", str(gw)], env)
    assert "Not protecting anything yet" in status.stdout + status.stderr
    pair = _cli(["gateway", "pair", "--dir", str(gw)], env)
    assert "No code issued" in pair.stdout + pair.stderr        # no invitation to a dead end
    guide = _guide()
    assert "will refuse every job" in guide
    assert "issues no code" in guide


# ---- the older page no longer says nothing can be installed --------------------------------- #


def test_the_design_page_points_to_the_guide():
    page = (SDK.parent / "docs" / "sandbox" / "self-hosted.md").read_text(encoding="utf-8")
    assert "../../sdk/docs/self-hosted-sandbox.md" in page
    assert "agentnode remote" in page
    # what is still not built stays said: installed packs are not sent to a gateway
    assert "agentnode run" in page
