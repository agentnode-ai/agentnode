"""Tests for AgentNodeClient.detect_and_install()."""
from unittest.mock import MagicMock, patch, call

import pytest

from agentnode_sdk import AgentNodeClient
from agentnode_sdk.models import (
    DetectAndInstallResult,
    InstallResult,
)


def _make_client() -> AgentNodeClient:
    """Create a client without real HTTP."""
    client = AgentNodeClient.__new__(AgentNodeClient)
    client._client = MagicMock()
    return client


def _make_install_result(installed: bool = True, slug: str = "pdf-reader") -> InstallResult:
    return InstallResult(
        slug=slug,
        version="1.0.0",
        installed=installed,
        already_installed=False,
        message="ok" if installed else "not installed",
    )


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


class TestDetection:
    def test_high_confidence_installs(self):
        client = _make_client()
        error = ImportError("No module named 'pdfplumber'")
        with patch.object(client, "resolve_and_install", return_value=_make_install_result()):
            result = client.detect_and_install(error)

        assert result.detected is True
        assert result.capability == "pdf_extraction"
        assert result.confidence == "high"
        assert result.installed is True

    def test_no_detection(self):
        client = _make_client()
        error = RuntimeError("Something unrelated")
        result = client.detect_and_install(error)

        assert result.detected is False
        assert result.error == "No capability gap detected"

    def test_low_confidence_blocked_by_default(self):
        client = _make_client()
        error = RuntimeError("failed")
        with patch.object(client, "resolve_and_install") as mock_rai:
            result = client.detect_and_install(error, context={"file": "report.pdf"})

        assert result.detected is True
        assert result.confidence == "low"
        assert result.installed is False
        assert result.error == "Low-confidence detection blocked"
        mock_rai.assert_not_called()


# ---------------------------------------------------------------------------
# Low confidence
# ---------------------------------------------------------------------------


class TestLowConfidence:
    def test_allow_low_confidence_installs(self):
        client = _make_client()
        error = RuntimeError("failed")
        with patch.object(client, "resolve_and_install", return_value=_make_install_result()):
            result = client.detect_and_install(
                error, context={"file": "report.pdf"}, allow_low_confidence=True
            )

        assert result.detected is True
        assert result.installed is True


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------


class TestCallbacks:
    def test_on_detect_fires(self):
        client = _make_client()
        error = ImportError("No module named 'pdfplumber'")
        on_detect = MagicMock()
        with patch.object(client, "resolve_and_install", return_value=_make_install_result()):
            client.detect_and_install(error, on_detect=on_detect)

        on_detect.assert_called_once()
        args = on_detect.call_args[0]
        assert len(args) == 3  # capability, confidence, error_msg
        assert args[0] == "pdf_extraction"
        assert args[1] == "high"

    def test_on_install_fires(self):
        client = _make_client()
        error = ImportError("No module named 'pdfplumber'")
        on_install = MagicMock()
        with patch.object(client, "resolve_and_install", return_value=_make_install_result()):
            client.detect_and_install(error, on_install=on_install)

        on_install.assert_called_once_with("pdf-reader")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class TestErrors:
    def test_resolve_fails_gracefully(self):
        client = _make_client()
        error = ImportError("No module named 'pdfplumber'")
        with patch.object(
            client,
            "resolve_and_install",
            return_value=_make_install_result(installed=False, slug=""),
        ):
            result = client.detect_and_install(error)

        assert result.detected is True
        assert result.installed is False

    def test_install_returns_not_installed(self):
        client = _make_client()
        error = ImportError("No module named 'pdfplumber'")
        ir = _make_install_result(installed=False)
        with patch.object(client, "resolve_and_install", return_value=ir):
            result = client.detect_and_install(error)

        assert result.detected is True
        assert result.installed is False
        assert result.install_result is ir


# ---------------------------------------------------------------------------
# Result object
# ---------------------------------------------------------------------------


class TestResultObject:
    def test_fields_populated(self):
        client = _make_client()
        error = ImportError("No module named 'pdfplumber'")
        ir = _make_install_result()
        with patch.object(client, "resolve_and_install", return_value=ir):
            result = client.detect_and_install(error, auto_upgrade_policy="safe")

        assert result.detected is True
        assert result.capability == "pdf_extraction"
        assert result.confidence == "high"
        assert result.installed is True
        assert result.install_result is ir
        assert result.auto_upgrade_policy == "safe"

    def test_policy_field_present(self):
        client = _make_client()
        error = RuntimeError("unrelated")
        result = client.detect_and_install(error, auto_upgrade_policy="strict")
        assert result.auto_upgrade_policy == "strict"
