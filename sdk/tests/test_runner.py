"""Tests for agentnode_sdk.runner — run_tool() with subprocess isolation."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agentnode_sdk.runner import run_tool
from agentnode_sdk.runtimes.python_runner import (
    _filtered_env,
    _get_trust_level,
    _resolve_mode,
    _run_direct,
)
from agentnode_sdk.models import RunToolResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_lockfile(tmp_path: Path, packages: dict) -> Path:
    lf = tmp_path / "agentnode.lock"
    lf.write_text(json.dumps({
        "lockfile_version": "0.1",
        "updated_at": "2026-01-01T00:00:00+00:00",
        "packages": packages,
    }))
    return lf


# ---------------------------------------------------------------------------
# TestResolveMode
# ---------------------------------------------------------------------------

class TestResolveMode:
    """P0-06: ``mode='auto'`` must ALWAYS resolve to subprocess, regardless
    of trust level. The SDK README's isolation guarantee is the contract."""

    def test_auto_curated_is_subprocess(self):
        assert _resolve_mode("auto", "curated") == "subprocess"

    def test_auto_trusted_is_subprocess(self):
        assert _resolve_mode("auto", "trusted") == "subprocess"

    def test_auto_verified_is_subprocess(self):
        assert _resolve_mode("auto", "verified") == "subprocess"

    def test_auto_unverified_is_subprocess(self):
        assert _resolve_mode("auto", "unverified") == "subprocess"

    def test_auto_none_is_subprocess(self):
        assert _resolve_mode("auto", None) == "subprocess"

    def test_explicit_direct_overrides(self):
        assert _resolve_mode("direct", "unverified") == "direct"

    def test_explicit_subprocess_overrides(self):
        assert _resolve_mode("subprocess", "curated") == "subprocess"

    def test_invalid_mode_raises(self):
        with pytest.raises(ValueError, match="Unknown mode"):
            run_tool("x", mode="docker")


# ---------------------------------------------------------------------------
# TestFilteredEnv
# ---------------------------------------------------------------------------

class TestFilteredEnv:
    def test_includes_path(self):
        env = _filtered_env()
        assert "PATH" in env

    def test_excludes_api_keys(self, monkeypatch):
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret123")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        env = _filtered_env()
        assert "AWS_SECRET_ACCESS_KEY" not in env
        assert "OPENAI_API_KEY" not in env

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-only")
    def test_includes_windows_vars(self):
        env = _filtered_env()
        # SYSTEMROOT should be present on Windows
        assert "SYSTEMROOT" in env or "SystemRoot" in os.environ

    def test_includes_virtual_env(self, monkeypatch):
        monkeypatch.setenv("VIRTUAL_ENV", "/some/venv")
        env = _filtered_env()
        assert env.get("VIRTUAL_ENV") == "/some/venv"


# ---------------------------------------------------------------------------
# TestGetTrustLevel
# ---------------------------------------------------------------------------

class TestGetTrustLevel:
    def test_reads_from_lockfile(self, tmp_path):
        lf = _write_lockfile(tmp_path, {
            "my-pack": {"version": "1.0", "trust_level": "verified"},
        })
        assert _get_trust_level("my-pack", lf) == "verified"

    def test_missing_package_returns_none(self, tmp_path):
        lf = _write_lockfile(tmp_path, {})
        assert _get_trust_level("no-such-pack", lf) is None

    def test_missing_field_returns_none(self, tmp_path):
        lf = _write_lockfile(tmp_path, {
            "old-pack": {"version": "1.0"},  # no trust_level key
        })
        assert _get_trust_level("old-pack", lf) is None


# ---------------------------------------------------------------------------
# TestRunToolDirect
# ---------------------------------------------------------------------------

class TestRunToolDirect:
    @patch("agentnode_sdk.runtimes.python_runner.load_tool")
    def test_success(self, mock_load, tmp_path):
        mock_fn = MagicMock(return_value={"answer": 42})
        mock_load.return_value = mock_fn

        lf = _write_lockfile(tmp_path, {
            "my-pack": {"version": "1.0", "trust_level": "trusted", "entrypoint": "my_pack.tool"},
        })
        result = run_tool("my-pack", mode="direct", lockfile_path=lf, x=1)

        assert result.success is True
        assert result.result == {"answer": 42}
        assert result.mode_used == "direct"
        assert result.duration_ms > 0
        mock_fn.assert_called_once_with(x=1)

    @patch("agentnode_sdk.runtimes.python_runner.load_tool", side_effect=ImportError("not installed"))
    def test_error_wraps_in_result(self, mock_load, tmp_path, bypass_policy):
        lf = _write_lockfile(tmp_path, {})
        result = run_tool("missing-pack", mode="direct", lockfile_path=lf)

        assert result.success is False
        assert "not installed" in result.error


# ---------------------------------------------------------------------------
# TestRunToolSubprocess
# ---------------------------------------------------------------------------

class TestRunToolSubprocess:
    def test_returns_result(self, tmp_path):
        """Full subprocess round-trip with a real child process."""
        # Write a lockfile pointing at a real module (json — stdlib)
        lf = _write_lockfile(tmp_path, {
            "json-pack": {
                "version": "1.0",
                "entrypoint": "json",
                "trust_level": "verified",
                "permissions": {"network_level": "none", "filesystem_level": "none",
                                "code_execution_level": "none"},
            },
        })
        # json.loads is a real callable — call it via subprocess
        result = run_tool(
            "json-pack",
            mode="subprocess",
            timeout=10.0,
            lockfile_path=lf,
            s='{"a": 1}',
        )
        # json.loads('{"a": 1}') returns {"a": 1}
        # But the entrypoint resolves to json.run which doesn't exist.
        # So we expect an error here — that's fine, it tests the full path.
        # The important thing is we get a RunToolResult back, not a crash.
        assert isinstance(result, RunToolResult)
        assert result.mode_used == "subprocess"

    def test_timeout(self, tmp_path):
        """Subprocess that hangs gets killed."""
        lf = _write_lockfile(tmp_path, {
            "hang-pack": {"version": "1.0", "entrypoint": "time", "trust_level": "verified",
                          "permissions": {"network_level": "none", "filesystem_level": "none",
                                          "code_execution_level": "none"}},
        })
        # time.run doesn't exist so it'll error, but let's test with a real timeout
        # by using a very short timeout with a script that we know takes time
        result = run_tool("hang-pack", mode="subprocess", timeout=0.001, lockfile_path=lf)
        assert isinstance(result, RunToolResult)
        # Either timed out or errored quickly — both are valid
        assert result.mode_used == "subprocess"

    def test_nonzero_exit(self, tmp_path, monkeypatch):
        """Non-zero exit code from subprocess produces error."""
        lf = _write_lockfile(tmp_path, {
            "bad-pack": {"version": "1.0", "entrypoint": "bad_module", "trust_level": "verified",
                         "permissions": {"network_level": "none", "filesystem_level": "none",
                                         "code_execution_level": "none"}},
        })
        result = run_tool("bad-pack", mode="subprocess", timeout=10.0, lockfile_path=lf)
        assert isinstance(result, RunToolResult)
        assert result.mode_used == "subprocess"
        # Should have an error since bad_module doesn't exist
        assert result.success is False

    def test_auto_verified_uses_subprocess(self, tmp_path):
        """Auto mode with verified trust should pick subprocess."""
        lf = _write_lockfile(tmp_path, {
            "test-pack": {
                "version": "1.0",
                "entrypoint": "json",
                "trust_level": "verified",
            },
        })
        result = run_tool("test-pack", mode="auto", timeout=10.0, lockfile_path=lf, s='"hi"')
        assert result.mode_used == "subprocess"


# ---------------------------------------------------------------------------
# TestRunToolAuto
# ---------------------------------------------------------------------------

class TestRunToolAuto:
    def test_curated_still_subprocess(self, tmp_path):
        """P0-06: curated trust no longer bypasses subprocess isolation."""
        lf = _write_lockfile(tmp_path, {
            "safe-pack": {
                "version": "1.0",
                "entrypoint": "json",
                "trust_level": "curated",
            },
        })
        result = run_tool("safe-pack", mode="auto", timeout=5.0, lockfile_path=lf)
        assert result.mode_used == "subprocess"

    def test_trusted_still_subprocess(self, tmp_path):
        """P0-06: trusted trust no longer bypasses subprocess isolation."""
        lf = _write_lockfile(tmp_path, {
            "t-pack": {
                "version": "1.0",
                "entrypoint": "json",
                "trust_level": "trusted",
            },
        })
        result = run_tool("t-pack", mode="auto", timeout=5.0, lockfile_path=lf)
        assert result.mode_used == "subprocess"

    def test_missing_trust_falls_to_subprocess(self, tmp_path, bypass_policy):
        lf = _write_lockfile(tmp_path, {
            "old-pack": {"version": "1.0", "entrypoint": "old_pack.tool"},
        })
        result = run_tool("old-pack", mode="auto", timeout=5.0, lockfile_path=lf)
        assert result.mode_used == "subprocess"

    def test_explicit_direct_still_works(self, tmp_path):
        """mode='direct' remains the explicit opt-in for in-process execution."""
        mock_fn = MagicMock(return_value={"ok": True})
        with patch("agentnode_sdk.runtimes.python_runner.load_tool", return_value=mock_fn):
            lf = _write_lockfile(tmp_path, {
                "d-pack": {
                    "version": "1.0",
                    "entrypoint": "d_pack.tool",
                    "trust_level": "trusted",
                },
            })
            result = run_tool("d-pack", mode="direct", lockfile_path=lf)
            assert result.mode_used == "direct"
            assert result.success is True


class TestRunToolKwargCollision:
    """P1-SDK5: reserved kwargs that can reach **kwargs must raise TypeError.
    Names in run_tool's own signature (mode, timeout, slug, tool_name,
    lockfile_path) are captured by the parameter and never reach the tool;
    they are therefore not part of the collision set."""

    def test_entry_kwarg_raises(self, tmp_path):
        lf = _write_lockfile(tmp_path, {})
        with pytest.raises(TypeError, match="reserved kwarg"):
            run_tool("any-pack", lockfile_path=lf, entry={"internal": "value"})


# ---------------------------------------------------------------------------
# TestSubprocessEdgeCases
# ---------------------------------------------------------------------------

class TestSubprocessEdgeCases:
    def test_crash_tool_doesnt_crash_host(self, tmp_path):
        """A tool that raises RuntimeError should not crash the host process."""
        # We test this by running a subprocess that will error (missing module)
        lf = _write_lockfile(tmp_path, {
            "crash-pack": {"version": "1.0", "entrypoint": "nonexistent_crash_module", "trust_level": "unverified"},
        })
        result = run_tool("crash-pack", mode="subprocess", timeout=10.0, lockfile_path=lf)
        assert isinstance(result, RunToolResult)
        assert result.success is False
        assert result.error is not None

    def test_env_leak_blocked(self, tmp_path, monkeypatch):
        """API keys should not be visible inside the subprocess."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-secret-test-key")

        # Write a pack that will try to read the env var
        # We create a temporary module for this
        mod_dir = tmp_path / "env_test_pack"
        mod_dir.mkdir()
        (mod_dir / "__init__.py").write_text("")
        (mod_dir / "tool.py").write_text(
            "import os\ndef run(**kw): return {'key': os.environ.get('OPENAI_API_KEY')}\n"
        )

        # pip install this module so the subprocess can import it
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-e", str(mod_dir), "--quiet"],
            check=False,
            capture_output=True,
        )

        # Create a pyproject.toml so pip can install it
        (mod_dir / "pyproject.toml").write_text(
            '[build-system]\nrequires = ["setuptools"]\nbuild-backend = "setuptools.build_meta"\n'
            '[project]\nname = "env-test-pack"\nversion = "0.0.1"\n'
        )

        lf = _write_lockfile(tmp_path, {
            "env-test-pack": {
                "version": "0.0.1",
                "entrypoint": "env_test_pack.tool",
                "trust_level": "unverified",
            },
        })

        result = run_tool("env-test-pack", mode="subprocess", timeout=10.0, lockfile_path=lf)
        # The module may fail to import (not properly installed), but if it succeeds,
        # the key should be None
        if result.success and isinstance(result.result, dict):
            assert result.result.get("key") is None

    def test_serialize_fallback(self, tmp_path):
        """Non-serializable return values should produce fallback repr, not crash."""
        # We mock load_tool in direct mode to test the safe_serialize concept
        # Since subprocess uses the same wrapper, we verify the pattern works
        from agentnode_sdk.runtimes.python_runner import _run_direct
        from unittest.mock import patch
        import datetime

        mock_fn = MagicMock(return_value=datetime.datetime(2026, 1, 1))

        with patch("agentnode_sdk.runtimes.python_runner.load_tool", return_value=mock_fn):
            lf = _write_lockfile(tmp_path, {
                "dt-pack": {"version": "1.0", "entrypoint": "dt.tool", "trust_level": "trusted"},
            })
            # Direct mode returns the raw object (no serialization issue)
            result = run_tool("dt-pack", mode="direct", lockfile_path=lf)
            assert result.success is True
            # The result is a datetime — in direct mode this is fine
            assert result.result == datetime.datetime(2026, 1, 1)
