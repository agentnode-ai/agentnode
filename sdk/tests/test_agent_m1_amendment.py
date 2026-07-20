"""A1-M1 amendment: exact-entry commit (Blocker 1), controlled-env-into-build
(Blocker 2), no user-site fallback (Blocker 3)."""
from __future__ import annotations

import os
import subprocess

import pytest

from agentnode_sdk import _agent_pip as ap
from agentnode_sdk import _wheel_meta as wm
from agentnode_sdk import installer
from agentnode_sdk import lock_integrity as li
from tests.agent_m1_helpers import make_target_venv, pip_python


def _agent_entry(**over):
    e = {
        "version": "2.1.4",
        "package_type": "agent",
        "runtime": "python",
        "entrypoint": "m1agent.agent:run",
        "artifact_hash": "sha256:" + "a" * 64,
        "installed_at": "2026-01-01T00:00:00+00:00",
        "source": "sdk",
        "trust_level": "trusted",
        "last_trust_check": "2026-01-01T00:00:00+00:00",
        "permissions": {},
        "python_distribution": "m1-agent",
        "python_distribution_version": "1.0.0",
    }
    e.update(over)
    return e


@pytest.fixture
def lockpath(tmp_path, monkeypatch):
    lf = tmp_path / "agentnode.lock"
    monkeypatch.setenv("AGENTNODE_LOCKFILE", str(lf))
    monkeypatch.setenv("AGENTNODE_CONFIG", str(tmp_path / "cfg"))
    return lf


# ---------------------------------------------------------------------------
# Blocker 1 — exact-entry commit predicate
# ---------------------------------------------------------------------------

def _lockdata(*entries):
    from agentnode_sdk.lock_integrity import seal_structure
    data = {"lockfile_version": "0.1", "updated_at": "", "packages": {}}
    for slug, entry in entries:
        data["packages"][slug] = li.seal_entry(entry)
    return seal_structure(data)


def test_stored_matches_expected_exact():
    expected = li.seal_entry(_agent_entry())
    data = _lockdata(("m1-agent", _agent_entry()))
    assert installer._stored_matches_expected(data, "m1-agent", expected,
                                              expected["_integrity"]["hash"]) is True


def test_stored_matches_expected_different_hash():
    expected = li.seal_entry(_agent_entry())
    data = _lockdata(("m1-agent", _agent_entry()))
    # tamper the expected hash
    assert installer._stored_matches_expected(data, "m1-agent", expected, "0" * 64) is False


def test_stored_matches_expected_version_only_differs():
    expected = li.seal_entry(_agent_entry(python_distribution_version="1.0.0"))
    data = _lockdata(("m1-agent", _agent_entry(python_distribution_version="2.0.0")))
    assert installer._stored_matches_expected(data, "m1-agent", expected,
                                              expected["_integrity"]["hash"]) is False


def test_stored_matches_expected_different_valid_entry():
    expected = li.seal_entry(_agent_entry())
    # a DIFFERENT but perfectly valid+sealed entry for the same slug
    data = _lockdata(("m1-agent", _agent_entry(entrypoint="other.agent:run",
                                               python_distribution="other-agent")))
    assert li.verify_entry("m1-agent", data["packages"]["m1-agent"]).status == "verified"
    assert installer._stored_matches_expected(data, "m1-agent", expected,
                                              expected["_integrity"]["hash"]) is False


def test_commit_exact_success(lockpath):
    installer._commit_agent_entry("m1-agent", _agent_entry(), lockpath)
    data = installer.read_lockfile(lockpath)
    stored = data["packages"]["m1-agent"]
    assert stored["python_distribution"] == "m1-agent"
    assert li.verify_entry("m1-agent", stored).status == "verified"


def test_commit_refuses_foreign_entry_no_overwrite(lockpath):
    # a foreign, valid entry is already present for the slug
    installer.update_lockfile("m1-agent", _agent_entry(
        entrypoint="foreign.agent:run", python_distribution="foreign-agent"),
        path=lockpath)
    with pytest.raises(RuntimeError, match="unavailable until it is reinstalled"):
        installer._commit_agent_entry("m1-agent", _agent_entry(), lockpath)
    # foreign entry NOT overwritten
    stored = installer.read_lockfile(lockpath)["packages"]["m1-agent"]
    assert stored["python_distribution"] == "foreign-agent"


def test_recover_to_absent_removes(lockpath):
    installer.update_lockfile("m1-agent", _agent_entry(), path=lockpath)
    installer._recover_to_absent("m1-agent", lockpath)
    assert "m1-agent" not in installer.read_lockfile(lockpath)["packages"]


def test_recover_to_absent_indeterminate_when_not_removed(lockpath, monkeypatch):
    installer.update_lockfile("m1-agent", _agent_entry(), path=lockpath)
    # simulate a remove that does not actually remove the entry
    monkeypatch.setattr(installer, "remove_from_lockfile", lambda *a, **k: None)
    with pytest.raises(installer._IndeterminateLockState):
        installer._recover_to_absent("m1-agent", lockpath)


def test_recover_to_absent_indeterminate_on_read_failure(lockpath, monkeypatch):
    installer.update_lockfile("m1-agent", _agent_entry(), path=lockpath)
    from agentnode_sdk.exceptions import LockfileFormatError

    def boom(*a, **k):
        raise LockfileFormatError("x", "unreadable")

    monkeypatch.setattr(installer, "read_lockfile_strict", boom)
    with pytest.raises(installer._IndeterminateLockState):
        installer._recover_to_absent("m1-agent", lockpath)


# ---------------------------------------------------------------------------
# Blocker 2 — the controlled environment reaches build_wheel
# ---------------------------------------------------------------------------

def test_build_wheel_forwards_env(tmp_path, monkeypatch):
    dest = tmp_path / "d"
    dest.mkdir()
    sentinel = dict(os.environ)
    sentinel["ANWHEEL_SENTINEL"] = "yes"
    captured = {}

    def fake_run(cmd, **kw):
        captured["env"] = kw.get("env")
        (dest / "pkg-1.0-py3-none-any.whl").write_bytes(b"PK\x03\x04")

        class P:
            returncode = 0
            stdout = b""
            stderr = b""

        return P()

    monkeypatch.setattr(wm.subprocess, "run", fake_run)
    wm.build_wheel("py", tmp_path / "src", dest, env=sentinel)
    assert captured["env"] is sentinel
    assert captured["env"]["ANWHEEL_SENTINEL"] == "yes"


# ---------------------------------------------------------------------------
# Blocker 3 — install scheme (no user-site fallback)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("probe,ok", [
    ({"purelib_writable": True, "platlib_writable": True}, True),
    ({"purelib_writable": False, "platlib_writable": True}, False),
    ({"purelib_writable": True, "platlib_writable": False}, False),
    ({"purelib_writable": False, "platlib_writable": False}, False),
    ({}, False),
])
def test_install_scheme_predicate(probe, ok):
    assert ap._install_scheme_ok(probe) is ok


def test_install_scheme_allows_writable_venv(tmp_path):
    py = pip_python()
    tp = make_target_venv(py, tmp_path / "v")
    ap.assert_install_scheme(tp, ap.controlled_pip_env())  # writable venv → no raise


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory permissions")
def test_install_scheme_refuses_non_writable_site(tmp_path):
    py = pip_python()
    tp = make_target_venv(py, tmp_path / "v")
    purelib = subprocess.run(
        [tp, "-c", "import sysconfig;print(sysconfig.get_path('purelib'))"],
        capture_output=True, text=True).stdout.strip()
    os.chmod(purelib, 0o500)  # read+execute, NOT writable
    try:
        with pytest.raises(ap.AgentPipError):
            ap.assert_install_scheme(tp, ap.controlled_pip_env())
    finally:
        os.chmod(purelib, 0o700)  # restore so tmp cleanup works
