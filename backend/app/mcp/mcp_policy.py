"""Server-authoritative Stage-5 policy: egress allowlist + install descriptor + the
credentialed-publish gate. Independent backend mirror of the SDK
``agentnode_sdk.sandbox.domain_policy`` / ``installer.validate_mcp_install`` — kept in
lockstep by the SHARED test vectors (sdk/tests/data/allowed_domains_vectors.json); any
divergence is a test failure.

Pure: no network, no DNS, no SDK import. This module is the HARD publish gate (used by
``registry_verify``); ``packages/validator`` + the client are only advisory pre-checks.
"""

from __future__ import annotations

import ipaddress
import re

# ---- allowed_domains (mirror of sdk domain_policy) ------------------------------------

_LABEL = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")
_MAX_DOMAIN_LEN = 253
_FORBIDDEN_SUBSTR = ("://", "/", "?", "#", "@", "*", "\\", ":")


class DomainPolicyError(ValueError):
    pass


def _canonicalize_one(d) -> str:
    if not isinstance(d, str):
        raise DomainPolicyError(f"domain must be a string: {d!r}")
    h = d.strip()
    if not h:
        raise DomainPolicyError("empty domain")
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in h) or " " in h or "\t" in h:
        raise DomainPolicyError(f"control/whitespace char in domain: {d!r}")
    for bad in _FORBIDDEN_SUBSTR:
        if bad in h:
            raise DomainPolicyError(f"bare hostname only: {d!r}")
    try:
        h.encode("ascii")
    except UnicodeEncodeError:
        raise DomainPolicyError(f"non-ASCII domain not allowed (use punycode): {d!r}")
    h = h.lower()
    if h.endswith("."):
        h = h[:-1]
    if not h:
        raise DomainPolicyError("empty domain")
    try:
        ipaddress.ip_address(h)
    except ValueError:
        pass
    else:
        raise DomainPolicyError(f"IP literals not allowed: {d!r}")
    if h == "localhost":
        raise DomainPolicyError("localhost not allowed")
    if len(h) > _MAX_DOMAIN_LEN:
        raise DomainPolicyError(f"domain too long: {d!r}")
    labels = h.split(".")
    if len(labels) < 2:
        raise DomainPolicyError(f"need a fully-qualified domain (>=2 labels): {d!r}")
    for lab in labels:
        if not _LABEL.match(lab):
            raise DomainPolicyError(f"invalid label {lab!r} in {d!r}")
    return h


def canonicalize_allowed_domains(domains) -> tuple[str, ...]:
    if not isinstance(domains, (list, tuple)) or len(domains) == 0:
        raise DomainPolicyError("allowed_domains must be a non-empty list")
    return tuple(sorted({_canonicalize_one(d) for d in domains}))


# ---- install descriptor (mirror of installer.validate_mcp_install) --------------------

_NPM_PACKAGE = re.compile(r"^(@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]*$")
_NPM_SEMVER = re.compile(
    r"^\d+\.\d+\.\d+(-[0-9A-Za-z][0-9A-Za-z.-]*)?(\+[0-9A-Za-z][0-9A-Za-z.-]*)?$"
)
_PYPI_PACKAGE = re.compile(r"^[a-z0-9]([a-z0-9._-]*[a-z0-9])?$")
_PYPI_VERSION = re.compile(
    r"^[0-9]+(\.[0-9]+)*((a|b|rc)[0-9]+)?(\.post[0-9]+)?(\.dev[0-9]+)?$"
)
_UNPINNED = ("^", "~", "<", ">", "=", "|", "@", ":", "/", "\\", " ", "\t", "*")


class InstallDescriptorError(ValueError):
    pass


def validate_install_descriptor(descriptor) -> tuple[str, str, str]:
    if not isinstance(descriptor, dict):
        raise InstallDescriptorError("install must be an object")
    if set(descriptor) - {"manager", "package", "version"}:
        raise InstallDescriptorError("install has unexpected keys")
    manager = descriptor.get("manager")
    package = descriptor.get("package")
    version = descriptor.get("version")
    if not all(isinstance(x, str) for x in (manager, package, version)):
        raise InstallDescriptorError("install.manager/package/version must be strings")
    manager = manager.strip().lower()
    package = package.strip()
    version = version.strip()
    if manager not in ("npm", "pypi"):
        raise InstallDescriptorError(
            f"install.manager must be npm or pypi, got {manager!r}"
        )
    if not package or not version:
        raise InstallDescriptorError("install.package/version must be non-empty")
    if (
        not version
        or version.lower() in ("latest", "*", "x")
        or any(c in version for c in _UNPINNED)
    ):
        raise InstallDescriptorError(
            f"install.version must be exact-pinned, got {version!r}"
        )
    for tok in ("git+", "http://", "https://", "file:", "workspace:", "github:"):
        if tok in version.lower():
            raise InstallDescriptorError(
                f"install.version spec not allowed: {version!r}"
            )
    if manager == "npm":
        if not _NPM_PACKAGE.match(package) or not _NPM_SEMVER.match(version):
            raise InstallDescriptorError(
                f"invalid npm install descriptor: {package}@{version}"
            )
    else:
        norm = re.sub(r"[-_.]+", "-", package.lower())
        if (
            "[" in package
            or not _PYPI_PACKAGE.match(norm)
            or not _PYPI_VERSION.match(version)
        ):
            raise InstallDescriptorError(
                f"invalid pypi install descriptor: {package}=={version}"
            )
        package = norm
    return manager, package, version


# ---- env_keys shape (the gate must not treat env_keys as merely truthy) ---------------


def validate_env_keys(raw) -> tuple[list[str], list[str]]:
    """Return (env_keys, errors). ``None`` (absent) -> ([], []). Any malformed value is a
    fail-closed error (NOT silently treated as non-credentialed): must be a list of
    non-empty strings with no duplicates. Pure."""
    if raw is None:
        return [], []
    if not isinstance(raw, list) or not all(
        isinstance(k, str) and k.strip() for k in raw
    ):
        return [], ["mcp_server.env_keys must be a list of non-empty strings"]
    if len(raw) != len(set(raw)):
        return [], ["mcp_server.env_keys must not contain duplicate keys"]
    return list(raw), []


# ---- install <-> registry-source binding (credentialed MCPs) --------------------------


def check_install_binding(
    mcp: dict, manager: str, package: str, version: str
) -> list[str]:
    """Pure: for a credentialed MCP, the ``install`` descriptor is the AUTHORITATIVE package
    source. The manager-matching ``*_package`` field MUST equal canonical ``install.package``;
    the other manager's ``*_package`` field MUST be absent (no divergent second source); and a
    command-pinned version, if any, MUST equal ``install.version``. No network."""
    from app.mcp.registry_verify import _command_version, normalize_package_name

    errors: list[str] = []
    npm = mcp.get("npm_package")
    pypi = mcp.get("pypi_package")
    if manager == "npm":
        same, other, other_name = npm, pypi, "pypi_package"
        reg = "npm"
    else:
        same, other, other_name = pypi, npm, "npm_package"
        reg = "pypi"

    if not isinstance(same, str) or not same.strip():
        errors.append(
            f"credentialed MCP must declare mcp_server.{reg}_package matching install.package"
        )
    elif normalize_package_name(reg, same) != normalize_package_name(reg, package):
        errors.append(
            f"mcp_server.{reg}_package does not match mcp_server.install.package"
        )
    if other not in (None, "", []):
        errors.append(
            f"mcp_server.{other_name} diverges from the install descriptor ({manager} install)"
        )

    cmd = mcp.get("command")
    pinned = _command_version(cmd) if isinstance(cmd, list) else None
    if pinned is not None and pinned != version:
        errors.append(
            "command-pinned version does not match mcp_server.install.version"
        )
    return errors


# ---- the authoritative credentialed-publish gate -------------------------------------


def check_credentialed_publish(mcp: dict, permissions=None) -> list[str]:
    """Return a list of fail-closed errors (empty = ok). If the MCP declares ``env_keys``
    it MUST declare a valid ``install`` AND a valid non-empty ``allowed_domains``, and the
    install descriptor MUST be authoritatively bound to the declared registry package
    (``check_install_binding``). If both ``mcp_server.allowed_domains`` and
    ``permissions.network.allowed_domains`` are present and diverge (after canonicalization)
    the publish is refused. Malformed ``env_keys`` / ``permissions`` are fail-closed errors,
    never AttributeError/500. Pure; no network."""
    errors: list[str] = []
    mcp = mcp or {}

    # permissions robustness: a non-dict permissions / permissions.network must surface as
    # a gate error, not crash the authoritative path.
    if permissions is not None and not isinstance(permissions, dict):
        errors.append("permissions must be an object")
        permissions = None
    network = (permissions or {}).get("network")
    if network is not None and not isinstance(network, dict):
        errors.append("permissions.network must be an object")
        network = None
    net = (network or {}).get("allowed_domains")

    # env_keys shape (malformed -> error; only a valid non-empty list is "credentialed").
    env_keys, env_errors = validate_env_keys(mcp.get("env_keys"))
    errors.extend(env_errors)

    declared = mcp.get("allowed_domains")
    canon = None
    if declared is not None:
        try:
            canon = canonicalize_allowed_domains(declared)
        except ValueError as e:
            errors.append(f"mcp_server.allowed_domains invalid: {e}")

    if env_keys:
        try:
            mgr, pkg, ver = validate_install_descriptor(mcp.get("install"))
        except ValueError as e:
            errors.append(f"credentialed MCP requires a valid mcp_server.install: {e}")
        else:
            errors.extend(check_install_binding(mcp, mgr, pkg, ver))
        if declared is None:
            errors.append("credentialed MCP requires mcp_server.allowed_domains")
        elif canon is not None and len(canon) == 0:
            errors.append(
                "credentialed MCP requires a non-empty mcp_server.allowed_domains"
            )

    # Double-source rule: a second allowlist must not silently diverge.
    if net is not None:
        try:
            net_canon = canonicalize_allowed_domains(net) if net else tuple()
        except ValueError as e:
            errors.append(f"permissions.network.allowed_domains invalid: {e}")
            net_canon = None
        if net_canon is not None and canon is not None and net_canon != canon:
            errors.append(
                "permissions.network.allowed_domains diverges from mcp_server.allowed_domains"
            )
    return errors
