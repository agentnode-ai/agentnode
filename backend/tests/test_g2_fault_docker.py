"""G2 restart/orphan hardening — REAL Docker fault-test (isolated CI only).

Proves the last open G2 gap on a real Linux Docker engine: run the real
``run_and_store_smoke`` path, SIGKILL it mid-install (so the Python ``finally``
never runs), leaving a genuine orphaned smoke volume + ``smoke_running`` marker;
then run a fresh startup recovery and assert:

  * only strictly-identified AgentNode smoke resources are reaped,
  * foreign / similar-but-invalid control resources survive untouched,
  * the marker is cleared submission-specifically,
  * the stored SmokeResult is preserved byte-for-byte (canonical JSON),
  * the gate is recomputed, and the recovery gate ends ``ready``.

Gated behind ``RUN_G2_FAULT_TEST=1`` (its own workflow) — skipped in normal CI and
locally (needs Docker + the pinned sandbox image + Postgres). Exactly ONE real
fault-smoke per run; no retries.
"""

import json
import os
import re
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import select

from tests.conftest import setup_publisher_user
from app.config import settings
from app.database import async_session_factory
from app.mcp.models import McpSubmission
from app.mcp.router import _smoke_summary

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_G2_FAULT_TEST") != "1",
    reason="G2 real-docker fault-test runs only in its dedicated workflow",
)

_HERE = Path(__file__).parent
_BACKEND = _HERE.parent
_RUNNER = str(_HERE / "_g2_fault_runner.py")
_RECOVERY = str(_HERE / "_g2_recovery_proc.py")
_LABEL = "agentnode.component=mcp-smoke"
_VOL_RE = re.compile(r"^mcp-smoke-[0-9a-f]{16}$")

# Control resources (exactly the founder's negative-control set). Foreign or
# similar-but-invalid — the reaper must preserve every one of them.
_CTRL_CONTAINERS = {
    "fault-control-unrelated": [],  # no AgentNode label
    "mcp-smoke-control-invalid": [],  # smoke-ish NAME but no label
    "fault-control-labeled": [
        "--label",
        "agentnode.component=not-mcp-smoke",
    ],  # wrong value
}
_CTRL_VOLUMES = [
    "fault-control-volume",  # unrelated
    "mcp-smoke-nothex",  # prefix but not hex
    "mcp-smoke-0123456789abcdef-extra",  # 16 hex + extra -> fails strict regex
]


def _docker(*args, check=True, timeout=120):
    r = subprocess.run(
        ["docker", *args], capture_output=True, text=True, timeout=timeout
    )
    if check and r.returncode != 0:
        raise RuntimeError(f"docker {' '.join(args)} failed: {r.stderr.strip()}")
    return r.stdout.strip()


def _lines(s):
    return [x.strip() for x in s.splitlines() if x.strip()]


def _agentnode_smoke_containers():
    return _lines(_docker("ps", "-aq", "--filter", f"label={_LABEL}", check=False))


def _agentnode_smoke_volumes():
    return [
        v
        for v in _lines(
            _docker("volume", "ls", "-q", "--filter", "name=mcp-smoke-", check=False)
        )
        if _VOL_RE.match(v)
    ]


def _container_exists(name):
    return (
        subprocess.run(
            ["docker", "container", "inspect", name], capture_output=True
        ).returncode
        == 0
    )


def _volume_exists(name):
    return (
        subprocess.run(
            ["docker", "volume", "inspect", name], capture_output=True
        ).returncode
        == 0
    )


def _controls_snapshot():
    return {
        **{f"c:{n}": _container_exists(n) for n in _CTRL_CONTAINERS},
        **{f"v:{n}": _volume_exists(n) for n in _CTRL_VOLUMES},
    }


@pytest.fixture
def controls():
    # create foreign control resources on the (fresh, isolated) runner
    for name, extra in _CTRL_CONTAINERS.items():
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
        _docker("run", "-d", "--name", name, *extra, "alpine:3.20", "sleep", "3600")
    for v in _CTRL_VOLUMES:
        subprocess.run(["docker", "volume", "rm", "-f", v], capture_output=True)
        _docker("volume", "create", v)
    yield
    # teardown: remove ONLY the explicit controls + any strict smoke leftovers
    for name in _CTRL_CONTAINERS:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    for v in _CTRL_VOLUMES:
        subprocess.run(["docker", "volume", "rm", "-f", v], capture_output=True)
    for c in _agentnode_smoke_containers():
        subprocess.run(["docker", "rm", "-f", c], capture_output=True)
    for v in _agentnode_smoke_volumes():
        subprocess.run(["docker", "volume", "rm", "-f", v], capture_output=True)


def _run_recovery():
    r = subprocess.run(
        [sys.executable, _RECOVERY],
        cwd=str(_BACKEND),
        capture_output=True,
        text=True,
        env={**os.environ, "MCP_SMOKE_MODE": "disabled"},
        timeout=240,
    )
    assert r.returncode == 0, f"recovery proc failed: {r.stderr}"
    line = next(
        (x for x in r.stdout.splitlines() if x.startswith("G2_RECOVERY_JSON=")), None
    )
    assert line, f"no recovery JSON in output:\n{r.stdout}\n{r.stderr}"
    return json.loads(line[len("G2_RECOVERY_JSON=") :])


@pytest.mark.asyncio
async def test_g2_docker_fault_reaper_e2e(client, session, controls):
    # ---- 0. environment evidence + preflight ----
    print("DOCKER:", _docker("version", "--format", "server={{.Server.Version}}"))
    print("IMAGE :", settings.MCP_SMOKE_IMAGE)
    print("PYTHON:", sys.version.split()[0])
    assert _agentnode_smoke_containers() == [], (
        "unexpected pre-existing smoke containers"
    )
    assert _agentnode_smoke_volumes() == [], "unexpected pre-existing smoke volumes"

    controls_before = _controls_snapshot()
    assert all(controls_before.values()), f"controls not all created: {controls_before}"
    print("CONTROLS BEFORE:", controls_before)

    # ---- 1. one internal test submission w/ a synthetic prior SmokeResult ----
    tag = os.urandom(4).hex()
    _tok, pub = await setup_publisher_user(
        client,
        f"g2f{tag}@t.dev",
        f"g2f{tag}",
        "TestPass123!",
        f"pub-g2f{tag}",
        "G2 Fault",
    )
    manifest = {
        "runtime": "mcp",
        "package_id": f"time-mcp-fault-{tag}",
        "mcp_server": {
            "pypi_package": "mcp-server-time",
            "command": ["uvx", "mcp-server-time==2026.7.10"],
        },
    }
    # Synthetic prior smoke: PASSED but deliberately key-mismatched + expired so the
    # G1 dedup guard does NOT skip the fault run (the child must actually install),
    # while still proving preservation. Distinctive marker for canonical comparison.
    synthetic_smoke = {
        "status": "passed",
        "runtime": "pypi",
        "package": "mcp-server-time",
        "version": "2026.7.10",
        "initialized": True,
        "tools_count": 2,
        "image_digest": "sha256:synthetic-old-digest-not-current",
        "schema_version": 1,
        "run_model": "console_script",
        "checked_at": "2020-01-01T00:00:00+00:00",
        "expires_at": "2020-02-01T00:00:00+00:00",
        "synthetic_test_marker": "g2-fault-preserve-me",
    }
    sv = {
        "registry": "pypi",
        "package_name": "mcp-server-time",
        "package_exists": True,
        "resolved_version": "2026.7.10",
        "command_pinning": "pinned",
        "smoke": synthetic_smoke,
    }
    sub = McpSubmission(
        publisher_id=UUID(str(pub["id"])),
        package_name="mcp-server-time",
        package_registry="pypi",
        package_version="2026.7.10",
        manifest_raw=manifest,
        verification_report={"note": "g2-fault-report-preserve"},
        server_verification=sv,
        status="REVIEW_NEEDED",
    )
    session.add(sub)
    await session.commit()
    sub_id = str(sub.id)
    smoke_canon = json.dumps(synthetic_smoke, sort_keys=True)

    # ---- 2. spawn the REAL runner child; capture its PID + output ----
    child_log = _HERE / f"_g2_child_{tag}.log"
    child_fh = open(child_log, "w")
    child = subprocess.Popen(
        [sys.executable, _RUNNER, sub_id],
        cwd=str(_BACKEND),
        env={**os.environ},
        stdout=child_fh,
        stderr=subprocess.STDOUT,
    )
    runner_pid = child.pid
    print("RUNNER PID:", runner_pid)

    # ---- 3. wait for the install container, inspect identity + isolation ----
    install_cid = None
    deadline = time.time() + 90
    while time.time() < deadline:
        cids = _lines(
            _docker(
                "ps",
                "-aq",
                "--filter",
                "label=agentnode.phase=install",
                "--filter",
                f"label={_LABEL}",
                check=False,
            )
        )
        if cids:
            install_cid = cids[0]
            break
        if child.poll() is not None:
            break
        time.sleep(0.2)
    if not install_cid:
        try:
            child.wait(timeout=5)
        except Exception:
            pass
        child_fh.flush()
        print(
            "---- CHILD OUTPUT ----\n"
            + child_log.read_text()
            + "\n----------------------"
        )
    assert install_cid, "install container never observed -> INCONCLUSIVE (no retry)"

    inspect = None
    for _ in range(3):  # tolerate a list/inspect race without masking a real defect
        r = subprocess.run(
            ["docker", "inspect", install_cid], capture_output=True, text=True
        )
        if r.returncode == 0:
            inspect = json.loads(r.stdout)[0]
            break
        time.sleep(0.2)
    assert inspect, "install container vanished before inspect -> INCONCLUSIVE"

    name = inspect["Name"].lstrip("/")
    labels = inspect["Config"]["Labels"] or {}
    sid = labels.get("agentnode.smoke_id")
    print("INSTALL CONTAINER:", name, "labels=", labels)
    assert sid and re.fullmatch(r"[0-9a-f]{16}", sid)
    assert name == f"mcp-smoke-{sid}-install"
    assert labels.get("agentnode.component") == "mcp-smoke"
    assert labels.get("agentnode.managed") == "true"
    assert labels.get("agentnode.phase") == "install"
    assert inspect["Config"]["Image"] == settings.MCP_SMOKE_IMAGE
    mounts = inspect["Mounts"]
    assert any(
        m.get("Name") == f"mcp-smoke-{sid}"
        and m.get("Destination") == "/app"
        and m.get("RW")
        for m in mounts
    ), f"expected rw smoke volume mount: {mounts}"
    assert not any("docker.sock" in json.dumps(m) for m in mounts)
    hc = inspect["HostConfig"]
    assert "ALL" in (hc.get("CapDrop") or [])
    assert any("no-new-privileges" in s for s in (hc.get("SecurityOpt") or []))
    assert hc.get("PidsLimit") and hc.get("Memory")

    # ---- 4. SIGKILL exactly the captured runner PID (simulate crashed finally) ----
    os.kill(runner_pid, signal.SIGKILL)
    kill_ts = datetime.now(timezone.utc).isoformat()
    print("SIGKILL runner pid", runner_pid, "at", kill_ts)
    try:
        child.wait(timeout=20)
    except Exception:
        pass

    # ---- 5. orphan proof: marker present + volume present ----
    orphan_vol = f"mcp-smoke-{sid}"
    async with async_session_factory() as s2:
        row = (
            await s2.execute(select(McpSubmission).where(McpSubmission.id == sub.id))
        ).scalar_one()
        assert "smoke_running" in (row.server_verification or {}), (
            "smoke_running marker not left behind -> INCONCLUSIVE"
        )
    assert _volume_exists(orphan_vol), "orphan volume not left behind -> INCONCLUSIVE"
    print(
        "ORPHAN STATE: marker=present volume=",
        orphan_vol,
        "container_still_present=",
        _container_exists(name),
    )

    # ---- 6. fresh startup recovery ----
    payload = _run_recovery()
    summary, reap = payload["summary"], payload["summary"]["reaper"]
    print("RECOVERY SUMMARY:", json.dumps(summary))
    assert summary["status"] == "ready"
    assert reap["successful"] is True
    assert summary["markers_cleared"] >= 1
    assert reap["volumes_removed"] >= 1
    assert reap["errors"] == []
    assert payload["recovery_status"] == "ready"
    assert payload["smoke_availability"] == [False, "disabled"]

    # ---- 7. reaper safety: AgentNode gone, controls survive, no image/prune ----
    assert _agentnode_smoke_containers() == []
    assert _agentnode_smoke_volumes() == []
    assert not _volume_exists(orphan_vol)
    controls_after = _controls_snapshot()
    print("CONTROLS AFTER:", controls_after)
    assert controls_after == controls_before, "a foreign control resource was touched!"
    # pinned image still present (never removed)
    assert (
        subprocess.run(
            ["docker", "image", "inspect", settings.MCP_SMOKE_IMAGE],
            capture_output=True,
        ).returncode
        == 0
    )

    # ---- 8. DB: marker cleared, SmokeResult byte-preserved, gate recomputed ----
    async with async_session_factory() as s3:
        row = (
            await s3.execute(select(McpSubmission).where(McpSubmission.id == sub.id))
        ).scalar_one()
        sv2 = row.server_verification or {}
        assert "smoke_running" not in sv2
        assert json.dumps(sv2.get("smoke"), sort_keys=True) == smoke_canon, (
            "stored SmokeResult was mutated"
        )
        assert row.verification_report == {"note": "g2-fault-report-preserve"}
        assert sv2.get("registry") == "pypi"
        assert sv2.get("resolved_version") == "2026.7.10"
        assert sv2.get("package_exists") is True
        assert "gate_result" in sv2  # recomputed via the central helper
        # no other submission rows carry a marker
        remaining = (
            (
                await s3.execute(
                    select(McpSubmission).where(
                        McpSubmission.server_verification.has_key("smoke_running")
                    )
                )
            )
            .scalars()
            .all()
        )
        assert remaining == []

    # ---- 9. admin summary via the real helper (fresh -> running; stale -> fallback) ----
    fresh = datetime.now(timezone.utc).isoformat()
    gate = {"gates": [{"id": "sandbox_smoke", "evidence": {"status": "passed"}}]}
    assert (
        _smoke_summary({"smoke_running": {"started_at": fresh}, "gate_result": gate})[
            "smoke_status"
        ]
        == "running"
    )
    assert (
        _smoke_summary(
            {
                "smoke_running": {"started_at": "2020-01-01T00:00:00+00:00"},
                "gate_result": gate,
            }
        )["smoke_status"]
        == "passed"
    )

    # ---- 10. idempotency: recovery again on the now-clean host is a clean no-op ----
    payload2 = _run_recovery()
    reap2 = payload2["summary"]["reaper"]
    assert reap2["successful"] is True
    assert reap2["containers_found"] == 0 and reap2["containers_removed"] == 0
    assert reap2["volumes_found"] == 0 and reap2["volumes_removed"] == 0
    assert reap2["errors"] == []
    assert payload2["summary"]["markers_cleared"] == 0
    assert _controls_snapshot() == controls_before  # controls STILL survive
    print("IDEMPOTENT re-run: clean 0/0/0/0, controls intact")
