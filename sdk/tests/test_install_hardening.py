"""P0.0 install hardening (SDK self-extension):

- verify (hash + publisher signature) happens BEFORE pip runs build hooks
- non-trusted (community) toolpacks are not built on the host (fail-closed)
- a registry artifact must carry a hash

Leitsatz: community code runs isolated or not at all. P0.0 ensures no foreign
build code runs on the host before verification / before a sandbox exists.
"""
import pytest

from agentnode_sdk import installer


def _mock_io(monkeypatch, tmp_path, pip_calls):
    """Stub the install I/O; record whether pip (the build step) was invoked."""
    pkg_dir = tmp_path / "extracted" / "pk"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "setup.py").write_text("print('arbitrary build code')")  # a source build
    monkeypatch.setenv("AGENTNODE_LOCKFILE", str(tmp_path / "agentnode.lock"))
    monkeypatch.setattr(installer, "download_artifact", lambda *a, **k: None)
    monkeypatch.setattr(installer, "verify_hash", lambda *a, **k: "abc123")
    monkeypatch.setattr(installer, "extract_archive", lambda *a, **k: pkg_dir)
    monkeypatch.setattr(installer, "resolve_python", lambda *a, **k: "python")
    monkeypatch.setattr(installer, "pip_install", lambda *a, **k: pip_calls.append(1))
    return pkg_dir


@pytest.mark.parametrize("tier", ["verified", "unverified", None, "weird"])
def test_nontrusted_toolpack_failclosed_when_no_sandbox(monkeypatch, tmp_path, tier):
    """P0.3: a non-trusted toolpack is built INSIDE the container; with NO
    container runtime the build is fail-closed (never falls back to a host build)."""
    from agentnode_sdk.sandbox import set_default_backend
    from agentnode_sdk.sandbox.backend import SandboxBackend
    from agentnode_sdk.sandbox.types import SandboxAvailability

    class _Unavailable(SandboxBackend):
        def check_available(self):
            return SandboxAvailability(available=False, backend="none",
                                       reason="no container runtime found")
        def wrap_command(self, spec):  # pragma: no cover - never reached
            raise AssertionError("must not wrap/run without a sandbox")

    set_default_backend(_Unavailable())  # overrides the conftest available-backend
    try:
        pip_calls = []
        _mock_io(monkeypatch, tmp_path, pip_calls)
        with pytest.raises(RuntimeError, match="non-trusted|container runtime|host build"):
            installer.install_package(
                slug="evil", version="1.0", artifact_url="https://x/p.tar.gz",
                artifact_hash="sha256:abc123", entrypoint="pk.tool", trust_level=tier,
            )
        assert pip_calls == []  # the package's build code never ran on the host
    finally:
        set_default_backend(None)


@pytest.mark.parametrize("tier", ["trusted", "curated"])
def test_trusted_toolpack_builds(monkeypatch, tmp_path, tier):
    pip_calls = []
    _mock_io(monkeypatch, tmp_path, pip_calls)
    res = installer.install_package(
        slug="ok", version="1.0", artifact_url="https://x/p.tar.gz",
        artifact_hash="sha256:abc123", entrypoint="pk.tool", trust_level=tier,
    )
    assert res["installed"] is True
    assert pip_calls == [1]  # trusted/curated may build natively


def test_publisher_signature_verified_before_pip(monkeypatch, tmp_path):
    """A signature failure must block BEFORE pip executes build hooks."""
    pip_calls = []
    _mock_io(monkeypatch, tmp_path, pip_calls)

    def boom(*a, **k):
        raise RuntimeError("Publisher signature verification failed for 'ok'")
    monkeypatch.setattr(installer, "_verify_publisher_signature", boom)

    with pytest.raises(RuntimeError, match="signature"):
        installer.install_package(
            slug="ok", version="1.0", artifact_url="https://x/p.tar.gz",
            artifact_hash="sha256:abc123", entrypoint="pk.tool", trust_level="trusted",
        )
    assert pip_calls == []  # reorder proven: pip never ran because verify failed first


def test_missing_artifact_hash_blocked(monkeypatch, tmp_path):
    pip_calls = []
    _mock_io(monkeypatch, tmp_path, pip_calls)
    with pytest.raises(RuntimeError, match="hash|integrity"):
        installer.install_package(
            slug="ok", version="1.0", artifact_url="https://x/p.tar.gz",
            artifact_hash=None, entrypoint="pk.tool", trust_level="trusted",
        )
    assert pip_calls == []


def test_resolve_python_prefers_sys_executable(monkeypatch, tmp_path):
    """0.11.1 + A1-E-Lock L3: the host build must target the interpreter actually running
    AgentNode, so `agentnode install` + `agentnode run` use the SAME Python. With no explicit
    target, resolve_python() returns ONLY the absolute, lexically-normalised LAUNCH path of
    ``sys.executable`` — venv-preserving (the final symlink is NOT physically resolved) and
    with NO $VIRTUAL_ENV / .venv / `python` / `python3` / PATH fallback (which could lock a
    different env-id). Preserving the launch path is what lets an explicit venv target hit
    that venv instead of its shared base interpreter."""
    import os
    import sys
    from pathlib import Path

    expected = os.path.abspath(os.path.normpath(sys.executable))   # normalised, NO realpath
    # A bogus $VIRTUAL_ENV and a cwd without a ./.venv must NOT change the result.
    monkeypatch.setenv("VIRTUAL_ENV", str(tmp_path / "not-a-venv"))
    monkeypatch.chdir(tmp_path)
    got = installer.resolve_python()
    assert got == expected                  # normalised launch path of sys.executable
    assert Path(got).is_absolute()


def test_resolve_python_no_fallback_with_manipulated_env(monkeypatch, tmp_path):
    """No-explicit-target resolution ignores PATH / VIRTUAL_ENV / CWD / ./.venv entirely — the
    start point is ONLY sys.executable (normalised), so a hostile environment cannot redirect
    the target interpreter."""
    import os
    import sys

    expected = os.path.abspath(os.path.normpath(sys.executable))
    fake = tmp_path / ".venv" / ("Scripts" if os.name == "nt" else "bin")
    fake.mkdir(parents=True)                            # a plausible ./.venv that MUST be ignored
    monkeypatch.setenv("VIRTUAL_ENV", str(tmp_path / ".venv"))
    monkeypatch.setenv("PATH", str(fake))               # only the decoy on PATH
    monkeypatch.chdir(tmp_path)
    assert installer.resolve_python() == expected


def test_resolve_python_bare_command_uses_which(monkeypatch):
    """A bare program NAME (no path component) is resolved via shutil.which() only, then
    normalised WITHOUT realpath."""
    import os
    import shutil
    import sys

    calls = []
    real_which = shutil.which

    def _which(name, *a, **k):
        calls.append(name)
        return sys.executable if name == "python-custom" else real_which(name, *a, **k)

    monkeypatch.setattr(shutil, "which", _which)
    got = installer.resolve_python("python-custom")
    assert got == os.path.abspath(os.path.normpath(sys.executable))
    assert calls == ["python-custom"]                   # a bare name → which()


def test_resolve_python_relative_path_bound_to_cwd(tmp_path, monkeypatch):
    """A RELATIVE explicit path is bound against the CWD AT CALL TIME (then normalised, symlink
    preserved). A later CWD change does not retarget the already-returned path."""
    import os

    from tests.agent_m1_helpers import make_target_venv, pip_python

    base = pip_python()
    venv_py = make_target_venv(base, tmp_path / "venvR")
    rel = os.path.relpath(venv_py, tmp_path)            # e.g. venvR/bin/python (has a separator)
    monkeypatch.chdir(tmp_path)
    got = installer.resolve_python(rel)                 # bound against tmp_path now
    assert got == os.path.abspath(os.path.normpath(venv_py))
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.chdir(other)                            # later CWD change
    assert got == os.path.abspath(os.path.normpath(venv_py))   # returned value is stable


def test_resolve_python_missing_target_fail_closed(tmp_path):
    """A non-existent explicit target fails closed (InterpreterResolutionError) BEFORE any lock
    or mutation — never a silent fallback to another interpreter."""
    with pytest.raises(installer.InterpreterResolutionError):
        installer.resolve_python(str(tmp_path / "does-not-exist" / "python"))


def test_resolve_python_preserves_venv_launcher_symlink(tmp_path):
    """POSIX: an explicit venv launcher (venv/bin/python -> base) is returned AS the venv launch
    path — NOT physically resolved to the base interpreter — and its environment identity stays
    the venv's (purelib inside the venv). Replaces the old 'symlink resolves to canonical' test,
    whose premise (a launcher symlink is semantically its target) is wrong for a venv."""
    import os
    import sys

    if sys.platform == "win32":
        pytest.skip("POSIX venv launcher is a symlink; Windows copies the interpreter")
    from agentnode_sdk._env_lock import resolve_env_identity
    from tests.agent_m1_helpers import make_target_venv, pip_python

    base = pip_python()
    venv_py = make_target_venv(base, tmp_path / "venvL")
    if not os.path.islink(venv_py):
        pytest.skip("this venv used copies, not a symlink; nothing to preserve")
    launch = installer.resolve_python(venv_py)
    assert launch == os.path.abspath(os.path.normpath(venv_py))   # launcher preserved
    assert launch != os.path.realpath(venv_py)                    # NOT collapsed to base
    ident = resolve_env_identity(launch)
    assert os.path.normcase(str(tmp_path / "venvL")) in ident.purelib   # venv identity kept


def test_no_arg_preserves_symlinked_sys_executable_launch_path(tmp_path, monkeypatch):
    """POSIX: with NO explicit target, resolve_python()'s only start point is sys.executable —
    and a SYMLINKED venv launcher there is preserved (absolute + lexically normalised), never
    physically resolved to the base interpreter. sys.executable is SIMULATED via monkeypatch
    here; the test does NOT spawn a full AgentNode process inside the venv (a bare venv has none
    of the SDK's runtime deps). This isolates the no-argument launch-path contract itself."""
    import os
    import sys

    if sys.platform == "win32":
        pytest.skip("POSIX venv launcher symlink semantics")
    from tests.agent_m1_helpers import make_target_venv, pip_python

    venv_python = make_target_venv(pip_python(), tmp_path / "venvS")
    if not os.path.islink(venv_python):
        pytest.skip("this venv implementation does not use a symlinked launcher")

    expected = os.path.abspath(os.path.normpath(venv_python))
    base_realpath = os.path.realpath(venv_python)
    monkeypatch.setattr(sys, "executable", venv_python)      # SIMULATE running under the venv

    resolved = installer.resolve_python()
    assert resolved == expected                              # symlinked venv launcher preserved
    assert resolved != base_realpath                         # NOT physically resolved to base
