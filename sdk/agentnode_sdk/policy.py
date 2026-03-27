"""Runtime policy resolution (stub for future policy engine)."""
from __future__ import annotations


def resolve_runtime(entry: dict, context: dict | None = None) -> str:
    """Resolve which runtime to use for a package.

    Currently just reads the runtime field from the lockfile entry.
    Future: policy engine checks permissions, context, user preferences.
    """
    runtime = entry.get("runtime", "python")
    # Future expansion point:
    # if not policy.allows(runtime, entry, context):
    #     raise PolicyViolation(f"Runtime '{runtime}' not permitted")
    return runtime
