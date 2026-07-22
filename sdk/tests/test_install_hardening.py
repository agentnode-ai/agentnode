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
    target, resolve_python() returns ONLY the CANONICAL realpath of ``sys.executable`` — never
    a $VIRTUAL_ENV / .venv / `python` / `python3` / PATH fallback (which could lock a different
    env-id). A symlinked interpreter (e.g. Linux ``.../bin/python`` -> ``.../bin/python3.11``)
    resolves to its canonical target; the value is absolute + canonical."""
    import sys
    from pathlib import Path

    expected = str(Path(sys.executable).resolve(strict=True))
    # A bogus $VIRTUAL_ENV and a cwd without a ./.venv must NOT change the result.
    monkeypatch.setenv("VIRTUAL_ENV", str(tmp_path / "not-a-venv"))
    monkeypatch.chdir(tmp_path)
    got = installer.resolve_python()
    assert got == expected                  # canonical realpath of sys.executable, no fallback
    assert Path(got).is_absolute()


def test_resolve_python_symlink_resolves_to_canonical_same_env_id(tmp_path):
    """POSIX: an explicit symlinked interpreter resolves to its canonical target, and the
    symlink and its target yield the SAME environment identity (so the env-lock key is stable
    regardless of which interpreter name was used)."""
    import os
    import sys
    from pathlib import Path

    if sys.platform == "win32":
        pytest.skip("POSIX symlink semantics (Windows symlinks need privileges)")
    real = str(Path(sys.executable).resolve(strict=True))
    link = tmp_path / "python-link"
    os.symlink(real, link)
    assert installer.resolve_python(str(link)) == real   # explicit symlink → canonical target

    from agentnode_sdk._env_lock import resolve_env_identity
    assert resolve_env_identity(str(link)).env_id == resolve_env_identity(real).env_id
