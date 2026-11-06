"""Task #22: the ruff cache is generated, so it is not tracked and stays ignored.

Nine files under `sdk/.ruff_cache` had been committed. They are a linter's cache, rewritten by every
run and meaningless to anyone reading the source, so they only made diffs noisy. This holds both
halves: nothing under any `.ruff_cache` directory is in the index, and git ignores the directory, so
a new run cannot bring it back.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.skipif(shutil.which("git") is None or not (REPO / ".git").exists(),
                                reason="needs a git checkout")


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True,
                          timeout=60)


def test_no_ruff_cache_file_is_tracked():
    tracked = [p for p in _git("ls-files").stdout.splitlines() if "/.ruff_cache/" in "/" + p]
    assert tracked == [], "generated ruff cache files are tracked: %s" % tracked[:5]


@pytest.mark.parametrize("where", ["sdk/.ruff_cache/probe", ".ruff_cache/probe",
                                   "backend/.ruff_cache/0.16.6/probe"])
def test_the_ruff_cache_is_ignored_wherever_ruff_runs(where):
    """By the REPOSITORY's rule. Ruff writes its own `.gitignore` into a cache it creates, but the
    committed `sdk/.ruff_cache` had none, so relying on ruff's file is relying on luck."""
    said = _git("check-ignore", "-v", "--no-index", where)
    assert said.returncode == 0, "%s is not ignored" % where
    source = said.stdout.split(":", 1)[0]
    assert source == ".gitignore", "%s is ignored only by %s, not by the repository" % (where, source)
