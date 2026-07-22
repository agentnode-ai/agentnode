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


# A host-toolpack install for the TWO-PHASE, liveness-checked concurrency barrier:
#   Phase 1 (boot barrier)     — finish imports + probe the target env, write an ATOMIC
#     boot-ready marker, wait for a shared `start` signal. Removes irrelevant Linux-runner
#     process/import/probe skew; it is NOT a concurrency proof.
#   Phase 2 (mutation barrier) — FROM INSIDE the write-locked pip stub (the env writer for
#     THIS worker's env_id is already held), write an ATOMIC mutation-ready marker, then wait
#     for a shared `release` signal.
# Markers are written atomically (os.replace) and carry pid + interpreter + env_id + a
# monotonic timestamp for the parent's diagnostics. Both waits are bounded (75s) so a worker
# never hangs if the parent dies.
_TOOLPACK_BARRIER_WORKER = textwrap.dedent(
    '''
    import json, os, sys, time
    os.environ["AGENTNODE_CONFIG"] = sys.argv[1]
    os.environ["AGENTNODE_LOCKFILE"] = sys.argv[2]
    sys.path.insert(0, sys.argv[3])
    worker_id, slug, target, pkg_dir = sys.argv[4], sys.argv[5], sys.argv[6], sys.argv[7]
    boot_ready, start_signal = sys.argv[8], sys.argv[9]
    mutation_ready, release_signal = sys.argv[10], sys.argv[11]
    from pathlib import Path
    from agentnode_sdk import installer
    from agentnode_sdk._env_lock import resolve_env_identity

    def _atomic_write(path, payload):
        tmp = path + ".tmp." + str(os.getpid())
        with open(tmp, "w") as f:
            f.write(payload)
        os.replace(tmp, path)          # atomic publish — never a half-written marker

    def _wait_for(path, timeout, what):
        deadline = time.monotonic() + timeout
        while not os.path.exists(path):
            if time.monotonic() > deadline:
                raise RuntimeError(what + " signal not received within " + str(timeout) + "s")
            time.sleep(0.02)

    env_id = resolve_env_identity(target).env_id          # also warms the probe subprocess
    def _marker(kind):
        return json.dumps({"kind": kind, "worker": worker_id, "pid": os.getpid(),
                           "interpreter": target, "env_id": env_id,
                           "monotonic": time.monotonic()})

    # Phase 1 — fully booted; wait for the shared start (removes startup skew, NOT a proof).
    _atomic_write(boot_ready, _marker("boot"))
    _wait_for(start_signal, 75, "start")

    pkg = Path(pkg_dir)
    installer.download_artifact = lambda *a, **k: None
    installer.verify_hash = lambda *a, **k: "abc123def456"
    installer.extract_archive = lambda *a, **k: pkg
    def _pip(python, package_dir, verbose=False):
        # Phase 2 — reached ONLY while THIS worker holds the env write-lock for its env_id.
        _atomic_write(mutation_ready, _marker("mutation"))
        _wait_for(release_signal, 75, "release")
    installer.pip_install = _pip
    installer.install_package(
        slug=slug, version="1.0", artifact_url="x",
        artifact_hash="sha256:abc123def456", entrypoint="pk.tool",
        trust_level="trusted", target_python=target,
    )
    '''
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


def test_two_real_venvs_concurrent_mutation_sections(tmp_path):
    """Deterministic proof that two REAL, DISTINCT target environments hold their write-locked
    mutation sections CONCURRENTLY (they use different env-id locks, so they are NOT serialized).

    Marker files persist after a worker exits, so mere co-existence of two markers is NOT a
    proof (they could have been written sequentially by a live worker and a dead one). The
    proof requires, at the SAME observation point: both mutation markers present AND both
    workers still ALIVE. A two-phase barrier:
      (0) DISTINCT env_ids are asserted first (else serialization would be correct);
      (1) BOOT barrier removes Linux-runner startup/import/probe skew (not a proof);
      (2) MUTATION barrier — each worker, while ALREADY holding its env write-lock, publishes
          a mutation marker and waits; the parent releases only when both markers exist AND
          both processes are live at once.
    A real serialization regression → one worker never reaches its mutation section → BOUNDED
    failure (~60s) with full diagnostics; never the 300s env-lock timeout. Timeouts are not
    tuned to "luck into" overlap — the synchronisation is deterministic."""
    import time

    from agentnode_sdk._env_lock import resolve_env_identity

    base = pip_python()
    venv_a = make_target_venv(base, tmp_path / "venvA")
    venv_b = make_target_venv(base, tmp_path / "venvB")

    # (0) DISTINCT environment identities — else serialization is correct and this is moot.
    ea = resolve_env_identity(venv_a)
    eb = resolve_env_identity(venv_b)
    if ea.env_id == eb.env_id:
        pytest.fail(
            "the two venvs resolved to the SAME environment identity (inconclusive):\n"
            f"  A python={venv_a}\n    purelib={ea.purelib}\n    platlib={ea.platlib}\n"
            f"    env_id={ea.env_id}\n"
            f"  B python={venv_b}\n    purelib={eb.purelib}\n    platlib={eb.platlib}\n"
            f"    env_id={eb.env_id}")

    worker = tmp_path / "barrier_worker.py"
    worker.write_text(_TOOLPACK_BARRIER_WORKER, encoding="utf-8")
    cfg = tmp_path / "cfg"
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "setup.py").write_text("x")
    boot_a, boot_b = tmp_path / "boot_a", tmp_path / "boot_b"
    start = tmp_path / "start"
    mut_a, mut_b = tmp_path / "mut_a", tmp_path / "mut_b"
    release = tmp_path / "release"

    def _spawn(lf_name, wid, slug, target, boot, mut):
        return subprocess.Popen(
            [sys.executable, str(worker), str(cfg), str(tmp_path / lf_name), _REPO, wid, slug,
             target, str(pkg), str(boot), str(start), str(mut), str(release)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    pa = _spawn("a.lock", "A", "pka", venv_a, boot_a, mut_a)
    pb = _spawn("b.lock", "B", "pkb", venv_b, boot_b, mut_b)

    def _drain(p):
        try:
            o, e = p.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            p.kill()
            o, e = p.communicate()
        return p.returncode, o.decode()[-2000:], e.decode()[-2000:]

    def _marker_text(path):
        try:
            return path.read_text()
        except OSError:
            return "<absent>"

    def _fail(reason):
        start.write_text("go")           # free a worker stuck in EITHER barrier so it exits
        release.write_text("go")
        ra, oa, era = _drain(pa)
        rb, ob, erb = _drain(pb)
        pytest.fail(
            f"{reason}\n"
            f"  A interpreter={venv_a} env_id={ea.env_id}\n"
            f"    purelib={ea.purelib} platlib={ea.platlib}\n"
            f"    boot_ready={'yes' if boot_a.exists() else 'no'} "
            f"mutation_ready={'yes' if mut_a.exists() else 'no'} exit={ra}\n"
            f"    boot_marker={_marker_text(boot_a)!r}\n"
            f"    mutation_marker={_marker_text(mut_a)!r}\n"
            f"    stdout={oa!r}\n    stderr={era!r}\n"
            f"  B interpreter={venv_b} env_id={eb.env_id}\n"
            f"    purelib={eb.purelib} platlib={eb.platlib}\n"
            f"    boot_ready={'yes' if boot_b.exists() else 'no'} "
            f"mutation_ready={'yes' if mut_b.exists() else 'no'} exit={rb}\n"
            f"    boot_marker={_marker_text(boot_b)!r}\n"
            f"    mutation_marker={_marker_text(mut_b)!r}\n"
            f"    stdout={ob!r}\n    stderr={erb!r}")

    try:
        # PHASE 1 — BOOT barrier: both workers fully booted (imports + env probe) AND alive.
        deadline = time.monotonic() + 60
        while not (boot_a.exists() and boot_b.exists()):
            if pa.poll() is not None or pb.poll() is not None:
                _fail("worker exited during boot")
            if time.monotonic() > deadline:
                _fail("workers did not both finish booting")
            time.sleep(0.02)
        if pa.poll() is not None or pb.poll() is not None:
            _fail("a worker exited between booting and start")
        start.write_text("go")

        # PHASE 2 — MUTATION barrier: both markers present AND both alive at the SAME instant.
        deadline = time.monotonic() + 60
        while True:
            a_mut, b_mut = mut_a.exists(), mut_b.exists()
            a_alive, b_alive = pa.poll() is None, pb.poll() is None
            if a_mut and b_mut:
                if not (a_alive and b_alive):
                    _fail("both markers existed but one worker was already dead")
                release.write_text("go")        # concurrency proven → release immediately
                break
            if not a_alive or not b_alive:
                _fail("worker exited before concurrent mutation was established")
            if time.monotonic() > deadline:
                _fail("only one (or neither) mutation section was reached: the two distinct "
                      "target environments did not enter concurrently")
            time.sleep(0.02)

        ra, _, era = _drain(pa)
        rb, _, erb = _drain(pb)
        assert ra == 0, f"worker A failed after release: {era}"
        assert rb == 0, f"worker B failed after release: {erb}"
    finally:
        start.write_text("go")
        release.write_text("go")
        for p in (pa, pb):
            if p.poll() is None:
                p.kill()
            try:
                p.communicate(timeout=10)
            except Exception:
                pass


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
