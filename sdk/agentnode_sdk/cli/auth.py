"""agentnode auth — credential management CLI."""
from __future__ import annotations

import getpass
import sys

from agentnode_sdk.credential_store import (
    has_credential,
    list_credentials,
    remove_credential,
    set_credential,
)
from agentnode_sdk.installer import read_lockfile
from agentnode_sdk.cli.output import bold, dim, section


def cmd_auth_set(provider: str) -> int:
    provider = provider.lower().strip()
    if not provider:
        print("  Error: provider name required.", file=sys.stderr)
        return 1
    try:
        token = getpass.getpass(f"  Enter access token for {provider}: ")
    except (KeyboardInterrupt, EOFError):
        print("\n  Cancelled.")
        return 130
    token = token.strip()
    if not token:
        print("  Error: no token provided.", file=sys.stderr)
        return 1
    set_credential(provider, token, auth_type="api_key")
    print(f"\n  Credential stored for {provider}.\n")
    return 0


def cmd_auth_list() -> int:
    creds = list_credentials()
    if not creds:
        print("\n  No credentials configured.")
        print(dim("  Run `agentnode auth set <provider>` to store a credential.\n"))
        return 0
    print()
    print(section("Credentials"))
    print(f"  {'Provider':<14}{'Auth type':<13}{'Stored'}")
    print(f"  {'--------':<14}{'--------':<13}{'------'}")
    for provider, info in sorted(creds.items()):
        stored = info.get("stored_at", "")[:10]
        auth_type = info.get("auth_type", "unknown")
        print(f"  {provider:<14}{auth_type:<13}{stored}")
    print(f"\n  {len(creds)} credential(s) configured.\n")
    return 0


def cmd_auth_remove(provider: str) -> int:
    provider = provider.lower().strip()
    if remove_credential(provider):
        print(f"\n  Removed credential for {provider}.\n")
        return 0
    print(f"\n  No credential found for {provider}.\n")
    return 1


def cmd_auth_status() -> int:
    lock = read_lockfile()
    pkgs = lock.get("packages", {})

    # Collect provider → list of slugs
    provider_packages: dict[str, list[str]] = {}
    for slug, info in pkgs.items():
        connector = info.get("connector")
        if isinstance(connector, dict) and connector.get("provider"):
            provider = connector["provider"].lower().strip()
            provider_packages.setdefault(provider, []).append(slug)

    if not provider_packages:
        print("\n  No installed packages require credentials.\n")
        return 0

    print()
    print(section("Credential Status"))
    print(f"  {'Provider':<14}{'Status':<16}{'Used by'}")
    print(f"  {'--------':<14}{'------':<16}{'------'}")
    missing: list[str] = []
    for provider in sorted(provider_packages):
        configured = has_credential(provider)
        status = "configured" if configured else "missing"
        slugs = ", ".join(sorted(provider_packages[provider]))
        print(f"  {provider:<14}{status:<16}{slugs}")
        if not configured:
            missing.append(provider)

    if missing:
        print()
        print(dim("  Fix missing:"))
        for p in missing:
            print(dim(f"    agentnode auth set {p}"))
    print()
    return 0
