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
    _normalize_interpreter_launch_path,
    resolve_python,
)


def _norm(p):
    """The launch-path normalisation the interpreter contract now guarantees: absolute +
    lexically normalised, WITHOUT physically resolving (no realpath / Path.resolve)."""
    return os.path.abspath(os.path.normpath(p))


# ---------------------------------------------------------------------------
# 1. Interpreter resolution contract — a venv-preserving LAUNCH path
#    (absolute + lexically normalised; the final symlink is NOT physically resolved)
# ---------------------------------------------------------------------------

def test_no_arg_preserves_normalized_sys_executable_launch_path():
    got = resolve_python()
    assert got == _norm(sys.executable)          # normalised launch path, NOT a realpath
    assert os.path.isabs(got)


def test_no_arg_ignores_venv_path_and_dotvenv(monkeypatch, tmp_path):
    # NO $VIRTUAL_ENV / ./.venv / PATH fallback for the no-arg path: the start point is ONLY
    # sys.executable (normalised), whatever the environment claims.
    expected = _norm(sys.executable)
    (tmp_path / ".venv" / ("Scripts" if os.name == "nt" else "bin")).mkdir(parents=True)
    monkeypatch.setenv("VIRTUAL_ENV", str(tmp_path / ".venv"))
    monkeypatch.setenv("PATH", str(tmp_path / "decoy"))    # only a decoy dir on PATH
    monkeypatch.chdir(tmp_path)                            # a CWD that owns a ./.venv
    assert resolve_python() == expected


def test_explicit_absolute_path_used_verbatim():
    # An explicit absolute launch path is returned normalised (and symlink-preserving) — never
    # re-pointed to another interpreter.
    target = _norm(sys.executable)
    assert resolve_python(target) == target


def test_explicit_priority_over_sys_executable(monkeypatch):
    # An explicit interpreter must win over sys.executable.
    target = _norm(sys.executable)
    monkeypatch.setattr(sys, "executable", "C:/some/other/pythonA.exe"
                        if sys.platform == "win32" else "/some/other/pythonA")
    assert resolve_python(target) == target        # explicit wins → not the (fake) sys.executable


def test_explicit_relative_path_bound_to_cwd(tmp_path, monkeypatch):
    # A relative explicit path is bound against the CWD AT CALL TIME, then lexically normalised
    # (no realpath) — it resolves to the same absolute launch path the interpreter lives at.
    launch = _norm(sys.executable)
    monkeypatch.chdir(tmp_path)
    rel = os.path.relpath(launch, str(tmp_path))   # a genuine relative path (has a separator)
    assert resolve_python(rel) == launch


def test_bare_command_uses_which_without_realpath(monkeypatch):
    # A bare NAME (no path component) is resolved via shutil.which() only, then normalised
    # WITHOUT realpath. An explicit PATH (with a separator) must NOT go through which().
    calls = []
    real_which = shutil.which

    def _which(name, *a, **k):
        calls.append(name)
        return sys.executable if name == "python-custom" else real_which(name, *a, **k)

    monkeypatch.setattr(shutil, "which", _which)
    got = resolve_python("python-custom")
    assert got == _norm(sys.executable)            # which() result, normalised, no realpath
    assert calls == ["python-custom"]              # a bare name → which()

    calls.clear()
    target = _norm(sys.executable)
    assert resolve_python(target) == target
    assert calls == []                             # a path (has a separator) never calls which()


@pytest.mark.parametrize("bad", ["/no/such/interp", "definitely-not-a-real-cmd-xyz", ""])
def test_invalid_target_fail_closed(bad):
    if bad == "":
        # A falsy explicit target falls through to the no-arg (sys.executable) launch path.
        assert resolve_python(bad) == _norm(sys.executable)
        return
    with pytest.raises(InterpreterResolutionError) as e:
        resolve_python(bad)
    assert e.value.code == "interpreter_not_resolvable"


def test_error_code_is_stable():
    assert InterpreterResolutionError.code == "interpreter_not_resolvable"


def test_interpreter_launch_path_rejects_non_python(tmp_path):
    # The launch-path normaliser fail-closes (None) on everything that is not an executable
    # Python 3 launcher — never a silent fallback to some other interpreter.
    assert _normalize_interpreter_launch_path(None) is None       # missing path
    assert _normalize_interpreter_launch_path("") is None
    d = tmp_path / "adir"
    d.mkdir()
    assert _normalize_interpreter_launch_path(str(d)) is None      # a directory, not a file
    f = tmp_path / "notpython.txt"
    f.write_text("x")
    assert _normalize_interpreter_launch_path(str(f)) is None      # existing non-Python file
    assert _normalize_interpreter_launch_path(                     # nonexistent → no fallback
        str(tmp_path / "missing" / "python")) is None
    accepted = _normalize_interpreter_launch_path(sys.executable)  # a real launcher is accepted
    assert accepted == _norm(sys.executable)


def test_explicit_symlink_path_normalized_not_resolved(tmp_path):
    # New-contract focused proof: an explicit EXISTING symlink launcher is lexically normalised
    # but NOT physically resolved to its target. POSIX only (Windows symlinks need privileges);
    # the full venv-launcher behaviour is covered in test_install_hardening /
    # test_layer3_installer_concurrency.
    if sys.platform == "win32":
        pytest.skip("POSIX symlink semantics (Windows symlinks need privileges)")
    target = _norm(sys.executable)
    link = tmp_path / "py-link"
    os.symlink(target, link)
    got = resolve_python(str(link))
    assert got == _norm(str(link))                 # the symlink launch path, lexically normalised
    assert got != target                           # NOT physically resolved to the target


def test_two_interpreters_distinct_env_ids():
    from agentnode_sdk._env_lock import resolve_env_identity
    other = shutil.which("python") or shutil.which("python3")
    a = resolve_python()
    if not other or resolve_python(other) == a:
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


@pytest.mark.usefixtures("legacy_default_policy")
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


@pytest.mark.usefixtures("legacy_default_policy")
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


@pytest.mark.usefixtures("legacy_default_policy")
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
