"""G2 restart/orphan hardening — reaper, strict identity, fail-closed recovery
status, startup recovery orchestration, and the freshness-aware admin summary.

No real Docker / DB here: the reaper's docker subprocess seam is injected, and the
startup orchestrator's reaper + DB-recovery steps are monkeypatched. DB-backed
smoke_running recovery + G1 dedup live in test_mcp_smoke_recovery_db.py.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.mcp import smoke_executor as ex

_VOL = "mcp-smoke-0123456789abcdef"  # a valid 16-hex smoke volume


@pytest.fixture(autouse=True)
def _recovery_ready():
    ex._set_recovery_status("ready")
    yield
    ex._set_recovery_status("ready")


# ---------------------------------------------------------------------------
# A. strict identity on every phase container
# ---------------------------------------------------------------------------


def _has_pair(argv, a, b):
    """True if argv contains a followed immediately by b (docker flag+value)."""
    return any(argv[i] == a and argv[i + 1] == b for i in range(len(argv) - 1))


@pytest.mark.parametrize(
    "builder,phase",
    [
        (ex.install_argv, "install"),
        (ex.run_argv, "runtime"),
        (ex.install_argv_pypi, "install"),
        (ex.run_argv_pypi, "runtime"),
    ],
)
def test_builders_carry_strict_name_and_labels(builder, phase):
    argv = builder("IMG", _VOL, "some-pkg", "1.0.0")
    sid = "0123456789abcdef"
    assert _has_pair(argv, "--name", f"mcp-smoke-{sid}-{phase}")
    assert _has_pair(argv, "--label", "agentnode.managed=true")
    assert _has_pair(argv, "--label", "agentnode.component=mcp-smoke")
    assert _has_pair(argv, "--label", f"agentnode.smoke_id={sid}")
    assert _has_pair(argv, "--label", f"agentnode.phase={phase}")


def test_identity_uses_volume_id_not_package_name():
    # The name must derive from our own volume id, never a user-controlled package.
    argv = ex.install_argv_pypi("IMG", _VOL, "evil; rm -rf /", "1.0.0")
    assert _has_pair(argv, "--name", "mcp-smoke-0123456789abcdef-install")
    assert not any("evil" in tok for tok in argv[: argv.index("IMG")])


def test_security_flags_and_isolation_unchanged():
    # Adding identity must not weaken the runtime sandbox.
    argv = ex.run_argv_pypi("IMG", _VOL, "pkg", "1.0.0")
    assert "--read-only" in argv
    assert _has_pair(argv, "--network", "none")
    assert _has_pair(argv, "--user", "1000:1000")
    assert "--cap-drop=ALL" in argv
    assert "--security-opt=no-new-privileges:true" in argv
    assert _has_pair(argv, "-v", f"{_VOL}:/app:ro")
    # no host source mount, no docker.sock passthrough
    assert not any("docker.sock" in tok for tok in argv)
    assert not any(
        tok.startswith("/") and ":/app" not in tok and "/tmp" not in tok
        for tok in argv
        if ":" in tok
    )


# ---------------------------------------------------------------------------
# B. reap_smoke_orphans — selection safety, ordering, idempotency, fail-closed
# ---------------------------------------------------------------------------


class _FakeDocker:
    """Records docker argv calls and returns canned (rc, out, err)."""

    def __init__(self, containers=(), volumes=(), fail=None, raise_on=None):
        self.containers = list(containers)
        self.volumes = list(volumes)
        self.fail = fail or set()  # set of subcommands to return rc=1 for
        self.raise_on = raise_on  # subcommand substring to raise TimeoutExpired
        self.calls = []

    def __call__(self, argv, input_text, timeout):
        self.calls.append(argv)
        joined = " ".join(argv)
        if self.raise_on and self.raise_on in joined:
            import subprocess

            raise subprocess.TimeoutExpired(argv, timeout)
        if "ps" in argv:
            if "ps" in self.fail:
                return 1, "", "boom"
            return 0, "\n".join(self.containers) + "\n", ""
        if "volume" in argv and "ls" in argv:
            if "volume_ls" in self.fail:
                return 1, "", "boom"
            return 0, "\n".join(self.volumes) + "\n", ""
        if "stop" in argv:
            return (1, "", "no such") if "stop" in self.fail else (0, "", "")
        if "rm" in argv and "volume" in argv:
            return (1, "", "gone") if "vol_rm" in self.fail else (0, "", "")
        if "rm" in argv:
            return (1, "", "gone") if "rm" in self.fail else (0, "", "")
        return 0, "", ""


def test_reaper_selects_by_label_and_strict_volume_regex(monkeypatch):
    monkeypatch.setattr(ex, "CONTAINER_RUNTIME", "docker")
    fake = _FakeDocker(
        containers=["cid1", "cid2"],
        volumes=[
            _VOL,
            "mcp-smoke-not16",
            "otherproj-vol",
            "mcp-smoke-GGGGGGGGGGGGGGGG",
        ],
    )
    res = ex.reap_smoke_orphans(run=fake)
    assert res["successful"] is True
    assert res["containers_found"] == 2
    assert res["containers_removed"] == 2
    # only the strict 16-hex volume is eligible; the others are left untouched
    assert res["volumes_found"] == 1
    assert res["volumes_removed"] == 1
    ps_call = next(c for c in fake.calls if "ps" in c)
    assert "label=agentnode.component=mcp-smoke" in ps_call


def test_reaper_stops_before_rm_and_container_before_volume(monkeypatch):
    monkeypatch.setattr(ex, "CONTAINER_RUNTIME", "docker")
    fake = _FakeDocker(containers=["cid1"], volumes=[_VOL])
    ex.reap_smoke_orphans(run=fake)
    seq = [" ".join(c) for c in fake.calls]
    i_stop = next(i for i, s in enumerate(seq) if "stop" in s)
    i_crm = next(
        i for i, s in enumerate(seq) if s.startswith("docker rm") or " rm cid1" in s
    )
    i_vrm = next(i for i, s in enumerate(seq) if "volume rm" in s)
    assert i_stop < i_crm < i_vrm


def test_reaper_never_prunes_or_removes_image(monkeypatch):
    monkeypatch.setattr(ex, "CONTAINER_RUNTIME", "docker")
    fake = _FakeDocker(containers=["cid1"], volumes=[_VOL])
    ex.reap_smoke_orphans(run=fake)
    for c in fake.calls:
        joined = " ".join(c)
        assert "prune" not in joined
        assert "rmi" not in joined
        assert "image rm" not in joined


def test_reaper_tolerates_already_gone(monkeypatch):
    monkeypatch.setattr(ex, "CONTAINER_RUNTIME", "docker")
    # stop + rm + vol_rm all return rc=1 (raced away) -> not an error, still successful
    fake = _FakeDocker(
        containers=["cid1"], volumes=[_VOL], fail={"stop", "rm", "vol_rm"}
    )
    res = ex.reap_smoke_orphans(run=fake)
    assert res["successful"] is True
    assert res["containers_found"] == 1
    assert res["containers_removed"] == 0  # rm raced -> not counted, no error
    assert res["errors"] == []


def test_reaper_idempotent_second_run_finds_nothing(monkeypatch):
    monkeypatch.setattr(ex, "CONTAINER_RUNTIME", "docker")
    first = _FakeDocker(containers=["cid1"], volumes=[_VOL])
    ex.reap_smoke_orphans(run=first)
    second = _FakeDocker(containers=[], volumes=[])
    res = ex.reap_smoke_orphans(run=second)
    assert res == {
        "containers_found": 0,
        "containers_stopped": 0,
        "containers_removed": 0,
        "volumes_found": 0,
        "volumes_removed": 0,
        "errors": [],
        "successful": True,
    }


def test_reaper_list_failure_is_not_successful(monkeypatch):
    monkeypatch.setattr(ex, "CONTAINER_RUNTIME", "docker")
    fake = _FakeDocker(fail={"ps"})
    res = ex.reap_smoke_orphans(run=fake)
    assert res["successful"] is False
    assert "container_list_failed" in res["errors"]


def test_reaper_volume_list_failure_is_not_successful(monkeypatch):
    monkeypatch.setattr(ex, "CONTAINER_RUNTIME", "docker")
    fake = _FakeDocker(containers=[], fail={"volume_ls"})
    res = ex.reap_smoke_orphans(run=fake)
    assert res["successful"] is False
    assert "volume_list_failed" in res["errors"]


def test_reaper_timeout_is_structured_not_raised(monkeypatch):
    monkeypatch.setattr(ex, "CONTAINER_RUNTIME", "docker")
    fake = _FakeDocker(containers=["cid1"], volumes=[_VOL], raise_on="ps")
    res = ex.reap_smoke_orphans(run=fake)
    assert res["successful"] is False
    assert "timeout" in res["errors"]


def test_reaper_no_runtime_is_successful_noop(monkeypatch):
    monkeypatch.setattr(ex, "CONTAINER_RUNTIME", None)
    calls = []
    res = ex.reap_smoke_orphans(run=lambda *a, **k: calls.append(a) or (0, "", ""))
    assert res["successful"] is True
    assert calls == []  # no runtime -> nothing enumerated


# ---------------------------------------------------------------------------
# C. fail-closed recovery status gate
# ---------------------------------------------------------------------------


def test_availability_blocks_until_recovery_ready(monkeypatch):
    monkeypatch.setattr(ex.settings, "MCP_SMOKE_MODE", "container")
    monkeypatch.setattr(ex, "CONTAINER_RUNTIME", "docker")
    ex._set_recovery_status("not_started")
    assert ex.smoke_availability() == (False, "recovery_pending")
    ex._set_recovery_status("unavailable")
    assert ex.smoke_availability() == (False, "recovery_unavailable")


def test_disabled_short_circuits_before_recovery(monkeypatch):
    monkeypatch.setattr(ex.settings, "MCP_SMOKE_MODE", "disabled")
    ex._set_recovery_status("unavailable")
    assert ex.smoke_availability() == (False, "disabled")


def test_run_smoke_unavailable_when_recovery_not_ready(monkeypatch):
    monkeypatch.setattr(ex.settings, "MCP_SMOKE_MODE", "container")
    monkeypatch.setattr(ex, "CONTAINER_RUNTIME", "docker")
    ex._set_recovery_status("unavailable")
    calls = []
    res = ex.run_smoke(
        {}, {}, run_container=lambda *a, **k: calls.append(a) or (0, "", "")
    )
    assert res["status"] == "unavailable"
    assert res["failure_reason"] == "recovery_unavailable"
    assert calls == []  # fail-closed: no container touched


# ---------------------------------------------------------------------------
# D. startup_smoke_recovery orchestration (reaper + DB step monkeypatched)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_startup_ready_when_reaper_and_db_ok(monkeypatch):
    monkeypatch.setattr(
        ex,
        "reap_smoke_orphans",
        lambda: {"successful": True, "containers_removed": 1, "volumes_removed": 2},
    )

    async def fake_db():
        return True, 3

    monkeypatch.setattr(ex, "_recover_stale_smoke_running", fake_db)
    ex._set_recovery_status("not_started")
    summary = await ex.startup_smoke_recovery()
    assert summary["status"] == "ready"
    assert ex.get_recovery_status() == "ready"
    assert summary["markers_cleared"] == 3


@pytest.mark.asyncio
async def test_startup_unavailable_when_reaper_fails(monkeypatch):
    monkeypatch.setattr(
        ex,
        "reap_smoke_orphans",
        lambda: {"successful": False, "errors": ["container_list_failed"]},
    )

    async def fake_db():
        return True, 0

    monkeypatch.setattr(ex, "_recover_stale_smoke_running", fake_db)
    summary = await ex.startup_smoke_recovery()
    assert summary["status"] == "unavailable"
    assert ex.get_recovery_status() == "unavailable"


@pytest.mark.asyncio
async def test_startup_unavailable_when_db_recovery_fails(monkeypatch):
    monkeypatch.setattr(ex, "reap_smoke_orphans", lambda: {"successful": True})

    async def fake_db():
        return False, 0

    monkeypatch.setattr(ex, "_recover_stale_smoke_running", fake_db)
    summary = await ex.startup_smoke_recovery()
    assert summary["status"] == "unavailable"
    assert ex.get_recovery_status() == "unavailable"


@pytest.mark.asyncio
async def test_startup_never_raises_when_reaper_explodes(monkeypatch):
    def boom():
        raise RuntimeError("docker exploded")

    monkeypatch.setattr(ex, "reap_smoke_orphans", boom)

    async def fake_db():
        return True, 0

    monkeypatch.setattr(ex, "_recover_stale_smoke_running", fake_db)
    summary = await ex.startup_smoke_recovery()  # must not raise
    assert summary["status"] == "unavailable"
    assert ex.get_recovery_status() == "unavailable"


# ---------------------------------------------------------------------------
# E. admin summary is freshness-aware (stale marker never shows "running")
# ---------------------------------------------------------------------------


def _sv_summary(gate_status, running_started=None):
    sv = {
        "gate_result": {
            "gates": [
                {
                    "id": "sandbox_smoke",
                    "evidence": {"status": gate_status},
                    "reason": "",
                }
            ]
        }
    }
    if running_started is not None:
        sv["smoke_running"] = {"started_at": running_started}
    return sv


def _iso(delta):
    return (datetime.now(timezone.utc) + delta).isoformat()


def test_summary_fresh_marker_shows_running():
    from app.mcp.router import _smoke_summary

    out = _smoke_summary(_sv_summary("passed", _iso(timedelta(seconds=-5))))
    assert out["smoke_status"] == "running"


def test_summary_stale_marker_falls_back_to_smoke_status():
    from app.mcp.router import _smoke_summary

    old = _iso(timedelta(minutes=-20))  # older than the 10-min TTL
    assert _smoke_summary(_sv_summary("passed", old))["smoke_status"] == "passed"
    assert _smoke_summary(_sv_summary("failed", old))["smoke_status"] == "failed"
    assert _smoke_summary(_sv_summary("not_run", old))["smoke_status"] == "not_run"


def test_summary_invalid_timestamp_not_running():
    from app.mcp.router import _smoke_summary

    out = _smoke_summary(_sv_summary("passed", "not-a-timestamp"))
    assert out["smoke_status"] == "passed"
