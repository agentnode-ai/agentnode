"""The trust refresh runs one at a time, offers one stable pair to every target, and records an
acknowledgement only when it is EXACTLY that pair; admission requires the exact pair
(trust-refresh C6, C7, C11, C12).

Real issuer, register, acknowledgements and worker-side install (the lifecycle stand)."""
from __future__ import annotations

import hashlib
import threading
import time

import pytest

from agentnode_sdk.gateway.filelock import ProcessLock
from agentnode_sdk.pki import revocation as rl
from agentnode_sdk.remote_mcp import target_ops as O
from agentnode_sdk.remote_mcp import targets as T
from agentnode_sdk.remote_mcp import targets_cli
from agentnode_sdk.remote_mcp.target_ops import OperationFailed, Operations
from agentnode_sdk.worker import trust_install as TI
from tests.test_mtls_transport import _one_boot  # noqa: F401 - autouse: one boot throughout

from .test_target_lifecycle import _provision, env  # noqa: F401 - the lifecycle stand


def _twin_ops(env, **kw):
    """A second Operations over the SAME stand, as a second process (timer + operator) would be."""
    o = env["ops"]
    return Operations(stand=o.stand, register=o.register, acks=o.acks, issuer=o.issuer, keyring_path=o.keyring_path,
                      gateway_instance=o.gateway, deployment=o.deployment, bundles_dir=o.bundles,
                      worker_for=o.worker_for, **kw)


def _crl(env):
    return (env["world"].root / "trust" / "revoked.crl").read_bytes()


def test_concurrent_refreshes_publish_once(env, monkeypatch):
    """C6 / CC13: the timer and an operator's run at once -- one publication, the other sees it."""
    monkeypatch.setattr(rl, "REFRESH_AFTER_SECONDS", 3.0)
    time.sleep(3.2)                                          # the installed list is now "due"
    issuer = env["world"].issuer
    real, published = issuer.publish, []

    def slow_publish():
        done = real()
        published.append(done.get("number"))
        time.sleep(0.6)                                      # long enough for the other to look
        return done
    monkeypatch.setattr(issuer, "publish", slow_publish)
    a, b = env["ops"], _twin_ops(env)
    gate, reports = threading.Barrier(2), []

    def run(ops):
        gate.wait()
        reports.append(ops.refresh_trust())
    threads = [threading.Thread(target=run, args=(o,)) for o in (a, b)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert len(published) == 1, published
    assert sorted(r["published"] is None for r in reports) == [False, True]
    assert all(r["lagging"] == [] for r in reports)


def test_a_timer_run_behind_a_forced_one_does_not_publish_again(env, monkeypatch):
    """N6: the operator's `--force` holds the lock and publishes; the timer's run, started while the forced
    one is publishing on a DUE list, waits, re-judges on the fresh list and publishes nothing."""
    monkeypatch.setattr(rl, "REFRESH_AFTER_SECONDS", 3.0)
    time.sleep(3.2)                                          # due for the timer as well
    issuer = env["world"].issuer
    real, published, inside = issuer.publish, [], threading.Event()

    def slow_publish():
        inside.set()
        done = real()
        published.append(done.get("number"))
        time.sleep(0.6)
        return done
    monkeypatch.setattr(issuer, "publish", slow_publish)
    reports = {}
    forced = threading.Thread(target=lambda: reports.__setitem__("force", env["ops"].refresh_trust(force=True)))
    forced.start()
    assert inside.wait(10)                                    # the forced run holds the lock, mid-publication
    reports["timer"] = _twin_ops(env).refresh_trust()
    forced.join(30)
    assert len(published) == 1 and reports["force"]["published"] == published[0]
    assert reports["timer"]["published"] is None and reports["timer"]["lagging"] == []


def test_a_lock_that_cannot_be_had_is_a_named_failure(env):
    """C6 / CC14: never a successful skip."""
    held, release = threading.Event(), threading.Event()

    def hold():
        with ProcessLock(env["ops"].stand / "targets" / O.REFRESH_LOCK_NAME, timeout=5):
            held.set()
            release.wait(10)
    t = threading.Thread(target=hold)
    t.start()
    held.wait(5)
    try:
        before = _crl(env)
        with pytest.raises(OperationFailed) as failed:
            _twin_ops(env, refresh_lock_timeout=0.5).refresh_trust(force=True)
        assert failed.value.code == "trust_refresh.busy" and _crl(env) == before
    finally:
        release.set()
        t.join(10)


def test_the_cli_exits_non_zero_when_busy(env, monkeypatch, capsys):
    busy = _twin_ops(env)
    monkeypatch.setattr(busy, "refresh_trust", lambda force=False: (_ for _ in ()).throw(
        OperationFailed("trust_refresh.busy", "held")))
    monkeypatch.setattr(targets_cli, "build", lambda stand, canary: (busy, None, None))
    assert targets_cli.main(["--stand", "S", "trust-refresh"]) == 5
    assert "trust_refresh.busy" in capsys.readouterr().out


def test_one_stable_pair_for_every_target(env):
    """C7 / CC15: an issuer publication in the middle of a refresh does not split the targets."""
    tid = _provision(env)
    env["ops"].probe(tid)
    offered, fired = {}, []
    for target_id, w in env["workers"].items():
        real = w.install_trust

        def spy(crl, tombs, _real=real, _tid=target_id):
            offered[_tid] = hashlib.sha256(crl).hexdigest()
            if not fired:
                fired.append(_tid)
                env["world"].issuer.publish()                # someone publishes while we push
            return _real(crl, tombs)
        w.install_trust = spy
    report = env["ops"].refresh_trust()
    assert len(offered) == 2 and len(set(offered.values())) == 1, offered
    assert report["lagging"] == [] and len(set(report["pushed"].values())) == 1


@pytest.mark.parametrize("field,change", [("revocation_list_number", lambda v: v + 1),
                                          ("revocation_list_number", lambda v: v - 1),
                                          ("revocation_list_sha256", lambda v: "0" * 64),
                                          ("revocation_list_next_update", lambda v: v + 1.0),
                                          ("tombstones_sha256", lambda v: "f" * 64),
                                          ("tombstones_published_at", lambda v: v - 1.0),
                                          ("tombstones_not_after", lambda v: v + 1.0)])
def test_an_inexact_answer_is_lagging_and_never_recorded(env, field, change):
    """C11 / CC18-21: number, digests and validity must all be EXACTLY the offered pair."""
    before = env["acks"].load().get("managed-w1")
    w = env["workers"]["managed-w1"]
    real = w.install_trust

    def lying(crl, tombs):
        ack = real(crl, tombs)
        return dict(ack, **{field: change(ack[field])})
    w.install_trust = lying
    report = env["ops"].refresh_trust(force=True)
    assert report["lagging"] == ["managed-w1"] and "trust.ack_mismatch" in report["pushed"]["managed-w1"]
    assert field in report["pushed"]["managed-w1"]
    assert any("managed-w1" in a for a in report["alerts"])
    assert env["acks"].load().get("managed-w1") == before


def test_an_answer_without_digests_is_lagging(env):
    """CC17 / CC23: a worker that does not confirm both digests is not acknowledged."""
    w = env["workers"]["managed-w1"]
    real = w.install_trust
    w.install_trust = lambda crl, tombs: {k: v for k, v in real(crl, tombs).items() if not k.endswith("sha256")}
    report = env["ops"].refresh_trust(force=True)
    assert report["lagging"] == ["managed-w1"]


def test_an_exact_answer_is_recorded_with_every_fact(env):
    report = env["ops"].refresh_trust(force=True)
    ack = env["acks"].load()["managed-w1"]
    expected = TI.facts_of(_crl(env), (env["world"].root / "trust" / "revoked-identities.json").read_bytes())
    assert report["lagging"] == [] and TI.differences(ack, expected) == []
    assert set(ack) == set(TI.FACT_FIELDS) | {"acked_at"}


def test_a_failing_acknowledgement_write_is_lagging(env, monkeypatch):
    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(env["acks"], "record", boom)
    report = env["ops"].refresh_trust(force=True)
    assert report["lagging"] == ["managed-w1"] and report["pushed"]["managed-w1"].startswith("failed")


def test_a_worker_failing_mid_install_is_not_acknowledged(env):
    before = env["acks"].load().get("managed-w1")
    w = env["workers"]["managed-w1"]

    def half(crl, tombs):
        raise OSError("between the two writes")
    w.install_trust = half
    report = env["ops"].refresh_trust(force=True)
    assert report["lagging"] == ["managed-w1"] and env["acks"].load().get("managed-w1") == before


def test_the_ack_records_refuse_an_incomplete_answer(env):
    with pytest.raises(T.TargetsRefused) as refused:
        env["acks"].record("managed-w1", {"revocation_list_number": 1, "acked_at": time.time()})
    assert refused.value.code == "acks.incomplete"


# ---------------------------------------------------------------------- C12: exact admission
def _facts(n=5, until=None):
    until = until or time.time() + 86400
    return {"revocation_list_number": n, "revocation_list_sha256": "a" * 64, "revocation_list_next_update": until,
            "tombstones_sha256": "b" * 64, "tombstones_published_at": until - 7 * 86400, "tombstones_not_after": until}


def test_exact_admission():
    now, dep = time.time(), _facts()
    assert T.trust_current(dict(dep, acked_at=now), now=now, deployment=dep) is True
    assert T.trust_current(dict(dep, revocation_list_number=6), now=now, deployment=dep) is False   # newer is not equal
    assert T.trust_current(dict(dep, revocation_list_number=4), now=now, deployment=dep) is False
    for field in TI.FACT_FIELDS:
        bad = dict(dep, **{field: ("c" * 64 if field.endswith("sha256") else dep[field] + 1)})
        assert T.trust_current(bad, now=now, deployment=dep) is False, field


def test_legacy_partial_and_missing_fail_closed():
    now, dep = time.time(), _facts()
    legacy = {k: v for k, v in dep.items() if not k.endswith("sha256")}
    assert T.trust_current(legacy, now=now, deployment=dep) is False
    assert T.trust_current(None, now=now, deployment=dep) is False
    assert T.trust_current(dict(dep), now=now, deployment=None) is False                # unreadable own pair
    expired = _facts(until=now - 1)
    assert T.trust_current(dict(expired), now=now, deployment=expired) is False


def test_the_router_refuses_a_legacy_ack(env):
    """End to end through activation: the stand's own pair decides, legacy refuses."""
    tid = _provision(env)
    env["ops"].probe(tid)
    ack = env["acks"].load()[tid]
    pair = env["ops"]._pair()[2]
    assert T.trust_current(ack, now=time.time(), deployment=pair) is True
    legacy = {k: v for k, v in ack.items() if not k.endswith("sha256")}
    assert T.trust_current(legacy, now=time.time(), deployment=pair) is False


# ---------------------------------------------------------------------- the pair before the last publication
def _current(env):
    trust = env["world"].root / "trust"
    return TI.facts_of((trust / "revoked.crl").read_bytes(), (trust / "revoked-identities.json").read_bytes())


def _previous(env):
    """What server.previous_trust believes: the saved pair, verified against the deployment's anchor."""
    pair = T.previous_pair(env["reg"].stand)
    if pair is None:
        return None
    rl.read(pair[0], env["world"]._ca()[1], time.time())
    return TI.facts_of(*pair)


def _admitted(env, tid="managed-w1"):
    return T.trust_current(env["acks"].load().get(tid), now=time.time(), deployment=_current(env),
                           previous=_previous(env))


def _a_serial(env):
    inv = env["world"].issuer.inventory()["entries"]
    return next(c["serial"] for e in inv.values() for c in e.get("certificates", []) if c.get("status") == "current")


def test_a_scheduled_refresh_never_refuses_a_target_mid_flight(env):
    """Between the publication and the target's acknowledgement the target holds exactly the pair the
    control plane judged by a moment before, with the same revocation content: it stays admissible
    (owner: refresh under traffic without a failed call). Judged INSIDE the window -- after the
    publication, while the worker is being offered the new pair -- and before and after it."""
    assert env["ops"].refresh_trust()["lagging"] == []        # the target holds the current pair first
    assert _admitted(env)
    w = env["workers"]["managed-w1"]
    real = w.install_trust
    seen = {"checks": 0, "refused": 0, "on_previous": 0, "new_pair_published": 0}

    def in_the_window(crl, tombs):
        before = env["acks"].load()["managed-w1"]
        seen["new_pair_published"] += 1 if TI.differences(before, _current(env)) else 0
        seen["on_previous"] += 0 if TI.differences(before, _previous(env)) else 1
        seen["checks"] += 1
        seen["refused"] += 0 if _admitted(env) else 1
        return real(crl, tombs)
    w.install_trust = in_the_window
    for _ in range(3):
        assert env["ops"].refresh_trust(force=True)["lagging"] == []
        assert _admitted(env)
    assert seen == {"checks": 3, "refused": 0, "on_previous": 3, "new_pair_published": 3}, seen
    assert T.trust_current(env["acks"].load()["managed-w1"], now=time.time(), deployment=_current(env))


def test_a_revocation_makes_the_previous_pair_inadmissible(env):
    """R0002 F-005/F-006: whatever was saved, a pair that does not refuse what the current pair refuses
    admits nothing -- a revocation published at any moment is strict at once."""
    w = env["workers"]["managed-w1"]
    assert env["ops"].refresh_trust()["lagging"] == []
    real = w.install_trust
    w.install_trust = lambda crl, tombs: (_ for _ in ()).throw(OSError("worker unreachable"))
    report = env["ops"].refresh_trust(force=True)              # P0 saved, P1 published, the worker keeps P0
    assert report["lagging"] == ["managed-w1"] and any("managed-w1" in a for a in report["alerts"])
    assert _admitted(env) is True                              # P0 refuses exactly what P1 refuses
    env["world"].issuer.revoke(_a_serial(env))                 # a revocation publishes P2: new content
    assert _previous(env)["content_sha256"] != _current(env)["content_sha256"]
    assert _admitted(env) is False                             # ... and P0 admits nothing any more
    w.install_trust = real
    assert env["ops"].refresh_trust()["lagging"] == [] and _admitted(env) is True


def test_only_one_publication_behind_is_admitted(env):
    """The saved pair is the one replaced by the LAST scheduled publication; a target two behind runs nowhere."""
    w = env["workers"]["managed-w1"]
    assert env["ops"].refresh_trust()["lagging"] == []
    w.install_trust = lambda crl, tombs: (_ for _ in ()).throw(OSError("worker unreachable"))
    env["ops"].refresh_trust(force=True)
    assert _admitted(env) is True                              # one behind, same content
    env["ops"].refresh_trust(force=True)
    assert _admitted(env) is False                             # two behind


def test_a_saved_pair_the_anchor_does_not_verify_admits_nothing(env, monkeypatch, tmp_path):
    """server.previous_trust believes the saved pair only if the control plane's own anchor verifies both
    documents; a forged, tampered or expired one is None."""
    import dataclasses
    from agentnode_sdk.pki import tombstones as tomb
    from agentnode_sdk.remote_mcp import server
    gw = env["world"].service("gateway", "g1")              # the identity the World's gateway floor is for
    tls = dataclasses.replace(env["world"].settings(gw, {"w1"}, role="gateway"),
                              identity_tombstones=str(env["world"].root / "trust" / tomb.LIST_NAME), tombstones_required=True)
    monkeypatch.setattr(server, "_worker_base", lambda stand: ({}, tls))
    stand = str(env["reg"].stand)
    trust = env["world"].root / "trust"
    crl, tombs = (trust / "revoked.crl").read_bytes(), (trust / "revoked-identities.json").read_bytes()
    T.save_previous_pair(stand, crl, tombs)
    assert server.previous_trust(stand)() == TI.facts_of(crl, tombs)
    key, cert, _ = env["world"].foreign_ca()
    T.save_previous_pair(stand, rl.build(key, cert, {}, number=99, now=time.time()), tombs)
    assert server.previous_trust(stand)() is None              # not signed by the deployment's CA
    tampered = bytearray(tombs)
    tampered[len(tampered) // 2] ^= 1
    T.save_previous_pair(stand, crl, bytes(tampered))
    assert server.previous_trust(stand)() is None
    k, c = env["world"]._ca()
    T.save_previous_pair(stand, rl.build(k, c, {}, number=98, now=time.time() - 8 * 86400), tombs)
    assert server.previous_trust(stand)() is None              # expired


def test_only_the_exact_previous_pair_with_equal_content_is_admitted():
    now = time.time()
    dep, prev = dict(_facts(6), content_sha256="e" * 64), dict(_facts(5), content_sha256="e" * 64)
    assert T.trust_current(dict(prev), now=now, deployment=dep, previous=prev) is True
    assert T.trust_current(dict(prev, revocation_list_sha256="d" * 64), now=now, deployment=dep, previous=prev) is False
    assert T.trust_current(dict(_facts(4)), now=now, deployment=dep, previous=prev) is False
    assert T.trust_current(dict(prev), now=now, deployment=dep, previous=None) is False
    assert T.trust_current(dict(prev), now=now, deployment=dep, previous=dict(prev, content_sha256="f" * 64)) is False
    assert T.trust_current(dict(prev), now=now, deployment={k: v for k, v in dep.items() if k != "content_sha256"},
                           previous=prev) is False
    expired = dict(_facts(5, until=now - 1), content_sha256="e" * 64)
    assert T.trust_current(dict(expired), now=now, deployment=dep, previous=expired) is False