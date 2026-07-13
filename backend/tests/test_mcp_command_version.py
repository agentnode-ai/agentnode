"""Pinned-version extraction from an MCP launch command (_command_version).

Covers the PyPI `==` form (the template-recommended `uvx pkg==ver`), which was
previously unrecognized, plus the unchanged npm `@` forms and the unpinned case.
This is what feeds command_pinning="pinned" / resolved_version for a
non-credentialed MCP, so it gates whether a PyPI submission is version-pinned.
"""

from __future__ import annotations

from app.mcp.registry_verify import _command_version


# --- PyPI: the newly recognized `==` form ------------------------------------


def test_pypi_double_equals_is_recognized():
    assert _command_version(["uvx", "mcp-server-time==1.2.3"]) == "1.2.3"


def test_pypi_double_equals_with_extra():
    assert _command_version(["uvx", "pkg[extra]==2.0.0"]) == "2.0.0"


def test_pypi_from_flag_double_equals():
    assert _command_version(["uvx", "--from", "pkg==3.4.5", "run-server"]) == "3.4.5"


def test_pypi_empty_version_after_equals_is_unpinned():
    # `pkg==` (no version) must not be treated as pinned.
    assert _command_version(["uvx", "pkg=="]) is None


# --- npm: unchanged `@` forms ------------------------------------------------


def test_npm_at_form_still_recognized():
    assert _command_version(["npx", "-y", "server-x@1.5.6"]) == "1.5.6"


def test_npm_scoped_at_form_still_recognized():
    assert _command_version(["npx", "-y", "@scope/mcp@2.3.4"]) == "2.3.4"


def test_uvx_at_form_still_recognized():
    # uv also accepts pkg@ver; the existing PyPI test uses this form.
    assert _command_version(["uvx", "py-mcp@1.5.6"]) == "1.5.6"


# --- unpinned stays unpinned -------------------------------------------------


def test_unpinned_npm_is_none():
    assert _command_version(["npx", "-y", "server-x"]) is None


def test_unpinned_pypi_is_none():
    assert _command_version(["uvx", "mcp-server-time"]) is None


def test_scoped_without_version_is_none():
    assert _command_version(["npx", "-y", "@scope/mcp"]) is None


def test_empty_and_nonstring_tokens():
    assert _command_version([]) is None
    assert _command_version(None) is None
    assert _command_version(["uvx", 123, "pkg==9.9.9"]) == "9.9.9"
