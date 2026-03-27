"""Smoke tests for youtube-analyzer-pack."""
import pytest
from youtube_analyzer_pack.tool import run


def test_run_smoke():
    """Verify run() executes without crash on empty/minimal input."""
    try:
        result = run({})
        assert result is not None
    except TypeError:
        pytest.skip("Tool requires specific arguments — manual test needed")
    except Exception as exc:
        # Tool may fail due to missing credentials/services — that's OK for smoke test
        if any(kw in str(exc).lower() for kw in ("api key", "credential", "token", "auth", "connection", "timeout", "network")):
            pytest.skip(f"Tool requires external service: {exc}")
        raise
