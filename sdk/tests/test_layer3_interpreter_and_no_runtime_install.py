"""A1-E-Lock Layer 3 — target-interpreter resolution + no runtime/agent-initiated
installation. (The central environment write-lock + M1 split are added on top of these.)
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

from agentnode_sdk.installer import (
    InterpreterResolutionError,
    _canonical_interpreter,
    resolve_python,
)


def _realpath(p):
    return str(Path(p).resolve())


# ---------------------------------------------------------------------------
# 1. Interpreter resolution contract
# ---------------------------------------------------------------------------

def test_no_arg_is_canonical_sys_executable():
    assert resolve_python() == _realpath(sys.executable)


def test_no_arg_ignores_venv_and_path(monkeypatch):
    # Blocker 2: NO $VIRTUAL_ENV / .venv / PATH fallback for the no-arg path.
    here = resolve_python()
    monkeypatch.setenv("VIRTUAL_ENV", "C:/does-not-exist-venv" if sys.platform == "win32"
                       else "/does-not-exist-venv")
    monkeypatch.chdir(Path(__file__).parent)   # a dir with no ./.venv
    assert resolve_python() == here


def test_explicit_absolute_path_used_verbatim():
    target = _realpath(sys.executable)
    assert resolve_python(target) == target


def test_explicit_priority_over_sys_executable(monkeypatch):
    # Blocker 1 (the exact bug): an explicit interpreter must win over sys.executable.
    real = _realpath(sys.executable)
    monkeypatch.setattr(sys, "executable", "C:/some/other/pythonA.exe"
                        if sys.platform == "win32" else "/some/other/pythonA")
    assert resolve_python(real) == real            # explicit wins → not the (fake) sys.executable


def test_explicit_relative_path(tmp_path, monkeypatch):
    # a relative path is resolved from the CWD
    real = Path(sys.executable).resolve()
    link_dir = tmp_path / "d"
    link_dir.mkdir()
    monkeypatch.chdir(tmp_path)
    rel = os.path.join("d", "..", real.name)       # d/../python(.exe) — resolves near cwd? no
    # Use a genuinely relative reference to the real interpreter's directory:
    rel = os.path.relpath(str(real), str(tmp_path))
    assert resolve_python(rel) == str(real)


def test_explicit_bare_name_resolves_to_abs(monkeypatch):
    name = "python" if shutil.which("python") else "python3"
    if not shutil.which(name):
        pytest.skip("no python on PATH")
    r = resolve_python(name)
    assert os.path.isabs(r)
    assert r == _realpath(shutil.which(name))


@pytest.mark.parametrize("bad", ["/no/such/interp", "definitely-not-a-real-cmd-xyz", ""])
def test_invalid_target_fail_closed(bad):
    if bad == "":
        # empty explicit falls through to the no-arg (sys.executable) path
        assert resolve_python(bad) == _realpath(sys.executable)
        return
    with pytest.raises(InterpreterResolutionError) as e:
        resolve_python(bad)
    assert e.value.code == "interpreter_not_resolvable"


def test_error_code_is_stable():
    assert InterpreterResolutionError.code == "interpreter_not_resolvable"


def test_canonical_interpreter_rejects_non_python(tmp_path):
    f = tmp_path / "notpython.txt"
    f.write_text("x")
    assert _canonical_interpreter(str(f)) is None


def test_two_interpreters_distinct_env_ids():
    from agentnode_sdk._env_lock import resolve_env_identity
    other = shutil.which("python") or shutil.which("python3")
    a = resolve_python()
    if not other or _realpath(other) == a:
        pytest.skip("no second distinct interpreter available")
    b = resolve_python(other)
    assert resolve_env_identity(a).env_id != resolve_env_identity(b).env_id


# ---------------------------------------------------------------------------
# 2. No runtime / agent-initiated installation (Decision 1)
# ---------------------------------------------------------------------------

def _install_tripwire(monkeypatch):
    """Trip if ANY install/pip path is reached during a run."""
    tripped = []
    import agentnode_sdk.client as _client

    def boom_install(self, *a, **k):
        tripped.append("client.install")
        raise AssertionError("client.install reached during a run")
    monkeypatch.setattr(_client.AgentNodeClient, "install", boom_install)
    return tripped


def test_ensure_installed_present_true(monkeypatch):
    from agentnode_sdk.runtimes.agent_runner import AgentContext
    monkeypatch.setattr("agentnode_sdk.installer.read_lockfile",
                        lambda *a, **k: {"packages": {"p": {}}})
    ctx = AgentContext(goal="g", allowed_packages=None, max_tool_calls=5,
                       max_iterations=5, stop_on_consecutive_errors=3, _agent_slug="a")
    assert ctx._ensure_installed("p") is True


def test_ensure_installed_missing_no_install(monkeypatch):
    from agentnode_sdk.runtimes.agent_runner import AgentContext
    trip = _install_tripwire(monkeypatch)
    monkeypatch.setattr("agentnode_sdk.installer.read_lockfile",
                        lambda *a, **k: {"packages": {}})
    ctx = AgentContext(goal="g", allowed_packages=None, max_tool_calls=5,
                       max_iterations=5, stop_on_consecutive_errors=3, _agent_slug="a")
    assert ctx._ensure_installed("missing") is False
    assert trip == []


def test_dispatch_tool_missing_dependency_code(monkeypatch):
    from agentnode_sdk.runtimes.agent_runner import AgentContext
    trip = _install_tripwire(monkeypatch)
    monkeypatch.setattr("agentnode_sdk.installer.read_lockfile",
                        lambda *a, **k: {"packages": {}})
    ctx = AgentContext(goal="g", allowed_packages=None, max_tool_calls=5,
                       max_iterations=5, stop_on_consecutive_errors=3, _agent_slug="a")
    from agentnode_sdk.exceptions import MISSING_DEPENDENCY
    r = ctx._dispatch_tool("missing", None)
    assert r.success is False
    assert r.error_code == MISSING_DEPENDENCY
    assert trip == []


def test_eager_install_deps_removed():
    import agentnode_sdk.runtimes.agent_runner as ar
    assert not hasattr(ar, "_eager_install_deps")


def test_planner_suggest_only_never_installs(monkeypatch):
    import agentnode_sdk.planner as planner
    trip = _install_tripwire(monkeypatch)
    resolved = []

    class _Res:
        results = [type("R", (), {"slug": "some-pack"})()]

    class _Client:
        def resolve(self, caps):
            resolved.append(caps)
            return _Res()
        def close(self):
            pass
    monkeypatch.setattr("agentnode_sdk.client.AgentNodeClient", lambda *a, **k: _Client())
    slug, installed, reason = planner._install_for_capability("summarize")
    assert installed is False and slug is None
    assert "agentnode install" in reason
    assert resolved == [["summarize"]]      # resolve is allowed
    assert trip == []                       # install is not


def test_runtime_install_tool_disabled(monkeypatch):
    from agentnode_sdk.runtime import AgentNodeRuntime
    trip = _install_tripwire(monkeypatch)
    monkeypatch.setattr("agentnode_sdk.runtime.read_lockfile",
                        lambda *a, **k: {"packages": {}})
    rt = object.__new__(AgentNodeRuntime)
    rt._minimum_trust_level = "verified"
    rt._client = None
    from agentnode_sdk.exceptions import RUNTIME_INSTALL_DISABLED
    res = AgentNodeRuntime._handle_install(rt, {"slug": "x"})
    assert res.success is False
    assert res.error.code == RUNTIME_INSTALL_DISABLED
    assert trip == []


def test_error_code_constants_are_single_source_and_stable():
    # The three L3 codes each have exactly ONE defining constant with a stable value.
    from agentnode_sdk.exceptions import MISSING_DEPENDENCY, RUNTIME_INSTALL_DISABLED
    assert MISSING_DEPENDENCY == "missing_dependency"
    assert RUNTIME_INSTALL_DISABLED == "runtime_install_disabled"
    assert InterpreterResolutionError.code == "interpreter_not_resolvable"


# ---------------------------------------------------------------------------
# 3. Interpreter resolution happens ONLY on host-mutating routes
#    (container / MCP / skill must never fail-close on an unusable sys.executable)
# ---------------------------------------------------------------------------

class _Reached(Exception):
    """Sentinel raised by the resolve_python tripwire to prove the host route
    reached it (and to short-circuit before the heavy build/transaction)."""


def _mock_install_io(monkeypatch, tmp_path):
    """Drive install_package up to the routing branch without real download/build."""
    from agentnode_sdk import installer
    pkg_dir = tmp_path / "extracted" / "pk"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "setup.py").write_text("print('build')")
    monkeypatch.setenv("AGENTNODE_LOCKFILE", str(tmp_path / "agentnode.lock"))
    monkeypatch.setattr(installer, "download_artifact", lambda *a, **k: None)
    monkeypatch.setattr(installer, "verify_hash", lambda *a, **k: "abc123def456")
    monkeypatch.setattr(installer, "extract_archive", lambda *a, **k: pkg_dir)
    # container + host build sinks are stubbed so a *not-reached* resolve_python
    # lets the install complete cleanly.
    monkeypatch.setattr(installer, "_container_build_into_volume",
                        lambda *a, **k: "agentnode-vol-x")
    monkeypatch.setattr(installer, "pip_install", lambda *a, **k: None)
    monkeypatch.setattr(installer, "_install_agent_host_transaction",
                        lambda *a, **k: None)
    return pkg_dir


def _tripwire_resolve_python(monkeypatch):
    from agentnode_sdk import installer
    hits = []

    def trip(*a, **k):
        hits.append(1)
        raise _Reached()
    monkeypatch.setattr(installer, "resolve_python", trip)
    return hits


def test_host_toolpack_reaches_interpreter_resolution(monkeypatch, tmp_path):
    from agentnode_sdk import installer
    _mock_install_io(monkeypatch, tmp_path)
    hits = _tripwire_resolve_python(monkeypatch)
    with pytest.raises(_Reached):
        installer.install_package(
            slug="hp", version="1.0", artifact_url="https://x/p.tar.gz",
            artifact_hash="sha256:abc123def456", entrypoint="pk.tool",
            trust_level="trusted",
        )
    assert hits == [1]


def test_host_agent_reaches_interpreter_resolution(monkeypatch, tmp_path):
    from agentnode_sdk import installer
    _mock_install_io(monkeypatch, tmp_path)
    hits = _tripwire_resolve_python(monkeypatch)
    with pytest.raises(_Reached):
        installer.install_package(
            slug="ha", version="1.0", artifact_url="https://x/p.tar.gz",
            artifact_hash="sha256:abc123def456", entrypoint="ha.agent:run",
            trust_level="trusted", package_type="agent",
        )
    assert hits == [1]


def test_container_toolpack_does_not_reach_interpreter_resolution(monkeypatch, tmp_path):
    from agentnode_sdk import installer
    _mock_install_io(monkeypatch, tmp_path)
    hits = _tripwire_resolve_python(monkeypatch)
    # verified tier under the default host-trust policy → container route (no host python)
    res = installer.install_package(
        slug="cp", version="1.0", artifact_url="https://x/p.tar.gz",
        artifact_hash="sha256:abc123def456", entrypoint="pk.tool",
        trust_level="verified",
    )
    assert res["installed"] is True
    assert hits == []


def test_skill_does_not_reach_interpreter_resolution(monkeypatch, tmp_path):
    from agentnode_sdk import installer
    _mock_install_io(monkeypatch, tmp_path)
    hits = _tripwire_resolve_python(monkeypatch)
    monkeypatch.setattr(installer, "_install_skill",
                        lambda *a, **k: {"installed": True, "slug": "sk"})
    res = installer.install_package(
        slug="sk", version="1.0", artifact_url="https://x/p.tar.gz",
        artifact_hash="sha256:abc123def456", entrypoint="",
        trust_level="verified", package_type="skill",
    )
    assert res["installed"] is True
    assert hits == []


def test_interpreter_error_surfaces_structurally_through_install_package(monkeypatch, tmp_path):
    """A host route with an unresolvable interpreter raises InterpreterResolutionError,
    which is an AgentNodeError carrying the stable code — so it propagates uncaught
    through install_package/client.install and the CLI renders it traceback-free."""
    from agentnode_sdk.exceptions import AgentNodeError
    from agentnode_sdk import installer
    _mock_install_io(monkeypatch, tmp_path)
    # real strict resolve_python + unusable sys.executable → host route must fail-close
    monkeypatch.setattr(sys, "executable",
                        "C:/no/such/pythonZ.exe" if sys.platform == "win32"
                        else "/no/such/pythonZ")
    with pytest.raises(InterpreterResolutionError) as e:
        installer.install_package(
            slug="hp", version="1.0", artifact_url="https://x/p.tar.gz",
            artifact_hash="sha256:abc123def456", entrypoint="pk.tool",
            trust_level="trusted",
        )
    assert isinstance(e.value, AgentNodeError)          # structural: CLI renders it
    assert e.value.code == "interpreter_not_resolvable"
    assert str(e.value).startswith("[interpreter_not_resolvable]")


def test_mcp_and_skill_installers_never_reference_resolve_python():
    import inspect

    from agentnode_sdk import installer
    for fn in (installer._install_mcp, installer._install_skill):
        assert "resolve_python" not in inspect.getsource(fn), fn.__name__


def test_unusable_sys_executable_does_not_break_container_or_skill(monkeypatch, tmp_path):
    """The real (strict) resolve_python is used; sys.executable is made unusable.
    Container + skill installs must still succeed — they never resolve the host interp."""
    from agentnode_sdk import installer
    _mock_install_io(monkeypatch, tmp_path)
    monkeypatch.setattr(sys, "executable",
                        "C:/no/such/pythonZ.exe" if sys.platform == "win32"
                        else "/no/such/pythonZ")
    # sanity: the no-arg host resolution really would fail-close now
    with pytest.raises(InterpreterResolutionError):
        installer.resolve_python()

    # container toolpack — must NOT raise interpreter_not_resolvable
    res = installer.install_package(
        slug="cp2", version="1.0", artifact_url="https://x/p.tar.gz",
        artifact_hash="sha256:abc123def456", entrypoint="pk.tool",
        trust_level="verified",
    )
    assert res["installed"] is True

    # skill — must NOT raise interpreter_not_resolvable
    monkeypatch.setattr(installer, "_install_skill",
                        lambda *a, **k: {"installed": True, "slug": "sk2"})
    res2 = installer.install_package(
        slug="sk2", version="1.0", artifact_url="https://x/p.tar.gz",
        artifact_hash="sha256:abc123def456", entrypoint="",
        trust_level="verified", package_type="skill",
    )
    assert res2["installed"] is True


def test_only_explicit_admin_install_callers():
    # AST-precise (excludes message strings): the ONLY actual `<name>.install(` calls
    # onto a client object may live in the explicit admin surface (cli/commands.py:
    # CLI install + doctor --fix). client.py defines install itself.
    import ast

    import agentnode_sdk
    root = Path(agentnode_sdk.__file__).parent
    offenders = []
    for p in root.rglob("*.py"):
        if p.name == "client.py":
            continue
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "install"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id in ("client", "_client", "fix_client")):
                rel = str(p.relative_to(root))
                if rel == os.path.join("cli", "commands.py"):    # explicit admin surface
                    continue
                offenders.append(f"{rel}:{node.lineno}")
    assert offenders == [], f"non-admin install caller(s): {offenders}"
