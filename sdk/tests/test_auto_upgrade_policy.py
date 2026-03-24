"""Tests for auto_upgrade_policy resolution and behavior."""
import pytest

from agentnode_sdk.client import _resolve_auto_upgrade_policy


class TestPolicyResolution:
    def test_off_disables_install(self):
        auto_install, req_verified, req_trusted, allow_low = _resolve_auto_upgrade_policy(
            "off",
            auto_install=True,
            require_verified=False,
            require_trusted=False,
            allow_low_confidence=True,
        )
        assert auto_install is False
        assert req_verified is True
        assert req_trusted is False
        assert allow_low is False

    def test_safe_enables_verified(self):
        auto_install, req_verified, req_trusted, allow_low = _resolve_auto_upgrade_policy(
            "safe",
            auto_install=False,
            require_verified=False,
            require_trusted=True,
            allow_low_confidence=True,
        )
        assert auto_install is True
        assert req_verified is True
        assert req_trusted is False
        assert allow_low is False

    def test_strict_requires_trusted(self):
        auto_install, req_verified, req_trusted, allow_low = _resolve_auto_upgrade_policy(
            "strict",
            auto_install=False,
            require_verified=True,
            require_trusted=False,
            allow_low_confidence=True,
        )
        assert auto_install is True
        assert req_verified is False
        assert req_trusted is True
        assert allow_low is False


class TestPolicyOverridesParams:
    def test_strict_overrides_allow_low(self):
        """Policy='strict' forces allow_low_confidence=False even if passed True."""
        _, _, _, allow_low = _resolve_auto_upgrade_policy(
            "strict",
            auto_install=True,
            require_verified=True,
            require_trusted=True,
            allow_low_confidence=True,
        )
        assert allow_low is False


class TestInvalidPolicy:
    def test_unknown_policy_raises(self):
        with pytest.raises(ValueError, match="auto_upgrade_policy must be"):
            _resolve_auto_upgrade_policy(
                "aggressive",
                auto_install=True,
                require_verified=True,
                require_trusted=False,
                allow_low_confidence=False,
            )


class TestNonePolicy:
    def test_none_passes_through(self):
        """When policy is None, individual params are returned as-is."""
        result = _resolve_auto_upgrade_policy(
            None,
            auto_install=False,
            require_verified=True,
            require_trusted=True,
            allow_low_confidence=True,
        )
        assert result == (False, True, True, True)


class TestPolicyInDetectAndInstall:
    def test_off_blocks_install(self):
        """Policy='off' on detect_and_install should detect but not install."""
        from unittest.mock import MagicMock, patch

        from agentnode_sdk import AgentNodeClient

        client = AgentNodeClient.__new__(AgentNodeClient)
        client._client = MagicMock()

        error = ImportError("No module named 'pdfplumber'")
        with patch.object(client, "resolve_and_install") as mock_rai:
            result = client.detect_and_install(error, auto_upgrade_policy="off")

        assert result.detected is True
        assert result.capability == "pdf_extraction"
        assert result.installed is False
        mock_rai.assert_not_called()


class TestPolicyInSmartRunResult:
    def test_policy_in_result(self):
        """auto_upgrade_policy field should be populated in SmartRunResult."""
        from agentnode_sdk import AgentNodeClient

        client = AgentNodeClient.__new__(AgentNodeClient)
        client._client = None

        result = client.smart_run(
            lambda: 42,
            auto_upgrade_policy="safe",
        )
        assert result.success is True
        assert result.auto_upgrade_policy == "safe"
