"""Publish-challenge ownership for MCP packages (Slice 2b-2 UX).

Two verbs let a publisher prove they control an npm/PyPI package:

  agentnode mcp ownership challenge --registry npm|pypi <package>
  agentnode mcp ownership verify    --registry npm|pypi <package>

`challenge` asks the backend for a one-time token + keyword; the publisher adds
the keyword to their package's metadata and publishes a new version. `verify`
then asks the backend to look at the latest published version and confirm the
keyword -- only someone with publish rights can put it there, so a match is
strong ownership evidence.

The token is never written to disk here -- it is shown once, straight from the
API response. Verifying ownership does NOT publish anything: MCP listings stay
review-gated until the sandbox-smoke gate is built.
"""
from __future__ import annotations

import json
import os
import sys

import httpx

_REVIEW_NOTE = (
    "  Ownership can be verified, but MCP listings still remain review-gated\n"
    "  until the sandbox-smoke gate is built."
)


def _resolve_api_base() -> str:
    from agentnode_sdk.client import DEFAULT_BASE_URL
    return os.environ.get("AGENTNODE_API_URL", DEFAULT_BASE_URL).rstrip("/")


def _api_key(token: str | None) -> str:
    return token or os.environ.get("AGENTNODE_API_KEY", "")


def _no_key() -> int:
    print(
        "  Error: No AgentNode API key configured.\n"
        "  Set AGENTNODE_API_KEY or pass --token <key>.",
        file=sys.stderr,
    )
    return 1


def _handle_error(resp: httpx.Response, json_output: bool) -> int:
    """Print a clear error for a non-2xx response and return 1.

    Backend error messages are already user-facing (expired challenge, no
    challenge, package not found, registry unavailable), so we surface them
    directly rather than re-inventing copy per status code.
    """
    try:
        err = resp.json()
        code = err.get("code", "")
        message = err.get("message", resp.text)
    except Exception:
        err = None
        code = ""
        message = resp.text

    if json_output and err is not None:
        print(json.dumps(err, indent=2))
        return 1

    if resp.status_code == 401:
        print("  Error: Authentication failed. Check your API key.", file=sys.stderr)
    elif resp.status_code == 403:
        if "PUBLISHER" in code:
            print("  Error: You need a publisher profile.", file=sys.stderr)
            print("  Create one at https://agentnode.net/dashboard", file=sys.stderr)
        else:
            print(f"  Error: {message}", file=sys.stderr)
    else:
        print(f"  Error ({resp.status_code}): {message}", file=sys.stderr)
    return 1


def cmd_mcp_ownership_challenge(
    registry: str,
    package: str,
    *,
    token: str | None = None,
    json_output: bool = False,
) -> int:
    """Issue a one-time publish-challenge for a package."""
    api_key = _api_key(token)
    if not api_key:
        return _no_key()

    registry = (registry or "").strip().lower()
    url = f"{_resolve_api_base()}/v1/mcp/ownership/challenge"
    try:
        resp = httpx.post(
            url,
            json={"registry": registry, "package_name": package},
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=30,
        )
    except Exception as e:
        print(f"  Error: Connection failed: {e}", file=sys.stderr)
        return 1

    if resp.status_code != 200:
        return _handle_error(resp, json_output)

    data = resp.json()
    if json_output:
        print(json.dumps(data, indent=2))
        return 0

    keyword = data.get("keyword", "")
    tok = data.get("token", "")
    expires = (data.get("expires_at") or "")[:10]
    verify_cmd = f"agentnode mcp ownership verify --registry {registry} {package}"

    print()
    print("  AgentNode Ownership Challenge")
    print("  " + "-" * 29)
    print(f"  Registry: {registry}")
    print(f"  Package:  {package}")
    print()
    print("  Prove package ownership by publishing this keyword in your package")
    print("  metadata. Only someone with publish rights can do that.")
    print()
    print(f"  Token (shown once):  {tok}")
    print(f"  Keyword:             {keyword}")
    if expires:
        print(f"  Expires:             {expires}")
    print()
    print("  Next steps:")
    print(f'    1. Add "{keyword}" to your package\'s keywords.')
    print(f"    2. Publish a new version to {registry}.")
    print(f"    3. Run: {verify_cmd}")
    print()
    print("  The token is shown only once and is stored only as a hash. If you")
    print("  lose it, just issue a new challenge -- that replaces the old one.")
    print()
    print(_REVIEW_NOTE)
    print()
    return 0


def cmd_mcp_ownership_verify(
    registry: str,
    package: str,
    *,
    token: str | None = None,
    json_output: bool = False,
) -> int:
    """Verify a pending publish-challenge against the published package."""
    api_key = _api_key(token)
    if not api_key:
        return _no_key()

    registry = (registry or "").strip().lower()
    url = f"{_resolve_api_base()}/v1/mcp/ownership/challenge/verify"
    try:
        resp = httpx.post(
            url,
            json={"registry": registry, "package_name": package},
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=30,
        )
    except Exception as e:
        print(f"  Error: Connection failed: {e}", file=sys.stderr)
        return 1

    if resp.status_code != 200:
        return _handle_error(resp, json_output)

    data = resp.json()
    verified = bool(data.get("verified"))
    if json_output:
        print(json.dumps(data, indent=2))
        return 0 if verified else 1

    status = data.get("status", "?")
    message = data.get("message", "")
    version = data.get("version")

    print()
    print("  AgentNode Ownership Verify")
    print("  " + "-" * 26)
    print(f"  Registry: {registry}")
    print(f"  Package:  {package}")
    print(f"  Status:   {status}")
    if version:
        print(f"  Version:  {version}")
    print()
    if message:
        print(f"  {message}")
    if verified:
        print("  Strong ownership evidence recorded for this package.")
    else:
        print(
            f"  Not verified yet. Publish a version carrying the challenge keyword, "
            f"then run:\n    agentnode mcp ownership verify --registry {registry} {package}"
        )
    print()
    print(_REVIEW_NOTE)
    print()
    return 0 if verified else 1
