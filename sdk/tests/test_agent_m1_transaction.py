"""A1-M1: the host-agent install transaction (CAS, ownership, quarantine, crash,
controlled pip target, cross-lockfile honesty). Uses the internal transaction entry
point directly (download/hash/signature gates are unchanged and tested elsewhere)."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from agentnode_sdk import _agent_pip as ap
from agentnode_sdk import _wheel_meta as wm
from agentnode_sdk import installer
from agentnode_sdk import lock_integrity as li
from tests.agent_m1_helpers import make_target_venv, pip_python


# ---------------------------------------------------------------------------
# Fixtures: a minimal agent source tree + a clean target venv.
# ---------------------------------------------------------------------------

def _write_agent_source(root: Path, *, name="m1-agent", version="1.0.0",
                        top="m1agent", marker="v1", deps=None) -> Path:
    src = root / "src" / top
    src.mkdir(parents=True)
    (src / "__init__.py").write_text('"""agent"""\n')
    (src / "agent.py").write_text(f"MARKER = {marker!r}\n\ndef run():\n    return MARKER\n")
    dep_line = ""
    if deps:
        dep_line = "dependencies = [" + ", ".join(repr(d) for d in deps) + "]\n"
    (root / "pyproject.toml").write_text(textwrap.dedent(f"""
        [build-system]
        requires = ["setuptools>=61"]
        build-backend = "setuptools.build_meta"
        [project]
        name = "{name}"
        version = "{version}"
        {dep_line}
        [tool.setuptools.packages.find]
        where = ["src"]
    """))
    return root


def _lock_entry(version="1.0.0", entrypoint="m1agent.agent:run", **over):
    e = {
        "version": version,
        "package_type": "agent",
        "runtime": "python",
        "entrypoint": entrypoint,
        "artifact_hash": "sha256:" + "a" * 64,
        "installed_at": "2026-01-01T00:00:00+00:00",
        "source": "sdk",
        "trust_level": "trusted",
        "last_trust_check": "2026-01-01T00:00:00+00:00",
        "permissions": {},
    }
    e.update(over)
    return e


@pytest.fixture(scope="module")
def target_python(tmp_path_factory):
    base = pip_python()
    venv_dir = tmp_path_factory.mktemp("target-venv")
    return make_target_venv(base, venv_dir)


@pytest.fixture
def isolated_lock(tmp_path, monkeypatch):
    lf = tmp_path / "agentnode.lock"
    monkeypatch.setenv("AGENTNODE_LOCKFILE", str(lf))
    monkeypatch.setenv("AGENTNODE_CONFIG", str(tmp_path / "cfg"))
    return lf


def _run(slug, source, target, lock_entry=None):
    installer._install_agent_host_transaction(
        slug,
        lock_entry=lock_entry or _lock_entry(),
        package_dir=source,
        target_python=target,
        policy="default",
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_happy_path_commits_sealed_fields(tmp_path, isolated_lock, target_python):
    src = _write_agent_source(tmp_path / "a")
    _run("m1-agent", src, target_python)
    data = installer.read_lockfile(isolated_lock)
    entry = data["packages"]["m1-agent"]
    assert entry["python_distribution"] == "m1-agent"
    assert entry["python_distribution_version"] == "1.0.0"
    assert entry["build_mode"] == "host"
    assert li.verify_entry("m1-agent", entry).status == "verified"
    li.validate_python_distribution_fields(entry)


# ---------------------------------------------------------------------------
# CAS (§10)
# ---------------------------------------------------------------------------

def test_cas_stale_build_aborts_without_touching_old_entry(tmp_path, isolated_lock,
                                                           target_python, monkeypatch):
    # existing entry present, but the captured baseline claims a different hash →
    # a newer install superseded us → abort before quarantine + pip.
    installer.update_lockfile("m1-agent", _lock_entry(
        python_distribution="m1-agent", python_distribution_version="1.0.0"),
        path=isolated_lock)
    before = installer.read_lockfile(isolated_lock)["packages"]["m1-agent"]["_integrity"]["hash"]
    monkeypatch.setattr(installer, "_capture_cas_baseline", lambda s, p: (False, "wrong" * 16))
    called = []
    monkeypatch.setattr(ap, "pip_install_wheel", lambda *a, **k: called.append(1))
    src = _write_agent_source(tmp_path / "a")
    with pytest.raises(RuntimeError, match="modified by another install"):
        _run("m1-agent", src, target_python)
    assert called == []  # pip never started
    after = installer.read_lockfile(isolated_lock)["packages"]["m1-agent"]["_integrity"]["hash"]
    assert before == after  # old entry intact


def test_cas_expected_absent_but_appeared_aborts(tmp_path, isolated_lock,
                                                 target_python, monkeypatch):
    installer.update_lockfile("m1-agent", _lock_entry(), path=isolated_lock)
    monkeypatch.setattr(installer, "_capture_cas_baseline", lambda s, p: (True, None))
    called = []
    monkeypatch.setattr(ap, "pip_install_wheel", lambda *a, **k: called.append(1))
    src = _write_agent_source(tmp_path / "a")
    with pytest.raises(RuntimeError, match="modified by another install"):
        _run("m1-agent", src, target_python)
    assert called == []


# ---------------------------------------------------------------------------
# Cross-slug ownership + name-change (§11, §17)
# ---------------------------------------------------------------------------

def test_cross_slug_same_distribution_refused(tmp_path, isolated_lock,
                                              target_python, monkeypatch):
    # another agent already owns the distribution "m1-agent"
    installer.update_lockfile("other-agent", _lock_entry(
        entrypoint="otherns.agent:run",
        python_distribution="m1-agent", python_distribution_version="9.0.0"),
        path=isolated_lock)
    called = []
    monkeypatch.setattr(ap, "pip_install_wheel", lambda *a, **k: called.append(1))
    src = _write_agent_source(tmp_path / "a")
    with pytest.raises(RuntimeError, match="already\\s+owned by another agent"):
        _run("m1-agent", src, target_python)
    assert called == []


def test_cross_slug_same_top_level_refused(tmp_path, isolated_lock,
                                           target_python, monkeypatch):
    # another agent's sealed entrypoint has the same top-level "m1agent"
    installer.update_lockfile("other-agent", _lock_entry(
        entrypoint="m1agent.other:run"), path=isolated_lock)
    called = []
    monkeypatch.setattr(ap, "pip_install_wheel", lambda *a, **k: called.append(1))
    src = _write_agent_source(tmp_path / "a")
    with pytest.raises(RuntimeError, match="already\\s+owned by another agent"):
        _run("m1-agent", src, target_python)
    assert called == []


def test_distribution_name_change_refused(tmp_path, isolated_lock,
                                          target_python, monkeypatch):
    installer.update_lockfile("m1-agent", _lock_entry(
        python_distribution="old-distribution", python_distribution_version="0.9.0"),
        path=isolated_lock)
    monkeypatch.setattr(installer, "_capture_cas_baseline",
                        lambda s, p: (False, installer.read_lockfile(isolated_lock)
                                      ["packages"]["m1-agent"]["_integrity"]["hash"]))
    called = []
    monkeypatch.setattr(ap, "pip_install_wheel", lambda *a, **k: called.append(1))
    src = _write_agent_source(tmp_path / "a")
    with pytest.raises(RuntimeError, match="distribution name changed"):
        _run("m1-agent", src, target_python)
    assert called == []


# ---------------------------------------------------------------------------
# Quarantine crash + post-quarantine failure (§13, §18, §19)
# ---------------------------------------------------------------------------

def test_quarantine_write_failure_does_not_start_pip(tmp_path, isolated_lock,
                                                     target_python, monkeypatch):
    installer.update_lockfile("m1-agent", _lock_entry(
        python_distribution="m1-agent", python_distribution_version="1.0.0"),
        path=isolated_lock)
    before = installer.read_lockfile(isolated_lock)["packages"]["m1-agent"]["_integrity"]["hash"]
    monkeypatch.setattr(installer, "_capture_cas_baseline",
                        lambda s, p: (False, before))
    import agentnode_sdk._fileutil as fu
    monkeypatch.setattr(fu, "atomic_write_json",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    called = []
    monkeypatch.setattr(ap, "pip_install_wheel", lambda *a, **k: called.append(1))
    src = _write_agent_source(tmp_path / "a")
    with pytest.raises(RuntimeError):
        _run("m1-agent", src, target_python)
    assert called == []  # pip not started
    # old entry still present (quarantine write failed before replace)
    after = installer.read_lockfile(isolated_lock)["packages"]["m1-agent"]["_integrity"]["hash"]
    assert before == after


def test_post_verify_failure_leaves_entry_absent_no_restore(tmp_path, isolated_lock,
                                                            target_python, monkeypatch):
    installer.update_lockfile("m1-agent", _lock_entry(
        python_distribution="m1-agent", python_distribution_version="1.0.0"),
        path=isolated_lock)
    before = installer.read_lockfile(isolated_lock)["packages"]["m1-agent"]["_integrity"]["hash"]
    monkeypatch.setattr(installer, "_capture_cas_baseline",
                        lambda s, p: (False, before))
    monkeypatch.setattr(ap, "post_verify",
                        lambda *a, **k: (_ for _ in ()).throw(
                            ap.AgentPipError("postverify", "x")))
    src = _write_agent_source(tmp_path / "a")
    with pytest.raises(RuntimeError, match="unavailable until it is reinstalled"):
        _run("m1-agent", src, target_python)
    data = installer.read_lockfile(isolated_lock)
    # quarantined + failed → absent, old entry NOT restored
    assert "m1-agent" not in data["packages"]


def test_file_lock_not_held_across_pip(tmp_path, isolated_lock, target_python, monkeypatch):
    """During pip the per-lockfile file_lock must be free — a concurrent lockfile
    mutation from within the (patched) pip step must succeed, not deadlock."""
    def fake_pip(target, wheel, env):
        installer.update_lockfile(
            "sentinel-pack",
            {"version": "1.0.0", "package_type": "toolpack",
             "artifact_hash": "sha256:" + "b" * 64},
            path=isolated_lock)

    monkeypatch.setattr(ap, "pip_install_wheel", fake_pip)
    monkeypatch.setattr(ap, "post_verify", lambda *a, **k: ap.PostVerifyResult("m1-agent", "1.0.0"))
    src = _write_agent_source(tmp_path / "a")
    _run("m1-agent", src, target_python)
    data = installer.read_lockfile(isolated_lock)
    assert "sentinel-pack" in data["packages"]  # the mid-pip mutation went through
    assert "m1-agent" in data["packages"]


# ---------------------------------------------------------------------------
# Cross-lockfile: HONEST non-guarantee (§20)
# ---------------------------------------------------------------------------

def test_cross_lockfile_entry_not_blocked(tmp_path, monkeypatch, target_python):
    """M1 quarantines only the current lockfile's entry. A second lockfile's entry
    for the same distribution/namespace remains present — M1 does NOT (and must not
    be tested to) block it; that is A1-Enforcement scope."""
    l1 = tmp_path / "l1.lock"
    l2 = tmp_path / "l2.lock"
    monkeypatch.setenv("AGENTNODE_CONFIG", str(tmp_path / "cfg"))
    # L2 owns an agent providing the same top-level / distribution
    installer.update_lockfile("m1-agent", _lock_entry(
        python_distribution="m1-agent", python_distribution_version="0.1.0"),
        path=l2)
    monkeypatch.setenv("AGENTNODE_LOCKFILE", str(l1))
    src = _write_agent_source(tmp_path / "a")
    _run("m1-agent", src, target_python)
    # L1 committed; L2's independent entry is untouched and still present
    assert "m1-agent" in installer.read_lockfile(l1)["packages"]
    assert "m1-agent" in installer.read_lockfile(l2)["packages"]


# ---------------------------------------------------------------------------
# Controlled pip target (§12)
# ---------------------------------------------------------------------------

def test_config_target_redirect_refused_pre_quarantine(tmp_path, isolated_lock,
                                                       target_python, monkeypatch):
    redir = tmp_path / "redir"
    redir.mkdir()
    cfg = tmp_path / "pip.conf"
    cfg.write_text(f"[global]\ntarget = {redir}\n")
    monkeypatch.setenv("PIP_CONFIG_FILE", str(cfg))
    called = []
    monkeypatch.setattr(ap, "pip_install_wheel", lambda *a, **k: called.append(1))
    src = _write_agent_source(tmp_path / "a")
    with pytest.raises(RuntimeError, match="target could not be established"):
        _run("m1-agent", src, target_python)
    assert called == []


def test_pip_env_neutralizes_redirect_vars(monkeypatch):
    monkeypatch.setenv("PIP_TARGET", "/somewhere")
    monkeypatch.setenv("PIP_PREFIX", "/pre")
    monkeypatch.setenv("PYTHONUSERBASE", "/ub")
    env = ap.controlled_pip_env()
    assert "PIP_TARGET" not in env
    assert "PIP_PREFIX" not in env
    assert "PYTHONUSERBASE" not in env
    assert env["PYTHONNOUSERSITE"] == "1"
    assert env["PIP_USER"] == "0"


# ---------------------------------------------------------------------------
# pip semantics (§15): same version, different wheel bytes → exact bytes installed
# ---------------------------------------------------------------------------

def test_same_version_different_bytes_reinstalled(tmp_path, isolated_lock, target_python):
    src = _write_agent_source(tmp_path / "a", marker="v1")
    _run("m1-agent", src, target_python)
    import subprocess
    got1 = subprocess.run([target_python, "-c", "import m1agent.agent as a; print(a.MARKER)"],
                          capture_output=True, text=True)
    assert got1.stdout.strip() == "v1"
    # rebuild SAME version with different bytes, reinstall (upgrade path)
    src2 = _write_agent_source(tmp_path / "b", marker="v2")
    _run("m1-agent", src2, target_python, lock_entry=_lock_entry())
    got2 = subprocess.run([target_python, "-c", "import m1agent.agent as a; print(a.MARKER)"],
                          capture_output=True, text=True)
    assert got2.stdout.strip() == "v2"  # force-reinstall applied the exact new bytes


# ---------------------------------------------------------------------------
# Amendment — Blocker 1: foreign re-insert between quarantine and commit
# ---------------------------------------------------------------------------

def test_foreign_reinsert_before_commit_refused(tmp_path, isolated_lock, target_python, monkeypatch):
    """A concurrent writer inserts a foreign entry for the slug after quarantine but
    before commit (injected via post_verify) → commit refuses, foreign entry kept."""
    foreign = _lock_entry(entrypoint="foreign.agent:run",
                          python_distribution="foreign-agent",
                          python_distribution_version="9.9.9")

    def inject_foreign(*a, **k):
        installer.update_lockfile("m1-agent", foreign, path=isolated_lock)
        return ap.PostVerifyResult("m1-agent", "1.0.0")

    monkeypatch.setattr(ap, "post_verify", inject_foreign)
    src = _write_agent_source(tmp_path / "a")
    with pytest.raises(RuntimeError, match="unavailable until it is reinstalled"):
        _run("m1-agent", src, target_python)
    stored = installer.read_lockfile(isolated_lock)["packages"]["m1-agent"]
    assert stored["python_distribution"] == "foreign-agent"  # not overwritten


# ---------------------------------------------------------------------------
# Amendment — Blocker 2: build_wheel receives the controlled env, not os.environ
# ---------------------------------------------------------------------------

def test_transaction_build_receives_controlled_env(tmp_path, isolated_lock,
                                                   target_python, monkeypatch):
    monkeypatch.setenv("PIP_TARGET", "/somewhere")
    monkeypatch.setenv("PIP_PREFIX", "/pre")
    monkeypatch.setenv("PYTHONUSERBASE", "/ub")
    real_build = wm.build_wheel
    captured = {}

    def spy_build(target, src, dest, *, env=None):
        captured["env"] = env
        return real_build(target, src, dest, env=env)

    monkeypatch.setattr(wm, "build_wheel", spy_build)
    src = _write_agent_source(tmp_path / "a")
    _run("m1-agent", src, target_python)
    env = captured["env"]
    assert env is not None
    assert "PIP_TARGET" not in env and "PIP_PREFIX" not in env and "PYTHONUSERBASE" not in env
    assert env["PYTHONNOUSERSITE"] == "1" and env["PIP_USER"] == "0"


# ---------------------------------------------------------------------------
# Amendment — Blocker 3: install-scheme failure refuses pre-quarantine
# ---------------------------------------------------------------------------

def test_transaction_refused_when_install_scheme_fails(tmp_path, isolated_lock,
                                                       target_python, monkeypatch):
    installer.update_lockfile("m1-agent", _lock_entry(
        python_distribution="m1-agent", python_distribution_version="1.0.0"),
        path=isolated_lock)
    before = installer.read_lockfile(isolated_lock)["packages"]["m1-agent"]["_integrity"]["hash"]
    monkeypatch.setattr(ap, "assert_install_scheme",
                        lambda *a, **k: (_ for _ in ()).throw(
                            ap.AgentPipError("install_scheme", "Install target could not be established")))
    called = []
    monkeypatch.setattr(ap, "pip_install_wheel", lambda *a, **k: called.append(1))
    src = _write_agent_source(tmp_path / "a")
    with pytest.raises(RuntimeError, match="target could not be established"):
        _run("m1-agent", src, target_python)
    assert called == []  # pip never started (pre-quarantine refusal)
    after = installer.read_lockfile(isolated_lock)["packages"]["m1-agent"]["_integrity"]["hash"]
    assert before == after  # old entry intact
