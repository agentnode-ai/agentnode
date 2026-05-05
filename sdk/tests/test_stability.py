"""Tests for Phase 4: Stability & Concurrency — atomic writes and file locking."""
import json
import os
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.json"
    lock_file = tmp_path / "agentnode.lock"
    monkeypatch.setenv("AGENTNODE_CONFIG", str(cfg_file))
    monkeypatch.setenv("AGENTNODE_LOCKFILE", str(lock_file))
    monkeypatch.delenv("AGENTNODE_NON_INTERACTIVE", raising=False)
    return tmp_path


# --- atomic_write_json ---


def test_atomic_write_creates_file(tmp_path):
    from agentnode_sdk._fileutil import atomic_write_json

    target = tmp_path / "out.json"
    atomic_write_json(target, {"key": "value"})
    assert target.is_file()
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data == {"key": "value"}


def test_atomic_write_overwrites_existing(tmp_path):
    from agentnode_sdk._fileutil import atomic_write_json

    target = tmp_path / "out.json"
    atomic_write_json(target, {"v": 1})
    atomic_write_json(target, {"v": 2})
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["v"] == 2


def test_atomic_write_no_partial_on_error(tmp_path):
    from agentnode_sdk._fileutil import atomic_write_json

    target = tmp_path / "out.json"
    atomic_write_json(target, {"original": True})

    with patch("os.write", side_effect=OSError("disk full")):
        with pytest.raises(OSError, match="disk full"):
            atomic_write_json(target, {"corrupted": True})

    data = json.loads(target.read_text(encoding="utf-8"))
    assert data == {"original": True}


def test_atomic_write_cleans_temp_on_error(tmp_path):
    from agentnode_sdk._fileutil import atomic_write_json

    target = tmp_path / "out.json"
    before = set(tmp_path.iterdir())

    with patch("os.replace", side_effect=OSError("rename failed")):
        with pytest.raises(OSError, match="rename failed"):
            atomic_write_json(target, {"data": 1})

    after = set(tmp_path.iterdir())
    assert after == before


@pytest.mark.skipif(os.name == "nt", reason="chmod not effective on Windows")
def test_atomic_write_with_chmod(tmp_path):
    from agentnode_sdk._fileutil import atomic_write_json

    target = tmp_path / "secret.json"
    atomic_write_json(target, {"token": "xxx"}, mode=0o600)
    stat = target.stat()
    assert oct(stat.st_mode)[-3:] == "600"


def test_atomic_write_creates_parent_dirs(tmp_path):
    from agentnode_sdk._fileutil import atomic_write_json

    target = tmp_path / "sub" / "dir" / "out.json"
    atomic_write_json(target, {"nested": True})
    assert target.is_file()


# --- file_lock ---


def test_file_lock_serializes_access(tmp_path):
    from agentnode_sdk._fileutil import file_lock

    lock_target = tmp_path / "test.lock"
    results = []

    def worker(name, delay):
        with file_lock(lock_target):
            results.append(f"{name}_start")
            time.sleep(delay)
            results.append(f"{name}_end")

    t1 = threading.Thread(target=worker, args=("A", 0.1))
    t2 = threading.Thread(target=worker, args=("B", 0.05))
    t1.start()
    time.sleep(0.02)
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)

    assert results[0] == "A_start"
    assert results[1] == "A_end"
    assert results[2] == "B_start"
    assert results[3] == "B_end"


def test_file_lock_released_on_exception(tmp_path):
    from agentnode_sdk._fileutil import file_lock

    lock_target = tmp_path / "test.lock"

    with pytest.raises(ValueError):
        with file_lock(lock_target):
            raise ValueError("boom")

    acquired = False
    with file_lock(lock_target):
        acquired = True
    assert acquired


def test_file_lock_creates_sidecar(tmp_path):
    from agentnode_sdk._fileutil import file_lock

    lock_target = tmp_path / "data.json"
    with file_lock(lock_target):
        assert (tmp_path / "data.json.lk").is_file()


# --- update_lockfile atomic + locked ---


def test_update_lockfile_atomic(isolated_env):
    from agentnode_sdk.installer import update_lockfile, read_lockfile

    lock_path = isolated_env / "agentnode.lock"
    update_lockfile("pack-a", {"version": "1.0.0"}, path=lock_path)

    data = read_lockfile(lock_path)
    assert "pack-a" in data["packages"]
    assert data["packages"]["pack-a"]["version"] == "1.0.0"
    raw = lock_path.read_text(encoding="utf-8")
    json.loads(raw)


def test_update_lockfile_concurrent(isolated_env):
    from agentnode_sdk.installer import update_lockfile, read_lockfile

    lock_path = isolated_env / "agentnode.lock"
    errors = []

    def writer(slug, version):
        try:
            update_lockfile(slug, {"version": version}, path=lock_path)
        except Exception as e:
            errors.append(e)

    threads = [
        threading.Thread(target=writer, args=(f"pack-{i}", f"{i}.0.0"))
        for i in range(10)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert not errors
    data = read_lockfile(lock_path)
    assert len(data["packages"]) == 10
    for i in range(10):
        assert f"pack-{i}" in data["packages"]


# --- save_config atomic ---


def test_save_config_atomic(isolated_env):
    from agentnode_sdk.config import save_config, load_config

    save_config({"auto_upgrade_policy": "minor"})
    cfg = load_config()
    assert cfg["auto_upgrade_policy"] == "minor"


# --- credential_store uses shared atomic ---


def test_credential_store_atomic(isolated_env):
    from agentnode_sdk.credential_store import save_credentials, load_credentials

    save_credentials({"version": 1, "providers": {"github": {"token": "x"}}})
    data = load_credentials()
    assert data["providers"]["github"]["token"] == "x"
