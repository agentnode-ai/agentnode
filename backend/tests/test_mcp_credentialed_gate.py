"""Stage 5: server-authoritative credentialed-publish gate + domain-policy parity.

The backend mirror consumes the SAME shared vectors as the SDK (sdk/tests/data/
allowed_domains_vectors.json), so the two canonicalizers decide identically — divergence
is a test failure. The gate is enforced in the authoritative registry_verify path; no
submit/verify path may bypass it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.mcp.mcp_policy import (
    DomainPolicyError,
    InstallDescriptorError,
    canonicalize_allowed_domains,
    check_credentialed_publish,
    validate_install_descriptor,
)
from app.mcp.registry_verify import verify_registry

_VEC = json.loads(
    (
        Path(__file__).resolve().parents[2]
        / "sdk"
        / "tests"
        / "data"
        / "allowed_domains_vectors.json"
    ).read_text(encoding="utf-8")
)
INSTALL = {"manager": "npm", "package": "@scope/some-mcp", "version": "1.2.3"}
INSTALL_PYPI = {"manager": "pypi", "package": "some-mcp", "version": "1.0.0"}
# Fully-valid credentialed manifests: install bound to the declared *_package + domains.
NPM_FULL = {
    "env_keys": ["TOKEN"],
    "install": INSTALL,
    "allowed_domains": ["api.example.com"],
    "npm_package": "@scope/some-mcp",
}
PYPI_FULL = {
    "env_keys": ["TOKEN"],
    "install": INSTALL_PYPI,
    "allowed_domains": ["api.example.com"],
    "pypi_package": "some-mcp",
}


# ---- shared-vector parity (backend must match the SDK's expected outputs) ----


@pytest.mark.parametrize("case", _VEC["valid"])
def test_backend_domain_parity_valid(case):
    assert list(canonicalize_allowed_domains(case["input"])) == case["output"]


@pytest.mark.parametrize("bad", _VEC["invalid"])
def test_backend_domain_parity_invalid(bad):
    with pytest.raises(DomainPolicyError):
        canonicalize_allowed_domains(bad)


# ---- install descriptor mirror ----


def test_install_descriptor_ok():
    assert validate_install_descriptor(INSTALL) == ("npm", "@scope/some-mcp", "1.2.3")


@pytest.mark.parametrize(
    "bad",
    [
        {"manager": "npm", "package": "p", "version": "^1.0.0"},
        {"manager": "npm", "package": "p", "version": "latest"},
        {"manager": "cargo", "package": "p", "version": "1.0.0"},
        {"manager": "pypi", "package": "p", "version": ">=1.0"},
        {"manager": "npm", "package": "p"},
        "not-a-dict",
    ],
)
def test_install_descriptor_rejects(bad):
    with pytest.raises(InstallDescriptorError):
        validate_install_descriptor(bad)


# ---- the credentialed gate (pure) ----


def test_gate_noncredentialed_ok():
    assert check_credentialed_publish({"command": ["npx", "x"]}) == []


def test_gate_noncredentialed_validates_domains_if_present():
    assert check_credentialed_publish(
        {"allowed_domains": ["http://evil"]}
    )  # has errors


def test_gate_credentialed_requires_install_and_domains():
    assert check_credentialed_publish({"env_keys": ["TOKEN"]})  # missing both -> errors
    assert check_credentialed_publish(
        {"env_keys": ["TOKEN"], "install": INSTALL}
    )  # missing domains
    assert check_credentialed_publish(
        {"env_keys": ["TOKEN"], "allowed_domains": ["api.x.com"]}
    )  # missing install


def test_gate_credentialed_full_ok():
    assert check_credentialed_publish(NPM_FULL) == []
    assert check_credentialed_publish(PYPI_FULL) == []


def test_gate_credentialed_invalid_domains():
    assert check_credentialed_publish(dict(NPM_FULL, allowed_domains=["127.0.0.1"]))


def test_gate_divergent_second_allowlist_refused():
    errs = check_credentialed_publish(
        NPM_FULL, {"network": {"allowed_domains": ["other.example.com"]}}
    )
    assert any("diverge" in e for e in errs)


def test_gate_identical_second_allowlist_ok():
    # NPM_FULL declares api.example.com; an identically-canonicalizing second source is ok.
    assert (
        check_credentialed_publish(
            dict(NPM_FULL, allowed_domains=["API.EXAMPLE.COM"]),
            {"network": {"allowed_domains": ["api.example.com"]}},
        )
        == []
    )


# ---- Blocker 1: install <-> registry-source binding (credentialed) ----


def test_binding_npm_happy_ok():
    assert check_credentialed_publish(NPM_FULL) == []


def test_binding_pypi_happy_ok():
    assert check_credentialed_publish(PYPI_FULL) == []


def test_binding_npm_package_mismatch():
    assert any(
        "npm_package" in e
        for e in check_credentialed_publish(dict(NPM_FULL, npm_package="@scope/evil"))
    )


def test_binding_pypi_package_mismatch():
    assert any(
        "pypi_package" in e
        for e in check_credentialed_publish(dict(PYPI_FULL, pypi_package="evil-pkg"))
    )


def test_binding_npm_install_with_divergent_pypi_package():
    assert any(
        "pypi_package" in e
        for e in check_credentialed_publish(dict(NPM_FULL, pypi_package="some-mcp"))
    )


def test_binding_pypi_install_with_divergent_npm_package():
    assert any(
        "npm_package" in e
        for e in check_credentialed_publish(
            dict(PYPI_FULL, npm_package="@scope/some-mcp")
        )
    )


def test_binding_missing_matching_package_field():
    m = {k: v for k, v in NPM_FULL.items() if k != "npm_package"}
    assert any("npm_package" in e for e in check_credentialed_publish(m))


def test_binding_command_version_mismatch():
    m = dict(NPM_FULL, command=["npx", "-y", "@scope/some-mcp@9.9.9"])
    assert any("command-pinned" in e for e in check_credentialed_publish(m))


def test_binding_command_version_match_ok():
    m = dict(NPM_FULL, command=["npx", "-y", "@scope/some-mcp@1.2.3"])
    assert check_credentialed_publish(m) == []


# ---- Blocker 2: env_keys must be validated, not merely truthy ----


@pytest.mark.parametrize("bad", ["TOKEN", [123], [""], [None], ["A", "A"], ["A", " "]])
def test_env_keys_malformed_rejected(bad):
    assert check_credentialed_publish(dict(NPM_FULL, env_keys=bad))


def test_env_keys_valid_list_ok():
    assert check_credentialed_publish(NPM_FULL) == []


# ---- Blocker 3: malformed permissions / permissions.network are fail-closed (no 500) ----


@pytest.mark.parametrize(
    "perms",
    [
        "bad",
        {"network": "bad"},
        {"network": {"allowed_domains": "api.example.com"}},  # str, not a list
        {"network": {"allowed_domains": ["http://evil"]}},
    ],
)
def test_permissions_malformed_rejected(perms):
    assert check_credentialed_publish(NPM_FULL, perms)


def test_permissions_well_formed_no_network_ok():
    assert check_credentialed_publish(NPM_FULL, {"filesystem": {"level": "none"}}) == []


# ---- authoritative path: verify_registry refuses BEFORE any registry lookup ----


async def test_verify_registry_blocks_credentialed_without_domains():
    sv = await verify_registry(
        {
            "mcp_server": {
                "env_keys": ["TOKEN"],
                "install": INSTALL,
                "npm_package": "@scope/some-mcp",
                "command": ["npx", "-y", "@scope/some-mcp@1.2.3"],
            }
        }
    )
    assert sv["server_status"] == "mismatch"
    assert any("allowed_domains" in e for e in sv["errors"])


async def test_verify_registry_blocks_credentialed_without_install():
    sv = await verify_registry(
        {
            "mcp_server": {
                "env_keys": ["TOKEN"],
                "allowed_domains": ["api.example.com"],
                "npm_package": "@scope/some-mcp",
                "command": ["npx", "-y", "@scope/some-mcp@1.2.3"],
            }
        }
    )
    assert sv["server_status"] == "mismatch"
    assert any("install" in e for e in sv["errors"])


async def test_verify_registry_blocks_install_package_mismatch():
    # install.package != npm_package -> the gate refuses BEFORE any registry lookup
    # (so the divergent install package can never be the verified source).
    sv = await verify_registry(
        {"mcp_server": dict(NPM_FULL, npm_package="@scope/evil")}
    )
    assert sv["server_status"] == "mismatch"
    assert any("npm_package" in e for e in sv["errors"])


async def test_verify_registry_blocks_malformed_permissions():
    # malformed permissions must surface as a gate mismatch, never an AttributeError/500.
    sv = await verify_registry({"mcp_server": NPM_FULL, "permissions": "bad"})
    assert sv["server_status"] == "mismatch"
    assert any("permissions" in e for e in sv["errors"])


# ---- Stage 5.1: credentialed HAPPY paths — verify_registry confirms exactly
# install.manager / install.package / install.version, NOT npm_package/pypi_package or
# command-resolved latest. The registry boundary (_fetch_npm/_fetch_pypi) is monkeypatched
# with RECORDING stubs (mirrors test_mcp_submit.py); no httpx, no network. The registry's
# advertised latest (9.9.9) is deliberately != install.version and the command is left
# UNPINNED — the only place old (command/latest) vs new (install) behaviour can diverge.


class _Recorder:
    """Async stand-in for _fetch_npm/_fetch_pypi: records each requested name and returns
    a canned packument/project JSON (or None => 404)."""

    def __init__(self, payloads: dict):
        self.payloads = payloads
        self.calls: list[str] = []

    async def __call__(self, name: str):
        self.calls.append(name)
        return self.payloads.get(name)


async def test_verify_registry_credentialed_npm_verifies_install(monkeypatch):
    npm = _Recorder(
        {
            "@scope/some-mcp": {
                "versions": {
                    "1.2.3": {"dist": {"shasum": "abc123", "integrity": "sha512-xxx"}},
                    "9.9.9": {"dist": {"shasum": "zzz999"}},
                },
                "dist-tags": {"latest": "9.9.9"},  # != install.version on purpose
                "repository": {"url": "git+https://github.com/owner/repo.git"},
                "maintainers": [{"name": "owner"}],
            }
        }
    )
    pypi = _Recorder({})
    monkeypatch.setattr("app.mcp.registry_verify._fetch_npm", npm)
    monkeypatch.setattr("app.mcp.registry_verify._fetch_pypi", pypi)

    sv = await verify_registry(
        {
            "mcp_server": dict(
                NPM_FULL,
                command=["npx", "-y", "@scope/some-mcp"],  # UNPINNED
                source_repo="https://github.com/owner/repo",
            )
        }
    )

    # the install descriptor is the authoritative source: install.package on install.manager
    assert npm.calls == ["@scope/some-mcp"]
    assert pypi.calls == []
    assert sv["registry"] == "npm"
    assert sv["package_name"] == "@scope/some-mcp"
    # install.version is verified, NOT the command-resolved latest 9.9.9
    assert sv["resolved_version"] == "1.2.3"
    assert sv["command_pinning"] == "pinned"
    assert sv["package_exists"] is True
    assert sv["version_exists"] is True
    assert sv["server_status"] == "verified"


async def test_verify_registry_credentialed_pypi_verifies_install(monkeypatch):
    pypi = _Recorder(
        {
            "some-mcp": {
                "info": {
                    "version": "9.9.9",  # advertised latest != install.version on purpose
                    "project_urls": {"Repository": "https://github.com/owner/repo"},
                },
                "releases": {"1.0.0": [], "9.9.9": []},
            }
        }
    )
    npm = _Recorder({})
    monkeypatch.setattr("app.mcp.registry_verify._fetch_npm", npm)
    monkeypatch.setattr("app.mcp.registry_verify._fetch_pypi", pypi)

    sv = await verify_registry(
        {
            "mcp_server": dict(
                PYPI_FULL,
                command=["uvx", "some-mcp"],  # not version-decisive
                source_repo="https://github.com/owner/repo",
            )
        }
    )

    # manager comes from install.manager (pypi) — the npm branch is never touched
    assert pypi.calls == ["some-mcp"]
    assert npm.calls == []
    assert sv["registry"] == "pypi"
    assert sv["package_name"] == "some-mcp"
    # install.version is verified, NOT the advertised latest 9.9.9
    assert sv["resolved_version"] == "1.0.0"
    assert sv["command_pinning"] == "pinned"
    assert sv["package_exists"] is True
    assert sv["version_exists"] is True
    assert sv["server_status"] == "verified"


async def test_verify_registry_credentialed_install_version_must_exist(monkeypatch):
    # install.version is the EXACT version the server checks: if it is not published, the
    # result is mismatch — never a silent fallback to the registry's latest (9.9.9).
    npm = _Recorder(
        {
            "@scope/some-mcp": {
                "versions": {
                    "9.9.9": {"dist": {"shasum": "zzz999"}}
                },  # 1.2.3 NOT published
                "dist-tags": {"latest": "9.9.9"},
                "repository": {"url": "git+https://github.com/owner/repo.git"},
            }
        }
    )
    pypi = _Recorder({})
    monkeypatch.setattr("app.mcp.registry_verify._fetch_npm", npm)
    monkeypatch.setattr("app.mcp.registry_verify._fetch_pypi", pypi)

    sv = await verify_registry(
        {
            "mcp_server": dict(
                NPM_FULL,
                command=[
                    "npx",
                    "-y",
                    "@scope/some-mcp",
                ],  # UNPINNED — must not rescue via latest
                source_repo="https://github.com/owner/repo",
            )
        }
    )

    assert npm.calls == ["@scope/some-mcp"]
    assert sv["server_status"] == "mismatch"
    assert sv["resolved_version"] is None  # never the latest 9.9.9
    assert any("1.2.3" in e for e in sv["errors"])
