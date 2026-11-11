"""Task #17: an optional provider whose package is missing is SKIPPED, never a failure.

With OPENAI_API_KEY set and the `openai` package not installed, test_provider_matrix.py and
test_e2e_runtime.py produced 41 failures and errors in every full run (T-0059): the OpenAI paths
checked the key and not the package. That noise hides real SDK regressions. Both files now require
the key AND the package for every provider, as R5 Route B already did for Anthropic.

This runs the two files in a child process with placeholder keys -- never a real one, and nothing
is called -- and holds: no failure, no error, and every skip names the precondition it lacks in
the words the execution lanes check (.github/workflows/sdk-lanes.yml, tests/lanes/freeze_inventory.py).
"""

from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

SDK = Path(__file__).resolve().parents[1]
PROVIDER_FILES = ["tests/test_provider_matrix.py", "tests/test_e2e_runtime.py"]
#: What sdk-lanes.yml accepts as a named precondition for a non-Anthropic skip.
LANE_NAMED = (
    "not set",
    "not available",
    "needs BOTH a key",
    "No Anthropic API key",
    "base_url",
    "BASE_URL",
    "set AGENTNODE",
)


def _installed(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


pytestmark = pytest.mark.skipif(
    _installed("openai") or _installed("google.genai"),
    reason="an optional provider package is installed here, so the missing-package case "
    "cannot arise; with placeholder keys its tests would try the network",
)


def test_a_set_key_without_its_package_skips_and_names_why():
    env = dict(
        os.environ,
        OPENAI_API_KEY="placeholder-not-a-key",
        GEMINI_API_KEY="placeholder-not-a-key",
    )
    env.pop("ANTHROPIC_API_KEY", None)
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            *PROVIDER_FILES,
            "-q",
            "-p",
            "no:cacheprovider",
            "-rs",
            "--tb=line",
        ],
        cwd=str(SDK),
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )
    summary = done.stdout.strip().splitlines()[-1]
    assert done.returncode == 0, done.stdout[-3000:]
    assert not re.search(r"\b\d+ (failed|errors?)\b", summary), summary
    assert "placeholder-not-a-key" not in done.stdout + done.stderr
    reasons = [
        line.split(": ", 1)[-1]
        for line in done.stdout.splitlines()
        if line.startswith("SKIPPED")
    ]
    assert reasons, "nothing was skipped, so nothing was checked"
    for reason in reasons:
        assert any(word in reason for word in LANE_NAMED), (
            "skip names no precondition: " + reason
        )
    assert any(
        "'openai' package" in r or "'openai' not available" in r for r in reasons
    )
