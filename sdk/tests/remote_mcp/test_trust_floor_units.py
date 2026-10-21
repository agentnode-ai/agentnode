"""trust-floor: the two new 5-minute timers and the floor service held to the unit policy, the watchdog's new
checks (C5, C20, C21; CC5-CC7, CC18, CC19), and the floor units' byte-exact backup/restore (C23)."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from agentnode_sdk.remote_mcp import trust_publication as TP
from agentnode_sdk.remote_mcp import trust_timers as TT
from agentnode_sdk.remote_mcp import trust_watchdog as W
from tests.test_mtls_transport import _one_boot  # noqa: F401 - autouse: one boot throughout

from .test_trust_schedule import SHIPPED, FakeSystemd, release  # noqa: F401 - the release fixture
from .test_trust_watchdog import Stand, _codes, _show, healthy_cycle

LEGACY_TIMER = ("[Unit]\nDescription=AgentNode remote MCP control floor advance every 5 min\n\n[Timer]\n"
                "OnActiveSec=30s\nOnUnitActiveSec=5min\nAccuracySec=15s\n\n[Install]\nWantedBy=timers.target\n")
LEGACY_SERVICE = ("[Unit]\nDescription=AgentNode remote MCP control: advance the issuer/gateway floor\n\n[Service]\n"
                  "Type=oneshot\nEnvironment=PYTHONPATH=/opt/agentnode/sdk\n"
                  "ExecStart=/opt/agentnode/venv/bin/python /opt/agentnode/scripts/control_floor_advance.py\n")


# ---------------------------------------------------------------------- C5: the shipped 5-minute units
@pytest.mark.parametrize("name", [TT.CYCLE_TIMER, TT.FLOOR_TIMER])
def test_the_five_minute_timers_literal_schedule(name):
    timer = dict(TT.parse_unit(SHIPPED[name])["Timer"])
    assert timer == {"OnCalendar": "*-*-* *:0/5:00 UTC", "Persistent": "true", "AccuracySec": "15s"}
    assert TT.unit_problems(name, SHIPPED[name]) == []


@pytest.mark.parametrize("name", [TT.CYCLE_TIMER, TT.FLOOR_TIMER])
def test_a_monotonic_five_minute_timer_is_refused(name):
    """CC5."""
    text = SHIPPED[name].replace("OnCalendar=*-*-* *:0/5:00 UTC", "OnUnitActiveSec=5min")
    found = TT.unit_problems(name, text)
    assert any("monotonic trigger OnUnitActiveSec" in p for p in found) and any("no OnCalendar" in p for p in found)
    assert TT.unit_problems(name, LEGACY_TIMER) != []


@pytest.mark.parametrize("name", [TT.CYCLE_TIMER, TT.FLOOR_TIMER])
def test_persistent_is_required_on_both(name):
    """CC6."""
    assert any("Persistent=true missing" in p for p in TT.unit_problems(name, SHIPPED[name].replace("Persistent=true\n", "")))


@pytest.mark.parametrize("acc", ["16s", "1min", "30s"])
def test_floor_accuracy_above_15_seconds_is_refused(acc):
    """CC7."""
    text = SHIPPED[TT.FLOOR_TIMER].replace("AccuracySec=15s", "AccuracySec=" + acc)
    assert any("AccuracySec must be at most 15s" in p for p in TT.unit_problems(TT.FLOOR_TIMER, text))


def test_the_floor_service_is_local_and_bounded():
    """C4 / CC4 (unit side): the floor service runs the control_floor module only, within 60 s, and alerts."""
    assert TT.unit_problems(TT.FLOOR_SERVICE, SHIPPED[TT.FLOOR_SERVICE]) == []
    pushing = SHIPPED[TT.FLOOR_SERVICE].replace("agentnode_sdk.remote_mcp.control_floor --stand /var/lib/agentnode-mcp/stand",
                                                "agentnode_sdk.remote_mcp.targets_cli --stand S trust-cycle")
    assert any("must not distribute" in p or "control floor module" in p
               for p in TT.unit_problems(TT.FLOOR_SERVICE, pushing))
    slow = SHIPPED[TT.FLOOR_SERVICE].replace("TimeoutStartSec=60", "TimeoutStartSec=5min")
    assert any("TimeoutStartSec must be at most 60s" in p for p in TT.unit_problems(TT.FLOOR_SERVICE, slow))
    assert any("OnFailure" in p for p in TT.unit_problems(TT.FLOOR_SERVICE, SHIPPED[TT.FLOOR_SERVICE].replace(
        "OnFailure=rmcp-alert@%n.service\n", "")))
    assert TT.unit_problems(TT.FLOOR_SERVICE, LEGACY_SERVICE) != []


def test_the_cycle_service_alerts_and_ends():
    assert TT.unit_problems(TT.CYCLE_SERVICE, SHIPPED[TT.CYCLE_SERVICE]) == []
    assert any("TimeoutStartSec" in p for p in TT.unit_problems(
        TT.CYCLE_SERVICE, SHIPPED[TT.CYCLE_SERVICE].replace("TimeoutStartSec=4min\n", "")))


def test_systemd_analyze_agrees_on_five_minutes():
    import shutil
    import subprocess
    if not shutil.which("systemd-analyze"):
        pytest.skip("systemd-analyze is not on this machine (the Linux run proves it)")
    out = subprocess.run(["systemd-analyze", "calendar", "--iterations=3", "*-*-* *:0/5:00 UTC"],
                         stdout=subprocess.PIPE, universal_newlines=True, env=dict(os.environ, TZ="UTC")).stdout
    assert out.count(":") >= 6 and "Next elapse" in out


# ---------------------------------------------------------------------- C20/C21: the watchdog
@pytest.fixture
def s(tmp_path):
    return Stand(tmp_path)


def test_positive_control_with_the_new_checks(s):
    report = s.check()
    assert report["state"] == "green", report["problems"]
    assert report["facts"][TT.CYCLE_TIMER + "_next"] and report["facts"][TT.FLOOR_TIMER + "_next"]


@pytest.mark.parametrize("unit", [TT.FLOOR_TIMER, TT.CYCLE_TIMER])
def test_a_five_minute_timer_without_next_is_red(s, unit):
    """CC19: the floor timer (and the cycle's) is watched like the others."""
    now = time.time()
    healthy_cycle(s.stand, at=now)
    base = _show(int((now + 3600) * 1e6), five_usec=int((now + 60) * 1e6))

    def run(argv):
        u = argv[2] if argv[1] == "show" else argv[-1]
        if u == unit:
            if argv[1] == "show":
                return "LoadState=loaded\nActiveState=active\nUnitFileState=enabled\nNextElapseUSecMonotonic=infinity\n"
            return json.dumps([{"next": None, "unit": u}])
        return base(argv)
    report = W.check(s.stand, now=now, run=run, tls=s.tls, state_file=s.state)
    assert any(p.startswith("timer_next_not_finite " + unit) for p in report["problems"])


def test_a_cycle_that_does_not_succeed_is_red(s):
    s.cycle_ok = False
    healthy_cycle(s.stand, at=time.time() - 3600)
    assert "trust_cycle_not_succeeding" in _codes(s.check())
    TP.cycle_state_path(s.stand).unlink()
    assert "trust_cycle_not_succeeding" in _codes(s.check())


def _record(s, rid, **fields):
    d = TP.records_dir(s.stand)
    d.mkdir(parents=True, exist_ok=True)
    rec = {"schema": TP.SCHEMA, "record_id": rid, "kind": TP.PUBLICATION, "reason": "rotation", "state": TP.OPEN,
           "phase": "previous_saved", "created_at": time.time(), "from": None, "result": None, "targets": {}}
    rec.update(fields)
    (d / (rid + ".json")).write_text(json.dumps(rec))
    return rec


def test_a_hung_publication_is_red_after_ten_minutes(s):
    _record(s, "r-000000000001", created_at=time.time() - 599)
    assert "trust_publication_hung" not in _codes(s.check())
    _record(s, "r-000000000001", created_at=time.time() - 601)
    report = s.check()
    hung = [p for p in report["problems"] if p.startswith("trust_publication_hung")]
    assert hung and ("lock not held" in hung[0] or "lock state unknown" in hung[0] or "no lock file" in hung[0])


def test_an_unreadable_journal_or_pointer_is_red(s):
    d = TP.records_dir(s.stand)
    d.mkdir(parents=True, exist_ok=True)
    (d / "r-000000000001.json").write_text("{trunc")
    assert "trust_journal_unreadable" in _codes(s.check())
    (d / "r-000000000001.json").unlink()
    TP.pointer_path(s.stand).write_text("{trunc")
    assert "previous_pointer_unreadable" in _codes(s.check())


def _save_generation(s, rid="r-000000000001"):
    """A predecessor generation exactly as the authority writes it (pointer + immutable directory)."""
    import hashlib
    crl, tombs = s.pair()
    gen = "g-" + rid[2:]
    g = TP.generations_dir(s.stand) / gen
    g.mkdir(parents=True)
    (g / "revoked.crl").write_bytes(crl)
    (g / "revoked-identities.json").write_bytes(tombs)
    from agentnode_sdk.worker import trust_install
    facts = trust_install.facts_of(crl, tombs)
    TP.pointer_path(s.stand).write_text(json.dumps({
        "schema": TP.POINTER_SCHEMA, "generation": gen, "record_id": rid,
        "revocation_list_sha256": hashlib.sha256(crl).hexdigest(), "tombstones_sha256": hashlib.sha256(tombs).hexdigest(),
        "revocation_list_number": facts["revocation_list_number"], "content_sha256": facts["content_sha256"]}))
    return facts


def test_the_predecessor_pointer_must_match_what_its_record_saved(s):
    facts = _save_generation(s)
    _record(s, "r-000000000001", state=TP.DONE, phase="distributing", **{"from": facts},
            previous={"generation": "g-000000000001"})
    assert {"previous_missing", "previous_mismatch"}.isdisjoint(_codes(s.check()))
    _record(s, "r-000000000001", state=TP.DONE, phase="distributing", **{"from": dict(facts, tombstones_sha256="0" * 64)})
    assert "previous_mismatch" in _codes(s.check())
    (TP.generations_dir(s.stand) / "g-000000000001" / "revoked.crl").write_bytes(b"x")
    assert "previous_missing" in _codes(s.check())


def test_ack_on_the_predecessor_is_green_only_inside_ten_minutes(s, monkeypatch):
    """R10 / CC18: exactly the admissible predecessor -> `ack_previous` for 10 min, then `distribution_overdue`."""
    prev_facts = _save_generation(s)
    _record(s, "r-000000000001", state=TP.DONE, phase="distributing", **{"from": prev_facts})
    s.world.issuer.publish()                                    # the deployment moved on; the target did not
    cur = s.facts()
    _record(s, "r-000000000002", state=TP.DONE, phase="distributing", **{"from": prev_facts, "result": cur},
            published_at=time.time() - 300)
    report = s.check()
    assert "managed-w1" in report["facts"].get("ack_previous", []) and report["state"] == "green", report["problems"]
    _record(s, "r-000000000002", state=TP.DONE, phase="distributing", **{"from": prev_facts, "result": cur},
            published_at=time.time() - 601)
    assert "distribution_overdue" in _codes(s.check())


def test_an_ack_on_neither_pair_is_red_at_once(s):
    s.world.issuer.publish()
    s.world.issuer.publish()
    assert "ack_mismatch" in _codes(s.check())


# ---------------------------------------------------------------------- C23: the legacy floor units
def _legacy_root(tmp_path):
    root = tmp_path / "root"
    etc = root / "etc" / "systemd" / "system"
    etc.mkdir(parents=True)
    (etc / TT.FLOOR_TIMER).write_text(LEGACY_TIMER)
    (etc / TT.FLOOR_SERVICE).write_text(LEGACY_SERVICE)
    os.chmod(etc / TT.FLOOR_TIMER, 0o644)
    os.chmod(etc / TT.FLOOR_SERVICE, 0o640)
    script = root / TT.LEGACY_SCRIPT
    script.parent.mkdir(parents=True)
    script.write_text("print('legacy')\n")
    return root, etc, script


def test_install_backs_up_and_remove_restores_the_floor_units_byte_exact(tmp_path, release):
    """C23: bytes, type, mode, owner and digest restored; the legacy script never touched; the legacy timer
    enabled and started again; the backup is gone once proven."""
    root, etc, script = _legacy_root(tmp_path)
    before = {n: TT._meta(etc / n) for n in TT.FLOOR_UNITS}
    script_before = TT._meta(script)
    fake = FakeSystemd(root)
    assert TT.install(release, root=root, run=fake, now=lambda: fake.now)["installed"]
    assert (etc / TT.FLOOR_TIMER).read_bytes() == (release / TT.SOURCE / TT.FLOOR_TIMER).read_bytes()
    assert (TT.legacy_dir(root) / "manifest.json").exists()
    assert TT.install(release, root=root, run=fake, now=lambda: fake.now)["installed"]   # a reinstall keeps it
    manifest = json.loads((TT.legacy_dir(root) / "manifest.json").read_text())
    assert manifest["units"][TT.FLOOR_TIMER]["sha256"] == before[TT.FLOOR_TIMER]["sha256"]
    done = TT.remove(root=root, run=fake, preflight=False)
    assert done == {"removed": True, "left": []}, done
    assert {n: TT._meta(etc / n) for n in TT.FLOOR_UNITS} == before
    assert TT._meta(script) == script_before
    assert ["systemctl", "enable", "--now", TT.FLOOR_TIMER] in fake.calls
    assert not TT.legacy_dir(root).exists()


def test_no_backup_is_fine_only_where_there_never_was_a_floor_unit(tmp_path):
    """A host that never had floor units (nothing installed): nothing to restore. A floor unit with no backup:
    named, never silently deleted."""
    root = tmp_path / "root"
    assert TT.restore_legacy_floor(root=root, run=FakeSystemd(root)) == []
    etc = root / "etc" / "systemd" / "system"
    etc.mkdir(parents=True)
    (etc / TT.FLOOR_TIMER).write_text(LEGACY_TIMER)
    assert TT.restore_legacy_floor(root=root, run=FakeSystemd(root)) == ["no legacy floor backup to restore"]
    assert (etc / TT.FLOOR_TIMER).read_text() == LEGACY_TIMER


def test_a_changed_legacy_script_is_reported(tmp_path, release):
    root, etc, script = _legacy_root(tmp_path)
    fake = FakeSystemd(root)
    assert TT.install(release, root=root, run=fake, now=lambda: fake.now)["installed"]
    script.write_text("print('someone changed me')\n")
    done = TT.remove(root=root, run=fake, preflight=False)
    assert done["removed"] is False and any("legacy script" in item for item in done["left"])


def test_a_symlinked_floor_unit_is_refused_before_anything_changes(tmp_path, release):
    root, etc, _script = _legacy_root(tmp_path)
    if os.name == "nt":
        pytest.skip("symlinks need privileges on Windows")
    (etc / TT.FLOOR_SERVICE).unlink()
    os.symlink(str(tmp_path / "elsewhere.service"), str(etc / TT.FLOOR_SERVICE))
    done = TT.install(release, root=root, run=FakeSystemd(root))
    assert done["installed"] is False and done["stage"] == "floor-backup"


@pytest.mark.skipif(not Path("/proc/locks").exists(), reason="the kernel's lock table is a Linux file")
@pytest.mark.parametrize("name", ["DISTRIBUTION", "PUBLICATION"])
def test_rollback_refuses_a_held_publication_or_distribution_lock(tmp_path, name):
    """C22 / negative: a lock held by a running publication or distribution refuses the rollback (read from
    /proc/locks, never by taking it)."""
    import threading
    from agentnode_sdk.gateway.filelock import ProcessLock
    from agentnode_sdk.remote_mcp import target_ops as O
    from .test_attest_watchdog import _quiet_systemd
    s = Stand(tmp_path)
    lock_name = TP.DISTRIBUTION_LOCK_NAME if name == "DISTRIBUTION" else O.REFRESH_LOCK_NAME
    held, release = threading.Event(), threading.Event()

    def hold():
        with ProcessLock(Path(s.stand) / "targets" / lock_name, timeout=5):
            held.set()
            release.wait(30)
    t = threading.Thread(target=hold)
    t.start()
    held.wait(5)
    try:
        found = TT.quiesce(stand=str(s.stand), trust_check=s.trust_refusals, run=_quiet_systemd([]), wait_seconds=0)
    finally:
        release.set()
        t.join(10)
    assert any(lock_name in p and "lock held" in p for p in found), found


def test_rollback_refuses_an_open_publication_record(tmp_path):
    """C22 / CC21."""
    s = Stand(tmp_path)
    _record(s, "r-000000000001")
    found = TT.publication_problems(str(s.stand), now=time.time(), trust_check=s.trust_refusals)
    assert any("open trust publication record" in p for p in found)


def test_rollback_refuses_a_target_refused_on_trust(tmp_path):
    s = Stand(tmp_path)
    s.world.issuer.publish()
    found = TT.publication_problems(str(s.stand), now=time.time(), trust_check=s.trust_refusals)
    assert any("unresolved trust distribution: managed-w1" in p for p in found)
    assert TT.publication_problems(str(Path(s.stand)), now=time.time(),
                                   trust_check=lambda st, now: (_ for _ in ()).throw(OSError("x")))[0].startswith(
        "unresolved trust distribution: trust state unreadable")
