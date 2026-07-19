"""P0.3 — community toolpacks build+run inside the container (volume-cache model).

The exec-sandbox bow's last piece. Community/non-trusted toolpacks are:
  * BUILT once inside the container into a deterministic per-pack-version volume
    (setup.py / PEP-517 hooks run isolated, never on the host);
  * RUN per call in an ephemeral container that mounts ONLY that volume (ro),
    with a clean HOME, no host paths, no secrets, and the network derived from the
    declared permission (allowlist; unknown = deny).

No real containers run here: a real ContainerBackend produces the genuine hardened
argv, but its `run_process` is replaced with a recorder and `subprocess.run`
(volume inspect / rm) is mocked. Image is PENDING → in real environments
check_available() stays False and all of this is fully fail-closed.
"""
from __future__ import annotations

import json
from unittest import mock

import pytest

from agentnode_sdk import installer
from agentnode_sdk.runtimes.python_runner import _resolve_container_target
from tests.hostpolicy import run_python
from agentnode_sdk.sandbox import sandbox_volume_name, set_default_backend
from agentnode_sdk.sandbox.backend import SandboxBackend
from agentnode_sdk.sandbox.container_backend import ContainerBackend
from agentnode_sdk.sandbox.types import SandboxAvailability


class _Recorder:
    """Stands in for backend.run_process — captures the real wrap_command argv
    and the stdin payload, returns a configurable (returncode, stdout, stderr)."""

    def __init__(self, backend: ContainerBackend):
        self.backend = backend
        self.calls: list[tuple[list[str], str | None]] = []
        self.response: tuple[int, str, str] = (0, json.dumps({"ok": True, "result": None}), "")

    def __call__(self, spec, input_text=None, timeout=120.0):
        self.calls.append((self.backend.wrap_command(spec), input_text))
        return self.response

    @property
    def last_argv(self) -> list[str]:
        return self.calls[-1][0]

    @property
    def last_input(self) -> str | None:
        return self.calls[-1][1]


def _available_backend(monkeypatch) -> tuple[ContainerBackend, _Recorder]:
    """Real ContainerBackend (real hardened argv), forced available, with
    run_process recorded. Set in-body so it wins over the conftest global."""
    be = ContainerBackend(runtime="docker")
    monkeypatch.setattr(be, "check_available", lambda: SandboxAvailability(
        available=True, backend="docker", reason="", daemon_ok=True, image_available=True))
    rec = _Recorder(be)
    be.run_process = rec  # type: ignore[method-assign]
    set_default_backend(be)
    return be, rec


@pytest.fixture(autouse=True)
def _reset_backend():
    yield
    set_default_backend(None)


# ===========================================================================
# INSTALL — containerized build into the deterministic volume
# ===========================================================================

def _mock_install_io(monkeypatch, tmp_path, pip_calls):
    pkg_dir = tmp_path / "extracted" / "pk"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "setup.py").write_text("print('arbitrary build code')")
    monkeypatch.setenv("AGENTNODE_LOCKFILE", str(tmp_path / "agentnode.lock"))
    monkeypatch.setattr(installer, "download_artifact", lambda *a, **k: None)
    monkeypatch.setattr(installer, "verify_hash", lambda *a, **k: "abc123def456")
    monkeypatch.setattr(installer, "extract_archive", lambda *a, **k: pkg_dir)
    monkeypatch.setattr(installer, "resolve_python", lambda: "python")
    monkeypatch.setattr(installer, "pip_install", lambda *a, **k: pip_calls.append(1))
    # volume rm (best-effort cleanup before build) — no real docker
    monkeypatch.setattr(installer.subprocess, "run",
                        lambda *a, **k: mock.Mock(returncode=0, stdout=b"", stderr=b""))
    return pkg_dir


def test_install_builds_in_container_into_volume_not_host(monkeypatch, tmp_path):
    pip_calls: list[int] = []
    pkg_dir = _mock_install_io(monkeypatch, tmp_path, pip_calls)
    _, rec = _available_backend(monkeypatch)

    res = installer.install_package(
        slug="community-pack", version="2.0", artifact_url="https://x/p.tar.gz",
        artifact_hash="sha256:abc123def456", entrypoint="pk.tool:run",
        trust_level="verified",
    )
    assert res["installed"] is True
    assert pip_calls == []  # NEVER a host build for community code

    # The BUILD argv: docker … -v <src>:/src:ro -v <vol>:/install:rw … pip install --target /install /src
    argv = rec.last_argv
    vol = sandbox_volume_name("community-pack", "2.0", "sha256:abc123def456")
    assert argv[0] == "docker"
    assert f"{pkg_dir}:/src:ro" in argv
    assert f"{vol}:/install:rw" in argv
    # Build runs via `sh -c`: copy the read-only /src into writable /tmp, then
    # pip install --target /install from the copy (setuptools writes build/ in cwd,
    # which must be writable — /src stays read-only).
    assert argv[-3] == "sh" and argv[-2] == "-c"
    build_script = argv[-1]
    assert "cp -r /src /tmp/build-src" in build_script
    assert "pip install" in build_script
    assert "--target /install /tmp/build-src" in build_script
    assert "--network" not in argv  # build keeps network (pip fetches deps)


def test_install_records_sandbox_volume_in_lockfile(monkeypatch, tmp_path):
    pip_calls: list[int] = []
    _mock_install_io(monkeypatch, tmp_path, pip_calls)
    _available_backend(monkeypatch)

    installer.install_package(
        slug="community-pack", version="2.0", artifact_url="https://x/p.tar.gz",
        artifact_hash="sha256:abc123def456", entrypoint="pk.tool:run",
        trust_level="verified",
    )
    lock = json.loads((tmp_path / "agentnode.lock").read_text())
    entry = lock["packages"]["community-pack"]
    assert entry["sandboxed"] is True
    assert entry["sandbox_volume"] == sandbox_volume_name(
        "community-pack", "2.0", "sha256:abc123def456")


def test_install_failclosed_when_no_runtime(monkeypatch, tmp_path):
    pip_calls: list[int] = []
    _mock_install_io(monkeypatch, tmp_path, pip_calls)

    class _Un(SandboxBackend):
        def check_available(self):
            return SandboxAvailability(available=False, backend="none",
                                       reason="no container runtime found")
        def wrap_command(self, spec):  # pragma: no cover
            raise AssertionError("must not run without a sandbox")
    set_default_backend(_Un())

    with pytest.raises(RuntimeError, match="container runtime|host build"):
        installer.install_package(
            slug="evil", version="1.0", artifact_url="https://x/p.tar.gz",
            artifact_hash="sha256:abc123def456", entrypoint="pk.tool",
            trust_level="verified",
        )
    assert pip_calls == []


def test_install_trusted_still_builds_on_host(monkeypatch, tmp_path):
    pip_calls: list[int] = []
    _mock_install_io(monkeypatch, tmp_path, pip_calls)
    _, rec = _available_backend(monkeypatch)

    res = installer.install_package(
        slug="ok", version="1.0", artifact_url="https://x/p.tar.gz",
        artifact_hash="sha256:abc123def456", entrypoint="pk.tool", trust_level="trusted",
    )
    assert res["installed"] is True
    assert pip_calls == [1]      # host build for vetted tier
    assert rec.calls == []       # no container build
    lock = json.loads((tmp_path / "agentnode.lock").read_text())
    assert "sandbox_volume" not in lock["packages"]["ok"]


# --- 1C: policy-aware install gate + MUTABLE metadata ------------------------

def _install_ok(monkeypatch, tmp_path, trust_level, policy):
    pip_calls: list[int] = []
    _mock_install_io(monkeypatch, tmp_path, pip_calls)
    _available_backend(monkeypatch)
    monkeypatch.setattr("agentnode_sdk.config.host_trust_policy", lambda: policy)
    installer.install_package(
        slug="ok", version="1.0", artifact_url="https://x/p.tar.gz",
        artifact_hash="sha256:abc123def456", entrypoint="pk.tool", trust_level=trust_level,
    )
    e = json.loads((tmp_path / "agentnode.lock").read_text())["packages"]["ok"]
    return e, pip_calls


def test_install_default_trusted_records_host_build_mode(monkeypatch, tmp_path):
    e, pip_calls = _install_ok(monkeypatch, tmp_path, "trusted", "default")
    assert pip_calls == [1]                       # host build — today's behavior
    assert e["build_mode"] == "host"
    assert "sandbox_volume" not in e
    assert e["effective_host_trust_policy_at_install"] == "default"
    assert e["pinnable"] is True


def test_install_curated_only_trusted_builds_into_volume(monkeypatch, tmp_path):
    e, pip_calls = _install_ok(monkeypatch, tmp_path, "trusted", "curated_only")
    assert pip_calls == []                        # NO host build under the stricter policy
    assert e["build_mode"] == "sandbox_volume"
    assert e["sandboxed"] is True
    assert e["sandbox_volume"] == sandbox_volume_name("ok", "1.0", "sha256:abc123def456")
    assert e["effective_host_trust_policy_at_install"] == "curated_only"


def test_install_curated_only_curated_stays_on_host(monkeypatch, tmp_path):
    e, pip_calls = _install_ok(monkeypatch, tmp_path, "curated", "curated_only")
    assert pip_calls == [1]                       # curated still host under curated_only
    assert e["build_mode"] == "host"


def test_install_none_curated_builds_into_volume(monkeypatch, tmp_path):
    e, pip_calls = _install_ok(monkeypatch, tmp_path, "curated", "none")
    assert pip_calls == []                        # none sandboxes even curated
    assert e["build_mode"] == "sandbox_volume"


def test_install_trusted_agent_curated_only_builds_volume(monkeypatch, tmp_path):
    # Agents-A1 verification: agents use the SAME trust-gated install path (no separate
    # branch), so 1C's policy-aware gate builds the sealed volume for a trusted AGENT
    # under curated_only — which run_agent_sandboxed then finds (else fail-closed).
    pip_calls: list[int] = []
    _mock_install_io(monkeypatch, tmp_path, pip_calls)
    _available_backend(monkeypatch)
    monkeypatch.setattr("agentnode_sdk.config.host_trust_policy", lambda: "curated_only")
    installer.install_package(
        slug="ag", version="1.0", artifact_url="https://x/p.tar.gz",
        artifact_hash="sha256:abc123def456", entrypoint="ag.mod:run", trust_level="trusted",
        package_type="agent",
    )
    e = json.loads((tmp_path / "agentnode.lock").read_text())["packages"]["ag"]
    assert pip_calls == []                         # NOT host-built
    assert e["build_mode"] == "sandbox_volume" and e["sandboxed"] is True
    assert e["sandbox_volume"] == sandbox_volume_name("ag", "1.0", "sha256:abc123def456")


# ===========================================================================
# RUN — ephemeral container, volume read-only, clean env
# ===========================================================================

def _run_entry(network_level: str | None = "none", **over) -> dict:
    entry = {
        "version": "2.0",
        "package_type": "toolpack",
        "runtime": "python",
        "entrypoint": "pk.tool:run",
        "trust_level": "verified",
        "artifact_hash": "sha256:abc123def456",
        "sandboxed": True,
        "permissions": {},
    }
    entry["sandbox_volume"] = sandbox_volume_name(
        "community-pack", entry["version"], entry["artifact_hash"])
    if network_level is not None:
        entry["permissions"]["network_level"] = network_level
    entry.update(over)
    return entry


def _patch_inspect_ok(monkeypatch, returncode=0):
    from agentnode_sdk.runtimes import python_runner
    monkeypatch.setattr(python_runner.subprocess, "run",
                        lambda *a, **k: mock.Mock(returncode=returncode, stdout=b"", stderr=b""))


def test_run_mounts_volume_readonly_only_volume(monkeypatch):
    _, rec = _available_backend(monkeypatch)
    _patch_inspect_ok(monkeypatch)
    rec.response = (0, json.dumps({"ok": True, "result": {"answer": 42}}), "")

    res = run_python("community-pack", "do", entry=_run_entry(), x=1)
    assert res.success is True
    assert res.result == {"answer": 42}
    assert res.mode_used == "sandbox"

    argv = rec.last_argv
    vol = _run_entry()["sandbox_volume"]
    assert argv[0] == "docker"
    assert "-i" in argv                                  # stdin forwarded
    assert f"{vol}:/pack:ro" in argv                     # volume read-only
    assert "PYTHONPATH=/pack" in argv
    assert argv[-3:] == ["python", "-c", _wrapper()]     # SDK-free wrapper at the end
    # ONLY the volume is mounted — no host source, no host paths
    mounts = [argv[i + 1] for i, a in enumerate(argv) if a == "-v"]
    assert mounts == [f"{vol}:/pack:ro"]


def test_run_input_carries_module_functions_kwargs(monkeypatch):
    _, rec = _available_backend(monkeypatch)
    _patch_inspect_ok(monkeypatch)

    run_python("community-pack", None, entry=_run_entry(), a=1, b="x")
    payload = json.loads(rec.last_input)
    assert payload["module"] == "pk.tool"
    assert payload["functions"] == ["run"]
    assert payload["kwargs"] == {"a": 1, "b": "x"}


def test_run_env_is_clean_no_host_paths(monkeypatch):
    _, rec = _available_backend(monkeypatch)
    _patch_inspect_ok(monkeypatch)
    monkeypatch.setenv("HOME", "/home/realuser")
    monkeypatch.setenv("APPDATA", r"C:\Users\realuser\AppData")

    run_python("community-pack", "do", entry=_run_entry())
    joined = " ".join(rec.last_argv)
    assert "HOME=/sandbox-home" in rec.last_argv       # clean container HOME
    assert "/home/realuser" not in joined              # no host HOME
    assert "realuser" not in joined                    # no host APPDATA/USERPROFILE
    assert ".agentnode" not in joined                  # credential store never mounted


# --- network enforcement (allowlist; unknown = deny) -------------------------

@pytest.mark.parametrize("level", ["none", "unknown-garbage", None])
def test_run_network_denied_for_none_or_unknown(monkeypatch, level):
    _, rec = _available_backend(monkeypatch)
    _patch_inspect_ok(monkeypatch)
    run_python("community-pack", "do", entry=_run_entry(network_level=level))
    argv = rec.last_argv
    # --network none must be present (no silent grant)
    assert "--network" in argv
    assert argv[argv.index("--network") + 1] == "none"


@pytest.mark.parametrize("level", ["restricted", "full", "unrestricted", "external"])
def test_run_network_granted_for_recognized_levels(monkeypatch, level):
    _, rec = _available_backend(monkeypatch)
    _patch_inspect_ok(monkeypatch)
    run_python("community-pack", "do", entry=_run_entry(network_level=level))
    assert "--network" not in rec.last_argv  # network allowed (no isolation flag)


# --- volume gate -------------------------------------------------------------

def test_run_failclosed_when_volume_inspect_fails(monkeypatch):
    _, rec = _available_backend(monkeypatch)
    _patch_inspect_ok(monkeypatch, returncode=1)  # volume inspect fails
    res = run_python("community-pack", "do", entry=_run_entry())
    assert res.success is False
    assert "reinstall" in (res.error or "").lower() or "missing or stale" in (res.error or "").lower()
    assert rec.calls == []  # never ran the container


def test_run_failclosed_when_volume_name_mismatch(monkeypatch):
    _, rec = _available_backend(monkeypatch)
    _patch_inspect_ok(monkeypatch)
    entry = _run_entry()
    entry["sandbox_volume"] = "agentnode-pack-spoofed-volume"  # stale/forged
    res = run_python("community-pack", "do", entry=entry)
    assert res.success is False
    assert rec.calls == []  # gate fails before inspect/run


def test_run_failclosed_when_not_sandboxed(monkeypatch):
    _, rec = _available_backend(monkeypatch)
    _patch_inspect_ok(monkeypatch)
    entry = _run_entry()
    entry["sandboxed"] = False  # e.g. a pre-P0.3 install
    res = run_python("community-pack", "do", entry=entry)
    assert res.success is False
    assert rec.calls == []


def test_run_failclosed_when_no_runtime(monkeypatch):
    class _Un(SandboxBackend):
        def check_available(self):
            return SandboxAvailability(available=False, backend="none",
                                       reason="no container runtime found")
        def wrap_command(self, spec):  # pragma: no cover
            raise AssertionError("must not run without a sandbox")
    set_default_backend(_Un())
    _patch_inspect_ok(monkeypatch)
    res = run_python("community-pack", "do", entry=_run_entry())
    assert res.success is False
    assert res.mode_used == "sandbox_unavailable"


def test_run_failclosed_trusted_hostbuilt_under_curated_only(monkeypatch):
    # 1B routes trusted into the sandbox under curated_only; a package host-built
    # under an older/default policy has no volume → fail-closed (never a host run).
    _, rec = _available_backend(monkeypatch)
    _patch_inspect_ok(monkeypatch)
    monkeypatch.setattr("agentnode_sdk.config.host_trust_policy", lambda: "curated_only")
    res = run_python("community-pack", "do", entry=_run_entry(trust_level="trusted", sandboxed=False))
    assert res.success is False
    assert rec.calls == []       # container never ran; no host fallback


# --- host path for vetted tiers ----------------------------------------------

def test_curated_toolpack_stays_on_host(monkeypatch):
    _, rec = _available_backend(monkeypatch)
    entry = _run_entry(trust_level="curated", entrypoint="json")
    res = run_python("json-pack", None, mode="subprocess", timeout=10.0, entry=entry, s='{"a":1}')
    assert res.mode_used == "subprocess"   # host path, not the container
    assert rec.calls == []                 # container never invoked


# ===========================================================================
# entrypoint resolution — STRING-ONLY (no import), mirrors load_tool precedence
# ===========================================================================

def test_resolve_target_v02_per_tool_entrypoint():
    entry = {"tools": [{"name": "summarize", "entrypoint": "mod.tool:summarize"}],
             "entrypoint": "mod.tool"}
    assert _resolve_container_target(entry, "summarize") == ("mod.tool", ["summarize"])


def test_resolve_target_v01_package_default():
    entry = {"entrypoint": "mod.tool", "tools": []}
    assert _resolve_container_target(entry, None) == ("mod.tool", ["run"])


def test_resolve_target_single_tool_autoselect():
    # 0.11.2: single-tool pack, no tool_name → auto-select the sole tool (not run)
    entry = {"entrypoint": "wc.tool",
             "tools": [{"name": "count_words", "entrypoint": "wc.tool:count_words"}]}
    assert _resolve_container_target(entry, None) == ("wc.tool", ["count_words"])


def test_resolve_target_multi_tool_keeps_package_default():
    # 0.11.2: multi-tool, no tool_name → package-level entrypoint (unchanged)
    entry = {"entrypoint": "mod.tool:dispatch",
             "tools": [{"name": "a", "entrypoint": "mod.tool:a"},
                       {"name": "b", "entrypoint": "mod.tool:b"}]}
    assert _resolve_container_target(entry, None) == ("mod.tool", ["dispatch"])


def test_resolve_target_multi_tool_no_entrypoint_hints():
    # 0.11.3: multi-tool pack with NO package-level entrypoint → helpful hint
    # listing tools + slug:tool (message-only; container path mirrors the host).
    from agentnode_sdk.exceptions import AgentNodeToolError
    entry = {"tools": [{"name": "a", "entrypoint": "m:a"},
                       {"name": "b", "entrypoint": "m:b"}]}
    with pytest.raises(AgentNodeToolError, match="multiple tools"):
        _resolve_container_target(entry, None)


def test_resolve_target_v02_colon_function():
    entry = {"entrypoint": "mod.tool:describe", "tools": []}
    assert _resolve_container_target(entry, None) == ("mod.tool", ["describe"])


def test_resolve_target_v01_tool_name_candidates():
    # v0.1 pack (no tools list): try tool_name as a function, then the default.
    entry = {"entrypoint": "mod.tool", "tools": []}
    assert _resolve_container_target(entry, "fetch") == ("mod.tool", ["fetch", "run"])


# ===========================================================================
# uninstall removes the sandbox volume
# ===========================================================================

def test_uninstall_removes_sandbox_volume(monkeypatch, tmp_path):
    import subprocess as _sp
    from agentnode_sdk.cli.commands import cmd_remove

    from agentnode_sdk.lock_integrity import seal_entry, seal_structure
    lock = {
        "lockfile_version": "0.1", "updated_at": "", "packages": {
            "community-pack": seal_entry({
                "version": "2.0", "package_type": "toolpack",
                "sandboxed": True,
                "sandbox_volume": "agentnode-pack-community-pack-2.0-abc123de",
            }),
        },
    }
    lock = seal_structure(lock)
    lf = tmp_path / "agentnode.lock"
    lf.write_text(json.dumps(lock))
    monkeypatch.setenv("AGENTNODE_LOCKFILE", str(lf))
    _, _ = _available_backend(monkeypatch)

    runs: list[list[str]] = []
    monkeypatch.setattr(_sp, "run", lambda args, **k: runs.append(args) or mock.Mock(returncode=0))

    assert cmd_remove("community-pack", yes=True) == 0
    assert ["docker", "volume", "rm", "agentnode-pack-community-pack-2.0-abc123de"] in runs


# ---------------------------------------------------------------------------

def _wrapper() -> str:
    from agentnode_sdk.runtimes.python_runner import _CONTAINER_WRAPPER
    return _CONTAINER_WRAPPER
