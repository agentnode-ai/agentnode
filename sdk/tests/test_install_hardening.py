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
    monkeypatch.setattr(installer, "resolve_python", lambda: "python")
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
