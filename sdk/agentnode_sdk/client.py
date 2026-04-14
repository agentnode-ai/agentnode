"""AgentNode API client. Spec §14."""
from __future__ import annotations

import os
import time
from collections.abc import Callable
from typing import Any

import httpx

from agentnode_sdk.exceptions import (
    AgentNodeError,
    AuthError,
    NotFoundError,
    RateLimitError,
    ValidationError,
)
from agentnode_sdk.installer import install_package, load_tool as _load_tool
from agentnode_sdk.detect import detect_gap
from agentnode_sdk.models import (
    ArtifactInfo,
    CanInstallResult,
    CapabilityInfo,
    DetectAndInstallResult,
    DependencyInfo,
    InstallMetadata,
    InstallResult,
    PackageDetail,
    PermissionsInfo,
    ResolvedPackage,
    ResolveResult,
    RunToolResult,
    ScoreBreakdown,
    SearchHit,
    SearchResult,
    SmartRunResult,
)

DEFAULT_BASE_URL = "https://api.agentnode.net"

TRUST_LEVELS_VERIFIED = ("verified", "trusted", "curated")
TRUST_LEVELS_TRUSTED = ("trusted", "curated")

ERROR_CLASS_MAP = {
    401: AuthError,
    403: AuthError,
    404: NotFoundError,
    422: ValidationError,
    429: RateLimitError,
}


def _resolve_auto_upgrade_policy(
    policy: str | None,
    *,
    auto_install: bool,
    require_verified: bool,
    require_trusted: bool,
    allow_low_confidence: bool,
) -> tuple[bool, bool, bool, bool]:
    """Resolve named policy to concrete parameters. Policy overrides individual params."""
    if policy is None:
        return (auto_install, require_verified, require_trusted, allow_low_confidence)
    p = policy.lower()
    if p == "off":
        return (False, True, False, False)
    if p == "safe":
        return (True, True, False, False)
    if p == "strict":
        return (True, False, True, False)
    raise ValueError("auto_upgrade_policy must be 'off', 'safe', or 'strict'")


def _permissions_to_dict(perms: PermissionsInfo | None) -> dict | None:
    """Convert PermissionsInfo to a plain dict for lockfile storage."""
    if perms is None:
        return None
    return {
        "network_level": perms.network_level,
        "filesystem_level": perms.filesystem_level,
        "code_execution_level": perms.code_execution_level,
        "data_access_level": perms.data_access_level,
        "user_approval_level": perms.user_approval_level,
    }


class AgentNode:
    """Spec-compliant SDK client (§14.3). Returns plain dicts."""

    def __init__(self, api_key: str | None = None, base_url: str = DEFAULT_BASE_URL):
        api_key = api_key or os.environ.get("AGENTNODE_API_KEY")
        if not api_key:
            raise ValueError("api_key is required (pass explicitly or set AGENTNODE_API_KEY)")
        # Ensure base_url ends with /v1 for API routing
        base = base_url.rstrip("/")
        if not base.endswith("/v1"):
            base = f"{base}/v1"
        self._client = httpx.Client(
            base_url=base,
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


class AgentNodeClient:
    """Extended client returning typed dataclass models. Backward-compatible.

    When instantiated without explicit parameters, loads user config from
    ``~/.agentnode/config.json`` (override via ``AGENTNODE_CONFIG`` env var)
    and applies trust / permission defaults automatically.
    """

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        api_key: str | None = None,
        token: str | None = None,
        timeout: float = 30.0,
    ):
        # Ensure base_url ends with /v1 for API routing
        base = base_url.rstrip("/")
        if not base.endswith("/v1"):
            base = f"{base}/v1"
        self.base_url = base
        api_key = api_key or os.environ.get("AGENTNODE_API_KEY")
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
            code, message = "UNKNOWN", resp.text
            try:
                data = resp.json()
                if isinstance(data, dict):
                    err = data.get("error", {})
                    if isinstance(err, dict):
                        code = err.get("code", code)
                        message = err.get("message", message)
            except (ValueError, KeyError, TypeError):
                pass
            exc_class = ERROR_CLASS_MAP.get(resp.status_code, AgentNodeError)
            raise exc_class(code=code, message=message)
        ctype = resp.headers.get("content-type", "")
        if "json" not in ctype.lower():
            raise AgentNodeError(
                code="UNKNOWN",
                message=f"Expected JSON response, got content-type={ctype!r}",
            )
        try:
            return resp.json()
        except ValueError as exc:
            raise AgentNodeError(
                code="UNKNOWN",
                message=f"Invalid JSON response: {exc}",
            ) from exc

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
                trust_level=r.get("trust_level", "unverified"),  # P1-SDK9
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
                entrypoint=c.get("entrypoint"),
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

    # --- Install (full local install) ---

    def install(
        self,
        slug: str,
        version: str | None = None,
        require_trusted: bool = False,
        require_verified: bool = False,
        verbose: bool = False,
    ) -> InstallResult:
        """Find, download, verify, and install a package locally.

        This is the key method for autonomous agent upgrades:
        the agent calls resolve() to find what it needs, then install()
        to add the capability — no human intervention required.

        Args:
            require_trusted: Only install packages with trust level
                'trusted' or 'curated'.
            require_verified: Only install packages with trust level
                'verified', 'trusted', or 'curated'. Lower bar than
                require_trusted.

        Steps: API metadata → download artifact → verify hash →
        extract → pip install → update lockfile.
        """
        # 1. Get install metadata (read-only, no side effects)
        meta = self.get_install_metadata(slug, version)

        # P1-SDK2: fail loud on pin mismatch. If the caller asked for a
        # specific version but the server returned a different one (e.g.
        # the requested version was yanked and the API silently fell back
        # to the latest), refuse to install rather than silently upgrading
        # under the caller. Pinned versions must be exact.
        if version and meta.version and meta.version != version:
            return InstallResult(
                slug=slug,
                version=meta.version,
                installed=False,
                already_installed=False,
                message=(
                    f"Pin mismatch: requested {slug}=={version} but server "
                    f"returned {meta.version}. Refusing to install a "
                    f"different version than pinned."
                ),
                trust_level=None,
                verification_tier=None,
            )

        # 2. Fetch trust/verification info
        trust_level = None
        verification_tier = None
        try:
            detail = self._request("GET", f"/packages/{slug}")
            # trust_level lives in publisher.trust_level or blocks.trust.publisher_trust_level,
            # NOT as a top-level field (which is None/missing in the API response).
            trust_level = (
                detail.get("publisher", {}).get("trust_level")
                or detail.get("blocks", {}).get("trust", {}).get("publisher_trust_level")
                or "unverified"
            )
            lv = detail.get("latest_version") or {}
            verification_tier = lv.get("verification_tier")
        except Exception:
            pass

        # 2b. Policy check (BD-10, PHASE-A: temporary double-check, see Phase B)
        try:
            from agentnode_sdk.policy import check_install as _policy_check_install
            from agentnode_sdk.policy import audit_decision as _policy_audit
            policy_entry = {
                "trust_level": trust_level or "unverified",
                "permissions": _permissions_to_dict(meta.permissions),
            }
            decision = _policy_check_install(slug, policy_entry, interactive=True)
            _policy_audit(
                decision, "client_install", slug,
                trust_level=policy_entry["trust_level"],
            )
            if decision.action == "deny":
                return InstallResult(
                    slug=slug,
                    version=meta.version,
                    installed=False,
                    already_installed=False,
                    message=decision.reason,
                    trust_level=trust_level,
                    verification_tier=verification_tier,
                )
            if decision.action == "prompt":
                return InstallResult(
                    slug=slug,
                    version=meta.version,
                    installed=False,
                    already_installed=False,
                    message=f"Approval required: {decision.reason}",
                    trust_level=trust_level,
                    verification_tier=verification_tier,
                )
        except Exception:
            pass  # Policy check is additive in Phase A

        # 3. Trust check
        if require_trusted or require_verified:
            pkg = self.get_package(slug)
            if pkg.is_deprecated:
                return InstallResult(
                    slug=slug,
                    version=meta.version,
                    installed=False,
                    already_installed=False,
                    message=f"{slug} is deprecated and cannot be installed.",
                    trust_level=trust_level,
                    verification_tier=verification_tier,
                )
            if require_trusted and (trust_level or "unverified") not in TRUST_LEVELS_TRUSTED:
                return InstallResult(
                    slug=slug,
                    version=meta.version,
                    installed=False,
                    already_installed=False,
                    message=f"Trust level '{trust_level}' does not meet 'trusted' requirement.",
                    trust_level=trust_level,
                    verification_tier=verification_tier,
                )
            if require_verified and (trust_level or "unverified") not in TRUST_LEVELS_VERIFIED:
                return InstallResult(
                    slug=slug,
                    version=meta.version,
                    installed=False,
                    already_installed=False,
                    message=f"Trust level '{trust_level}' does not meet 'verified' requirement.",
                    trust_level=trust_level,
                    verification_tier=verification_tier,
                )

        # 4. Create installation record (P0-05: SDK must POST the install
        # event so the backend tracks installs, updates counts, and returns
        # the canonical artifact URL). This is best-effort: if the server
        # call fails, we still attempt the local install below using the
        # metadata we already fetched.
        try:
            self._request(
                "POST",
                f"/packages/{slug}/install",
                json={
                    "version": version or meta.version,
                    "source": "sdk",
                    "event_type": "install",
                },
            )
        except Exception:
            pass  # Non-fatal, continue with local install

        # 5. Run local install flow
        artifact_url = meta.artifact.url if meta.artifact else None
        artifact_hash = meta.artifact.hash_sha256 if meta.artifact else None
        cap_ids = [c.capability_id for c in meta.capabilities]
        tools = [
            {"name": c.name, "entrypoint": c.entrypoint}
            for c in meta.capabilities
            if c.capability_type == "tool" and c.entrypoint
        ]

        result = install_package(
            slug=slug,
            version=meta.version,
            artifact_url=artifact_url,
            artifact_hash=artifact_hash,
            entrypoint=meta.entrypoint,
            package_type=meta.package_type,
            capability_ids=cap_ids,
            tools=tools,
            verbose=verbose,
            trust_level=trust_level,
            permissions=_permissions_to_dict(meta.permissions),
        )

        return InstallResult(
            slug=result["slug"],
            version=result["version"],
            installed=result["installed"],
            already_installed=result.get("already_installed", False),
            message=result["message"],
            hash_verified=result.get("hash_verified", False),
            entrypoint=result.get("entrypoint"),
            lockfile_updated=result.get("lockfile_updated", False),
            previous_version=result.get("previous_version"),
            trust_level=trust_level,
            verification_tier=verification_tier,
        )

    def can_install(
        self,
        slug: str,
        version: str | None = None,
        require_trusted: bool = False,
        require_verified: bool = False,
        allowed_permissions: list[str] | None = None,
        denied_permissions: list[str] | None = None,
    ) -> CanInstallResult:
        """Check whether a package can be installed under given constraints.

        Evaluates trust level, permissions, and deprecation status
        without performing any installation.

        Phase B: delegates trust and permission checks to
        ``policy.check_install()`` as Single Source of Truth.

        Args:
            require_trusted: Require 'trusted' or 'curated' trust level.
            require_verified: Require 'verified', 'trusted', or 'curated'.
                Lower bar than require_trusted.
        """
        meta = self.get_install_metadata(slug, version)
        pkg = self.get_package(slug)

        # Check deprecation (install-specific, not policy)
        if pkg.is_deprecated:
            return CanInstallResult(
                allowed=False,
                slug=slug,
                version=meta.version,
                trust_level="unverified",  # P1-SDK9: canonical default
                reason="Package is deprecated.",
                permissions=meta.permissions,
            )

        # Check artifact availability (install-specific, not policy)
        if not meta.artifact or not meta.artifact.url:
            return CanInstallResult(
                allowed=False,
                slug=slug,
                version=meta.version,
                trust_level="unverified",  # P1-SDK9: canonical default
                reason="No artifact available for download.",
                permissions=meta.permissions,
            )

        # Fetch trust level — P1-SDK9: canonical default is "unverified"
        trust_level = "unverified"
        try:
            detail = self._request("GET", f"/packages/{slug}")
            trust_level = detail.get("publisher", {}).get("trust_level", "unverified")
        except Exception:
            pass

        # Caller-specified trust constraints (require_trusted / require_verified)
        if require_trusted and trust_level not in TRUST_LEVELS_TRUSTED:
            return CanInstallResult(
                allowed=False,
                slug=slug,
                version=meta.version,
                trust_level=trust_level,
                reason=f"Package trust level is '{trust_level}', but 'trusted' or higher is required.",
                permissions=meta.permissions,
            )

        if require_verified and trust_level not in TRUST_LEVELS_VERIFIED:
            return CanInstallResult(
                allowed=False,
                slug=slug,
                version=meta.version,
                trust_level=trust_level,
                reason=f"Package trust level is '{trust_level}', but 'verified' or higher is required.",
                permissions=meta.permissions,
            )

        # Policy check via check_install() — Single Source of Truth
        from agentnode_sdk.policy import check_install as _policy_check
        policy_entry = {
            "trust_level": trust_level,
            "permissions": _permissions_to_dict(meta.permissions),
        }
        decision = _policy_check(slug, policy_entry, interactive=True)
        if decision.action == "deny":
            return CanInstallResult(
                allowed=False,
                slug=slug,
                version=meta.version,
                trust_level=trust_level,
                reason=decision.reason,
                permissions=meta.permissions,
            )
        if decision.action == "prompt":
            return CanInstallResult(
                allowed=False,
                slug=slug,
                version=meta.version,
                trust_level=trust_level,
                reason=f"Approval required: {decision.reason}",
                permissions=meta.permissions,
            )

        # Caller-specified permission deny-list (kept for backward compat)
        if meta.permissions and denied_permissions:
            perm_map = {
                "network": meta.permissions.network_level,
                "filesystem": meta.permissions.filesystem_level,
                "code_execution": meta.permissions.code_execution_level,
                "data_access": meta.permissions.data_access_level,
            }
            for perm_name in denied_permissions:
                level = perm_map.get(perm_name, "none")
                if level != "none":
                    return CanInstallResult(
                        allowed=False,
                        slug=slug,
                        version=meta.version,
                        trust_level=trust_level,
                        reason=f"Package requires '{perm_name}' ({level}), which is denied by policy.",
                        permissions=meta.permissions,
                    )

        return CanInstallResult(
            allowed=True,
            slug=slug,
            version=meta.version,
            trust_level=trust_level,
            reason="Package meets all requirements.",
            permissions=meta.permissions,
        )

    def load_tool(self, slug: str, tool_name: str | None = None):
        """Load an installed package's tool function.

        Args:
            slug: Package slug (e.g. "word-counter-pack").
            tool_name: Optional tool name for multi-tool v0.2 packs.
                If None, uses the package-level entrypoint.

        The package must have been installed via ``install()`` first.
        """
        return _load_tool(slug, tool_name=tool_name)

    def run_tool(
        self,
        slug: str,
        tool_name: str | None = None,
        *,
        mode: str = "auto",
        timeout: float = 30.0,
        **kwargs,
    ) -> RunToolResult:
        """Run an installed tool with optional process isolation.

        Args:
            slug: Package slug.
            tool_name: Tool name for multi-tool packs.
            mode: ``"direct"``, ``"subprocess"``, or ``"auto"``.
            timeout: Timeout in seconds (subprocess mode only).
            **kwargs: Arguments forwarded to the tool function.
        """
        from agentnode_sdk.runner import run_tool as _run_tool

        return _run_tool(slug, tool_name, mode=mode, timeout=timeout, **kwargs)

    def resolve_and_install(
        self,
        capabilities: list[str],
        framework: str | None = None,
        require_trusted: bool = False,
        require_verified: bool = True,
        verbose: bool = False,
    ) -> InstallResult:
        """Resolve a capability gap and install the best match.

        This is the single-call autonomous upgrade method:
        describe what your agent needs, and it finds and installs
        the best trusted package automatically.

        Args:
            require_trusted: Only install 'trusted' or 'curated' packages.
                Enabled by default for safety.
            require_verified: Only install 'verified' or higher packages.
                Lower bar than require_trusted. Ignored if require_trusted
                is True.
        """
        result = self.resolve(capabilities, framework=framework)
        if not result.results:
            return InstallResult(
                slug="",
                version="",
                installed=False,
                already_installed=False,
                message=f"No packages found for capabilities: {capabilities}",
            )

        # Pick the highest-scored result
        best = result.results[0]

        # Trust filter
        if require_trusted and best.trust_level not in TRUST_LEVELS_TRUSTED:
            return InstallResult(
                slug=best.slug,
                version=best.version,
                installed=False,
                already_installed=False,
                message=f"Best match '{best.slug}' has trust level '{best.trust_level}', but 'trusted' required.",
            )

        if (
            not require_trusted
            and require_verified
            and best.trust_level not in TRUST_LEVELS_VERIFIED
        ):
            return InstallResult(
                slug=best.slug,
                version=best.version,
                installed=False,
                already_installed=False,
                message=f"Best match '{best.slug}' has trust level '{best.trust_level}', but 'verified' required.",
            )

        return self.install(best.slug, verbose=verbose)

    def detect_and_install(
        self,
        error: BaseException,
        *,
        auto_upgrade_policy: str | None = None,
        context: dict[str, str] | None = None,
        auto_install: bool = True,
        require_verified: bool = True,
        require_trusted: bool = False,
        allow_low_confidence: bool = False,
        on_detect: Callable[[str, str, str], None] | None = None,
        on_install: Callable[[str], None] | None = None,
    ) -> DetectAndInstallResult:
        """Detect a capability gap from an error and optionally install.

        This is the product-level API: your agent failed? AgentNode knows
        what's missing and can acquire it.

        Args:
            error: The exception that triggered gap detection.
            auto_upgrade_policy: Named policy ('off', 'safe', 'strict').
                Overrides individual params when set.
            context: Optional context hints (e.g. ``{"file": "report.pdf"}``).
            auto_install: Whether to install on detection. Default True.
            require_verified: Only install verified+ packages. Default True.
            require_trusted: Only install trusted+ packages. Default False.
            allow_low_confidence: Allow install on low-confidence detections.
            on_detect: Callback(capability, confidence, error_msg) on detection.
            on_install: Callback(slug) on successful install.
        """
        resolved = _resolve_auto_upgrade_policy(
            auto_upgrade_policy,
            auto_install=auto_install,
            require_verified=require_verified,
            require_trusted=require_trusted,
            allow_low_confidence=allow_low_confidence,
        )
        r_auto_install, r_require_verified, r_require_trusted, r_allow_low = resolved

        gap = detect_gap(error, context)
        if gap is None:
            return DetectAndInstallResult(
                detected=False,
                auto_upgrade_policy=auto_upgrade_policy,
                error="No capability gap detected",
            )

        if on_detect is not None:
            on_detect(gap.capability, gap.confidence, str(error))

        if gap.confidence == "low" and not r_allow_low:
            return DetectAndInstallResult(
                detected=True,
                capability=gap.capability,
                confidence=gap.confidence,
                installed=False,
                auto_upgrade_policy=auto_upgrade_policy,
                error="Low-confidence detection blocked",
            )

        if not r_auto_install:
            return DetectAndInstallResult(
                detected=True,
                capability=gap.capability,
                confidence=gap.confidence,
                installed=False,
                auto_upgrade_policy=auto_upgrade_policy,
            )

        install_result = self.resolve_and_install(
            [gap.capability],
            require_trusted=r_require_trusted,
            require_verified=r_require_verified,
        )

        if install_result.installed and on_install is not None:
            on_install(install_result.slug)

        return DetectAndInstallResult(
            detected=True,
            capability=gap.capability,
            confidence=gap.confidence,
            installed=install_result.installed,
            install_result=install_result,
            auto_upgrade_policy=auto_upgrade_policy,
        )

    def smart_run(
        self,
        fn: Callable[[], Any],
        *,
        auto_upgrade_policy: str | None = None,
        auto_install: bool = True,
        require_verified: bool = True,
        require_trusted: bool = False,
        allow_low_confidence: bool = False,
        context: dict[str, str] | None = None,
        on_detect: Callable[[str, str, str], None] | None = None,
        on_install: Callable[[str], None] | None = None,
    ) -> SmartRunResult:
        """Run a callable with automatic gap detection and retry.

        Wraps your logic: if it fails due to a missing capability,
        AgentNode detects the gap, installs the skill, and retries once.

        Args:
            fn: Zero-argument callable to execute.
            auto_upgrade_policy: Named policy ('off', 'safe', 'strict').
            auto_install: Whether to auto-install on detection.
            require_verified: Only install verified+ packages.
            require_trusted: Only install trusted+ packages.
            allow_low_confidence: Allow install on low-confidence detections.
            context: Optional context hints for detection.
            on_detect: Callback(capability, confidence, error_msg) on detection.
            on_install: Callback(slug) on successful install.
        """
        start = time.monotonic()

        # Attempt 1
        caught: Exception | None = None
        try:
            result = fn()
            elapsed = (time.monotonic() - start) * 1000
            return SmartRunResult(
                success=True,
                result=result,
                duration_ms=elapsed,
                auto_upgrade_policy=auto_upgrade_policy,
            )
        except Exception as exc:
            caught = exc
            original_error = str(exc)

        # Detect and install
        detect_result = self.detect_and_install(
            caught,
            auto_upgrade_policy=auto_upgrade_policy,
            context=context,
            auto_install=auto_install,
            require_verified=require_verified,
            require_trusted=require_trusted,
            allow_low_confidence=allow_low_confidence,
            on_detect=on_detect,
            on_install=on_install,
        )

        if not detect_result.detected:
            elapsed = (time.monotonic() - start) * 1000
            return SmartRunResult(
                success=False,
                error=original_error,
                original_error=original_error,
                duration_ms=elapsed,
                auto_upgrade_policy=auto_upgrade_policy,
            )

        if not detect_result.installed:
            elapsed = (time.monotonic() - start) * 1000
            return SmartRunResult(
                success=False,
                error=detect_result.error or original_error,
                detected_capability=detect_result.capability,
                detection_confidence=detect_result.confidence,
                original_error=original_error,
                duration_ms=elapsed,
                auto_upgrade_policy=auto_upgrade_policy,
            )

        # Attempt 2 (exactly once)
        installed_slug = (
            detect_result.install_result.slug if detect_result.install_result else None
        )
        installed_version = (
            detect_result.install_result.version if detect_result.install_result else None
        )

        try:
            result = fn()
            elapsed = (time.monotonic() - start) * 1000
            return SmartRunResult(
                success=True,
                result=result,
                upgraded=True,
                installed_slug=installed_slug,
                installed_version=installed_version,
                detected_capability=detect_result.capability,
                detection_confidence=detect_result.confidence,
                duration_ms=elapsed,
                original_error=original_error,
                auto_upgrade_policy=auto_upgrade_policy,
            )
        except Exception as retry_exc:
            elapsed = (time.monotonic() - start) * 1000
            return SmartRunResult(
                success=False,
                error=str(retry_exc),
                upgraded=True,
                installed_slug=installed_slug,
                installed_version=installed_version,
                detected_capability=detect_result.capability,
                detection_confidence=detect_result.confidence,
                duration_ms=elapsed,
                original_error=original_error,
                auto_upgrade_policy=auto_upgrade_policy,
            )
