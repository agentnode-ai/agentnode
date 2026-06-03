"""`agentnode sandbox` — minimal, explicit sandbox image management.

Sprint A scope: ONLY an explicit, user-initiated `pull` of the pinned image.
There is NO auto-pull anywhere in the SDK — the sandbox image is acquired only
here, on purpose. The full guided setup/repair UX (`sandbox doctor/install/
repair`) is Sprint B.
"""
from __future__ import annotations

import subprocess


def cmd_sandbox_pull() -> int:
    """Explicitly pull the digest-pinned sandbox image. No auto-pull, no tag."""
    from agentnode_sdk.sandbox.container_backend import _BASE_IMAGE, ContainerBackend

    # Refuse to act on an unactivated placeholder image (all-zero digest).
    digest = _BASE_IMAGE.rsplit(":", 1)[-1]
    if set(digest) == {"0"}:
        print(
            "\n  Sandbox image is not activated yet (placeholder digest).\n"
            "  Community code execution is fail-closed until the image is built,\n"
            "  pushed to GHCR and pinned by digest.\n"
            "  See sdk/sandbox-image/README.md.\n"
        )
        return 1

    avail = ContainerBackend().check_available()
    runtime = avail.backend
    if not runtime or runtime == "none":
        print(
            "\n  No container runtime (Docker or Podman) found.\n"
            f"  {avail.reason or ''}\n"
            "  Install Docker or Podman, then run: agentnode sandbox pull\n"
        )
        return 1

    print(f"\n  Pulling pinned sandbox image with {runtime} (explicit, no auto-pull):")
    print(f"  {_BASE_IMAGE}\n")
    try:
        result = subprocess.run([runtime, "pull", _BASE_IMAGE], timeout=600)
    except FileNotFoundError:
        print(f"  {runtime} not found on PATH.\n")
        return 1
    except subprocess.TimeoutExpired:
        print("  Pull timed out.\n")
        return 1
    if result.returncode != 0:
        print("  Pull failed.\n")
        return 1

    # Re-probe with a fresh backend (the pull changed local image state).
    after = ContainerBackend().check_available()
    if after.available:
        print("  Sandbox ready. Community packages can now run isolated.\n")
        return 0
    print(
        "  Image pulled, but the sandbox still reports unavailable: "
        f"{after.reason or 'unknown'}.\n"
    )
    return 1
