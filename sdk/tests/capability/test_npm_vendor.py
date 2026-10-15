"""The npm-view provenance parse (Slice 2).

Regression lock: `npm view '<pkg>@<ver>' dist.integrity dist.tarball version` prints `key = value`
lines (values single-quoted) when more than one field is requested. The first implementation read
the tokens positionally and captured the `key`/`=` labels as the integrity/tarball values; this
test pins the by-name parse against the exact format the real registry returns.
"""
from __future__ import annotations

from agentnode_sdk.capability.npm_vendor import _parse_npm_view_fields

# verbatim shape of a real multi-field `npm view` reply (values single-quoted by npm)
REAL = (
    "dist.integrity = 'sha512-ykTmeS4Eejs+FESDrD73mDBNYCkb+cCZJ1MWuv7/vwucbVgGf"
    "EbBL5HVt2nVw0/vNF8jz+o9sO/WmxxfXt0J6w=='\n"
    "dist.tarball = 'https://registry.npmjs.org/@modelcontextprotocol/server-sequential-"
    "thinking/-/server-sequential-thinking-1.0.0.tgz'\n"
    "version = '1.0.0'\n"
)


def test_parse_keys_by_name_not_position():
    f = _parse_npm_view_fields(REAL)
    assert f["dist.integrity"].startswith("sha512-")
    assert f["dist.integrity"].endswith("==")
    assert f["dist.tarball"].startswith("https://") and f["dist.tarball"].endswith(".tgz")
    assert f["version"] == "1.0.0"
    # the old positional bug surfaced the label/'=' token as a value -- guard against regression
    assert f["dist.integrity"] != "dist.integrity"
    assert "=" not in (f["dist.tarball"],)  # tarball is a URL, never the bare '=' token


def test_blank_and_malformed_lines_ignored():
    f = _parse_npm_view_fields("\n  \nnot-a-field-line\nversion = '2.3.4'\n")
    assert f == {"version": "2.3.4"}


def test_empty_output_is_empty_mapping():
    assert _parse_npm_view_fields("") == {}
