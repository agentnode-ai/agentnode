"""Tests for AgentNodeClient.smart_run()."""
from unittest.mock import MagicMock, patch, call

import pytest

from agentnode_sdk import AgentNodeClient
from agentnode_sdk.models import (
    DetectAndInstallResult,
    InstallResult,
    SmartRunResult,
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


def _make_detect_result(
    detected: bool = True,
    installed: bool = True,
    capability: str = "pdf_extraction",
    confidence: str = "high",
    slug: str = "pdf-reader",
    error: str | None = None,
) -> DetectAndInstallResult:
    ir = _make_install_result(installed=installed, slug=slug) if installed else None
    return DetectAndInstallResult(
        detected=detected,
        capability=capability if detected else None,
        confidence=confidence if detected else None,
        installed=installed,
        install_result=ir,
        error=error,
    )


# ---------------------------------------------------------------------------
# First attempt succeeds
# ---------------------------------------------------------------------------


class TestFirstAttemptSucceeds:
    def test_returns_result(self):
        client = _make_client()
        result = client.smart_run(lambda: "hello")
        assert result.success is True
        assert result.result == "hello"
        assert result.upgraded is False

    def test_timing_populated(self):
        client = _make_client()
        result = client.smart_run(lambda: 42)
        assert result.duration_ms >= 0


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


class TestDetection:
    def test_detect_only(self):
        """Error detected but install blocked → success=False, detected_capability set."""
        client = _make_client()
        error = ImportError("No module named 'pdfplumber'")
        call_count = 0

        def fn():
            nonlocal call_count
            call_count += 1
            raise error

        dr = _make_detect_result(detected=True, installed=False, error="Low-confidence detection blocked")
        with patch.object(client, "detect_and_install", return_value=dr):
            result = client.smart_run(fn)

        assert result.success is False
        assert result.detected_capability == "pdf_extraction"
        assert call_count == 1  # Only 1 attempt, no retry

    def test_undetectable_error(self):
        client = _make_client()

        def fn():
            raise RuntimeError("something weird")

        dr = _make_detect_result(detected=False)
        with patch.object(client, "detect_and_install", return_value=dr):
            result = client.smart_run(fn)

        assert result.success is False
        assert result.detected_capability is None

    def test_context_helps_detection(self):
        client = _make_client()
        call_count = 0

        def fn():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("failed")
            return "ok"

        dr = _make_detect_result(detected=True, installed=True)
        with patch.object(client, "detect_and_install", return_value=dr) as mock_dai:
            result = client.smart_run(fn, context={"file": "report.pdf"})

        assert mock_dai.call_args[1]["context"] == {"file": "report.pdf"}


# ---------------------------------------------------------------------------
# Confidence gating
# ---------------------------------------------------------------------------


class TestConfidenceGating:
    def test_high_confidence_installs_and_retries(self):
        client = _make_client()
        call_count = 0

        def fn():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ImportError("No module named 'pdfplumber'")
            return "extracted"

        dr = _make_detect_result(detected=True, installed=True, confidence="high")
        with patch.object(client, "detect_and_install", return_value=dr):
            result = client.smart_run(fn)

        assert result.success is True
        assert result.upgraded is True
        assert call_count == 2

    def test_low_confidence_blocked(self):
        client = _make_client()

        def fn():
            raise RuntimeError("failed")

        dr = _make_detect_result(
            detected=True, installed=False, confidence="low",
            error="Low-confidence detection blocked",
        )
        with patch.object(client, "detect_and_install", return_value=dr):
            result = client.smart_run(fn)

        assert result.success is False
        assert result.detection_confidence == "low"


# ---------------------------------------------------------------------------
# Install and retry
# ---------------------------------------------------------------------------


class TestInstallAndRetry:
    def test_full_flow(self):
        client = _make_client()
        call_count = 0

        def fn():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ImportError("No module named 'pdfplumber'")
            return "success"

        dr = _make_detect_result(detected=True, installed=True, slug="pdf-reader")
        with patch.object(client, "detect_and_install", return_value=dr):
            result = client.smart_run(fn)

        assert result.success is True
        assert result.upgraded is True
        assert result.installed_slug == "pdf-reader"
        assert result.installed_version == "1.0.0"
        assert result.detected_capability == "pdf_extraction"


# ---------------------------------------------------------------------------
# Install failure
# ---------------------------------------------------------------------------


class TestInstallFailure:
    def test_no_packages_found(self):
        client = _make_client()

        def fn():
            raise ImportError("No module named 'pdfplumber'")

        dr = _make_detect_result(detected=True, installed=False, error="No packages found")
        with patch.object(client, "detect_and_install", return_value=dr):
            result = client.smart_run(fn)

        assert result.success is False
        assert result.upgraded is False

    def test_trust_blocks_install(self):
        client = _make_client()

        def fn():
            raise ImportError("No module named 'pdfplumber'")

        dr = _make_detect_result(detected=True, installed=False, error="Trust level too low")
        with patch.object(client, "detect_and_install", return_value=dr):
            result = client.smart_run(fn)

        assert result.success is False
        assert result.upgraded is False


# ---------------------------------------------------------------------------
# Retry failure
# ---------------------------------------------------------------------------


class TestRetryFailure:
    def test_install_ok_but_retry_fails(self):
        client = _make_client()

        def fn():
            raise RuntimeError("still broken")

        dr = _make_detect_result(detected=True, installed=True)
        with patch.object(client, "detect_and_install", return_value=dr):
            result = client.smart_run(fn)

        assert result.success is False
        assert result.upgraded is True
        assert result.original_error == "still broken"
        assert result.error == "still broken"


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------


class TestCallbacks:
    def test_callbacks_fire(self):
        client = _make_client()
        call_count = 0

        def fn():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ImportError("No module named 'pdfplumber'")
            return "ok"

        on_detect = MagicMock()
        on_install = MagicMock()
        dr = _make_detect_result(detected=True, installed=True)
        with patch.object(client, "detect_and_install", return_value=dr) as mock_dai:
            result = client.smart_run(fn, on_detect=on_detect, on_install=on_install)

        # Callbacks should be passed to detect_and_install
        assert mock_dai.call_args[1]["on_detect"] is on_detect
        assert mock_dai.call_args[1]["on_install"] is on_install

    def test_no_callbacks_on_first_success(self):
        client = _make_client()
        on_detect = MagicMock()
        on_install = MagicMock()

        result = client.smart_run(lambda: 42, on_detect=on_detect, on_install=on_install)

        assert result.success is True
        on_detect.assert_not_called()
        on_install.assert_not_called()


# ---------------------------------------------------------------------------
# Safety
# ---------------------------------------------------------------------------


class TestSafety:
    def test_max_one_retry(self):
        """fn should be called at most twice (1 original + 1 retry)."""
        client = _make_client()
        call_count = 0

        def fn():
            nonlocal call_count
            call_count += 1
            raise RuntimeError(f"fail #{call_count}")

        dr = _make_detect_result(detected=True, installed=True)
        with patch.object(client, "detect_and_install", return_value=dr):
            result = client.smart_run(fn)

        assert call_count == 2
        assert result.success is False

    def test_require_verified_passed(self):
        client = _make_client()

        def fn():
            raise ImportError("No module named 'pdfplumber'")

        dr = _make_detect_result(detected=True, installed=False)
        with patch.object(client, "detect_and_install", return_value=dr) as mock_dai:
            client.smart_run(fn, require_verified=True, require_trusted=True)

        assert mock_dai.call_args[1]["require_verified"] is True
        assert mock_dai.call_args[1]["require_trusted"] is True


# ---------------------------------------------------------------------------
# Delegation
# ---------------------------------------------------------------------------


class TestDelegation:
    def test_smart_run_calls_detect_and_install(self):
        """smart_run should delegate to detect_and_install, not duplicate logic."""
        client = _make_client()

        def fn():
            raise ImportError("No module named 'pdfplumber'")

        dr = _make_detect_result(detected=True, installed=False)
        with patch.object(client, "detect_and_install", return_value=dr) as mock_dai:
            client.smart_run(fn, auto_upgrade_policy="safe")

        mock_dai.assert_called_once()
        kwargs = mock_dai.call_args[1]
        assert kwargs["auto_upgrade_policy"] == "safe"
