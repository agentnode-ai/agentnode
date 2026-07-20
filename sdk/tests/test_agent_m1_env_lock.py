"""A1-M1: target-environment identity + environment-scoped install lock."""
from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from agentnode_sdk import _env_lock as envlock


def test_env_identity_deterministic_and_binds_roots():
    a = envlock.resolve_env_identity(sys.executable)
    b = envlock.resolve_env_identity(sys.executable)
    assert a.env_id == b.env_id
    assert a.purelib and a.platlib and a.target_python
    # env_id is exactly sha256(target_python \0 purelib \0 platlib)
    import hashlib
    expect = hashlib.sha256(
        f"{a.target_python}\0{a.purelib}\0{a.platlib}".encode()
    ).hexdigest()
    assert a.env_id == expect


def test_env_identity_bad_interpreter():
    with pytest.raises(envlock.EnvironmentResolutionError):
        envlock.resolve_env_identity("this-python-does-not-exist-xyz")


def test_lock_path_uses_hash_not_raw_path(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTNODE_CONFIG", str(tmp_path))
    with envlock.env_lock("deadbeef" * 8):
        pass
    locks = list((tmp_path / "locks").glob("env-*"))
    assert locks, "a lock sidecar should have been created"
    # the env_id (a hash) appears; no raw filesystem path leaks into the name
    assert all(":" not in p.name.replace(".lk", "") for p in locks)


def test_env_lock_and_lockfile_lock_no_deadlock(tmp_path, monkeypatch):
    """The environment lock (outer) and the per-lockfile file_lock (inner) are
    independent files; holding the env lock and then mutating a lockfile must not
    deadlock (fixed lock order: env → file_lock)."""
    monkeypatch.setenv("AGENTNODE_CONFIG", str(tmp_path))
    from agentnode_sdk import installer
    lf = tmp_path / "agentnode.lock"
    with envlock.env_lock("a" * 64):
        installer.update_lockfile(
            "x-pack",
            {"version": "1.0.0", "package_type": "toolpack", "artifact_hash": "sha256:" + "a" * 64},
            path=lf,
        )
    assert lf.is_file()


_COUNTER_CHILD = textwrap.dedent(
    """
    import json, os, sys
    os.environ["AGENTNODE_CONFIG"] = sys.argv[1]
    sys.path.insert(0, sys.argv[4])
    from agentnode_sdk import _env_lock as envlock
    counter = sys.argv[2]
    env_id = sys.argv[3]
    for _ in range(50):
        with envlock.env_lock(env_id):
            try:
                n = int(open(counter).read().strip() or "0")
            except FileNotFoundError:
                n = 0
            # a read-modify-write that WILL lose updates without real mutual exclusion
            open(counter, "w").write(str(n + 1))
    """
)


def test_env_lock_serializes_across_processes(tmp_path, monkeypatch):
    """3 processes × 50 increments through a lost-update-prone read-modify-write —
    the total is exactly 150 only if the environment lock is genuinely exclusive
    across processes."""
    monkeypatch.setenv("AGENTNODE_CONFIG", str(tmp_path))
    child = tmp_path / "child.py"
    child.write_text(_COUNTER_CHILD)
    counter = tmp_path / "counter.txt"
    repo_root = str(Path(__file__).resolve().parents[1])
    procs = [
        subprocess.Popen([sys.executable, str(child), str(tmp_path), str(counter),
                          "b" * 64, repo_root])
        for _ in range(3)
    ]
    for p in procs:
        assert p.wait(timeout=120) == 0
    assert int(counter.read_text().strip()) == 150


def test_different_environments_do_not_share_a_lock():
    """Two genuinely different interpreters map to different env_ids (so they never
    contend on one lock and can install in parallel)."""
    a = envlock.resolve_env_identity(sys.executable)
    from tests.agent_m1_helpers import pip_python
    other = pip_python()
    b = envlock.resolve_env_identity(other)
    if a.target_python == b.target_python:
        pytest.skip("only one interpreter available to compare")
    assert a.env_id != b.env_id
