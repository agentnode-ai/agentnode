"""Sprint B tests for AgentNodeClient.install() — P0-05 install record POST."""
from __future__ import annotations

import httpx
import pytest
import respx

from agentnode_sdk import AgentNodeClient

BASE = "https://api.agentnode.net"


def _install_info_payload(slug: str = "pdf-reader", version: str = "1.0.0") -> dict:
    return {
        "slug": slug,
        "version": version,
        "package_type": "toolpack",
        "install_mode": "package",
        "hosting_type": "agentnode_hosted",
        "runtime": "python",
        "entrypoint": f"{slug.replace('-', '_')}.tool",
        "artifact": {
            "url": "https://s3.example.com/artifact.tar.gz",
            "hash_sha256": "abc123",
            "size_bytes": 1000,
        },
        "capabilities": [
            {"name": "extract", "capability_id": "pdf_extraction", "capability_type": "tool"},
        ],
        "dependencies": [],
        "permissions": {
            "network_level": "none",
            "filesystem_level": "temp",
            "code_execution_level": "none",
            "data_access_level": "input_only",
            "user_approval_level": "never",
        },
        "published_at": "2026-01-01T00:00:00Z",
    }


def _package_detail_payload(slug: str = "pdf-reader", trust: str = "verified") -> dict:
    return {
        "slug": slug,
        "name": "PDF Reader",
        "package_type": "toolpack",
        "summary": "Read PDFs",
        "description": None,
        "download_count": 0,
        "is_deprecated": False,
        "latest_version": {
            "version_number": "1.0.0",
            "channel": "stable",
            "published_at": "2026-01-01T00:00:00Z",
            "verification_tier": "passed",
        },
        "publisher": {"slug": "t", "display_name": "T", "trust_level": trust},
        "trust_level": trust,
        "blocks": {},
    }


@respx.mock
def test_p0_05_install_posts_install_record(monkeypatch):
    """P0-05: AgentNodeClient.install() must POST /packages/{slug}/install
    to create an install record on the backend. Previously it only
    downloaded and installed locally, so install counts never incremented."""
    # Stub the HTTP layer.
    respx.get(f"{BASE}/v1/packages/pdf-reader/install-info").mock(
        return_value=httpx.Response(200, json=_install_info_payload())
    )
    respx.get(f"{BASE}/v1/packages/pdf-reader").mock(
        return_value=httpx.Response(200, json=_package_detail_payload())
    )
    install_route = respx.post(f"{BASE}/v1/packages/pdf-reader/install").mock(
        return_value=httpx.Response(200, json={
            "package_slug": "pdf-reader",
            "version": "1.0.0",
            "artifact_url": "https://s3.example.com/artifact.tar.gz",
            "artifact_hash": "abc123",
            "entrypoint": "pdf_reader.tool",
            "post_install_code": None,
            "installation_id": "inst_abc",
            "deprecated": False,
            "tools": [],
            "verification_status": "passed",
            "verification_tier": "passed",
            "verification_score": 90,
            "install_resolution": "latest",
        })
    )
    # Stub the (legacy) download tracking endpoint; may or may not be hit.
    respx.post(f"{BASE}/v1/packages/pdf-reader/download").mock(
        return_value=httpx.Response(200, json={"download_url": "https://s3.example.com/artifact.tar.gz"})
    )

    # Prevent the local install flow from actually running — we only care
    # that the HTTP POST happens.
    monkeypatch.setattr(
        "agentnode_sdk.client.install_package",
        lambda **kwargs: {
            "slug": kwargs["slug"],
            "version": kwargs["version"],
            "installed": True,
            "already_installed": False,
            "message": "ok",
            "hash_verified": True,
            "entrypoint": kwargs.get("entrypoint"),
            "lockfile_updated": True,
        },
    )

    with AgentNodeClient(api_key="k") as client:
        result = client.install("pdf-reader")

    assert install_route.called, (
        "AgentNodeClient.install() must POST /packages/{slug}/install"
    )
    import json
    body = json.loads(install_route.calls[0].request.content)
    assert body["source"] == "sdk"
    assert body["event_type"] == "install"
    assert result.installed is True


@respx.mock
def test_p0_05_install_tolerates_post_failure(monkeypatch):
    """Network failure on the install-record POST must not abort the local
    install — the server call is best-effort."""
    respx.get(f"{BASE}/v1/packages/pdf-reader/install-info").mock(
        return_value=httpx.Response(200, json=_install_info_payload())
    )
    respx.get(f"{BASE}/v1/packages/pdf-reader").mock(
        return_value=httpx.Response(200, json=_package_detail_payload())
    )
    respx.post(f"{BASE}/v1/packages/pdf-reader/install").mock(
        return_value=httpx.Response(503, json={"error": {"code": "SERVICE_UNAVAILABLE", "message": "down"}})
    )
    respx.post(f"{BASE}/v1/packages/pdf-reader/download").mock(
        return_value=httpx.Response(200, json={"download_url": "https://s3.example.com/x.tar.gz"})
    )

    monkeypatch.setattr(
        "agentnode_sdk.client.install_package",
        lambda **kwargs: {
            "slug": kwargs["slug"],
            "version": kwargs["version"],
            "installed": True,
            "already_installed": False,
            "message": "ok",
            "hash_verified": True,
            "entrypoint": kwargs.get("entrypoint"),
            "lockfile_updated": True,
        },
    )

    with AgentNodeClient(api_key="k") as client:
        result = client.install("pdf-reader")
    assert result.installed is True
