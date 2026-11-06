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


@pytest.mark.xfail(strict=True, reason=(
    "OPEN (task #10): how a pip self-hoster gets a runtime pin is a decision, not a doc fix -- "
    "deploy/deploy-pinned.sh is the managed service's tool and is not in the wheel. Strict: when "
    "it is decided and documented this passes, the xfail fails, and the marker must go."))
def test_following_the_guide_does_not_dead_end_at_start(tmp_path):
    """Steps 2 and 4 of the guide, for real: what `start` requires must be in the guide."""
    env = dict(os.environ, PYTHONPATH=str(SDK), HOME=str(tmp_path), USERPROFILE=str(tmp_path),
               AGENTNODE_CONFIG=str(tmp_path / "config.json"))
    env.pop("AGENTNODE_ALLOW_UNPINNED", None)
    gw = tmp_path / "gw"
    init = _cli(["gateway", "init", "--dir", str(gw)], env)
    assert init.returncode == 0, init.stdout + init.stderr
    start = _cli(["gateway", "start", "--dir", str(gw), "--port", "0"], env, timeout=30)
    said = start.stdout + start.stderr
    if start.returncode != 0 and "runtime pin" in said:
        guide = _guide().lower()
        assert "runtime pin" in guide, (
            "`agentnode gateway start` refuses without a runtime pin, and the guide never says "
            "how a pip installation gets one:\n" + said)
