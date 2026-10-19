"""The independent trust watchdog (trust-refresh C13, C14; DEFECT-LIVE-001).

A real issuer and real lists (World), a real signed register and signed acknowledgements; only
systemd is a stand-in, answering with what systemd 257 printed for a healthy calendar timer and
for the live defect. Every fault turns the watchdog red with its own named code; the positive
control is green; the watchdog changes nothing but its own state file."""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import time
from pathlib import Path

import pytest

from agentnode_sdk.pki import revocation as rl
from agentnode_sdk.pki import tombstones as tomb
from agentnode_sdk.remote_mcp import targets as T
from agentnode_sdk.remote_mcp import trust_watchdog as W
from agentnode_sdk.worker import trust_install
from tests.test_mtls_transport import _one_boot  # noqa: F401 - autouse: one boot throughout
from tests.test_mtls_transport import World

from .catalog_fixtures import raw_pub
from .target_fixtures import make_register


def _show(next_usec):
    def run(argv):
        if argv[1] == "show":
            return "LoadState=loaded\nActiveState=active\nUnitFileState=enabled\nNextElapseUSecMonotonic=%s\n" % (
                "0" if next_usec else "infinity")
        return json.dumps([{"next": next_usec, "unit": W.REFRESH_TIMER}])
    return run


class Stand:
    def __init__(self, tmp_path: Path):
        self.world = World(tmp_path / "pki")
        gw = self.world.service("gateway", "g1")
        self.tls = dataclasses.replace(self.world.settings(gw, {"w1"}, role="gateway"),
                                       identity_tombstones=str(self.world.root / "trust" / tomb.LIST_NAME),
                                       tombstones_required=True)
        self.reg, self.op = make_register(tmp_path / "stand")
        self.stand = self.reg.stand
        (Path(self.stand) / "operator_pub.hex").write_text(raw_pub(self.op).hex())
        self.acks = T.TrustAcks(self.stand, raw_pub(self.op), operator_private=self.op)
        self.state = tmp_path / "alerts" / "trust-watchdog.json"
        self.ack("managed-w1")

    def pair(self):
        return (Path(self.tls.revocation_list).read_bytes(), Path(self.tls.identity_tombstones).read_bytes())

    def facts(self):
        return trust_install.facts_of(*self.pair())

    def ack(self, target_id, *, acked_at=None, **change):
        self.acks.record(target_id, dict(self.facts(), acked_at=time.time() if acked_at is None else acked_at,
                                         **change))

    def check(self, *, now=None, next_usec="default", state_file=None):
        now = time.time() if now is None else now
        nxt = int((now + 3600) * 1e6) if next_usec == "default" else next_usec
        return W.check(self.stand, now=now, run=_show(nxt), tls=self.tls, state_file=state_file or self.state)


def _codes(report):
    return {p.split()[0] for p in report["problems"]}


@pytest.fixture
def s(tmp_path):
    return Stand(tmp_path)


def test_positive_control_is_green(s):
    report = s.check()
    assert report["state"] == "green" and report["problems"] == []
    assert report["facts"]["live_targets"] == ["managed-w1"]
    assert json.loads(s.state.read_text())["state"] == "green"


def _next_update(s):
    return rl.read(s.pair()[0], s.world._ca()[1], time.time()).next_update


H48 = 48 * 3600                                               # the owner's number, not the module's


def test_list_margin_boundary(s):
    nu = _next_update(s)
    assert "list_expires_within_48h" not in _codes(s.check(now=nu - H48))
    assert "list_expires_within_48h" in _codes(s.check(now=nu - H48 + 1))


def test_tombstone_margin_boundary(s):
    na = s.facts()["tombstones_not_after"]
    assert "tombstones_expire_within_48h" not in _codes(s.check(now=na - H48))
    assert "tombstones_expire_within_48h" in _codes(s.check(now=na - H48 + 1))


def test_margin_is_measured_from_the_effective_time(s, monkeypatch):
    """A clock set back does not buy margin: the later of clock and floor counts."""
    nu = _next_update(s)

    class View:
        def effective_time(self):
            return nu - H48 + 10

        def anchor(self):
            return s.world._ca()[1]

        def _verify_with_anchor(self, body, sig):
            return s.tls.trust("gateway")._verify_with_anchor(body, sig)
    report = W.check(s.stand, now=time.time(), run=_show(int((time.time() + 3600) * 1e6)), tls=s.tls,
                     view=View(), state_file=s.state)
    assert "list_expires_within_48h" in _codes(report)


def test_missing_ack_is_red(s):
    doc =json.loads((Path(s.stand) / "targets" / "trust-acks.json").read_text())
    doc["acks"] = {}
    doc["signature"] = {"key_id": s.acks.key_id,
                        "signature": s.op.sign(T._signed_bytes({"schema": doc["schema"], "acks": {}}, T._ACKS_DOMAIN)).hex()}
    (Path(s.stand) / "targets" / "trust-acks.json").write_bytes(T.canonical(doc) + b"\n")
    assert "ack_missing" in _codes(s.check())


def test_stale_ack_is_red(s):
    s.ack("managed-w1", acked_at=time.time() - 13 * 3600 - 5)
    assert _codes(s.check()) == {"ack_stale"}
    s.ack("managed-w1", acked_at=time.time() - 13 * 3600 + 60)
    assert s.check()["state"] == "green"


@pytest.mark.parametrize("field,value", [("revocation_list_number", 99), ("revocation_list_sha256", "0" * 64),
                                         ("revocation_list_next_update", 1.0), ("tombstones_sha256", "f" * 64),
                                         ("tombstones_published_at", 2.0), ("tombstones_not_after", 3.0)])
def test_every_field_mismatch_is_red(s, field, value):
    s.ack("managed-w1", **{field: value})
    report = s.check()
    assert "ack_mismatch" in _codes(report) and field in " ".join(report["problems"])


def test_a_legacy_ack_without_digests_is_red(s):
    legacy = {k: v for k, v in s.facts().items() if not k.endswith("_sha256")}
    doc = {"schema": T.ACKS_SCHEMA, "acks": {"managed-w1": dict(legacy, acked_at=time.time())}}
    doc["signature"] = {"key_id": s.acks.key_id, "signature": s.op.sign(T._signed_bytes(doc, T._ACKS_DOMAIN)).hex()}
    (Path(s.stand) / "targets" / "trust-acks.json").write_bytes(T.canonical(doc) + b"\n")
    assert "ack_mismatch" in _codes(s.check())


def test_an_ack_for_a_target_the_register_never_had_is_red(s):
    s.ack("ghost-target")
    assert "ack_unknown_target" in _codes(s.check())


def test_unsigned_acks_are_red(s):
    p = Path(s.stand) / "targets" / "trust-acks.json"
    doc = json.loads(p.read_text())
    doc["acks"]["managed-w1"]["acked_at"] = time.time() + 1
    p.write_bytes(T.canonical(doc) + b"\n")
    assert "register_or_acks_unavailable" in _codes(s.check())


def test_a_crl_with_a_bad_signature_is_red(s):
    key, cert, _ = s.world.foreign_ca()
    forged = rl.build(key, cert, {}, number=99, now=time.time())
    Path(s.tls.revocation_list).write_bytes(forged)
    report = s.check()
    assert "list_unusable" in _codes(report) and rl.SIGNATURE in " ".join(report["problems"])


def test_tombstones_with_a_bad_signature_are_red(s):
    p = Path(s.tls.identity_tombstones)
    doc = json.loads(p.read_text())
    doc["signature"] = ("00" if doc["signature"][:2] != "00" else "11") + doc["signature"][2:]
    p.write_text(json.dumps(doc, sort_keys=True, indent=1))
    report = s.check()
    assert "tombstones_unusable" in _codes(report) and tomb.BAD_SIGNATURE in " ".join(report["problems"])


def test_an_expired_list_is_red(s):
    key, cert = s.world._ca()
    old = rl.build(key, cert, {}, number=rl.number_of(s.pair()[0]) + 1, now=time.time() - 8 * 86400)
    Path(s.tls.revocation_list).write_bytes(old)
    report = s.check()
    assert "list_unusable" in _codes(report) and rl.EXPIRED in " ".join(report["problems"])


def test_refresh_timer_without_next_is_red(s):
    """The live defect: active, NEXT=infinity."""
    report = s.check(next_usec=None)
    assert _codes(report) == {"timer_next_not_finite"} and report["facts"]["refresh_next"] is None


def test_refresh_timer_next_beyond_the_bound_is_red(s):
    now = time.time()
    assert "timer_next_too_far" in _codes(s.check(now=now, next_usec=int((now + W.REFRESH_BOUND_SECONDS + 60) * 1e6)))


def test_unwritable_state_is_red(s, tmp_path):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")
    report = s.check(state_file=blocker / "trust-watchdog.json")
    assert report["state"] == "red" and "state_file_unwritable" in _codes(report)


def test_state_file_is_atomic_mode_0644_and_secret_free(s, tmp_path):
    s.check(next_usec=None)
    red = s.state.read_text()
    assert json.loads(red)["state"] == "red"
    if os.name == "posix":
        assert (s.state.stat().st_mode & 0o777) == 0o644
    assert not list(s.state.parent.glob(".*.tmp"))
    secret_hex = s.op.private_bytes_raw().hex()
    for forbidden in (secret_hex, "PRIVATE KEY", "BEGIN X509 CRL", s.pair()[1].decode()[:40]):
        assert forbidden not in red
    s.check()
    assert json.loads(s.state.read_text())["state"] == "green"   # a green run replaces the red state


def test_a_crash_inside_is_red_not_green(s, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("x")
    monkeypatch.setattr(W, "trust_problems", boom)
    assert "watchdog_error" in _codes(s.check())


def _tree(*roots):
    out = {}
    for root in roots:
        for p in sorted(Path(root).rglob("*")):
            if p.is_file():
                out[str(p)] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def test_the_watchdog_changes_nothing_but_its_state(s):
    """Read-only: register (pinned clock included), acks, trust lists, issuer and floors untouched."""
    clock = Path(s.stand) / "targets" / "clock.json"            # an old pin: a writing reader WOULD re-pin it
    clock.write_text(json.dumps({"high_water": time.time() - 3600}))
    before = _tree(s.stand, s.world.root)
    s.check()
    s.check(now=time.time() + 3 * 86400, next_usec=None)
    assert _tree(s.stand, s.world.root) == before


def test_main_exit_status(monkeypatch, capsys):
    monkeypatch.setattr(W, "check", lambda stand, **kw: {"state": "red", "problems": ["x"], "facts": {}})
    assert W.main(["check", "--stand", "S"]) == 1
    monkeypatch.setattr(W, "check", lambda stand, **kw: {"state": "green", "problems": [], "facts": {}})
    assert W.main(["check", "--stand", "S"]) == 0


def test_timer_check_cli(monkeypatch, capsys):
    monkeypatch.setattr(W, "timer_state", lambda unit, run=None: {"unit": unit, "LoadState": "loaded",
                                                                   "ActiveState": "active", "UnitFileState": "enabled",
                                                                   "next_usec": None})
    assert W.main(["timer-check", "--unit", W.WATCHDOG_TIMER, "--bound-seconds", "3900"]) == 1
    assert "ALERT timer_next_not_finite" in capsys.readouterr().out
