"""A1-E-Lock Layer 3 — REAL cross-process installer concurrency + kill-mid-quarantine
recovery. These exercise the genuine installer chokepoint (resolve_env_identity +
env_write_lock), not just the lock primitive.

* Two lockfiles / same target env  -> mutating (pip) sections never overlap (max 1).
* Same lockfile / same target env   -> never overlap (max 1).
* Two REAL venvs (distinct env_ids) -> mutating sections demonstrably overlap.
* Installer A killed AFTER a durable quarantine -> installer B acquires the SAME env
  write-lock (auto-released on A's death), recovers, and completes.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from agentnode_sdk import installer
from tests.agent_m1_helpers import make_target_venv, pip_python

_REPO = str(Path(__file__).resolve().parents[1])


def _max_concurrency(intervals: list[tuple[float, float]]) -> int:
    """Maximum number of intervals covering any instant. Touching intervals (exit==enter)
    do NOT count as overlap (exit is ordered before enter at the same timestamp)."""
    pts: list[tuple[float, int]] = []
    for a, b in intervals:
        pts.append((a, 1))
        pts.append((b, -1))
    pts.sort()
    cur = mx = 0
    for _, d in pts:
        cur += d
        mx = max(mx, cur)
    return mx


def _parse_intervals(events: Path) -> list[tuple[float, float]]:
    enters: dict[str, float] = {}
    out: list[tuple[float, float]] = []
    for line in events.read_text().splitlines():
        kind, slug, ts = line.split()
        if kind == "enter":
            enters[slug] = float(ts)
        else:
            out.append((enters[slug], float(ts)))
    return out


# A host-toolpack install with mocked IO + a pip that records its (enter, exit) window and
# sleeps — so the section under the env write-lock is observable across processes. The
# chokepoint (identity probe + env_write_lock) is REAL.
_TOOLPACK_WORKER = textwrap.dedent(
    """
    import os, sys, time
    os.environ["AGENTNODE_CONFIG"] = sys.argv[1]
    os.environ["AGENTNODE_LOCKFILE"] = sys.argv[2]
    sys.path.insert(0, sys.argv[6])
    from pathlib import Path
    from agentnode_sdk import installer
    events, slug, target = sys.argv[3], sys.argv[4], sys.argv[5]
    pkg = Path(sys.argv[7])
    installer.download_artifact = lambda *a, **k: None
    installer.verify_hash = lambda *a, **k: "abc123def456"
    installer.extract_archive = lambda *a, **k: pkg
    def _pip(python, package_dir, verbose=False):
        with open(events, "a") as f:
            f.write("enter %s %.6f\\n" % (slug, time.time()))
        time.sleep(0.8)
        with open(events, "a") as f:
            f.write("exit %s %.6f\\n" % (slug, time.time()))
    installer.pip_install = _pip
    installer.install_package(
        slug=slug, version="1.0", artifact_url="x",
        artifact_hash="sha256:abc123def456", entrypoint="pk.tool",
        trust_level="trusted", target_python=(target or None),
    )
    """
)


def _spawn_toolpack(script: Path, cfg: Path, lf: Path, events: Path, slug: str,
                    target: str, pkg: Path) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, str(script), str(cfg), str(lf), str(events), slug, target,
         _REPO, str(pkg)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


@pytest.fixture
def worker_script(tmp_path):
    s = tmp_path / "toolpack_worker.py"
    s.write_text(_TOOLPACK_WORKER)
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "setup.py").write_text("x")
    return s, pkg


def test_two_lockfiles_same_env_serialize(tmp_path, worker_script):
    script, pkg = worker_script
    cfg = tmp_path / "cfg"
    events = tmp_path / "events.txt"
    events.write_text("")
    # THREE processes, THREE distinct lockfiles, ALL targeting this interpreter → one env_id.
    procs = [
        _spawn_toolpack(script, cfg, tmp_path / f"lf{i}.lock", events, f"pk{i}", "", pkg)
        for i in range(3)
    ]
    for p in procs:
        assert p.wait(timeout=120) == 0
    intervals = _parse_intervals(events)
    assert len(intervals) == 3
    assert _max_concurrency(intervals) == 1     # serialized by the env write-lock


def test_same_lockfile_same_env_serialize(tmp_path, worker_script):
    script, pkg = worker_script
    cfg = tmp_path / "cfg"
    lf = tmp_path / "shared.lock"
    events = tmp_path / "events.txt"
    events.write_text("")
    # THREE processes, ONE shared lockfile, distinct slugs, same interpreter → one env_id.
    procs = [
        _spawn_toolpack(script, cfg, lf, events, f"pk{i}", "", pkg)
        for i in range(3)
    ]
    for p in procs:
        assert p.wait(timeout=120) == 0
    intervals = _parse_intervals(events)
    assert len(intervals) == 3
    assert _max_concurrency(intervals) == 1


def test_two_real_venvs_overlap(tmp_path, worker_script):
    script, pkg = worker_script
    cfg = tmp_path / "cfg"
    events = tmp_path / "events.txt"
    events.write_text("")
    base = pip_python()
    venv_a = make_target_venv(base, tmp_path / "venvA")
    venv_b = make_target_venv(base, tmp_path / "venvB")
    # Distinct interpreters → distinct env_ids → the mutating sections may run concurrently.
    pa = _spawn_toolpack(script, cfg, tmp_path / "a.lock", events, "pka", venv_a, pkg)
    pb = _spawn_toolpack(script, cfg, tmp_path / "b.lock", events, "pkb", venv_b, pkg)
    assert pa.wait(timeout=180) == 0
    assert pb.wait(timeout=180) == 0
    intervals = _parse_intervals(events)
    assert len(intervals) == 2
    assert _max_concurrency(intervals) == 2     # demonstrable overlap (different env_ids)


# ---------------------------------------------------------------------------
# Kill-mid-quarantine recovery (real agent build + real interpreter)
# ---------------------------------------------------------------------------

_KILL_WORKER = textwrap.dedent(
    """
    import os, sys
    os.environ["AGENTNODE_CONFIG"] = sys.argv[1]
    os.environ["AGENTNODE_LOCKFILE"] = sys.argv[2]
    sys.path.insert(0, sys.argv[5])
    from pathlib import Path
    from agentnode_sdk import _agent_pip as ap
    from agentnode_sdk import installer
    from tests.test_agent_m1_transaction import _lock_entry
    src, target, marker = Path(sys.argv[3]), sys.argv[4], sys.argv[6]
    def _die_at_pip(*a, **k):
        # We are past the DURABLE quarantine (the old entry has been popped) and about to
        # mutate the environment -- die hard. The OS releases the env write-lock on exit.
        Path(marker).write_text("quarantined")
        os._exit(137)
    ap.pip_install_wheel = _die_at_pip
    installer._install_agent_host_transaction(
        "rec-agent",
        lock_entry=_lock_entry(entrypoint="recagent.agent:run"),
        package_dir=src, target_python=target, policy="default",
    )
    """
)


def test_kill_mid_quarantine_recovers_under_same_lock(tmp_path):
    from tests.test_agent_m1_transaction import _lock_entry, _write_agent_source
    from agentnode_sdk.lock_integrity import seal_entry

    base = pip_python()
    venv = make_target_venv(base, tmp_path / "venv")
    src = _write_agent_source(tmp_path / "src", name="rec-agent", top="recagent")
    cfg = tmp_path / "cfg"
    lf = tmp_path / "agentnode.lock"
    marker = tmp_path / "marker.txt"
    os.environ["AGENTNODE_CONFIG"] = str(cfg)
    os.environ["AGENTNODE_LOCKFILE"] = str(lf)
    try:
        # Pre-seed an OLD sealed entry so the quarantine is a REAL durable pop.
        old = _lock_entry(entrypoint="recagent.agent:run")
        old["python_distribution"] = "rec-agent"
        old["python_distribution_version"] = "0.9.0"
        installer.update_lockfile("rec-agent", seal_entry(old), path=lf)
        assert "rec-agent" in installer.read_lockfile(lf)["packages"]

        # Installer A: builds, durably quarantines (pops the old entry), then dies at pip.
        worker = tmp_path / "kill_worker.py"
        worker.write_text(_KILL_WORKER, encoding="utf-8")
        pa = subprocess.run(
            [sys.executable, str(worker), str(cfg), str(lf), str(src), venv, _REPO,
             str(marker)],
            capture_output=True, timeout=240,
        )
        assert pa.returncode == 137, pa.stderr.decode()[-2000:]
        assert marker.read_text() == "quarantined"
        # A's durable quarantine removed the old entry and A never committed.
        assert "rec-agent" not in installer.read_lockfile(lf).get("packages", {})

        # Installer B (this process): acquires the SAME env write-lock (A released it on
        # death), recovers the absent state, and completes a fresh install + commit.
        installer._install_agent_host_transaction(
            "rec-agent",
            lock_entry=_lock_entry(entrypoint="recagent.agent:run"),
            package_dir=src, target_python=venv, policy="default",
        )
        entry = installer.read_lockfile(lf)["packages"]["rec-agent"]
        assert entry["python_distribution"] == "rec-agent"
        assert entry["build_mode"] == "host"
    finally:
        os.environ.pop("AGENTNODE_CONFIG", None)
        os.environ.pop("AGENTNODE_LOCKFILE", None)


# A host-toolpack install where pip COMPLETES (env mutated + marker + return) and the process
# is then blocked at the FINAL publish. The parent kills it there -- proving a real
# post-pip / pre-publish death, not a death inside the pip stub. Uses the real chokepoint.
_TOOLPACK_POSTPIP_KILL_WORKER = textwrap.dedent(
    """
    import os, sys, time
    os.environ["AGENTNODE_CONFIG"] = sys.argv[1]
    os.environ["AGENTNODE_LOCKFILE"] = sys.argv[2]
    sys.path.insert(0, sys.argv[4])
    from pathlib import Path
    from agentnode_sdk import installer
    pkg = Path(sys.argv[3])
    env_marker, pip_done, publish_entered = sys.argv[5], sys.argv[6], sys.argv[7]
    installer.download_artifact = lambda *a, **k: None
    installer.verify_hash = lambda *a, **k: "abc123def456"
    installer.extract_archive = lambda *a, **k: pkg
    def _pip_completes(python, package_dir, verbose=False):
        Path(env_marker).write_text("env-mutated")   # a SUCCESSFUL environment mutation
        Path(pip_done).write_text("yes")
        return None                                  # pip returns normally -> completed
    installer.pip_install = _pip_completes
    def _block_at_publish(slug, lock_entry, path):
        Path(publish_entered).write_text("yes")
        while True:
            time.sleep(1)                            # blocked at the FINAL publish
    installer._commit_toolpack_entry = _block_at_publish
    installer.install_package(
        slug="tp-rec", version="2.0", artifact_url="x",
        artifact_hash="sha256:abc123def456", entrypoint="pk.tool", trust_level="trusted",
    )
    """
)


def test_toolpack_kill_after_pip_before_publish_recovers(tmp_path, monkeypatch):
    """Blocker-2 real crash contract: installer A durably quarantines the OLD entry, pip
    COMPLETES (env mutation marker written, pip returned), then A is killed while BLOCKED at
    the final publish. The old entry stays ABSENT (never executable against the now-changed
    env); the OS releases the writer; installer B reinstalls cleanly under the same writer."""
    import time

    from agentnode_sdk.lock_integrity import seal_entry

    cfg = tmp_path / "cfg"
    lf = tmp_path / "agentnode.lock"
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "setup.py").write_text("x")
    env_marker = tmp_path / "env.marker"
    pip_done = tmp_path / "pip.done"
    publish_entered = tmp_path / "publish.entered"
    monkeypatch.setenv("AGENTNODE_CONFIG", str(cfg))    # inherited by the subprocess + used by B
    monkeypatch.setenv("AGENTNODE_LOCKFILE", str(lf))

    old = {"version": "0.9", "package_type": "toolpack", "entrypoint": "pk.tool",
           "artifact_hash": "sha256:" + "b" * 64}
    installer.update_lockfile("tp-rec", seal_entry(old), path=lf)
    assert "tp-rec" in installer.read_lockfile(lf)["packages"]

    worker = tmp_path / "tp_postpip_kill_worker.py"
    worker.write_text(_TOOLPACK_POSTPIP_KILL_WORKER, encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, str(worker), str(cfg), str(lf), str(pkg), _REPO,
         str(env_marker), str(pip_done), str(publish_entered)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        # Wait until pip has COMPLETED and the process is blocked at the publish.
        for _ in range(1200):
            if pip_done.exists() and publish_entered.exists():
                break
            if proc.poll() is not None:
                raise AssertionError(f"worker exited early: {proc.stderr.read().decode()[-2000:]}")
            time.sleep(0.05)
        assert pip_done.exists() and publish_entered.exists()
        proc.kill()                                     # hard kill: post-pip, pre-publish
        proc.wait(timeout=30)
    finally:
        if proc.poll() is None:
            proc.kill()

    assert env_marker.read_text() == "env-mutated"      # pip DID complete before the kill
    # OLD entry durably quarantined; A never published → slug ABSENT (fail-closed).
    assert "tp-rec" not in installer.read_lockfile(lf).get("packages", {})

    # Installer B reinstalls cleanly under the same (OS-released) writer.
    monkeypatch.setattr(installer, "pip_install", lambda *a, **k: None)
    monkeypatch.setattr(installer, "download_artifact", lambda *a, **k: None)
    monkeypatch.setattr(installer, "verify_hash", lambda *a, **k: "abc123def456")
    monkeypatch.setattr(installer, "extract_archive", lambda *a, **k: pkg)
    installer.install_package(
        slug="tp-rec", version="2.0", artifact_url="x",
        artifact_hash="sha256:abc123def456", entrypoint="pk.tool", trust_level="trusted",
    )
    entry = installer.read_lockfile(lf)["packages"]["tp-rec"]
    assert entry["build_mode"] == "host" and entry["version"] == "2.0"
