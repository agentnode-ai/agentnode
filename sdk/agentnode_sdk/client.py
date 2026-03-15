"""AgentNode API client. Spec §14."""
from __future__ import annotations

import httpx

from agentnode_sdk.exceptions import (
    AgentNodeError,
    AuthError,
    NotFoundError,
    ValidationError,
)
from agentnode_sdk.models import (
    ArtifactInfo,
    CapabilityInfo,
    DependencyInfo,
    InstallMetadata,
    PackageDetail,
    PermissionsInfo,
    ResolvedPackage,
    ResolveResult,
    ScoreBreakdown,
    SearchHit,
    SearchResult,
)

DEFAULT_BASE_URL = "https://api.agentnode.net/v1"

ERROR_CLASS_MAP = {
    401: AuthError,
    403: AuthError,
    404: NotFoundError,
    422: ValidationError,
}


class AgentNode:
    """Spec-compliant SDK client (§14.3). Returns plain dicts."""

    def __init__(self, api_key: str, base_url: str = DEFAULT_BASE_URL):
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"X-API-Key": api_key},
            timeout=30,
        )

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    # --- Public API ---

    def search(
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
        return self._handle(self._client.post("/search", json=body))

    def resolve_upgrade(
        self,
        missing_capability: str,
        framework: str = "",
        runtime: str = "",
        current_capabilities: list[str] | None = None,
        policy: dict | None = None,
    ) -> dict:
        return self._post(
            "/resolve-upgrade",
            missing_capability=missing_capability,
            current_capabilities=current_capabilities or [],
            framework=framework,
            runtime=runtime,
            policy=policy or {},
        )

    def check_policy(
        self,
        package_slug: str,
        framework: str = "",
        policy: dict | None = None,
    ) -> dict:
        return self._post(
            "/check-policy",
            package_slug=package_slug,
            framework=framework,
            policy=policy or {},
        )

    def get_install_metadata(self, package_slug: str, version: str = "") -> dict:
        """Read-only install metadata. Does NOT create installation records."""
        params = {"version": version} if version else {}
        return self._handle(
            self._client.get(f"/packages/{package_slug}/install-info", params=params)
        )

    def get_package(self, slug: str) -> dict:
        return self._handle(self._client.get(f"/packages/{slug}"))

    def validate(self, manifest: dict) -> dict:
        return self._handle(
            self._client.post("/packages/validate", json={"manifest": manifest})
        )

    def install(
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
            self._client.post(f"/packages/{package_slug}/install", json=body)
        )

    def recommend(
        self,
        missing_capabilities: list[str],
        framework: str = "",
        runtime: str = "",
    ) -> dict:
        return self._post(
            "/recommend",
            missing_capabilities=missing_capabilities,
            framework=framework,
            runtime=runtime,
        )

    # --- Internal ---

    def _post(self, path: str, **kwargs) -> dict:
        # Keep lists/dicts even if empty; only filter out None and empty strings
        body = {k: v for k, v in kwargs.items() if v is not None and v != ""}
        return self._handle(self._client.post(path, json=body))

    def _handle(self, response: httpx.Response) -> dict:
        """Parse response. Raise typed AgentNodeError on API errors."""
        if response.status_code >= 400:
            try:
                body = response.json()
                err = body.get("error", {})
                code = err.get("code", "UNKNOWN")
                message = err.get("message", response.text)
            except (ValueError, KeyError):
                code, message = "UNKNOWN", response.text
            exc_class = ERROR_CLASS_MAP.get(response.status_code, AgentNodeError)
            raise exc_class(code, message)
        return response.json()


class AgentNodeClient:
    """Extended client returning typed dataclass models. Backward-compatible."""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        api_key: str | None = None,
        token: str | None = None,
        timeout: float = 30.0,
    ):
        self.base_url = base_url.rstrip("/")
        headers = {}
        if api_key:
            headers["X-API-Key"] = api_key
        elif token:
            headers["Authorization"] = f"Bearer {token}"
        self._client = httpx.Client(
            base_url=self.base_url, headers=headers, timeout=timeout
        )

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def _request(self, method: str, path: str, **kwargs) -> dict:
        resp = self._client.request(method, path, **kwargs)
        if resp.status_code >= 400:
            data = resp.json()
            err = data.get("error", {})
            exc_class = ERROR_CLASS_MAP.get(resp.status_code, AgentNodeError)
            raise exc_class(
                code=err.get("code", "UNKNOWN"),
                message=err.get("message", resp.text),
            )
        return resp.json()

    # --- Search ---

    def search(
        self,
        query: str = "",
        package_type: str | None = None,
        capability_id: str | None = None,
        framework: str | None = None,
        sort_by: str | None = None,
        page: int = 1,
        per_page: int = 20,
    ) -> SearchResult:
        body: dict = {"q": query, "page": page, "per_page": per_page}
        if package_type:
            body["package_type"] = package_type
        if capability_id:
            body["capability_id"] = capability_id
        if framework:
            body["framework"] = framework
        if sort_by:
            body["sort_by"] = sort_by

        data = self._request("POST", "/search", json=body)
        hits = [
            SearchHit(
                slug=h["slug"],
                name=h["name"],
                package_type=h["package_type"],
                summary=h["summary"],
                publisher_slug=h.get("publisher_slug", ""),
                trust_level=h.get("trust_level", "unverified"),
                latest_version=h.get("latest_version"),
                runtime=h.get("runtime"),
                capability_ids=h.get("capability_ids", []),
                download_count=h.get("download_count", 0),
            )
            for h in data.get("hits", [])
        ]
        return SearchResult(query=data["query"], hits=hits, total=data["total"])

    # --- Resolve ---

    def resolve(
        self,
        capabilities: list[str],
        framework: str | None = None,
        runtime: str | None = None,
        package_type: str | None = None,
        limit: int = 10,
    ) -> ResolveResult:
        body: dict = {"capabilities": capabilities, "limit": limit}
        if framework:
            body["framework"] = framework
        if runtime:
            body["runtime"] = runtime
        if package_type:
            body["package_type"] = package_type

        data = self._request("POST", "/resolve", json=body)
        results = [
            ResolvedPackage(
                slug=r["slug"],
                name=r["name"],
                package_type=r["package_type"],
                summary=r["summary"],
                version=r["version"],
                publisher_slug=r["publisher_slug"],
                trust_level=r["trust_level"],
                score=r["score"],
                breakdown=ScoreBreakdown(**r["breakdown"]),
                matched_capabilities=r.get("matched_capabilities", []),
            )
            for r in data.get("results", [])
        ]
        return ResolveResult(results=results, total=data["total"])

    # --- Package detail ---

    def get_package(self, slug: str) -> PackageDetail:
        data = self._request("GET", f"/packages/{slug}")
        lv = data.get("latest_version")
        return PackageDetail(
            slug=data["slug"],
            name=data["name"],
            package_type=data["package_type"],
            summary=data["summary"],
            description=data.get("description"),
            download_count=data["download_count"],
            is_deprecated=data["is_deprecated"],
            latest_version=lv["version_number"] if lv else None,
        )

    # --- Install metadata ---

    def get_install_metadata(
        self, slug: str, version: str | None = None
    ) -> InstallMetadata:
        params = {}
        if version:
            params["version"] = version
        data = self._request("GET", f"/packages/{slug}/install-info", params=params)

        artifact = None
        if data.get("artifact"):
            a = data["artifact"]
            artifact = ArtifactInfo(
                url=a.get("url"),
                hash_sha256=a.get("hash_sha256"),
                size_bytes=a.get("size_bytes"),
            )

        caps = [
            CapabilityInfo(
                name=c["name"],
                capability_id=c["capability_id"],
                capability_type=c["capability_type"],
            )
            for c in data.get("capabilities", [])
        ]
        deps = [
            DependencyInfo(
                package_slug=d["package_slug"],
                role=d.get("role"),
                is_required=d["is_required"],
                min_version=d.get("min_version"),
            )
            for d in data.get("dependencies", [])
        ]
        perms = None
        if data.get("permissions"):
            p = data["permissions"]
            perms = PermissionsInfo(
                network_level=p["network_level"],
                filesystem_level=p["filesystem_level"],
                code_execution_level=p["code_execution_level"],
                data_access_level=p["data_access_level"],
                user_approval_level=p["user_approval_level"],
            )

        return InstallMetadata(
            slug=data["slug"],
            version=data["version"],
            package_type=data["package_type"],
            install_mode=data["install_mode"],
            hosting_type=data["hosting_type"],
            runtime=data["runtime"],
            entrypoint=data.get("entrypoint"),
            artifact=artifact,
            capabilities=caps,
            dependencies=deps,
            permissions=perms,
        )

    # --- Download ---

    def download(self, slug: str, version: str | None = None) -> str | None:
        """Track download and return artifact URL."""
        params = {}
        if version:
            params["version"] = version
        data = self._request("POST", f"/packages/{slug}/download", params=params)
        return data.get("download_url")
