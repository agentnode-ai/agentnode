"""Server-side registry re-verification for MCP submissions.

Authoritative source of truth for registry-checkable facts. Re-derives what the
client ``agentnode mcp verify`` *claimed*, WITHOUT executing any MCP code — pure
npm/PyPI metadata lookups plus manifest inspection.

The client ``verification_report`` is advisory (maintainer-attested). The output
of this module (``server_verification``) is what the publish gate trusts.

Note on honesty: ``repo_consistency`` proves only that the manifest's
``source_repo`` and the registry's repository URL point at the same GitHub repo.
It does NOT prove the submitter controls that package or repo — true ownership
is a separate, later problem. ``shasum``/``integrity`` are recorded provenance
(what npm reported); the tarball is not downloaded or hashed here.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

import httpx

NPM_REGISTRY = "https://registry.npmjs.org"
PYPI_REGISTRY = "https://pypi.org/pypi"
_ALLOWED_HOSTS = {"registry.npmjs.org", "pypi.org"}

# npm (optional @scope/name) or PyPI name. Reject anything that could inject a
# scheme/host or path traversal once appended to the registry base URL.
_SAFE_NAME = re.compile(r"^@?[a-zA-Z0-9._-]+(?:/[a-zA-Z0-9._-]+)?$")
_GITHUB = re.compile(r"github\.com/([a-zA-Z0-9._-]+/[a-zA-Z0-9._-]+)")

_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0)


_PYPI_NORM = re.compile(r"[-_.]+")


def normalize_package_name(registry: str, name: str) -> str:
    """Canonical match key for a package name.

    PyPI: PEP 503 — lowercase, runs of -_. collapse to a single -.
    npm: lowercase (npm enforces lowercase; @scope/name is preserved).
    """
    n = (name or "").strip()
    if registry == "pypi":
        return _PYPI_NORM.sub("-", n).lower()
    return n.lower()


class RegistryUnavailable(Exception):
    """Registry could not be reached or returned an unexpected error.

    Distinct from a 404 (which is an authoritative "does not exist"). Unavailable
    must never be treated as ``passed`` — it maps to ``server_status=unavailable``.
    """


def _gh(url: str | None) -> str | None:
    """Extract ``owner/repo`` (lowercased, no .git) from a GitHub URL, or None."""
    if not url:
        return None
    m = _GITHUB.search(url)
    return m.group(1).removesuffix(".git").lower() if m else None


def _command_version(command: list | None) -> str | None:
    """Extract a pinned version from the launch command (mirrors the CLI verifier).

    Handles both pin forms:
      - PyPI (uv/pip): ``pkg==1.2.3`` (also ``pkg[extra]==1.2.3``) -> "1.2.3"
      - npm (npx):     ``pkg@1.2.3`` and scoped ``@scope/name@1.2.3`` -> "1.2.3"
    npm packages never contain ``==`` and PyPI packages never use the npm ``@``
    form, so the two are mutually exclusive per token — adding ``==`` does not
    change npm behavior.
    """
    for part in command or []:
        if not isinstance(part, str):
            continue
        if "==" in part:  # PyPI exact pin (pkg==ver, pkg[extra]==ver, --from pkg==ver)
            ver = part.split("==")[-1].strip()
            if ver:
                return ver
            continue
        if "@" in part and not part.startswith("@"):
            return part.split("@")[-1]
        if part.startswith("@"):
            parts = part.split("@")
            if len(parts) == 3:  # @scope/name@version
                return parts[2]
    return None


# npm launch runners whose package argument we can safely re-pin.
_NPM_RUNNERS = {"npx"}


def _rewrite_npm_command(
    command: list | None, npm_package: str | None, resolved_version: str | None
) -> tuple[list | None, str]:
    """Re-pin an npx command's package argument to ``<package>@<resolved_version>``.

    Matches the command element against the known ``npm_package`` name (exact, or
    ``name@<anything>``) instead of parsing argv structurally. Returns
    (new_command, "pinned") on success, or (None, "not_supported") for anything we
    cannot rewrite without risk: non-npx runner, the package token not found
    exactly once, or missing inputs. Never raises; never blocks publish.
    """
    if not (command and isinstance(command, list) and npm_package and resolved_version):
        return None, "not_supported"
    if command[0] not in _NPM_RUNNERS:
        return None, "not_supported"
    prefix = npm_package + "@"
    matches = [
        i
        for i, e in enumerate(command)
        if isinstance(e, str) and (e == npm_package or e.startswith(prefix))
    ]
    if len(matches) != 1:
        return None, "not_supported"
    new = list(command)
    new[matches[0]] = f"{npm_package}@{resolved_version}"
    return new, "pinned"


async def _http_get_json(url: str) -> dict | None:
    """GET a registry URL. Returns parsed JSON, or None on 404.

    Raises RegistryUnavailable on network error, timeout, non-404 HTTP error,
    disallowed host, or unparseable body.
    """
    host = httpx.URL(url).host
    if host not in _ALLOWED_HOSTS:
        raise RegistryUnavailable(f"host not allowed: {host}")
    try:
        async with httpx.AsyncClient(
            timeout=_TIMEOUT,
            follow_redirects=False,
            limits=httpx.Limits(max_connections=5, max_keepalive_connections=2),
        ) as cx:
            resp = await cx.get(url)
    except httpx.HTTPError as e:
        raise RegistryUnavailable(str(e)) from e
    if resp.status_code == 404:
        return None
    if resp.status_code >= 400:
        raise RegistryUnavailable(f"HTTP {resp.status_code}")
    try:
        return resp.json()
    except Exception as e:  # pragma: no cover - defensive
        raise RegistryUnavailable(f"bad json: {e}") from e


async def _fetch_npm(name: str) -> dict | None:
    """Fetch the npm packument, or None on 404. Raises RegistryUnavailable."""
    return await _http_get_json(f"{NPM_REGISTRY}/{name}")


async def _fetch_pypi(name: str) -> dict | None:
    """Fetch the PyPI project JSON, or None on 404. Raises RegistryUnavailable."""
    return await _http_get_json(f"{PYPI_REGISTRY}/{name}/json")


def _blank(
    npm_name: str | None, pypi_name: str | None, declared_repo: str | None
) -> dict:
    return {
        "server_status": "indeterminate",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "registry": "npm" if npm_name else ("pypi" if pypi_name else None),
        "package_name": npm_name or pypi_name,
        "package_exists": False,
        "resolved_version": None,
        "version_exists": False,
        "shasum": None,
        "integrity": None,
        "registry_repo_url": None,
        "declared_source_repo": declared_repo,
        "repo_consistency": "indeterminate",
        "maintainer_list": [],
        "command_pinning": "indeterminate",
        "pinned_command": None,  # rewritten command pinning resolved_version (npm only)
        "command_rewrite": "not_supported",  # pinned | not_supported
        "warnings": [],
        "errors": [],
    }


async def _verify_npm(
    name: str, command: list | None, pinned: str | None, sv: dict
) -> None:
    data = await _fetch_npm(name)
    if data is None:
        sv["errors"].append(f"{name} not found on npm")
        return
    sv["package_exists"] = True
    versions = data.get("versions") or {}

    if pinned:
        sv["command_pinning"] = "pinned"
        if pinned in versions:
            sv["resolved_version"] = pinned
            sv["version_exists"] = True
        else:
            sv["errors"].append(f"Pinned version {pinned} not published on npm")
            return
    else:
        latest = (data.get("dist-tags") or {}).get("latest")
        if latest and latest in versions:
            sv["resolved_version"] = latest
            sv["version_exists"] = True
            sv["command_pinning"] = "unpinned_resolved"
            sv["warnings"].append(f"Command not pinned; resolved latest = {latest}")
        else:
            sv["command_pinning"] = "indeterminate"
            sv["warnings"].append("Command not pinned and no resolvable latest version")
            return

    dist = (versions.get(sv["resolved_version"]) or {}).get("dist") or {}
    sv["shasum"] = dist.get("shasum")
    sv["integrity"] = dist.get("integrity")

    repo = data.get("repository")
    if isinstance(repo, str):
        sv["registry_repo_url"] = repo
    elif isinstance(repo, dict):
        sv["registry_repo_url"] = repo.get("url")
    sv["maintainer_list"] = [
        m.get("name", "")
        for m in (data.get("maintainers") or [])
        if isinstance(m, dict)
    ]

    # Re-pin the launch command to the resolved version so the catalog entry is
    # reproducible. npm/npx only; anything else stays as-is (not_supported).
    new_cmd, rewrite = _rewrite_npm_command(command, name, sv["resolved_version"])
    sv["command_rewrite"] = rewrite
    if new_cmd is not None:
        sv["pinned_command"] = new_cmd


async def _verify_pypi(name: str, pinned: str | None, sv: dict) -> None:
    data = await _fetch_pypi(name)
    if data is None:
        sv["errors"].append(f"{name} not found on PyPI")
        return
    sv["package_exists"] = True
    info = data.get("info") or {}
    releases = data.get("releases") or {}

    if pinned:
        sv["command_pinning"] = "pinned"
        if pinned in releases:
            sv["resolved_version"] = pinned
            sv["version_exists"] = True
        else:
            sv["errors"].append(f"Pinned version {pinned} not published on PyPI")
            return
    else:
        latest = info.get("version")
        if latest:
            sv["resolved_version"] = latest
            sv["version_exists"] = True
            sv["command_pinning"] = "unpinned_resolved"
            sv["warnings"].append(f"Command not pinned; resolved latest = {latest}")
        else:
            sv["command_pinning"] = "indeterminate"
            sv["warnings"].append("Command not pinned and no resolvable latest version")
            return

    project_urls = info.get("project_urls") or {}
    sv["registry_repo_url"] = (
        project_urls.get("Repository")
        or project_urls.get("Source")
        or project_urls.get("Source Code")
        or project_urls.get("GitHub")
        or project_urls.get("Homepage")
        or info.get("home_page")
        or None
    )


async def verify_registry(manifest: dict) -> dict:
    """Re-derive registry-checkable facts server-side. Never executes MCP code.

    Returns a ``server_verification`` dict with a tri-state-plus ``server_status``:
    ``verified`` | ``mismatch`` | ``indeterminate`` | ``unavailable``.

    - mismatch:      registry contradicts the manifest (package/version missing,
                     or source_repo points elsewhere). Blocks publish.
    - indeterminate: facts confirmed but ownership link unprovable (registry has
                     no repo URL, or none declared). Does not block; admin decides.
    - unavailable:   registry unreachable. Retryable; never counts as passed.
    """
    mcp = manifest.get("mcp_server") or {}
    command = mcp.get("command") or []
    declared_repo = mcp.get("source_repo") or None
    npm_name = mcp.get("npm_package")
    pypi_name = mcp.get("pypi_package")

    sv = _blank(npm_name, pypi_name, declared_repo)

    # Stage 5 AUTHORITATIVE GATE (pure; runs BEFORE any registry lookup so no submit/verify
    # path can bypass it). A credentialed MCP (declares env_keys) MUST declare a valid
    # install descriptor AND a valid non-empty allowed_domains; a divergent second
    # allowlist is refused. Failure => mismatch (blocks publish; not credentialed-ready).
    from app.mcp.mcp_policy import check_credentialed_publish

    cred_errors = check_credentialed_publish(mcp, manifest.get("permissions"))
    if cred_errors:
        sv["errors"].extend(cred_errors)
        sv["server_status"] = "mismatch"
        return sv

    # Authoritative registry source. For a CREDENTIALED MCP the gate above has already
    # proven the install descriptor is consistent with the declared *_package field and
    # the command pin; here we VERIFY exactly install.package @ install.version on
    # install.manager — never a divergent *_package field, never the command-resolved
    # "latest". For a non-credentialed MCP the legacy *_package + command-pin path stands.
    env_keys = mcp.get("env_keys") or []
    install = mcp.get("install")
    if env_keys and isinstance(install, dict):
        from app.mcp.mcp_policy import validate_install_descriptor

        registry_branch, name, pinned = validate_install_descriptor(install)
        sv["registry"] = registry_branch
        sv["package_name"] = name
    else:
        name = npm_name or pypi_name
        registry_branch = "npm" if npm_name else "pypi"
        pinned = _command_version(command)
        if not name:
            sv["errors"].append("No npm_package or pypi_package declared in mcp_server")
            sv["server_status"] = "mismatch"
            return sv

    if not _SAFE_NAME.match(name):
        sv["errors"].append(f"Invalid package name: {name!r}")
        sv["server_status"] = "mismatch"
        return sv

    try:
        if registry_branch == "npm":
            await _verify_npm(name, command, pinned, sv)
        else:
            await _verify_pypi(name, pinned, sv)
    except RegistryUnavailable as e:
        sv["server_status"] = "unavailable"
        sv["errors"].append(f"Registry unavailable: {e}")
        return sv

    # Flag when we resolved a version but could not auto-pin the command
    # (PyPI/uvx/non-npx/ambiguous). Publish is not blocked; admin/maintainer see it.
    if sv["version_exists"] and sv["command_rewrite"] == "not_supported":
        sv["warnings"].append("command_rewrite_not_supported")

    declared = _gh(declared_repo)
    registry = _gh(sv["registry_repo_url"])
    if declared and registry:
        sv["repo_consistency"] = "match" if declared == registry else "mismatch"
    else:
        sv["repo_consistency"] = "indeterminate"

    if sv["errors"] or not sv["package_exists"] or not sv["version_exists"]:
        sv["server_status"] = "mismatch"
    elif sv["repo_consistency"] == "mismatch":
        sv["server_status"] = "mismatch"
    elif sv["repo_consistency"] == "indeterminate":
        sv["server_status"] = "indeterminate"
    else:
        sv["server_status"] = "verified"

    return sv


def derive_status(server_verification: dict, report: dict) -> str:
    """Combined submission status: server facts authoritative, client report advisory.

    Precedence: server ``mismatch`` always wins (-> action_required). Otherwise the
    client-attested status drives: clean (TESTED/RESOLVED, no high actions) ->
    quarantined_review (the converged review-hold state), everything else ->
    action_required. This NEVER returns ``published``: a clean submission is held
    for review, not made live — admin approval + publish are still required
    (Slice 1 of the MCP auto-publish arc: prepare, do not activate).
    """
    from app.mcp.status import ACTION_REQUIRED, QUARANTINED_REVIEW

    actions = report.get("actions") or []
    client_high = any(a.get("severity") == "high" for a in actions)
    if server_verification.get("server_status") == "mismatch":
        return ACTION_REQUIRED
    if report.get("status") in ("TESTED", "RESOLVED") and not client_high:
        return QUARANTINED_REVIEW
    return ACTION_REQUIRED
