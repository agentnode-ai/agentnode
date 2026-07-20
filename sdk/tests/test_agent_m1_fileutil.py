"""A1-M1: durable atomic write (contained; default caller behavior unchanged)."""
from __future__ import annotations

import json
import os

import pytest

from agentnode_sdk._fileutil import _fsync_dir, atomic_write_json


def test_durable_write_roundtrips(tmp_path):
    p = tmp_path / "x.json"
    atomic_write_json(p, {"a": 1}, durable=True)
    assert json.loads(p.read_text()) == {"a": 1}


def test_default_is_not_durable_but_writes(tmp_path):
    p = tmp_path / "y.json"
    atomic_write_json(p, {"b": 2})
    assert json.loads(p.read_text()) == {"b": 2}


def test_durable_and_nondurable_same_content(tmp_path):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    atomic_write_json(a, {"k": [1, 2, 3]}, durable=False)
    atomic_write_json(b, {"k": [1, 2, 3]}, durable=True)
    assert a.read_text() == b.read_text()


def test_fsync_dir_platform_contract(tmp_path):
    # POSIX: fsyncs the directory without error. Windows: documented no-op (no
    # directory fsync); must not raise.
    _fsync_dir(tmp_path)


def test_atomic_replace_leaves_old_on_write_error(tmp_path, monkeypatch):
    p = tmp_path / "z.json"
    atomic_write_json(p, {"old": True})
    # force the durable fsync to fail AFTER bytes are written but before replace
    import agentnode_sdk._fileutil as fu
    real_fsync = os.fsync

    def boom(fd):
        raise OSError("simulated fsync failure")

    monkeypatch.setattr(fu.os, "fsync", boom)
    with pytest.raises(OSError):
        atomic_write_json(p, {"new": True}, durable=True)
    monkeypatch.setattr(fu.os, "fsync", real_fsync)
    # the old content survives (temp file discarded, replace never happened)
    assert json.loads(p.read_text()) == {"old": True}
    # no leftover temp files
    assert list(tmp_path.glob(".z.json_*")) == []
