"""Tests for agentnode publish CLI command."""
from __future__ import annotations

import io
import json
import os
import sys
import tarfile
import types
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from agentnode_sdk.cli.publish import (
    PUBLISH_EXCLUDE_DIRS,
    PUBLISH_EXCLUDE_FILES,
    PUBLISH_EXCLUDE_SUFFIXES,
    _build_artifact,
    _confirm_publish,
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
            rc = cmd_publish(str(tmp_path), token="test-key-123", yes=True)
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
            rc = cmd_publish(str(tmp_path), token="test-key-123", yes=True)
        assert rc == 1
        out = capsys.readouterr().out
        assert "already exists" in out

    def test_api_base_from_client_default(self):
        from agentnode_sdk.cli.publish import _resolve_api_base
        from agentnode_sdk.client import DEFAULT_BASE_URL
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("AGENTNODE_API_URL", None)
            assert _resolve_api_base() == DEFAULT_BASE_URL


# ---------------------------------------------------------------------------
# manifest_to_entry
# ---------------------------------------------------------------------------

class TestManifestToEntry:
    def test_maps_basic_fields(self):
        from agentnode_sdk.cli.publish import manifest_to_entry
        entry = manifest_to_entry(MINIMAL_MANIFEST, "sha256:abc123")
        assert entry["version"] == "1.0.0"
        assert entry["package_type"] == "toolpack"
        assert entry["artifact_hash"] == "sha256:abc123"

    def test_maps_tools_from_capabilities(self):
        from agentnode_sdk.cli.publish import manifest_to_entry
        manifest = {
            **MINIMAL_MANIFEST,
            "capabilities": {
                "tools": [
                    {"name": "do_thing", "entrypoint": "mod:do_thing", "action_type": "read"},
                ],
            },
        }
        entry = manifest_to_entry(manifest, "sha256:abc123")
        assert len(entry["tools"]) == 1
        assert entry["tools"][0] == {"name": "do_thing", "entrypoint": "mod:do_thing"}

    def test_strips_extra_tool_fields(self):
        from agentnode_sdk.cli.publish import manifest_to_entry
        manifest = {
            **MINIMAL_MANIFEST,
            "capabilities": {
                "tools": [
                    {"name": "t", "entrypoint": "mod:t", "capability_id": "x.y", "extra_field": "ignored"},
                ],
            },
        }
        entry = manifest_to_entry(manifest, "sha256:abc123")
        assert entry["tools"][0] == {"name": "t", "entrypoint": "mod:t"}

    def test_defaults_for_missing_fields(self):
        from agentnode_sdk.cli.publish import manifest_to_entry
        minimal = {"package_id": "x", "version": "1.0.0"}
        entry = manifest_to_entry(minimal, "sha256:abc")
        assert entry["runtime"] == "python"
        assert entry["entrypoint"] == ""
        assert entry["tools"] == []
        assert entry["prompts"] == []

    def test_maps_mcp_fields(self):
        from agentnode_sdk.cli.publish import manifest_to_entry
        manifest = {
            **MINIMAL_MANIFEST,
            "runtime": "mcp",
            "mcp_command": ["python", "-m", "server"],
        }
        entry = manifest_to_entry(manifest, "sha256:abc")
        assert entry["runtime"] == "mcp"
        assert entry["mcp_command"] == ["python", "-m", "server"]

    def test_maps_remote_fields(self):
        from agentnode_sdk.cli.publish import manifest_to_entry
        manifest = {
            **MINIMAL_MANIFEST,
            "runtime": "remote",
            "remote_endpoint": "https://api.example.com/v1",
            "connector": {"provider": "slack", "auth_type": "oauth2"},
        }
        entry = manifest_to_entry(manifest, "sha256:abc")
        assert entry["remote_endpoint"] == "https://api.example.com/v1"
        assert entry["connector"]["provider"] == "slack"

    def test_maps_skill_prompts(self):
        from agentnode_sdk.cli.publish import manifest_to_entry
        manifest = {
            "package_id": "my-skill",
            "version": "1.0.0",
            "package_type": "skill",
            "capabilities": {
                "prompts": [{"name": "greet", "template": "greet.md"}],
            },
            "assets": [{"path": "greet.md", "type": "template"}],
        }
        entry = manifest_to_entry(manifest, "sha256:abc")
        assert entry["prompts"] == [{"name": "greet", "template": "greet.md"}]
        assert entry["assets"] == [{"path": "greet.md", "type": "template"}]


# ---------------------------------------------------------------------------
# _sign_for_publish
# ---------------------------------------------------------------------------

class TestSignForPublish:
    def test_returns_valid_signature_block(self, tmp_path, monkeypatch):
        from agentnode_sdk.cli.publish import _sign_for_publish
        monkeypatch.setenv("AGENTNODE_CONFIG", str(tmp_path))

        sig = _sign_for_publish("test-pack", MINIMAL_MANIFEST, b"artifact-data")
        assert sig["algorithm"] == "ed25519"
        assert sig["key_id"].startswith("ed25519:")
        assert sig["canonical_version"] == 1
        assert "signature" in sig
        assert "public_key" in sig
        assert "signed_at" in sig

    def test_signature_verifies(self, tmp_path, monkeypatch):
        from agentnode_sdk.cli.publish import _sign_for_publish, manifest_to_entry
        from agentnode_sdk.signature import verify_entry_signature, SignatureStatus
        import hashlib

        monkeypatch.setenv("AGENTNODE_CONFIG", str(tmp_path))

        artifact = b"some-artifact-bytes"
        sig = _sign_for_publish("test-pack", MINIMAL_MANIFEST, artifact)

        artifact_hash = f"sha256:{hashlib.sha256(artifact).hexdigest()}"
        entry = manifest_to_entry(MINIMAL_MANIFEST, artifact_hash)
        entry["_signatures"] = {"publisher": [sig]}

        result = verify_entry_signature("test-pack", entry)
        assert result.status == SignatureStatus.VALID

    def test_creates_signing_key_if_missing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENTNODE_CONFIG", str(tmp_path))
        key_path = tmp_path / "signing_key"
        assert not key_path.exists()

        from agentnode_sdk.cli.publish import _sign_for_publish
        _sign_for_publish("test-pack", MINIMAL_MANIFEST, b"data")
        assert key_path.exists()

    def test_reuses_existing_signing_key(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENTNODE_CONFIG", str(tmp_path))

        from agentnode_sdk.cli.publish import _sign_for_publish
        sig1 = _sign_for_publish("test-pack", MINIMAL_MANIFEST, b"data")
        sig2 = _sign_for_publish("test-pack", MINIMAL_MANIFEST, b"data")
        assert sig1["key_id"] == sig2["key_id"]
        assert sig1["public_key"] == sig2["public_key"]

    def test_private_key_not_in_signature(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENTNODE_CONFIG", str(tmp_path))

        from agentnode_sdk.cli.publish import _sign_for_publish
        from agentnode_sdk.signing_key import get_signing_key_path
        sig = _sign_for_publish("test-pack", MINIMAL_MANIFEST, b"data")

        sig_json = json.dumps(sig)
        key_pem = (tmp_path / "signing_key").read_text()
        assert "PRIVATE" not in sig_json
        for line in key_pem.strip().split("\n"):
            if line.startswith("-----"):
                continue
            assert line not in sig_json

    def test_wrong_slug_fails_verification(self, tmp_path, monkeypatch):
        from agentnode_sdk.cli.publish import _sign_for_publish, manifest_to_entry
        from agentnode_sdk.signature import verify_entry_signature, SignatureStatus
        import hashlib

        monkeypatch.setenv("AGENTNODE_CONFIG", str(tmp_path))

        artifact = b"artifact"
        sig = _sign_for_publish("real-slug", MINIMAL_MANIFEST, artifact)

        artifact_hash = f"sha256:{hashlib.sha256(artifact).hexdigest()}"
        entry = manifest_to_entry(MINIMAL_MANIFEST, artifact_hash)
        entry["_signatures"] = {"publisher": [sig]}

        result = verify_entry_signature("wrong-slug", entry)
        assert result.status == SignatureStatus.INVALID


# ---------------------------------------------------------------------------
# cmd_publish with signing
# ---------------------------------------------------------------------------

class TestCmdPublishSigning:
    def test_publish_request_contains_signatures(self, tmp_path, capsys, monkeypatch):
        monkeypatch.setenv("AGENTNODE_CONFIG", str(tmp_path / "config"))

        _write_manifest(tmp_path)
        (tmp_path / "tools.py").write_text("def run(): pass")

        captured_manifest = {}
        mock_resp = {
            "slug": "test-pack",
            "version": "1.0.0",
            "message": "Published",
        }

        def capture_post(manifest_json, artifact_bytes, api_key):
            captured_manifest.update(json.loads(manifest_json))
            return mock_resp

        with patch("agentnode_sdk.cli.publish._post_publish", side_effect=capture_post):
            rc = cmd_publish(str(tmp_path), token="test-key", yes=True)

        assert rc == 0
        assert "_signatures" in captured_manifest
        pub = captured_manifest["_signatures"]["publisher"]
        assert isinstance(pub, list)
        assert len(pub) == 1
        assert pub[0]["algorithm"] == "ed25519"
        assert pub[0]["key_id"].startswith("ed25519:")

    def test_publish_signature_verifies_against_canonical(
        self, tmp_path, capsys, monkeypatch,
    ):
        monkeypatch.setenv("AGENTNODE_CONFIG", str(tmp_path / "config"))

        manifest = {
            **MINIMAL_MANIFEST,
            "entrypoint": "test_pack.tool",
            "capabilities": {
                "tools": [{"name": "run", "entrypoint": "test_pack.tool:run", "capability_id": "test.run"}],
            },
        }
        _write_manifest(tmp_path, manifest)
        (tmp_path / "tools.py").write_text("def run(): pass")

        captured = {}

        def capture_post(manifest_json, artifact_bytes, api_key):
            captured["manifest"] = json.loads(manifest_json)
            captured["artifact"] = artifact_bytes
            return {"slug": "test-pack", "version": "1.0.0", "message": "ok"}

        with patch("agentnode_sdk.cli.publish._post_publish", side_effect=capture_post):
            rc = cmd_publish(str(tmp_path), token="test-key", yes=True)

        assert rc == 0

        import hashlib
        from agentnode_sdk.cli.publish import manifest_to_entry
        from agentnode_sdk.signature import verify_entry_signature, SignatureStatus

        artifact_hash = f"sha256:{hashlib.sha256(captured['artifact']).hexdigest()}"
        entry = manifest_to_entry(captured["manifest"], artifact_hash)
        entry["_signatures"] = captured["manifest"]["_signatures"]

        result = verify_entry_signature("test-pack", entry)
        assert result.status == SignatureStatus.VALID

    def test_signing_failure_warns_but_publishes(self, tmp_path, capsys, monkeypatch):
        _write_manifest(tmp_path)
        (tmp_path / "tools.py").write_text("def run(): pass")

        mock_resp = {
            "slug": "test-pack",
            "version": "1.0.0",
            "message": "Published",
        }

        with patch("agentnode_sdk.cli.publish._sign_for_publish", side_effect=RuntimeError("key error")):
            with patch("agentnode_sdk.cli.publish._post_publish", return_value=mock_resp):
                rc = cmd_publish(str(tmp_path), token="test-key", yes=True)

        assert rc == 0
        err = capsys.readouterr().err
        assert "Could not sign" in err

    def test_dry_run_does_not_sign(self, tmp_path, capsys, monkeypatch):
        monkeypatch.setenv("AGENTNODE_CONFIG", str(tmp_path / "config"))
        _write_manifest(tmp_path)
        (tmp_path / "tools.py").write_text("def run(): pass")

        with patch("agentnode_sdk.cli.publish._sign_for_publish") as mock_sign:
            rc = cmd_publish(str(tmp_path), dry_run=True)

        assert rc == 0
        mock_sign.assert_not_called()


# ---------------------------------------------------------------------------
# Regression guards — publisher_slug boundaries (Phase 16.6)
# ---------------------------------------------------------------------------

class TestPublisherSlugRegressionGuards:
    def test_build_sign_payload_does_not_include_publisher_slug(self):
        from agentnode_sdk.signature import build_sign_payload
        entry = {
            "version": "1.0.0",
            "package_type": "toolpack",
            "runtime": "python",
            "entrypoint": "my_pack.tool",
            "artifact_hash": "sha256:abc123",
            "tools": [],
            "permissions": {},
            "prompts": [],
            "resources": [],
            "connector": None,
            "agent": None,
            "publisher_slug": "acme-ai",
        }
        payload = build_sign_payload("test-pack", entry)
        assert b"publisher_slug" not in payload
        assert b"acme-ai" not in payload

    def test_manifest_to_entry_does_not_produce_publisher_slug(self):
        from agentnode_sdk.cli.publish import manifest_to_entry
        entry = manifest_to_entry(MINIMAL_MANIFEST, "sha256:abc123")
        assert "publisher_slug" not in entry


# ---------------------------------------------------------------------------
# Publish confirm gate (0.11.4)
# ---------------------------------------------------------------------------

class TestConfirmPublishHelper:
    """The decision logic in isolation — the security-critical part."""

    def test_yes_bypasses(self):
        assert _confirm_publish("p", "1.0", "reg", yes=True, interactive=False) is True

    def test_non_interactive_without_yes_refuses(self, capsys):
        assert _confirm_publish("p", "1.0", "reg", yes=False, interactive=False) is False
        assert "non-interactively" in capsys.readouterr().err

    def test_interactive_y_proceeds(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda *a, **k: "y")
        assert _confirm_publish("p", "1.0", "reg", yes=False, interactive=True) is True

    def test_interactive_yes_word_proceeds(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda *a, **k: "YES")
        assert _confirm_publish("p", "1.0", "reg", yes=False, interactive=True) is True

    def test_interactive_n_cancels(self, monkeypatch, capsys):
        monkeypatch.setattr("builtins.input", lambda *a, **k: "n")
        assert _confirm_publish("p", "1.0", "reg", yes=False, interactive=True) is False
        assert "cancelled" in capsys.readouterr().out

    def test_interactive_enter_default_cancels(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda *a, **k: "")
        assert _confirm_publish("p", "1.0", "reg", yes=False, interactive=True) is False


class TestCmdPublishConfirmGate:
    """End-to-end wiring: the gate must sit before _post_publish."""

    def test_non_interactive_without_yes_refuses(self, tmp_path, capsys, monkeypatch):
        _write_manifest(tmp_path)
        (tmp_path / "tools.py").write_text("def run(): pass")
        # force non-tty deterministically (independent of pytest -s)
        monkeypatch.setattr(sys, "stdin", types.SimpleNamespace(isatty=lambda: False))
        with patch("agentnode_sdk.cli.publish._post_publish") as mock_post:
            rc = cmd_publish(str(tmp_path), token="test-key")  # no --yes
        assert rc == 1
        mock_post.assert_not_called()
        assert "non-interactively" in capsys.readouterr().err

    def test_non_interactive_with_yes_publishes(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENTNODE_CONFIG", str(tmp_path / "config"))
        _write_manifest(tmp_path)
        (tmp_path / "tools.py").write_text("def run(): pass")
        monkeypatch.setattr(sys, "stdin", types.SimpleNamespace(isatty=lambda: False))
        resp = {"slug": "test-pack", "version": "1.0.0", "message": "Published"}
        with patch("agentnode_sdk.cli.publish._post_publish", return_value=resp) as mock_post:
            rc = cmd_publish(str(tmp_path), token="test-key", yes=True)
        assert rc == 0
        mock_post.assert_called_once()

    def test_interactive_decline_does_not_publish(self, tmp_path, monkeypatch):
        _write_manifest(tmp_path)
        (tmp_path / "tools.py").write_text("def run(): pass")
        monkeypatch.delenv("AGENTNODE_NON_INTERACTIVE", raising=False)
        monkeypatch.setattr(sys, "stdin", types.SimpleNamespace(isatty=lambda: True))
        monkeypatch.setattr("builtins.input", lambda *a, **k: "n")
        with patch("agentnode_sdk.cli.publish._post_publish") as mock_post:
            rc = cmd_publish(str(tmp_path), token="test-key")
        assert rc == 0
        mock_post.assert_not_called()

    def test_interactive_accept_publishes(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENTNODE_CONFIG", str(tmp_path / "config"))
        _write_manifest(tmp_path)
        (tmp_path / "tools.py").write_text("def run(): pass")
        monkeypatch.delenv("AGENTNODE_NON_INTERACTIVE", raising=False)
        monkeypatch.setattr(sys, "stdin", types.SimpleNamespace(isatty=lambda: True))
        monkeypatch.setattr("builtins.input", lambda *a, **k: "y")
        resp = {"slug": "test-pack", "version": "1.0.0", "message": "Published"}
        with patch("agentnode_sdk.cli.publish._post_publish", return_value=resp) as mock_post:
            rc = cmd_publish(str(tmp_path), token="test-key")
        assert rc == 0
        mock_post.assert_called_once()
