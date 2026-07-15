"""MCP verify — version-pin recognition for npm (@) and PyPI (==).

Regression guard for the CLI verifier drifting from the backend
(``registry_verify._command_version``): a PyPI command pinned with ``==1.2.3``
must be recognised as pinned exactly like an npm command pinned with ``@1.2.3``.
Before the fix the CLI only parsed the npm ``@`` form, so a correctly-pinned
PyPI command produced a spurious, non-blocking ``version_pinned`` FAIL.

The registry HTTP layer is mocked — these tests never touch the network.
"""

from __future__ import annotations

import pytest

from agentnode_sdk.cli import mcp_verify
from agentnode_sdk.cli.mcp_verify import (
    VerifyReport,
    _extract_pinned_version,
    _is_exact_version,
    check_pinning,
    verify_manifest,
)


# --------------------------------------------------------------------------- #
# Unit: exact-version predicate                                               #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "ver",
    [
        "1.2.3",
        "1.2.3rc1",
        "1.2.3+build.1",
        "1.0",
        "10.20.30",
        "1.2.3-rc.1",
        "1.2.3.post1",
        "1.2.3.dev1",
    ],
)
def test_is_exact_version_accepts(ver):
    assert _is_exact_version(ver) is True


@pytest.mark.parametrize(
    "ver",
    [
        "",
        "   ",
        None,
        "latest",
        "next",
        "^1.2.3",
        ">=1.2.3",
        "~=1.2",
        ">1.2",
        "<2.0",
        "1.2.*",
        "*",
        "1.x",
        "v1.2.3",
        "1.2.3,!=1.2.4",
        "=1.2.3",
    ],
)
def test_is_exact_version_rejects(ver):
    assert _is_exact_version(ver) is False


# --------------------------------------------------------------------------- #
# Unit: command -> pinned version extraction (npm @ and PyPI ==)              #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "command,expected",
    [
        # PyPI positives
        (["uvx", "example-mcp==1.2.3"], "1.2.3"),
        (["uvx", "example-mcp[cli]==1.2.3"], "1.2.3"),
        (["uv", "run", "--from", "example-mcp==2.0.0rc1", "example-mcp"], "2.0.0rc1"),
        # npm positives
        (["npx", "-y", "example-mcp@1.2.3"], "1.2.3"),
        (["npx", "-y", "@scope/example-mcp@1.2.3"], "1.2.3"),
        # PyPI negatives
        (["uvx", "example-mcp"], None),
        (["uvx", "example-mcp>=1.2.3"], None),
        (["uvx", "example-mcp~=1.2"], None),
        (["uvx", "example-mcp=="], None),
        (["uvx", "example-mcp==1.2.*"], None),
        # npm negatives
        (["npx", "-y", "example-mcp"], None),
        (["npx", "-y", "example-mcp@latest"], None),
        (["npx", "-y", "example-mcp@^1.2.3"], None),
        (["npx", "-y", "@scope/example-mcp"], None),
        (["npx", "-y", "example-mcp@"], None),
        # nothing pinnable
        ([], None),
        (None, None),
    ],
)
def test_extract_pinned_version(command, expected):
    assert _extract_pinned_version(command) == expected


# --------------------------------------------------------------------------- #
# Registry mock                                                               #
# --------------------------------------------------------------------------- #


class _FakeResp:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict:
        return self._payload


class _FakeHttp:
    """Answers npm and PyPI metadata GETs deterministically."""

    def get(self, url: str, *a, **k):
        if "registry.npmjs.org" in url:
            # npm: /{name}
            return _FakeResp(
                200,
                {
                    "versions": {
                        "1.2.3": {"dist": {"shasum": "a" * 40, "integrity": "sha512-x"}}
                    },
                    "maintainers": [{"name": "example-maintainer"}],
                    "repository": {"url": "https://github.com/example/example-mcp"},
                },
            )
        if "pypi.org/pypi" in url:
            # PyPI: /{name}/json or /{name}/{version}/json — either resolves
            return _FakeResp(
                200,
                {
                    "info": {
                        "project_urls": {
                            "Repository": "https://github.com/example/example-mcp"
                        },
                        "home_page": "",
                    },
                },
            )
        return _FakeResp(404, {})


@pytest.fixture(autouse=True)
def _mock_registry(monkeypatch):
    monkeypatch.setattr(mcp_verify, "_http", _FakeHttp())


def _write_manifest(tmp_path, command: list[str], *, registry: str) -> "object":
    pkg_line = (
        "  npm_package: example-mcp\n"
        if registry == "npm"
        else "  pypi_package: example-mcp\n"
    )
    cmd_json = "[" + ", ".join(f'"{c}"' for c in command) + "]"
    manifest = (
        "manifest_version: '0.3'\n"
        "id: example-mcp-listing\n"
        "name: Example MCP\n"
        "publisher: example-pub\n"
        "summary: Example server for pin tests.\n"
        "runtime: mcp\n"
        "package_type: toolpack\n"
        "mcp_server:\n"
        f"  command: {cmd_json}\n"
        f"{pkg_line}"
        "  source_repo: https://github.com/example/example-mcp\n"
    )
    p = tmp_path / "agentnode.yaml"
    p.write_text(manifest, encoding="utf-8")
    return p


def _check(report: VerifyReport, name: str):
    return next((c for c in report.checks if c.name == name), None)


# --------------------------------------------------------------------------- #
# Full-path reproduction — the bug and its fix                                #
# --------------------------------------------------------------------------- #


def test_pypi_exact_pin_is_recognised(tmp_path):
    """PyPI ``==1.2.3`` must resolve version_pinned=passed (the reported bug)."""
    path = _write_manifest(tmp_path, ["uvx", "example-mcp==1.2.3"], registry="pypi")
    report = verify_manifest(path, run_test=False)
    assert report.package.get("version") == "1.2.3"
    pin = _check(report, "version_pinned")
    assert pin is not None and pin.passed is True
    assert report.status == "RESOLVED"


def test_npm_exact_pin_still_recognised(tmp_path):
    """npm ``@1.2.3`` must stay green — no regression from the PyPI fix."""
    path = _write_manifest(tmp_path, ["npx", "-y", "example-mcp@1.2.3"], registry="npm")
    report = verify_manifest(path, run_test=False)
    assert report.package.get("version") == "1.2.3"
    pin = _check(report, "version_pinned")
    assert pin is not None and pin.passed is True


def test_pypi_unpinned_is_not_marked_pinned(tmp_path):
    """A PyPI command with no ``==`` must remain version_pinned=failed."""
    path = _write_manifest(tmp_path, ["uvx", "example-mcp"], registry="pypi")
    report = verify_manifest(path, run_test=False)
    assert report.package.get("version") is None
    pin = _check(report, "version_pinned")
    assert pin is not None and pin.passed is False


def test_pypi_range_is_not_marked_pinned(tmp_path):
    """A PyPI range (``>=``) has no ``==`` token, so it is not pinned."""
    path = _write_manifest(tmp_path, ["uvx", "example-mcp>=1.2.3"], registry="pypi")
    report = verify_manifest(path, run_test=False)
    pin = _check(report, "version_pinned")
    assert pin is not None and pin.passed is False


# --------------------------------------------------------------------------- #
# check_pinning evaluates the normalised report.package.version only          #
# --------------------------------------------------------------------------- #


def test_check_pinning_reads_normalised_version():
    manifest = {"mcp_server": {"command": ["uvx", "example-mcp==1.2.3"]}}
    report = VerifyReport()
    report.package = {"registry": "pypi", "name": "example-mcp", "version": "1.2.3"}
    check_pinning(manifest, report)
    pin = _check(report, "version_pinned")
    assert pin is not None and pin.passed is True


def test_check_pinning_fails_without_version():
    manifest = {"mcp_server": {"command": ["uvx", "example-mcp"]}}
    report = VerifyReport()
    report.package = {"registry": "pypi", "name": "example-mcp", "version": None}
    check_pinning(manifest, report)
    pin = _check(report, "version_pinned")
    assert pin is not None and pin.passed is False


# --------------------------------------------------------------------------- #
# Regression: the real `agentnode init --type mcp` scaffold command forms      #
# --------------------------------------------------------------------------- #


def test_scaffold_command_forms_are_pinnable_once_filled(tmp_path):
    """The scaffold ships placeholder commands (npm ``@`` + a commented PyPI
    ``==`` alternative); once a real version replaces the placeholder, both
    forms must be recognised as pinned."""
    from agentnode_sdk.cli.init import scaffold_package

    scaffold_package(
        template_key="mcp",
        target_dir=tmp_path,
        package_id="test-mcp-listing",
        name="Test MCP Listing",
        publisher="test-pub",
    )
    text = (tmp_path / "agentnode.yaml").read_text(encoding="utf-8")
    # npm form is the active command line; PyPI form is the commented alternative.
    assert _extract_pinned_version(["npx", "-y", "example-mcp@1.2.3"]) == "1.2.3"
    assert _extract_pinned_version(["uvx", "example-mcp==1.2.3"]) == "1.2.3"
    # unfilled placeholder must NOT be mistaken for a pin
    assert (
        _extract_pinned_version(["npx", "-y", "REPLACE_ME_PACKAGE@REPLACE_VERSION"])
        is None
    )
    assert (
        "==0.1.0" in text or "==REPLACE_VERSION" in text
    )  # PyPI == example present in scaffold
