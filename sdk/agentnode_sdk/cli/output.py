"""Output helpers for AgentNode CLI. All functions return str."""
from __future__ import annotations

import json
import os
import sys

_color_override: bool | None = None


def _colors_enabled() -> bool:
    if _color_override is not None:
        return _color_override
    if os.environ.get("NO_COLOR"):
        return False
    if not hasattr(sys.stdout, "isatty") or not sys.stdout.isatty():
        return False
    return True


def set_color(enabled: bool) -> None:
    global _color_override
    _color_override = enabled


def bold(text: str) -> str:
    if _colors_enabled():
        return f"\033[1m{text}\033[0m"
    return text


def dim(text: str) -> str:
    if _colors_enabled():
        return f"\033[2m{text}\033[0m"
    return text


def section(title: str) -> str:
    line = bold(title)
    underline = "=" * len(title)
    return f"{line}\n{underline}\n"


def kv(label: str, value: str, width: int = 22) -> str:
    return f"  {label:<{width}}{value}"


def spacer() -> str:
    return ""


# ---------------------------------------------------------------- is this host even installed
#
# HERE RATHER THAN IN EITHER ROLE'S COMMAND MODULE. The gateway's readiness paths and the
# worker's preflight need the same answer, and `agentnode_sdk/roles.py` walks the import graph
# statically over the source: importing `cli/gateway_commands.py` from `cli/worker_commands.py`
# -- even inside a function -- put twenty-four gateway modules into the worker's graph and broke
# the separation `test_the_two_roles_are_separable.py` records. This module is the CLI's
# presentation layer, is imported by both already, and is owned by neither role.
def an_incomplete_installation(as_json: bool = False) -> int | None:
    """Say that this host is NOT READY because its install never finished. None when it did.

    WHY THIS IS FIRST, before a service is built or a state directory is read. On a host whose
    install was interrupted there is nothing to build a service from, and a readiness command that
    dies trying has reported nothing -- which is what the third beta acceptance measured, with
    `agentnode gateway doctor` answering `ModuleNotFoundError: No module named 'httpx'`.

    WHAT IT SAYS, and PR7 asks for both halves: which step is missing, and a next step that can
    actually be run. The machine-readable form carries the same three facts, because a report an
    operator reads and a report their tooling reads must not be able to disagree.
    """
    import agentnode_sdk

    missing = ""
    try:
        missing = agentnode_sdk.what_this_installation_is_missing()
    except AttributeError:
        # An older build of this package, which has no such reader. Absent is not incomplete.
        missing = ""
    if not missing:
        return None

    step = ("Re-run the installer for this host from the artefact it was installed from, or "
            "`%s -m pip install %s` into the environment this command runs in."
            % (sys.executable, missing))
    if as_json:
        print(json.dumps({
            "ready": False,
            "reason": "installation_incomplete",
            "missing_dependency": missing,
            "interrupted_step": "installing this package's dependencies",
            "next_step": step,
        }, indent=2))
        return 1
    print()
    print("  %s" % bold("This host is not ready: its installation never finished"))
    print()
    print("  The package is here and the %r it needs is not, so nothing on this host can" % missing)
    print("  run work and nothing will be started.")
    print()
  
    print("  Which step was interrupted: installing this package's dependencies.")
    print("  Which dependency is missing: %s" % missing)
    print()
    print("  Next:")
    print("    %s" % step)
    return 1
