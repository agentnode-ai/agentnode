"""Tests for agentnode publish CLI command."""
from __future__ import annotations

import io
import json
import os
import tarfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from agentnode_sdk.cli.publish import (
    PUBLISH_EXCLUDE_DIRS,
    PUBLISH_EXCLUDE_FILES,
    PUBLISH_EXCLUDE_SUFFIXES,
    _build_artifact,
    _should_exclude,
    cmd_publish,
)

MINIMAL_MANIFEST = {
    "package_id": "test-pack",
    "version": "1.0.0",
    "name": "Test Pack",
    "package_type": "toolpack",
    "summary": "A test package for publish testing purposes.",
    "capabilities": {
        "tools": [{"name": "test_tool", "capability_id": "test.tool"}],
    },
    "verification": {
        "cases": [
            {"name": "basic-test", "input": {"query": "hello"}, "expected": {"status": "ok"}},
            {"name": "edge-case", "input": {"query": ""}, "expected": {"status": "ok"}},
        ],
    },
}


def _write_manifest(pkg_dir: Path, manifest: dict | None = None) -> None:
    import yaml
    m = manifest or MINIMAL_MANIFEST
    (pkg_dir / "agentnode.yaml").write_text(
        yaml.dump(m, default_flow_style=False), encoding="utf-8",
    )


def _tar_names(artifact_bytes: bytes) -> list[str]:
    with tarfile.open(fileobj=io.BytesIO(artifact_bytes), mode="r:gz") as tar:
        return tar.getnames()


class TestShouldExclude:
    def test_excludes_pycache_dir(self):
        info = tarfile.TarInfo(name="pkg/__pycache__/module.cpython-313.pyc")
        assert _should_exclude(info) is True

    def test_excludes_git_dir(self):
        info = tarfile.TarInfo(name="pkg/.git/config")
        assert _should_exclude(info) is True

    def test_excludes_venv(self):
        info = tarfile.TarInfo(name="pkg/.venv/lib/python3.13/site-packages/foo.py")
        assert _should_exclude(info) is True

    def test_excludes_dot_env_file(self):
        info = tarfile.TarInfo(name="pkg/.env")
        assert _should_exclude(info) is True

    def test_excludes_pyc_suffix(self):
        info = tarfile.TarInfo(name="pkg/module.pyc")
        assert _should_exclude(info) is True

    def test_does_not_exclude_env_example(self):
        info = tarfile.TarInfo(name="pkg/.env.example")
        assert _should_exclude(info) is False

    def test_does_not_exclude_my_env_module(self):
        info = tarfile.TarInfo(name="pkg/my_env.py")
        assert _should_exclude(info) is False

    def test_does_not_exclude_python_file(self):
        info = tarfile.TarInfo(name="pkg/tools.py")
        assert _should_exclude(info) is False

    def test_does_not_exclude_manifest(self):
        info = tarfile.TarInfo(name="pkg/agentnode.yaml")
        assert _should_exclude(info) is False

    def test_does_not_exclude_fixtures(self):
        info = tarfile.TarInfo(name="pkg/fixtures/cassettes/test.yaml")
        assert _should_exclude(info) is False

    def test_excludes_node_modules(self):
        info = tarfile.TarInfo(name="pkg/node_modules/lodash/index.js")
        assert _should_exclude(info) is True

    def test_excludes_ds_store(self):
        info = tarfile.TarInfo(name="pkg/.DS_Store")
        assert _should_exclude(info) is True


class TestBuildArtifact:
    def test_includes_manifest(self, tmp_path):
        _write_manifest(tmp_path)
        (tmp_path / "tools.py").write_text("def run(): pass")
        artifact, count = _build_artifact(tmp_path, "test-pack")
        names = _tar_names(artifact)
        assert "test-pack/agentnode.yaml" in names
        assert "test-pack/tools.py" in names
        assert count == 2

    def test_excludes_pycache(self, tmp_path):
        _write_manifest(tmp_path)
        cache_dir = tmp_path / "__pycache__"
        cache_dir.mkdir()
        (cache_dir / "mod.cpython-313.pyc").write_text("bytecode")
        artifact, count = _build_artifact(tmp_path, "test-pack")
        names = _tar_names(artifact)
        assert not any("__pycache__" in n for n in names)
        assert count == 1

    def test_excludes_dot_env(self, tmp_path):
        _write_manifest(tmp_path)
        (tmp_path / ".env").write_text("SECRET=xxx")
        artifact, count = _build_artifact(tmp_path, "test-pack")
        names = _tar_names(artifact)
        assert not any(n.endswith(".env") for n in names)

    def test_includes_env_example(self, tmp_path):
        _write_manifest(tmp_path)
        (tmp_path / ".env.example").write_text("SECRET=")
        artifact, count = _build_artifact(tmp_path, "test-pack")
        names = _tar_names(artifact)
        assert "test-pack/.env.example" in names

    def test_excludes_git(self, tmp_path):
        _write_manifest(tmp_path)
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "config").write_text("[core]")
        artifact, count = _build_artifact(tmp_path, "test-pack")
        names = _tar_names(artifact)
        assert not any(".git" in n for n in names)

    def test_includes_fixtures(self, tmp_path):
        _write_manifest(tmp_path)
        fix_dir = tmp_path / "fixtures" / "cassettes"
        fix_dir.mkdir(parents=True)
        (fix_dir / "test.yaml").write_text("interactions: []")
        artifact, count = _build_artifact(tmp_path, "test-pack")
        names = _tar_names(artifact)
        assert "test-pack/fixtures/cassettes/test.yaml" in names

    def test_skips_symlinks(self, tmp_path):
        _write_manifest(tmp_path)
        (tmp_path / "real.py").write_text("x = 1")
        link = tmp_path / "link.py"
        try:
            link.symlink_to(tmp_path / "real.py")
        except OSError:
            pytest.skip("symlinks not supported on this platform")
        artifact, count = _build_artifact(tmp_path, "test-pack")
        names = _tar_names(artifact)
        assert "test-pack/link.py" not in names
        assert "test-pack/real.py" in names

    def test_skips_path_traversal(self, tmp_path):
        _write_manifest(tmp_path)
        artifact, count = _build_artifact(tmp_path, "test-pack")
        names = _tar_names(artifact)
        assert not any(".." in n for n in names)


class TestCmdPublish:
    def test_no_manifest_exits_1(self, tmp_path, capsys):
        rc = cmd_publish(str(tmp_path))
        assert rc == 1
        assert "agentnode.yaml not found" in capsys.readouterr().err

    def test_not_a_directory_exits_1(self, tmp_path, capsys):
        f = tmp_path / "file.txt"
        f.write_text("x")
        rc = cmd_publish(str(f))
        assert rc == 1
        assert "not a directory" in capsys.readouterr().err

    def test_invalid_yaml_exits_1(self, tmp_path, capsys):
        (tmp_path / "agentnode.yaml").write_text("}{invalid")
        rc = cmd_publish(str(tmp_path))
        assert rc == 1

    def test_missing_package_id_exits_1(self, tmp_path, capsys):
        import yaml
        (tmp_path / "agentnode.yaml").write_text(
            yaml.dump({"version": "1.0.0", "name": "x"}),
        )
        rc = cmd_publish(str(tmp_path))
        assert rc == 1
        assert "package_id and version are required" in capsys.readouterr().err

    def test_validation_errors_exit_1(self, tmp_path, capsys):
        import yaml
        bad = {
            "package_id": "t",
            "version": "1.0.0",
            "name": "Test",
            "package_type": "toolpack",
            "summary": "short",
        }
        (tmp_path / "agentnode.yaml").write_text(yaml.dump(bad))
        rc = cmd_publish(str(tmp_path))
        assert rc == 1
        out = capsys.readouterr().out
        assert "FAIL" in out or "failed" in out

    def test_skip_validate_continues_past_errors(self, tmp_path, capsys):
        import yaml
        bad = {
            "package_id": "test-pack",
            "version": "1.0.0",
            "name": "Test",
            "package_type": "toolpack",
            "summary": "short",
        }
        (tmp_path / "agentnode.yaml").write_text(yaml.dump(bad))
        rc = cmd_publish(str(tmp_path), skip_validate=True)
        assert rc == 1
        err = capsys.readouterr().err
        assert "No AgentNode API key" in err

    def test_dry_run_does_not_post(self, tmp_path, capsys):
        _write_manifest(tmp_path)
        (tmp_path / "tools.py").write_text("def run(): pass")
        with patch("agentnode_sdk.cli.publish._post_publish") as mock_post:
            rc = cmd_publish(str(tmp_path), dry_run=True)
        assert rc == 0
        mock_post.assert_not_called()
        out = capsys.readouterr().out
        assert "Dry run" in out

    def test_no_api_key_exits_1(self, tmp_path, capsys):
        _write_manifest(tmp_path)
        (tmp_path / "tools.py").write_text("def run(): pass")
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("AGENTNODE_API_KEY", None)
            rc = cmd_publish(str(tmp_path))
        assert rc == 1
        err = capsys.readouterr().err
        assert "No AgentNode API key" in err

    def test_success_201(self, tmp_path, capsys):
        _write_manifest(tmp_path)
        (tmp_path / "tools.py").write_text("def run(): pass")
        mock_resp = {
            "slug": "test-pack",
            "version": "1.0.0",
            "package_type": "toolpack",
            "message": "Published test-pack@1.0.0",
        }
        with patch("agentnode_sdk.cli.publish._post_publish", return_value=mock_resp):
            rc = cmd_publish(str(tmp_path), token="test-key-123")
        assert rc == 0
        out = capsys.readouterr().out
        assert "Published test-pack@1.0.0" in out
        assert "agentnode.net/packages/test-pack" in out

    def test_version_conflict_409(self, tmp_path, capsys):
        _write_manifest(tmp_path)
        (tmp_path / "tools.py").write_text("def run(): pass")
        mock_resp = {
            "_error": True,
            "status": 409,
            "code": "VERSION_EXISTS",
            "message": "Version 1.0.0 already exists",
            "details": [],
        }
        with patch("agentnode_sdk.cli.publish._post_publish", return_value=mock_resp):
            rc = cmd_publish(str(tmp_path), token="test-key-123")
        assert rc == 1
        out = capsys.readouterr().out
        assert "already exists" in out

    def test_api_base_from_client_default(self):
        from agentnode_sdk.cli.publish import _resolve_api_base
        from agentnode_sdk.client import DEFAULT_BASE_URL
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("AGENTNODE_API_URL", None)
            assert _resolve_api_base() == DEFAULT_BASE_URL
