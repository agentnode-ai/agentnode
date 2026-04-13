"""Async AgentNode API client. Mirrors AgentNode (sync) using httpx.AsyncClient."""
from __future__ import annotations

import httpx

from agentnode_sdk.client import DEFAULT_BASE_URL, ERROR_CLASS_MAP
from agentnode_sdk.exceptions import AgentNodeError


class AsyncAgentNode:
    """Async variant of the AgentNode SDK client. Returns plain dicts."""

    def __init__(self, api_key: str, base_url: str = DEFAULT_BASE_URL):
        # Ensure base_url ends with /v1 for API routing (parity with sync AgentNode)
        base = base_url.rstrip("/")
        if not base.endswith("/v1"):
            base = f"{base}/v1"
        self._client = httpx.AsyncClient(
            base_url=base,
            headers={"X-API-Key": api_key},
            timeout=30,
        )

    async def close(self):
        await self._client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()

    # --- Public API ---

    async def search(
        self,
        query: str = "",
        capability_id: str = "",
        framework: str = "",
        sort_by: str = "relevance",
        page: int = 1,
    ) -> dict:
        body = {k: v for k, v in {
            "q": query,
            "capability_id": capability_id,
            "framework": framework,
            "sort_by": sort_by,
            "page": page,
        }.items() if v}
        return self._handle(await self._client.post("/search", json=body))

    async def resolve_upgrade(
        self,
        missing_capability: str,
        framework: str = "",
        runtime: str = "",
        current_capabilities: list[str] | None = None,
        policy: dict | None = None,
    ) -> dict:
        return await self._post(
            "/resolve-upgrade",
            missing_capability=missing_capability,
            current_capabilities=current_capabilities or [],
            framework=framework,
            runtime=runtime,
            policy=policy or {},
        )

    async def check_policy(
        self,
        package_slug: str,
        framework: str = "",
        policy: dict | None = None,
    ) -> dict:
        return await self._post(
            "/check-policy",
            package_slug=package_slug,
            framework=framework,
            policy=policy or {},
        )

    async def get_install_metadata(self, package_slug: str, version: str = "") -> dict:
        """Read-only install metadata. Does NOT create installation records."""
        params = {"version": version} if version else {}
        return self._handle(
            await self._client.get(f"/packages/{package_slug}/install-info", params=params)
        )

    async def get_package(self, slug: str) -> dict:
        return self._handle(await self._client.get(f"/packages/{slug}"))

    async def validate(self, manifest: dict) -> dict:
        return self._handle(
            await self._client.post("/packages/validate", json={"manifest": manifest})
        )

    async def install(
        self,
        package_slug: str,
        version: str = "",
        source: str = "sdk",
        event_type: str = "install",
    ) -> dict:
        """Create installation record and get artifact URL."""
        body: dict = {"source": source, "event_type": event_type}
        if version:
            body["version"] = version
        return self._handle(
            await self._client.post(f"/packages/{package_slug}/install", json=body)
        )

    async def recommend(
        self,
        missing_capabilities: list[str],
        framework: str = "",
        runtime: str = "",
    ) -> dict:
        return await self._post(
            "/recommend",
            missing_capabilities=missing_capabilities,
            framework=framework,
            runtime=runtime,
        )

    # --- Internal ---

    async def _post(self, path: str, **kwargs) -> dict:
        body = {k: v for k, v in kwargs.items() if v is not None and v != ""}
        return self._handle(await self._client.post(path, json=body))

    def _handle(self, response: httpx.Response) -> dict:
        """Parse response. Raise typed AgentNodeError on API errors."""
        if response.status_code >= 400:
            code, message = "UNKNOWN", response.text
            try:
                body = response.json()
                if isinstance(body, dict):
                    err = body.get("error", {})
                    if isinstance(err, dict):
                        code = err.get("code", code)
                        message = err.get("message", message)
            except (ValueError, KeyError, TypeError):
                pass
            exc_class = ERROR_CLASS_MAP.get(response.status_code, AgentNodeError)
            raise exc_class(code, message)
        # Guard against non-JSON response (e.g. HTML error page or empty body)
        ctype = response.headers.get("content-type", "")
        if "json" not in ctype.lower():
            raise AgentNodeError(
                "UNKNOWN",
                f"Expected JSON response, got content-type={ctype!r}",
            )
        try:
            return response.json()
        except ValueError as exc:
            raise AgentNodeError("UNKNOWN", f"Invalid JSON response: {exc}") from exc
